"""
Pydantic schemas for the Tencent Digital Human (數智人) Livestream API endpoints.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Request Schemas
# ──────────────────────────────────────────────

class VideoLayerSchema(BaseModel):
    url: str = Field(..., description="Image URL (jpg/jpeg/png/gif, <2MB recommended)")
    x: int = Field(0, description="Left-top X coordinate")
    y: int = Field(0, description="Left-top Y coordinate")
    width: int = Field(1920, description="Output width")
    height: int = Field(1080, description="Output height")


class SpeechParamSchema(BaseModel):
    speed: float = Field(1.0, ge=0.5, le=2.0, description="Speech speed (0.5-2.0)")
    timbre_key: Optional[str] = Field(None, description="Voice timbre key")
    volume: int = Field(0, ge=-10, le=10, description="Volume adjustment (-10 to 10)")


class AnchorParamSchema(BaseModel):
    horizontal_position: float = Field(0.0, description="Horizontal position offset")
    vertical_position: float = Field(0.0, description="Vertical position offset")
    scale: float = Field(1.0, ge=0.1, le=3.0, description="Scale factor")


class CreateLiveroomRequest(BaseModel):
    """Request to create a new digital human livestream room."""
    video_id: Optional[str] = Field(
        None,
        description="AitherHub video ID to generate scripts from analysis data. "
        "If provided, scripts will be auto-generated from analysis results."
    )
    scripts: Optional[List[str]] = Field(
        None,
        description="Manual script texts. If video_id is provided, this is ignored."
    )
    cycle_times: int = Field(5, ge=0, le=500, description="Number of script loop cycles")
    protocol: str = Field("rtmp", description="Stream protocol: rtmp / trtc / webrtc")
    virtualman_project_id: Optional[str] = Field(
        None, description="Override Tencent IVH project ID"
    )
    callback_url: Optional[str] = Field(None, description="Callback URL for status updates")
    speech_param: Optional[SpeechParamSchema] = None
    anchor_param: Optional[AnchorParamSchema] = None
    backgrounds: Optional[List[VideoLayerSchema]] = Field(
        None, description="Background image layers"
    )
    # Script generation options (only used when video_id is provided)
    product_focus: Optional[str] = Field(
        None, description="Product name to emphasize in generated script"
    )
    tone: str = Field(
        "professional_friendly",
        description="Script tone: professional_friendly / energetic / calm"
    )
    language: str = Field("ja", description="Script language: ja / zh / en")


class CreateLiveroomResponse(BaseModel):
    """Response after creating a livestream room."""
    success: bool
    liveroom_id: Optional[str] = None
    status: Optional[int] = None
    status_label: Optional[str] = None
    req_id: Optional[str] = None
    play_url: Optional[str] = None
    script_preview: Optional[str] = Field(
        None, description="First 500 chars of the generated script"
    )
    error: Optional[str] = None


class GetLiveroomRequest(BaseModel):
    liveroom_id: str


class GetLiveroomResponse(BaseModel):
    success: bool
    liveroom_id: Optional[str] = None
    status: Optional[int] = None
    status_label: Optional[str] = None
    play_url: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ListLiveroomsResponse(BaseModel):
    success: bool
    liverooms: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class TakeoverRequest(BaseModel):
    """Request to send real-time interjection to a livestream."""
    liveroom_id: str
    content: Optional[str] = Field(
        None,
        max_length=500,
        description="Direct text to speak (max 500 chars). "
        "If not provided, use event_context + event_type to auto-generate."
    )
    # Auto-generation options
    event_context: Optional[str] = Field(
        None,
        description="Context for auto-generating takeover script "
        "(e.g., 'Product X just sold 50 units')"
    )
    event_type: str = Field(
        "product_highlight",
        description="Event type: product_highlight / engagement_spike / flash_sale / viewer_question"
    )
    language: str = Field("ja", description="Language for auto-generated script")


class TakeoverResponse(BaseModel):
    success: bool
    content_sent: Optional[str] = None
    error: Optional[str] = None


class CloseLiveroomRequest(BaseModel):
    liveroom_id: str


class CloseLiveroomResponse(BaseModel):
    success: bool
    liveroom_id: Optional[str] = None
    error: Optional[str] = None


# ──────────────────────────────────────────────
# Script Generation Schemas
# ──────────────────────────────────────────────

class GenerateScriptRequest(BaseModel):
    """Request to generate a script from video analysis without creating a liveroom."""
    video_id: str = Field(..., description="AitherHub video ID")
    product_focus: Optional[str] = Field(None, description="Product to emphasize")
    tone: str = Field("professional_friendly", description="Script tone")
    language: str = Field("ja", description="Output language")


class GenerateScriptResponse(BaseModel):
    success: bool
    video_id: Optional[str] = None
    script: Optional[str] = None
    script_length: Optional[int] = None
    phases_used: Optional[int] = Field(
        None, description="Number of analysis phases used to generate the script"
    )
    error: Optional[str] = None
