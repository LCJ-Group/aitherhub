"""
Tests for the Tencent Digital Human (數智人) Livestream integration.

These tests verify:
  1. HMAC-SHA256 signature generation matches Tencent's documented examples
  2. Service class correctly builds API payloads
  3. Script generator scoring and fallback logic
"""

import hashlib
import hmac
import base64
import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest

# ──────────────────────────────────────────────
# Test 1: Signature Generation
# ──────────────────────────────────────────────


def test_signature_generation():
    """
    Verify that our signature generation matches the documented example:
      appkey=example_appkey, timestamp=1717639699, accesstoken=example_accesstoken
      Expected signature (before URL encode): aCNWYzZdplxWVo+JsqzZc9+J9XrwWWITfX3eQpsLVno=
    """
    from app.services.tencent_digital_human_service import _generate_signature

    params = {
        "appkey": "example_appkey",
        "timestamp": "1717639699",
    }
    access_token = "example_accesstoken"

    signature = _generate_signature(params, access_token)

    # The expected raw base64 signature
    expected_raw = "aCNWYzZdplxWVo+JsqzZc9+J9XrwWWITfX3eQpsLVno="
    expected_encoded = quote(expected_raw, safe="")

    assert signature == expected_encoded, (
        f"Signature mismatch!\n"
        f"  Got:      {signature}\n"
        f"  Expected: {expected_encoded}"
    )


def test_signature_with_request_id():
    """
    Verify signature with 3 params (appkey, requestid, timestamp).
    Expected: QVenICk0VHtHGYZKXM6IC+W1CjZC1joSr/x0gfKKYT4=
    """
    from app.services.tencent_digital_human_service import _generate_signature

    params = {
        "appkey": "example_appkey",
        "requestid": "example_requestid",
        "timestamp": "1717639699",
    }
    access_token = "example_accesstoken"

    signature = _generate_signature(params, access_token)

    expected_raw = "QVenICk0VHtHGYZKXM6IC+W1CjZC1joSr/x0gfKKYT4="
    expected_encoded = quote(expected_raw, safe="")

    assert signature == expected_encoded, (
        f"Signature mismatch!\n"
        f"  Got:      {signature}\n"
        f"  Expected: {expected_encoded}"
    )


# ──────────────────────────────────────────────
# Test 2: URL Building
# ──────────────────────────────────────────────


def test_build_signed_url():
    """Verify the signed URL format is correct."""
    from app.services.tencent_digital_human_service import _build_signed_url

    url = _build_signed_url(
        "/v2/ivh/liveroom/liveroomservice/openliveroom",
        "test_appkey",
        "test_token",
    )

    assert url.startswith("https://gw.tvs.qq.com/v2/ivh/liveroom/liveroomservice/openliveroom?")
    assert "appkey=test_appkey" in url
    assert "timestamp=" in url
    assert "signature=" in url


# ──────────────────────────────────────────────
# Test 3: Data Classes
# ──────────────────────────────────────────────


def test_script_req_to_dict():
    """Verify ScriptReq serialization."""
    from app.services.tencent_digital_human_service import ScriptReq, VideoLayer

    script = ScriptReq(
        content="Hello, welcome to our livestream!",
        backgrounds=[VideoLayer(url="https://example.com/bg.jpg")],
    )
    d = script.to_dict()

    assert d["Content"] == "Hello, welcome to our livestream!"
    assert len(d["Backgrounds"]) == 1
    assert d["Backgrounds"][0]["Url"] == "https://example.com/bg.jpg"
    assert d["Backgrounds"][0]["Width"] == 1920
    assert "Foregrounds" not in d  # Empty foregrounds should be omitted


def test_video_layer_to_dict():
    """Verify VideoLayer serialization."""
    from app.services.tencent_digital_human_service import VideoLayer

    layer = VideoLayer(url="https://example.com/img.png", x=100, y=200, width=800, height=600)
    d = layer.to_dict()

    assert d == {
        "Url": "https://example.com/img.png",
        "X": 100,
        "Y": 200,
        "Width": 800,
        "Height": 600,
    }


def test_speech_param_to_dict():
    """Verify SpeechParam serialization."""
    from app.services.tencent_digital_human_service import SpeechParam

    param = SpeechParam(speed=1.2, timbre_key="voice_001", volume=5)
    d = param.to_dict()

    assert d["Speed"] == 1.2
    assert d["TimbreKey"] == "voice_001"
    assert d["Volume"] == 5


# ──────────────────────────────────────────────
# Test 4: Phase Scoring
# ──────────────────────────────────────────────


