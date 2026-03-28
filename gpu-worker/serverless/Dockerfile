##############################################################################
# AitherHub GPU Worker — RunPod Serverless Docker Image (v4 Self-Contained)
#
# Multi-stage build:
#   Stage 1 (builder): Compile native extensions (insightface, chumpy, mmcv)
#   Stage 2 (runtime): Slim runtime + FaceFusion + MuseTalk + IMTalker + models
#
# This image is FULLY SELF-CONTAINED:
#   - FaceFusion code + essential models baked in
#   - MuseTalk code baked in (models downloaded at first cold start)
#   - IMTalker code baked in (models downloaded at first cold start)
#   - No Network Volume required (but supported if available)
#   - Pod stop/restart safe: Serverless workers auto-scale independently
#
# Base: nvidia/cuda 12.1 + Python 3.11
##############################################################################

# ═══════════════════════════════════════════════════════════════════════════
# Stage 1: Builder — compile native Python extensions
# ═══════════════════════════════════════════════════════════════════════════
FROM nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Tokyo

# System build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3.11-distutils \
    build-essential \
    g++ \
    cmake \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set python3.11 as default
RUN ln -sf /usr/bin/python3.11 /usr/local/bin/python && \
    ln -sf /usr/bin/python3.11 /usr/local/bin/python3 && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

RUN pip install --no-cache-dir --upgrade pip setuptools wheel numpy cython

# Install PyTorch (CUDA 12.1)
RUN pip install --no-cache-dir \
    torch==2.1.2 \
    torchvision==0.16.2 \
    torchaudio==2.1.2 \
    --index-url https://download.pytorch.org/whl/cu121

# Install insightface + chumpy (compiled extensions)
RUN pip install --no-cache-dir --no-build-isolation chumpy || \
    echo "WARNING: chumpy install failed (optional)"
RUN pip install --no-cache-dir insightface>=0.7.3

# Install mmcv pre-built binary (cu121 + torch2.1 + cp311)
RUN pip install --no-cache-dir \
    mmcv==2.1.0 -f https://download.openmmlab.com/mmcv/dist/cu121/torch2.1/index.html

# ═══════════════════════════════════════════════════════════════════════════
# Stage 2: Runtime — self-contained image
# ═══════════════════════════════════════════════════════════════════════════
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Tokyo

# Runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-distutils \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    git \
    git-lfs \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set python3.11 as default
RUN ln -sf /usr/bin/python3.11 /usr/local/bin/python && \
    ln -sf /usr/bin/python3.11 /usr/local/bin/python3 && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# ── Copy compiled packages from builder ────────────────────────────────────
COPY --from=builder /usr/local/lib/python3.11/dist-packages /usr/local/lib/python3.11/dist-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# ── Working Directory ────────────────────────────────────────────────────────
WORKDIR /app

# ── Install RunPod SDK ──────────────────────────────────────────────────────
RUN pip install --no-cache-dir runpod>=1.7.0

# ── Install remaining Python dependencies ─────────────────────────────────
RUN pip install --no-cache-dir \
    opencv-python-headless>=4.8.0 \
    Pillow>=10.0.0 \
    numpy==1.26.4 \
    librosa>=0.10.0 \
    soundfile>=0.12.0 \
    mmpose>=0.29.0 \
    mmdet>=3.2.0 \
    onnxruntime-gpu \
    diffusers==0.30.2 \
    huggingface_hub==0.30.2 \
    "transformers>=4.40.0,<5.0.0" \
    accelerate \
    safetensors \
    einops \
    omegaconf \
    tyro \
    dill \
    scipy \
    imageio \
    imageio-ffmpeg \
    lmdb \
    tqdm \
    rich \
    azure-storage-blob \
    boto3 \
    httpx>=0.27.0 \
    pydantic>=2.6.0 \
    python-multipart>=0.0.9 \
    gfpgan \
    basicsr==1.4.2 \
    mediapipe \
    face-alignment \
    yacs \
    timm \
    matplotlib \
    torchdiffeq \
    "av>=13.0.0,<14.0.0"

# ── Fix numpy version ────────────────────────────────────────────────────
RUN pip install --no-cache-dir --force-reinstall numpy==1.26.4

# ── Fix basicsr torchvision import ─────────────────────────────────────────
RUN BASICSR_DEG=$(python3 -c "import basicsr; import os; print(os.path.join(os.path.dirname(basicsr.__file__), 'data', 'degradations.py'))" 2>/dev/null) && \
    if [ -f "$BASICSR_DEG" ]; then \
        sed -i 's/from torchvision.transforms.functional_tensor import rgb_to_grayscale/from torchvision.transforms.functional import rgb_to_grayscale/' "$BASICSR_DEG" || true; \
    fi || true

# ═══════════════════════════════════════════════════════════════════════════
# Clone AI Tools (code only, models downloaded separately)
# ═══════════════════════════════════════════════════════════════════════════

# ── FaceFusion ────────────────────────────────────────────────────────────
RUN cd /app && \
    git clone --depth 1 https://github.com/facefusion/facefusion.git && \
    cd facefusion && \
    pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

