##############################################################################
# AitherHub GPU Worker — RunPod Serverless Docker Image
#
# All-in-one image with: MuseTalk, FaceFusion, IMTalker, LivePortrait
# Models are loaded from Network Volume at /runpod-volume/
#
# Base: nvidia/cuda 12.1 + Python 3.11 (NOT runpod/base which uses Python 3.13)
# This avoids the pkg_resources/setuptools incompatibility with Python 3.13
##############################################################################

FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Tokyo

# ── System Dependencies ──────────────────────────────────────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3.11-distutils \
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

# Set python3.11 as default and install pip
RUN ln -sf /usr/bin/python3.11 /usr/local/bin/python && \
    ln -sf /usr/bin/python3.11 /usr/local/bin/python3 && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

# Ensure setuptools and wheel are available
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# ── Working Directory ────────────────────────────────────────────────────────

WORKDIR /workspace

# ── Install RunPod SDK ──────────────────────────────────────────────────────

RUN pip install --no-cache-dir runpod>=1.7.0

# ── Install Core ML Dependencies ────────────────────────────────────────────

RUN pip install --no-cache-dir \
    torch>=2.0.0 \
    torchvision>=0.15.0 \
    torchaudio>=2.0.0 \
    --index-url https://download.pytorch.org/whl/cu121

# ── Install insightface (chumpy needs special handling) ─────────────────────

RUN pip install --no-cache-dir chumpy || true && \
    pip install --no-cache-dir insightface>=0.7.3

# ── Install mmcv (pre-built binary for CUDA 12.1 + torch 2.x) ──────────────

RUN pip install --no-cache-dir \
    mmcv==2.1.0 -f https://download.openmmlab.com/mmcv/dist/cu121/torch2.1/index.html || \
    pip install --no-cache-dir mmcv

# ── Install Python Dependencies ──────────────────────────────────────────────

COPY gpu-worker/serverless/requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r /workspace/requirements.txt

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

# ── Install HTTP client ─────────────────────────────────────────────────────

RUN pip install --no-cache-dir httpx>=0.27.0 pydantic>=2.6.0

# ── Copy Worker Code ────────────────────────────────────────────────────────

COPY gpu-worker/serverless/handler.py /workspace/handler.py
COPY gpu-worker/live_engine.py /workspace/live_engine.py
COPY gpu-worker/liveportrait_engine.py /workspace/liveportrait_engine.py
COPY gpu-worker/imtalker_generate_patch.py /workspace/imtalker_generate_patch.py

# ── Setup Script ─────────────────────────────────────────────────────────────

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

CMD ["/bin/bash", "-c", "/workspace/setup.sh && python -u /workspace/handler.py"]
