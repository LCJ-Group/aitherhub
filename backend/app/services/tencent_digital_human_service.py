"""
Tencent Cloud Digital Human (數智人) Livestream Service

This module provides a Python client for the Tencent Cloud IVH (Intelligent Virtual Human)
Livestream aPaaS API. It handles authentication (HMAC-SHA256 signing), and wraps all five
core livestream endpoints:

  1. open_liveroom   – Create a new livestream room with scripts
  2. get_liveroom    – Query livestream room status
  3. list_liverooms  – List all active (non-closed) rooms for the appkey
  4. takeover        – Send real-time interjection text (max 500 chars)
  5. close_liveroom  – Shut down a livestream room

Architecture note:
  This is a PoC integration module. Tencent Cloud credentials (appkey, access_token)
  are loaded from environment variables. In production, these should be stored in
  a secure vault (e.g., Azure Key Vault) and rotated periodically.

Reference:
  https://cloud.tencent.com/document/product/1240/112139
"""

from __future__ import annotations

import hashlib
import hmac
import base64
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import httpx

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

TENCENT_IVH_BASE_URL = os.getenv("TENCENT_IVH_BASE_URL", "https://gw.tvs.qq.com")
TENCENT_IVH_APPKEY = os.getenv("TENCENT_IVH_APPKEY", "")
TENCENT_IVH_ACCESS_TOKEN = os.getenv("TENCENT_IVH_ACCESS_TOKEN", "")
TENCENT_IVH_PROJECT_ID = os.getenv("TENCENT_IVH_PROJECT_ID", "")
TENCENT_IVH_PROTOCOL = os.getenv("TENCENT_IVH_PROTOCOL", "rtmp")  # rtmp / trtc / webrtc


# ──────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────

class VideoLayer:
    """Background or foreground image layer for the livestream."""

    def __init__(self, url: str, x: int = 0, y: int = 0, width: int = 1920, height: int = 1080):
        self.url = url
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Url": self.url,
            "X": self.x,
            "Y": self.y,
            "Width": self.width,
            "Height": self.height,
        }


class ScriptReq:
    """A single script segment for the digital human to read."""

    def __init__(
        self,
        content: str,
        backgrounds: Optional[List[VideoLayer]] = None,
        foregrounds: Optional[List[VideoLayer]] = None,
    ):
        self.content = content
        self.backgrounds = backgrounds or []
        self.foregrounds = foregrounds or []

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"Content": self.content}
        if self.backgrounds:
            d["Backgrounds"] = [bg.to_dict() for bg in self.backgrounds]
        if self.foregrounds:
            d["Foregrounds"] = [fg.to_dict() for fg in self.foregrounds]
        return d


class SpeechParam:
    """Voice parameters for the digital human."""

    def __init__(
        self,
        speed: float = 1.0,
        timbre_key: Optional[str] = None,
        volume: int = 0,
    ):
        self.speed = speed
        self.timbre_key = timbre_key
        self.volume = volume

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"Speed": self.speed, "Volume": self.volume}
        if self.timbre_key:
            d["TimbreKey"] = self.timbre_key
        return d


class AnchorParam:
    """Position and scale parameters for the digital human anchor."""

    def __init__(
        self,
        horizontal_position: float = 0.0,
        vertical_position: float = 0.0,
        scale: float = 1.0,
    ):
        self.horizontal_position = horizontal_position
        self.vertical_position = vertical_position
        self.scale = scale

    def to_dict(self) -> Dict[str, Any]:
        return {
            "HorizontalPosition": self.horizontal_position,
            "VerticalPosition": self.vertical_position,
            "Scale": self.scale,
        }


# ──────────────────────────────────────────────
# Liveroom Status Enum
# ──────────────────────────────────────────────

LIVEROOM_STATUS = {
    0: "INITIAL",
    1: "STREAM_CREATING",
    2: "STREAM_READY",
    3: "SCRIPT_SPLIT_DONE",
    4: "SCHEDULING",
    5: "SCHEDULE_DONE",
    6: "CLOSED",
}


# ──────────────────────────────────────────────
# Signing Utilities
# ──────────────────────────────────────────────

