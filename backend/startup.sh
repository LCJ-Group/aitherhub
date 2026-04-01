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
# Dependencies are installed as flat packages in _pypackages/ (pip --target).
# This avoids Kudu's PythonProjectOptimizer which crashes on "antenv" venv dirs.
# We use PYTHONPATH to make them importable.

echo "[startup] Setting up Python environment..."

PACKAGES_FOUND=false

# Check _pypackages first (new flat install approach)
for PKG_PATH in "_pypackages" "/home/site/wwwroot/_pypackages"; do
    if [ -d "$PKG_PATH" ] && [ -f "$PKG_PATH/fastapi/__init__.py" ]; then
        echo "[startup] Found _pypackages at: $PKG_PATH"
        export PYTHONPATH="$PKG_PATH:${PYTHONPATH:-}"
        echo "[startup] PYTHONPATH=$PYTHONPATH"

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

# Fallback: check antenv (legacy venv approach)
if [ "$PACKAGES_FOUND" = "false" ]; then
    for VENV_PATH in "antenv" "/home/site/wwwroot/antenv"; do
        SITE_PACKAGES=$(find "$VENV_PATH/lib" -name "site-packages" -type d 2>/dev/null | head -1)
        if [ -n "$SITE_PACKAGES" ]; then
            echo "[startup] Found legacy antenv at: $SITE_PACKAGES"
            export PYTHONPATH="$SITE_PACKAGES:${PYTHONPATH:-}"

            if python -c "import fastapi; print(f'fastapi {fastapi.__version__}')" 2>/dev/null; then
                echo "[startup] Package imports verified successfully (legacy antenv)"
                PACKAGES_FOUND=true
                break
            else
                unset PYTHONPATH
            fi
        fi
    done
fi

# Last resort: pip install
if [ "$PACKAGES_FOUND" = "false" ]; then
    echo "[startup] WARNING: No pre-built packages found"
    echo "[startup] Falling back to pip install (this may take several minutes)..."
    pip install --no-cache-dir -r requirements.txt 2>&1 | tail -10
    pip install --no-cache-dir gunicorn 2>&1 | tail -3
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
