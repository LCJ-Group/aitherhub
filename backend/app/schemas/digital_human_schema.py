"""
Pydantic schemas for the Tencent Digital Human (數智人) Livestream API endpoints.

Includes schemas for:
  - Tencent Cloud IVH direct API (text-driven)
  - Hybrid mode: ElevenLabs voice cloning + Tencent Cloud (audio-driven)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Common Schemas
# ──────────────────────────────────────────────

class VideoLayerSchema(BaseModel):
    url: str = Field(..., description="Image URL (jpg/jpeg/png/gif, <2MB recommended)")
    x: int = Field(0, description="Left-top X coordinate")
    y: int = Field(0, description="Left-top Y coordinate")
    width: int = Field(1920, description="Output width")
    height: int = Field(1080, description="Output height")


class SpeechParamSchema(BaseModel):
    speed: float = Field(1.0, ge=0.5, le=2.0, description="Speech speed (0.5-2.0)")
    timbre_key: Optional[str] = Field(
        None,
        description="Voice timbre key. For custom cloned voice (声音复刻), "
        "use the voice ID from Tencent IVH voice cloning service."
    )
    volume: int = Field(0, ge=-10, le=10, description="Volume adjustment (-10 to 10)")
    pitch: float = Field(0.0, ge=-12.0, le=12.0, description="Pitch adjustment in semitones (-12 to 12)")


class AnchorParamSchema(BaseModel):
    horizontal_position: float = Field(0.0, description="Horizontal position offset")
    vertical_position: float = Field(0.0, description="Vertical position offset")
    scale: float = Field(1.0, ge=0.1, le=3.0, description="Scale factor")


# ──────────────────────────────────────────────
# Liveroom Request/Response Schemas
# ──────────────────────────────────────────────

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
    # Hybrid mode options
    use_hybrid_voice: bool = Field(
        False,
        description="If true, use ElevenLabs voice cloning for TTS "
        "(supports Japanese). Pre-generates audio for each script."
    )
    elevenlabs_voice_id: Optional[str] = Field(
        None,
        description="Override ElevenLabs voice ID for hybrid mode. "
        "If not set, uses the default configured voice."
    )


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
    mode: Optional[str] = Field(
        None, description="Operation mode: text_only / hybrid"
    )
    audio_results: Optional[List[Dict[str, Any]]] = Field(
        None, description="Pre-generated audio info (hybrid mode only)"
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
    # Hybrid mode options
    use_hybrid_voice: bool = Field(
        False,
        description="If true, also generate audio with ElevenLabs cloned voice"
    )
    elevenlabs_voice_id: Optional[str] = Field(
        None, description="Override ElevenLabs voice ID"
    )


class TakeoverResponse(BaseModel):
    success: bool
    content_sent: Optional[str] = None
    mode: Optional[str] = Field(None, description="text_only / hybrid")
    audio_info: Optional[Dict[str, Any]] = Field(
        None, description="Audio generation info (hybrid mode)"
    )
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


# ──────────────────────────────────────────────
# Hybrid / ElevenLabs Schemas
# ──────────────────────────────────────────────

class HybridHealthResponse(BaseModel):
    """Health check response for the hybrid architecture."""
    success: bool
    overall_status: Optional[str] = None
    elevenlabs: Optional[Dict[str, Any]] = None
    tencent: Optional[Dict[str, Any]] = None
    capabilities: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class GenerateAudioRequest(BaseModel):
    """Request to pre-generate audio from text using ElevenLabs voice cloning."""
    texts: List[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="List of texts to convert to speech (max 50)"
    )
    language: str = Field("ja", description="Language code (ja/zh/en/ko etc.)")
    voice_id: Optional[str] = Field(
        None, description="Override ElevenLabs voice ID"
    )


class GenerateAudioResponse(BaseModel):
    """Response with audio generation results."""
    success: bool
    results: Optional[List[Dict[str, Any]]] = None
    total_duration_ms: Optional[float] = None
    error: Optional[str] = None


class VoiceListResponse(BaseModel):
    """Response listing available ElevenLabs voices."""
    success: bool
    voices: Optional[List[Dict[str, Any]]] = None
    cloned_count: Optional[int] = None
    total_count: Optional[int] = None
    error: Optional[str] = None


# ──────────────────────────────────────────────
# Face Swap / Mode B Schemas
# ──────────────────────────────────────────────


class SetSourceFaceRequest(BaseModel):
    """Request to set the source face for face swapping (influencer's photo)."""
    image_url: Optional[str] = Field(
        None,
        description="Publicly accessible URL of the source face image. "
        "The image should contain a clear, front-facing photo of the person "
        "whose face will be used in the stream."
    )
    image_base64: Optional[str] = Field(
        None,
        description="Base64-encoded image data (alternative to image_url). "
        "Supported formats: JPEG, PNG."
    )
    face_index: int = Field(
        0,
        ge=0,
        le=10,
        description="If the image contains multiple faces, which one to use "
        "(0 = largest/most prominent face)."
    )


class SetSourceFaceResponse(BaseModel):
    """Response after setting the source face."""
    success: bool
    face_detected: Optional[bool] = None
    face_bbox: Optional[List[float]] = Field(
        None, description="Bounding box of the detected face [x1, y1, x2, y2]"
    )
    face_landmarks: Optional[int] = Field(
        None, description="Number of facial landmarks detected"
    )
    error: Optional[str] = None


class StartFaceSwapStreamRequest(BaseModel):
    """Request to start a real-time face swap livestream."""
    input_rtmp: str = Field(
        ...,
        description="RTMP URL of the input stream (body double's camera feed). "
        "Example: rtmp://input-server/live/body-double-key"
    )
    output_rtmp: str = Field(
        ...,
        description="RTMP URL of the output stream (streaming platform ingest). "
        "Example: rtmp://live-push.platform.com/live/stream-key"
    )
    quality: str = Field(
        "balanced",
        description="Quality preset: fast (60fps, lower quality), "
        "balanced (30fps, good quality), high (15-20fps, best quality)"
    )
    resolution: str = Field(
        "720p",
        description="Output resolution: 480p / 720p / 1080p"
    )
    fps: int = Field(
        30,
        ge=10,
        le=60,
        description="Output frame rate (10-60)"
    )
    face_enhancer: bool = Field(
        True,
        description="Enable GFPGAN face enhancement for more realistic results"
    )
    face_mask_blur: float = Field(
        0.3,
        ge=0.0,
        le=1.0,
        description="Face mask blur amount for seamless blending (0.0-1.0)"
    )
    # Voice integration (optional, uses existing ElevenLabs setup)
    enable_voice_clone: bool = Field(
        False,
        description="Also enable ElevenLabs voice cloning for audio output"
    )
    elevenlabs_voice_id: Optional[str] = Field(
        None,
        description="Override ElevenLabs voice ID for voice cloning"
    )


class StartFaceSwapStreamResponse(BaseModel):
    """Response after starting a face swap stream."""
    success: bool
    session_id: Optional[str] = None
    status: Optional[str] = Field(
        None, description="Stream status: starting / running / error"
    )
    input_rtmp: Optional[str] = None
    output_rtmp: Optional[str] = None
    quality: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[int] = None
    error: Optional[str] = None


class StopFaceSwapStreamRequest(BaseModel):
    """Request to stop a face swap stream."""
    session_id: Optional[str] = Field(
        None,
        description="Specific session to stop. If not provided, stops the current stream."
    )


class StopFaceSwapStreamResponse(BaseModel):
    """Response after stopping a face swap stream."""
    success: bool
    session_id: Optional[str] = None
    uptime_seconds: Optional[float] = None
    frames_processed: Optional[int] = None
    error: Optional[str] = None


class FaceSwapStreamStatusResponse(BaseModel):
    """Response with current face swap stream status."""
    success: bool
    status: Optional[str] = Field(
        None, description="idle / starting / running / stopping / error"
    )
    session_id: Optional[str] = None
    fps: Optional[float] = Field(None, description="Current processing FPS")
    latency_ms: Optional[float] = Field(
        None, description="Current processing latency in ms"
    )
    uptime_seconds: Optional[float] = None
    frames_processed: Optional[int] = None
    errors: Optional[List[str]] = Field(
        None, description="Recent errors (if any)"
    )
    error: Optional[str] = None


class SwapSingleFrameRequest(BaseModel):
    """Request to swap face in a single frame (for testing/preview)."""
    frame_base64: str = Field(
        ...,
        description="Base64-encoded input frame (JPEG or PNG)"
    )
    quality: str = Field(
        "high",
        description="Quality preset: fast / balanced / high"
    )
    face_enhancer: bool = Field(
        True,
        description="Enable GFPGAN face enhancement"
    )


class SwapSingleFrameResponse(BaseModel):
    """Response with the face-swapped frame."""
    success: bool
    output_base64: Optional[str] = Field(
        None, description="Base64-encoded output frame (JPEG)"
    )
    processing_ms: Optional[float] = Field(
        None, description="Processing time in milliseconds"
    )
    faces_detected: Optional[int] = Field(
        None, description="Number of faces detected in the input"
    )
    error: Optional[str] = None


class FaceSwapHealthResponse(BaseModel):
    """Health check response for the face swap GPU worker."""
    success: bool
    status: Optional[str] = Field(
        None, description="ok / unreachable / error / not_configured"
    )
    gpu_name: Optional[str] = None
    gpu_memory_used_mb: Optional[float] = None
    gpu_memory_total_mb: Optional[float] = None
    facefusion_version: Optional[str] = None
    stream_status: Optional[str] = None
    worker_url: Optional[str] = None
    error: Optional[str] = None


class FullHealthResponse(BaseModel):
    """Combined health check for all services (Mode A + Mode B)."""
    success: bool
    overall_status: Optional[str] = None
    mode_a: Optional[Dict[str, Any]] = Field(
        None, description="Mode A health (Tencent Digital Human + ElevenLabs)"
    )
    mode_b: Optional[Dict[str, Any]] = Field(
        None, description="Mode B health (FaceFusion GPU Worker)"
    )
    capabilities: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ──────────────────────────────────────────────
# Mode C: MuseTalk Lip-Sync Schemas
# ──────────────────────────────────────────────


class MuseTalkGenerateRequest(BaseModel):
    """Request to generate a lip-synced video using MuseTalk."""
    portrait_url: str = Field(
        ...,
        description="Publicly accessible URL of the portrait image (front-facing photo). "
        "Supported formats: JPEG, PNG. Recommended: 512x512 or larger, clear face."
    )
    audio_url: str = Field(
        ...,
        description="Publicly accessible URL of the audio file. "
        "Supported formats: WAV (16kHz recommended), MP3. "
        "The audio will drive the lip-sync animation."
    )
    job_id: Optional[str] = Field(
        None,
        description="Custom job ID. If not provided, one will be auto-generated."
    )
    bbox_shift: int = Field(
        0,
        ge=-50,
        le=50,
        description="Vertical shift for face bounding box detection. "
        "Positive values shift down, negative values shift up."
    )
    extra_margin: int = Field(
        10,
        ge=0,
        le=50,
        description="Extra margin below face for MuseTalk v1.5 (pixels)."
    )
    batch_size: int = Field(
        16,
        ge=1,
        le=64,
        description="Inference batch size. Higher = faster but more VRAM."
    )
    output_fps: int = Field(
        25,
        ge=15,
        le=60,
        description="Output video frame rate."
    )


class MuseTalkGenerateResponse(BaseModel):
    """Response after starting a MuseTalk generation job."""
    success: bool
    job_id: Optional[str] = None
    status: Optional[str] = Field(
        None, description="Job status: queued / processing / completed / error"
    )
    error: Optional[str] = None


class MuseTalkStatusResponse(BaseModel):
    """Response with MuseTalk job status."""
    success: bool
    job_id: Optional[str] = None
    status: Optional[str] = Field(
        None, description="queued / processing / completed / error"
    )
    progress: Optional[int] = Field(
        None, ge=0, le=100, description="Progress percentage (0-100)"
    )
    error: Optional[str] = None


class MuseTalkHealthResponse(BaseModel):
    """Health check response for the MuseTalk GPU worker."""
    success: bool
    status: Optional[str] = Field(
        None, description="ok / unreachable / error / not_configured"
    )
    gpu_name: Optional[str] = None
    gpu_memory_used_mb: Optional[float] = None
    gpu_memory_total_mb: Optional[float] = None
    musetalk_loaded: Optional[bool] = None
    worker_url: Optional[str] = None
    error: Optional[str] = None


# ──────────────────────────────────────────────
# Mode C+: MuseTalk + ElevenLabs TTS (Text → Video)
# ──────────────────────────────────────────────


class MuseTalkTextGenerateRequest(BaseModel):
    """Request to generate a lip-synced video from text using ElevenLabs TTS + MuseTalk."""
    portrait_url: str = Field(
        ...,
        description="Publicly accessible URL of the portrait image (front-facing photo). "
        "Supported formats: JPEG, PNG. Recommended: 512x512 or larger, clear face."
    )
    text: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Text to convert to speech. The portrait will lip-sync to this text. "
        "Supports Japanese and other languages."
    )
    voice_id: Optional[str] = Field(
        None,
        description="ElevenLabs voice ID. If not provided, uses the default configured voice."
    )
    language_code: Optional[str] = Field(
        "ja",
        description="Language code for TTS (e.g., 'ja' for Japanese, 'en' for English)."
    )
    voice_settings: Optional[Dict[str, Any]] = Field(
        None,
        description="ElevenLabs voice settings override (stability, similarity_boost, etc.)."
    )
    job_id: Optional[str] = Field(
        None,
        description="Custom job ID. If not provided, one will be auto-generated."
    )
    bbox_shift: int = Field(
        0, ge=-50, le=50,
        description="Vertical shift for face bounding box detection."
    )
    extra_margin: int = Field(
        10, ge=0, le=50,
        description="Extra margin below face for MuseTalk v1.5 (pixels)."
    )
    batch_size: int = Field(
        16, ge=1, le=64,
        description="Inference batch size. Higher = faster but more VRAM."
    )
    output_fps: int = Field(
        25, ge=15, le=60,
        description="Output video frame rate."
    )


