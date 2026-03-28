#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# AitherHub GPU Worker — Serverless Setup Script (v3 Self-Contained)
#
# Runs at container start (cold start) to:
#   1. Check if Network Volume is available (optional, for models/cache)
#   2. Verify AI tools are present (baked into Docker image at /app/)
#   3. Download MuseTalk models if not present
#   4. Apply runtime patches for compatibility
#   5. Verify GPU and critical paths
#   6. Hand off to handler.py
#
# Docker Image Layout (always available):
#   /app/
#   ├── facefusion/          — FaceFusion repo + essential models
#   ├── MuseTalk/            — MuseTalk repo (models downloaded at first start)
#   ├── models/              — Shared model files (GFPGAN, etc.)
#   ├── handler.py           — RunPod Serverless handler
#   ├── live_engine.py       — MuseTalk engine
#   ├── liveportrait_engine.py
#   └── setup.sh             — This script
#
# Optional Network Volume (/runpod-volume/):
#   If attached, MuseTalk models are cached here for faster cold starts.
#   If not attached, models are downloaded to /app/ (slower first start).
# ──────────────────────────────────────────────────────────────────────────────

APP_DIR="/app"
WORKSPACE="/workspace"
VOLUME="${RUNPOD_VOLUME_PATH:-/runpod-volume}"

echo "=== AitherHub Serverless Setup v3 (Self-Contained) ==="
echo "Timestamp: $(date)"
echo "App dir: $APP_DIR"
echo "Workspace: $WORKSPACE"
echo "Volume path: $VOLUME"

# ── 1. Network Volume Check (Optional) ────────────────────────────────────

if [ -d "$VOLUME" ] && [ "$(ls -A $VOLUME 2>/dev/null)" ]; then
    echo "[OK] Network volume found and not empty"
    HAS_VOLUME=true
else
    echo "[INFO] No network volume — using Docker image contents only"
    HAS_VOLUME=false
fi

# ── 2. Verify AI Tools (baked into Docker image) ──────────────────────────

echo ""
echo "=== Verifying AI Tools ==="

for dir_name in "facefusion" "MuseTalk"; do
    app_path="$APP_DIR/$dir_name"
    if [ -d "$app_path" ]; then
        echo "  [OK] $dir_name at $app_path"
    else
        echo "  [MISSING] $dir_name at $app_path"
    fi
done

# If Network Volume has IMTalker or FasterLivePortrait, link them
if [ "$HAS_VOLUME" = true ]; then
    for dir_name in "IMTalker" "FasterLivePortrait"; do
        vol_path="$VOLUME/$dir_name"
        ws_path="$WORKSPACE/$dir_name"
        if [ -d "$vol_path" ] && [ ! -e "$ws_path" ]; then
            ln -sf "$vol_path" "$ws_path"
            echo "  [linked] $ws_path -> $vol_path"
        fi
    done
fi

# ── 3. MuseTalk Models ────────────────────────────────────────────────────
# MuseTalk needs several model directories. Check if they exist in:
#   1. Network Volume (fastest, cached)
#   2. Docker image /app/MuseTalk/models/
#   3. Download from HuggingFace (first cold start only)

echo ""
echo "=== MuseTalk Models ==="

MUSETALK_MODELS="$APP_DIR/MuseTalk/models"
mkdir -p "$MUSETALK_MODELS"

# Model directories needed by MuseTalk
MUSETALK_MODEL_DIRS=(
    "dwpose"
    "face-parse-bisenet"
    "face-parse-bisent"
    "whisper"
    "sd-vae-ft-mse"
    "musetalk"
    "musetalkV15"
)

# Check if models exist in Network Volume first
if [ "$HAS_VOLUME" = true ] && [ -d "$VOLUME/models" ]; then
    echo "  Checking Network Volume for cached models..."
    for subdir in "${MUSETALK_MODEL_DIRS[@]}"; do
        vol_model="$VOLUME/models/$subdir"
        local_model="$MUSETALK_MODELS/$subdir"
        if [ -d "$vol_model" ] && [ ! -e "$local_model" ]; then
            ln -sf "$vol_model" "$local_model"
            echo "  [cached] $subdir -> $vol_model"
        fi
    done
    # Also check MuseTalk-specific location
    if [ -d "$VOLUME/MuseTalk/models" ]; then
        for subdir in "${MUSETALK_MODEL_DIRS[@]}"; do
            vol_model="$VOLUME/MuseTalk/models/$subdir"
            local_model="$MUSETALK_MODELS/$subdir"
            if [ -d "$vol_model" ] && [ ! -e "$local_model" ]; then
                ln -sf "$vol_model" "$local_model"
                echo "  [cached] $subdir -> $vol_model"
            fi
        done
    fi
