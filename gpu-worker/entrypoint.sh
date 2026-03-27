#!/bin/bash
# DO NOT use set -e — individual failures should not abort the whole startup.
# RunPod's /start.sh calls /post_start.sh, which should symlink to this file.
#
# === /workspace/ Persistent Storage Strategy ===
# Everything is stored in /workspace/ (RunPod volume disk) so that:
#   - Pod stop/restart: instant recovery (all data persists)
#   - New Pod with same volume: instant recovery
#   - New Pod without volume: full install (first time only, ~15-20 min)
#
# Layout:
#   /workspace/pip-packages/   — All Python packages (persistent)
#   /workspace/.pip-cache/     — pip download cache (persistent)
#   /workspace/aitherhub/      — GitHub repo (git pull on each start)
#   /workspace/models/         — AI model files (persistent)
#   /workspace/MuseTalk/       — MuseTalk repo + models
#   /workspace/FasterLivePortrait/ — LivePortrait repo + models
#   /workspace/*.py            — Worker scripts (copied from repo)
#   /workspace/*.log           — Runtime logs

echo "============================================"
echo "  AitherHub GPU Worker — Entrypoint v2"
echo "  Persistent /workspace/ Storage Mode"
echo "============================================"
echo ""

LOG_FILE="/workspace/entrypoint_run.log"
exec > >(tee -a "$LOG_FILE") 2>&1

# ── Configuration ────────────────────────────────────────────────────────────

WORKSPACE="/workspace"
WORKER_API_KEY="${WORKER_API_KEY:-change-me-in-production}"
WORKER_PORT="${WORKER_PORT:-8000}"

# Persistent package directory
PIP_PKG_DIR="$WORKSPACE/pip-packages"
PIP_CACHE_DIR="$WORKSPACE/.pip-cache"

# ── [1/9] Self-install as /post_start.sh ───────────────────────────────────

echo "[1/9] Ensuring entrypoint auto-start..."
SELF_PATH="$WORKSPACE/aitherhub/gpu-worker/entrypoint.sh"
if [ -f "$SELF_PATH" ]; then
    cat > /post_start.sh << 'POSTEOF'
#!/bin/bash
exec /workspace/aitherhub/gpu-worker/entrypoint.sh
POSTEOF
    chmod +x /post_start.sh
    echo "  [ok] /post_start.sh created → will auto-run on Pod restart"
else
    echo "  [skip] Self-install skipped (running from different path)"
fi

# ── [2/9] GPU Check ─────────────────────────────────────────────────────────

echo "[2/9] Checking GPU..."
NVIDIA_SMI=""
for p in /usr/bin/nvidia-smi /usr/local/bin/nvidia-smi /usr/lib/nvidia-smi; do
    if [ -x "$p" ]; then NVIDIA_SMI="$p"; break; fi
done
if [ -z "$NVIDIA_SMI" ] && command -v nvidia-smi &>/dev/null; then
    NVIDIA_SMI="nvidia-smi"
fi

if [ -n "$NVIDIA_SMI" ]; then
    $NVIDIA_SMI --query-gpu=name,memory.total,driver_version --format=csv,noheader || true
else
    echo "  WARNING: nvidia-smi not found in PATH."
fi

CUDA_OK=$(python3 -c "import torch; print('yes' if torch.cuda.is_available() else 'no')" 2>/dev/null || echo "no")
if [ "$CUDA_OK" = "yes" ]; then
    GPU_NAME=$(python3 -c "import torch; print(torch.cuda.get_device_name(0))" 2>/dev/null || echo "unknown")
    echo "  PyTorch CUDA: available ($GPU_NAME)"
else
    echo "  WARNING: PyTorch cannot see GPU (torch.cuda.is_available()=False)"
    echo "  LivePortrait and other GPU workloads will fail."
fi

# ── [3/9] System Dependencies ────────────────────────────────────────────────

echo "[3/9] Checking system dependencies..."

