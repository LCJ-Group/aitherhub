-- Migration: Add unusable clip marking to video_clips
-- Purpose: Allow admins to mark clips as "unusable" with a reason,
--          so AI learning can exclude/reference them in future processing.

ALTER TABLE video_clips
    ADD COLUMN IF NOT EXISTS is_unusable BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS unusable_reason TEXT,
    ADD COLUMN IF NOT EXISTS unusable_at TIMESTAMPTZ;

-- Index for filtering unusable clips
CREATE INDEX IF NOT EXISTS idx_video_clips_is_unusable
    ON video_clips (is_unusable) WHERE is_unusable = TRUE;
