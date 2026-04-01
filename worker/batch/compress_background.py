#!/usr/bin/env python3
"""
Background compression script.
Runs as a separate subprocess to compress video to 1080p without blocking
the main analysis pipeline.

Compression result is saved as a SEPARATE preview file (not overwriting original)
so that:
  - Preview playback uses the lightweight compressed version
  - Clip generation uses the original high-quality video

Blob naming convention:
  Original:  email/video_id/video_id.mp4
  Preview:   email/video_id/video_id_preview.mp4
"""

import argparse
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [COMPRESS_BG] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Ensure the worker/batch directory is on sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from video_compressor import (
    compress_to_1080p,
    needs_compression,
    parse_blob_url,
    upload_compressed_to_blob,
)


def update_compressed_blob_url(video_id: str, compressed_blob_url: str):
    """Update the compressed_blob_url column in the videos table."""
    try:
        from db_ops import AsyncSessionLocal, get_event_loop
        from sqlalchemy import text

        async def _update():
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text(
                        "UPDATE videos SET compressed_blob_url = :url WHERE id = :vid"
                    ),
                    {"url": compressed_blob_url, "vid": video_id},
                )
                await session.commit()

        loop = get_event_loop()
        loop.run_until_complete(_update())
        logger.info("DB updated: compressed_blob_url set for video %s", video_id)
    except Exception as e:
        logger.error("Failed to update DB with compressed_blob_url: %s", e)


def _record_compress_error(video_id: str, error_code: str, error_message: str):
    """Best-effort: record compression error to video_error_logs."""
    try:
        from db_ops import insert_video_error_log_sync
        insert_video_error_log_sync(
            video_id=video_id,
            error_code=error_code,
            error_step="BACKGROUND_COMPRESSION",
            error_message=error_message[:2000],
            error_detail=None,
            source="compress_bg",
            update_last_error=False,  # Non-fatal: don't overwrite last_error_code
        )
    except Exception as e:
        logger.warning("Failed to record compress error log: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Background video compression")
    parser.add_argument("--video-path", required=True, help="Local path to video file")
    parser.add_argument("--video-id", required=True, help="Video ID in database")
    parser.add_argument("--blob-url", default="", help="Original blob URL")
    args = parser.parse_args()

    video_path = args.video_path
    video_id = args.video_id
    blob_url = args.blob_url

    # Initialize DB connection for error logging
    try:
        from db_ops import init_db_sync
        init_db_sync()
    except Exception as e:
        logger.warning("Failed to init DB for compress_bg: %s", e)

    logger.info("Starting background compression for video: %s", video_id)
    logger.info("Video path: %s", video_path)

    if not os.path.exists(video_path):
        logger.error("Video file not found: %s", video_path)
        sys.exit(1)

    file_size = os.path.getsize(video_path)
    if file_size == 0:
        logger.error("Video file is 0 bytes, skipping compression: %s", video_path)
        return

    logger.info("Video file size: %.2f GB", file_size / (1024 ** 3))

    if not needs_compression(video_path):
        logger.info("Compression not needed (already <= 1080p and < 2GB)")
        return

    # Compress to 1080p, saving as _preview.mp4
    base, ext = os.path.splitext(video_path)
    preview_local_path = f"{base}_preview{ext}"
    compressed_path = compress_to_1080p(
        video_path, output_path=preview_local_path, crf=23, preset="veryfast"
    )

    if compressed_path is None:
        logger.error("Compression failed")
        _record_compress_error(video_id, "COMPRESS_FAILED", "compress_to_1080p returned None")
        sys.exit(1)

    logger.info("Compression complete: %s", compressed_path)

    # Upload preview to Blob Storage as a SEPARATE blob (not overwriting original)
    if blob_url:
        blob_info = parse_blob_url(blob_url)
        original_blob_path = blob_info.get("blob_path", "")

        if original_blob_path:
            # Original: email/video_id/video_id.mp4
            # Preview:  email/video_id/video_id_preview.mp4
            base_blob, ext_blob = os.path.splitext(original_blob_path)
            preview_blob_path = f"{base_blob}_preview{ext_blob}"

            logger.info("Uploading preview to blob: %s", preview_blob_path)
            upload_ok = upload_compressed_to_blob(compressed_path, preview_blob_path)

            if upload_ok:
                logger.info("Preview uploaded successfully to: %s", preview_blob_path)
                # Update DB with the preview blob URL
                update_compressed_blob_url(video_id, preview_blob_path)
            else:
                logger.error("Failed to upload preview to blob")
    else:
        logger.warning("No blob_url provided, skipping blob upload")

    # Clean up local preview file (original is kept for clip generation)
    try:
        if os.path.exists(compressed_path):
            os.remove(compressed_path)
            logger.info("Cleaned up local preview file: %s", compressed_path)
    except Exception as e:
        logger.warning("Failed to clean up local preview file: %s", e)

    logger.info("Background compression finished for video: %s", video_id)


if __name__ == "__main__":
    main()
