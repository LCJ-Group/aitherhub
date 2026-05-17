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

# ── Check ffmpeg availability ──
if command -v ffmpeg &> /dev/null; then
    echo "[startup] ffmpeg available: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "[startup] WARNING: ffmpeg not found. Export features will not work."
    echo "[startup] Installing ffmpeg synchronously (one-time)..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg libass9 libass-dev fonts-noto-cjk fontconfig 2>&1 | tail -5
    fc-cache -fv 2>/dev/null
    echo "[startup] ffmpeg installed: $(ffmpeg -version 2>&1 | head -1)"
fi

# ── Ensure CJK fonts are available (critical for subtitle rendering) ──
CJK_FONT_FOUND=false
for FONT_PATH in /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc /usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc /usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc; do
    if [ -f "$FONT_PATH" ]; then
        CJK_FONT_FOUND=true
        echo "[startup] CJK font found: $FONT_PATH"
        break
    fi
done
if [ "$CJK_FONT_FOUND" = "false" ]; then
    echo "[startup] WARNING: CJK fonts not found. Installing fonts-noto-cjk..."
    apt-get update -qq 2>/dev/null
    apt-get install -y -qq --no-install-recommends fonts-noto-cjk fontconfig libass9 2>&1 | tail -5
    fc-cache -fv 2>/dev/null
    echo "[startup] CJK fonts installed."
    # Verify
    for FONT_PATH in /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc /usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc; do
        if [ -f "$FONT_PATH" ]; then
            echo "[startup] Verified CJK font: $FONT_PATH"
            break
        fi
    done
fi

# ── Verify libass can find fonts ──
if command -v fc-list &> /dev/null; then
    CJK_COUNT=$(fc-list | grep -ci 'noto.*cjk' || echo "0")
    echo "[startup] fontconfig CJK fonts registered: $CJK_COUNT"
fi

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
    --graceful-timeout 120 \
    --preload \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --bind 0.0.0.0:8000 \
    --access-logfile - \
    --error-logfile - \
    --keep-alive 120