class MuseTalkTextGenerateResponse(BaseModel):
    """Response after starting a TTS + MuseTalk generation job."""
    success: bool
    job_id: Optional[str] = None
    status: Optional[str] = Field(
        None, description="Job status: tts_generating / queued / processing / completed / error"
    )
    tts_duration_ms: Optional[float] = Field(
        None, description="Duration of the generated TTS audio in milliseconds."
    )
    audio_url: Optional[str] = Field(
        None, description="URL of the generated TTS audio (stored in Azure Blob)."
    )
    error: Optional[str] = None


class VoiceOption(BaseModel):
    """A single voice option for the voice selector."""
    voice_id: str
    name: str
    category: Optional[str] = None
    is_cloned: bool = False
    labels: Optional[Dict[str, Any]] = None


# ──────────────────────────────────────────────
# IMTalker Premium Digital Human Schemas
# ──────────────────────────────────────────────

class IMTalkerGenerateRequest(BaseModel):
    """Request to generate a premium digital human video using IMTalker."""
    portrait_url: str = Field(
        ...,
        description="URL of the portrait image (front-facing photo). "
        "Must be publicly accessible or have a SAS token."
    )
    audio_url: str = Field(
        ...,
        description="URL of the audio file (WAV/MP3). "
        "Must be publicly accessible or have a SAS token."
    )
    job_id: Optional[str] = Field(
        None,
        description="Custom job ID. If not provided, one will be auto-generated."
    )
    a_cfg_scale: float = Field(
        2.0, ge=0.5, le=5.0,
        description="Audio classifier-free guidance scale. "
        "Higher values = more expressive animation. Default 2.0."
    )
    nfe: int = Field(
        10, ge=5, le=30,
        description="Number of function evaluations for ODE solver. "
        "Higher = better quality but slower. Default 10."
    )
    crop: bool = Field(
        True,
        description="Whether to auto-crop the face region from the portrait."
    )
    output_fps: int = Field(
        25, ge=15, le=60,
        description="Output video frame rate."
    )