# Download essential FaceFusion models (small enough to bake in)
RUN mkdir -p /app/facefusion/.assets/models && \
    cd /app/facefusion/.assets/models && \
    curl -sL "https://huggingface.co/facefusion/models/resolve/main/inswapper_128_fp16.onnx" -o inswapper_128_fp16.onnx && \
    curl -sL "https://huggingface.co/facefusion/models/resolve/main/inswapper_128.onnx" -o inswapper_128.onnx && \
    curl -sL "https://huggingface.co/facefusion/models/resolve/main/gfpgan_1.4.onnx" -o gfpgan_1.4.onnx && \
    curl -sL "https://huggingface.co/facefusion/models/resolve/main/yoloface_8n.onnx" -o yoloface_8n.onnx && \
    curl -sL "https://huggingface.co/facefusion/models/resolve/main/arcface_simswap.onnx" -o arcface_simswap.onnx && \
    curl -sL "https://huggingface.co/facefusion/models/resolve/main/2dfan4.onnx" -o 2dfan4.onnx && \
    curl -sL "https://huggingface.co/facefusion/models/resolve/main/face_landmarker_68_5.onnx" -o face_landmarker_68_5.onnx && \
    echo "FaceFusion models downloaded"

# ── MuseTalk ──────────────────────────────────────────────────────────────
RUN cd /app && \
    git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git

# ── MuseTalk Models (baked into image to avoid cold-start download failures) ──
# face-parse-bisent (CRITICAL: this was the root cause of worker crashes)
RUN mkdir -p /app/MuseTalk/models/face-parse-bisent && \
    wget -q -O /app/MuseTalk/models/face-parse-bisent/resnet18-5c106cde.pth \
        "https://download.pytorch.org/models/resnet18-5c106cde.pth" && \
    python3 -c "
from huggingface_hub import hf_hub_download
import shutil
path = hf_hub_download(repo_id='ManyOtherFunctions/face-parse-bisent', filename='79999_iter.pth')
shutil.copy2(path, '/app/MuseTalk/models/face-parse-bisent/79999_iter.pth')
print('[done] face-parse-bisent models')
" && \
    echo "face-parse-bisent models downloaded" && \
    ls -la /app/MuseTalk/models/face-parse-bisent/

# dwpose
RUN mkdir -p /app/MuseTalk/models/dwpose && \
    python3 -c "
from huggingface_hub import hf_hub_download
import shutil
path = hf_hub_download(repo_id='yzd-v/DWPose', filename='dw-ll_ucoco_384.onnx')
shutil.copy2(path, '/app/MuseTalk/models/dwpose/dw-ll_ucoco_384.onnx')
print('[done] dwpose')
" && \
    echo "dwpose model downloaded"

# sd-vae-ft-mse
RUN mkdir -p /app/MuseTalk/models/sd-vae-ft-mse && \
    python3 -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='stabilityai/sd-vae-ft-mse', local_dir='/app/MuseTalk/models/sd-vae-ft-mse')
print('[done] sd-vae-ft-mse')
" && \
    ln -sf /app/MuseTalk/models/sd-vae-ft-mse /app/MuseTalk/models/sd-vae && \
    echo "sd-vae-ft-mse downloaded + sd-vae symlink created"

# musetalk weights
RUN mkdir -p /app/MuseTalk/models/musetalk && \
    python3 -c "
from huggingface_hub import snapshot_download
import shutil, os
snapshot_download(repo_id='TMElyralab/MuseTalk', local_dir='/tmp/musetalk_hf', allow_patterns=['models/musetalk/*'])
src = '/tmp/musetalk_hf/models/musetalk'
if os.path.isdir(src):
    for f in os.listdir(src):
        shutil.copy2(os.path.join(src, f), '/app/MuseTalk/models/musetalk/')
print('[done] musetalk weights')
" && \
    rm -rf /tmp/musetalk_hf && \
    echo "musetalk weights downloaded"

# musetalkV15 weights
RUN mkdir -p /app/MuseTalk/models/musetalkV15 && \
    python3 -c "
from huggingface_hub import snapshot_download
import shutil, os
snapshot_download(repo_id='TMElyralab/MuseTalk', local_dir='/tmp/musetalk_hf_v15', allow_patterns=['musetalkV15/*'])
src = '/tmp/musetalk_hf_v15/musetalkV15'
if os.path.isdir(src):
    for f in os.listdir(src):
        shutil.copy2(os.path.join(src, f), '/app/MuseTalk/models/musetalkV15/')
print('[done] musetalkV15 weights')
" && \
    rm -rf /tmp/musetalk_hf_v15 && \
    echo "musetalkV15 weights downloaded"

# whisper model
RUN mkdir -p /app/MuseTalk/models/whisper && \
    python3 -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='openai/whisper-tiny', local_dir='/app/MuseTalk/models/whisper')
print('[done] whisper')
" && \
    echo "whisper model downloaded"

