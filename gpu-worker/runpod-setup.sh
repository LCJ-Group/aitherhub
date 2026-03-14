#!/bin/bash
##############################################################################
# RunPod Quick Setup Script
#
# Run this script inside a RunPod GPU Pod to set up the FaceFusion worker.
#
# Usage:
#   1. Create a RunPod GPU Pod (RTX 4090, Community Cloud)
#   2. SSH into the pod or use the web terminal
#   3. Run: bash runpod-setup.sh
#
# The script will:
#   - Install system dependencies
#   - Clone and install FaceFusion
#   - Download AI models
#   - Start the worker API server
##############################################################################

set -e

echo "============================================"
echo "  AitherHub Face Swap Worker — RunPod Setup"
echo "============================================"
echo ""

# ── Configuration ────────────────────────────────────────────────────────────

WORKER_API_KEY="${WORKER_API_KEY:-change-me-in-production}"
WORKER_PORT="${WORKER_PORT:-8000}"
WORKSPACE="/workspace"

# ── System Dependencies ──────────────────────────────────────────────────────

echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    ffmpeg \
    v4l2loopback-dkms \
    v4l2loopback-utils \
    libgl1-mesa-glx \
    libglib2.0-0 \
    git-lfs \
    > /dev/null 2>&1
echo "  Done."

# ── FaceFusion ───────────────────────────────────────────────────────────────

echo "[2/6] Installing FaceFusion..."
cd "$WORKSPACE"

if [ ! -d "facefusion" ]; then
    git clone https://github.com/facefusion/facefusion.git
fi

cd facefusion
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet onnxruntime-gpu
echo "  Done."

# ── Download Models ──────────────────────────────────────────────────────────

echo "[3/6] Downloading AI models (this may take a few minutes)..."
python3 facefusion.py force-download || echo "  Some models may download on first use."
echo "  Done."

# ── Worker API Dependencies ──────────────────────────────────────────────────

echo "[4/6] Installing worker API dependencies..."
cd "$WORKSPACE"
pip install --quiet fastapi uvicorn httpx python-multipart pydantic
echo "  Done."

# ── Copy Worker Files ────────────────────────────────────────────────────────

echo "[5/6] Setting up worker API..."
# If running from the cloned repo, files are already here
# Otherwise, download from GitHub
if [ ! -f "$WORKSPACE/worker_api.py" ]; then
    echo "  Downloading worker_api.py from GitHub..."
    curl -sL "https://raw.githubusercontent.com/LCJ-Group/aitherhub/feature/face-swap-mode-b/gpu-worker/worker_api.py" \
        -o "$WORKSPACE/worker_api.py"
fi

mkdir -p "$WORKSPACE/source_faces" "$WORKSPACE/tmp"
echo "  Done."

# ── v4l2loopback ─────────────────────────────────────────────────────────────

echo "[6/6] Setting up virtual webcam..."
if modprobe v4l2loopback video_nr=10 card_label="FaceSwap" exclusive_caps=1 2>/dev/null; then
    echo "  Virtual webcam created: /dev/video10"
else
    echo "  WARNING: v4l2loopback not available (normal on RunPod)."
    echo "  Single-frame swap will work. For streaming, use ffmpeg pipe mode."
fi

# ── Start ────────────────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "  GPU Worker API: http://0.0.0.0:${WORKER_PORT}"
echo "  API Key: ${WORKER_API_KEY:0:4}****"
echo ""
echo "  Quick Test:"
echo "    curl -H 'X-Api-Key: ${WORKER_API_KEY}' http://localhost:${WORKER_PORT}/api/health"
echo ""
echo "  Starting worker..."
echo ""

export WORKER_API_KEY="$WORKER_API_KEY"
export WORKER_PORT="$WORKER_PORT"
export FACEFUSION_DIR="$WORKSPACE/facefusion"
export SOURCE_FACE_DIR="$WORKSPACE/source_faces"
export TEMP_DIR="$WORKSPACE/tmp"

cd "$WORKSPACE"
python3 worker_api.py
