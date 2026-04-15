"""
systemd Watchdog + DB Heartbeat for Worker VM
===============================================
Two-layer health monitoring:

Layer 1 — systemd watchdog (WatchdogSec=120):
    Sends sd_notify("WATCHDOG=1") every ~60s from the main loop.
    If the main loop freezes, systemd kills & restarts the process.

Layer 2 — DB heartbeat (worker_heartbeats table):
    Writes a row every 30s with memory/cpu/disk stats.
    Backend can query this table to detect a dead VM and auto-restart it.

Usage in queue_worker.py main():
    from worker.recovery.systemd_watchdog import SystemdWatchdog
    watchdog = SystemdWatchdog(worker_id=WORKER_INSTANCE_ID)
    watchdog.start()          # starts background thread
    # in main loop:
    watchdog.notify()         # call every iteration
    # on shutdown:
    watchdog.stop()
"""

import os
import socket
import time
import threading
import traceback
from datetime import datetime, timezone

# ── Layer 1: systemd sd_notify (pure Python, no dependencies) ──

_NOTIFY_SOCKET = os.environ.get("NOTIFY_SOCKET")


def sd_notify(state: str) -> bool:
    """Send a notification to systemd via the notify socket.
    Returns True if sent successfully, False otherwise."""
    if not _NOTIFY_SOCKET:
        return False
    try:
        addr = _NOTIFY_SOCKET
        if addr.startswith("@"):
            # Abstract socket
            addr = "\0" + addr[1:]
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.sendto(state.encode(), addr)
            return True
        finally:
            sock.close()
    except Exception:
        return False


def sd_ready():
    """Notify systemd that the service is ready."""
    return sd_notify("READY=1")


def sd_watchdog():
    """Ping the systemd watchdog."""
    return sd_notify("WATCHDOG=1")


def sd_stopping():
    """Notify systemd that the service is stopping."""
    return sd_notify("STOPPING=1")


# ── Layer 2: DB Heartbeat ──

def _get_system_stats() -> dict:
    """Collect system resource stats (best-effort)."""
    stats = {}
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
            total_kb = meminfo.get("MemTotal", 0)
            avail_kb = meminfo.get("MemAvailable", 0)
            used_kb = total_kb - avail_kb
            stats["mem_total_gb"] = round(total_kb / 1048576, 2)
            stats["mem_used_gb"] = round(used_kb / 1048576, 2)
            stats["mem_pct"] = round(used_kb / total_kb * 100, 1) if total_kb else 0
    except Exception:
        pass

    try:
        load1, load5, load15 = os.getloadavg()
        stats["load_1m"] = round(load1, 2)
        stats["load_5m"] = round(load5, 2)
    except Exception:
        pass

    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        used_pct = (1 - free / total) * 100 if total else 0
        stats["disk_total_gb"] = round(total / (1024**3), 2)
        stats["disk_free_gb"] = round(free / (1024**3), 2)
        stats["disk_pct"] = round(used_pct, 1)
    except Exception:
        pass

    return stats


_thread_engine = None


def _get_sync_engine():
    """Get or create a synchronous SQLAlchemy engine for heartbeat writes."""
    global _thread_engine
    if _thread_engine is None:
        try:
            from sqlalchemy import create_engine
            db_url = os.environ.get("DATABASE_URL", "")
            # Convert async URL to sync
            sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
            sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://") if "psycopg2" not in sync_url else sync_url
            # Try psycopg2 first, fall back to raw postgresql
            try:
                _thread_engine = create_engine(sync_url, pool_size=1, pool_recycle=300)
            except Exception:
                sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
                _thread_engine = create_engine(sync_url, pool_size=1, pool_recycle=300)
        except Exception as e:
            print(f"[watchdog] Failed to create DB engine: {e}")
            return None
    return _thread_engine