class IMTalkerGenerateResponse(BaseModel):
    """Response after starting an IMTalker generation job."""
    success: bool
    job_id: Optional[str] = None
    status: Optional[str] = Field(
        None, description="Job status: queued / processing / completed / error"
    )
    engine: str = Field("imtalker", description="Engine used for generation.")
    error: Optional[str] = None


class IMTalkerTextGenerateRequest(BaseModel):
    """Request to generate a premium digital human video from text.
    Pipeline: Text → ElevenLabs TTS → IMTalker → Full-animation video."""
    portrait_url: str = Field(
        ...,
        description="URL of the portrait image (front-facing photo)."
    )
    text: str = Field(
        ..., min_length=1, max_length=5000,
        description="Text to convert to speech. The portrait will animate to this text."
    )
    voice_id: Optional[str] = Field(
        None,
        description="ElevenLabs voice ID. If not provided, uses the default configured voice."
    )
    language_code: Optional[str] = Field(
        "ja",
        description="Language code for TTS (e.g., 'ja' for Japanese, 'en' for English)."
    )
    voice_settings: Optional[Dict[str, Any]] = Field(
        None,
        description="ElevenLabs voice settings override."
    )
    job_id: Optional[str] = Field(
        None,
        description="Custom job ID. If not provided, one will be auto-generated."
    )
    a_cfg_scale: float = Field(
        2.0, ge=0.5, le=5.0,
        description="Audio CFG scale. Higher = more expressive."
    )
    nfe: int = Field(
        10, ge=5, le=30,
        description="ODE solver steps. Higher = better quality but slower."
    )
    crop: bool = Field(
        True,
        description="Whether to auto-crop the face region."
    )
    output_fps: int = Field(
        25, ge=15, le=60,
        description="Output video frame rate."
    )


