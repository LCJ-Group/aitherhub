"""
backfill_screen_moments.py  –  既存 screen_recording 動画への screen moment 一括抽出
===================================================================================

対象: upload_type='screen_recording' かつ status='DONE' の全動画
処理: 動画DL → フレーム抽出 → screen_moment_extractor → DB保存 → クリーンアップ

実行方法 (Worker VM):
  cd /var/www/aitherhub/worker/batch
  python backfill_screen_moments.py [--limit N] [--video-id UUID] [--skip-existing] [--dry-run]

コスト見積もり:
  - 1動画あたり最大30フレーム × GPT-4o Vision = ~$0.30-0.60/動画
  - 235本全件 = ~$70-140 (max_frames=30 の場合)
  - max_frames=15 にすれば半額

安全設計:
  - --dry-run で実際のAPI呼び出しなしにリストだけ確認
  - --skip-existing で既にscreen momentがある動画をスキップ
  - --limit N で処理件数を制限（テスト用）
  - 1動画ごとにクリーンアップ（ディスク圧迫防止）
  - エラーは記録して次の動画に進む（1件の失敗で全体が止まらない）
"""

import argparse
import asyncio
import glob
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("backfill_screen_moments.log", mode="a"),
    ],
)
logger = logging.getLogger("backfill_screen_moments")

# ── DB ──
from db_ops import (
    init_db_sync,
    close_db_sync,
    ensure_sales_moments_table_sync,
    bulk_insert_sales_moments_sync,
    get_event_loop,
    AsyncSessionLocal,
)
from sqlalchemy import text as sa_text

# ── Screen Moment Extractor ──
from screen_moment_extractor import detect_screen_moments

# ── Frame Extraction ──
from video_frames import extract_frames

# ── Blob Download ──
# Re-use process_video's download logic
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "videos")

# ── Paths ──
WORK_DIR = "backfill_work"
UPLOAD_DIR = "uploadedvideo"


def _generate_sas_url(blob_path: str) -> str:
    """Generate a fresh SAS URL for a blob path."""
    if not AZURE_STORAGE_CONNECTION_STRING:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING not set")

    from azure.storage.blob import generate_blob_sas, BlobSasPermissions

    # Parse account info
    account_name = None
    account_key = None
    for part in AZURE_STORAGE_CONNECTION_STRING.split(";"):
        if part.startswith("AccountName="):
            account_name = part.split("=", 1)[1]
        if part.startswith("AccountKey="):
            account_key = part.split("=", 1)[1]

    if not account_name or not account_key:
        raise RuntimeError("Cannot parse AccountName/AccountKey from connection string")

    expiry = datetime.utcnow() + timedelta(hours=24)
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=AZURE_BLOB_CONTAINER,
        blob_name=blob_path,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
    )
    return f"https://{account_name}.blob.core.windows.net/{AZURE_BLOB_CONTAINER}/{blob_path}?{sas_token}"


def _download_video(url: str, dest_path: str) -> bool:
    """Download video from blob URL. Returns True on success."""
    import requests

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    try:
        logger.info("  Downloading video...")
        resp = requests.get(url, stream=True, timeout=600)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                f.write(chunk)
        size_mb = os.path.getsize(dest_path) / (1024 * 1024)
        logger.info("  Downloaded: %.1f MB", size_mb)
        return True
    except Exception as e:
        logger.error("  Download failed: %s", e)
        return False


def _cleanup_video_files(video_id: str):
    """Remove temporary files for a video."""
    # Work directory
    work_path = os.path.join(WORK_DIR, video_id)
    if os.path.isdir(work_path):
        shutil.rmtree(work_path, ignore_errors=True)

    # Downloaded video
    video_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
    if os.path.exists(video_path):
        try:
            os.remove(video_path)
        except Exception as _e:
            logger.debug(f"Suppressed: {_e}")

    # Output frames
    output_path = os.path.join("output", video_id)
    if os.path.isdir(output_path):
        shutil.rmtree(output_path, ignore_errors=True)