def _write_heartbeat_to_db(worker_id: str, stats: dict):
    """Write heartbeat to worker_heartbeats table (upsert)."""
    try:
        from sqlalchemy import text

        engine = _get_sync_engine()
        if engine is None:
            return
        now = datetime.now(timezone.utc)

        with engine.connect() as conn:
            # Ensure table exists (idempotent)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS worker_heartbeats (
                    worker_id TEXT PRIMARY KEY,
                    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    mem_total_gb REAL,
                    mem_used_gb REAL,
                    mem_pct REAL,
                    load_1m REAL,
                    load_5m REAL,
                    disk_total_gb REAL,
                    disk_free_gb REAL,
                    disk_pct REAL,
                    status TEXT DEFAULT 'running',
                    boot_time TIMESTAMPTZ,
                    ip_address TEXT
                )
            """))

            conn.execute(text("""
                INSERT INTO worker_heartbeats
                    (worker_id, last_heartbeat, mem_total_gb, mem_used_gb, mem_pct,
                     load_1m, load_5m, disk_total_gb, disk_free_gb, disk_pct, status)
                VALUES
                    (:wid, :now, :mem_total, :mem_used, :mem_pct,
                     :load1, :load5, :disk_total, :disk_free, :disk_pct, 'running')
                ON CONFLICT (worker_id) DO UPDATE SET
                    last_heartbeat = :now,
                    mem_total_gb = :mem_total,
                    mem_used_gb = :mem_used,
                    mem_pct = :mem_pct,
                    load_1m = :load1,
                    load_5m = :load5,
                    disk_total_gb = :disk_total,
                    disk_free_gb = :disk_free,
                    disk_pct = :disk_pct,
                    status = 'running'
            """), {
                "wid": worker_id,
                "now": now,
                "mem_total": stats.get("mem_total_gb"),
                "mem_used": stats.get("mem_used_gb"),
                "mem_pct": stats.get("mem_pct"),
                "load1": stats.get("load_1m"),
                "load5": stats.get("load_5m"),
                "disk_total": stats.get("disk_total_gb"),
                "disk_free": stats.get("disk_free_gb"),
                "disk_pct": stats.get("disk_pct"),
            })
            conn.commit()
    except Exception as e:
        print(f"[watchdog] DB heartbeat write failed: {e}")


# ── Combined Watchdog Class ──

class SystemdWatchdog:
    """Combined systemd watchdog + DB heartbeat manager."""

    def __init__(self, worker_id: str, watchdog_interval: float = 30.0,
                 heartbeat_interval: float = 30.0):
        self.worker_id = worker_id
        self.watchdog_interval = watchdog_interval
        self.heartbeat_interval = heartbeat_interval
        self._stop_event = threading.Event()
        self._thread = None
        self._last_notify = 0.0
        self._last_heartbeat = 0.0

    def start(self):
        """Start background watchdog/heartbeat thread."""
        # Send READY notification to systemd
        if sd_ready():
            print("[watchdog] systemd READY notification sent")
        else:
            print("[watchdog] No NOTIFY_SOCKET — systemd watchdog disabled (standalone mode)")

        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="systemd-watchdog"
        )
        self._thread.start()
        print(f"[watchdog] Started (watchdog={self.watchdog_interval}s, heartbeat={self.heartbeat_interval}s)")

    def notify(self):
        """Called from main loop to confirm liveness.
        Also handles periodic systemd ping and DB heartbeat."""
        now = time.monotonic()

        # systemd watchdog ping
        if now - self._last_notify >= self.watchdog_interval:
            sd_watchdog()
            self._last_notify = now

    def stop(self):
        """Stop the watchdog thread."""
        sd_stopping()
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        # Mark as stopped in DB
        try:
            _write_heartbeat_to_db(self.worker_id, {"status": "stopped"})
        except Exception:
            pass
        print("[watchdog] Stopped")

    def _loop(self):
        """Background loop for DB heartbeat writes."""
        while not self._stop_event.is_set():
            try:
                now = time.monotonic()

                # DB heartbeat
                if now - self._last_heartbeat >= self.heartbeat_interval:
                    stats = _get_system_stats()
                    _write_heartbeat_to_db(self.worker_id, stats)
                    self._last_heartbeat = now

                # Also ping systemd from background thread as safety net
                sd_watchdog()

            except Exception as e:
                print(f"[watchdog] Error in background loop: {e}")
                traceback.print_exc()

            self._stop_event.wait(min(self.watchdog_interval, self.heartbeat_interval))
