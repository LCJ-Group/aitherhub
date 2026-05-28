"""
User Profile / Onboarding Counseling API
Stores user type (liver/brand/both), main challenge, and TikTok account.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy import text as _text

from app.core.database import get_db
from app.api.v1.endpoints.auth import get_current_user

router = APIRouter(prefix="/profile", tags=["User Profile"])


class ProfileCreate(BaseModel):
    user_type: str = Field(..., description="liver | brand | both")
    main_challenge: Optional[str] = Field(None, description="Main challenge/goal")
    tiktok_account: Optional[str] = Field(None, description="TikTok account name")


class ProfileResponse(BaseModel):
    user_id: int
    user_type: str
    main_challenge: Optional[str] = None
    tiktok_account: Optional[str] = None
    onboarding_completed: bool = False


@router.post("/onboarding", response_model=ProfileResponse)
async def save_onboarding(
    data: ProfileCreate,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Save or update user onboarding profile."""
    user_id = current_user["id"]

    async with db.begin():
        # Upsert
        result = await db.execute(
            _text("""
                INSERT INTO user_profiles (user_id, user_type, main_challenge, tiktok_account, onboarding_completed)
                VALUES (:user_id, :user_type, :main_challenge, :tiktok_account, true)
                ON CONFLICT (user_id) DO UPDATE SET
                    user_type = EXCLUDED.user_type,
                    main_challenge = EXCLUDED.main_challenge,
                    tiktok_account = EXCLUDED.tiktok_account,
                    onboarding_completed = true,
                    updated_at = NOW()
                RETURNING user_id, user_type, main_challenge, tiktok_account, onboarding_completed
            """),
            {
                "user_id": user_id,
                "user_type": data.user_type,
                "main_challenge": data.main_challenge,
                "tiktok_account": data.tiktok_account,
            },
        )
        row = result.fetchone()

    return ProfileResponse(
        user_id=row.user_id,
        user_type=row.user_type,
        main_challenge=row.main_challenge,
        tiktok_account=row.tiktok_account,
        onboarding_completed=row.onboarding_completed,
    )


@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get current user's profile."""
    user_id = current_user["id"]

    result = await db.execute(
        _text("SELECT user_id, user_type, main_challenge, tiktok_account, onboarding_completed FROM user_profiles WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    row = result.fetchone()

    if not row:
        # Return empty profile (onboarding not completed)
        return ProfileResponse(
            user_id=user_id,
            user_type="unknown",
            main_challenge=None,
            tiktok_account=None,
            onboarding_completed=False,
        )

    return ProfileResponse(
        user_id=row.user_id,
        user_type=row.user_type,
        main_challenge=row.main_challenge,
        tiktok_account=row.tiktok_account,
        onboarding_completed=row.onboarding_completed,
    )