fi

# Check if all critical models are present
MODELS_READY=true
for subdir in "sd-vae-ft-mse" "musetalk" "musetalkV15" "whisper"; do
    if [ ! -d "$MUSETALK_MODELS/$subdir" ] || [ -z "$(ls -A $MUSETALK_MODELS/$subdir 2>/dev/null)" ]; then
        echo "  [MISSING] $subdir — will download"
        MODELS_READY=false
    else
        echo "  [OK] $subdir"
    fi
done

# Download missing MuseTalk models if needed
if [ "$MODELS_READY" = false ]; then
    echo ""
    echo "  Downloading MuseTalk models (first cold start, ~5-10 min)..."

    # sd-vae-ft-mse
    if [ ! -d "$MUSETALK_MODELS/sd-vae-ft-mse" ] || [ -z "$(ls -A $MUSETALK_MODELS/sd-vae-ft-mse 2>/dev/null)" ]; then
        echo "  [download] sd-vae-ft-mse..."
        mkdir -p "$MUSETALK_MODELS/sd-vae-ft-mse"
        python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='stabilityai/sd-vae-ft-mse', local_dir='$MUSETALK_MODELS/sd-vae-ft-mse')
print('  [done] sd-vae-ft-mse')
" 2>/dev/null || echo "  [warn] sd-vae-ft-mse download failed"
    fi

    # musetalk model weights
    if [ ! -d "$MUSETALK_MODELS/musetalk" ] || [ -z "$(ls -A $MUSETALK_MODELS/musetalk 2>/dev/null)" ]; then
        echo "  [download] musetalk weights..."
        mkdir -p "$MUSETALK_MODELS/musetalk"
        python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='TMElyralab/MuseTalk', local_dir='/tmp/musetalk_hf', allow_patterns=['models/musetalk/*'])
import shutil, os
src = '/tmp/musetalk_hf/models/musetalk'
if os.path.isdir(src):
    for f in os.listdir(src):
        shutil.copy2(os.path.join(src, f), '$MUSETALK_MODELS/musetalk/')
    print('  [done] musetalk weights')
else:
    print('  [warn] musetalk weights not found in HF download')
" 2>/dev/null || echo "  [warn] musetalk weights download failed"
    fi

    # musetalkV15 model weights (v1.5)
    if [ ! -d "$MUSETALK_MODELS/musetalkV15" ] || [ -z "$(ls -A $MUSETALK_MODELS/musetalkV15 2>/dev/null)" ]; then
        echo "  [download] musetalkV15 weights..."
        mkdir -p "$MUSETALK_MODELS/musetalkV15"
        python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='TMElyralab/MuseTalk', local_dir='/tmp/musetalk_hf_v15', allow_patterns=['musetalkV15/*'])
import shutil, os
src = '/tmp/musetalk_hf_v15/musetalkV15'
if os.path.isdir(src):
    for f in os.listdir(src):
        shutil.copy2(os.path.join(src, f), '$MUSETALK_MODELS/musetalkV15/')
    print('  [done] musetalkV15 weights')
else:
    print('  [warn] musetalkV15 weights not found in HF download')
" 2>/dev/null || echo "  [warn] musetalkV15 weights download failed"
    fi

    # whisper model
    if [ ! -d "$MUSETALK_MODELS/whisper" ] || [ -z "$(ls -A $MUSETALK_MODELS/whisper 2>/dev/null)" ]; then
        echo "  [download] whisper..."
        mkdir -p "$MUSETALK_MODELS/whisper"
        python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='openai/whisper-tiny', local_dir='$MUSETALK_MODELS/whisper')
