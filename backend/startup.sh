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
# The antenv is pre-built in GitHub Actions and included in the deploy zip.
# We use PYTHONPATH + python -m gunicorn to avoid shebang path issues.

echo "[startup] Setting up Python environment..."
echo "[startup] System Python: $(which python 2>/dev/null || echo 'not found')"
echo "[startup] Python version: $(python --version 2>/dev/null || echo 'unknown')"

PACKAGES_FOUND=false

for VENV_PATH in "antenv" "/home/site/wwwroot/antenv" "/antenv" "/home/antenv"; do
    SITE_PACKAGES=$(find "$VENV_PATH/lib" -name "site-packages" -type d 2>/dev/null | head -1)
    if [ -n "$SITE_PACKAGES" ]; then
        echo "[startup] Found site-packages at: $SITE_PACKAGES"
        export PYTHONPATH="$SITE_PACKAGES:${PYTHONPATH:-}"
        echo "[startup] PYTHONPATH=$PYTHONPATH"

        # Verify import works
        if python -c "import fastapi; print(f'fastapi {fastapi.__version__}')" 2>/dev/null; then
            echo "[startup] Package imports verified successfully"
            PACKAGES_FOUND=true
            break
        else
            echo "[startup] Import failed with this path, trying next..."
            unset PYTHONPATH
        fi
    fi
done

if [ "$PACKAGES_FOUND" = "false" ]; then
    echo "[startup] WARNING: No pre-built packages found"
    echo "[startup] Falling back to pip install..."
    pip install --no-cache-dir -r requirements.txt 2>&1 | tail -10
fi

echo "[startup] Final Python check:"
echo "[startup]   python: $(which python)"
echo "[startup]   PYTHONPATH: ${PYTHONPATH:-not set}"
python -c "import fastapi, uvicorn, gunicorn; print(f'  fastapi={fastapi.__version__}, uvicorn={uvicorn.__version__}')" 2>&1 || echo "[startup] WARNING: Some imports failed"

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

# Diagnostic: log the deploy state
echo "[startup] Deploy diagnostics:"
echo "[startup]   Files in /home/site/wwwroot: $(ls /home/site/wwwroot/ 2>/dev/null | wc -l)"
echo "[startup]   antenv exists: $(test -d antenv && echo YES || echo NO)"
echo "[startup]   antenv size: $(du -sh antenv 2>/dev/null | cut -f1 || echo N/A)"