def _generate_signature(params: Dict[str, str], access_token: str) -> str:
    """
    Generate HMAC-SHA256 signature for Tencent IVH aPaaS API.

    Steps:
      1. Sort params by key (dict order)
      2. Build signing string: key1=val1&key2=val2
      3. HMAC-SHA256 with access_token as key
      4. Base64 encode
      5. URL encode
    """
    sorted_keys = sorted(params.keys())
    signing_content = "&".join(f"{k}={params[k]}" for k in sorted_keys)
    hash_bytes = hmac.new(
        access_token.encode("utf-8"),
        signing_content.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature_b64 = base64.b64encode(hash_bytes).decode("utf-8")
    return quote(signature_b64, safe="")


def _build_signed_url(path: str, appkey: str, access_token: str) -> str:
    """Build a fully signed URL for the given API path."""
    timestamp = str(int(time.time()))
    params = {
        "appkey": appkey,
        "timestamp": timestamp,
    }
    signature = _generate_signature(params, access_token)
    query_string = f"appkey={appkey}&timestamp={timestamp}&signature={signature}"
    return f"{TENCENT_IVH_BASE_URL}{path}?{query_string}"


# ──────────────────────────────────────────────
# Main Service Class
# ──────────────────────────────────────────────

class TencentDigitalHumanService:
    """
    Client for the Tencent Cloud IVH Livestream aPaaS API.

    Usage:
        service = TencentDigitalHumanService()
        result = await service.open_liveroom(scripts=[ScriptReq(content="Hello!")])
    """

    def __init__(
        self,
        appkey: Optional[str] = None,
        access_token: Optional[str] = None,
        project_id: Optional[str] = None,
        protocol: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.appkey = appkey or TENCENT_IVH_APPKEY
        self.access_token = access_token or TENCENT_IVH_ACCESS_TOKEN
        self.project_id = project_id or TENCENT_IVH_PROJECT_ID
        self.protocol = protocol or TENCENT_IVH_PROTOCOL
        self.base_url = base_url or TENCENT_IVH_BASE_URL

        if not self.appkey or not self.access_token:
            logger.warning(
                "TencentDigitalHumanService: TENCENT_IVH_APPKEY or TENCENT_IVH_ACCESS_TOKEN "
                "not configured. API calls will fail."
            )

    def _signed_url(self, path: str) -> str:
        return _build_signed_url(path, self.appkey, self.access_token)

    @staticmethod
    def _gen_req_id() -> str:
        return uuid.uuid4().hex

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send a signed POST request to the Tencent IVH API."""
        url = self._signed_url(path)
        body = {
            "Header": {},
            "Payload": payload,
        }
        logger.info(f"Tencent IVH API call: POST {path}")
        logger.debug(f"Tencent IVH request body: {body}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json=body,
                headers={"Content-Type": "application/json;charset=utf-8"},
            )

        if response.status_code != 200:
            logger.error(
                f"Tencent IVH API error: status={response.status_code}, "
                f"body={response.text}"
            )
            raise TencentAPIError(
                f"HTTP {response.status_code}: {response.text}",
                status_code=response.status_code,
            )

        data = response.json()
        header = data.get("Header", {})
        code = header.get("Code", -1)
        if code != 0:
            msg = header.get("Message", "Unknown error")
            logger.error(f"Tencent IVH API business error: code={code}, message={msg}")
            raise TencentAPIError(f"Business error {code}: {msg}", code=code)

        return data.get("Payload", {})

    # ──────────────────────────────────────────
    # 1. Open Liveroom (創建直播間)
    # ──────────────────────────────────────────

    async def open_liveroom(
        self,
        scripts: List[ScriptReq],
        cycle_times: int = 5,
        callback_url: Optional[str] = None,
        virtualman_project_id: Optional[str] = None,
        protocol: Optional[str] = None,
        speech_param: Optional[SpeechParam] = None,
        anchor_param: Optional[AnchorParam] = None,
    ) -> Dict[str, Any]:
        """
        Create a new livestream room with the given scripts.

        Returns:
            dict with keys: LiveRoomId, Status, ReqId, VideoStreamPlayUrl (when ready)
        """
        req_id = self._gen_req_id()
        proj_id = virtualman_project_id or self.project_id
        proto = protocol or self.protocol

        video_stream_req: Dict[str, Any] = {
            "Protocol": proto,
        }
        if proj_id:
            video_stream_req["VirtualmanProjectId"] = proj_id
        if speech_param:
            video_stream_req["SpeechParam"] = speech_param.to_dict()
        if anchor_param:
            video_stream_req["AnchorParam"] = anchor_param.to_dict()

        payload: Dict[str, Any] = {
            "ReqId": req_id,
            "VideoStreamReq": video_stream_req,
            "CycleTimes": cycle_times,
            "Scripts": [s.to_dict() for s in scripts],
        }
        if callback_url:
            payload["CallbackUrl"] = callback_url

        result = await self._post(
            "/v2/ivh/liveroom/liveroomservice/openliveroom",
            payload,
        )
        logger.info(
            f"Liveroom created: id={result.get('LiveRoomId')}, "
            f"status={result.get('Status')}"
        )
        return result

    # ──────────────────────────────────────────
    # 2. Get Liveroom (查詢直播間信息)
    # ──────────────────────────────────────────

    async def get_liveroom(self, liveroom_id: str) -> Dict[str, Any]:
        """
        Query the status and details of a livestream room.

        Returns:
            dict with keys: LiveRoomId, Status, VideoStreamPlayUrl, etc.
        """
        req_id = self._gen_req_id()
        result = await self._post(
            "/v2/ivh/liveroom/liveroomservice/getliveroom",
            {"ReqId": req_id, "LiveRoomId": liveroom_id},
        )
        status_code = result.get("Status", -1)
        status_label = LIVEROOM_STATUS.get(status_code, "UNKNOWN")
        logger.info(f"Liveroom {liveroom_id}: status={status_code} ({status_label})")
        return result

    # ──────────────────────────────────────────
    # 3. List Liverooms (查詢直播間列表)
    # ──────────────────────────────────────────

    async def list_liverooms(self) -> Dict[str, Any]:
        """
        List all active (non-closed) livestream rooms for this appkey.
        """
        req_id = self._gen_req_id()
        result = await self._post(
            "/v2/ivh/liveroom/liveroomservice/listliveroomofappkey",
            {"ReqId": req_id},
        )
        return result

    # ──────────────────────────────────────────
    # 4. Takeover (直播接管 / 即時挿播)
    # ──────────────────────────────────────────

    async def takeover(self, liveroom_id: str, content: str) -> Dict[str, Any]:
        """
        Send real-time interjection text to a livestream room.
        The digital human will immediately speak this text, interrupting
        the current script.

        Args:
            liveroom_id: The livestream room ID
            content: Text to speak (max 500 characters)
        """
        if len(content) > 500:
            logger.warning(
                f"Takeover content truncated from {len(content)} to 500 chars"
            )
            content = content[:500]

        req_id = self._gen_req_id()
        result = await self._post(
            "/v2/ivh/liveroom/liveroomservice/takeover",
            {
                "ReqId": req_id,
                "LiveRoomId": liveroom_id,
                "Content": content,
            },
        )
        logger.info(f"Takeover sent to liveroom {liveroom_id}: {content[:50]}...")
        return result

    # ──────────────────────────────────────────
    # 5. Close Liveroom (關閉直播間)
    # ──────────────────────────────────────────

    async def close_liveroom(self, liveroom_id: str) -> Dict[str, Any]:
        """
        Close a livestream room. This stops the digital human and
        releases all resources.
        """
        req_id = self._gen_req_id()
        result = await self._post(
            "/v2/ivh/liveroom/liveroomservice/closeliveroom",
            {"ReqId": req_id, "LiveRoomId": liveroom_id},
        )
        logger.info(f"Liveroom {liveroom_id} closed")
        return result

    # ──────────────────────────────────────────
    # Convenience: Wait for stream ready
    # ──────────────────────────────────────────

    async def wait_for_stream_ready(
        self,
        liveroom_id: str,
        max_wait_seconds: int = 120,
        poll_interval: float = 3.0,
    ) -> Dict[str, Any]:
        """
        Poll the liveroom status until the video stream is ready (Status=2)
        or timeout is reached.

        Returns:
            The liveroom info dict when ready.

        Raises:
            TimeoutError if the stream doesn't become ready in time.
        """
        import asyncio

        start = time.time()
        while True:
            info = await self.get_liveroom(liveroom_id)
            status = info.get("Status", 0)

            if status == 2:
                logger.info(f"Liveroom {liveroom_id} stream is ready!")
                return info
            if status == 6:
                raise TencentAPIError(
                    f"Liveroom {liveroom_id} was closed before stream became ready"
                )

            elapsed = time.time() - start
            if elapsed > max_wait_seconds:
                raise TimeoutError(
                    f"Liveroom {liveroom_id} stream not ready after {max_wait_seconds}s "
                    f"(current status={status})"
                )

            logger.debug(
                f"Waiting for stream ready: status={status}, "
                f"elapsed={elapsed:.1f}s/{max_wait_seconds}s"
            )
            await asyncio.sleep(poll_interval)


# ──────────────────────────────────────────────
# Custom Exception
# ──────────────────────────────────────────────

class TencentAPIError(Exception):
    """Exception raised when a Tencent IVH API call fails."""

    def __init__(self, message: str, status_code: int = 0, code: int = 0):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
