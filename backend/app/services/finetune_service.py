# app/services/finetune_service.py
"""
Service for building fine-tuning datasets from persona-tagged videos
and managing OpenAI fine-tuning jobs.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from openai import OpenAI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── OpenAI client (lazy init) ──
_openai_client: Optional[OpenAI] = None


def _get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url="https://api.openai.com/v1",  # Use original OpenAI for fine-tuning
        )
    return _openai_client


# ── Dataset building ──

async def build_training_dataset(
    db: AsyncSession,
    persona_id: str,
) -> dict:
    """
    Collect speech segments from all persona-tagged videos and build
    a JSONL training dataset for OpenAI fine-tuning.

    Returns: {
        "examples": [...],       # list of training examples
        "video_count": int,
        "segment_count": int,
        "duration_hours": float,
    }
    """
    # 1. Get tagged video IDs
    tag_sql = text("""
        SELECT pvt.video_id, v.original_filename, v.status
        FROM persona_video_tags pvt
        JOIN videos v ON v.id = pvt.video_id
        WHERE pvt.persona_id = :pid
          AND v.status = 'DONE'
        ORDER BY v.created_at ASC
    """)
    tag_result = await db.execute(tag_sql, {"pid": persona_id})
    tagged_videos = tag_result.fetchall()

    if not tagged_videos:
        return {
            "examples": [],
            "video_count": 0,
            "segment_count": 0,
            "duration_hours": 0.0,
        }

    video_ids = [str(v.video_id) for v in tagged_videos]
    logger.info(f"Building dataset for persona {persona_id}: {len(video_ids)} videos")

    # 2. Get persona info for system prompt
    persona_sql = text("""
        SELECT name, description, style_prompt FROM personas WHERE id = :pid
    """)
    persona_result = await db.execute(persona_sql, {"pid": persona_id})
    persona = persona_result.fetchone()

    system_prompt = _build_system_prompt(persona)

    # 3. Collect speech segments grouped by video and phase
    # Join speech_segments → audio_chunks → videos, and also get phase context
    segments_sql = text("""
        SELECT
            ss.text AS segment_text,
            ss.start_ms,
            ss.end_ms,
            ss.confidence,
            ac.video_id,
            ac.chunk_index,
            vp.phase_description,
            vp.phase_index,
            pi.insight
        FROM speech_segments ss
        JOIN audio_chunks ac ON ss.audio_chunk_id = ac.id
        LEFT JOIN video_phases vp ON vp.video_id = ac.video_id
            AND ss.start_ms >= (vp.time_start * 1000)
            AND ss.start_ms < (vp.time_end * 1000)
        LEFT JOIN phase_insights pi ON pi.video_id = ac.video_id
            AND pi.phase_index = vp.phase_index
        WHERE ac.video_id = ANY(:vids)
          AND ss.confidence >= 0.5
        ORDER BY ac.video_id, ss.start_ms ASC
    """)
    seg_result = await db.execute(segments_sql, {"vids": video_ids})
    all_segments = seg_result.fetchall()

    logger.info(f"Collected {len(all_segments)} speech segments")

    # 4. Group segments into conversational turns (sliding window)
    examples = _build_conversation_examples(all_segments, system_prompt)

    # 5. Calculate stats
    total_duration_ms = sum(
        (s.end_ms - s.start_ms) for s in all_segments
    )

    return {
        "examples": examples,
        "video_count": len(video_ids),
        "segment_count": len(all_segments),
        "duration_hours": round(total_duration_ms / 3_600_000, 2),
    }


def _build_system_prompt(persona) -> str:
    """Build the system prompt for fine-tuning examples."""
    name = persona.name if persona else "ライバー"
    desc = persona.description if persona and persona.description else ""
    style = persona.style_prompt if persona and persona.style_prompt else ""

    prompt = f"""あなたは「{name}」というライブコマース配信者です。
視聴者とリアルタイムで会話しながら商品を紹介するライブ配信を行っています。

{f'プロフィール: {desc}' if desc else ''}
{f'話し方の特徴: {style}' if style else ''}

