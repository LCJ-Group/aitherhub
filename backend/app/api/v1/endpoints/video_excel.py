"""Video API — Excel & CSV Operations

Split from video.py for maintainability.
"""
from typing import List, Optional
import json
import uuid as uuid_module
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from loguru import logger

from app.core.dependencies import get_db, get_current_user
from app.models.orm.video import Video

router = APIRouter(
    prefix="/videos",
    tags=["videos"],
)

@router.put("/{video_id}/replace-excel")
async def replace_excel(
    video_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    既存動画のExcelファイル（商品データ/トレンドデータ）を差し替える。

    フロー:
    1. 新しいExcelファイルはフロントから直接Blobにアップロード済み
    2. このAPIで videos テーブルの excel_*_blob_url を更新
    3. upload_type を clean_video に変更
    4. Worker再処理をキューに投入

    Request body:
    {
        "excel_product_blob_url": "https://...",  // optional
        "excel_trend_blob_url": "https://...",    // optional
        "reprocess": true  // Workerの再処理をトリガーするか
    }
    """
    try:
        user_id = current_user["id"]
        email = current_user["email"]
        body = await request.json()

        excel_product_blob_url = body.get("excel_product_blob_url")
        excel_trend_blob_url = body.get("excel_trend_blob_url")
        reprocess = body.get("reprocess", True)

        if not excel_product_blob_url and not excel_trend_blob_url:
            raise HTTPException(status_code=400, detail="At least one Excel URL is required")

        # 1. 動画の存在確認とオーナーチェック
        video_sql = text("""
            SELECT id, original_filename, status, upload_type, user_id,
                   excel_product_blob_url, excel_trend_blob_url
            FROM videos
            WHERE id = :video_id AND user_id = :user_id
        """)
        result = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
        video = result.fetchone()

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        old_product_url = video.excel_product_blob_url
        old_trend_url = video.excel_trend_blob_url

        # 2. Excel URLを更新
        update_fields = ["upload_type = 'clean_video'", "updated_at = NOW()"]
        params = {"video_id": video_id}

        if excel_product_blob_url:
            update_fields.append("excel_product_blob_url = :product_url")
            params["product_url"] = excel_product_blob_url
        if excel_trend_blob_url:
            update_fields.append("excel_trend_blob_url = :trend_url")
            params["trend_url"] = excel_trend_blob_url

        update_sql = text(f"""
            UPDATE videos SET {', '.join(update_fields)}
            WHERE id = :video_id
        """)
        await db.execute(update_sql, params)
        await db.commit()

        logger.info(
            f"[replace-excel] video_id={video_id} user_id={user_id} "
            f"product={'replaced' if excel_product_blob_url else 'unchanged'} "
            f"trend={'replaced' if excel_trend_blob_url else 'unchanged'}"
        )

        # 3. Worker再処理をトリガー
        reprocess_status = "skipped"
        if reprocess:
            try:
                from app.services.storage_service import generate_download_sas
                from app.services.queue_service import enqueue_job

                # 動画のダウンロードURLを生成
                download_url, _ = await generate_download_sas(
                    email=email,
                    video_id=video_id,
                    filename=video.original_filename,
                    expires_in_minutes=1440,
                )

                queue_payload = {
                    "video_id": video_id,
                    "blob_url": download_url,
                    "original_filename": video.original_filename,
                    "user_id": user_id,
                    "upload_type": "clean_video",
                    "time_offset_seconds": 0,
                    "is_reprocess": True,
                }

                # Excel SAS URLを生成して追加
                final_product_url = excel_product_blob_url or old_product_url
                final_trend_url = excel_trend_blob_url or old_trend_url

                if final_product_url:
                    try:
                        product_download_url, _ = await generate_download_sas(
                            email=email,
                            video_id=video_id,
                            filename=f"excel/{final_product_url.split('/')[-1].split('?')[0]}",
                            expires_in_minutes=1440,
                        )
                        queue_payload["excel_product_url"] = product_download_url
                    except Exception as exc:
                        logger.warning(f"[replace-excel] Excel product SAS failed: {exc}")

                if final_trend_url:
                    try:
                        trend_download_url, _ = await generate_download_sas(
                            email=email,
                            video_id=video_id,
                            filename=f"excel/{final_trend_url.split('/')[-1].split('?')[0]}",
                            expires_in_minutes=1440,
                        )
                        queue_payload["excel_trend_url"] = trend_download_url
                    except Exception as exc:
                        logger.warning(f"[replace-excel] Excel trend SAS failed: {exc}")

                await enqueue_job(queue_payload)
                reprocess_status = "queued"
                logger.info(f"[replace-excel] Reprocess queued for video_id={video_id}")
            except Exception as exc:
                logger.exception(f"[replace-excel] Failed to enqueue reprocess: {exc}")
                reprocess_status = f"failed: {str(exc)}"

        # 4. video_upload_assets テーブルに versioned attachment を記録
        try:
            # テーブル作成（初回のみ）
            create_assets_sql = text("""
                CREATE TABLE IF NOT EXISTS video_upload_assets (
                    id BIGSERIAL PRIMARY KEY,
                    video_id VARCHAR(100) NOT NULL,
                    asset_type VARCHAR(20) NOT NULL CHECK (asset_type IN ('video', 'trend_csv', 'product_csv')),
                    original_filename VARCHAR(500),
                    blob_url TEXT,
                    file_size BIGINT DEFAULT 0,
                    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
                    uploaded_by INT,
                    is_active BOOLEAN DEFAULT TRUE,
                    version INT DEFAULT 1,
                    validation_status VARCHAR(20) DEFAULT 'unknown',
                    validation_result JSONB,
                    replaced_by_id BIGINT DEFAULT NULL
                )
            """)
            await db.execute(create_assets_sql)
            # Create indexes separately (PostgreSQL doesn't support inline INDEX in CREATE TABLE)
            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_vua_video ON video_upload_assets (video_id)",
                "CREATE INDEX IF NOT EXISTS idx_vua_video_type ON video_upload_assets (video_id, asset_type)",
                "CREATE INDEX IF NOT EXISTS idx_vua_active ON video_upload_assets (video_id, asset_type, is_active)",
            ]:
                await db.execute(text(idx_sql))

            # 旧アセットを非アクティブにして、新アセットを登録
            if excel_product_blob_url:
                # 旧product CSVの最大versionを取得
                ver_sql = text("""
                    SELECT COALESCE(MAX(version), 0) as max_ver
                    FROM video_upload_assets
                    WHERE video_id = :video_id AND asset_type = 'product_csv'
                """)
                ver_result = await db.execute(ver_sql, {"video_id": video_id})
                max_ver = ver_result.scalar() or 0

                # 旧アセットを非アクティブに
                await db.execute(text("""
                    UPDATE video_upload_assets
                    SET is_active = FALSE
                    WHERE video_id = :video_id AND asset_type = 'product_csv' AND is_active = TRUE
                """), {"video_id": video_id})

                # 新アセットを登録
                product_fn = excel_product_blob_url.split("?")[0].split("/")[-1] if excel_product_blob_url else None
                await db.execute(text("""
                    INSERT INTO video_upload_assets
                        (video_id, asset_type, original_filename, blob_url,
                         uploaded_by, version, is_active)
                    VALUES
                        (:video_id, 'product_csv', :filename, :blob_url,
                         :user_id, :version, TRUE)
                """), {
                    "video_id": video_id,
                    "filename": product_fn,
                    "blob_url": (excel_product_blob_url or "")[:2000],
                    "user_id": user_id,
                    "version": max_ver + 1,
                })

            if excel_trend_blob_url:
                ver_sql = text("""
                    SELECT COALESCE(MAX(version), 0) as max_ver
                    FROM video_upload_assets
                    WHERE video_id = :video_id AND asset_type = 'trend_csv'
                """)
                ver_result = await db.execute(ver_sql, {"video_id": video_id})
                max_ver = ver_result.scalar() or 0

                await db.execute(text("""
                    UPDATE video_upload_assets
                    SET is_active = FALSE
                    WHERE video_id = :video_id AND asset_type = 'trend_csv' AND is_active = TRUE
                """), {"video_id": video_id})

                trend_fn = excel_trend_blob_url.split("?")[0].split("/")[-1] if excel_trend_blob_url else None
                await db.execute(text("""
                    INSERT INTO video_upload_assets
                        (video_id, asset_type, original_filename, blob_url,
                         uploaded_by, version, is_active)
                    VALUES
                        (:video_id, 'trend_csv', :filename, :blob_url,
                         :user_id, :version, TRUE)
                """), {
                    "video_id": video_id,
                    "filename": trend_fn,
                    "blob_url": (excel_trend_blob_url or "")[:2000],
                    "user_id": user_id,
                    "version": max_ver + 1,
                })

            await db.commit()
            logger.info(f"[replace-excel] Assets recorded for video_id={video_id}")
        except Exception as exc:
            logger.warning(f"[replace-excel] Failed to record assets: {exc}")

        # 5. 差し替えログも記録（後方互換）
        try:
            create_log_sql = text("""
                CREATE TABLE IF NOT EXISTS excel_replace_logs (
                    id BIGSERIAL PRIMARY KEY,
                    video_id VARCHAR(100),
                    user_id INT,
                    old_product_url TEXT,
                    old_trend_url TEXT,
                    new_product_url TEXT,
                    new_trend_url TEXT,
                    reprocess_status VARCHAR(50),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await db.execute(create_log_sql)
            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_excel_replace_video ON excel_replace_logs (video_id)",
                "CREATE INDEX IF NOT EXISTS idx_excel_replace_created ON excel_replace_logs (created_at)",
            ]:
                await db.execute(text(idx_sql))

            insert_log_sql = text("""
                INSERT INTO excel_replace_logs
                    (video_id, user_id, old_product_url, old_trend_url,
                     new_product_url, new_trend_url, reprocess_status)
                VALUES
                    (:video_id, :user_id, :old_product, :old_trend,
                     :new_product, :new_trend, :reprocess_status)
            """)
            await db.execute(insert_log_sql, {
                "video_id": video_id,
                "user_id": user_id,
                "old_product": (old_product_url or "")[:500],
                "old_trend": (old_trend_url or "")[:500],
                "new_product": (excel_product_blob_url or "")[:500],
                "new_trend": (excel_trend_blob_url or "")[:500],
                "reprocess_status": reprocess_status,
            })
            await db.commit()
        except Exception as exc:
            logger.warning(f"[replace-excel] Failed to log replacement: {exc}")

        return {
            "status": "ok",
            "video_id": video_id,
            "product_replaced": bool(excel_product_blob_url),
            "trend_replaced": bool(excel_trend_blob_url),
            "reprocess_status": reprocess_status,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[replace-excel] Unexpected error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{video_id}/excel-info")
async def get_excel_info(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    動画に紐付いているExcel（CSV）情報を取得する。
    video_upload_assets テーブルから versioned attachment 情報を返す。
    """
    try:
        user_id = current_user["id"]

        video_sql = text("""
            SELECT id, original_filename, upload_type,
                   excel_product_blob_url, excel_trend_blob_url,
                   created_at, updated_at
            FROM videos
            WHERE id = :video_id AND (user_id = :user_id OR user_id IS NULL)
        """)
        result = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
        video = result.fetchone()

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        def extract_filename(url):
            if not url:
                return None
            try:
                path = url.split("?")[0]
                return path.split("/")[-1]
            except Exception:
                return url

        product_filename = extract_filename(video.excel_product_blob_url)
        trend_filename = extract_filename(video.excel_trend_blob_url)

        # video_upload_assets からアセット情報を取得
        current_assets = {"product_csv": None, "trend_csv": None}
        asset_history = []
        try:
            # 現在アクティブなアセット
            active_sql = text("""
                SELECT id, asset_type, original_filename, blob_url, file_size,
                       uploaded_at, uploaded_by, version, validation_status, validation_result
                FROM video_upload_assets
                WHERE video_id = :video_id AND is_active = TRUE
                ORDER BY asset_type
            """)
            active_result = await db.execute(active_sql, {"video_id": video_id})
            for r in active_result.fetchall():
                current_assets[r.asset_type] = {
                    "id": r.id,
                    "filename": r.original_filename,
                    "version": r.version,
                    "uploaded_at": str(r.uploaded_at) if r.uploaded_at else None,
                    "uploaded_by": r.uploaded_by,
                    "file_size": r.file_size,
                    "validation_status": r.validation_status,
                    "validation_result": json.loads(r.validation_result) if r.validation_result else None,
                }

            # 全履歴（非アクティブ含む）
            history_sql = text("""
                SELECT id, asset_type, original_filename, version,
                       uploaded_at, uploaded_by, is_active, validation_status
                FROM video_upload_assets
                WHERE video_id = :video_id
                ORDER BY uploaded_at DESC
                LIMIT 50
            """)
            history_result = await db.execute(history_sql, {"video_id": video_id})
            for r in history_result.fetchall():
                asset_history.append({
                    "id": r.id,
                    "asset_type": r.asset_type,
                    "filename": r.original_filename,
                    "version": r.version,
                    "uploaded_at": str(r.uploaded_at) if r.uploaded_at else None,
                    "uploaded_by": r.uploaded_by,
                    "is_active": bool(r.is_active),
                    "validation_status": r.validation_status,
                })
        except Exception as exc:
            logger.debug(f"[excel-info] video_upload_assets not available: {exc}")

        # 差し替え履歴を取得（後方互換）
        replace_history = []
        try:
            history_sql = text("""
                SELECT id, old_product_url, old_trend_url,
                       new_product_url, new_trend_url,
                       reprocess_status, created_at
                FROM excel_replace_logs
                WHERE video_id = :video_id
                ORDER BY created_at DESC
                LIMIT 10
            """)
            history_result = await db.execute(history_sql, {"video_id": video_id})
            for r in history_result.fetchall():
                replace_history.append({
                    "id": r.id,
                    "old_product": extract_filename(r.old_product_url),
                    "old_trend": extract_filename(r.old_trend_url),
                    "new_product": extract_filename(r.new_product_url),
                    "new_trend": extract_filename(r.new_trend_url),
                    "reprocess_status": r.reprocess_status,
                    "created_at": str(r.created_at) if r.created_at else None,
                })
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

        return {
            "video_id": video_id,
            "original_filename": video.original_filename,
            "upload_type": video.upload_type or "screen_recording",
            "has_product": bool(video.excel_product_blob_url),
            "has_trend": bool(video.excel_trend_blob_url),
            "product_filename": product_filename,
            "trend_filename": trend_filename,
            "current_assets": current_assets,
            "asset_history": asset_history,
            "created_at": str(video.created_at) if video.created_at else None,
            "updated_at": str(video.updated_at) if video.updated_at else None,
            "replace_history": replace_history,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[excel-info] Unexpected error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# CSV Preview / Re-validation / Validation Status Update APIs
# ============================================================

@router.get("/{video_id}/csv-preview")
async def get_csv_preview(
    video_id: str,
    asset_type: str = "trend_csv",
    max_rows: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    CSVプレビュー: Excelファイルの先頭行とカラム情報を返す。
    asset_type: 'trend_csv' or 'product_csv'
    """
    import tempfile
    import os as _os
    import openpyxl
    import httpx
    from azure.storage.blob import generate_blob_sas, BlobSasPermissions

    try:
        user_id = current_user["id"]
        email = current_user.get("email", "")

        # 動画情報取得
        video_sql = text("""
            SELECT id, excel_product_blob_url, excel_trend_blob_url, user_id
            FROM videos
            WHERE id = :video_id AND (user_id = :user_id OR user_id IS NULL)
        """)
        result = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
        video = result.fetchone()
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        # blob_url 取得
        if asset_type == "product_csv":
            blob_url = video.excel_product_blob_url
        else:
            blob_url = video.excel_trend_blob_url

        if not blob_url:
            return {
                "video_id": video_id,
                "asset_type": asset_type,
                "available": False,
                "message": f"No {asset_type} attached",
            }

        # SAS URL 生成
        conn_str = _os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        account_name = ""
        account_key = ""
        for part in conn_str.split(";"):
            if part.startswith("AccountName="):
                account_name = part.split("=", 1)[1]
            elif part.startswith("AccountKey="):
                account_key = part.split("=", 1)[1]

        from urllib.parse import urlparse, unquote
        parsed = urlparse(blob_url)
        path = unquote(parsed.path)
        if path.startswith("/videos/"):
            blob_name = path[len("/videos/"):]
        else:
            blob_name = path.lstrip("/")
            if blob_name.startswith("videos/"):
                blob_name = blob_name[len("videos/"):]

        expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
        sas = generate_blob_sas(
            account_name=account_name,
            container_name="videos",
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
        )
        sas_url = f"https://{account_name}.blob.core.windows.net/videos/{blob_name}?{sas}"

        # ダウンロードしてパース
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(sas_url)
            if resp.status_code != 200:
                return {
                    "video_id": video_id,
                    "asset_type": asset_type,
                    "available": False,
                    "message": f"Failed to download file (HTTP {resp.status_code})",
                }

            file_size = len(resp.content)

            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                f.write(resp.content)
                tmp_path = f.name

            try:
                wb = openpyxl.load_workbook(tmp_path, read_only=True, data_only=True)
                ws = wb.active
                if not ws:
                    return {
                        "video_id": video_id,
                        "asset_type": asset_type,
                        "available": True,
                        "file_size": file_size,
                        "message": "No active worksheet found",
                        "columns": [],
                        "rows": [],
                        "total_rows": 0,
                    }

                rows_data = list(ws.iter_rows(values_only=True))
                total_rows = len(rows_data) - 1 if len(rows_data) > 0 else 0

                headers = []
                preview_rows = []
                if len(rows_data) >= 1:
                    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows_data[0])]

                if len(rows_data) >= 2:
                    limit = min(max_rows, len(rows_data) - 1)
                    for data_row in rows_data[1:limit + 1]:
                        row_dict = {}
                        for i, val in enumerate(data_row):
                            if i < len(headers):
                                if val is None:
                                    row_dict[headers[i]] = None
                                elif isinstance(val, datetime):
                                    row_dict[headers[i]] = str(val)
                                elif isinstance(val, (int, float)):
                                    row_dict[headers[i]] = val
                                else:
                                    row_dict[headers[i]] = str(val)
                        preview_rows.append(row_dict)

                # カラム分析
                column_info = []
                for col_name in headers:
                    col_data = {
                        "name": col_name,
                        "non_null_count": 0,
                        "sample_values": [],
                    }
                    for row in preview_rows[:5]:
                        val = row.get(col_name)
                        if val is not None:
                            col_data["non_null_count"] += 1
                            if len(col_data["sample_values"]) < 3:
                                col_data["sample_values"].append(str(val)[:100])
                    column_info.append(col_data)

                # 日時カラム検出
                datetime_columns = []
                for col in headers:
                    cl = col.lower() if col else ""
                    if any(kw in cl for kw in ["日時", "time", "date", "timestamp", "開始", "start"]):
                        datetime_columns.append(col)

                wb.close()

                return {
                    "video_id": video_id,
                    "asset_type": asset_type,
                    "available": True,
                    "file_size": file_size,
                    "total_rows": total_rows,
                    "columns": headers,
                    "column_info": column_info,
                    "datetime_columns": datetime_columns,
                    "preview_rows": preview_rows,
                    "sheet_name": ws.title,
                }
            finally:
                _os.unlink(tmp_path)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[csv-preview] Unexpected error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{video_id}/update-validation-status")
async def update_validation_status(
    video_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    フロントエンドからCSVバリデーション結果を保存する。
    video_upload_assets テーブルの validation_status/validation_result を更新。
    """
    try:
        body = await request.json()
        asset_type = body.get("asset_type", "trend_csv")
        validation_status = body.get("validation_status", "unknown")
        validation_result = body.get("validation_result")

        # video_upload_assets テーブルの該当アセットを更新
        update_sql = text("""
            UPDATE video_upload_assets
            SET validation_status = :status,
                validation_result = :result
            WHERE video_id = :video_id
              AND asset_type = :asset_type
              AND is_active = TRUE
        """)
        await db.execute(update_sql, {
            "video_id": video_id,
            "status": validation_status[:20],
            "result": json.dumps(validation_result) if validation_result else None,
            "asset_type": asset_type,
        })
        await db.commit()

        return {"status": "ok", "video_id": video_id, "asset_type": asset_type}
    except Exception as exc:
        logger.warning(f"[update-validation-status] Error: {exc}")
        # テーブルが存在しない場合も許容
        return {"status": "skipped", "reason": str(exc)}


@router.get("/{video_id}/asset-history")
async def get_asset_history(
    video_id: str,
    asset_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    アセットのバージョン履歴を取得する。
    """
    try:
        user_id = current_user["id"]

        # 動画の所有権確認
        video_sql = text("""
            SELECT id FROM videos
            WHERE id = :video_id AND (user_id = :user_id OR user_id IS NULL)
        """)
        result = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Video not found")

        # アセット履歴取得
        if asset_type:
            history_sql = text("""
                SELECT id, asset_type, original_filename, blob_url, file_size,
                       uploaded_at, uploaded_by, is_active, version,
                       validation_status, validation_result
                FROM video_upload_assets
                WHERE video_id = :video_id AND asset_type = :asset_type
                ORDER BY version DESC
                LIMIT 50
            """)
            params = {"video_id": video_id, "asset_type": asset_type}
        else:
            history_sql = text("""
                SELECT id, asset_type, original_filename, blob_url, file_size,
                       uploaded_at, uploaded_by, is_active, version,
                       validation_status, validation_result
                FROM video_upload_assets
                WHERE video_id = :video_id
                ORDER BY asset_type, version DESC
                LIMIT 100
            """)
            params = {"video_id": video_id}

        result = await db.execute(history_sql, params)
        history = []
        for r in result.fetchall():
            history.append({
                "id": r.id,
                "asset_type": r.asset_type,
                "filename": r.original_filename,
                "file_size": r.file_size,
                "uploaded_at": str(r.uploaded_at) if r.uploaded_at else None,
                "uploaded_by": r.uploaded_by,
                "is_active": bool(r.is_active),
                "version": r.version,
                "validation_status": r.validation_status,
                "validation_result": json.loads(r.validation_result) if r.validation_result else None,
            })

        return {
            "video_id": video_id,
            "asset_type": asset_type,
            "history": history,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[asset-history] Unexpected error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))



# =========================================================
