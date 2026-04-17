# app/services/finetune_service.py  # v6 - use video_phases.audio_text as assistant output
"""
Service for building fine-tuning datasets from persona-tagged videos
and managing OpenAI fine-tuning jobs.

Data sources:
  - video_phases.audio_text = Whisper transcription of actual streamer speech → assistant output
  - phase_insights.insight = GPT analysis of each phase → user context
  - video_phases.phase_description = scene description → NOT used as assistant output

The key insight: assistant output MUST be the streamer's actual words (audio_text),
not GPT-generated descriptions (phase_description). This ensures the fine-tuned model
speaks like the streamer, not like an analyst.
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
        # Prefer OPENAI_FINETUNE_API_KEY (real OpenAI key) over OPENAI_API_KEY (may be proxy)
        api_key = os.getenv("OPENAI_FINETUNE_API_KEY") or os.getenv("OPENAI_API_KEY")
        _openai_client = OpenAI(
            api_key=api_key,
            base_url="https://api.openai.com/v1",  # Use original OpenAI for fine-tuning
        )
    return _openai_client


# ── Dataset building ──

async def build_training_dataset(
    db: AsyncSession,
    persona_id: str,
) -> dict:
    """
    Build a fine-tuning dataset using video_phases.audio_text (Whisper transcripts)
    as assistant output.

    Strategy:
    1. Get tagged videos (DONE status) with video_phases data
    2. For each phase, use audio_text (actual speech) as assistant output
    3. Use phase_insights.insight as user context
    4. Fall back to phase_description only if audio_text is empty

    Returns: {
        "examples": [...],
        "video_count": int,
        "segment_count": int,
        "duration_hours": float,
    }
    """
    # 1. Get tagged video IDs
    tag_sql = text("""
        SELECT pvt.video_id, v.original_filename, v.status
        FROM persona_video_tags pvt
        JOIN videos v ON pvt.video_id = v.id
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

    video_ids = [v.video_id for v in tagged_videos]
    logger.info(f"Building dataset for persona {persona_id}: {len(video_ids)} videos")

    # 2. Get persona info for system prompt
    persona_sql = text("""
        SELECT name, description, style_prompt FROM personas WHERE id = :pid
    """)
    persona_result = await db.execute(persona_sql, {"pid": persona_id})
    persona = persona_result.fetchone()
    system_prompt = _build_system_prompt(persona)

    # 3. Get video_phases with audio_text AND phase_insights
    #    Exclude phases whose clips have been marked as unusable (NG)
    phases_sql = text("""
        SELECT
            vp.video_id,
            vp.phase_index,
            vp.audio_text,
            vp.phase_description,
            vp.time_start,
            vp.time_end,
            COALESCE(vp.product_names, '') AS product_names,
            pi.insight
        FROM video_phases vp
        LEFT JOIN phase_insights pi
            ON pi.video_id = vp.video_id
            AND pi.phase_index = vp.phase_index
        WHERE vp.video_id = ANY(:vids)
          AND NOT EXISTS (
              SELECT 1 FROM video_clips vc
              WHERE vc.video_id = vp.video_id
                AND CASE
                    WHEN vc.phase_index ~ '^[0-9]+$' THEN CAST(vc.phase_index AS INTEGER)
                    ELSE -1
                END = vp.phase_index
                AND COALESCE(vc.is_unusable, FALSE) = TRUE
          )
        ORDER BY vp.video_id, vp.phase_index ASC
    """)
    try:
        result = await db.execute(phases_sql, {"vids": video_ids})
        all_phases = result.fetchall()
    except Exception as e:
        logger.exception(f"Failed to get video_phases: {e}")
        return {
            "examples": [],
            "video_count": len(video_ids),
            "segment_count": 0,
            "duration_hours": 0.0,
            "error": str(e),
        }

    logger.info(f"Collected {len(all_phases)} phases from {len(video_ids)} videos")

    # 4. Build training examples
    examples = []
    audio_text_count = 0
    fallback_count = 0
    total_duration_sec = 0.0

    for phase in all_phases:
        audio_text = (phase.audio_text or "").strip()
        phase_desc = (phase.phase_description or "").strip()
        insight = (phase.insight or "").strip()

        # Calculate duration
        t_start = phase.time_start or 0
        t_end = phase.time_end or 0
        if t_end > t_start:
            total_duration_sec += (t_end - t_start)

        # Determine assistant output: prefer audio_text (real speech)
        if audio_text and len(audio_text) >= 30:
            assistant_text = audio_text
            audio_text_count += 1
        elif phase_desc and len(phase_desc) >= 30:
            # Fallback to phase_description (less ideal but better than nothing)
            assistant_text = phase_desc
            fallback_count += 1
        else:
            continue  # Skip phases with no usable content

        # Build user context from insight
        user_parts = []
        if insight:
            trimmed_insight = insight[:400] + "..." if len(insight) > 400 else insight
            user_parts.append(f"配信の状況: {trimmed_insight}")

        products = (phase.product_names or "").strip()
        if products:
            user_parts.append(f"紹介中の商品: {products}")

        if user_parts:
            user_msg = "\n".join(user_parts) + "\n\nこの場面であなたらしく話してください。"
        else:
            user_msg = "ライブ配信を続けてください。あなたらしく自然に話してください。"

        examples.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_text},
            ]
        })

    logger.info(
        f"Built {len(examples)} examples "
        f"(audio_text: {audio_text_count}, fallback_desc: {fallback_count})"
    )

    return {
        "examples": examples,
        "video_count": len(video_ids),
        "segment_count": len(all_phases),
        "duration_hours": round(total_duration_sec / 3600, 2),
        "audio_text_examples": audio_text_count,
        "fallback_examples": fallback_count,
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
- ライブ配信のテンポ感を大切にする
- あなた自身の言葉で話す（第三者視点の描写はしない）"""

    return prompt.strip()


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
        "created_at": str(job.created_at) if job.created_at else None,
        "finished_at": str(job.finished_at) if job.finished_at else None,
        "trained_tokens": job.trained_tokens,
        "error": job.error.message if job.error else None,
    }
