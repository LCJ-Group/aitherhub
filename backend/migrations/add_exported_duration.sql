-- Add exported_duration column to video_clips table
-- This stores the actual duration of the AI-generated clip (after silence trimming etc.)
-- Used for auto-matching TikTok tracked videos to AI-generated clips
ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS exported_duration FLOAT;

-- Add index for performance tracking auto-match queries
CREATE INDEX IF NOT EXISTS idx_video_clips_exported_duration
    ON video_clips (exported_duration)
    WHERE exported_url IS NOT NULL AND exported_duration IS NOT NULL;
