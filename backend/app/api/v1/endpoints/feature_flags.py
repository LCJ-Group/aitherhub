"""
Feature Flags API – expose current flag state to the frontend.
"""

from fastapi import APIRouter

from app.core.feature_flags import flags

router = APIRouter(
    prefix="/feature-flags",
    tags=["feature-flags"],
)


@router.get("")
async def get_feature_flags():
    """Return current feature flag values (public, no auth required)."""
    return flags.to_dict()
