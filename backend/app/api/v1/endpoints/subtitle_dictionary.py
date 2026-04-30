"""
Subtitle Dictionary API — per-account custom dictionary for subtitle correction.

Features:
1. Replace misrecognized words (e.g., "京獄" → "KYOGOKU")
2. Mark words as no-break (never split across subtitle lines)
3. Feed dictionary words into Whisper initial_prompt for better recognition
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.db import engine as async_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/subtitle-dictionary", tags=["Subtitle Dictionary"])


# ─── Schemas ───────────────────────────────────────────────────────────────────

class DictionaryEntryCreate(BaseModel):
    from_text: str = Field(..., description="Text to find/replace (misrecognized text or no-break word)")
    to_text: str = Field("", description="Replacement text (empty = no replacement, just no-break)")
    no_break: bool = Field(True, description="If True, this word will never be split across subtitle lines")
    category: str = Field("brand", description="Category: brand, product, person, other")
    notes: Optional[str] = Field(None, description="Optional notes about this entry")


class DictionaryEntryUpdate(BaseModel):
    from_text: Optional[str] = None
    to_text: Optional[str] = None
    no_break: Optional[bool] = None
    category: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class DictionaryEntryResponse(BaseModel):
    id: int
    user_id: str
    from_text: str
    to_text: str
    no_break: bool
    is_active: bool
    category: str
    notes: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


class BulkImportItem(BaseModel):
    from_text: str
    to_text: str = ""
    no_break: bool = True
    category: str = "brand"


# ─── Helper ────────────────────────────────────────────────────────────────────

def _get_user_id(x_admin_key: Optional[str] = None) -> str:
    """Extract user_id from admin key or default."""
    # For now, use 'default' as user_id (single-tenant)
    # In future, extract from JWT or admin key
    return "default"


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[DictionaryEntryResponse])
async def list_dictionary_entries(
    category: Optional[str] = None,
    active_only: bool = True,
    x_admin_key: Optional[str] = Header(None),
):
    """List all dictionary entries for the current user."""
    user_id = _get_user_id(x_admin_key)
    
    conditions = ["user_id = :user_id"]
    params = {"user_id": user_id}
    
    if active_only:
        conditions.append("is_active = TRUE")
    if category:
        conditions.append("category = :category")
        params["category"] = category
    
    where_clause = " AND ".join(conditions)
    
    async with async_engine.connect() as conn:
        result = await conn.execute(
            text(f"SELECT * FROM subtitle_dictionary WHERE {where_clause} ORDER BY category, from_text"),
            params
        )
        rows = result.fetchall()
    
    return [
        DictionaryEntryResponse(
            id=row.id,
            user_id=row.user_id,
            from_text=row.from_text,
            to_text=row.to_text,
            no_break=row.no_break,
            is_active=row.is_active,
            category=row.category,
            notes=row.notes,
            created_at=str(row.created_at) if row.created_at else None,
            updated_at=str(row.updated_at) if row.updated_at else None,
        )
        for row in rows
    ]


@router.post("", response_model=DictionaryEntryResponse)
async def create_dictionary_entry(
    entry: DictionaryEntryCreate,
    x_admin_key: Optional[str] = Header(None),
):
    """Create a new dictionary entry."""
    user_id = _get_user_id(x_admin_key)
    
    if not entry.from_text.strip():
        raise HTTPException(status_code=400, detail="from_text cannot be empty")
    
    async with async_engine.begin() as conn:
        # Check for duplicate
        existing = await conn.execute(
            text("SELECT id FROM subtitle_dictionary WHERE user_id = :user_id AND from_text = :from_text AND is_active = TRUE"),
            {"user_id": user_id, "from_text": entry.from_text.strip()}
        )
        if existing.fetchone():
            raise HTTPException(status_code=409, detail=f"Entry for '{entry.from_text}' already exists")
        
        result = await conn.execute(
            text("""
                INSERT INTO subtitle_dictionary (user_id, from_text, to_text, no_break, category, notes)
                VALUES (:user_id, :from_text, :to_text, :no_break, :category, :notes)
                RETURNING id, user_id, from_text, to_text, no_break, is_active, category, notes, created_at, updated_at
            """),
            {
                "user_id": user_id,
                "from_text": entry.from_text.strip(),
                "to_text": entry.to_text.strip(),
                "no_break": entry.no_break,
                "category": entry.category,
                "notes": entry.notes,
            }
        )
        row = result.fetchone()
    
    logger.info(f"[SubtitleDict] Created entry: '{entry.from_text}' → '{entry.to_text}' (user={user_id})")
    
    return DictionaryEntryResponse(
        id=row.id,
        user_id=row.user_id,
        from_text=row.from_text,
        to_text=row.to_text,
        no_break=row.no_break,
        is_active=row.is_active,
        category=row.category,
        notes=row.notes,
        created_at=str(row.created_at) if row.created_at else None,
        updated_at=str(row.updated_at) if row.updated_at else None,
    )


@router.put("/{entry_id}", response_model=DictionaryEntryResponse)
async def update_dictionary_entry(
    entry_id: int,
    update: DictionaryEntryUpdate,
    x_admin_key: Optional[str] = Header(None),
):
    """Update an existing dictionary entry."""
    user_id = _get_user_id(x_admin_key)
    
    # Build dynamic update
    updates = []
    params = {"entry_id": entry_id, "user_id": user_id}
    
    if update.from_text is not None:
        updates.append("from_text = :from_text")
        params["from_text"] = update.from_text.strip()
    if update.to_text is not None:
        updates.append("to_text = :to_text")
        params["to_text"] = update.to_text.strip()
    if update.no_break is not None:
        updates.append("no_break = :no_break")
        params["no_break"] = update.no_break
    if update.category is not None:
        updates.append("category = :category")
        params["category"] = update.category
    if update.notes is not None:
        updates.append("notes = :notes")
        params["notes"] = update.notes
    if update.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = update.is_active
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    updates.append("updated_at = NOW()")
    set_clause = ", ".join(updates)
    
    async with async_engine.begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE subtitle_dictionary SET {set_clause}
                WHERE id = :entry_id AND user_id = :user_id
                RETURNING id, user_id, from_text, to_text, no_break, is_active, category, notes, created_at, updated_at
            """),
            params
        )
        row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    
    logger.info(f"[SubtitleDict] Updated entry {entry_id} (user={user_id})")
    
    return DictionaryEntryResponse(
        id=row.id,
        user_id=row.user_id,
        from_text=row.from_text,
        to_text=row.to_text,
        no_break=row.no_break,
        is_active=row.is_active,
        category=row.category,
        notes=row.notes,
        created_at=str(row.created_at) if row.created_at else None,
        updated_at=str(row.updated_at) if row.updated_at else None,
    )


