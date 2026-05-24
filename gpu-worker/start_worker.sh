#!/bin/bash
# GPU Worker API startup script for RunPod
# Ensures LD_LIBRARY_PATH includes cuDNN libraries

export PORT=11434
export WORKER_API_KEY="aitherhub-gpu-worker-secret-2024"
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH

cd /workspace

# Kill existing worker process
kill $(pgrep -f face_swap_worker_api.py) 2>/dev/null
sleep 1

# Start worker API
nohup python3 face_swap_worker_api.py > /var/log/worker_api.log 2>&1 &
echo "Worker API started on port $PORT (PID: $!)"
