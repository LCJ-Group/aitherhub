"""
Request ID Middleware
====================
Frontend から送られる X-Request-Id ヘッダーを受け取り、
全ログ出力に request_id / video_id / user_id / processing_time を含めることで、
フロントエンド ↔ バックエンドのログ相関を実現する。

X-Request-Id がない場合は、バックエンド側で自動生成する。
レスポンスヘッダーにも X-Request-Id を返すことで、
フロントエンド側でも確認可能にする。
"""

import uuid
import time
import re
import logging
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# ContextVars for request context - 非同期タスク間で安全に伝搬
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
video_id_var: ContextVar[str] = ContextVar("video_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")


def get_request_id() -> str:
    """現在のリクエストIDを取得する。どこからでも呼べる。"""
    return request_id_var.get("")


def get_video_id() -> str:
    """現在のリクエストのvideo_idを取得する。"""
    return video_id_var.get("")


def get_user_id() -> str:
    """現在のリクエストのuser_idを取得する。"""
    return user_id_var.get("")


# video_id をパスから抽出するパターン
# /api/v1/video/{video_id}/... のようなパスに対応
VIDEO_ID_PATTERN = re.compile(r"/video/([a-zA-Z0-9_-]+)")


def _extract_video_id(path: str) -> str:
    """URLパスからvideo_idを抽出する。見つからなければ空文字。"""
    match = VIDEO_ID_PATTERN.search(path)
    return match.group(1) if match else ""


def _extract_user_id(request: Request) -> str:
    """リクエストからuser_idを抽出する。認証トークンまたはクエリパラメータから。"""
    # 1. X-User-Id ヘッダー（フロントエンドから明示的に送る場合）
    user_id = request.headers.get("x-user-id", "")
    if user_id:
        return user_id

    # 2. クエリパラメータの user_id
    user_id = request.query_params.get("user_id", "")
    if user_id:
        return user_id

    # 3. Authorization ヘッダーからの抽出は認証ミドルウェアに任せる
    return ""


class RequestIdFilter(logging.Filter):
    """
    ログフィルター: 全ログレコードに request_id / video_id / user_id を自動付与。
    logging.Formatter で %(request_id)s %(video_id)s %(user_id)s が使えるようになる。
    """

    def filter(self, record):
        record.request_id = request_id_var.get("")
        record.video_id = video_id_var.get("")
        record.user_id = user_id_var.get("")
        return True


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    X-Request-Id ミドルウェア

    - Frontend の X-Request-Id ヘッダーを受け取る
    - なければ backend 側で生成 (be-{uuid4[:12]})
    - URLパスから video_id を抽出
    - リクエストから user_id を抽出
    - ContextVar に格納し、全ログで参照可能にする
    - レスポンスヘッダーに X-Request-Id を返す
    - リクエスト処理時間もログに含める
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. X-Request-Id を取得 or 生成
        request_id = request.headers.get("x-request-id", "")
        if not request_id:
            request_id = f"be-{uuid.uuid4().hex[:12]}"

        # 2. video_id / user_id を抽出
        video_id = _extract_video_id(request.url.path)
        user_id = _extract_user_id(request)

        # 3. ContextVar に格納
        rid_token = request_id_var.set(request_id)
        vid_token = video_id_var.set(video_id)
        uid_token = user_id_var.set(user_id)

        # 4. リクエスト処理
        start_time = time.time()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.time() - start_time) * 1000)
            logger.error(
                f"[{request_id}] [video:{video_id}] [user:{user_id}] "
                f"{request.method} {request.url.path} "
                f"EXCEPTION after {duration_ms}ms: {exc}"
            )
            raise
        finally:
            request_id_var.reset(rid_token)
            video_id_var.reset(vid_token)
            user_id_var.reset(uid_token)

        # 5. レスポンスヘッダーに X-Request-Id を付与
        response.headers["X-Request-Id"] = request_id

        # 6. 構造化アクセスログ（request_id / video_id / user_id / processing_time 付き）
        duration_ms = round((time.time() - start_time) * 1000)
        status = response.status_code
        path = request.url.path

        # ヘルスチェックはログを抑制
        if path not in ("/", "/health", "/favicon.ico"):
            log_level = logging.WARNING if status >= 400 else logging.INFO

            # 構造化ログ: JSON風フォーマットで全フィールドを出力
            log_parts = [
                f"request_id={request_id}",
                f"method={request.method}",
                f"path={path}",
                f"status={status}",
                f"duration_ms={duration_ms}",
            ]
            if video_id:
                log_parts.append(f"video_id={video_id}")
            if user_id:
                log_parts.append(f"user_id={user_id}")

            logger.log(log_level, " | ".join(log_parts))

        return response
