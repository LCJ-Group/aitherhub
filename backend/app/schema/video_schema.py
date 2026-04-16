from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel

from app.schema.base_schema import ModelBaseInfo


class GenerateUploadURLRequest(BaseModel):
    """Request schema for generating upload URL"""
    email: str
    filename: str
    video_id: Optional[str] = None

    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "filename": "my_video.mp4",
                "video_id": "550e8400-e29b-41d4-a716-446655440000"
            }
        }


class GenerateUploadURLResponse(BaseModel):
    """Response schema for upload URL generation"""
    video_id: str
    upload_id: str
    upload_url: str
    blob_url: str
    expires_at: datetime

    class Config:
        schema_extra = {
            "example": {
                "video_id": "550e8400-e29b-41d4-a716-446655440000",
                "upload_id": "550e8400-e29b-41d4-a716-446655440001",
                "upload_url": "https://tien.blob.core.windows.net/videos/user@example.com/550e8400-e29b-41d4-a716-446655440000/550e8400.mp4?se=...&sp=cw&sv=...",
                "blob_url": "https://tien.blob.core.windows.net/videos/user@example.com/550e8400-e29b-41d4-a716-446655440000/550e8400.mp4",
                "expires_at": "2025-12-30T10:30:00+00:00"
            }
        }


class GenerateDownloadURLRequest(BaseModel):
    """Request schema for generating download URL"""
    email: str
    video_id: str
    filename: Optional[str] = None
    expires_in_minutes: Optional[int] = None

    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "video_id": "550e8400-e29b-41d4-a716-446655440000",
                "filename": "my_video.mp4",
                "expires_in_minutes": 1440
            }
        }


class GenerateDownloadURLResponse(BaseModel):
    """Response schema for download URL generation"""
    video_id: str
    download_url: str
    expires_at: datetime

    class Config:
        schema_extra = {
            "example": {
                "video_id": "550e8400-e29b-41d4-a716-446655440000",
                "download_url": "https://tien.blob.core.windows.net/videos/user@example.com/550e8400-e29b-41d4-a716-446655440000/550e8400.mp4?se=...&sp=r&sv=...",
                "expires_at": "2025-12-31T10:30:00+00:00"
            }
        }


class UploadCompleteRequest(BaseModel):
    """Request schema for upload completion"""
    email: str
    video_id: str
    filename: str
    upload_id: Optional[str] = None
    upload_type: Optional[str] = "screen_recording"  # 'screen_recording' or 'clean_video'
    excel_product_blob_url: Optional[str] = None
    excel_trend_blob_url: Optional[str] = None
    time_offset_seconds: Optional[float] = 0  # offset within CSV timeline (seconds)
    language: Optional[str] = "ja"  # AI analysis output language (ja, zh-TW)

    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "video_id": "550e8400-e29b-41d4-a716-446655440000",
                "filename": "my_video.mp4",
                "upload_id": "550e8400-e29b-41d4-a716-446655440001",
                "upload_type": "screen_recording",
                "time_offset_seconds": 0
            }
        }


class UploadCompleteResponse(BaseModel):
    """Response schema for upload completion"""
    video_id: str
    status: str
    message: str

    class Config:
        schema_extra = {
            "example": {
                "video_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "pending",
                "message": "Video upload completed successfully"
            }
        }


class GenerateExcelUploadURLRequest(BaseModel):
    """Request schema for generating Excel file upload URLs"""
    email: str
    video_id: str
    product_filename: str
    trend_filename: str

    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "video_id": "550e8400-e29b-41d4-a716-446655440000",
                "product_filename": "ryukyogoku_product.xlsx",
                "trend_filename": "ryukyogoku_trend_stats.xlsx"
            }
        }


class GenerateExcelUploadURLResponse(BaseModel):
    """Response schema for Excel file upload URL generation"""
    video_id: str
    product_upload_url: str
    product_blob_url: str
    trend_upload_url: str
    trend_blob_url: str
    expires_at: datetime


class RenameVideoRequest(BaseModel):
    """Request schema for renaming a video"""
    name: str


class RenameVideoResponse(BaseModel):
    """Response schema for renaming a video"""
    id: str
    original_filename: str
    message: str


class DeleteVideoResponse(BaseModel):
    """Response schema for deleting a video"""
    id: str
    message: str


class BatchVideoItem(BaseModel):
    """A single video in a batch upload"""
    video_id: str
    filename: str
    upload_id: Optional[str] = None
    time_offset_seconds: Optional[float] = 0  # offset within CSV timeline (seconds)


class BatchUploadCompleteRequest(BaseModel):
    """Request schema for batch upload completion (multiple videos sharing same Excel)"""
    email: str
    videos: List[BatchVideoItem]
    excel_product_blob_url: Optional[str] = None
    excel_trend_blob_url: Optional[str] = None
    language: Optional[str] = "ja"  # AI analysis output language (ja, zh-TW)

    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "videos": [
                    {"video_id": "id-1", "filename": "part1.mp4", "time_offset_seconds": 0},
                    {"video_id": "id-2", "filename": "part2.mp4", "time_offset_seconds": 36000},
                ],
                "excel_product_blob_url": "https://...",
                "excel_trend_blob_url": "https://..."
            }
        }


class BatchUploadCompleteResponse(BaseModel):
    """Response schema for batch upload completion"""
    video_ids: List[str]
    status: str
    message: str
    failed: List[dict] = []  # [{"video_id": "...", "error": "..."}]


class LiveCaptureRequest(BaseModel):
    """Request schema for TikTok live stream capture"""
    live_url: str  # TikTok live URL (e.g., https://www.tiktok.com/@user/live)
    duration: Optional[int] = 0  # Max recording duration in seconds (0 = until stream ends)

    class Config:
        schema_extra = {
            "example": {
                "live_url": "https://www.tiktok.com/@hapimaruuu/live",
                "duration": 0
            }
        }


class LiveCaptureResponse(BaseModel):
    """Response schema for live capture initiation"""
    video_id: str
    status: str
    stream_title: Optional[str] = None
    username: Optional[str] = None
    message: str


class LiveCheckResponse(BaseModel):
    """Response schema for checking if a TikTok user is live"""
    is_live: bool
    username: Optional[str] = None
    room_id: Optional[str] = None
    title: Optional[str] = None
    message: str


class VideoResponse(ModelBaseInfo):
    """Video response schema"""
    original_filename: Optional[str] = None
    status: str  # pending, processing, completed, failed
    duration: Optional[float] = None
    file_size: Optional[int] = None
    upload_type: Optional[str] = "screen_recording"