class IMTalkerTextGenerateResponse(BaseModel):
    """Response after starting a TTS + IMTalker generation job."""
    success: bool
    job_id: Optional[str] = None
    status: Optional[str] = Field(
        None, description="Job status: tts_generating / queued / processing / completed / error"
    )
    tts_duration_ms: Optional[float] = Field(
        None, description="Duration of the generated TTS audio in milliseconds."
    )
    audio_url: Optional[str] = Field(
        None, description="URL of the generated TTS audio."
    )
    engine: str = Field("imtalker", description="Engine used for generation.")
    error: Optional[str] = None


# ══════════════════════════════════════════════
# Live Session (AI Live Creator Livestream Brain)
# ══════════════════════════════════════════════


class ProductInfo(BaseModel):
    """Product information for the Sales Brain."""
    name: str = Field(..., description="Product name")
    description: str = Field("", description="Product description")
    price: str = Field("", description="Price (e.g., '¥3,980')")
    features: Optional[List[str]] = Field(None, description="Key features list")
    image_url: Optional[str] = Field(None, description="Product image URL")
    tone: str = Field("professional_friendly", description="Script tone for this product")


class CreateLiveSessionRequest(BaseModel):
    """Request to create a new AI Live Creator session."""
    portrait_url: str = Field(
        ...,
        description="URL of the portrait image for the digital human."
    )
    engine: str = Field(
        "imtalker",
        description="Engine: 'musetalk' (Standard) or 'imtalker' (Premium)"
    )
    voice_id: Optional[str] = Field(
        None,
        description="ElevenLabs voice ID. If not set, uses default."
    )
    language: str = Field("ja", description="Language: ja / zh / en")
    products: Optional[List[ProductInfo]] = Field(
        None,
        description="List of products for the Sales Brain to generate scripts for."
    )


