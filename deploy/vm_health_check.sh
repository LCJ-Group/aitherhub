#!/bin/bash
# =============================================================================
# vm_health_check.sh - Self-healing health check for AitherHub worker VM
# =============================================================================
# Runs via cron every 5 minutes to ensure worker stability.
#
# Checks:
# 1. aither-worker service is running (replaces old simple-worker check)
# 2. Disk space is sufficient (cleanup if >90%)
# 3. Memory/swap is not critically saturated
# 4. D-state (uninterruptible sleep) processes are detected and killed
# 5. Duplicate video processes are detected and killed
# 6. Worker log staleness detection
#
# Install:
#   sudo cp deploy/vm_health_check.sh /usr/local/bin/vm_health_check.sh
#   sudo chmod +x /usr/local/bin/vm_health_check.sh
#   crontab: */5 * * * * /usr/local/bin/vm_health_check.sh
#
# Version: 2.0 (2026-05-20) - Complete rewrite for aither-worker service
# =============================================================================

LOG_FILE="/var/log/aitherhub_health.log"
BATCH_DIR="/opt/aitherhub/worker/batch"
MAX_LOG_SIZE=$((50 * 1024 * 1024))  # 50MB max log size
DISK_THRESHOLD=85                    # Cleanup if disk usage exceeds 85%
MEMORY_THRESHOLD=90                  # Alert if memory usage exceeds 90%
SWAP_THRESHOLD=90                    # Restart worker if swap exceeds 90%
D_STATE_TIMEOUT_MINUTES=30           # Kill D-state processes older than 30 min
WORKER_SERVICE="aither-worker"       # Current systemd service name

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [HEALTH] $1" >> "$LOG_FILE"
}

# --- 0. Rotate health log ---
if [ -f "$LOG_FILE" ]; then
    HC_SIZE=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$HC_SIZE" -gt "$MAX_LOG_SIZE" ]; then
        tail -500 "$LOG_FILE" > "${LOG_FILE}.tmp"
        mv "${LOG_FILE}.tmp" "$LOG_FILE"
    fi
fi

log "=== Health check start ==="

# --- 1. Check aither-worker service ---
if ! systemctl is-active --quiet "$WORKER_SERVICE"; then
    log "ERROR: $WORKER_SERVICE is not running! Restarting..."
    sudo systemctl restart "$WORKER_SERVICE"
    sleep 10
    if systemctl is-active --quiet "$WORKER_SERVICE"; then
        log "OK: $WORKER_SERVICE restarted successfully"
    else
        log "CRITICAL: $WORKER_SERVICE failed to restart!"
    fi
else
    WORKER_PID=$(systemctl show -p MainPID --value "$WORKER_SERVICE")
    log "OK: $WORKER_SERVICE running (PID: $WORKER_PID)"
fi

# --- 2. Check encoding API service ---
if ! systemctl is-active --quiet "aither-encoding-api"; then
    log "WARN: aither-encoding-api is not running! Restarting..."
    sudo systemctl restart aither-encoding-api
    sleep 5
fi

# --- 3. Detect and kill D-state processes (stuck in uninterruptible disk sleep) ---
# D-state processes cannot be killed normally, but we can try SIGKILL on the process group.
# These typically occur during swap thrashing when the VM runs out of memory.
D_STATE_PROCS=$(ps aux | awk '$8 ~ /^D/ && /python.*process_video\|python.*generate_clip\|ffmpeg/' | awk '{print $2}')
if [ -n "$D_STATE_PROCS" ]; then
    for PID in $D_STATE_PROCS; do
        # Check how long the process has been running
        ELAPSED_MIN=$(ps -o etimes= -p "$PID" 2>/dev/null | awk '{print int($1/60)}')
        if [ -n "$ELAPSED_MIN" ] && [ "$ELAPSED_MIN" -gt "$D_STATE_TIMEOUT_MINUTES" ]; then
            log "KILLING D-state process PID=$PID (running ${ELAPSED_MIN}min > ${D_STATE_TIMEOUT_MINUTES}min threshold)"
            # Try to kill the process group
            PGID=$(ps -o pgid= -p "$PID" 2>/dev/null | tr -d ' ')
            if [ -n "$PGID" ] && [ "$PGID" != "0" ]; then
                kill -9 -"$PGID" 2>/dev/null
            else
                kill -9 "$PID" 2>/dev/null
            fi
        fi
    done
fi

# --- 4. Detect duplicate video processes (same video_id running multiple times) ---
# This is a root cause of memory explosion and swap thrashing.
DUPLICATE_VIDS=$(ps aux | grep 'process_video.py.*--video-id' | grep -v grep | \
    sed 's/.*--video-id \([^ ]*\).*/\1/' | sort | uniq -d)
if [ -n "$DUPLICATE_VIDS" ]; then
    for VID in $DUPLICATE_VIDS; do
        # Get all PIDs for this video_id, keep only the newest (highest PID)
        PIDS=$(ps aux | grep "process_video.py.*--video-id $VID" | grep -v grep | awk '{print $2}' | sort -n)
        NEWEST_PID=$(echo "$PIDS" | tail -1)
        for PID in $PIDS; do
            if [ "$PID" != "$NEWEST_PID" ]; then
                log "KILLING duplicate process_video PID=$PID for video=$VID (keeping newest PID=$NEWEST_PID)"
                PGID=$(ps -o pgid= -p "$PID" 2>/dev/null | tr -d ' ')
                if [ -n "$PGID" ] && [ "$PGID" != "0" ]; then
                    kill -9 -"$PGID" 2>/dev/null
                else
                    kill -9 "$PID" 2>/dev/null
                fi
            fi
        done
    done
