# app/api/v1/endpoints/persona.py
"""
Persona (Streamer Clone) management endpoints.
Handles CRUD, video tagging, dataset building, and fine-tuning.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Header, Query, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.services import finetune_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/personas", tags=["Persona Clone"])

ADMIN_ID = "aither"
ADMIN_PASS = "hub"


def _check_admin(key: Optional[str]):
    expected = f"{ADMIN_ID}:{ADMIN_PASS}"
    if key != expected:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")


# ── Schemas ──

class PersonaCreate(BaseModel):
    name: str = Field(..., description="Persona display name")
    description: Optional[str] = Field(None, description="Profile description")
    user_email: Optional[str] = Field(None, description="Associated user email")
    voice_id: Optional[str] = Field(None, description="ElevenLabs voice ID")
    style_prompt: Optional[str] = Field(None, description="Speaking style description")


class LivePersonaConfig(BaseModel):
    """Enhanced persona settings for AI Auto Live v3."""
    host_name: Optional[str] = Field(None, description="Streamer display name")
    catchphrases: Optional[List[str]] = Field(None, description="Catchphrases / signature phrases")
    speaking_style: Optional[str] = Field(None, description="Speaking style description")
    expertise: Optional[str] = Field(None, description="Expertise / career background")
    brand_story: Optional[str] = Field(None, description="Brand story")
    self_introduction: Optional[str] = Field(None, description="Opening self-introduction")
    flow_preset: Optional[str] = Field(None, description="Flow preset: short/standard/long")
    language: Optional[str] = Field(None, description="Language code")
    style: Optional[str] = Field(None, description="Style: professional/casual/energetic")


class PersonaUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    user_email: Optional[str] = None
    voice_id: Optional[str] = None
    voice_name: Optional[str] = None
    style_prompt: Optional[str] = None
    finetune_model_id: Optional[str] = None
    live_persona_config: Optional[LivePersonaConfig] = None


class VideoTagRequest(BaseModel):
    video_ids: List[str] = Field(..., description="List of video IDs to tag")


class VideoUntagRequest(BaseModel):
    video_ids: List[str] = Field(..., description="List of video IDs to untag")


class TrainRequest(BaseModel):
    base_model: str = Field(
        default="gpt-4.1-mini-2025-04-14",
        description="Base model for fine-tuning"
    )
    n_epochs: int = Field(default=3, ge=1, le=10)


# ── CRUD ──

@router.get("")
async def list_personas(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """List all personas."""
    _check_admin(x_admin_key)

    sql = text("""
        SELECT p.*,
            (SELECT COUNT(*) FROM persona_video_tags pvt WHERE pvt.persona_id = p.id) AS tagged_video_count,
            (SELECT COALESCE(array_agg(pvt.video_id::text), ARRAY[]::text[])
             FROM persona_video_tags pvt WHERE pvt.persona_id = p.id) AS tagged_video_ids
        FROM personas p
        WHERE p.deleted_at IS NULL
        ORDER BY p.created_at DESC
    """)
    result = await db.execute(sql)
    rows = result.fetchall()

    personas = []
    for r in rows:
        personas.append({
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "user_email": r.user_email,
            "voice_id": r.voice_id,
            "voice_name": r.voice_name,
            "finetune_model_id": r.finetune_model_id,
            "finetune_status": r.finetune_status,
            "style_prompt": r.style_prompt,
            "training_video_count": r.training_video_count,
            "training_segment_count": r.training_segment_count,
            "training_duration_hours": r.training_duration_hours,
            "tagged_video_count": r.tagged_video_count,
            "tagged_video_ids": list(r.tagged_video_ids) if r.tagged_video_ids else [],
            "live_persona_config": r.live_persona_config if hasattr(r, 'live_persona_config') else None,
            "created_at": str(r.created_at) if r.created_at else None,
            "updated_at": str(r.updated_at) if r.updated_at else None,
        })

    return {"personas": personas, "total": len(personas)}


@router.post("")
async def create_persona(
    body: PersonaCreate,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Create a new persona."""
    _check_admin(x_admin_key)

    import uuid
    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    sql = text("""
        INSERT INTO personas (id, name, description, user_email, voice_id, style_prompt,
                              finetune_status, training_video_count, training_segment_count,
                              training_duration_hours, created_at, updated_at)
        VALUES (:id, :name, :desc, :email, :voice, :style,
                'none', 0, 0, 0.0, :now, :now)
        RETURNING id
    """)
    await db.execute(sql, {
        "id": pid, "name": body.name, "desc": body.description,
        "email": body.user_email, "voice": body.voice_id,
        "style": body.style_prompt, "now": now,
    })
    await db.commit()

    logger.info(f"Created persona: {pid} ({body.name})")
    return {"id": pid, "name": body.name, "status": "created"}