print('  [done] whisper')
" 2>/dev/null || echo "  [warn] whisper download failed"
    fi

    # dwpose
    if [ ! -d "$MUSETALK_MODELS/dwpose" ] || [ -z "$(ls -A $MUSETALK_MODELS/dwpose 2>/dev/null)" ]; then
        echo "  [download] dwpose..."
        mkdir -p "$MUSETALK_MODELS/dwpose"
        python -c "
from huggingface_hub import hf_hub_download
hf_hub_download(repo_id='yzd-v/DWPose', filename='dw-ll_ucoco_384.onnx', local_dir='$MUSETALK_MODELS/dwpose')
print('  [done] dwpose')
" 2>/dev/null || echo "  [warn] dwpose download failed"
    fi

    # face-parse-bisent
    if [ ! -d "$MUSETALK_MODELS/face-parse-bisent" ] || [ -z "$(ls -A $MUSETALK_MODELS/face-parse-bisent 2>/dev/null)" ]; then
        echo "  [download] face-parse-bisent..."
        mkdir -p "$MUSETALK_MODELS/face-parse-bisent"
        # These are standard pretrained weights
        wget -q -O "$MUSETALK_MODELS/face-parse-bisent/resnet18-5c106cde.pth" \
            "https://download.pytorch.org/models/resnet18-5c106cde.pth" 2>/dev/null || true
        # Download 79999_iter.pth from HuggingFace mirror
        python -c "
from huggingface_hub import hf_hub_download
import shutil, os
path = hf_hub_download(repo_id='ManyOtherFunctions/face-parse-bisent', filename='79999_iter.pth')
dst = '$MUSETALK_MODELS/face-parse-bisent/79999_iter.pth'
shutil.copy2(path, dst)
if os.path.isfile(dst) and os.path.getsize(dst) > 1000:
    print('  [done] face-parse-bisent/79999_iter.pth')
else:
    print('  [warn] face-parse-bisent download may have failed')
" 2>/dev/null || echo "  [warn] face-parse-bisent HF download failed, trying gdown..."
        # Fallback: try gdown from Google Drive
        if [ ! -f "$MUSETALK_MODELS/face-parse-bisent/79999_iter.pth" ]; then
            python -c "
import gdown, os
output = '$MUSETALK_MODELS/face-parse-bisent/79999_iter.pth'
gdown.download(id='154JgKpzCPW82qINcVieuPH3fZ2e0P812', output=output, quiet=True)
if os.path.isfile(output) and os.path.getsize(output) > 1000:
    print('  [done] face-parse-bisent/79999_iter.pth via gdown')
else:
    print('  [FAIL] face-parse-bisent download failed')
" 2>/dev/null || echo "  [warn] gdown fallback also failed"
        fi
    fi

    # Cache downloaded models to Network Volume if available
    if [ "$HAS_VOLUME" = true ]; then
        echo "  Caching models to Network Volume for future cold starts..."
        mkdir -p "$VOLUME/models"
        for subdir in "${MUSETALK_MODEL_DIRS[@]}"; do
            src="$MUSETALK_MODELS/$subdir"
            dst="$VOLUME/models/$subdir"
            if [ -d "$src" ] && [ ! -L "$src" ] && [ ! -d "$dst" ]; then
                cp -r "$src" "$dst" 2>/dev/null && echo "  [cached] $subdir to volume" || true
            fi
        done
    fi
fi

# ── 3b. Create sd-vae symlink ────────────────────────────────────────────────
# MuseTalk's load_all_model() uses vae_type="sd-vae" → "models/sd-vae"
# but the actual model is downloaded as sd-vae-ft-mse
if [ -d "$MUSETALK_MODELS/sd-vae-ft-mse" ] && [ ! -e "$MUSETALK_MODELS/sd-vae" ]; then
    ln -sf "$MUSETALK_MODELS/sd-vae-ft-mse" "$MUSETALK_MODELS/sd-vae"
    echo "  [symlink] sd-vae -> sd-vae-ft-mse"
fi

# ── 4. Apply Runtime Patches ────────────────────────────────────────────────

echo ""
echo "=== Applying Runtime Patches ==="