NEED_APT=0
for cmd in ffmpeg git-lfs; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "  [missing] $cmd"
        NEED_APT=1
    else
        echo "  [ok] $cmd"
    fi
done

if [ "$NEED_APT" -eq 1 ]; then
    echo "  Installing missing system packages..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq 2>/dev/null || true
    apt-get install -y -qq \
        ffmpeg \
        libgl1-mesa-glx \
        libglib2.0-0 \
        git-lfs \
        > /dev/null 2>&1 || true
    echo "  System packages installed."
else
    echo "  All system dependencies present."
fi

# ── [4/9] Python Dependencies (Persistent in /workspace/) ───────────────────
#
# Strategy:
#   1. Set PYTHONPATH to include /workspace/pip-packages/
#   2. Use pip --target to install into persistent volume
#   3. Use pip --cache-dir to cache downloads in persistent volume
#   4. Check a marker file to skip if already installed
#   5. Only re-install if marker is missing (first run or volume wiped)

echo "[4/9] Checking Python dependencies..."

# Set up persistent Python path
mkdir -p "$PIP_PKG_DIR" "$PIP_CACHE_DIR"
export PYTHONPATH="$PIP_PKG_DIR:${PYTHONPATH:-}"
export PATH="$PIP_PKG_DIR/bin:$PATH"

# Marker file to track successful installation
MARKER_FILE="$PIP_PKG_DIR/.install-complete-v2"

pip_install_persistent() {
    # Install a package into /workspace/pip-packages/ with cache
    local pkg="$1"
    pip install --quiet --target="$PIP_PKG_DIR" --cache-dir="$PIP_CACHE_DIR" \
        --upgrade --no-warn-script-location "$pkg" 2>/dev/null || true
}

if [ -f "$MARKER_FILE" ]; then
    echo "  [FAST MODE] Packages already installed in $PIP_PKG_DIR"
    echo "  Marker: $(cat $MARKER_FILE)"
    echo "  Skipping pip install phase entirely."