async def _fetch_screen_recording_videos(limit: int = None, video_id: str = None):
    """Fetch all screen_recording videos from DB."""
    sql = """
        SELECT v.id, v.original_filename, v.upload_type, v.status,
               u.email
        FROM videos v
        JOIN users u ON u.id = v.user_id
        WHERE v.upload_type = 'screen_recording'
          AND v.status IN ('completed', 'DONE')
    """
    if video_id:
        sql += f" AND v.id::text = '{video_id}'"
    sql += " ORDER BY v.created_at DESC"
    if limit:
        sql += f" LIMIT {limit}"

    async with AsyncSessionLocal() as session:
        result = await session.execute(sa_text(sql))
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


async def _count_existing_screen_moments(video_id: str) -> int:
    """Count existing screen moments for a video."""
    sql = sa_text("""
        SELECT COUNT(*) FROM video_sales_moments
        WHERE video_id = :vid AND source = 'screen'
    """)
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(sql, {"vid": video_id})
            return result.scalar() or 0
    except Exception:
        return 0


def process_one_video(
    video_id: str,
    email: str,
    filename: str,
    dry_run: bool = False,
    max_frames: int = 30,
    sample_interval: float = 5.0,
) -> dict:
    """
    Process a single video: download → extract frames → detect moments → save.

    Returns:
        dict with keys: status, moments_count, moment_types, error
    """
    result = {
        "video_id": video_id,
        "filename": filename,
        "status": "unknown",
        "moments_count": 0,
        "moment_types": {},
        "error": None,
    }

    if dry_run:
        result["status"] = "dry_run"
        logger.info("  [DRY RUN] Would process: %s (%s)", video_id[:8], filename)
        return result

    try:
        # 1. Construct blob path and generate SAS URL
        blob_path = f"{email}/{video_id}/{video_id}.mp4"
        sas_url = _generate_sas_url(blob_path)

        # 2. Download video
        video_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        if not _download_video(sas_url, video_path):
            result["status"] = "download_failed"
            result["error"] = "Failed to download video from blob"
            return result

        # 3. Extract frames
        logger.info("  Extracting frames...")
        frames_root = os.path.join("output", video_id)
        os.makedirs(frames_root, exist_ok=True)
        frame_dir = extract_frames(
            video_path=video_path,
            fps=1,
            frames_root=frames_root,
        )
        logger.info("  Frame dir: %s", frame_dir)

        # Count frames
        frame_files = glob.glob(os.path.join(frame_dir, "*.jpg"))
        if not frame_files:
            frame_files = glob.glob(os.path.join(frame_dir, "*.png"))
        logger.info("  Total frames: %d", len(frame_files))

        if not frame_files:
            result["status"] = "no_frames"
            result["error"] = "No frames extracted"
            return result

        # 4. Detect screen moments
        logger.info("  Detecting screen moments (max_frames=%d)...", max_frames)
        moments = detect_screen_moments(
            frame_dir=frame_dir,
            keyframes=None,  # Use all frames, let extractor sample
            fps=1.0,
            sample_interval_sec=sample_interval,
            max_frames=max_frames,
        )

        if not moments:
            result["status"] = "no_moments"
            result["moments_count"] = 0
            logger.info("  No moments detected")
            return result

        # 5. Save to DB
        logger.info("  Saving %d moments to DB (source='screen')...", len(moments))
        bulk_insert_sales_moments_sync(
            video_id=video_id,
            moments=moments,
            source="screen",
        )

        # Count by type
        type_counts = {}
        for m in moments:
            t = m.get("moment_type_detail", m.get("moment_type", "unknown"))
            type_counts[t] = type_counts.get(t, 0) + 1

        result["status"] = "success"
        result["moments_count"] = len(moments)
        result["moment_types"] = type_counts
        logger.info("  SUCCESS: %d moments (%s)", len(moments), type_counts)

        return result

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        logger.error("  ERROR: %s", e)
        traceback.print_exc()
        return result

    finally:
        # 6. Cleanup
        _cleanup_video_files(video_id)


