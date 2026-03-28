#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# AitherHub GPU Worker — Serverless Setup Script (v2)
#
# Runs at container start (cold start) to:
#   1. Link AI tools and models from Network Volume (/runpod-volume/)
#   2. Apply runtime patches for compatibility
#   3. Verify critical paths and GPU
#   4. Hand off to handler.py
#
# Network Volume Layout (must be pre-populated):
#   /runpod-volume/
#   ├── facefusion/          — FaceFusion repo + models
#   ├── MuseTalk/            — MuseTalk repo + models
#   ├── IMTalker/            — IMTalker repo + checkpoints
#   ├── FasterLivePortrait/  — LivePortrait repo + models
#   └── models/              — Shared model files (GFPGAN, etc.)
# ──────────────────────────────────────────────────────────────────────────────

VOLUME="${RUNPOD_VOLUME_PATH:-/runpod-volume}"
WORKSPACE="/workspace"

echo "=== AitherHub Serverless Setup v2 ==="
echo "Timestamp: $(date)"
echo "Volume path: $VOLUME"
echo "Workspace: $WORKSPACE"

# ── 1. Network Volume Check ─────────────────────────────────────────────────

if [ -d "$VOLUME" ] && [ "$(ls -A $VOLUME 2>/dev/null)" ]; then
    echo "[OK] Network volume found and not empty"
else
    echo "[WARNING] Network volume not found or empty at $VOLUME"
    echo "  AI tools (MuseTalk, FaceFusion, etc.) will NOT be available."
    echo "  Please attach a Network Volume with pre-populated AI tools."
fi

# ── 2. Symlink AI Tools from Network Volume ─────────────────────────────────

link_if_exists() {
    local src="$1"
    local dst="$2"
    if [ -d "$src" ]; then
        # Remove existing (file, symlink, or empty dir)
        rm -rf "$dst" 2>/dev/null || true
        ln -sf "$src" "$dst"
        echo "  [linked] $dst -> $src"
    else
        echo "  [skip] $src not found"
    fi
}

echo ""
echo "=== Linking AI Tools ==="

# Main AI tool directories
link_if_exists "$VOLUME/facefusion" "$WORKSPACE/facefusion"
link_if_exists "$VOLUME/MuseTalk" "$WORKSPACE/MuseTalk"
link_if_exists "$VOLUME/IMTalker" "$WORKSPACE/IMTalker"
link_if_exists "$VOLUME/FasterLivePortrait" "$WORKSPACE/FasterLivePortrait"

# Shared models directory
link_if_exists "$VOLUME/models" "$WORKSPACE/models"

# ── 3. MuseTalk Model Symlinks (if models are in separate location) ──────────

if [ -d "$VOLUME/models/musetalk" ] && [ -d "$WORKSPACE/MuseTalk" ]; then
    echo ""
    echo "=== Linking MuseTalk Models ==="
    mkdir -p "$WORKSPACE/MuseTalk/models"

    for subdir in dwpose face-parse-bisenet face-parse-bisent whisper sd-vae-ft-mse musetalk; do
        if [ -d "$VOLUME/models/$subdir" ]; then
            link_if_exists "$VOLUME/models/$subdir" "$WORKSPACE/MuseTalk/models/$subdir"
        fi
    done
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
        echo "  [patched] basicsr degradations.py (torchvision compat)"
    else
        echo "  [ok] basicsr already patched"
    fi
fi

# Patch 2: MuseTalk VAE low_cpu_mem_usage fix
VAE_FILE="$WORKSPACE/MuseTalk/musetalk/utils/vae.py"
if [ -f "$VAE_FILE" ]; then
    if grep -q "low_cpu_mem_usage" "$VAE_FILE"; then
        echo "  [ok] MuseTalk vae.py already patched"
    else
        sed -i 's/AutoencoderKL.from_pretrained(model_path)/AutoencoderKL.from_pretrained(model_path, low_cpu_mem_usage=False)/g' "$VAE_FILE"
        echo "  [patched] MuseTalk vae.py (low_cpu_mem_usage=False)"
    fi
fi

# Patch 3: MuseTalk FaceParsing absolute path fix
FP_INIT="$WORKSPACE/MuseTalk/musetalk/utils/face_parsing/__init__.py"
if [ -f "$FP_INIT" ]; then
    if grep -q "$WORKSPACE/MuseTalk/models" "$FP_INIT"; then
        echo "  [ok] FaceParsing __init__.py already patched"
    else
        sed -i "s|'./models/face-parse-bisent/resnet18-5c106cde.pth'|'$WORKSPACE/MuseTalk/models/face-parse-bisent/resnet18-5c106cde.pth'|g" "$FP_INIT"
        sed -i "s|'./models/face-parse-bisent/79999_iter.pth'|'$WORKSPACE/MuseTalk/models/face-parse-bisent/79999_iter.pth'|g" "$FP_INIT"
        echo "  [patched] FaceParsing __init__.py (absolute paths)"
    fi
fi

# Patch 4: JoyVASA weights_only fix for PyTorch 2.6+
JOYVASA_PIPELINE="$WORKSPACE/FasterLivePortrait/src/pipelines/joyvasa_audio_to_motion_pipeline.py"
if [ -f "$JOYVASA_PIPELINE" ]; then
    if grep -q 'torch.load(motion_model_path, map_location="cpu")' "$JOYVASA_PIPELINE" && \
       ! grep -q 'weights_only=False' "$JOYVASA_PIPELINE"; then
        sed -i 's/torch.load(motion_model_path, map_location="cpu")/torch.load(motion_model_path, map_location="cpu", weights_only=False)/' "$JOYVASA_PIPELINE"
        echo "  [patched] JoyVASA pipeline (weights_only=False)"
    else
        echo "  [ok] JoyVASA pipeline"
    fi
fi

# ── 5. Create Required Directories ──────────────────────────────────────────

mkdir -p /tmp/aitherhub
mkdir -p "$WORKSPACE/source_faces"
mkdir -p "$WORKSPACE/tmp"

# ── 6. Verify Critical Paths ────────────────────────────────────────────────

echo ""
echo "=== Path Verification ==="
for dir in "$WORKSPACE/facefusion" "$WORKSPACE/MuseTalk" "$WORKSPACE/IMTalker" "$WORKSPACE/FasterLivePortrait"; do
    if [ -d "$dir" ]; then
        echo "  [OK] $dir"
    else
        echo "  [MISSING] $dir"
    fi
done

# Check for key model files
echo ""
echo "=== Model Verification ==="
GFPGAN_MODEL="$WORKSPACE/models/GFPGANv1.4.pth"
if [ -f "$GFPGAN_MODEL" ]; then
    echo "  [OK] GFPGAN model"
else
    echo "  [MISSING] GFPGAN model at $GFPGAN_MODEL"
    # Try to download if missing
    mkdir -p "$WORKSPACE/models"
    wget -q -O "$GFPGAN_MODEL" "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth" 2>/dev/null && \
        echo "  [downloaded] GFPGAN model" || echo "  [failed] Could not download GFPGAN model"
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
