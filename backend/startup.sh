#!/bin/bash

# Install ffmpeg and subtitle dependencies
if ! command -v ffmpeg &> /dev/null; then
    echo "[startup] Installing ffmpeg and subtitle deps..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        ffmpeg fonts-noto-cjk libass-dev 2>&1 | tail -5
    echo "[startup] ffmpeg installed: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "[startup] ffmpeg already available: $(ffmpeg -version 2>&1 | head -1)"
    # Ensure fonts and libass are installed for subtitle rendering
    if ! fc-list 2>/dev/null | grep -qi "noto.*cjk"; then
        echo "[startup] Installing CJK fonts..."
        apt-get update -qq && apt-get install -y -qq --no-install-recommends \
            fonts-noto-cjk 2>&1 | tail -3
    fi
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

gunicorn -k uvicorn.workers.UvicornWorker app.main:app --workers 1 --threads 1 --timeout 120 --bind 0.0.0.0:8000 --access-logfile - --error-logfile -
