#!/bin/bash
##############################################################################
# RunPod Quick Setup Script
#
# Run this script inside a RunPod GPU Pod to set up the FaceFusion worker
# with MuseTalk digital human support.
#
# Usage:
#   1. Create a RunPod GPU Pod (RTX 4090, Container Disk 50GB)
#   2. SSH into the pod or use the web terminal
#   3. Run: bash runpod-setup.sh
#
# The script will:
#   - Install system dependencies (including ffmpeg)
#   - Clone and install FaceFusion
#   - Clone and install MuseTalk v1.5
#   - Download only essential AI models (saves ~15GB disk)
#   - Apply runtime patches for MuseTalk compatibility
#   - Start the worker API server
##############################################################################

set -e

echo "============================================"
echo "  AitherHub GPU Worker — RunPod Setup"
echo "  (FaceFusion + MuseTalk)"
echo "============================================"
echo ""

# ── Configuration ────────────────────────────────────────────────────────────

WORKER_API_KEY="${WORKER_API_KEY:-change-me-in-production}"
WORKER_PORT="${WORKER_PORT:-8000}"
WORKSPACE="/workspace"

# ── System Dependencies ──────────────────────────────────────────────────────

echo "[1/9] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    git-lfs \
    > /dev/null 2>&1
echo "  Done."

# ── FaceFusion ───────────────────────────────────────────────────────────────

echo "[2/9] Installing FaceFusion..."
cd "$WORKSPACE"

if [ ! -d "facefusion" ]; then
    git clone https://github.com/facefusion/facefusion.git
fi

cd facefusion
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet onnxruntime-gpu
echo "  Done."

# ── Download Essential FaceFusion Models ─────────────────────────────────────

echo "[3/9] Downloading essential FaceFusion AI models..."

MODEL_DIR="$WORKSPACE/facefusion/.assets/models"
mkdir -p "$MODEL_DIR"

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

download_model "inswapper_128_fp16.onnx"
download_model "gfpgan_1.4.onnx"
download_model "yoloface_8n.onnx"
download_model "arcface_simswap.onnx"
download_model "2dfan4.onnx"
download_model "face_landmarker_68_5.onnx"

echo "  Done."

# ── MuseTalk v1.5 Installation ──────────────────────────────────────────────

echo "[4/9] Installing MuseTalk v1.5..."
cd "$WORKSPACE"

if [ ! -d "MuseTalk" ]; then
    echo "  Cloning MuseTalk..."
    git clone https://github.com/TMElyralab/MuseTalk.git
fi

cd MuseTalk

# Install MuseTalk Python dependencies
echo "  Installing MuseTalk Python dependencies..."
pip install --quiet opencv-python-headless einops face_alignment \
    diffusers==0.30.2 transformers accelerate safetensors \
    omegaconf yacs mmpose mmdet mmcv mediapipe \
    2>/dev/null || true

echo "  Done."

# ── IMTalker Dependencies ──────────────────────────────────────────────────

echo "[4b/9] Installing IMTalker dependencies..."
cd "$WORKSPACE"

if [ -d "IMTalker" ]; then
    # Install IMTalker Python dependencies from requirement.txt
    echo "  Installing IMTalker Python dependencies..."
    pip install --quiet torchdiffeq==0.2.5 timm pytorch-lightning \
        flow-vis av==12.0.0 librosa \
        2>/dev/null || true
    echo "  Done."
else
    echo "  [skip] IMTalker directory not found"
fi

# ── MuseTalk Runtime Patches ────────────────────────────────────────────────

echo "[5/9] Applying MuseTalk runtime patches..."

# Patch 1: Fix diffusers meta tensor issue in VAE loading
# diffusers >= 0.28 defaults low_cpu_mem_usage=True which causes meta tensor errors
VAE_FILE="$WORKSPACE/MuseTalk/musetalk/utils/vae.py"
if [ -f "$VAE_FILE" ]; then
    if grep -q "low_cpu_mem_usage" "$VAE_FILE"; then
        echo "  [skip] vae.py already patched"
    else
        sed -i 's/AutoencoderKL.from_pretrained(model_path)/AutoencoderKL.from_pretrained(model_path, low_cpu_mem_usage=False)/g' "$VAE_FILE"
        echo "  [done] vae.py patched (low_cpu_mem_usage=False)"
    fi
fi

# Patch 2: Fix FaceParsing relative path issue
# FaceParsing uses relative path ./models/face-parse-bisent/ which breaks
# when cwd is not MUSETALK_DIR during inference
FP_INIT="$WORKSPACE/MuseTalk/musetalk/utils/face_parsing/__init__.py"
if [ -f "$FP_INIT" ]; then
    if grep -q "/workspace/MuseTalk/models" "$FP_INIT"; then
        echo "  [skip] FaceParsing __init__.py already patched"
    else
        sed -i "s|'./models/face-parse-bisent/resnet18-5c106cde.pth'|'/workspace/MuseTalk/models/face-parse-bisent/resnet18-5c106cde.pth'|g" "$FP_INIT"
        sed -i "s|'./models/face-parse-bisent/79999_iter.pth'|'/workspace/MuseTalk/models/face-parse-bisent/79999_iter.pth'|g" "$FP_INIT"
        echo "  [done] FaceParsing __init__.py patched (absolute paths)"
    fi
fi

echo "  Done."

# ── Worker API Dependencies ──────────────────────────────────────────────────

echo "[6/9] Installing worker API dependencies..."
cd "$WORKSPACE"
pip install --quiet fastapi uvicorn httpx python-multipart pydantic
echo "  Done."

# ── Copy Worker Files ────────────────────────────────────────────────────────

echo "[7/9] Setting up worker API..."
if [ ! -f "$WORKSPACE/worker_api.py" ]; then
    echo "  Downloading worker_api.py from GitHub..."
    curl -sL "https://raw.githubusercontent.com/proteanstudios/aitherhub-repo/main/gpu-worker/worker_api.py" \
        -o "$WORKSPACE/worker_api.py"
fi

mkdir -p "$WORKSPACE/source_faces" "$WORKSPACE/tmp"
echo "  Done."

# ── GPU Check ───────────────────────────────────────────────────────────────

echo "[8/9] Checking GPU..."
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

echo "[9/9] Disk usage report..."
echo "  FaceFusion Models: $(du -sh "$MODEL_DIR" 2>/dev/null | cut -f1)"
echo "  FaceFusion: $(du -sh "$WORKSPACE/facefusion" 2>/dev/null | cut -f1)"
echo "  MuseTalk: $(du -sh "$WORKSPACE/MuseTalk" 2>/dev/null | cut -f1)"
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
echo "  Features:"
echo "    - FaceFusion (Mode B: Real-time face swap)"
echo "    - MuseTalk v1.5 (Mode A: Digital human lip-sync)"
echo "    - IMTalker (Premium: Full facial animation)"
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
export MUSETALK_DIR="$WORKSPACE/MuseTalk"
export IMTALKER_DIR="$WORKSPACE/IMTalker"

cd "$WORKSPACE"
python3 worker_api.py
