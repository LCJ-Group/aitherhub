#!/bin/bash

# Force English locale for consistent ffmpeg output
export LANG=C
export LC_ALL=C

# ── Install ffmpeg & CJK fonts in BACKGROUND ──
# Azure startup probe has a 600s limit. ffmpeg+fonts install takes ~10 min,
# so we run them in the background and start gunicorn immediately.
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

    echo "[startup-bg] ffmpeg drawtext: $(ffmpeg -hide_banner -filters 2>&1 | grep drawtext || echo 'NOT AVAILABLE')"
    echo "[startup-bg] CJK font: $(fc-list 2>/dev/null | grep -i 'noto.*cjk' | head -1 || echo 'NONE')"
    echo "[startup-bg] Background dependency installation complete"
}

# Start background installation (won't block gunicorn startup)
install_deps_background &

# ── Activate virtual environment ──
# Oryx build creates antenv in various locations depending on config.
# Check all known locations.
VENV_ACTIVATED=false

for VENV_PATH in "antenv" "/antenv" "/home/site/wwwroot/antenv" "/home/antenv"; do
    if [ -d "$VENV_PATH" ] && [ -f "$VENV_PATH/bin/activate" ]; then
        echo "[startup] Found venv at $VENV_PATH, activating..."
        source "$VENV_PATH/bin/activate"
        echo "[startup] Python: $(which python) — $(python --version)"
        VENV_ACTIVATED=true
        break
    fi
done

if [ "$VENV_ACTIVATED" = "false" ]; then
    echo "[startup] WARNING: No virtual environment found in any known location"
    echo "[startup] Checked: antenv, /antenv, /home/site/wwwroot/antenv, /home/antenv"
    echo "[startup] Installing requirements with system pip..."
    pip install --no-cache-dir -r requirements.txt 2>&1 | tail -10
fi

echo "[startup] Starting gunicorn..."
gunicorn -k uvicorn.workers.UvicornWorker app.main:app --workers 2 --threads 1 --timeout 600 --graceful-timeout 30 --bind 0.0.0.0:8000 --access-logfile - --error-logfile - --keep-alive 120
