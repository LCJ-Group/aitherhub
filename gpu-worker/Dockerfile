##############################################################################
# FaceFusion GPU Worker — Docker Image
#
# Base: NVIDIA CUDA 12.1 + Ubuntu 22.04
# Includes: FaceFusion 3.x, FFmpeg, v4l2loopback, FastAPI worker
#
# Build:
#   docker build -t aitherhub/face-swap-worker:latest .
#
# Run:
#   docker run --gpus all -p 8000:8000 \
#     -e WORKER_API_KEY=your-secret-key \
#     aitherhub/face-swap-worker:latest
##############################################################################

FROM nvidia/cuda:12.1.0-devel-ubuntu22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Tokyo

# ── System Dependencies ──────────────────────────────────────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    python3-venv \
    git \
    git-lfs \
    ffmpeg \
    v4l2loopback-dkms \
    v4l2loopback-utils \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working Directory ────────────────────────────────────────────────────────

WORKDIR /workspace

# ── FaceFusion Installation ──────────────────────────────────────────────────

RUN git clone https://github.com/facefusion/facefusion.git /workspace/facefusion

WORKDIR /workspace/facefusion

# Install FaceFusion with CUDA support
RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir -r requirements.txt && \
    pip3 install --no-cache-dir onnxruntime-gpu

# Pre-download models for faster startup
# (Models will be downloaded on first run if not present)
RUN python3 facefusion.py force-download || true

# ── Worker API Dependencies ──────────────────────────────────────────────────

WORKDIR /workspace

COPY requirements.txt /workspace/requirements.txt
RUN pip3 install --no-cache-dir -r /workspace/requirements.txt

# ── Worker API Code ──────────────────────────────────────────────────────────

COPY worker_api.py /workspace/worker_api.py
COPY entrypoint.sh /workspace/entrypoint.sh
RUN chmod +x /workspace/entrypoint.sh

# ── Create Directories ───────────────────────────────────────────────────────

RUN mkdir -p /workspace/source_faces /workspace/tmp

# ── Environment Variables ────────────────────────────────────────────────────

ENV FACEFUSION_DIR=/workspace/facefusion
ENV SOURCE_FACE_DIR=/workspace/source_faces
ENV TEMP_DIR=/workspace/tmp
ENV WORKER_PORT=8000
ENV WORKER_API_KEY=change-me-in-production

# ── Expose Port ──────────────────────────────────────────────────────────────

EXPOSE 8000

# ── Health Check ─────────────────────────────────────────────────────────────

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf -H "X-Api-Key: ${WORKER_API_KEY}" http://localhost:8000/api/health || exit 1

# ── Entrypoint ───────────────────────────────────────────────────────────────

ENTRYPOINT ["/workspace/entrypoint.sh"]
