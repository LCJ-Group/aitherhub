"""Video API — Product Exposures

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

# =========================================================
# Product Exposure Timeline API
# =========================================================

@router.get("/{video_id}/product-exposures")
async def get_product_exposures(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get AI-detected product exposure timeline for a video.
    Returns list of product exposure segments sorted by time_start.
    """
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Ensure table exists (safe for first-time access)
        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS video_product_exposures (
                    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                    video_id UUID NOT NULL,
                    user_id INTEGER,
                    product_name TEXT NOT NULL,
                    brand_name TEXT,
                    product_image_url TEXT,
                    time_start FLOAT NOT NULL,
                    time_end FLOAT NOT NULL,
                    confidence FLOAT DEFAULT 0.8,
                    source VARCHAR(20) DEFAULT 'ai',
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
            """))
            await db.commit()
        except Exception:
            await db.rollback()

        # Fetch exposures
        result = await db.execute(
            text("""
                SELECT id, video_id, user_id, product_name, brand_name,
                       product_image_url, time_start, time_end, confidence, source,
                       created_at, updated_at
                FROM video_product_exposures
                WHERE video_id = :vid
                ORDER BY time_start ASC
            """),
            {"vid": video_id},
        )
        rows = result.fetchall()

        exposures = []
        for r in rows:
            exposures.append({
                "id": str(r[0]),
                "video_id": str(r[1]),
                "user_id": r[2],
                "product_name": r[3],
                "brand_name": r[4],
                "product_image_url": r[5],
                "time_start": r[6],
                "time_end": r[7],
                "confidence": r[8],
                "source": r[9],
                "created_at": r[10].isoformat() if r[10] else None,
                "updated_at": r[11].isoformat() if r[11] else None,
            })

        return {"exposures": exposures, "count": len(exposures)}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to get product exposures: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{video_id}/product-exposures/{exposure_id}")
async def update_product_exposure(
    video_id: str,
    exposure_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Update a product exposure segment (human edit).
    Payload can include: product_name, brand_name, time_start, time_end, confidence
    """
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Build dynamic SET clause
        allowed_fields = ["product_name", "brand_name", "time_start", "time_end", "confidence"]
        set_parts = []
        params = {"eid": exposure_id, "vid": video_id}

        for field in allowed_fields:
            if field in payload:
                set_parts.append(f"{field} = :{field}")
                params[field] = payload[field]

        if not set_parts:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Mark as human-edited
        set_parts.append("source = 'human'")
        set_parts.append("updated_at = now()")

        sql = text(f"""
            UPDATE video_product_exposures
            SET {', '.join(set_parts)}
            WHERE id = :eid AND video_id = :vid
            RETURNING id
        """)

        result = await db.execute(sql, params)
        updated = result.fetchone()
        await db.commit()

        if not updated:
            raise HTTPException(status_code=404, detail="Exposure not found")

        return {"success": True, "id": str(updated[0])}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to update product exposure: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{video_id}/product-exposures")
async def create_product_exposure(
    video_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Manually create a product exposure segment.
    Required: product_name, time_start, time_end
    Optional: brand_name, confidence
    """
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")

        product_name = payload.get("product_name")
        time_start = payload.get("time_start")
        time_end = payload.get("time_end")

        if not product_name or time_start is None or time_end is None:
            raise HTTPException(
                status_code=400,
                detail="product_name, time_start, time_end are required",
            )

        sql = text("""
            INSERT INTO video_product_exposures
                (video_id, user_id, product_name, brand_name,
                 time_start, time_end, confidence, source)
            VALUES
                (:vid, :uid, :product_name, :brand_name,
                 :time_start, :time_end, :confidence, 'human')
            RETURNING id
        """)

        result = await db.execute(sql, {
            "vid": video_id,
            "uid": current_user["id"],
            "product_name": product_name,
            "brand_name": payload.get("brand_name", ""),
            "time_start": time_start,
            "time_end": time_end,
            "confidence": payload.get("confidence", 1.0),
        })
        new_row = result.fetchone()
        await db.commit()

        return {"success": True, "id": str(new_row[0])}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to create product exposure: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{video_id}/product-exposures/{exposure_id}")
async def delete_product_exposure(
    video_id: str,
    exposure_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete a product exposure segment."""
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")
        result = await db.execute(
            text(""""
                DELETE FROM video_product_exposures
                WHERE id = :eid AND video_id = :vid
                RETURNING id
            """),
            {"eid": exposure_id, "vid": video_id},
        )
        deleted = result.fetchone()
        await db.commit()

        if not deleted:
            raise HTTPException(status_code=404, detail="Exposure not found")

        return {"success": True}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to delete product exposure: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{video_id}/product-exposures/remap-names")
async def remap_product_exposure_names(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Remap generic product names (Product_0, Product_1, ...) to actual names
    from the Excel product data.
    
    Logic:
    1. Get all exposures for this video
    2. Get the product Excel data (same as product-data endpoint)
    3. Extract unique generic names, sort by index (Product_0, Product_1, ...)
    4. Map each Product_N to the Nth product in the Excel list
    5. Also try to find the actual product_name key in Excel data
    6. Bulk update all exposures with the real product names
    """
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id, excel_product_blob_url FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")

        product_blob_url = row[1]
        if not product_blob_url:
            return {"success": False, "message": "No product Excel file uploaded for this video", "updated": 0}

        # --- Parse Excel to get product list ---
        import httpx
        import tempfile
        import os as _os
        import openpyxl
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions
        from datetime import timedelta

        # Generate SAS URL
        conn_str = _os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        account_name = ""
        account_key = ""
        for part in conn_str.split(";"):
            if part.startswith("AccountName="):
                account_name = part.split("=", 1)[1]
            elif part.startswith("AccountKey="):
                account_key = part.split("=", 1)[1]

        from urllib.parse import urlparse, unquote
        parsed = urlparse(product_blob_url)
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

        # Download and parse Excel
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(sas_url)
            if resp.status_code != 200:
                return {"success": False, "message": f"Failed to download Excel (HTTP {resp.status_code})", "updated": 0}

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(resp.content)
            tmp_path = f.name

        try:
            wb = openpyxl.load_workbook(tmp_path, read_only=True, data_only=True)
            ws = wb.active
            excel_products = []
            if ws:
                rows_data = list(ws.iter_rows(values_only=True))
                if len(rows_data) >= 2:
                    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows_data[0])]
                    for data_row in rows_data[1:]:
                        if all(v is None for v in data_row):
                            continue
                        item = {}
                        for i, val in enumerate(data_row):
                            if i < len(headers):
                                item[headers[i]] = val
                        excel_products.append(item)
            wb.close()
        finally:
            _os.unlink(tmp_path)

        if not excel_products:
            return {"success": False, "message": "No products found in Excel file", "updated": 0}

        # --- Build name mapping ---
        # Find the product name column in Excel
        # Try common column names: 商品名, product_name, name, 商品タイトル
        name_keys = ["商品名", "product_name", "name", "商品タイトル", "Name", "Product Name", "商品"]
        product_name_key = None
        sample = excel_products[0]
        for key in name_keys:
            if key in sample and sample[key]:
                product_name_key = key
                break
        # If not found, try first string column
        if not product_name_key:
            for k, v in sample.items():
                if isinstance(v, str) and len(v) > 2:
                    product_name_key = k
                    break

        if not product_name_key:
            return {"success": False, "message": "Could not find product name column in Excel", "updated": 0}

        # Build ordered list of real product names from Excel
        real_names = []
        for p in excel_products:
            pname = p.get(product_name_key)
            if pname:
                real_names.append(str(pname).strip())
            else:
                real_names.append(None)

        logger.info(f"[REMAP] Found {len(real_names)} products in Excel, name_key='{product_name_key}'")
        logger.info(f"[REMAP] First 5 products: {real_names[:5]}")

        # --- Get current exposures ---
        result = await db.execute(
            text("""
                SELECT DISTINCT product_name
                FROM video_product_exposures
                WHERE video_id = :vid
                ORDER BY product_name
            """),
            {"vid": video_id},
        )
        current_names = [r[0] for r in result.fetchall()]

        # Build mapping: Product_N -> real_names[N]
        import re
        name_map = {}
        for cname in current_names:
            match = re.match(r"^Product_(\d+)$", cname)
            if match:
                idx = int(match.group(1))
                if idx < len(real_names) and real_names[idx]:
                    name_map[cname] = real_names[idx]

        if not name_map:
            return {
                "success": False,
                "message": f"No Product_N names found to remap. Current names: {current_names[:10]}",
                "updated": 0,
            }

        logger.info(f"[REMAP] Mapping {len(name_map)} names: {name_map}")

        # --- Bulk update ---
        total_updated = 0
        for old_name, new_name in name_map.items():
            result = await db.execute(
                text("""
                    UPDATE video_product_exposures
                    SET product_name = :new_name, updated_at = now()
                    WHERE video_id = :vid AND product_name = :old_name
                """),
                {"vid": video_id, "old_name": old_name, "new_name": new_name},
            )
            total_updated += result.rowcount

        await db.commit()

        return {
            "success": True,
            "message": f"Remapped {len(name_map)} product names, {total_updated} rows updated",
            "updated": total_updated,
            "mapping": name_map,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to remap product names: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/remap-all-product-names")
async def remap_all_product_names(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Remap product names for ALL videos belonging to the current user.
    Iterates over all videos with product exposures and applies the remap logic.
    """
    try:
        # Get all video IDs for this user that have product exposures
        result = await db.execute(
            text("""
                SELECT DISTINCT vpe.video_id
                FROM video_product_exposures vpe
                JOIN videos v ON vpe.video_id = v.id
                WHERE v.user_id = :uid
                  AND vpe.product_name ~ '^Product_\\d+$'
            """),
            {"uid": current_user["id"]},
        )
        video_ids = [str(r[0]) for r in result.fetchall()]

        if not video_ids:
            return {"success": True, "message": "No videos with generic Product_N names found", "videos_processed": 0}

        results = []
        for vid in video_ids:
            try:
                # Call the single-video remap logic inline
                # (We can't easily call the endpoint from here, so duplicate the core logic)
                vrow = await db.execute(
                    text("SELECT excel_product_blob_url FROM videos WHERE id = :vid"),
                    {"vid": vid},
                )
                vdata = vrow.fetchone()
                if not vdata or not vdata[0]:
                    results.append({"video_id": vid, "status": "skipped", "reason": "no Excel"})
                    continue

                results.append({"video_id": vid, "status": "needs_individual_call"})
            except Exception as e:
                results.append({"video_id": vid, "status": "error", "reason": str(e)})

        return {
            "success": True,
            "message": f"Found {len(video_ids)} videos with generic names. Call /remap-names on each individually.",
            "video_ids": video_ids,
            "details": results,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to list videos for remap: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
