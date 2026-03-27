#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# AitherHub GPU Worker — Serverless Setup Script
#
# Runs at container start to:
#   1. Link models from Network Volume (/runpod-volume/) if available
#   2. Verify critical paths
#   3. Start the handler
# ──────────────────────────────────────────────────────────────────────────────

set -e

VOLUME="/runpod-volume"
WORKSPACE="/workspace"

echo "=== AitherHub Serverless Setup ==="
echo "Timestamp: $(date)"
echo "Volume path: $VOLUME"
echo "Workspace: $WORKSPACE"

# ── Link Network Volume Models ───────────────────────────────────────────────

if [ -d "$VOLUME" ]; then
    echo "Network volume found at $VOLUME"

    # Link MuseTalk models
    if [ -d "$VOLUME/models/musetalk" ]; then
        echo "Linking MuseTalk models from network volume..."
        mkdir -p "$WORKSPACE/MuseTalk/models"
        ln -sf "$VOLUME/models/musetalk/"* "$WORKSPACE/MuseTalk/models/" 2>/dev/null || true
    fi

    # Link FaceFusion models
    if [ -d "$VOLUME/models/facefusion" ]; then
        echo "Linking FaceFusion models from network volume..."
        mkdir -p "$WORKSPACE/facefusion/.assets/models"
        ln -sf "$VOLUME/models/facefusion/"* "$WORKSPACE/facefusion/.assets/models/" 2>/dev/null || true
    fi

    # Link IMTalker
    if [ -d "$VOLUME/IMTalker" ]; then
        echo "Linking IMTalker from network volume..."
        ln -sf "$VOLUME/IMTalker" "$WORKSPACE/IMTalker" 2>/dev/null || true
    fi

    # Link LivePortrait models
    if [ -d "$VOLUME/models/liveportrait" ]; then
        echo "Linking LivePortrait models from network volume..."
        mkdir -p "$WORKSPACE/liveportrait_models"
        ln -sf "$VOLUME/models/liveportrait/"* "$WORKSPACE/liveportrait_models/" 2>/dev/null || true
    fi

    # Link MuseTalk v1.5 specific models
    if [ -d "$VOLUME/models/dwpose" ]; then
        echo "Linking DWPose models..."
        mkdir -p "$WORKSPACE/MuseTalk/models/dwpose"
        ln -sf "$VOLUME/models/dwpose/"* "$WORKSPACE/MuseTalk/models/dwpose/" 2>/dev/null || true
    fi

    if [ -d "$VOLUME/models/face-parse-bisenet" ]; then
        echo "Linking face-parse-bisenet models..."
        mkdir -p "$WORKSPACE/MuseTalk/models/face-parse-bisenet"
        ln -sf "$VOLUME/models/face-parse-bisenet/"* "$WORKSPACE/MuseTalk/models/face-parse-bisenet/" 2>/dev/null || true
    fi

    if [ -d "$VOLUME/models/whisper" ]; then
        echo "Linking Whisper models..."
        mkdir -p "$WORKSPACE/MuseTalk/models/whisper"
        ln -sf "$VOLUME/models/whisper/"* "$WORKSPACE/MuseTalk/models/whisper/" 2>/dev/null || true
    fi

    echo "Network volume linking complete"
else
    echo "WARNING: No network volume found at $VOLUME"
    echo "Models must be included in the Docker image or downloaded at runtime"
fi

# ── Create Required Directories ──────────────────────────────────────────────

mkdir -p /tmp/aitherhub
mkdir -p "$WORKSPACE/source_faces"
mkdir -p "$WORKSPACE/tmp"

# ── Verify Critical Paths ────────────────────────────────────────────────────

echo ""
echo "=== Path Verification ==="
for dir in "$WORKSPACE/facefusion" "$WORKSPACE/MuseTalk" "$WORKSPACE/IMTalker"; do
    if [ -d "$dir" ]; then
        echo "  OK: $dir"
    else
        echo "  MISSING: $dir"
    fi
done

# ── GPU Check ────────────────────────────────────────────────────────────────

echo ""
echo "=== GPU Status ==="
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>/dev/null || echo "  No GPU detected"

echo ""
echo "=== Setup Complete ==="
echo "Starting handler..."