以下のルールに従ってください：
- 自然な日本語で話す（書き言葉ではなく話し言葉）
- 視聴者に親しみやすい口調で話す
- 商品の魅力を具体的に伝える
- 視聴者のコメントに自然に反応する
- ライブ配信のテンポ感を大切にする"""

    return prompt.strip()


def _build_conversation_examples(segments, system_prompt: str) -> list:
    """
    Build fine-tuning examples from speech segments.

    Strategy: Group consecutive segments into ~30-60 second windows.
    Each window becomes one assistant message. The user message provides
    context (phase description, viewer comments, etc.).
    """
    examples = []
    current_window = []
    window_start_ms = 0
    current_video_id = None
    current_phase_desc = None
    current_insight = None

    WINDOW_SIZE_MS = 45_000  # 45 seconds per example

    for seg in segments:
        # New video = flush window
        if seg.video_id != current_video_id:
            if current_window:
                ex = _flush_window(
                    current_window, system_prompt,
                    current_phase_desc, current_insight
                )
                if ex:
                    examples.append(ex)
            current_window = []
            current_video_id = seg.video_id
            window_start_ms = seg.start_ms
            current_phase_desc = seg.phase_description
            current_insight = seg.insight

        # Window full = flush
        if seg.start_ms - window_start_ms > WINDOW_SIZE_MS and current_window:
            ex = _flush_window(
                current_window, system_prompt,
                current_phase_desc, current_insight
            )
            if ex:
                examples.append(ex)
            current_window = []
            window_start_ms = seg.start_ms
            current_phase_desc = seg.phase_description
            current_insight = seg.insight

        current_window.append(seg)

    # Flush remaining
    if current_window:
        ex = _flush_window(
            current_window, system_prompt,
            current_phase_desc, current_insight
        )
        if ex:
            examples.append(ex)

    return examples


def _flush_window(segments, system_prompt, phase_desc, insight) -> Optional[dict]:
    """Convert a window of segments into a fine-tuning example."""
    text_parts = [s.segment_text for s in segments if s.segment_text]
    combined_text = " ".join(text_parts).strip()

    if len(combined_text) < 20:
        return None  # Too short, skip

    # Build user context message
    context_parts = []
    if phase_desc:
        context_parts.append(f"現在のシーン: {phase_desc}")
    if insight:
        # Truncate insight to avoid too long context
        truncated = insight[:300] if len(insight) > 300 else insight
        context_parts.append(f"分析: {truncated}")

    if context_parts:
        user_msg = "\n".join(context_parts) + "\n\nこの場面で自然に話してください。"
    else:
        user_msg = "ライブ配信を続けてください。自然に話してください。"

    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": combined_text},
        ]
    }


# ── Fine-tuning job management ──

def create_finetune_job(
    examples: list,
    base_model: str = "gpt-4.1-mini-2025-04-14",
    n_epochs: int = 3,
    suffix: str = "aitherhub-clone",
) -> dict:
    """
    Upload JSONL and create an OpenAI fine-tuning job.

    Returns: {"job_id": str, "file_id": str, "status": str}
    """
    client = _get_openai()

    # Write JSONL to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
        jsonl_path = f.name

    try:
        # Upload file
        with open(jsonl_path, "rb") as f:
            file_obj = client.files.create(file=f, purpose="fine-tune")

        logger.info(f"Uploaded training file: {file_obj.id} ({len(examples)} examples)")

        # Create fine-tuning job
        job = client.fine_tuning.jobs.create(
            training_file=file_obj.id,
            model=base_model,
            hyperparameters={"n_epochs": n_epochs},
            suffix=suffix,
        )

        logger.info(f"Created fine-tuning job: {job.id}")

        return {
            "job_id": job.id,
            "file_id": file_obj.id,
            "status": job.status,
        }
    finally:
        os.unlink(jsonl_path)


def get_finetune_status(job_id: str) -> dict:
    """Get the status of a fine-tuning job."""
    client = _get_openai()
    job = client.fine_tuning.jobs.retrieve(job_id)

    return {
        "job_id": job.id,
        "status": job.status,
        "model_id": job.fine_tuned_model,
        "error": job.error.message if job.error else None,
        "trained_tokens": job.trained_tokens,
        "created_at": str(job.created_at) if job.created_at else None,
        "finished_at": str(job.finished_at) if job.finished_at else None,
    }


def cancel_finetune_job(job_id: str) -> dict:
    """Cancel a fine-tuning job."""
    client = _get_openai()
    job = client.fine_tuning.jobs.cancel(job_id)
    return {
        "job_id": job.id,
        "status": job.status,
    }
