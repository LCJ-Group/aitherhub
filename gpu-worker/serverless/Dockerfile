##############################################################################
# AitherHub GPU Worker — RunPod Serverless Docker Image (Optimized v2)
#
# Multi-stage build for fast CI:
#   Stage 1 (builder): Compile native extensions (insightface, chumpy)
#   Stage 2 (runtime): Slim runtime with pre-built wheels + compiled packages
#
# MuseTalk, FaceFusion, IMTalker, LivePortrait code and models are loaded
# from RunPod Network Volume at /runpod-volume/ — NOT baked into the image.
# This keeps the Docker image small (~8-10GB) and build time under 30 min.
#
# Base: nvidia/cuda 12.1 runtime + Python 3.11
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

# Install PyTorch (CUDA 12.1) — needed for mmcv pre-built wheel compatibility
RUN pip install --no-cache-dir \
    torch==2.1.2 \
    torchvision==0.16.2 \
    torchaudio==2.1.2 \
    --index-url https://download.pytorch.org/whl/cu121

# Install insightface + chumpy (requires compilation)
RUN pip install --no-cache-dir chumpy && \
    pip install --no-cache-dir insightface>=0.7.3

# Install mmcv pre-built binary (cu121 + torch2.1 + cp311)
RUN pip install --no-cache-dir \
    mmcv==2.1.0 -f https://download.openmmlab.com/mmcv/dist/cu121/torch2.1/index.html

# ═══════════════════════════════════════════════════════════════════════════
# Stage 2: Runtime — slim image with only what's needed
# ═══════════════════════════════════════════════════════════════════════════
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Tokyo

# Runtime system dependencies only (no build tools)
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
WORKDIR /workspace

# ── Install RunPod SDK ──────────────────────────────────────────────────────
RUN pip install --no-cache-dir runpod>=1.7.0

# ── Install remaining Python dependencies (pure Python, no compilation) ─────
RUN pip install --no-cache-dir \
    opencv-python-headless>=4.8.0 \
    Pillow>=10.0.0 \
    numpy==1.26.4 \
    librosa>=0.10.0 \
    soundfile>=0.12.0 \
    mmpose>=0.29.0 \
    mmdet>=3.2.0 \
    onnxruntime-gpu \
    diffusers \
    transformers \
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
    yacs

# ── Fix numpy version (must be 1.x for onnxruntime/insightface compat) ────
RUN pip install --no-cache-dir --force-reinstall numpy==1.26.4

# ── Fix basicsr torchvision import ─────────────────────────────────────────
RUN BASICSR_DEG=$(python -c "import basicsr; import os; print(os.path.join(os.path.dirname(basicsr.__file__), 'data', 'degradations.py'))" 2>/dev/null) && \
    if [ -f "$BASICSR_DEG" ]; then \
        sed -i 's/from torchvision.transforms.functional_tensor import rgb_to_grayscale/from torchvision.transforms.functional import rgb_to_grayscale/' "$BASICSR_DEG" || true; \
    fi

# ── Copy Worker Code ────────────────────────────────────────────────────────
COPY gpu-worker/serverless/handler.py /workspace/handler.py
COPY gpu-worker/serverless/setup.sh /workspace/setup.sh
COPY gpu-worker/live_engine.py /workspace/live_engine.py
COPY gpu-worker/liveportrait_engine.py /workspace/liveportrait_engine.py
COPY gpu-worker/imtalker_generate_patch.py /workspace/imtalker_generate_patch.py

RUN chmod +x /workspace/setup.sh

# ── Environment Variables ────────────────────────────────────────────────────
ENV WORKSPACE=/workspace
ENV FACEFUSION_DIR=/runpod-volume/facefusion
ENV MUSETALK_DIR=/runpod-volume/MuseTalk
ENV IMTALKER_DIR=/runpod-volume/IMTalker
ENV TEMP_DIR=/tmp/aitherhub
ENV RUNPOD_VOLUME_PATH=/runpod-volume
ENV PYTHONPATH=/workspace:/runpod-volume/MuseTalk:/runpod-volume/IMTalker
# Extend cold start timeout (model loading can take time on first request)
ENV RUNPOD_INIT_TIMEOUT=600

# ── Entrypoint ───────────────────────────────────────────────────────────────
CMD ["/bin/bash", "-c", "/workspace/setup.sh && python -u /workspace/handler.py"]
