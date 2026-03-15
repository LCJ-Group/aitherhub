#!/bin/bash

# Install ffmpeg for audio extraction (needed for Whisper transcription)
if ! command -v ffmpeg &> /dev/null; then
    echo "[startup] Installing ffmpeg..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg libass-dev fonts-noto-cjk 2>&1 | tail -3
    echo "[startup] ffmpeg installed: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "[startup] ffmpeg already available: $(ffmpeg -version 2>&1 | head -1)"
fi

# Activate the virtual environment created during deployment
if [ -d "antenv" ]; then
    echo "[startup] Activating antenv virtual environment..."
    source antenv/bin/activate
    echo "[startup] Python: $(which python) — $(python --version)"
else
    echo "[startup] WARNING: antenv not found, using system Python"
    # Fallback: install requirements directly
    pip install -r requirements.txt 2>&1 | tail -5
fi

gunicorn -k uvicorn.workers.UvicornWorker app.main:app --workers 1 --threads 1 --timeout 300 --bind 0.0.0.0:8000 --access-logfile - --error-logfile -
