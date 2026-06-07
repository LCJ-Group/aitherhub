"""
Liver Guide API — allows livers to upload generic (product-free) video material
for use in the AI Video Generator's "one-click mass production" feature.

Flow:
  1. Liver enters their TikTok username on the guide page
  2. Frontend requests a SAS upload URL via POST /liver-guide/upload-sas
  3. Frontend uploads the video directly to Azure Blob via PUT
  4. Frontend registers the uploaded material via POST /liver-guide/register
  5. Material is stored in the DB and available for AI Video Generator

No authentication required — the TikTok username serves as the identifier.
"""

import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.services.storage_service import generate_upload_sas, generate_read_sas_from_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/liver-guide", tags=["Liver Guide"])


# ─── Request/Response Models ───

class UploadSASRequest(BaseModel):
    tiktok_username: str
    filename: str
    material_type: str = "generic"  # generic | greeting | product_intro


class UploadSASResponse(BaseModel):
    upload_url: str
    blob_url: str
    material_id: str
    expires_at: str


class RegisterMaterialRequest(BaseModel):
    tiktok_username: str
    blob_url: str
    material_id: str
    material_type: str = "generic"
    duration_seconds: Optional[float] = None
    notes: Optional[str] = None


class RegisterMaterialResponse(BaseModel):
    success: bool
    material_id: str
    message: str


class LiverMaterialInfo(BaseModel):
    material_id: str
    tiktok_username: str
    blob_url: str
    material_type: str
    status: str
    created_at: str
    thumbnail_url: Optional[str] = None


# ─── Endpoints ───

@router.post("/upload-sas", response_model=UploadSASResponse)
async def get_upload_sas(payload: UploadSASRequest):
    """
    Generate a write-only SAS URL for direct video upload to Azure Blob.
    No authentication required — uses tiktok_username as folder identifier.
    """
    username = payload.tiktok_username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="tiktok_username is required")
    
    filename = payload.filename or "material.mp4"
    material_id = str(uuid.uuid4())
    
    # Use folder structure: liver_material/{username}/{material_id}/filename
    email = f"liver_material/{username}"
    
    try:
        _vid, upload_url, blob_url, expiry = await generate_upload_sas(
            email=email,
            video_id=material_id,
            filename=filename,
        )
        logger.info(f"[LiverGuide] SAS generated for {username}: {material_id}")
        return UploadSASResponse(
            upload_url=upload_url,
            blob_url=blob_url,
            material_id=material_id,
            expires_at=expiry.isoformat(),
        )
    except Exception as e:
        logger.error(f"[LiverGuide] SAS generation failed for {username}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")


@router.post("/register", response_model=RegisterMaterialResponse)
async def register_material(payload: RegisterMaterialRequest):
    """
    Register an uploaded video material in the database.
    Creates a record in liver_materials table (auto-created if not exists).
    """
    username = payload.tiktok_username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="tiktok_username is required")
    if not payload.blob_url:
        raise HTTPException(status_code=400, detail="blob_url is required")
    
    try:
        async with AsyncSessionLocal() as db:
            # Ensure table exists (idempotent)
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS liver_materials (
                    id VARCHAR(36) PRIMARY KEY,
                    tiktok_username VARCHAR(255) NOT NULL,
                    blob_url TEXT NOT NULL,
                    material_type VARCHAR(50) DEFAULT 'generic',
                    status VARCHAR(50) DEFAULT 'uploaded',
                    duration_seconds FLOAT,
                    notes TEXT,
                    thumbnail_url TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            
            # Insert material record
            await db.execute(text("""
                INSERT INTO liver_materials (id, tiktok_username, blob_url, material_type, status, duration_seconds, notes)
                VALUES (:id, :username, :blob_url, :material_type, 'uploaded', :duration, :notes)
            """), {
                "id": payload.material_id,
                "username": username,
                "blob_url": payload.blob_url,
                "material_type": payload.material_type,
                "duration": payload.duration_seconds,
                "notes": payload.notes,
            })
            await db.commit()
        
        logger.info(f"[LiverGuide] Material registered: {payload.material_id} by {username}")
        return RegisterMaterialResponse(
            success=True,
            material_id=payload.material_id,
            message="素材が正常に登録されました。AIビデオ生成で使用可能になります。",
        )
    except Exception as e:
        logger.error(f"[LiverGuide] Registration failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")


@router.get("/materials/{tiktok_username}")
async def list_materials(tiktok_username: str):
    """List all uploaded materials for a specific liver."""
    username = tiktok_username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="tiktok_username is required")
    
    try:
        async with AsyncSessionLocal() as db:
            # Check if table exists first
            check = await db.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'liver_materials'
                )
            """))
            if not check.scalar():
                return {"materials": [], "total": 0}
            
            result = await db.execute(text("""
                SELECT id, tiktok_username, blob_url, material_type, status, 
                       duration_seconds, created_at, thumbnail_url
                FROM liver_materials
                WHERE tiktok_username = :username
                ORDER BY created_at DESC
            """), {"username": username})
            rows = result.fetchall()
        
        materials = []
        for row in rows:
            thumb_url = None
            if row.thumbnail_url:
                thumb_url = generate_read_sas_from_url(row.thumbnail_url, expires_hours=2)
            elif row.blob_url:
                # Try to generate thumbnail URL from blob URL
                thumb_blob = row.blob_url.rsplit('.', 1)[0] + '_thumb.jpg' if '.' in row.blob_url else None
                if thumb_blob:
                    try:
                        thumb_url = generate_read_sas_from_url(thumb_blob, expires_hours=2)
                    except Exception:
                        pass
            
            preview_url = generate_read_sas_from_url(row.blob_url, expires_hours=2) if row.blob_url else None
            
            materials.append({
                "material_id": row.id,
                "tiktok_username": row.tiktok_username,
                "blob_url": preview_url,
                "material_type": row.material_type,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "thumbnail_url": thumb_url,
            })
        
        return {"materials": materials, "total": len(materials)}
    except Exception as e:
        logger.error(f"[LiverGuide] List materials failed for {username}: {e}")
        return {"materials": [], "total": 0}
