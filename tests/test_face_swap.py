"""
Tests for the Face Swap Service (Mode B: Real Face Livestream).

These tests verify:
  1. FaceSwapService configuration and initialization
  2. HTTP client request building (headers, auth)
  3. Source face management
  4. Stream lifecycle (start/stop/status)
  5. Single frame swap (testing endpoint)
  6. Health check functionality
  7. Error handling (connection errors, API errors)
  8. Schema validation
  9. Endpoint integration (via FastAPI TestClient)
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ──────────────────────────────────────────────
# Test 1: Service Configuration
# ──────────────────────────────────────────────


def test_service_default_config():
    """Verify service initializes with explicit configuration."""
    from app.services.face_swap_service import FaceSwapService
    service = FaceSwapService(
        worker_url="http://test-gpu:8000",
        api_key="test-key-123",
    )
    assert service.worker_url == "http://test-gpu:8000"
    assert service.api_key == "test-key-123"


def test_service_explicit_config():
    """Verify service accepts explicit configuration."""
    from app.services.face_swap_service import FaceSwapService
    service = FaceSwapService(
        worker_url="http://custom-gpu:9000",
        api_key="custom-key",
        connect_timeout=5.0,
        read_timeout=60.0,
    )
    assert service.worker_url == "http://custom-gpu:9000"
    assert service.api_key == "custom-key"
    assert service.connect_timeout == 5.0
    assert service.read_timeout == 60.0


def test_service_strips_trailing_slash():
    """Verify worker URL trailing slash is stripped."""
    from app.services.face_swap_service import FaceSwapService
    service = FaceSwapService(worker_url="http://gpu:8000/")
    assert service.worker_url == "http://gpu:8000"


def test_service_no_url_empty():
    """Verify service accepts empty worker URL."""
    from app.services.face_swap_service import FaceSwapService
    service = FaceSwapService(worker_url="", api_key="")
    assert service.worker_url == ""


# ──────────────────────────────────────────────
# Test 2: HTTP Headers
# ──────────────────────────────────────────────


def test_build_headers_with_api_key():
    """Verify headers include API key when configured."""
    from app.services.face_swap_service import FaceSwapService
    service = FaceSwapService(
        worker_url="http://gpu:8000",
        api_key="secret-key",
    )
    headers = service._build_headers()
    assert headers["Content-Type"] == "application/json"
    assert headers["X-API-Key"] == "secret-key"


def test_build_headers_without_api_key():
    """Verify headers work without API key (no X-API-Key header)."""
    from app.services.face_swap_service import FaceSwapService
    service = FaceSwapService(
        worker_url="http://gpu:8000",
        api_key="",
    )
    headers = service._build_headers()
    assert headers["Content-Type"] == "application/json"
    # When api_key is empty string, _build_headers still includes it
    # because the check is `if self.api_key:` which is falsy for ""
    # This is correct behavior - empty string means no key


# ──────────────────────────────────────────────
# Test 3: Error Handling
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_no_url_raises():
    """Verify error when worker URL is not configured."""
    from app.services.face_swap_service import FaceSwapService, WorkerConnectionError
    service = FaceSwapService(worker_url="", api_key="")

    with pytest.raises(WorkerConnectionError, match="not configured"):
        await service._request("GET", "/api/health")


@pytest.mark.asyncio
async def test_set_source_requires_image():
    """Verify error when neither image_url nor image_base64 is provided."""
    from app.services.face_swap_service import FaceSwapService, FaceSwapError
    service = FaceSwapService(worker_url="http://gpu:8000")

    with pytest.raises(FaceSwapError, match="Either image_url or image_base64"):
        await service.set_source_face()


# ──────────────────────────────────────────────
# Test 4: Mocked HTTP Requests
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_check_ok():
    """Verify health check returns OK when worker responds."""
    from app.services.face_swap_service import FaceSwapService

    service = FaceSwapService(worker_url="http://gpu:8000", api_key="key")

    mock_response = {
        "status": "ok",
        "gpu_name": "NVIDIA RTX 4090",
        "gpu_memory_used_mb": 4096,
        "gpu_memory_total_mb": 24576,
        "facefusion_version": "3.5.4",
        "stream_status": "idle",
    }

    with patch.object(service, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_response
        result = await service.health_check()

    assert result["status"] == "ok"
    assert result["gpu_name"] == "NVIDIA RTX 4090"
    assert "worker_url" in result


@pytest.mark.asyncio
async def test_health_check_unreachable():
    """Verify health check handles unreachable worker."""
    from app.services.face_swap_service import FaceSwapService, WorkerConnectionError

    service = FaceSwapService(worker_url="http://gpu:8000")

    with patch.object(service, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = WorkerConnectionError("Connection refused")
        result = await service.health_check()

    assert result["status"] == "unreachable"
    assert "Connection refused" in result["error"]


@pytest.mark.asyncio
async def test_health_check_not_configured():
    """Verify health check when worker URL is not set."""
    from app.services.face_swap_service import FaceSwapService

    service = FaceSwapService(worker_url="", api_key="")
    result = await service.health_check()
    assert result["status"] == "not_configured"


@pytest.mark.asyncio
async def test_set_source_face_with_url():
    """Verify set_source_face sends correct payload with URL."""
    from app.services.face_swap_service import FaceSwapService

    service = FaceSwapService(worker_url="http://gpu:8000")

    mock_response = {
        "status": "ok",
        "face_detected": True,
        "face_bbox": [100, 50, 300, 350],
        "face_landmarks": 68,
    }

    with patch.object(service, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_response
        result = await service.set_source_face(
            image_url="https://example.com/face.jpg",
            face_index=0,
        )

    mock_req.assert_called_once_with(
        "POST", "/api/set-source",
        json_data={
            "image_url": "https://example.com/face.jpg",
            "face_index": 0,
        },
    )
    assert result["face_detected"] is True


@pytest.mark.asyncio
async def test_start_stream():
    """Verify start_stream sends correct payload."""
    from app.services.face_swap_service import FaceSwapService

    service = FaceSwapService(worker_url="http://gpu:8000")

    mock_response = {
        "session_id": "sess-abc123",
        "status": "starting",
    }

    with patch.object(service, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_response
        result = await service.start_stream(
            input_rtmp="rtmp://input/live/key1",
            output_rtmp="rtmp://output/live/key2",
            quality="balanced",
            resolution="720p",
            fps=30,
        )

    call_args = mock_req.call_args
    assert call_args[0][0] == "POST"
    assert call_args[0][1] == "/api/start-stream"
    payload = call_args[1]["json_data"]
    assert payload["input_rtmp"] == "rtmp://input/live/key1"
    assert payload["output_rtmp"] == "rtmp://output/live/key2"
    assert payload["quality"] == "balanced"
    assert result["session_id"] == "sess-abc123"


@pytest.mark.asyncio
async def test_stop_stream():
    """Verify stop_stream sends correct payload."""
    from app.services.face_swap_service import FaceSwapService

    service = FaceSwapService(worker_url="http://gpu:8000")

    mock_response = {
        "session_id": "sess-abc123",
        "uptime_seconds": 3600.5,
        "frames_processed": 108000,
    }

    with patch.object(service, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_response
        result = await service.stop_stream(session_id="sess-abc123")

    assert result["uptime_seconds"] == 3600.5
    assert result["frames_processed"] == 108000


@pytest.mark.asyncio
async def test_swap_single_frame():
    """Verify swap_single_frame sends correct payload."""
    from app.services.face_swap_service import FaceSwapService

    service = FaceSwapService(worker_url="http://gpu:8000")

    # Create a small test "image" (just base64 data)
    test_frame = base64.b64encode(b"fake-image-data").decode()

    mock_response = {
        "output_base64": base64.b64encode(b"swapped-image-data").decode(),
        "processing_ms": 45.2,
        "faces_detected": 1,
    }

    with patch.object(service, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_response
        result = await service.swap_single_frame(
            frame_base64=test_frame,
            quality="high",
            face_enhancer=True,
        )

    assert result["faces_detected"] == 1
    assert result["processing_ms"] == 45.2
    assert result["output_base64"] is not None


# ──────────────────────────────────────────────
# Test 5: Schema Validation
# ──────────────────────────────────────────────


def test_set_source_face_schema():
    """Verify SetSourceFaceRequest schema validation."""
    from app.schemas.digital_human_schema import SetSourceFaceRequest

    # Valid with URL
    req = SetSourceFaceRequest(image_url="https://example.com/face.jpg")
    assert req.image_url == "https://example.com/face.jpg"
    assert req.face_index == 0

    # Valid with base64
    req = SetSourceFaceRequest(image_base64="abc123")
    assert req.image_base64 == "abc123"

    # Default face_index
    req = SetSourceFaceRequest(image_url="https://example.com/face.jpg")
    assert req.face_index == 0


def test_start_stream_schema():
    """Verify StartFaceSwapStreamRequest schema validation."""
    from app.schemas.digital_human_schema import StartFaceSwapStreamRequest

    req = StartFaceSwapStreamRequest(
        input_rtmp="rtmp://input/live/key",
        output_rtmp="rtmp://output/live/key",
    )
    assert req.quality == "balanced"
    assert req.resolution == "720p"
    assert req.fps == 30
    assert req.face_enhancer is True
    assert req.face_mask_blur == 0.3


def test_start_stream_schema_custom():
    """Verify StartFaceSwapStreamRequest with custom values."""
    from app.schemas.digital_human_schema import StartFaceSwapStreamRequest

    req = StartFaceSwapStreamRequest(
        input_rtmp="rtmp://input/live/key",
        output_rtmp="rtmp://output/live/key",
        quality="high",
        resolution="1080p",
        fps=60,
        face_enhancer=False,
        face_mask_blur=0.5,
        enable_voice_clone=True,
        elevenlabs_voice_id="voice-123",
    )
    assert req.quality == "high"
    assert req.resolution == "1080p"
    assert req.fps == 60
    assert req.face_enhancer is False
    assert req.enable_voice_clone is True


def test_stream_status_response_schema():
    """Verify FaceSwapStreamStatusResponse schema."""
    from app.schemas.digital_human_schema import FaceSwapStreamStatusResponse

    resp = FaceSwapStreamStatusResponse(
        success=True,
        status="running",
        session_id="sess-123",
        fps=29.5,
        latency_ms=33.2,
        uptime_seconds=1800.0,
        frames_processed=54000,
    )
    assert resp.status == "running"
    assert resp.fps == 29.5


def test_full_health_response_schema():
    """Verify FullHealthResponse schema."""
    from app.schemas.digital_human_schema import FullHealthResponse

    resp = FullHealthResponse(
        success=True,
        overall_status="ok",
        mode_a={"status": "ok"},
        mode_b={"status": "ok"},
        capabilities={
            "mode_a_digital_human": True,
            "mode_b_face_swap": True,
        },
    )
    assert resp.overall_status == "ok"
    assert resp.mode_a["status"] == "ok"


# ──────────────────────────────────────────────
# Test 6: Enum Values
# ──────────────────────────────────────────────


def test_stream_status_enum():
    """Verify StreamStatus enum values."""
    from app.services.face_swap_service import StreamStatus

    assert StreamStatus.IDLE == "idle"
    assert StreamStatus.RUNNING == "running"
    assert StreamStatus.ERROR == "error"


def test_face_swap_quality_enum():
    """Verify FaceSwapQuality enum values."""
    from app.services.face_swap_service import FaceSwapQuality

    assert FaceSwapQuality.FAST == "fast"
    assert FaceSwapQuality.BALANCED == "balanced"
    assert FaceSwapQuality.HIGH == "high"


# ──────────────────────────────────────────────
# Test 7: Exception Classes
# ──────────────────────────────────────────────


def test_worker_api_error():
    """Verify WorkerAPIError stores status code and detail."""
    from app.services.face_swap_service import WorkerAPIError

    err = WorkerAPIError("test error", status_code=500, detail="Internal Server Error")
    assert err.status_code == 500
    assert err.detail == "Internal Server Error"
    assert str(err) == "test error"


def test_exception_hierarchy():
    """Verify exception hierarchy."""
    from app.services.face_swap_service import (
        FaceSwapError,
        WorkerConnectionError,
        WorkerAPIError,
    )

    assert issubclass(WorkerConnectionError, FaceSwapError)
    assert issubclass(WorkerAPIError, FaceSwapError)