class CreateLiveSessionResponse(BaseModel):
    success: bool
    session_id: Optional[str] = None
    status: Optional[str] = None
    engine: Optional[str] = None
    products_count: int = 0
    error: Optional[str] = None


class LiveSessionStatusResponse(BaseModel):
    success: bool
    session: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ListLiveSessionsResponse(BaseModel):
    success: bool
    sessions: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class GenerateProductScriptRequest(BaseModel):
    """Request to generate a script for a specific product (Sales Brain)."""
    session_id: Optional[str] = Field(None, description="Live session ID (optional)")
    product_name: str = Field(..., description="Product name")
    product_description: str = Field("", description="Product description")
    product_price: str = Field("", description="Price")
    product_features: Optional[List[str]] = Field(None, description="Key features")
    tone: str = Field("professional_friendly", description="Script tone")
    language: str = Field("ja", description="Output language")
    script_type: str = Field(
        "introduction",
        description="Script type: introduction / highlight / promotion / closing"
    )


class GenerateProductScriptResponse(BaseModel):
    success: bool
    product_name: Optional[str] = None
    script_type: Optional[str] = None
    script_text: Optional[str] = None
    script_length: Optional[int] = None
    error: Optional[str] = None


class CommentResponseRequest(BaseModel):
    """Request to generate a response to a viewer comment."""
    session_id: Optional[str] = Field(None, description="Live session ID (optional)")
    comment_text: str = Field(..., min_length=1, max_length=500, description="Viewer's comment")
    commenter_name: str = Field("", description="Viewer's display name")
    current_product: Optional[ProductInfo] = Field(
        None, description="Currently featured product (for context)"
    )
    language: str = Field("ja", description="Response language")
    # Auto-generate video
    auto_generate_video: bool = Field(
        False,
        description="If true, automatically generate a digital human video "
        "with the response (TTS + engine)."
    )
    portrait_url: Optional[str] = Field(
        None,
        description="Portrait URL for video generation (required if auto_generate_video=true)"
    )
    engine: str = Field("musetalk", description="Engine for video: musetalk / imtalker")
    voice_id: Optional[str] = Field(None, description="ElevenLabs voice ID")


class CommentResponseResponse(BaseModel):
    success: bool
    comment_text: Optional[str] = None
    reply_text: Optional[str] = None
    reply_length: Optional[int] = None
    video_job_id: Optional[str] = Field(
        None, description="Job ID if auto_generate_video was true"
    )
    error: Optional[str] = None


class GenerateAndQueueRequest(BaseModel):
    """Generate a digital human video from text and add to session queue."""
    session_id: str = Field(..., description="Live session ID")
    text: str = Field(..., min_length=1, max_length=5000, description="Text to speak")
    queue_type: str = Field(
        "product_intro",
        description="Type: product_intro / comment_reply / custom"
    )
    product_name: Optional[str] = Field(None, description="Associated product name")


class GenerateAndQueueResponse(BaseModel):
    success: bool
    job_id: Optional[str] = None
    queue_position: Optional[int] = None
    status: Optional[str] = None
    error: Optional[str] = None
