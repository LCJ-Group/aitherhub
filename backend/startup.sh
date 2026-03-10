#!/bin/bash

# Install ffmpeg for audio extraction (needed for Whisper transcription)
if ! command -v ffmpeg &> /dev/null; then
    echo "[startup] Installing ffmpeg..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg 2>&1 | tail -3
    echo "[startup] ffmpeg installed: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "[startup] ffmpeg already available: $(ffmpeg -version 2>&1 | head -1)"
fi

gunicorn -k uvicorn.workers.UvicornWorker app.main:app --workers 1 --threads 1 --timeout 120 --bind 0.0.0.0:8000 --access-logfile - --error-logfile -
