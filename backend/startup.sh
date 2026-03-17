#!/bin/bash

# Force English locale for consistent ffmpeg output
export LANG=C
export LC_ALL=C

# Install ffmpeg (required for subtitle export)
if ! command -v ffmpeg &> /dev/null; then
    echo "[startup] Installing ffmpeg..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg libass-dev 2>&1 | tail -3
    echo "[startup] ffmpeg installed: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "[startup] ffmpeg already available: $(ffmpeg -version 2>&1 | head -1)"
fi

# Install CJK fonts SYNCHRONOUSLY (required before subtitle export can work)
if ! fc-list 2>/dev/null | grep -qi "noto.*cjk"; then
    echo "[startup] Installing CJK fonts..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends fonts-noto-cjk 2>&1 | tail -3
    fc-cache -f 2>/dev/null
    echo "[startup] CJK fonts installed"
else
    echo "[startup] CJK fonts already available"
fi

# Log ffmpeg drawtext availability for diagnostics
echo "[startup] ffmpeg drawtext: $(ffmpeg -hide_banner -filters 2>&1 | grep drawtext || echo 'NOT AVAILABLE')"
echo "[startup] CJK font: $(fc-list 2>/dev/null | grep -i 'noto.*cjk' | head -1 || echo 'NONE')"

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

gunicorn -k uvicorn.workers.UvicornWorker app.main:app --workers 1 --threads 1 --timeout 600 --graceful-timeout 30 --bind 0.0.0.0:8000 --access-logfile - --error-logfile -
