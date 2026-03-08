"""
Request ID Middleware
====================
Frontend から送られる X-Request-Id ヘッダーを受け取り、
全ログ出力に request_id を含めることで、
フロントエンド ↔ バックエンドのログ相関を実現する。

X-Request-Id がない場合は、バックエンド側で自動生成する。
レスポンスヘッダーにも X-Request-Id を返すことで、
フロントエンド側でも確認可能にする。
"""

import uuid
import time
import logging
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# ContextVar for request_id - 非同期タスク間で安全に伝搬
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """現在のリクエストIDを取得する。どこからでも呼べる。"""
    return request_id_var.get("")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    X-Request-Id ミドルウェア

    - Frontend の X-Request-Id ヘッダーを受け取る
    - なければ backend 側で生成 (be-{uuid4[:12]})
    - ContextVar に格納し、全ログで参照可能にする
    - レスポンスヘッダーに X-Request-Id を返す
    - リクエスト処理時間もログに含める
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. X-Request-Id を取得 or 生成
        request_id = request.headers.get("x-request-id", "")
        if not request_id:
            request_id = f"be-{uuid.uuid4().hex[:12]}"

        # 2. ContextVar に格納
        token = request_id_var.set(request_id)

        # 3. リクエスト処理
        start_time = time.time()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.time() - start_time) * 1000)
            logger.error(
                f"[{request_id}] {request.method} {request.url.path} "
                f"EXCEPTION after {duration_ms}ms: {exc}"
            )
            raise
        finally:
            request_id_var.reset(token)

        # 4. レスポンスヘッダーに X-Request-Id を付与
        response.headers["X-Request-Id"] = request_id

        # 5. アクセスログ（request_id 付き）
        duration_ms = round((time.time() - start_time) * 1000)
        status = response.status_code
        path = request.url.path

        # ヘルスチェックはログを抑制
        if path != "/" and path != "/health":
            log_level = logging.WARNING if status >= 400 else logging.INFO
            logger.log(
                log_level,
                f"[{request_id}] {request.method} {path} "
                f"→ {status} ({duration_ms}ms)"
            )

        return response
