#!/bin/bash
##############################################################################
# RunPod Quick Setup Script
#
# Run this script inside a RunPod GPU Pod to set up the FaceFusion worker.
#
# Usage:
#   1. Create a RunPod GPU Pod (RTX 4090, Container Disk 50GB)
#   2. SSH into the pod or use the web terminal
#   3. Run: bash runpod-setup.sh
#
# The script will:
#   - Install system dependencies
#   - Clone and install FaceFusion
#   - Download only essential AI models (saves ~15GB disk)
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

echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    git-lfs \
    > /dev/null 2>&1
echo "  Done."

# ── FaceFusion ───────────────────────────────────────────────────────────────

echo "[2/7] Installing FaceFusion..."
cd "$WORKSPACE"

if [ ! -d "facefusion" ]; then
    git clone https://github.com/facefusion/facefusion.git
fi

cd facefusion
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet onnxruntime-gpu
echo "  Done."

# ── Download Essential Models Only ──────────────────────────────────────────
# Instead of force-download (which downloads ALL models ~20GB+),
# we only download the models we actually need:
#   - inswapper_128_fp16: face swap model (~280MB)
#   - gfpgan_1.4: face enhancer for quality (~350MB)
#   - yoloface_8n: face detector (~6MB)
#   - face_recognizer_arcface: face recognition (~250MB)

echo "[3/7] Downloading essential AI models only..."

MODEL_DIR="$WORKSPACE/facefusion/.assets/models"
mkdir -p "$MODEL_DIR"

# Base URL for HuggingFace facefusion models
HF_BASE="https://huggingface.co/facefusion/models/resolve/main"

download_model() {
    local filename="$1"
    local target="$MODEL_DIR/$filename"
    if [ -f "$target" ]; then
        echo "  [skip] $filename (already exists)"
    else
        echo "  [download] $filename ..."
        curl -sL "$HF_BASE/$filename" -o "$target" || {
            echo "  [warn] Failed to download $filename, will download on first use"
            return 0
        }
        echo "  [done] $filename ($(du -h "$target" | cut -f1))"
    fi
}

# Face Swapper - inswapper_128_fp16 (best quality)
download_model "inswapper_128_fp16.onnx"

# Face Enhancer - GFPGAN 1.4 (highest quality face restoration)
download_model "gfpgan_1.4.onnx"

# Face Detector - YOLOFace 8n (fast and accurate)
download_model "yoloface_8n.onnx"

# Face Recognizer - ArcFace (for face matching)
download_model "arcface_simswap.onnx"

# Face Landmarker (required for processing)
download_model "2dfan4.onnx"
download_model "face_landmarker_68_5.onnx"

echo "  Done. (Only essential models downloaded, ~1GB total)"

# ── Worker API Dependencies ──────────────────────────────────────────────────

echo "[4/7] Installing worker API dependencies..."
cd "$WORKSPACE"
pip install --quiet fastapi uvicorn httpx python-multipart pydantic
echo "  Done."

# ── Copy Worker Files ────────────────────────────────────────────────────────

echo "[5/7] Setting up worker API..."
if [ ! -f "$WORKSPACE/worker_api.py" ]; then
    echo "  Downloading worker_api.py from GitHub..."
    curl -sL "https://raw.githubusercontent.com/LCJ-Group/aitherhub/feature/face-swap-mode-b/gpu-worker/worker_api.py" \
        -o "$WORKSPACE/worker_api.py"
fi

mkdir -p "$WORKSPACE/source_faces" "$WORKSPACE/tmp"
echo "  Done."

# ── GPU Check ───────────────────────────────────────────────────────────────

echo "[6/7] Checking GPU..."
python3 -c "
import torch
if torch.cuda.is_available():
    gpu = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_mem / (1024**3)
    print(f'  GPU: {gpu} ({vram:.1f}GB VRAM)')
else:
    print('  WARNING: No GPU detected!')
" 2>/dev/null || echo "  GPU check skipped (torch not available for check)"

# ── Disk Usage Report ───────────────────────────────────────────────────────

echo "[7/7] Disk usage report..."
echo "  Models: $(du -sh "$MODEL_DIR" 2>/dev/null | cut -f1)"
echo "  FaceFusion: $(du -sh "$WORKSPACE/facefusion" 2>/dev/null | cut -f1)"
echo "  Total workspace: $(du -sh "$WORKSPACE" 2>/dev/null | cut -f1)"
df -h / | tail -1 | awk '{print "  Disk free: "$4" / "$2}'

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