def main():
    parser = argparse.ArgumentParser(
        description="Backfill screen moments for existing screen_recording videos"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit number of videos to process (for testing)"
    )
    parser.add_argument(
        "--video-id", type=str, default=None,
        help="Process a specific video ID only"
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip videos that already have screen moments"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List target videos without processing"
    )
    parser.add_argument(
        "--max-frames", type=int, default=30,
        help="Max frames to send to Vision API per video (default: 30)"
    )
    parser.add_argument(
        "--sample-interval", type=float, default=5.0,
        help="Frame sampling interval in seconds (default: 5.0)"
    )
    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("BACKFILL SCREEN MOMENTS")
    logger.info("  limit=%s  skip_existing=%s  dry_run=%s  max_frames=%d",
                args.limit, args.skip_existing, args.dry_run, args.max_frames)
    logger.info("=" * 70)

    # Initialize DB
    init_db_sync()
    ensure_sales_moments_table_sync()

    # Fetch target videos
    loop = get_event_loop()
    videos = loop.run_until_complete(
        _fetch_screen_recording_videos(limit=args.limit, video_id=args.video_id)
    )
    logger.info("Found %d screen_recording videos (status=DONE)", len(videos))

    if not videos:
        logger.info("No videos to process. Exiting.")
        close_db_sync()
        return

    # Filter: skip existing
    if args.skip_existing:
        filtered = []
        for v in videos:
            vid = str(v["id"])
            existing = loop.run_until_complete(_count_existing_screen_moments(vid))
            if existing > 0:
                logger.info("  [SKIP] %s (%s) – already has %d screen moments",
                            vid[:8], v.get("original_filename", "?"), existing)
            else:
                filtered.append(v)
        logger.info("After skip_existing filter: %d → %d videos",
                    len(videos), len(filtered))
        videos = filtered

    # Process
    results = []
    total = len(videos)
    start_time = time.time()

    for i, v in enumerate(videos, 1):
        vid = str(v["id"])
        email = v.get("email", "unknown")
        filename = v.get("original_filename", "unknown")

        logger.info("")
        logger.info("[%d/%d] %s – %s (email=%s)", i, total, vid[:8], filename, email)

        result = process_one_video(
            video_id=vid,
            email=email,
            filename=filename,
            dry_run=args.dry_run,
            max_frames=args.max_frames,
            sample_interval=args.sample_interval,
        )
        results.append(result)

        # Progress estimate
        elapsed = time.time() - start_time
        avg_per_video = elapsed / i
        remaining = avg_per_video * (total - i)
        logger.info("  [PROGRESS] %d/%d done, elapsed=%.0fs, ETA=%.0fs (%.1f min)",
                    i, total, elapsed, remaining, remaining / 60)

    # Summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("BACKFILL COMPLETE")
    logger.info("=" * 70)

    status_counts = {}
    total_moments = 0
    type_totals = {}
    for r in results:
        s = r["status"]
        status_counts[s] = status_counts.get(s, 0) + 1
        total_moments += r["moments_count"]
        for t, c in r.get("moment_types", {}).items():
            type_totals[t] = type_totals.get(t, 0) + c

    logger.info("  Total videos: %d", total)
    for s, c in sorted(status_counts.items()):
        logger.info("    %s: %d", s, c)
    logger.info("  Total moments: %d", total_moments)
    for t, c in sorted(type_totals.items()):
        logger.info("    %s: %d", t, c)
    logger.info("  Total time: %.1f min", (time.time() - start_time) / 60)

    # Save results to JSON
    results_path = f"backfill_screen_moments_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_path, "w") as f:
        json.dump({
            "run_at": datetime.now().isoformat(),
            "args": {
                "limit": args.limit,
                "video_id": args.video_id,
                "skip_existing": args.skip_existing,
                "dry_run": args.dry_run,
                "max_frames": args.max_frames,
                "sample_interval": args.sample_interval,
            },
            "summary": {
                "total_videos": total,
                "status_counts": status_counts,
                "total_moments": total_moments,
                "moment_type_totals": type_totals,
            },
            "results": results,
        }, f, indent=2, ensure_ascii=False, default=str)
    logger.info("  Results saved to: %s", results_path)

    # Cleanup
    close_db_sync()


if __name__ == "__main__":
    main()