# Patch 1: basicsr torchvision compatibility
BASICSR_DEG=$(python -c "
try:
    import basicsr, os
    print(os.path.join(os.path.dirname(basicsr.__file__), 'data', 'degradations.py'))
except:
    print('')
" 2>/dev/null)

if [ -n "$BASICSR_DEG" ] && [ -f "$BASICSR_DEG" ]; then
    if grep -q 'from torchvision.transforms.functional_tensor' "$BASICSR_DEG" 2>/dev/null; then
        sed -i 's/from torchvision.transforms.functional_tensor import rgb_to_grayscale/from torchvision.transforms.functional import rgb_to_grayscale/' "$BASICSR_DEG"
        echo "  [patched] basicsr degradations.py"
    else
        echo "  [ok] basicsr already patched"
    fi
fi

# Patch 2: MuseTalk VAE low_cpu_mem_usage fix
VAE_FILE="$APP_DIR/MuseTalk/musetalk/utils/vae.py"
if [ -f "$VAE_FILE" ]; then
    if grep -q "low_cpu_mem_usage" "$VAE_FILE"; then
        echo "  [ok] MuseTalk vae.py already patched"
    else
        sed -i 's/AutoencoderKL.from_pretrained(model_path)/AutoencoderKL.from_pretrained(model_path, low_cpu_mem_usage=False)/g' "$VAE_FILE"
        echo "  [patched] MuseTalk vae.py"
    fi
fi

# Patch 3: MuseTalk FaceParsing absolute path fix
FP_INIT="$APP_DIR/MuseTalk/musetalk/utils/face_parsing/__init__.py"
if [ -f "$FP_INIT" ]; then
    if grep -q "$APP_DIR/MuseTalk/models" "$FP_INIT"; then
        echo "  [ok] FaceParsing __init__.py already patched"
    else
        sed -i "s|'./models/face-parse-bisent/resnet18-5c106cde.pth'|'$APP_DIR/MuseTalk/models/face-parse-bisent/resnet18-5c106cde.pth'|g" "$FP_INIT"
        sed -i "s|'./models/face-parse-bisent/79999_iter.pth'|'$APP_DIR/MuseTalk/models/face-parse-bisent/79999_iter.pth'|g" "$FP_INIT"
        echo "  [patched] FaceParsing __init__.py"
    fi
fi

# ── 5. Create Required Directories ──────────────────────────────────────────

mkdir -p /tmp/aitherhub
mkdir -p "$WORKSPACE/source_faces"
mkdir -p "$WORKSPACE/tmp"

# ── 6. Verify Critical Paths ────────────────────────────────────────────────

echo ""
echo "=== Path Verification ==="
for dir in "$APP_DIR/facefusion" "$APP_DIR/MuseTalk"; do
    if [ -d "$dir" ]; then
        echo "  [OK] $dir"
    else
        echo "  [MISSING] $dir"
    fi
done

# Check FaceFusion models
FF_MODELS="$APP_DIR/facefusion/.assets/models"
if [ -d "$FF_MODELS" ]; then
    MODEL_COUNT=$(ls "$FF_MODELS/" 2>/dev/null | wc -l)
    echo "  [OK] FaceFusion models: $MODEL_COUNT files"
else
    echo "  [MISSING] FaceFusion models"
fi

# Check GFPGAN model
if [ -f "$APP_DIR/models/GFPGANv1.4.pth" ]; then
    echo "  [OK] GFPGAN model"
else
    echo "  [MISSING] GFPGAN model — downloading..."
    mkdir -p "$APP_DIR/models"
    wget -q -O "$APP_DIR/models/GFPGANv1.4.pth" \
        "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth" 2>/dev/null || true
fi

# ── 7. GPU Check ────────────────────────────────────────────────────────────

echo ""
echo "=== GPU Status ==="
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>/dev/null || echo "  No GPU detected"

python -c "
import torch
if torch.cuda.is_available():
    print(f'  PyTorch CUDA: {torch.cuda.get_device_name(0)}')
    print(f'  VRAM: {torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GB')
else:
    print('  WARNING: PyTorch cannot see GPU')
" 2>/dev/null || echo "  WARNING: PyTorch import failed"

echo ""
echo "=== Setup Complete ==="
echo "Starting RunPod Serverless handler..."
