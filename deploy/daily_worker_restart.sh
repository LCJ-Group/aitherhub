#!/bin/bash
# =============================================================================
# daily_worker_restart.sh - Daily scheduled restart for AitherHub worker
# =============================================================================
# Runs daily at 18:00 UTC (03:00 JST) via cron to:
# 1. Gracefully stop the worker (waits for active jobs to finish)
# 2. Clean up temp files, swap, and caches
# 3. Restart the worker fresh
#
# This prevents gradual memory leaks and swap accumulation from causing
# D-state process buildup over time.
#
# Install:
#   sudo cp deploy/daily_worker_restart.sh /usr/local/bin/daily_worker_restart.sh
#   sudo chmod +x /usr/local/bin/daily_worker_restart.sh
#   crontab: 0 18 * * * /usr/local/bin/daily_worker_restart.sh
#
# Version: 1.0 (2026-05-20)
# =============================================================================

LOG_FILE="/var/log/aitherhub_daily_restart.log"
BATCH_DIR="/opt/aitherhub/worker/batch"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [DAILY] $1" >> "$LOG_FILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [DAILY] $1"
}

log "=== Daily worker restart BEGIN ==="

# --- 1. Record pre-restart state ---
SWAP_BEFORE=$(free | awk '/Swap:/ {print $3}')
MEM_BEFORE=$(free | awk '/Mem:/ {print $3}')
CHILDREN_BEFORE=$(pgrep -f "process_video.py\|generate_clip.py\|split_video_async.py" | wc -l)
log "Pre-restart: Swap=${SWAP_BEFORE}kB, Mem=${MEM_BEFORE}kB, Children=${CHILDREN_BEFORE}"

# --- 2. Stop worker gracefully (TimeoutStopSec=300 in service file) ---
log "Stopping aither-worker (graceful, up to 300s)..."
sudo systemctl stop aither-worker
sleep 5

# --- 3. Kill any remaining child processes ---
pkill -9 -f "process_video.py" 2>/dev/null
pkill -9 -f "generate_clip.py" 2>/dev/null
pkill -9 -f "split_video_async.py" 2>/dev/null
pkill -9 -f "ffmpeg.*uploadedvideo\|ffmpeg.*splitvideo\|ffmpeg.*output" 2>/dev/null
sleep 3

# --- 4. Clean up temp files ---
log "Cleaning temp files..."
# Overlay images in /tmp
find /tmp -name "overlay_*.png" -delete 2>/dev/null
find /tmp -name "aitherhub_*" -delete 2>/dev/null
# Old uploaded videos (>4 hours)
find "${BATCH_DIR}/uploadedvideo" -type f -mmin +240 -delete 2>/dev/null
# Old split videos (>4 hours)
find "${BATCH_DIR}/splitvideo" -type f -mmin +240 -delete 2>/dev/null
# Old output files (>6 hours)
find "${BATCH_DIR}/output" -type f -mmin +360 -delete 2>/dev/null
# Empty directories
find "${BATCH_DIR}/splitvideo" -type d -empty -delete 2>/dev/null
find "${BATCH_DIR}/output" -type d -empty -delete 2>/dev/null
# Old log files (>7 days)
find "${BATCH_DIR}/.logs" -name "*.log" -mtime +7 -delete 2>/dev/null

# --- 5. Clear swap and caches ---
log "Clearing swap and caches..."
sync
echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null 2>&1
# Turn swap off and on to clear it (only if no critical processes)
if [ "$(pgrep -f 'process_video.py\|generate_clip.py' | wc -l)" -eq 0 ]; then
    sudo swapoff -a 2>/dev/null && sudo swapon -a 2>/dev/null
    log "Swap cleared"
else
    log "Skipping swap clear (active processes still running)"
fi

# --- 6. Restart worker ---
log "Starting aither-worker..."
sudo systemctl start aither-worker
sleep 10

# --- 7. Verify restart ---
if systemctl is-active --quiet aither-worker; then
    SWAP_AFTER=$(free | awk '/Swap:/ {print $3}')
    MEM_AFTER=$(free | awk '/Mem:/ {print $3}')
    log "OK: aither-worker restarted. Swap: ${SWAP_BEFORE}kB → ${SWAP_AFTER}kB, Mem: ${MEM_BEFORE}kB → ${MEM_AFTER}kB"
else
    log "CRITICAL: aither-worker failed to start after daily restart!"
    # Try one more time
    sleep 5
    sudo systemctl start aither-worker
fi

# --- 8. Also restart encoding API ---
sudo systemctl restart aither-encoding-api
log "aither-encoding-api restarted"

log "=== Daily worker restart DONE ==="

# --- 9. Rotate this log ---
if [ -f "$LOG_FILE" ]; then
    LOG_SIZE=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$LOG_SIZE" -gt 10485760 ]; then  # 10MB
        tail -200 "$LOG_FILE" > "${LOG_FILE}.tmp"
        mv "${LOG_FILE}.tmp" "$LOG_FILE"
    fi
fi
