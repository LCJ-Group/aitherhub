"""
Pydantic schemas for the LiveBoost live-analysis endpoints.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Request schemas
# ──────────────────────────────────────────────

class LiveAnalysisStartRequest(BaseModel):
    """POST /api/v1/live-analysis/start"""
    video_id: str = Field(..., description="Logical video ID used during chunk upload")
    user_id: Optional[str] = Field(None, description="User ID (fallback; normally taken from JWT)")
    stream_source: str = Field(
        default="tiktok_live",
        description="Origin platform: tiktok_live | instagram_live",
    )
    total_chunks: Optional[int] = Field(
        None, description="Total number of uploaded chunks",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "video_id": "550e8400-e29b-41d4-a716-446655440000",
                "stream_source": "tiktok_live",
                "total_chunks": 42,
            }
        }


# ──────────────────────────────────────────────
# Response schemas
# ──────────────────────────────────────────────

class LiveAnalysisStartResponse(BaseModel):
    """Response for POST /api/v1/live-analysis/start"""
    job_id: str
    video_id: str
    status: str
    message: str


class SalesMoment(BaseModel):
    """A single detected sales moment."""
    timestamp_start: float = Field(..., description="Start time in seconds")
    timestamp_end: float = Field(..., description="End time in seconds")
    product_name: Optional[str] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    trigger_type: Optional[str] = Field(
        None, description="sales_pop | verbal_cta | comment_surge",
    )
    transcript_snippet: Optional[str] = None


class HookCandidate(BaseModel):
    """A detected hook / attention-grabbing moment."""
    timestamp: float
    hook_text: Optional[str] = None
    score: float = Field(0.0, ge=0.0, le=1.0)


class ClipCandidate(BaseModel):
    """A clip candidate for short-form content."""
    timestamp_start: float
    timestamp_end: float
    title: Optional[str] = None
    score: float = Field(0.0, ge=0.0, le=1.0)
    clip_url: Optional[str] = None


class AnalysisResults(BaseModel):
    """Aggregated analysis results."""
    top_sales_moments: List[SalesMoment] = []
    hook_candidates: List[HookCandidate] = []
    clip_candidates: List[ClipCandidate] = []
    total_duration_seconds: Optional[float] = None
    total_sales_detected: int = 0


class LiveAnalysisStatusResponse(BaseModel):
    """Response for GET /api/v1/live-analysis/status/{video_id}"""
    job_id: str
    video_id: str
    status: str
    current_step: Optional[str] = None
    progress: Optional[float] = Field(None, ge=0.0, le=1.0)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    results: Optional[AnalysisResults] = None
    error_message: Optional[str] = None
    # BUILD 31: Timeout detection fields
    timeout_detected: bool = Field(False, description="True if job appears stuck")
    stale_seconds: Optional[int] = Field(None, description="Seconds since last progress update")


class GenerateChunkUploadURLRequest(BaseModel):
    """POST /api/v1/live-analysis/generate-chunk-upload-url"""
    video_id: str = Field(..., description="Logical video ID for this live session")
    chunk_index: int = Field(..., ge=0, description="0-based chunk index")

    class Config:
        json_schema_extra = {
            "example": {
                "video_id": "550e8400-e29b-41d4-a716-446655440000",
                "chunk_index": 0,
            }
        }


class GenerateChunkUploadURLResponse(BaseModel):
    """Response for chunk upload URL generation"""
    video_id: str
    chunk_index: int
    upload_url: str
    blob_url: str
    expires_at: datetime
