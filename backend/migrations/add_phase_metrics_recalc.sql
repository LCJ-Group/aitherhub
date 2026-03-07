-- Migration: Add phase_metrics_recalc_log table + logic_version columns
-- Purpose: Support safe recalculation of derived phase metrics for existing videos.
--          Tracks recalculation history and logic version per video.
-- Date: 2026-03-08

-- ── 1. Recalculation audit log table ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS phase_metrics_recalc_log (
    id              BIGSERIAL PRIMARY KEY,
    video_id        VARCHAR(255) NOT NULL,
    triggered_by    VARCHAR(255),                          -- 'admin:user@example.com', 'cli:backfill', 'system:auto'
    mode            VARCHAR(20)  NOT NULL DEFAULT 'dry-run', -- 'dry-run' | 'execute'
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending', -- 'pending' | 'success' | 'error'
    logic_version   INTEGER      NOT NULL DEFAULT 1,       -- version of recalc logic used
    before_json     JSONB,                                 -- snapshot of phase metrics before recalc
    after_json      JSONB,                                 -- snapshot of phase metrics after recalc
    diff_json       JSONB,                                 -- computed diff between before and after
    logs_json       JSONB,                                 -- structured execution logs
    error_message   TEXT,                                  -- error details if status = 'error'
    duration_ms     INTEGER,                               -- execution time in milliseconds
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recalc_log_video_id ON phase_metrics_recalc_log (video_id);
CREATE INDEX IF NOT EXISTS idx_recalc_log_created  ON phase_metrics_recalc_log (created_at DESC);

-- ── 2. Add logic_version column to video_phases ──────────────────────────────

ALTER TABLE video_phases
ADD COLUMN IF NOT EXISTS phase_metrics_version_applied INTEGER DEFAULT NULL;

-- ── 3. Add last_recalculated_at column to videos ────────────────────────────

ALTER TABLE videos
ADD COLUMN IF NOT EXISTS phase_metrics_version_applied INTEGER DEFAULT NULL;

ALTER TABLE videos
ADD COLUMN IF NOT EXISTS last_recalculated_at TIMESTAMPTZ DEFAULT NULL;