else
    echo "  [FULL INSTALL] First run — installing all packages to $PIP_PKG_DIR"
    echo "  This will take 15-20 minutes. Subsequent starts will be instant."
    echo ""

    # Worker API core
    echo "  [1/7] Installing API framework..."
    pip_install_persistent "fastapi"
    pip_install_persistent "uvicorn"
    pip_install_persistent "httpx"
    pip_install_persistent "python-multipart"
    pip_install_persistent "pydantic"

    # MuseTalk dependencies
    echo "  [2/7] Installing MuseTalk dependencies..."
    pip_install_persistent "opencv-python-headless"
    pip_install_persistent "einops"
    pip_install_persistent "face-alignment"
    pip_install_persistent "diffusers==0.30.2"
    pip_install_persistent "transformers"
    pip_install_persistent "accelerate"
    pip_install_persistent "safetensors"
    pip_install_persistent "omegaconf"
    pip_install_persistent "yacs"
    pip_install_persistent "mediapipe"

    # IMTalker dependencies
    echo "  [3/7] Installing IMTalker dependencies..."
    pip_install_persistent "torchdiffeq==0.2.5"
    pip_install_persistent "timm"
    pip_install_persistent "pytorch-lightning"
    pip_install_persistent "flow-vis"
    pip_install_persistent "av==12.0.0"
    pip_install_persistent "librosa"

    # FasterLivePortrait / JoyVASA dependencies
    echo "  [4/7] Installing LivePortrait dependencies..."
    pip_install_persistent "onnxruntime-gpu"
    pip_install_persistent "scipy"
    pip_install_persistent "tyro"

    # GFPGAN / basicsr dependencies
    echo "  [5/7] Installing GFPGAN/basicsr..."
    pip_install_persistent "gfpgan"
    pip_install_persistent "basicsr==1.4.2"

    # numpy downgrade for onnxruntime compat
    echo "  [6/7] Fixing numpy version..."
    pip_install_persistent "numpy==1.26.4"

    # torchaudio fix
    echo "  [7/7] Checking torchaudio..."
    TORCHAUDIO_OK=$(python3 -c "
import sys; sys.path.insert(0, '$PIP_PKG_DIR')
import torchaudio, torch
tv = torch.__version__.split('+')[0]
av = torchaudio.__version__.split('+')[0]
print('ok' if tv.split('.')[:2] == av.split('.')[:2] else 'mismatch')
" 2>/dev/null || echo "mismatch")
    if [ "$TORCHAUDIO_OK" != "ok" ]; then
        echo "  [fix] Reinstalling torchaudio to match torch..."
        pip install --quiet --force-reinstall --no-deps --target="$PIP_PKG_DIR" \
            --cache-dir="$PIP_CACHE_DIR" torchaudio 2>/dev/null || true
    fi

    # Write marker
    echo "Installed at $(date) on $(hostname)" > "$MARKER_FILE"
    echo "  [DONE] All packages installed to $PIP_PKG_DIR"
fi

echo "  Python dependencies ready."

# ── [5/9] Critical Compatibility Fixes ───────────────────────────────────────

echo "[5/9] Applying critical compatibility fixes..."

# Fix 1: numpy check (already handled in install phase, but verify)
NUMPY_VER=$(python3 -c "import numpy; print(numpy.__version__)" 2>/dev/null || echo "0")
NUMPY_MAJOR=$(echo "$NUMPY_VER" | cut -d. -f1)
if [ "$NUMPY_MAJOR" -ge 2 ] 2>/dev/null; then
    echo "  [fix] Downgrading numpy from $NUMPY_VER to 1.26.4..."
    pip install --quiet --target="$PIP_PKG_DIR" --cache-dir="$PIP_CACHE_DIR" \
        "numpy==1.26.4" 2>/dev/null || true
else
    echo "  [ok] numpy $NUMPY_VER (compatible)"
fi

# Fix 2: basicsr/torchvision compatibility for GFPGAN
BASICSR_DEG=""
# Check both system and persistent locations
for base_dir in "$PIP_PKG_DIR" "/usr/local/lib/python3.11/dist-packages"; do
    candidate="$base_dir/basicsr/data/degradations.py"
    if [ -f "$candidate" ]; then
        BASICSR_DEG="$candidate"
        break
    fi
done
if [ -n "$BASICSR_DEG" ] && [ -f "$BASICSR_DEG" ]; then
    if grep -q 'from torchvision.transforms.functional_tensor' "$BASICSR_DEG"; then
        sed -i 's/from torchvision.transforms.functional_tensor import rgb_to_grayscale/from torchvision.transforms.functional import rgb_to_grayscale/' "$BASICSR_DEG"
        echo "  [patched] basicsr degradations.py (torchvision compat)"
    else
        echo "  [ok] basicsr degradations.py already patched"
    fi
else
    echo "  [skip] basicsr degradations.py not found"
fi

# Fix 3: JoyVASA torch.load needs weights_only=False for PyTorch 2.6+
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
GFPGAN_MODEL="$WORKSPACE/models/GFPGANv1.4.pth"
if [ ! -f "$GFPGAN_MODEL" ]; then
    echo "  [download] GFPGAN model..."
    mkdir -p "$WORKSPACE/models"
    wget -q -O "$GFPGAN_MODEL" "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth" || true
fi

echo "  Compatibility fixes complete."

# ── [6/9] v4l2loopback Setup ────────────────────────────────────────────────

echo "[6/9] Setting up virtual webcam (v4l2loopback)..."
if [ -e /dev/video10 ]; then
    echo "  Virtual webcam /dev/video10 already exists."
else
    modprobe v4l2loopback video_nr=10 card_label="FaceSwap Virtual Cam" exclusive_caps=1 2>/dev/null || \
        echo "  WARNING: Could not load v4l2loopback (normal for RunPod)."
fi

# ── [7/9] FaceFusion Model Check ────────────────────────────────────────────

echo "[7/9] Checking FaceFusion models..."
FACEFUSION_DIR="${FACEFUSION_DIR:-$WORKSPACE/facefusion}"
MODELS_DIR="$FACEFUSION_DIR/.assets/models"
if [ -d "$MODELS_DIR" ] && [ "$(ls -A $MODELS_DIR 2>/dev/null)" ]; then
    MODEL_COUNT=$(ls "$MODELS_DIR/" 2>/dev/null | wc -l)
    echo "  Models found: $MODEL_COUNT files in $MODELS_DIR"
else
    echo "  Models not found. Will download on first use."
fi

# ── [8/9] MuseTalk Patches ──────────────────────────────────────────────────

echo "[8/9] Checking MuseTalk runtime patches..."

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

# ── [9/9] Pull Latest Code & Start Workers ─────────────────────────────────

echo "[9/9] Pulling latest code from GitHub..."
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

export PYTHONPATH="$PIP_PKG_DIR:${PYTHONPATH:-}"
export PATH="$PIP_PKG_DIR/bin:$PATH"
export WORKER_API_KEY="$WORKER_API_KEY"
export WORKER_PORT="$WORKER_PORT"
export FACEFUSION_DIR="${FACEFUSION_DIR:-$WORKSPACE/facefusion}"
export SOURCE_FACE_DIR="$WORKSPACE/source_faces"
export TEMP_DIR="$WORKSPACE/tmp"
export MUSETALK_DIR="${MUSETALK_DIR:-$WORKSPACE/MuseTalk}"
export IMTALKER_DIR="${IMTALKER_DIR:-$WORKSPACE/IMTalker}"
export FASTER_LIVEPORTRAIT_DIR="${FASTER_LIVEPORTRAIT_DIR:-$WORKSPACE/FasterLivePortrait}"

# ── Kill any existing workers ──────────────────────────────────────────────

pkill -f "python3 worker_api.py" 2>/dev/null || true
pkill -f "python3 live_api.py" 2>/dev/null || true
sleep 2

# ── Start Workers ──────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Startup Complete!"
echo "============================================"
echo ""
echo "  GPU Worker API: http://0.0.0.0:${WORKER_PORT}"
echo "  API Key: ${WORKER_API_KEY:0:4}****"
echo "  Packages: $PIP_PKG_DIR"
echo ""
echo "  Features:"
echo "    - FaceFusion (Mode B: Real-time face swap)"
echo "    - MuseTalk v1.5 (Mode A: Digital human lip-sync)"
echo "    - IMTalker (Premium: Full facial animation)"
echo "    - LivePortrait 3-Layer (Next-gen: Audio-driven face animation)"
echo ""

cd "$WORKSPACE"

# Start Live API (background)
if [ -f "$WORKSPACE/live_api.py" ]; then
    echo "  Starting Live API on port 8002..."
    PYTHONPATH="$PIP_PKG_DIR:${PYTHONPATH:-}" nohup python3 live_api.py > /var/log/live_api.log 2>&1 &
    echo "  Live API started (PID: $!)"
fi

# Start Worker API (background so this script can return to /start.sh)
echo "  Starting Worker API on port ${WORKER_PORT}..."
PYTHONPATH="$PIP_PKG_DIR:${PYTHONPATH:-}" nohup python3 worker_api.py > /var/log/worker_api.log 2>&1 &
WORKER_PID=$!
echo "  Worker API started (PID: $WORKER_PID)"

# Wait a moment and verify
sleep 5
if kill -0 $WORKER_PID 2>/dev/null; then
    echo "  [ok] Worker API is running (PID: $WORKER_PID)"
else
    echo "  [ERROR] Worker API failed to start. Check /var/log/worker_api.log"
fi

echo ""
echo "Entrypoint finished at $(date)"
echo "============================================"
