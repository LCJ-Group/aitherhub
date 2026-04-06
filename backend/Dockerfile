# ──────────────────────────────────────────────────────────────
# AitherHub API — Production Dockerfile
# Multi-stage build: dependencies pre-installed → 10-30s startup
# ──────────────────────────────────────────────────────────────

# Stage 1: Build dependencies
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies for compiled packages (asyncpg, bcrypt, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && pip install --no-cache-dir --prefix=/install gunicorn

# ──────────────────────────────────────────────────────────────
# Stage 2: Production image (slim, no build tools)
# ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Install runtime dependencies only
# - ffmpeg + libass for subtitle burning
# - fonts-noto-cjk for Japanese/CJK subtitle rendering
# - fontconfig for font discovery
# - libpq5 for asyncpg/psycopg2 runtime
# - ca-certificates for HTTPS
# - curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libass9 \
    libass-dev \
    fonts-noto-cjk \
    fontconfig \
    libpq5 \
    ca-certificates \
    curl \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-built Python packages from builder stage
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application code
COPY alembic.ini .
COPY migrations/ ./migrations/
COPY app/ ./app/
COPY tests/ ./tests/

# Force English locale for consistent ffmpeg output
ENV LANG=C \
    LC_ALL=C \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Health check: verify API responds within 10s
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:8000/ || exit 1

# Run with gunicorn + uvicorn workers for production
# - 2 workers (suitable for B3: 4 cores, 7GB RAM)
# - 600s timeout for long-running video exports
# - 120s keep-alive for Azure load balancer
CMD ["python", "-m", "gunicorn", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "app.main:app", \
     "--workers", "2", \
     "--threads", "1", \
     "--timeout", "600", \
     "--graceful-timeout", "30", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--keep-alive", "120"]
