##############################################################################
# AitherHub GPU Worker — RunPod Serverless Docker Image
#
# All-in-one image with: MuseTalk, FaceFusion, IMTalker, LivePortrait
# Models are loaded from Network Volume at /runpod-volume/
#
# Build:
#   docker build -t aitherhub/gpu-serverless:latest -f serverless/Dockerfile .
#
# Test locally:
#   docker run --gpus all -p 8000:8000 aitherhub/gpu-serverless:latest
##############################################################################

FROM runpod/base:0.6.3-cuda12.1.0

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Tokyo

# ── System Dependencies ──────────────────────────────────────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-lfs \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python Setup ─────────────────────────────────────────────────────────────

# Set python3.11 as default
RUN ln -sf $(which python3.11) /usr/local/bin/python && \
    ln -sf $(which python3.11) /usr/local/bin/python3

# ── Working Directory ────────────────────────────────────────────────────────

WORKDIR /workspace

# ── Install Python Dependencies ──────────────────────────────────────────────

# First ensure setuptools is available (required by mmcv build)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install mmcv pre-built binary separately (avoid building from source)
RUN pip install --no-cache-dir mmcv==2.1.0 -f https://download.openmmlab.com/mmcv/dist/cu121/torch2.1/index.html || \
    pip install --no-cache-dir mmcv-lite || \
    echo 'mmcv pre-built not available, will install from requirements'

# Install remaining dependencies
COPY gpu-worker/serverless/requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r /workspace/requirements.txt || \
    (pip install --no-cache-dir setuptools && pip install --no-cache-dir -r /workspace/requirements.txt)

# ── Install FaceFusion ───────────────────────────────────────────────────────

RUN git clone https://github.com/facefusion/facefusion.git /workspace/facefusion && \
    cd /workspace/facefusion && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir onnxruntime-gpu && \
    python facefusion.py force-download || true

# ── Install MuseTalk ─────────────────────────────────────────────────────────

RUN git clone https://github.com/TMElyralab/MuseTalk.git /workspace/MuseTalk && \
    cd /workspace/MuseTalk && \
    pip install --no-cache-dir -r requirements.txt || true

# ── Install IMTalker Dependencies ────────────────────────────────────────────

# IMTalker will be cloned/linked from network volume if available
RUN pip install --no-cache-dir \
    diffusers \
    transformers \
    accelerate \
    safetensors \
    einops \
    omegaconf

# ── Install LivePortrait Dependencies ────────────────────────────────────────

RUN pip install --no-cache-dir \
    tyro \
    dill \
    scipy \
    imageio \
    imageio-ffmpeg \
    lmdb \
    tqdm \
    rich

# ── Install Azure & S3 SDKs ─────────────────────────────────────────────────

RUN pip install --no-cache-dir \
    azure-storage-blob \
    boto3

# ── Copy Worker Code ────────────────────────────────────────────────────────

COPY gpu-worker/serverless/handler.py /workspace/handler.py
COPY gpu-worker/live_engine.py /workspace/live_engine.py
COPY gpu-worker/liveportrait_engine.py /workspace/liveportrait_engine.py
COPY gpu-worker/imtalker_generate_patch.py /workspace/imtalker_generate_patch.py

# ── Setup Script ─────────────────────────────────────────────────────────────
# This script runs at container start to link network volume models

COPY gpu-worker/serverless/setup.sh /workspace/setup.sh
RUN chmod +x /workspace/setup.sh

# ── Environment Variables ────────────────────────────────────────────────────

ENV WORKSPACE=/workspace
ENV FACEFUSION_DIR=/workspace/facefusion
ENV MUSETALK_DIR=/workspace/MuseTalk
ENV IMTALKER_DIR=/workspace/IMTalker
ENV TEMP_DIR=/tmp/aitherhub
ENV RUNPOD_VOLUME_PATH=/runpod-volume
ENV PYTHONPATH=/workspace:/workspace/MuseTalk:/workspace/IMTalker

# ── Entrypoint ───────────────────────────────────────────────────────────────
# setup.sh links network volume models, then starts the handler

CMD ["/bin/bash", "-c", "/workspace/setup.sh && python -u /workspace/handler.py"]
