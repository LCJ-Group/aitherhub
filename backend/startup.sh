#!/bin/bash

# Force English locale for consistent ffmpeg output
export LANG=C
export LC_ALL=C

echo "[startup] ========================================"
echo "[startup] AitherHub API startup"
echo "[startup] ========================================"
echo "[startup] Python: $(python --version 2>&1)"
echo "[startup] Working dir: $(pwd)"
echo "[startup] Date: $(date -u)"

# ── Install ffmpeg & CJK fonts in BACKGROUND ──
install_deps_background() {
    if ! command -v ffmpeg &> /dev/null; then
        echo "[startup-bg] Installing ffmpeg..."
        apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg libass-dev 2>&1 | tail -3
        echo "[startup-bg] ffmpeg installed: $(ffmpeg -version 2>&1 | head -1)"
    else
        echo "[startup-bg] ffmpeg already available: $(ffmpeg -version 2>&1 | head -1)"
    fi

    if ! fc-list 2>/dev/null | grep -qi "noto.*cjk"; then
        echo "[startup-bg] Installing CJK fonts..."
        apt-get install -y -qq --no-install-recommends fonts-noto-cjk 2>&1 | tail -3
        fc-cache -f 2>/dev/null
        echo "[startup-bg] CJK fonts installed"
    else
        echo "[startup-bg] CJK fonts already available"
    fi

    echo "[startup-bg] Background dependency installation complete"
}

# Start background installation (won't block gunicorn startup)
install_deps_background &

# ── Set up Python environment ──
# Strategy: Try to use existing antenv, otherwise pip install into antenv
ANTENV_FOUND=false

for VENV_PATH in "antenv" "/home/site/wwwroot/antenv"; do
    if [ -d "$VENV_PATH/lib" ]; then
        SITE_PACKAGES=$(find "$VENV_PATH/lib" -name "site-packages" -type d 2>/dev/null | head -1)
        if [ -n "$SITE_PACKAGES" ] && python -c "import sys; sys.path.insert(0,'$SITE_PACKAGES'); import fastapi" 2>/dev/null; then
            echo "[startup] Found working antenv at $VENV_PATH"
            export PYTHONPATH="$SITE_PACKAGES:${PYTHONPATH:-}"
            ANTENV_FOUND=true
            break
        fi
    fi
done

if [ "$ANTENV_FOUND" = "false" ]; then
    echo "[startup] No working antenv found. Running pip install..."
    echo "[startup] This will take a few minutes on first deploy..."

    # Install into a venv so packages are isolated
    if [ ! -d "antenv" ]; then
        python -m venv antenv
    fi

    # Activate and install
    source antenv/bin/activate
    pip install --no-cache-dir -r requirements.txt 2>&1 | tail -20
    pip install --no-cache-dir gunicorn 2>&1 | tail -5

    echo "[startup] pip install complete"

    # Use the venv's gunicorn directly
    echo "[startup] Starting gunicorn from antenv..."
    exec gunicorn -k uvicorn.workers.UvicornWorker app.main:app \
        --workers 2 \
        --threads 1 \
        --timeout 600 \
        --graceful-timeout 30 \
        --bind 0.0.0.0:8000 \
        --access-logfile - \
        --error-logfile - \
        --keep-alive 120
fi

# If antenv was found, use python -m gunicorn with PYTHONPATH
echo "[startup] PYTHONPATH=$PYTHONPATH"
echo "[startup] Verifying imports..."
python -c "import fastapi, uvicorn, gunicorn; print(f'[startup] fastapi={fastapi.__version__}, uvicorn={uvicorn.__version__}')" 2>&1

echo "[startup] Starting gunicorn via python -m..."
exec python -m gunicorn -k uvicorn.workers.UvicornWorker app.main:app \
    --workers 2 \
    --threads 1 \
    --timeout 600 \
    --graceful-timeout 30 \
    --bind 0.0.0.0:8000 \
    --access-logfile - \
    --error-logfile - \
    --keep-alive 120