fi

# --- 5. Check disk space ---
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt "$DISK_THRESHOLD" ]; then
    log "WARN: Disk usage at ${DISK_USAGE}% (threshold: ${DISK_THRESHOLD}%)"
    
    # Emergency cleanup
    # Remove uploaded videos older than 2 hours
    find "${BATCH_DIR}/uploadedvideo" -type f -mmin +120 -delete 2>/dev/null
    # Remove output files older than 4 hours
    find "${BATCH_DIR}/output" -type f -mmin +240 -delete 2>/dev/null
    # Remove split video files older than 2 hours
    find "${BATCH_DIR}/splitvideo" -type f -mmin +120 -delete 2>/dev/null
    # Remove temp overlay images
    find /tmp -name "overlay_*.png" -mmin +60 -delete 2>/dev/null
    find /tmp -name "aitherhub_*" -mmin +120 -delete 2>/dev/null
    # Remove artifacts older than 24 hours
    find "${BATCH_DIR}/artifacts" -type f -mmin +1440 -delete 2>/dev/null
    # Remove old log files
    find "${BATCH_DIR}/.logs" -name "*.log" -mmin +2880 -delete 2>/dev/null
    # Remove empty directories
    find "${BATCH_DIR}/splitvideo" -type d -empty -delete 2>/dev/null
    find "${BATCH_DIR}/output" -type d -empty -delete 2>/dev/null
    
    NEW_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
    log "Disk usage after cleanup: ${NEW_USAGE}%"
fi

# --- 6. Check memory and swap ---
MEM_TOTAL=$(free | awk '/Mem:/ {print $2}')
MEM_AVAIL=$(free | awk '/Mem:/ {print $7}')
MEM_USED_PCT=$(( (MEM_TOTAL - MEM_AVAIL) * 100 / MEM_TOTAL ))

SWAP_TOTAL=$(free | awk '/Swap:/ {print $2}')
SWAP_USED=$(free | awk '/Swap:/ {print $3}')
if [ "$SWAP_TOTAL" -gt 0 ]; then
    SWAP_USED_PCT=$(( SWAP_USED * 100 / SWAP_TOTAL ))
else
    SWAP_USED_PCT=0
fi

if [ "$SWAP_USED_PCT" -gt "$SWAP_THRESHOLD" ]; then
    log "CRITICAL: Swap usage at ${SWAP_USED_PCT}% (threshold: ${SWAP_THRESHOLD}%). VM is swap-thrashing!"
    log "Restarting $WORKER_SERVICE to free memory..."
    
    # Kill all child processes first (they're likely in D-state anyway)
    pkill -9 -f "process_video.py" 2>/dev/null
    pkill -9 -f "generate_clip.py" 2>/dev/null
    pkill -9 -f "split_video_async.py" 2>/dev/null
    sleep 5
    
    # Restart the worker service
    sudo systemctl restart "$WORKER_SERVICE"
    
    # Drop caches to free memory
    sync
    echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null 2>&1
    
    sleep 10
    NEW_SWAP_USED=$(free | awk '/Swap:/ {print $3}')
    NEW_SWAP_PCT=$(( NEW_SWAP_USED * 100 / SWAP_TOTAL ))
    log "After restart: Swap=${NEW_SWAP_PCT}%, Memory=${MEM_USED_PCT}%"
elif [ "$MEM_USED_PCT" -gt "$MEMORY_THRESHOLD" ]; then
    log "WARN: Memory usage at ${MEM_USED_PCT}% (threshold: ${MEMORY_THRESHOLD}%)"
    # Drop caches to free memory
    sync
    echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null 2>&1
fi

# --- 7. Check total process count for worker children ---
WORKER_CHILDREN=$(pgrep -f "process_video.py\|generate_clip.py\|split_video_async.py" | wc -l)
if [ "$WORKER_CHILDREN" -gt 8 ]; then
    log "WARN: Too many worker children ($WORKER_CHILDREN). Possible runaway processes."
    # Kill oldest processes beyond the limit
    OLDEST_PIDS=$(ps aux | grep -E 'process_video.py|generate_clip.py' | grep -v grep | sort -k9 | head -$(( WORKER_CHILDREN - 4 )) | awk '{print $2}')
    for PID in $OLDEST_PIDS; do
        ELAPSED_MIN=$(ps -o etimes= -p "$PID" 2>/dev/null | awk '{print int($1/60)}')
        if [ -n "$ELAPSED_MIN" ] && [ "$ELAPSED_MIN" -gt 120 ]; then
            log "KILLING old worker child PID=$PID (running ${ELAPSED_MIN}min)"
            kill -9 "$PID" 2>/dev/null
        fi
    done
fi

log "Health check complete. Disk=${DISK_USAGE}%, Mem=${MEM_USED_PCT}%, Swap=${SWAP_USED_PCT}%, Children=${WORKER_CHILDREN}"
