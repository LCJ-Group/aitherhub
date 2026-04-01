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
echo "[startup] Setting up Python environment..."

# Get the actual Python version
PY_VER=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "3.10")
echo "[startup] Runtime Python version: $PY_VER"

PACKAGES_FOUND=false

# Strategy 1: Check _pypackages (flat install - no Kudu interference)
for PKG_PATH in "_pypackages" "/home/site/wwwroot/_pypackages"; do
    if [ -d "$PKG_PATH" ] && [ -f "$PKG_PATH/fastapi/__init__.py" ]; then
        echo "[startup] Found _pypackages at: $PKG_PATH"
        export PYTHONPATH="$PKG_PATH:${PYTHONPATH:-}"
        if python -c "import fastapi; print(f'fastapi {fastapi.__version__}')" 2>/dev/null; then
            echo "[startup] _pypackages verified successfully"
            PACKAGES_FOUND=true
            break
        fi
        echo "[startup] _pypackages import failed, trying next..."
        unset PYTHONPATH
    fi
done

# Strategy 2: Check antenv with MATCHING python version first, then any version
if [ "$PACKAGES_FOUND" = "false" ]; then
    for VENV_PATH in "antenv" "/home/site/wwwroot/antenv"; do
        # First try matching version
        SP="$VENV_PATH/lib/python$PY_VER/site-packages"
        if [ -d "$SP" ] && [ -f "$SP/fastapi/__init__.py" ]; then
            echo "[startup] Found matching antenv: $SP"
            export PYTHONPATH="$SP:${PYTHONPATH:-}"
            if python -c "import fastapi; print(f'fastapi {fastapi.__version__}')" 2>/dev/null; then
                echo "[startup] antenv packages verified (matching version)"
                PACKAGES_FOUND=true
                break
            fi
            unset PYTHONPATH
        fi
        # Then try any version (cross-version compatibility)
        for PY_DIR in "$VENV_PATH"/lib/python3.*; do
            SP="$PY_DIR/site-packages"
            if [ -d "$SP" ] && [ -f "$SP/fastapi/__init__.py" ]; then
                echo "[startup] Found cross-version antenv: $SP"
                export PYTHONPATH="$SP:${PYTHONPATH:-}"
                if python -c "import fastapi; print(f'fastapi {fastapi.__version__}')" 2>/dev/null; then
                    echo "[startup] antenv packages verified (cross-version)"
                    PACKAGES_FOUND=true
                    break 2
                fi
                echo "[startup] Cross-version import failed"
                unset PYTHONPATH
            fi
        done
    done
fi

# Strategy 3: pip install (last resort)
if [ "$PACKAGES_FOUND" = "false" ]; then
    echo "[startup] WARNING: No pre-built packages found"
    echo "[startup] Running pip install (this may take 10-15 minutes)..."
    echo "[startup] Container timeout is 1800s, so this should complete in time."
    pip install --no-cache-dir -r requirements.txt 2>&1 | tail -20
    pip install --no-cache-dir gunicorn 2>&1 | tail -3
    echo "[startup] pip install completed"
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
