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

    echo "[startup-bg] Background dependency installation complete"
}

# Start background installation (won't block gunicorn startup)
install_deps_background &

# ── Activate virtual environment ──
# The antenv is pre-built in GitHub Actions and included in the deploy zip.
# pyvenv.cfg may point to wrong Python path, so we use PYTHONPATH as fallback.
VENV_ACTIVATED=false

for VENV_PATH in "antenv" "/home/site/wwwroot/antenv" "/antenv" "/home/antenv"; do
    if [ -d "$VENV_PATH" ] && [ -d "$VENV_PATH/lib" ]; then
        echo "[startup] Found antenv at $VENV_PATH"

        # Try standard activation first
        if [ -f "$VENV_PATH/bin/activate" ]; then
            source "$VENV_PATH/bin/activate" 2>/dev/null
        fi

        # Verify Python works after activation
        if python -c "import fastapi" 2>/dev/null; then
            echo "[startup] venv activation successful, fastapi importable"
            VENV_ACTIVATED=true
            break
        fi

        # Fallback: set PYTHONPATH and PATH directly
        # This works even if pyvenv.cfg points to wrong Python path
        echo "[startup] venv activate failed, using PYTHONPATH fallback"
        SITE_PACKAGES=$(find "$VENV_PATH/lib" -name "site-packages" -type d 2>/dev/null | head -1)
        if [ -n "$SITE_PACKAGES" ]; then
            export PYTHONPATH="$SITE_PACKAGES:${PYTHONPATH:-}"
            export PATH="$VENV_PATH/bin:$PATH"
            echo "[startup] PYTHONPATH=$PYTHONPATH"
            echo "[startup] PATH includes $VENV_PATH/bin"

            # Verify import works now
            if python -c "import fastapi" 2>/dev/null; then
                echo "[startup] PYTHONPATH fallback successful, fastapi importable"
                VENV_ACTIVATED=true
                break
            else
                echo "[startup] PYTHONPATH fallback also failed"
            fi
        fi
    fi
done

if [ "$VENV_ACTIVATED" = "false" ]; then
    echo "[startup] WARNING: No working virtual environment found"
    echo "[startup] Installing requirements with system pip..."
    pip install --no-cache-dir -r requirements.txt 2>&1 | tail -10
fi

echo "[startup] Python: $(which python 2>/dev/null || echo 'not found')"
echo "[startup] Python version: $(python --version 2>/dev/null || echo 'unknown')"
echo "[startup] gunicorn: $(which gunicorn 2>/dev/null || echo 'not found')"

echo "[startup] Starting gunicorn..."
gunicorn -k uvicorn.workers.UvicornWorker app.main:app --workers 2 --threads 1 --timeout 600 --graceful-timeout 30 --bind 0.0.0.0:8000 --access-logfile - --error-logfile - --keep-alive 120