@router.delete("/{entry_id}")
async def delete_dictionary_entry(
    entry_id: int,
    x_admin_key: Optional[str] = Header(None),
):
    """Delete a dictionary entry (soft delete by setting is_active=FALSE)."""
    user_id = _get_user_id(x_admin_key)
    
    async with async_engine.begin() as conn:
        result = await conn.execute(
            text("UPDATE subtitle_dictionary SET is_active = FALSE, updated_at = NOW() WHERE id = :entry_id AND user_id = :user_id RETURNING id"),
            {"entry_id": entry_id, "user_id": user_id}
        )
        row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    
    logger.info(f"[SubtitleDict] Deleted entry {entry_id} (user={user_id})")
    return {"status": "deleted", "id": entry_id}


@router.post("/bulk-import")
async def bulk_import_dictionary(
    entries: List[BulkImportItem],
    x_admin_key: Optional[str] = Header(None),
):
    """Bulk import dictionary entries (skips duplicates)."""
    user_id = _get_user_id(x_admin_key)
    imported = 0
    skipped = 0
    
    async with async_engine.begin() as conn:
        for entry in entries:
            if not entry.from_text.strip():
                skipped += 1
                continue
            # Check duplicate
            existing = await conn.execute(
                text("SELECT id FROM subtitle_dictionary WHERE user_id = :user_id AND from_text = :from_text AND is_active = TRUE"),
                {"user_id": user_id, "from_text": entry.from_text.strip()}
            )
            if existing.fetchone():
                skipped += 1
                continue
            
            await conn.execute(
                text("""
                    INSERT INTO subtitle_dictionary (user_id, from_text, to_text, no_break, category)
                    VALUES (:user_id, :from_text, :to_text, :no_break, :category)
                """),
                {
                    "user_id": user_id,
                    "from_text": entry.from_text.strip(),
                    "to_text": entry.to_text.strip(),
                    "no_break": entry.no_break,
                    "category": entry.category,
                }
            )
            imported += 1
    
    logger.info(f"[SubtitleDict] Bulk import: {imported} imported, {skipped} skipped (user={user_id})")
    return {"imported": imported, "skipped": skipped, "total": len(entries)}


@router.get("/for-whisper")
async def get_dictionary_for_whisper(
    language: Optional[str] = None,
    x_admin_key: Optional[str] = Header(None),
):
    """Get dictionary entries formatted for Whisper initial_prompt.
    Returns a comma-separated string of correct terms to hint Whisper."""
    user_id = _get_user_id(x_admin_key)
    
    async with async_engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT from_text, to_text FROM subtitle_dictionary
                WHERE user_id = :user_id AND is_active = TRUE
                ORDER BY category, from_text
            """),
            {"user_id": user_id}
        )
        rows = result.fetchall()
    
    # Build prompt: use to_text (correct form) if available, otherwise from_text
    terms = []
    for row in rows:
        correct_term = row.to_text.strip() if row.to_text.strip() else row.from_text.strip()
        if correct_term and correct_term not in terms:
            terms.append(correct_term)
    
    prompt_text = "、".join(terms)
    return {"prompt": prompt_text, "terms": terms, "count": len(terms)}


@router.get("/replacements")
async def get_replacement_map(
    x_admin_key: Optional[str] = Header(None),
):
    """Get all active replacement rules as a map for post-processing.
    Returns entries where from_text != to_text (actual replacements)."""
    user_id = _get_user_id(x_admin_key)
    
    async with async_engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT from_text, to_text, no_break FROM subtitle_dictionary
                WHERE user_id = :user_id AND is_active = TRUE AND to_text != '' AND to_text != from_text
                ORDER BY LENGTH(from_text) DESC
            """),
            {"user_id": user_id}
        )
        rows = result.fetchall()
    
    replacements = [
        {"from": row.from_text, "to": row.to_text, "no_break": row.no_break}
        for row in rows
    ]
    return {"replacements": replacements, "count": len(replacements)}


@router.get("/no-break-words")
async def get_no_break_words(
    x_admin_key: Optional[str] = Header(None),
):
    """Get all active no-break words for subtitle line splitting."""
    user_id = _get_user_id(x_admin_key)
    
    async with async_engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT from_text, to_text FROM subtitle_dictionary
                WHERE user_id = :user_id AND is_active = TRUE AND no_break = TRUE
                ORDER BY LENGTH(COALESCE(NULLIF(to_text, ''), from_text)) DESC
            """),
            {"user_id": user_id}
        )
        rows = result.fetchall()
    
    # Return the "final form" of each word (to_text if available, else from_text)
    words = []
    for row in rows:
        word = row.to_text.strip() if row.to_text.strip() else row.from_text.strip()
        if word and word not in words:
            words.append(word)
    
    return {"words": words, "count": len(words)}
