#!/bin/bash
set -e

echo "============================================"
echo "  AitherHub GPU Worker — Entrypoint"
echo "  (FaceFusion + MuseTalk + IMTalker + LivePortrait)"
echo "============================================"
echo ""

# ── Configuration ────────────────────────────────────────────────────────────

WORKSPACE="/workspace"
WORKER_API_KEY="${WORKER_API_KEY:-change-me-in-production}"
WORKER_PORT="${WORKER_PORT:-8000}"

# ── [1/8] GPU Check ─────────────────────────────────────────────────────────

echo "[1/8] Checking GPU..."
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
    echo ""
else
    echo "WARNING: nvidia-smi not found. GPU may not be available."
fi

# ── [2/8] System Dependencies ────────────────────────────────────────────────
# System packages (apt) are lost on Pod restart, so always check.

echo "[2/8] Checking system dependencies..."

NEED_APT=0
for cmd in ffmpeg git-lfs; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "  [missing] $cmd"
        NEED_APT=1
    else
        echo "  [ok] $cmd"
    fi
done

if [ "$NEED_APT" -eq 1 ]; then
    echo "  Installing missing system packages..."
    apt-get update -qq
    apt-get install -y -qq \
        ffmpeg \
        libgl1-mesa-glx \
        libglib2.0-0 \
        git-lfs \
        > /dev/null 2>&1
    echo "  System packages installed."
else
    echo "  All system dependencies present."
fi

# ── [3/8] Python Dependencies ────────────────────────────────────────────────
# pip packages outside /workspace are lost on Pod restart.

echo "[3/8] Checking Python dependencies..."

install_if_missing() {
    local pkg="$1"
    local pip_name="${2:-$1}"
    if ! python3 -c "import $pkg" 2>/dev/null; then
        echo "  [install] $pip_name"
        pip install --quiet "$pip_name" 2>/dev/null || true
    else
        echo "  [ok] $pkg"
    fi
}

# Worker API core
install_if_missing "fastapi" "fastapi"
install_if_missing "uvicorn" "uvicorn"
install_if_missing "httpx" "httpx"
install_if_missing "multipart" "python-multipart"
install_if_missing "pydantic" "pydantic"

# MuseTalk dependencies
install_if_missing "cv2" "opencv-python-headless"
install_if_missing "einops" "einops"
install_if_missing "face_alignment" "face-alignment"
install_if_missing "diffusers" "diffusers==0.30.2"
install_if_missing "transformers" "transformers"
install_if_missing "accelerate" "accelerate"
install_if_missing "safetensors" "safetensors"
install_if_missing "omegaconf" "omegaconf"
install_if_missing "yacs" "yacs"
install_if_missing "mediapipe" "mediapipe"

# IMTalker dependencies
install_if_missing "torchdiffeq" "torchdiffeq==0.2.5"
install_if_missing "timm" "timm"
install_if_missing "pytorch_lightning" "pytorch-lightning"
install_if_missing "flow_vis" "flow-vis"
install_if_missing "av" "av==12.0.0"
install_if_missing "librosa" "librosa"

# FasterLivePortrait / JoyVASA dependencies
install_if_missing "onnxruntime" "onnxruntime-gpu"
install_if_missing "scipy" "scipy"
install_if_missing "tyro" "tyro"

# GFPGAN / basicsr dependencies
install_if_missing "gfpgan" "gfpgan"
install_if_missing "basicsr" "basicsr==1.4.2"

echo "  Python dependencies check complete."

# ── [4/8] Critical Compatibility Fixes ───────────────────────────────────────
# These fixes are REQUIRED for LivePortrait engine to initialize.
# Without them, onnxruntime/insightface and JoyVASA will fail.

echo "[4/8] Applying critical compatibility fixes..."

# Fix 1: numpy must be <2.0 for onnxruntime (insightface dependency)
# onnxruntime compiled against numpy 1.x crashes with numpy 2.x
NUMPY_VER=$(python3 -c "import numpy; print(numpy.__version__)" 2>/dev/null || echo "0")
NUMPY_MAJOR=$(echo "$NUMPY_VER" | cut -d. -f1)
if [ "$NUMPY_MAJOR" -ge 2 ] 2>/dev/null; then
    echo "  [fix] Downgrading numpy from $NUMPY_VER to 1.26.4 (onnxruntime compat)..."
    pip install --quiet "numpy==1.26.4" 2>/dev/null || true
else
    echo "  [ok] numpy $NUMPY_VER (compatible)"
fi

