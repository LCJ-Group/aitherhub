-- Migration: Add clip playlists feature
-- Purpose: Allow admins to create playlists (e.g., "お気に入り", "バグあり", "本番用")
--          and assign clips to multiple playlists for organization and filtering.

-- Playlists table
CREATE TABLE IF NOT EXISTS clip_playlists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    color TEXT DEFAULT '#6366f1',  -- Hex color for UI badge
    icon TEXT DEFAULT 'tag',       -- Icon name (lucide)
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Junction table: clip <-> playlist (many-to-many)
CREATE TABLE IF NOT EXISTS clip_playlist_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id UUID NOT NULL,
    playlist_id UUID NOT NULL REFERENCES clip_playlists(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(clip_id, playlist_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_clip_playlist_items_clip_id
    ON clip_playlist_items (clip_id);
CREATE INDEX IF NOT EXISTS idx_clip_playlist_items_playlist_id
    ON clip_playlist_items (playlist_id);
CREATE INDEX IF NOT EXISTS idx_clip_playlists_name
    ON clip_playlists (name);
