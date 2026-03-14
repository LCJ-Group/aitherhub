#!/bin/bash
set -e

echo "============================================"
echo "  FaceFusion GPU Worker — AitherHub"
echo "============================================"
echo ""

# ── GPU Check ────────────────────────────────────────────────────────────────

echo "[1/4] Checking GPU..."
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
    echo ""
else
    echo "WARNING: nvidia-smi not found. GPU may not be available."
fi

# ── v4l2loopback Setup ───────────────────────────────────────────────────────

echo "[2/4] Setting up virtual webcam (v4l2loopback)..."
if [ -e /dev/video10 ]; then
    echo "Virtual webcam /dev/video10 already exists."
else
    # Try to load v4l2loopback kernel module
    if modprobe v4l2loopback video_nr=10 card_label="FaceSwap Virtual Cam" exclusive_caps=1 2>/dev/null; then
        echo "v4l2loopback loaded: /dev/video10"
    else
        echo "WARNING: Could not load v4l2loopback. Real-time stream may not work."
        echo "  For RunPod: Use the alternative ffmpeg pipe mode instead."
        echo "  The single-frame swap (/api/swap-frame) will still work."
    fi
fi

# ── FaceFusion Model Check ───────────────────────────────────────────────────

echo "[3/4] Checking FaceFusion models..."
MODELS_DIR="${FACEFUSION_DIR}/.assets/models"
if [ -d "$MODELS_DIR" ] && [ "$(ls -A $MODELS_DIR 2>/dev/null)" ]; then
    echo "Models found in $MODELS_DIR:"
    ls -lh "$MODELS_DIR/" | head -10
else
    echo "Models not found. Downloading on first use..."
    cd "$FACEFUSION_DIR" && python3 facefusion.py force-download || true
fi
echo ""

# ── Start Worker API ─────────────────────────────────────────────────────────

echo "[4/4] Starting Worker API on port ${WORKER_PORT:-8000}..."
echo ""
echo "  API Key: ${WORKER_API_KEY:0:4}****"
echo "  Endpoints:"
echo "    GET  /api/health        - Health check"
echo "    POST /api/set-source    - Set source face"
echo "    POST /api/start-stream  - Start face swap stream"
echo "    POST /api/stop-stream   - Stop stream"
echo "    GET  /api/stream-status - Stream metrics"
echo "    POST /api/swap-frame    - Single frame test"
echo "    GET  /api/config        - View config"
echo "    POST /api/config        - Update config"
echo ""
echo "============================================"
echo ""

exec python3 /workspace/worker_api.py