# Apply MuseTalk FaceParsing absolute path patch at build time
RUN FP_INIT="/app/MuseTalk/musetalk/utils/face_parsing/__init__.py" && \
    if [ -f "$FP_INIT" ]; then \
        sed -i "s|'./models/face-parse-bisent/resnet18-5c106cde.pth'|'/app/MuseTalk/models/face-parse-bisent/resnet18-5c106cde.pth'|g" "$FP_INIT" && \
        sed -i "s|'./models/face-parse-bisent/79999_iter.pth'|'/app/MuseTalk/models/face-parse-bisent/79999_iter.pth'|g" "$FP_INIT" && \
        echo "FaceParsing __init__.py patched with absolute paths"; \
    fi

# Apply MuseTalk vae.py low_cpu_mem_usage patch at build time
RUN VAE_FILE="/app/MuseTalk/musetalk/utils/vae.py" && \
    if [ -f "$VAE_FILE" ]; then \
        sed -i 's/AutoencoderKL.from_pretrained(vae_type)/AutoencoderKL.from_pretrained(vae_type, low_cpu_mem_usage=False)/g' "$VAE_FILE" && \
        echo "MuseTalk vae.py patched"; \
    fi

# Verify all critical models exist
RUN echo "=== Verifying MuseTalk models ===" && \
    ls -la /app/MuseTalk/models/face-parse-bisent/ && \
    ls -la /app/MuseTalk/models/dwpose/ && \
    ls /app/MuseTalk/models/sd-vae-ft-mse/ | head -5 && \
    ls /app/MuseTalk/models/musetalk/ | head -5 && \
    ls /app/MuseTalk/models/whisper/ | head -5 && \
    test -f /app/MuseTalk/models/face-parse-bisent/79999_iter.pth && \
    echo "ALL MUSETALK MODELS VERIFIED OK"

# ── IMTalker ──────────────────────────────────────────────────────────────
# Clone IMTalker repo (models are large, downloaded at first cold start via setup.sh)
RUN cd /app && \
    git clone --depth 1 https://github.com/bigai-nlco/IMTalker.git && \
    mkdir -p /app/IMTalker/checkpoints && \
    echo "IMTalker code cloned (models downloaded at cold start)"

# ── GFPGAN model ──────────────────────────────────────────────────────────
RUN mkdir -p /app/models && \
    wget -q -O /app/models/GFPGANv1.4.pth \
    "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth"

# ═══════════════════════════════════════════════════════════════════════════
# Copy Worker Code
# ═══════════════════════════════════════════════════════════════════════════
COPY gpu-worker/serverless/handler.py /app/handler.py
COPY gpu-worker/serverless/setup.sh /app/setup.sh
COPY gpu-worker/live_engine.py /app/live_engine.py
COPY gpu-worker/liveportrait_engine.py /app/liveportrait_engine.py
COPY gpu-worker/imtalker_generate_patch.py /app/imtalker_generate_patch.py

RUN chmod +x /app/setup.sh

# ── Apply IMTalker generate.py patch (fps fix + crop coords JSON) ─────────
# The patched version saves crop coordinates to JSON for v8 compositing
# and fixes numpy.float64 fps → int conversion
RUN if [ -d /app/IMTalker/generator ]; then \
        cp /app/imtalker_generate_patch.py /app/IMTalker/generator/generate.py && \
        echo "IMTalker generate.py patched"; \
    fi

# ── Create workspace symlinks ─────────────────────────────────────────────
# handler.py and live_engine.py expect tools at /workspace/*
# Create symlinks so both /workspace/ and /app/ paths work
RUN mkdir -p /workspace && \
    ln -sf /app/facefusion /workspace/facefusion && \
    ln -sf /app/MuseTalk /workspace/MuseTalk && \
    ln -sf /app/IMTalker /workspace/IMTalker && \
    ln -sf /app/models /workspace/models && \
    ln -sf /app/handler.py /workspace/handler.py && \
    ln -sf /app/live_engine.py /workspace/live_engine.py && \
    ln -sf /app/liveportrait_engine.py /workspace/liveportrait_engine.py && \
    ln -sf /app/imtalker_generate_patch.py /workspace/imtalker_generate_patch.py && \
    mkdir -p /workspace/source_faces /workspace/tmp /tmp/aitherhub

# ── Environment Variables ────────────────────────────────────────────────────
ENV WORKSPACE=/workspace
ENV FACEFUSION_DIR=/app/facefusion
ENV MUSETALK_DIR=/app/MuseTalk
ENV IMTALKER_DIR=/app/IMTalker
ENV TEMP_DIR=/tmp/aitherhub
ENV RUNPOD_VOLUME_PATH=/runpod-volume
ENV PYTHONPATH=/app:/app/MuseTalk:/app/IMTalker:/app/IMTalker/generator:/workspace
# Extend cold start timeout (model loading can take time on first request)
ENV RUNPOD_INIT_TIMEOUT=600

# ── Entrypoint ───────────────────────────────────────────────────────────────
CMD ["/bin/bash", "-c", "/app/setup.sh && python3 -u /app/handler.py"]
