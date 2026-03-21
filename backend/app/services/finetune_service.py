# app/services/finetune_service.py  # v5 - use speech_segments as assistant output
"""
Service for building fine-tuning datasets from persona-tagged videos
and managing OpenAI fine-tuning jobs.

Data sources:
  - speech_segments (via audio_chunks) = actual streamer speech transcripts → assistant output
  - phase_insights.insight = GPT analysis of each phase → user context
  - video_phases.phase_description = scene description → additional context (NOT assistant output)

The key insight: assistant output MUST be the streamer's actual words,
not GPT-generated descriptions. This ensures the fine-tuned model
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
    Build a fine-tuning dataset using REAL speech transcripts as assistant output.

    Strategy:
    1. Get tagged videos (DONE status)
    2. For each video, get speech_segments (actual transcripts) via audio_chunks
    3. For each video, get phase_insights (GPT analysis) as context
    4. Match speech segments to phase time ranges
    5. Build examples: system + user(context) → assistant(real speech)

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

    # 3. Get REAL speech transcripts from speech_segments
    speech_sql = text("""
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
          AND LENGTH(ss.text) > 5
        ORDER BY ac.video_id, ss.start_ms ASC
    """)
    try:
        speech_result = await db.execute(speech_sql, {"vids": video_ids})
        all_speech = speech_result.fetchall()
    except Exception as e:
        logger.warning(f"Failed to get speech_segments: {e}")
        all_speech = []

    # 4. Get phase insights as context (user prompts)
    insights_sql = text("""
        SELECT
            pi.video_id,
            pi.phase_index,
            pi.insight,
            vp.time_start,
            vp.time_end,
            COALESCE(vp.product_names, '') AS product_names,
            vp.phase_description
        FROM phase_insights pi
        JOIN video_phases vp
            ON vp.video_id = pi.video_id
            AND vp.phase_index = pi.phase_index
        WHERE pi.video_id = ANY(:vids)
          AND pi.insight IS NOT NULL
        ORDER BY pi.video_id, pi.phase_index ASC
    """)
    try:
        insights_result = await db.execute(insights_sql, {"vids": video_ids})
        all_insights = insights_result.fetchall()
    except Exception as e:
        logger.warning(f"Failed to get phase_insights: {e}")
        all_insights = []

    logger.info(f"Collected {len(all_speech)} speech segments, {len(all_insights)} phase insights")

    # 5. Build training examples
    if all_speech:
        # PRIMARY: Use speech segments as assistant output, insights as context
        if all_insights:
            examples = _build_examples_speech_with_insights(
                all_speech, all_insights, system_prompt
            )
        else:
            # No insights available, use speech segments alone
            examples = _build_examples_speech_only(all_speech, system_prompt)
    else:
        # FALLBACK: No speech segments, use phase_description (less ideal)
        logger.warning("No speech_segments found, falling back to phase_description")
        examples = await _build_fallback_from_phases(db, video_ids, system_prompt)

    # 6. Calculate stats
    total_duration_ms = sum(
        (s.end_ms - s.start_ms) for s in all_speech
        if s.end_ms and s.start_ms
    ) if all_speech else 0

    return {
        "examples": examples,
        "video_count": len(video_ids),
        "segment_count": len(all_speech) if all_speech else 0,
        "duration_hours": round(total_duration_ms / 3_600_000, 2),
    }


def _build_examples_speech_with_insights(
    speech_segments: list,
    phase_insights: list,
    system_prompt: str,
) -> list:
    """
    Build training examples by matching speech segments to phase time ranges.

    For each phase (with insight as context):
    - Find all speech segments that fall within the phase's time range
    - Concatenate those speech segments as the assistant's response
    - Use the insight as the user's context prompt

    This produces examples where:
    - user = "状況: [GPT分析]" (what's happening in the scene)
    - assistant = "[実際の配信者の発言]" (what the streamer actually said)
    """
    examples = []

    # Group speech segments by video_id for efficient lookup
    speech_by_video = {}
    for seg in speech_segments:
        vid = str(seg.video_id)
        if vid not in speech_by_video:
            speech_by_video[vid] = []
        speech_by_video[vid].append(seg)

    for phase in phase_insights:
        vid = str(phase.video_id)
        if vid not in speech_by_video:
            continue

        time_start_ms = int((phase.time_start or 0) * 1000)
        time_end_ms = int((phase.time_end or 0) * 1000)

        if time_end_ms <= time_start_ms:
            continue

        # Find speech segments within this phase's time range
        matching_speech = []
        for seg in speech_by_video[vid]:
            seg_mid = (seg.start_ms + seg.end_ms) / 2
            if time_start_ms <= seg_mid <= time_end_ms:
                matching_speech.append(seg)

        if not matching_speech:
            continue

        # Build assistant response from actual speech
        assistant_text = " ".join(
            s.segment_text.strip() for s in matching_speech
            if s.segment_text and s.segment_text.strip()
        )

        if len(assistant_text) < 30:
            continue  # Too short, skip

        # Build user context from insight
        user_parts = []

        insight_text = phase.insight.strip() if phase.insight else ""
        if insight_text:
            # Extract actionable parts from insight (trim to reasonable length)
            if len(insight_text) > 400:
                insight_text = insight_text[:400] + "..."
            user_parts.append(f"配信の状況: {insight_text}")

        products = phase.product_names.strip() if phase.product_names else ""
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

    # Also add speech-only examples for segments not covered by phases
    # This captures the streamer's natural speech patterns
    speech_only_extras = _build_speech_only_extras(
        speech_segments, phase_insights, system_prompt, max_extras=30
    )
    examples.extend(speech_only_extras)

    return examples


def _build_speech_only_extras(
    speech_segments: list,
    phase_insights: list,
    system_prompt: str,
    max_extras: int = 30,
) -> list:
    """
    Build additional examples from speech segments NOT covered by any phase.
    Groups them into ~45-second windows with generic user prompts.
    """
    # Build set of covered time ranges per video
    covered_ranges = {}
    for phase in phase_insights:
        vid = str(phase.video_id)
        if vid not in covered_ranges:
            covered_ranges[vid] = []
        ts = int((phase.time_start or 0) * 1000)
        te = int((phase.time_end or 0) * 1000)
        covered_ranges[vid].append((ts, te))

    # Find uncovered speech segments
    uncovered = []
    for seg in speech_segments:
        vid = str(seg.video_id)
        seg_mid = (seg.start_ms + seg.end_ms) / 2
        is_covered = False
        for (ts, te) in covered_ranges.get(vid, []):
            if ts <= seg_mid <= te:
                is_covered = True
                break
        if not is_covered:
            uncovered.append(seg)

    if not uncovered:
        return []

    # Group uncovered segments into windows
    examples = []
    current_window = []
    window_start_ms = 0
    current_video_id = None

    WINDOW_SIZE_MS = 45_000  # 45 seconds

    prompts = [
        "ライブ配信を続けてください。視聴者と自然に話してください。",
        "視聴者に向けて話しかけてください。",
        "配信を盛り上げてください。自然体で話してください。",
        "今の話題について、あなたらしく続けてください。",
        "視聴者のコメントに反応しながら話してください。",
    ]

    for seg in uncovered:
        if seg.video_id != current_video_id:
            if current_window:
                ex = _flush_speech_window(current_window, system_prompt, prompts, len(examples))
                if ex:
                    examples.append(ex)
            current_window = []
            current_video_id = seg.video_id
            window_start_ms = seg.start_ms

        if seg.start_ms - window_start_ms > WINDOW_SIZE_MS and current_window:
            ex = _flush_speech_window(current_window, system_prompt, prompts, len(examples))
            if ex:
                examples.append(ex)
            current_window = []
            window_start_ms = seg.start_ms

        current_window.append(seg)

        if len(examples) >= max_extras:
            break

    if current_window and len(examples) < max_extras:
        ex = _flush_speech_window(current_window, system_prompt, prompts, len(examples))
        if ex:
            examples.append(ex)

    return examples[:max_extras]


def _flush_speech_window(segments, system_prompt, prompts, index) -> Optional[dict]:
    """Convert a window of speech segments into a training example."""
    text_parts = [s.segment_text.strip() for s in segments if s.segment_text and s.segment_text.strip()]
    combined = " ".join(text_parts)

    if len(combined) < 30:
        return None

    user_msg = prompts[index % len(prompts)]

    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": combined},
        ]
    }


def _build_examples_speech_only(speech_segments: list, system_prompt: str) -> list:
    """Build examples from speech segments only (no phase insights available)."""
    examples = []
    current_window = []
    window_start_ms = 0
    current_video_id = None

    WINDOW_SIZE_MS = 60_000  # 60 seconds per example

    prompts = [
        "ライブ配信を続けてください。視聴者と自然に話してください。",
        "視聴者に向けて話しかけてください。",
        "配信を盛り上げてください。自然体で話してください。",
        "今の話題について、あなたらしく続けてください。",
        "視聴者のコメントに反応しながら話してください。",
        "商品について詳しく説明してください。",
        "視聴者からの質問に答えてください。",
    ]

    for seg in speech_segments:
        if seg.video_id != current_video_id:
            if current_window:
                ex = _flush_speech_window(current_window, system_prompt, prompts, len(examples))
                if ex:
                    examples.append(ex)
            current_window = []
            current_video_id = seg.video_id
            window_start_ms = seg.start_ms

        if seg.start_ms - window_start_ms > WINDOW_SIZE_MS and current_window:
            ex = _flush_speech_window(current_window, system_prompt, prompts, len(examples))
            if ex:
                examples.append(ex)
            current_window = []
            window_start_ms = seg.start_ms

        current_window.append(seg)

    if current_window:
        ex = _flush_speech_window(current_window, system_prompt, prompts, len(examples))
        if ex:
            examples.append(ex)

    return examples


async def _build_fallback_from_phases(
    db: AsyncSession,
    video_ids: list,
    system_prompt: str,
) -> list:
    """Fallback when no speech_segments exist. Uses phase_description (less ideal)."""
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
        WHERE vp.video_id = ANY(:vids)
          AND vp.phase_description IS NOT NULL
        ORDER BY vp.video_id, vp.phase_index ASC
    """)
    try:
        result = await db.execute(phases_sql, {"vids": video_ids})
        phases = result.fetchall()
    except Exception:
        return []

    if not phases:
        return []

    examples = []
    for phase in phases:
        desc = phase.phase_description.strip() if phase.phase_description else ""
        if len(desc) < 30:
            continue

        insight = phase.insight.strip() if phase.insight else ""
        if insight:
            if len(insight) > 400:
                insight = insight[:400] + "..."
            user_msg = f"配信の状況: {insight}\n\nこの場面であなたらしく話してください。"
        else:
            user_msg = "ライブ配信を続けてください。あなたらしく自然に話してください。"

        examples.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": desc},
            ]
        })

    return examples


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
        "model": job.fine_tuned_model,
        "created_at": str(job.created_at) if job.created_at else None,
        "finished_at": str(job.finished_at) if job.finished_at else None,
        "trained_tokens": job.trained_tokens,
        "error": job.error.message if job.error else None,
    }