@router.get("/{persona_id}")
async def get_persona(
    persona_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Get persona details including tagged videos."""
    _check_admin(x_admin_key)

    try:
        # Persona info
        p_sql = text("SELECT * FROM personas WHERE id = :pid AND deleted_at IS NULL")
        p_result = await db.execute(p_sql, {"pid": persona_id})
        persona = p_result.fetchone()
        if not persona:
            raise HTTPException(status_code=404, detail="Persona not found")

        # Tagged videos
        try:
            v_sql = text("""
                SELECT pvt.video_id, v.original_filename, v.status, v.created_at,
                       pvt.included_in_training
                FROM persona_video_tags pvt
        JOIN videos v ON pvt.video_id = v.id
        WHERE pvt.persona_id = :pid
        ORDER BY v.created_at DESC
            """)
            v_result = await db.execute(v_sql, {"pid": persona_id})
            tagged_videos = v_result.fetchall()
        except Exception as e:
            logger.exception(f"Error fetching tagged videos: {e}")
            tagged_videos = []

        # Training logs
        try:
            t_sql = text("""
                SELECT * FROM persona_training_logs
                WHERE persona_id = :pid
                ORDER BY created_at DESC
                LIMIT 10
            """)
            t_result = await db.execute(t_sql, {"pid": persona_id})
            training_logs = t_result.fetchall()
        except Exception as e:
            logger.exception(f"Error fetching training logs: {e}")
            training_logs = []
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error in get_persona: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

    return {
        "persona": {
            "id": persona.id,
            "name": persona.name,
            "description": persona.description,
            "user_email": persona.user_email,
            "voice_id": persona.voice_id,
            "voice_name": persona.voice_name,
            "finetune_model_id": persona.finetune_model_id,
            "finetune_job_id": persona.finetune_job_id,
            "finetune_status": persona.finetune_status,
            "style_prompt": persona.style_prompt,
            "training_video_count": persona.training_video_count,
            "training_segment_count": persona.training_segment_count,
            "training_duration_hours": persona.training_duration_hours,
            "live_persona_config": persona.live_persona_config if hasattr(persona, 'live_persona_config') else None,
            "created_at": str(persona.created_at) if persona.created_at else None,
        },
        "tagged_videos": [
            {
                "video_id": v.video_id,
                "filename": v.original_filename,
                "status": v.status,
                "created_at": str(v.created_at) if v.created_at else None,
                "included_in_training": v.included_in_training,
            }
            for v in tagged_videos
        ],
        "training_logs": [
            {
                "id": t.id,
                "openai_job_id": t.openai_job_id,
                "status": t.status,
                "model_id": t.model_id,
                "video_count": t.video_count,
                "segment_count": t.segment_count,
                "duration_hours": t.duration_hours,
                "training_examples": t.training_examples,
                "error_message": t.error_message,
                "started_at": str(t.started_at) if t.started_at else None,
                "completed_at": str(t.completed_at) if t.completed_at else None,
                "created_at": str(t.created_at) if t.created_at else None,
            }
            for t in training_logs
        ],
    }


@router.put("/{persona_id}")
async def update_persona(
    persona_id: str,
    body: PersonaUpdate,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Update persona details."""
    _check_admin(x_admin_key)

    updates = []
    params = {"pid": persona_id, "now": datetime.now(timezone.utc)}

    for field in ["name", "description", "user_email", "voice_id",
                  "voice_name", "style_prompt", "finetune_model_id"]:
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = :{field}")
            params[field] = val

    # Handle JSON live_persona_config
    if body.live_persona_config is not None:
        import json
        updates.append("live_persona_config = :live_persona_config")
        params["live_persona_config"] = json.dumps(body.live_persona_config.model_dump(exclude_none=True))

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = :now")
    sql = text(f"UPDATE personas SET {', '.join(updates)} WHERE id = :pid AND deleted_at IS NULL")
    await db.execute(sql, params)
    await db.commit()

    return {"status": "updated", "persona_id": persona_id}


@router.delete("/{persona_id}")
async def delete_persona(
    persona_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a persona."""
    _check_admin(x_admin_key)

    now = datetime.now(timezone.utc)
    sql = text("UPDATE personas SET deleted_at = :now WHERE id = :pid")
    await db.execute(sql, {"pid": persona_id, "now": now})
    await db.commit()

    return {"status": "deleted", "persona_id": persona_id}


# ── Video Tagging ──

@router.post("/{persona_id}/tag-videos")
async def tag_videos(
    persona_id: str,
    body: VideoTagRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Tag videos for this persona's training data."""
    _check_admin(x_admin_key)

    import uuid
    now = datetime.now(timezone.utc)
    added = 0

    for vid in body.video_ids:
        # Check if already tagged
        check_sql = text("""
            SELECT id FROM persona_video_tags
            WHERE persona_id = :pid AND video_id = :vid
        """)
        existing = await db.execute(check_sql, {"pid": persona_id, "vid": vid})
        if existing.fetchone():
            continue

        tag_sql = text("""
            INSERT INTO persona_video_tags (id, persona_id, video_id, included_in_training, created_at, updated_at)
            VALUES (:id, :pid, :vid, false, :now, :now)
        """)
        await db.execute(tag_sql, {
            "id": str(uuid.uuid4()), "pid": persona_id, "vid": vid, "now": now,
        })
        added += 1

    await db.commit()
    logger.info(f"Tagged {added} videos for persona {persona_id}")

    return {"status": "tagged", "added": added, "total_requested": len(body.video_ids)}


@router.post("/{persona_id}/untag-videos")
async def untag_videos(
    persona_id: str,
    body: VideoUntagRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Remove video tags from this persona."""
    _check_admin(x_admin_key)

    for vid in body.video_ids:
        sql = text("""
            DELETE FROM persona_video_tags
            WHERE persona_id = :pid AND video_id = :vid
        """)
        await db.execute(sql, {"pid": persona_id, "vid": vid})

    await db.commit()
    return {"status": "untagged", "removed": len(body.video_ids)}


# ── Dataset Preview ──

@router.get("/{persona_id}/dataset-preview")
async def dataset_preview(
    persona_id: str,
    limit: int = Query(5, ge=1, le=20),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Preview the training dataset that would be generated."""
    _check_admin(x_admin_key)

    try:
        dataset = await finetune_service.build_training_dataset(db, persona_id)
    except Exception as e:
        logger.exception(f"dataset-preview error: {e}")
        raise HTTPException(status_code=500, detail=f"Dataset build error: {str(e)}")

    return {
        "video_count": dataset["video_count"],
        "segment_count": dataset["segment_count"],
        "duration_hours": dataset["duration_hours"],
        "total_examples": len(dataset["examples"]),
        "audio_text_examples": dataset.get("audio_text_examples", 0),
        "fallback_examples": dataset.get("fallback_examples", 0),
        "preview_examples": dataset["examples"][:limit],
    }


# ── Training ──

@router.post("/{persona_id}/train")
async def start_training(
    persona_id: str,
    body: TrainRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Start fine-tuning for this persona."""
    _check_admin(x_admin_key)

    # Check persona exists
    p_sql = text("SELECT * FROM personas WHERE id = :pid AND deleted_at IS NULL")
    p_result = await db.execute(p_sql, {"pid": persona_id})
    persona = p_result.fetchone()
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    if persona.finetune_status == "training":
        raise HTTPException(status_code=409, detail="Training already in progress")

    # Build dataset
    dataset = await finetune_service.build_training_dataset(db, persona_id)

    if len(dataset["examples"]) < 10:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough training data. Need at least 10 examples, got {len(dataset['examples'])}. Tag more videos."
        )

    # Create training log
    import uuid
    log_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    log_sql = text("""
        INSERT INTO persona_training_logs
            (id, persona_id, status, video_count, segment_count,
             duration_hours, training_examples, started_at, created_at, updated_at)
        VALUES (:id, :pid, 'preparing', :vc, :sc, :dh, :te, :now, :now, :now)
    """)
    await db.execute(log_sql, {
        "id": log_id, "pid": persona_id,
        "vc": dataset["video_count"], "sc": dataset["segment_count"],
        "dh": dataset["duration_hours"], "te": len(dataset["examples"]),
        "now": now,
    })

    # Update persona status
    await db.execute(text("""
        UPDATE personas SET finetune_status = 'preparing', updated_at = :now
        WHERE id = :pid
    """), {"pid": persona_id, "now": now})

    await db.commit()

    # Start fine-tuning in background
    background_tasks.add_task(
        _run_finetune_background,
        persona_id, log_id, dataset["examples"],
        body.base_model, body.n_epochs,
    )

    logger.info(
        f"Starting fine-tuning for persona {persona_id}: "
        f"{len(dataset['examples'])} examples, {dataset['video_count']} videos"
    )

    return {
        "status": "training_started",
        "log_id": log_id,
        "examples_count": len(dataset["examples"]),
        "video_count": dataset["video_count"],
        "duration_hours": dataset["duration_hours"],
    }


async def _run_finetune_background(
    persona_id: str,
    log_id: str,
    examples: list,
    base_model: str,
    n_epochs: int,
):
    """Background task to run fine-tuning."""
    from app.core.db import AsyncSessionLocal

    try:
        # Create fine-tuning job
        result = finetune_service.create_finetune_job(
            examples=examples,
            base_model=base_model,
            n_epochs=n_epochs,
            suffix=f"persona-{persona_id[:8]}",
        )

        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)

            # Update training log
            await db.execute(text("""
                UPDATE persona_training_logs
                SET openai_job_id = :jid, status = 'training', updated_at = :now
                WHERE id = :lid
            """), {"jid": result["job_id"], "lid": log_id, "now": now})

            # Update persona
            await db.execute(text("""
                UPDATE personas
                SET finetune_status = 'training', finetune_job_id = :jid, updated_at = :now
                WHERE id = :pid
            """), {"jid": result["job_id"], "pid": persona_id, "now": now})

            await db.commit()

        logger.info(f"Fine-tuning job created: {result['job_id']}")

    except Exception as e:
        logger.exception(f"Fine-tuning failed for persona {persona_id}: {e}")

        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)
            await db.execute(text("""
                UPDATE persona_training_logs
                SET status = 'failed', error_message = :err, updated_at = :now
                WHERE id = :lid
            """), {"err": str(e), "lid": log_id, "now": now})

            await db.execute(text("""
                UPDATE personas SET finetune_status = 'failed', updated_at = :now
                WHERE id = :pid
            """), {"pid": persona_id, "now": now})

            await db.commit()


# ── Training Status ──

@router.get("/{persona_id}/training-status")
async def get_training_status(
    persona_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Check and update the training status from OpenAI."""
    _check_admin(x_admin_key)

    p_sql = text("SELECT finetune_job_id, finetune_status FROM personas WHERE id = :pid")
    p_result = await db.execute(p_sql, {"pid": persona_id})
    persona = p_result.fetchone()

    if not persona or not persona.finetune_job_id:
        # Still return logs even if no active job
        logs = []
        if persona:
            try:
                logs_sql = text("""
                    SELECT id, status, openai_job_id, model_id,
                           error_message, created_at
                    FROM persona_training_logs
                    WHERE persona_id = :pid
                    ORDER BY created_at DESC LIMIT 10
                """)
                logs_result = await db.execute(logs_sql, {"pid": persona_id})
                logs = [
                    {
                        "id": str(r.id), "status": r.status,
                        "base_model": None,
                        "openai_job_id": r.openai_job_id,
                        "error_message": r.error_message,
                        "created_at": str(r.created_at) if r.created_at else None,
                    }
                    for r in logs_result.fetchall()
                ]
            except Exception:
                pass
        return {
            "status": persona.finetune_status if persona else "none",
            "message": "No active training job",
            "logs": logs,
        }

    # Poll OpenAI
    try:
        status = finetune_service.get_finetune_status(persona.finetune_job_id)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    now = datetime.now(timezone.utc)

    # Update if status changed
    if status["status"] == "succeeded" and persona.finetune_status != "completed":
        await db.execute(text("""
            UPDATE personas
            SET finetune_status = 'completed',
                finetune_model_id = :mid,
                updated_at = :now
            WHERE id = :pid
        """), {"mid": status["model_id"], "pid": persona_id, "now": now})

        await db.execute(text("""
            UPDATE persona_training_logs
            SET status = 'completed', model_id = :mid, completed_at = :now, updated_at = :now
            WHERE persona_id = :pid AND openai_job_id = :jid
        """), {"mid": status["model_id"], "pid": persona_id, "jid": persona.finetune_job_id, "now": now})

        # Mark tagged videos as included
        await db.execute(text("""
            UPDATE persona_video_tags SET included_in_training = true
            WHERE persona_id = :pid
        """), {"pid": persona_id})

        await db.commit()
        logger.info(f"Fine-tuning completed for persona {persona_id}: model={status['model_id']}")

    elif status["status"] == "failed" and persona.finetune_status != "failed":
        await db.execute(text("""
            UPDATE personas SET finetune_status = 'failed', updated_at = :now
            WHERE id = :pid
        """), {"pid": persona_id, "now": now})

        await db.execute(text("""
            UPDATE persona_training_logs
            SET status = 'failed', error_message = :err, updated_at = :now
            WHERE persona_id = :pid AND openai_job_id = :jid
        """), {"err": status.get("error"), "pid": persona_id, "jid": persona.finetune_job_id, "now": now})

        await db.commit()

    # Fetch training logs
    logs_sql = text("""
        SELECT id, status, openai_job_id, model_id,
               video_count, segment_count, training_examples,
               error_message, started_at, completed_at, created_at
        FROM persona_training_logs
        WHERE persona_id = :pid
        ORDER BY created_at DESC
        LIMIT 10
    """)
    logs_result = await db.execute(logs_sql, {"pid": persona_id})
    logs = [
        {
            "id": str(r.id),
            "status": r.status,
            "base_model": None,
            "openai_job_id": r.openai_job_id,
            "model_id": r.model_id if hasattr(r, 'model_id') else None,
            "error_message": r.error_message,
            "created_at": str(r.created_at) if r.created_at else None,
        }
        for r in logs_result.fetchall()
    ]

    return {
        "status": status["status"],
        "model_id": status.get("model_id"),
        "trained_tokens": status.get("trained_tokens"),
        "error": status.get("error"),
        "logs": logs,
    }


# ── Available Videos (for tagging UI) ──

@router.get("/{persona_id}/available-videos")
async def get_available_videos(
    persona_id: str,
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """List videos available for tagging (DONE status, with training data)."""
    _check_admin(x_admin_key)

    try:
        return await _get_available_videos_impl(persona_id, search, limit, offset, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"available-videos error: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


async def _get_available_videos_impl(persona_id, search, limit, offset, db):
    where_clause = "v.status = 'DONE'"
    params = {"pid": persona_id, "limit": limit, "offset": offset}

    if search:
        where_clause += " AND v.original_filename ILIKE :search"
        params["search"] = f"%{search}%"

    # Simple query - get videos with phase count
    sql = text(f"""
        SELECT v.id, v.original_filename, v.created_at, v.upload_type,
               (SELECT COUNT(*) FROM video_phases vp
                WHERE vp.video_id = v.id
                AND vp.phase_description IS NOT NULL) AS segment_count,
               CASE WHEN pvt.id IS NOT NULL THEN true ELSE false END AS is_tagged
        FROM videos v
        LEFT JOIN persona_video_tags pvt
            ON pvt.video_id = v.id AND pvt.persona_id = :pid
        WHERE {where_clause}
        ORDER BY v.created_at DESC
        LIMIT :limit OFFSET :offset
    """)
    result = await db.execute(sql, params)
    rows = result.fetchall()

    # Total count
    count_sql = text(f"SELECT COUNT(*) FROM videos v WHERE {where_clause}")
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset", "pid")}
    count_result = await db.execute(count_sql, count_params)
    total = count_result.scalar() or 0

    return {
        "total": total,
        "videos": [
            {
                "id": str(r.id),
                "filename": r.original_filename,
                "segment_count": r.segment_count,
                "is_tagged": r.is_tagged,
                "created_at": str(r.created_at) if r.created_at else None,
            }
            for r in rows
        ],
    }


# ── Chat with Fine-tuned Persona ──

class ChatRequest(BaseModel):
    message: str = Field(..., description="User message to the persona")
    context: Optional[str] = Field(None, description="Optional context (e.g. current scene, products)")
    history: Optional[List[dict]] = Field(default_factory=list, description="Previous conversation messages")


@router.post("/{persona_id}/chat")
async def chat_with_persona(
    persona_id: str,
    body: ChatRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Chat with a fine-tuned persona model."""
    _check_admin(x_admin_key)

    # Get persona
    result = await db.execute(
        text("SELECT * FROM personas WHERE id = :pid"),
        {"pid": persona_id},
    )
    persona = result.fetchone()
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    model_id = persona.finetune_model_id
    if not model_id:
        raise HTTPException(
            status_code=400,
            detail="Persona has no fine-tuned model. Please train first.",
        )

    # Build system prompt
    system_prompt = finetune_service._build_system_prompt(persona)

    # Add context if provided
    if body.context:
        system_prompt += f"\n\n現在の状況:\n{body.context}"

    # Build messages
    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history
    if body.history:
        for msg in body.history[-10:]:  # Keep last 10 messages
            if msg.get("role") in ("user", "assistant"):
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

    # Add current user message
    messages.append({"role": "user", "content": body.message})

    try:
        client = finetune_service._get_openai()
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=0.8,
            max_tokens=1024,
        )

        assistant_message = response.choices[0].message.content

        return {
            "response": assistant_message,
            "model": model_id,
            "persona_name": persona.name,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        }
    except Exception as e:
        logger.exception(f"Chat error for persona {persona_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")


# ── Generate Script with Fine-tuned Persona ──

class ProductInfo(BaseModel):
    name: str = Field(..., description="Product name")
    description: Optional[str] = Field(None, description="Product description")
    price: Optional[str] = Field(None, description="Product price")
    features: Optional[str] = Field(None, description="Product features (comma-separated)")
    category: Optional[str] = Field(None, description="Product category")
    # Enhanced TikTok product data
    selling_points: Optional[List[str]] = Field(default_factory=list, description="Key selling points with evidence")
    achievements: Optional[List[str]] = Field(default_factory=list, description="Rankings, awards, sales milestones")
    reviews_summary: Optional[str] = Field(None, description="Review/rating summary")
    sold_info: Optional[str] = Field(None, description="Sales performance info")
    target_audience: Optional[str] = Field(None, description="Target audience description")
    talk_hooks: Optional[List[str]] = Field(default_factory=list, description="Phrases to hook viewer interest")
    variants: Optional[List[str]] = Field(default_factory=list, description="Product variants/colors/sizes")


class ScriptRequest(BaseModel):
    products: Optional[List[ProductInfo]] = Field(default_factory=list, description="Products to introduce with details")
    product_names: Optional[List[str]] = Field(default_factory=list, description="Simple product names (fallback)")
    duration_minutes: int = Field(5, description="Target script duration in minutes")
    style: Optional[str] = Field(None, description="Script style (e.g. energetic, calm)")
    notes: Optional[str] = Field(None, description="Additional notes for script generation")


@router.post("/{persona_id}/generate-script")
async def generate_script(
    persona_id: str,
    body: ScriptRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Generate a live commerce script using the fine-tuned persona model."""
    _check_admin(x_admin_key)

    # Get persona
    result = await db.execute(
        text("SELECT * FROM personas WHERE id = :pid"),
        {"pid": persona_id},
    )
    persona = result.fetchone()
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    model_id = persona.finetune_model_id
    if not model_id:
        raise HTTPException(
            status_code=400,
            detail="Persona has no fine-tuned model. Please train first.",
        )

    # Build system prompt
    system_prompt = finetune_service._build_system_prompt(persona)

    # Build script generation prompt
    # Build rich product descriptions
    products_text = ""
    if body.products:
        product_lines = []
        for p in body.products:
            if hasattr(p, 'name'):
                # ProductInfo object
                parts = [f"商品名: {p.name}"]
                if p.description:
                    parts.append(f"  説明: {p.description}")
                if p.price:
                    parts.append(f"  価格: {p.price}")
                if p.features:
                    parts.append(f"  特徴: {p.features}")
                if p.category:
                    parts.append(f"  カテゴリ: {p.category}")
                # Enhanced TikTok data
                if p.selling_points:
                    parts.append(f"  セールスポイント: {', '.join(p.selling_points)}")
                if p.achievements:
                    parts.append(f"  実績: {', '.join(p.achievements)}")
                if p.reviews_summary:
                    parts.append(f"  レビュー: {p.reviews_summary}")
                if p.sold_info:
                    parts.append(f"  販売実績: {p.sold_info}")
                if p.target_audience:
                    parts.append(f"  ターゲット層: {p.target_audience}")
                if p.variants:
                    parts.append(f"  バリエーション: {', '.join(p.variants)}")
                if p.talk_hooks:
                    parts.append(f"  トークフック: {', '.join(p.talk_hooks)}")
                product_lines.append("\n".join(parts))
            else:
                product_lines.append(f"商品名: {p}")
        products_text = "紹介する商品:\n" + "\n\n".join(product_lines)

    # Build enhanced product data from request notes (passed from frontend)
    enhanced_notes = ""
    if body.notes and "selling_points" in body.notes:
        enhanced_notes = body.notes

    # Calculate target character count (Japanese: ~250 chars/min)
    target_chars = body.duration_minutes * 250
    min_chars = int(target_chars * 0.85)
    max_chars = int(target_chars * 1.15)

    # Build mandatory facts section - these MUST appear in the script
    mandatory_facts = []
    for p in (body.products or []):
        if hasattr(p, 'achievements') and p.achievements:
            for a in p.achievements:
                mandatory_facts.append(f"- 実績: {a}")
        if hasattr(p, 'selling_points') and p.selling_points:
            for sp in p.selling_points:
                mandatory_facts.append(f"- セールスポイント: {sp}")
        if hasattr(p, 'sold_info') and p.sold_info:
            mandatory_facts.append(f"- 販売実績: {p.sold_info}")
        if hasattr(p, 'reviews_summary') and p.reviews_summary:
            mandatory_facts.append(f"- レビュー: {p.reviews_summary}")
        if hasattr(p, 'talk_hooks') and p.talk_hooks:
            for hook in p.talk_hooks:
                mandatory_facts.append(f"- トークフック例: {hook}")
        if hasattr(p, 'variants') and p.variants:
            mandatory_facts.append(f"- バリエーション: {', '.join(p.variants)}")

    mandatory_section = ""
    if mandatory_facts:
        facts_text = "\n".join(mandatory_facts)
        mandatory_section = f"""\n\n=== 必須情報（台本に自然に組み込むこと）===\n{facts_text}\n=== 上記の数字・実績は台本内で必ず言及すること ==="""

    style_text = f"スタイル: {body.style}" if body.style else ""
    notes_text = f"備考: {body.notes}" if body.notes else ""

    user_prompt = f"""以下の条件でライブ配信の台本を作成してください。

配信時間: 約{body.duration_minutes}分（{min_chars}〜{max_chars}文字）
{products_text}
{style_text}
{notes_text}
{mandatory_section}

台本の構成:
1. オープニング（挨拶・今日の配信テーマ）
2. 商品紹介（各商品の特徴・使い方・おすすめポイント）
   - 必ず商品名を正確に言う
   - 実績データ（ランキング、累計販売数）を具体的な数字で伝える
   - レビュー情報があれば「お客様からも〇〇という声が」と自然に入れる
   - バリエーションがあればそれぞれの特徴を紹介する
3. 視聴者とのインタラクション（コメント読み・質問対応のタイミング）
4. クロージング（まとめ・購入案内）

絶対ルール:
- 商品の実績（累計販売数、ランキング1位など）は必ず台本に入れる。「50万本突破」「No.1獲得」等の数字は省略しない
- セールスポイントの具体的な数字を使って説得力のある台本にする
- 商品名は正確に（漢字・カタカナを正しく）使う
- 指定された文字数（{min_chars}〜{max_chars}文字）を守る
- 同じフレーズを繰り返さない
- バリエーション（色・種類）がある場合は、それぞれの特徴を紹介する
- 台本はそのまま読み上げるテキストのみ出力する（【タグ】や**太字**等の記号は使わない）
- 台本以外の説明文やメモは出力しない

あなたの普段の話し方で、自然だけど説得力のある台本を作ってください。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        client = finetune_service._get_openai()

        # ── 2-stage generation: persona voice + product data integration ──
        has_rich_product_data = any(
            (hasattr(p, 'selling_points') and p.selling_points) or
            (hasattr(p, 'achievements') and p.achievements) or
            (hasattr(p, 'talk_hooks') and p.talk_hooks)
            for p in (body.products or [])
        )

        if has_rich_product_data:
            # Stage 1: Get persona voice/style from fine-tuned model (short sample)
            voice_prompt = f"""以下の商品を紹介するライブ配信のオープニングを50文字程度で書いてください。
商品名: {body.products[0].name if body.products else '商品'}
あなたの普段の話し方で、自然に。"""
            voice_response = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": voice_prompt},
                ],
                temperature=0.8,
                max_tokens=200,
            )
            voice_sample = voice_response.choices[0].message.content.strip()

            # Stage 2: Use GPT-4.1-mini to generate full script with product data
            from app.services.live_session_service import _call_gpt
            integration_prompt = f"""あなたはライブコマース台本ライターです。
以下の「配信者の口調サンプル」を参考に、同じ話し方・テンション・口癖で台本を書いてください。

【配信者の口調サンプル】
{voice_sample}

【配信者プロフィール】
{system_prompt}

{user_prompt}"""
            stage2_messages = [
                {"role": "user", "content": integration_prompt},
            ]
            stage2_result = await _call_gpt(
                stage2_messages,
                max_tokens=2000,
                temperature=0.7,
            )
            script = stage2_result.strip() if stage2_result else ""

            if not script or len(script) < 100:
                # Fallback: direct fine-tuned model
                response = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=4096,
                )
                script = response.choices[0].message.content
        else:
            # No rich data - use fine-tuned model directly
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.7,
                max_tokens=4096,
            )
            script = response.choices[0].message.content

        # Post-process: remove markdown/tags that shouldn't be in spoken script
        import re as _re
        script = _re.sub(r'\*\*', '', script)  # Remove **bold**
        script = _re.sub(r'\*', '', script)    # Remove *italic*
        script = _re.sub(r'\u3010[^\u3011]*\u3011', '', script)  # Remove 【tags】
        script = _re.sub(r'#{1,6}\s*', '', script)  # Remove # headings
        script = _re.sub(r'\n{3,}', '\n\n', script)  # Collapse excess newlines
        script = script.strip()

        char_count = len(script)
        estimated_duration_min = round(char_count / 250, 1)

        return {
            "script": script,
            "char_count": char_count,
            "estimated_duration_minutes": estimated_duration_min,
            "model": model_id if not has_rich_product_data else f"{model_id}+gpt-4.1-mini",
            "persona_name": persona.name,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
    except Exception as e:
        logger.exception(f"Script generation error for persona {persona_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Script generation error: {str(e)}")


# ── Debug: check audio_text data ──
@router.get("/{persona_id}/debug-audio-text")
async def debug_audio_text(
    persona_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Debug: check if audio_text exists in video_phases for tagged videos."""
    _check_admin(x_admin_key)

    tag_sql = text("""
        SELECT pvt.video_id FROM persona_video_tags pvt
        JOIN videos v ON pvt.video_id = v.id
        WHERE pvt.persona_id = :pid AND v.status = 'DONE'
    """)
    tag_result = await db.execute(tag_sql, {"pid": persona_id})
    video_ids = [r.video_id for r in tag_result.fetchall()]

    if not video_ids:
        return {"error": "no tagged videos"}

    # Check audio_text
    check_sql = text("""
        SELECT
            vp.video_id,
            vp.phase_index,
            LENGTH(vp.audio_text) as audio_text_len,
            LEFT(vp.audio_text, 100) as audio_text_preview,
            LENGTH(vp.phase_description) as desc_len
        FROM video_phases vp
        WHERE vp.video_id = ANY(:vids)
        ORDER BY vp.video_id, vp.phase_index
        LIMIT 20
    """)
    try:
        result = await db.execute(check_sql, {"vids": video_ids})
        rows = result.fetchall()
        data = []
        for r in rows:
            data.append({
                "video_id": str(r.video_id),
                "phase_index": r.phase_index,
                "audio_text_len": r.audio_text_len,
                "audio_text_preview": r.audio_text_preview,
                "desc_len": r.desc_len,
            })
        return {"video_count": len(video_ids), "phases": data}
    except Exception as e:
        return {"error": str(e)}


# ── Batch Transcription: populate audio_text for tagged videos ──

@router.post("/{persona_id}/batch-transcribe")
async def batch_transcribe(
    persona_id: str,
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="Re-transcribe even if audio_text exists"),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """
    Batch transcribe all tagged videos using Azure OpenAI Whisper API.
    Downloads each video from Azure Blob, extracts audio, transcribes,
    and saves to video_phases.audio_text.
    """
    _check_admin(x_admin_key)

    try:
        # Get tagged videos
        tag_sql = text("""
            SELECT pvt.video_id, v.original_filename, u.email as user_email
            FROM persona_video_tags pvt
            JOIN videos v ON CAST(pvt.video_id AS UUID) = v.id
            LEFT JOIN users u ON v.user_id = u.id
            WHERE pvt.persona_id = :pid AND v.status = 'DONE'
            ORDER BY v.created_at ASC
        """)
        tag_result = await db.execute(tag_sql, {"pid": persona_id})
        tagged_videos = tag_result.fetchall()

        if not tagged_videos:
            raise HTTPException(status_code=400, detail="No tagged DONE videos found")

        # Check which videos need transcription
        videos_to_process = []
        for v in tagged_videos:
            if not force:
                # Check if any phases already have audio_text
                check_sql = text("""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN audio_text IS NOT NULL AND LENGTH(audio_text) > 10 THEN 1 ELSE 0 END) as has_audio
                    FROM video_phases
                    WHERE video_id = :vid
                """)
                check_result = await db.execute(check_sql, {"vid": v.video_id})
                check_row = check_result.fetchone()
                if check_row and check_row.has_audio and check_row.has_audio > 0:
                    logger.info(f"[batch-transcribe] Skipping {v.video_id} - already has {check_row.has_audio} audio_text phases")
                    continue
            videos_to_process.append({
                "video_id": str(v.video_id),
                "filename": v.original_filename,
                "user_email": v.user_email,
            })

        if not videos_to_process:
            return {
                "status": "skipped",
                "message": "All videos already have audio_text. Use force=true to re-transcribe.",
                "total_tagged": len(tagged_videos),
            }

        # Start background task
        background_tasks.add_task(
            _run_batch_transcribe,
            persona_id,
            videos_to_process,
        )

        return {
            "status": "started",
            "videos_to_process": len(videos_to_process),
            "total_tagged": len(tagged_videos),
            "videos": [v["video_id"] for v in videos_to_process],
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.exception(f"[batch-transcribe] Error: {e}")
        return {"error": str(e), "traceback": traceback.format_exc()[-500:]}


# ── Single Video Transcription (synchronous, reliable) ──

@router.post("/{persona_id}/transcribe-video/{video_id}")
async def transcribe_single_video(
    persona_id: str,
    video_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """
    Transcribe a single video synchronously using Azure OpenAI Whisper API.
    More reliable than batch-transcribe as it processes one video at a time.
    """
    import asyncio
    import tempfile
    import os as _os
    import openai
    import httpx

    _check_admin(x_admin_key)

    try:
        # Get video info
        video_sql = text("""
            SELECT v.id, v.original_filename, u.email as user_email
            FROM videos v
            LEFT JOIN users u ON v.user_id = u.id
            WHERE v.id = CAST(:vid AS UUID) AND v.status = 'DONE'
        """)
        video_result = await db.execute(video_sql, {"vid": video_id})
        video = video_result.fetchone()
        if not video:
            return {"error": f"Video {video_id} not found or not DONE"}

        user_email = video.user_email
        filename = video.original_filename

        # Generate download URL
        from app.services.storage_service import generate_download_sas
        download_url, _ = await generate_download_sas(
            email=user_email,
            video_id=video_id,
            filename=filename,
            expires_in_minutes=60,
        )

        tmp_dir = tempfile.mkdtemp(prefix=f"transcribe_{video_id[:8]}_")

        # Download video
        video_path = _os.path.join(tmp_dir, "video.mp4")
        logger.info(f"[transcribe-video] Downloading {video_id}...")
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30, read=600, write=30, pool=600)) as client:
            async with client.stream("GET", download_url) as resp:
                if resp.status_code != 200:
                    return {"error": f"Download failed: HTTP {resp.status_code}"}
                with open(video_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)

        file_size = _os.path.getsize(video_path)
        logger.info(f"[transcribe-video] Downloaded: {file_size / 1024 / 1024:.1f} MB")

        # Extract audio
        audio_path = _os.path.join(tmp_dir, "audio.mp3")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "libmp3lame",
            "-ar", "16000", "-ac", "1", "-b:a", "64k",
            audio_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not _os.path.exists(audio_path):
            return {"error": f"ffmpeg failed: {stderr.decode()[:300]}"}

        audio_size = _os.path.getsize(audio_path)
        logger.info(f"[transcribe-video] Audio: {audio_size / 1024 / 1024:.1f} MB")

        # Delete video to free disk space
        _os.unlink(video_path)

        # Setup Whisper client
        azure_endpoint = _os.getenv("AZURE_OPENAI_ENDPOINT", "https://aoai-kyogoku-service.openai.azure.com/")
        azure_key = _os.getenv("AZURE_OPENAI_KEY", "")
        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(azure_endpoint)
        clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

        openai_client = openai.AsyncAzureOpenAI(
            api_key=azure_key,
            api_version="2024-06-01",
            azure_endpoint=clean_endpoint,
        )

        # Split audio if > 25MB
        max_whisper_size = 24 * 1024 * 1024
        audio_files = []

        if audio_size > max_whisper_size:
            probe_proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            probe_out, _ = await probe_proc.communicate()
            total_duration = float(probe_out.decode().strip())

            chunk_duration = int(total_duration * (20 * 1024 * 1024) / audio_size)
            chunk_duration = max(60, min(chunk_duration, 1800))

            chunk_idx = 0
            offset = 0.0
            while offset < total_duration:
                chunk_path = _os.path.join(tmp_dir, f"chunk_{chunk_idx:03d}.mp3")
                split_proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", audio_path,
                    "-ss", str(offset), "-t", str(chunk_duration),
                    "-acodec", "copy", chunk_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await split_proc.communicate()
                if _os.path.exists(chunk_path) and _os.path.getsize(chunk_path) > 100:
                    audio_files.append((chunk_path, offset))
                offset += chunk_duration
                chunk_idx += 1
            logger.info(f"[transcribe-video] Split into {len(audio_files)} chunks")
        else:
            audio_files = [(audio_path, 0.0)]

        # Transcribe
        all_segments = []
        for audio_file, time_offset in audio_files:
            try:
                with open(audio_file, "rb") as f:
                    response = await openai_client.audio.transcriptions.create(
                        model="whisper",
                        file=f,
                        response_format="verbose_json",
                        language="ja",
                        timestamp_granularities=["segment"],
                    )
                if hasattr(response, "segments") and response.segments:
                    for seg in response.segments:
                        s = float(getattr(seg, "start", 0) if hasattr(seg, "start") else seg.get("start", 0))
                        e = float(getattr(seg, "end", 0) if hasattr(seg, "end") else seg.get("end", 0))
                        t = (getattr(seg, "text", "") if hasattr(seg, "text") else seg.get("text", "")).strip()
                        if t:
                            all_segments.append({"start": s + time_offset, "end": e + time_offset, "text": t})
                elif hasattr(response, "text") and response.text:
                    all_segments.append({"start": time_offset, "end": time_offset + 60.0, "text": response.text.strip()})
            except Exception as whisper_err:
                logger.error(f"[transcribe-video] Whisper error: {whisper_err}")
                continue

        logger.info(f"[transcribe-video] Got {len(all_segments)} segments")

        if not all_segments:
            # Cleanup
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return {"error": "No segments from Whisper", "video_id": video_id}

        # Map segments to phases
        phases_sql = text("""
            SELECT id, phase_index, time_start, time_end
            FROM video_phases
            WHERE video_id = :vid
            ORDER BY phase_index ASC
        """)
        phases_result = await db.execute(phases_sql, {"vid": video_id})
        phases = phases_result.fetchall()

        phases_updated = 0
        for phase in phases:
            p_start = float(phase.time_start or 0)
            p_end = float(phase.time_end or 0)
            if p_end <= p_start:
                continue

            phase_texts = []
            for seg in all_segments:
                if seg["end"] > p_start and seg["start"] < p_end:
                    phase_texts.append(seg["text"])

            audio_text = " ".join(phase_texts).strip()
            if not audio_text or len(audio_text) < 5:
                continue

            update_sql = text("""
                UPDATE video_phases
                SET audio_text = :audio_text, updated_at = now()
                WHERE video_id = :vid AND phase_index = :pidx
            """)
            await db.execute(update_sql, {
                "audio_text": audio_text,
                "vid": video_id,
                "pidx": phase.phase_index,
            })
            phases_updated += 1

        await db.commit()

        # Cleanup
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

        return {
            "status": "ok",
            "video_id": video_id,
            "segments": len(all_segments),
            "phases_total": len(phases),
            "phases_updated": phases_updated,
            "sample_text": all_segments[0]["text"][:100] if all_segments else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.exception(f"[transcribe-video] Error: {e}")
        return {"error": str(e), "traceback": traceback.format_exc()[-500:]}


async def _run_batch_transcribe(persona_id: str, videos: list):
    """Background task: transcribe videos and save audio_text to DB."""
    import asyncio
    import tempfile
    import os
    import openai
    import httpx

    from app.core.db import AsyncSessionLocal
    from app.services.storage_service import generate_download_sas

    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "https://aoai-kyogoku-service.openai.azure.com/")
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    openai_client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        api_version="2024-06-01",
        azure_endpoint=clean_endpoint,
    )

    total_phases_updated = 0
    total_videos_done = 0

    for video_info in videos:
        video_id = video_info["video_id"]
        filename = video_info["filename"]
        user_email = video_info["user_email"]

        logger.info(f"[batch-transcribe] Processing video {video_id}: {filename}")

        tmp_dir = tempfile.mkdtemp(prefix=f"transcribe_{video_id[:8]}_")
        try:
            # 1. Generate download URL
            download_url, _ = await generate_download_sas(
                email=user_email,
                video_id=video_id,
                filename=filename,
                expires_in_minutes=60,
            )

            # 2. Download video
            video_path = os.path.join(tmp_dir, "video.mp4")
            logger.info(f"[batch-transcribe] Downloading video {video_id}...")
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30, read=300, write=30, pool=300)) as client:
                async with client.stream("GET", download_url) as resp:
                    if resp.status_code != 200:
                        logger.error(f"[batch-transcribe] Download failed for {video_id}: HTTP {resp.status_code}")
                        continue
                    with open(video_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)

            file_size = os.path.getsize(video_path)
            logger.info(f"[batch-transcribe] Downloaded {video_id}: {file_size / 1024 / 1024:.1f} MB")

            # 3. Extract audio as mp3 (smaller for Whisper API)
            audio_path = os.path.join(tmp_dir, "audio.mp3")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", video_path,
                "-vn", "-acodec", "libmp3lame",
                "-ar", "16000", "-ac", "1", "-b:a", "64k",
                audio_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not os.path.exists(audio_path):
                logger.error(f"[batch-transcribe] ffmpeg failed for {video_id}: {stderr.decode()[:300]}")
                continue

            audio_size = os.path.getsize(audio_path)
            logger.info(f"[batch-transcribe] Extracted audio: {audio_size / 1024 / 1024:.1f} MB")

            # 4. Split audio into chunks if > 25MB (Whisper API limit)
            max_whisper_size = 24 * 1024 * 1024  # 24MB to be safe
            audio_files = []

            if audio_size > max_whisper_size:
                # Split into ~20MB chunks by duration
                # Get audio duration first
                probe_proc = await asyncio.create_subprocess_exec(
                    "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                probe_out, _ = await probe_proc.communicate()
                total_duration = float(probe_out.decode().strip())

                # Calculate chunk duration to keep each chunk under 20MB
                chunk_duration = int(total_duration * (20 * 1024 * 1024) / audio_size)
                chunk_duration = max(60, min(chunk_duration, 1800))  # 1-30 min chunks

                chunk_idx = 0
                offset = 0
                while offset < total_duration:
                    chunk_path = os.path.join(tmp_dir, f"chunk_{chunk_idx:03d}.mp3")
                    split_proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y", "-i", audio_path,
                        "-ss", str(offset), "-t", str(chunk_duration),
                        "-acodec", "copy", chunk_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await split_proc.communicate()
                    if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 100:
                        audio_files.append((chunk_path, offset))
                    offset += chunk_duration
                    chunk_idx += 1
                logger.info(f"[batch-transcribe] Split into {len(audio_files)} chunks")
            else:
                audio_files = [(audio_path, 0.0)]

            # 5. Transcribe each chunk with Whisper
            all_segments = []
            for audio_file, time_offset in audio_files:
                try:
                    with open(audio_file, "rb") as f:
                        response = await openai_client.audio.transcriptions.create(
                            model="whisper",
                            file=f,
                            response_format="verbose_json",
                            language="ja",
                            timestamp_granularities=["segment"],
                        )

                    if hasattr(response, "segments") and response.segments:
                        for seg in response.segments:
                            s = float(getattr(seg, "start", 0) if hasattr(seg, "start") else seg.get("start", 0))
                            e = float(getattr(seg, "end", 0) if hasattr(seg, "end") else seg.get("end", 0))
                            t = (getattr(seg, "text", "") if hasattr(seg, "text") else seg.get("text", "")).strip()
                            if t:
                                all_segments.append({
                                    "start": s + time_offset,
                                    "end": e + time_offset,
                                    "text": t,
                                })
                    elif hasattr(response, "text") and response.text:
                        # Single segment fallback
                        all_segments.append({
                            "start": time_offset,
                            "end": time_offset + 60.0,  # approximate
                            "text": response.text.strip(),
                        })
                except Exception as whisper_err:
                    logger.error(f"[batch-transcribe] Whisper failed for {audio_file}: {whisper_err}")
                    continue

            logger.info(f"[batch-transcribe] Got {len(all_segments)} segments for {video_id}")

            if not all_segments:
                logger.warning(f"[batch-transcribe] No segments for {video_id}, skipping DB update")
                continue

            # 6. Map segments to phases and update DB
            async with AsyncSessionLocal() as db_session:
                # Get phases for this video
                phases_sql = text("""
                    SELECT id, phase_index, time_start, time_end
                    FROM video_phases
                    WHERE video_id = :vid
                    ORDER BY phase_index ASC
                """)
                phases_result = await db_session.execute(phases_sql, {"vid": video_id})
                phases = phases_result.fetchall()

                phases_updated = 0
                for phase in phases:
                    p_start = float(phase.time_start or 0)
                    p_end = float(phase.time_end or 0)
                    if p_end <= p_start:
                        continue

                    # Collect speech segments overlapping with this phase
                    phase_texts = []
                    for seg in all_segments:
                        # Segment overlaps with phase if seg.end > p_start and seg.start < p_end
                        if seg["end"] > p_start and seg["start"] < p_end:
                            phase_texts.append(seg["text"])

                    audio_text = " ".join(phase_texts).strip()
                    if not audio_text or len(audio_text) < 5:
                        continue

                    # Update DB
                    update_sql = text("""
                        UPDATE video_phases
                        SET audio_text = :audio_text, updated_at = now()
                        WHERE video_id = :vid AND phase_index = :pidx
                    """)
                    await db_session.execute(update_sql, {
                        "audio_text": audio_text,
                        "vid": video_id,
                        "pidx": phase.phase_index,
                    })
                    phases_updated += 1

                await db_session.commit()
                total_phases_updated += phases_updated
                total_videos_done += 1
                logger.info(f"[batch-transcribe] Updated {phases_updated}/{len(phases)} phases for {video_id}")

        except Exception as e:
            logger.exception(f"[batch-transcribe] Error processing {video_id}: {e}")
        finally:
            # Cleanup temp files
            import shutil
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    logger.info(
        f"[batch-transcribe] DONE: {total_videos_done}/{len(videos)} videos, "
        f"{total_phases_updated} phases updated"
    )


# ── Batch Transcription Status ──

@router.get("/{persona_id}/transcription-status")
async def transcription_status(
    persona_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Check how many phases have audio_text for tagged videos."""
    _check_admin(x_admin_key)

    sql = text("""
        SELECT
            COUNT(*) as total_phases,
            SUM(CASE WHEN vp.audio_text IS NOT NULL AND LENGTH(vp.audio_text) > 10 THEN 1 ELSE 0 END) as with_audio,
            SUM(CASE WHEN vp.audio_text IS NULL OR LENGTH(vp.audio_text) <= 10 THEN 1 ELSE 0 END) as without_audio,
            COUNT(DISTINCT vp.video_id) as total_videos,
            COUNT(DISTINCT CASE WHEN vp.audio_text IS NOT NULL AND LENGTH(vp.audio_text) > 10 THEN vp.video_id END) as videos_with_audio
        FROM video_phases vp
        JOIN persona_video_tags pvt ON pvt.video_id = vp.video_id
        JOIN videos v ON v.id = vp.video_id
        WHERE pvt.persona_id = :pid AND v.status = 'DONE'
    """)
    result = await db.execute(sql, {"pid": persona_id})
    row = result.fetchone()

    return {
        "total_phases": row.total_phases if row else 0,
        "phases_with_audio": row.with_audio if row else 0,
        "phases_without_audio": row.without_audio if row else 0,
        "total_videos": row.total_videos if row else 0,
        "videos_with_audio": row.videos_with_audio if row else 0,
        "completion_pct": round(
            (row.with_audio / row.total_phases * 100) if row and row.total_phases > 0 else 0, 1
        ),
    }