def test_phase_scoring():
    """Verify phase scoring logic."""
    from app.services.script_generator_service import _score_phase

    # High-performing phase
    high_phase = {
        "gmv": 1000,
        "delta_view": 500,
        "delta_like": 100,
        "cta_score": 0.8,
    }
    # Low-performing phase
    low_phase = {
        "gmv": 0,
        "delta_view": 10,
        "delta_like": 5,
        "cta_score": 0.1,
    }

    high_score = _score_phase(high_phase)
    low_score = _score_phase(low_phase)

    assert high_score > low_score, "High-performing phase should have higher score"
    assert high_score > 0, "Score should be positive for non-zero metrics"


def test_phase_scoring_with_none_values():
    """Verify scoring handles None values gracefully."""
    from app.services.script_generator_service import _score_phase

    phase = {
        "gmv": None,
        "delta_view": None,
        "delta_like": None,
        "cta_score": None,
    }
    score = _score_phase(phase)
    assert score == 0.0, "Score should be 0 for all-None metrics"


# ──────────────────────────────────────────────
# Test 5: Fallback Script Generation
# ──────────────────────────────────────────────


def test_fallback_script_ja():
    """Verify Japanese fallback script generation."""
    from app.services.script_generator_service import _build_fallback_script

    analysis_data = {
        "video": {"top_products": "Product A, Product B"},
        "phases": [
            {"phase_index": 1, "phase_description": "商品Aの紹介", "gmv": 100, "delta_view": 50, "delta_like": 10, "cta_score": 0.5},
        ],
        "insights": [],
        "speech_segments": [
            {"text": "こんにちは、今日は素晴らしい商品をご紹介します。"},
        ],
        "reports": [],
    }

    script = _build_fallback_script(analysis_data, language="ja")

    assert "こんにちは" in script
    assert "商品Aの紹介" in script
    assert "ありがとうございました" in script


def test_fallback_script_zh():
    """Verify Chinese fallback script generation."""
    from app.services.script_generator_service import _build_fallback_script

    analysis_data = {
        "video": {},
        "phases": [],
        "insights": [],
        "speech_segments": [],
        "reports": [],
    }

    script = _build_fallback_script(analysis_data, language="zh")
    assert "大家好" in script
    assert "下次再见" in script


# ──────────────────────────────────────────────
# Test 6: Liveroom Status Mapping
# ──────────────────────────────────────────────


def test_liveroom_status_mapping():
    """Verify all status codes are mapped."""
    from app.services.tencent_digital_human_service import LIVEROOM_STATUS

    assert LIVEROOM_STATUS[0] == "INITIAL"
    assert LIVEROOM_STATUS[1] == "STREAM_CREATING"
    assert LIVEROOM_STATUS[2] == "STREAM_READY"
    assert LIVEROOM_STATUS[3] == "SCRIPT_SPLIT_DONE"
    assert LIVEROOM_STATUS[4] == "SCHEDULING"
    assert LIVEROOM_STATUS[5] == "SCHEDULE_DONE"
    assert LIVEROOM_STATUS[6] == "CLOSED"


# ──────────────────────────────────────────────
# Test 7: Takeover Content Truncation
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_takeover_truncation():
    """Verify takeover content is truncated to 500 chars."""
    from app.services.tencent_digital_human_service import TencentDigitalHumanService

    service = TencentDigitalHumanService(
        appkey="test", access_token="test", project_id="test"
    )

    long_content = "A" * 600

    with patch.object(service, '_post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await service.takeover("room_123", long_content)

        # Verify the content was truncated
        call_args = mock_post.call_args
        payload = call_args[0][1]
        assert len(payload["Content"]) == 500


# ──────────────────────────────────────────────
# Test 8: Pydantic Schema Validation
# ──────────────────────────────────────────────


def test_create_liveroom_request_validation():
    """Verify request schema validation."""
    from app.schemas.digital_human_schema import CreateLiveroomRequest

    # Valid request with video_id
    req = CreateLiveroomRequest(video_id="test-video-123")
    assert req.video_id == "test-video-123"
    assert req.cycle_times == 5
    assert req.protocol == "rtmp"
    assert req.tone == "professional_friendly"

    # Valid request with scripts
    req2 = CreateLiveroomRequest(scripts=["Hello world"])
    assert req2.scripts == ["Hello world"]


def test_takeover_request_validation():
    """Verify takeover request schema."""
    from app.schemas.digital_human_schema import TakeoverRequest

    req = TakeoverRequest(
        liveroom_id="room_123",
        content="Flash sale!",
    )
    assert req.liveroom_id == "room_123"
    assert req.content == "Flash sale!"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
