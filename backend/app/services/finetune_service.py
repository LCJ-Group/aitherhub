# app/services/finetune_service.py
"""
Service for building fine-tuning datasets from persona-tagged videos
and managing OpenAI fine-tuning jobs.

Data sources (priority order):
  1. video_phases.phase_description  – Whisper transcript per phase
  2. phase_insights.insight          – GPT analysis per phase
Both are joined to build rich training examples.
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
    Collect phase descriptions and insights from all persona-tagged videos
    and build a JSONL training dataset for OpenAI fine-tuning.

    Uses video_phases (phase_description = transcript text per phase)
    and phase_insights (insight = GPT analysis) as data sources.

    Returns: {
        "examples": [...],
        "video_count": int,
        "segment_count": int,    # number of phases with text
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

    # 3. Collect phases with descriptions and insights from video_phases + phase_insights
    # video_phases.phase_description contains the transcript/description per phase
    # phase_insights.insight contains GPT analysis per phase
    phases_sql = text("""
        SELECT
            vp.video_id,
            vp.phase_index,
            vp.phase_description,
            vp.time_start,
            vp.time_end,
            COALESCE(vp.product_names, '') AS product_names,
            pi.insight
        FROM video_phases vp
        LEFT JOIN phase_insights pi
            ON pi.video_id = vp.video_id
            AND pi.phase_index = vp.phase_index
            AND pi.deleted_at IS NULL
        WHERE vp.video_id = ANY(:vids)
          AND (vp.phase_description IS NOT NULL OR pi.insight IS NOT NULL)
        ORDER BY vp.video_id, vp.phase_index ASC
    """)
    phase_result = await db.execute(phases_sql, {"vids": video_ids})
    all_phases = phase_result.fetchall()

    logger.info(f"Collected {len(all_phases)} phases with text data")

    if not all_phases:
        # Fallback: try speech_segments if available
        return await _build_from_speech_segments(db, video_ids, system_prompt)

    # 4. Group phases into conversational turns (sliding window by time)
    examples = _build_conversation_examples_from_phases(all_phases, system_prompt)

    # 5. Calculate stats
    total_duration_sec = sum(
        (p.time_end - p.time_start)
        for p in all_phases
        if p.time_start is not None and p.time_end is not None
    )

    return {
        "examples": examples,
        "video_count": len(video_ids),
        "segment_count": len(all_phases),
        "duration_hours": round(total_duration_sec / 3600, 2),
    }


async def _build_from_speech_segments(
    db: AsyncSession,
    video_ids: list,
    system_prompt: str,
) -> dict:
    """Fallback: try speech_segments if video_phases has no data."""
    segments_sql = text("""
        SELECT
            ss.text AS segment_text,
            ss.start_ms,
            ss.end_ms,
            ss.confidence,
            ac.video_id,
            ac.chunk_index
        FROM speech_segments ss
        JOIN audio_chunks ac ON ss.audio_chunk_id = ac.id
        WHERE ac.video_id = ANY(:vids)
          AND ss.confidence >= 0.5
        ORDER BY ac.video_id, ss.start_ms ASC
    """)
    try:
        seg_result = await db.execute(segments_sql, {"vids": video_ids})
        all_segments = seg_result.fetchall()
    except Exception:
        all_segments = []

    if not all_segments:
        return {
            "examples": [],
            "video_count": len(video_ids),
            "segment_count": 0,
            "duration_hours": 0.0,
        }

    examples = _build_conversation_examples_from_segments(all_segments, system_prompt)
    total_duration_ms = sum(
        (s.end_ms - s.start_ms) for s in all_segments
        if s.end_ms and s.start_ms
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


def _build_conversation_examples_from_phases(phases, system_prompt: str) -> list:
    """
    Build fine-tuning examples from video phase data.

    Strategy:
    - Group consecutive phases from the same video into ~60-90 second windows
    - phase_description = what the streamer actually said (transcript)
    - insight = GPT analysis of the scene (used as context/user prompt)
    - Each example: system prompt + user context → assistant response (transcript)
    """
    examples = []
    current_window = []
    window_start_sec = 0
    current_video_id = None

    WINDOW_SIZE_SEC = 75  # ~75 seconds per example

    for phase in phases:
        # New video = flush window
        if phase.video_id != current_video_id:
            if current_window:
                ex = _flush_phase_window(current_window, system_prompt)
                if ex:
                    examples.append(ex)
            current_window = []
            current_video_id = phase.video_id
            window_start_sec = phase.time_start or 0

        # Window full = flush
        time_start = phase.time_start or 0
        if time_start - window_start_sec > WINDOW_SIZE_SEC and current_window:
            ex = _flush_phase_window(current_window, system_prompt)
            if ex:
                examples.append(ex)
            current_window = []
            window_start_sec = time_start

        current_window.append(phase)

    # Flush remaining
    if current_window:
        ex = _flush_phase_window(current_window, system_prompt)
        if ex:
            examples.append(ex)

    return examples


def _flush_phase_window(phases, system_prompt: str) -> Optional[dict]:
    """Convert a window of phases into a fine-tuning example."""
    # Collect transcript text from phase_description
    transcript_parts = []
    insight_parts = []
    product_parts = []

    for p in phases:
        if p.phase_description:
            transcript_parts.append(p.phase_description.strip())
        if p.insight:
            insight_parts.append(p.insight.strip())
        if p.product_names:
            product_parts.append(p.product_names.strip())

    # The assistant response is the actual transcript
    combined_transcript = " ".join(transcript_parts).strip()

    # If no transcript, use insight as the assistant response
    if not combined_transcript and insight_parts:
        combined_transcript = " ".join(insight_parts).strip()

    if len(combined_transcript) < 20:
        return None  # Too short, skip

    # Build user context message
    context_parts = []

    if insight_parts:
        # Use insight as context for what's happening in the scene
        combined_insight = " ".join(insight_parts)
        # Truncate to reasonable length
        if len(combined_insight) > 500:
            combined_insight = combined_insight[:500] + "..."
        context_parts.append(f"シーンの状況: {combined_insight}")

    if product_parts:
        unique_products = list(set(p for p in product_parts if p))
        if unique_products:
            context_parts.append(f"紹介中の商品: {', '.join(unique_products)}")

    if context_parts:
        user_msg = "\n".join(context_parts) + "\n\nこの場面で自然に話してください。"
    else:
        user_msg = "ライブ配信を続けてください。自然に話してください。"

    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": combined_transcript},
        ]
    }


def _build_conversation_examples_from_segments(segments, system_prompt: str) -> list:
    """Fallback: Build examples from speech_segments."""
    examples = []
    current_window = []
    window_start_ms = 0
    current_video_id = None

    WINDOW_SIZE_MS = 45_000

    for seg in segments:
        if seg.video_id != current_video_id:
            if current_window:
                ex = _flush_segment_window(current_window, system_prompt)
                if ex:
                    examples.append(ex)
            current_window = []
            current_video_id = seg.video_id
            window_start_ms = seg.start_ms

        if seg.start_ms - window_start_ms > WINDOW_SIZE_MS and current_window:
            ex = _flush_segment_window(current_window, system_prompt)
            if ex:
                examples.append(ex)
            current_window = []
            window_start_ms = seg.start_ms

        current_window.append(seg)

    if current_window:
        ex = _flush_segment_window(current_window, system_prompt)
        if ex:
            examples.append(ex)

    return examples


def _flush_segment_window(segments, system_prompt: str) -> Optional[dict]:
    """Convert a window of speech segments into a fine-tuning example."""
    text_parts = [s.segment_text for s in segments if s.segment_text]
    combined_text = " ".join(text_parts).strip()

    if len(combined_text) < 20:
        return None

    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "ライブ配信を続けてください。自然に話してください。"},
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
