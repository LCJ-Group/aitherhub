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


class PersonaUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    user_email: Optional[str] = None
    voice_id: Optional[str] = None
    voice_name: Optional[str] = None
    style_prompt: Optional[str] = None
    finetune_model_id: Optional[str] = None


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
            (SELECT COUNT(*) FROM persona_video_tags pvt WHERE pvt.persona_id = p.id) AS tagged_video_count
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
        return {"status": persona.finetune_status if persona else "none", "message": "No active training job"}

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

    return {
        "status": status["status"],
        "model_id": status.get("model_id"),
        "trained_tokens": status.get("trained_tokens"),
        "error": status.get("error"),
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