# Fix 2: torchaudio must match torch version
# After pip installs, torchaudio may be wrong version
TORCH_VER=$(python3 -c "import torch; print(torch.__version__.split('+')[0])" 2>/dev/null || echo "0")
TORCHAUDIO_OK=$(python3 -c "
import torchaudio, torch
tv = torch.__version__.split('+')[0]
av = torchaudio.__version__.split('+')[0]
print('ok' if tv.split('.')[:2] == av.split('.')[:2] else 'mismatch')
" 2>/dev/null || echo "mismatch")
if [ "$TORCHAUDIO_OK" != "ok" ]; then
    echo "  [fix] Reinstalling torchaudio to match torch $TORCH_VER..."
    pip install --quiet --force-reinstall --no-deps torchaudio 2>/dev/null || true
else
    echo "  [ok] torchaudio matches torch"
fi

# Fix 3: basicsr/torchvision compatibility for GFPGAN
# basicsr 1.4.2 imports from torchvision.transforms.functional_tensor which was removed
BASICSR_DEG="/usr/local/lib/python3.11/dist-packages/basicsr/data/degradations.py"
if [ -f "$BASICSR_DEG" ]; then
    if grep -q 'from torchvision.transforms.functional_tensor' "$BASICSR_DEG"; then
        sed -i 's/from torchvision.transforms.functional_tensor import rgb_to_grayscale/from torchvision.transforms.functional import rgb_to_grayscale/' "$BASICSR_DEG"
        echo "  [patched] basicsr degradations.py (torchvision compat)"
    else
        echo "  [ok] basicsr degradations.py already patched"
    fi
fi

# Fix 4: JoyVASA torch.load needs weights_only=False for PyTorch 2.6+
# PyTorch 2.6 changed default to weights_only=True, breaking old checkpoints
JOYVASA_PIPELINE="$WORKSPACE/FasterLivePortrait/src/pipelines/joyvasa_audio_to_motion_pipeline.py"
if [ -f "$JOYVASA_PIPELINE" ]; then
    if grep -q 'torch.load(motion_model_path, map_location="cpu")' "$JOYVASA_PIPELINE" && \
       ! grep -q 'weights_only=False' "$JOYVASA_PIPELINE"; then
        sed -i 's/torch.load(motion_model_path, map_location="cpu")/torch.load(motion_model_path, map_location="cpu", weights_only=False)/' "$JOYVASA_PIPELINE"
        echo "  [patched] JoyVASA pipeline (weights_only=False)"
    else
        echo "  [ok] JoyVASA pipeline already patched"
    fi
fi

# Ensure GFPGAN model exists
GFPGAN_MODEL="/workspace/models/GFPGANv1.4.pth"
if [ ! -f "$GFPGAN_MODEL" ]; then
    echo "  [download] GFPGAN model..."
    mkdir -p /workspace/models
    wget -q -O "$GFPGAN_MODEL" "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth" || true
fi

echo "  Compatibility fixes complete."

# ── [5/8] v4l2loopback Setup ────────────────────────────────────────────────

echo "[5/8] Setting up virtual webcam (v4l2loopback)..."
if [ -e /dev/video10 ]; then
    echo "  Virtual webcam /dev/video10 already exists."
else
    if modprobe v4l2loopback video_nr=10 card_label="FaceSwap Virtual Cam" exclusive_caps=1 2>/dev/null; then
        echo "  v4l2loopback loaded: /dev/video10"
    else
        echo "  WARNING: Could not load v4l2loopback (normal for RunPod)."
    fi
fi

# ── [6/8] FaceFusion Model Check ────────────────────────────────────────────

echo "[6/8] Checking FaceFusion models..."
FACEFUSION_DIR="${FACEFUSION_DIR:-$WORKSPACE/facefusion}"
MODELS_DIR="$FACEFUSION_DIR/.assets/models"
if [ -d "$MODELS_DIR" ] && [ "$(ls -A $MODELS_DIR 2>/dev/null)" ]; then
    MODEL_COUNT=$(ls "$MODELS_DIR/" 2>/dev/null | wc -l)
    echo "  Models found: $MODEL_COUNT files in $MODELS_DIR"
else
    echo "  Models not found. Will download on first use."
fi

# ── [7/8] MuseTalk Patches ──────────────────────────────────────────────────

echo "[7/8] Checking MuseTalk runtime patches..."

MUSETALK_DIR="${MUSETALK_DIR:-$WORKSPACE/MuseTalk}"

# Patch 1: Fix diffusers meta tensor issue in VAE loading
VAE_FILE="$MUSETALK_DIR/musetalk/utils/vae.py"
if [ -f "$VAE_FILE" ]; then
    if grep -q "low_cpu_mem_usage" "$VAE_FILE"; then
        echo "  [ok] vae.py already patched"
    else
        sed -i 's/AutoencoderKL.from_pretrained(model_path)/AutoencoderKL.from_pretrained(model_path, low_cpu_mem_usage=False)/g' "$VAE_FILE"
        echo "  [patched] vae.py (low_cpu_mem_usage=False)"
    fi
fi

# Patch 2: Fix FaceParsing relative path issue
FP_INIT="$MUSETALK_DIR/musetalk/utils/face_parsing/__init__.py"
if [ -f "$FP_INIT" ]; then
    if grep -q "/workspace/MuseTalk/models" "$FP_INIT"; then
        echo "  [ok] FaceParsing __init__.py already patched"
    else
        sed -i "s|'./models/face-parse-bisent/resnet18-5c106cde.pth'|'/workspace/MuseTalk/models/face-parse-bisent/resnet18-5c106cde.pth'|g" "$FP_INIT"
        sed -i "s|'./models/face-parse-bisent/79999_iter.pth'|'/workspace/MuseTalk/models/face-parse-bisent/79999_iter.pth'|g" "$FP_INIT"
        echo "  [patched] FaceParsing __init__.py (absolute paths)"
    fi
fi

echo "  Patches check complete."

# ── [8/8] Pull Latest Code from GitHub ──────────────────────────────────────

echo "[8/8] Pulling latest code from GitHub..."
REPO_DIR="$WORKSPACE/aitherhub"
if [ -d "$REPO_DIR/.git" ]; then
    cd "$REPO_DIR"
    git fetch origin master --quiet 2>/dev/null || true
    git reset --hard origin/master --quiet 2>/dev/null || true
    # Copy latest worker files to workspace
    cp -f "$REPO_DIR/gpu-worker/worker_api.py" "$WORKSPACE/worker_api.py" 2>/dev/null || true
    cp -f "$REPO_DIR/gpu-worker/live_api.py" "$WORKSPACE/live_api.py" 2>/dev/null || true
    cp -f "$REPO_DIR/gpu-worker/live_engine.py" "$WORKSPACE/live_engine.py" 2>/dev/null || true
    cp -f "$REPO_DIR/gpu-worker/liveportrait_engine.py" "$WORKSPACE/liveportrait_engine.py" 2>/dev/null || true
    cp -f "$REPO_DIR/gpu-worker/imtalker_generate_patch.py" "$WORKSPACE/imtalker_generate_patch.py" 2>/dev/null || true
    echo "  Latest code pulled and copied."
else
    echo "  [skip] Git repo not found at $REPO_DIR. Using existing worker files."
fi

# ── Create Required Directories ─────────────────────────────────────────────

mkdir -p "$WORKSPACE/source_faces" "$WORKSPACE/tmp"

# ── Environment Variables ───────────────────────────────────────────────────

export WORKER_API_KEY="$WORKER_API_KEY"
export WORKER_PORT="$WORKER_PORT"
export FACEFUSION_DIR="${FACEFUSION_DIR:-$WORKSPACE/facefusion}"
export SOURCE_FACE_DIR="$WORKSPACE/source_faces"
export TEMP_DIR="$WORKSPACE/tmp"
export MUSETALK_DIR="${MUSETALK_DIR:-$WORKSPACE/MuseTalk}"
export IMTALKER_DIR="${IMTALKER_DIR:-$WORKSPACE/IMTalker}"
export FASTER_LIVEPORTRAIT_DIR="${FASTER_LIVEPORTRAIT_DIR:-$WORKSPACE/FasterLivePortrait}"

# ── Start Worker API ────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Startup Complete!"
echo "============================================"
echo ""
echo "  GPU Worker API: http://0.0.0.0:${WORKER_PORT}"
echo "  API Key: ${WORKER_API_KEY:0:4}****"
echo ""
echo "  Features:"
echo "    - FaceFusion (Mode B: Real-time face swap)"
echo "    - MuseTalk v1.5 (Mode A: Digital human lip-sync)"
echo "    - IMTalker (Premium: Full facial animation)"
echo "    - LivePortrait 3-Layer (Next-gen: Audio-driven face animation)"
echo ""
echo "  Endpoints:"
echo "    GET  /api/health                    - Health check"
echo "    GET  /api/digital-human/health      - Digital human health"
echo "    POST /api/digital-human/generate    - MuseTalk generate"
echo "    POST /api/digital-human/imtalker/generate - IMTalker generate"
echo "    POST /api/digital-human/liveportrait/generate - LivePortrait 3-layer"
echo ""

cd "$WORKSPACE"

# ── Start Live API (background) ─────────────────────────────────────────────
if [ -f "$WORKSPACE/live_api.py" ]; then
    echo "  Starting Live API on port 8002..."
    nohup python3 live_api.py > /var/log/live_api.log 2>&1 &
    LIVE_API_PID=$!
    echo "  Live API started (PID: $LIVE_API_PID)"
fi

exec python3 worker_api.py
