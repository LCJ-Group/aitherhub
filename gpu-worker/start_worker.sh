#!/bin/bash
# GPU Worker API startup script for RunPod (aitherhub-gpu pod)
# Auto-starts on pod boot via /root/.bashrc

export WORKER_API_KEY="aitherhub"
export WORKER_PORT=11434
export FACEFUSION_DIR=/workspace/facefusion
export SOURCE_FACE_DIR=/workspace/source_faces
export TEMP_DIR=/workspace/tmp
export MUSETALK_DIR=/workspace/MuseTalk
export IMTALKER_DIR=/workspace/IMTalker
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH

cd /workspace

# Kill existing worker process
if [ -f /workspace/worker.pid ]; then
    kill $(cat /workspace/worker.pid) 2>/dev/null
    sleep 2
fi

# Start worker API
nohup python3 worker_api.py > /workspace/worker.log 2>&1 &
echo $! > /workspace/worker.pid
echo "Worker API started on port $WORKER_PORT (PID: $!)"
