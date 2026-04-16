import React, { useState, useRef, useCallback, useEffect, useMemo } from "react";
import VideoService from "../base/services/videoService";
import ClipFeedbackPanel from "./ClipFeedbackPanel";

/**
 * ClipEditorV2 — Sales Intelligence Player style Clip Editor
 *
 * Layout (matching reference screenshot):
 * ┌──────────────────────────────────────────────────────────────────┐
 * │  Header (CLIP EDITOR, phase info, 2/59, close)                   │
 * ├──────────────────────┬───────────────────────────────────────────┤
 * │                      │  Time badge, tags                         │
 * │  9:16 Video          │  Sales Moments                            │
 * │  (full height,       │  AI要約                                    │
 * │   no black bars)     │  改善提案                                   │
 * │  + subtitle overlay  │  (scrollable)                             │
 * │                      │                                           │
 * ├──────────────────────┴───────────────────────────────────────────┤
 * │  Timeline (heatmap) + Controls (1x/1.5x/2x, 前/次, Phase/Full)  │
 * └──────────────────────────────────────────────────────────────────┘
 */

const C = {
  bg: "#0f0f1a",
  surface: "#1a1a2e",
  surfaceLight: "#252540",
  border: "#333355",
  text: "#fff",
  textMuted: "#8888aa",
  textDim: "#555577",
  accent: "#FF6B35",
  green: "#10b981",
  red: "#ef4444",
  blue: "#6366f1",
  yellow: "#f59e0b",
  purple: "#8b5cf6",
  cyan: "#06b6d4",
  teal: "#0d3d38",
};

const scoreColor = (s, a = 1) => {
  if (s == null) return `rgba(80,80,120,${a})`;
  if (s >= 80) return `rgba(16,185,129,${a})`;
  if (s >= 60) return `rgba(245,158,11,${a})`;
  if (s >= 40) return `rgba(251,146,60,${a})`;
  return `rgba(239,68,68,${a})`;
};

const fmt = (sec) => {
  if (!sec && sec !== 0) return "0:00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
};

const MARKERS = {
  sales: { icon: "\u{1F4B0}", label: "\u58F2\u4E0A" },
  hook: { icon: "\u{1F3A3}", label: "\u30D5\u30C3\u30AF" },
  comment_spike: { icon: "\u{1F4AC}", label: "\u30B3\u30E1\u30F3\u30C8" },
  speech_peak: { icon: "\u{1F3A4}", label: "\u767A\u8A71" },
  product_mention: { icon: "\u{1F6CD}\uFE0F", label: "\u5546\u54C1" },
};

// ═══════════════════════════════════════════════════════════════════════════
// SUBTITLE STYLE PRESETS
// ═══════════════════════════════════════════════════════════════════════════
const SUBTITLE_PRESETS = {
  simple: {
    id: 'simple',
    name: window.__t('clipEditorV2_8b806b', 'シンプル'),
    desc: window.__t('clipEditorV2_0c5fa6', 'ビジネス系におすすめ'),
    icon: 'Aa',
    container: {},
    text: {
      color: '#fff',
      fontSize: 16,
      fontWeight: 600,
      textShadow: '0 2px 8px rgba(0,0,0,0.95), 0 0 20px rgba(0,0,0,0.6)',
      backgroundColor: 'transparent',
      padding: '4px 8px',
      borderRadius: 0,
      letterSpacing: 0.5,
      lineHeight: 1.6,
    },
  },
  box: {
    id: 'box',
    name: window.__t('clipEditorV2_cc9aba', 'ボックス'),
    desc: window.__t('clipEditorV2_e08cc3', '視認性重視におすすめ'),
    icon: '\u25A0',
    container: {},
    text: {
      color: '#fff',
      fontSize: 16,
      fontWeight: 600,
      textShadow: '0 2px 6px rgba(0,0,0,0.9)',
      backgroundColor: 'rgba(0,0,0,0.80)',
      padding: '8px 18px',
      borderRadius: 8,
      letterSpacing: 0.3,
      lineHeight: 1.5,
    },
  },
  outline: {
    id: 'outline',
    name: window.__t('clipEditorV2_46eb23', '縁取り'),
    desc: window.__t('clipEditorV2_9e532b', '目立たせたい時におすすめ'),
    icon: 'A',
    container: {},
    text: {
      color: '#fff',
      fontSize: 18,
      fontWeight: 800,
      textShadow: '-2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 2px 2px 0 #000, 0 0 8px rgba(0,0,0,0.8)',
      backgroundColor: 'transparent',
      padding: '4px 8px',
      borderRadius: 0,
      letterSpacing: 0.5,
      lineHeight: 1.5,
      WebkitTextStroke: '1.5px #000',
      paintOrder: 'stroke fill',
    },
  },
  pop: {
    id: 'pop',
    name: window.__t('clipEditorV2_cdf397', 'ポップ'),
    desc: window.__t('clipEditorV2_03484c', 'TikTok投稿におすすめ'),
    icon: '\u2728',
    container: {},
    text: {
      color: '#FFE135',
      fontSize: 20,
      fontWeight: 900,
      textShadow: '-2px -2px 0 #FF6B35, 2px -2px 0 #FF6B35, -2px 2px 0 #FF6B35, 2px 2px 0 #FF6B35, 0 4px 12px rgba(0,0,0,0.7)',
      backgroundColor: 'transparent',
      padding: '4px 12px',
      borderRadius: 0,
      letterSpacing: 1,
      lineHeight: 1.4,
      WebkitTextStroke: '1px #FF6B35',
      paintOrder: 'stroke fill',
    },
  },
  gradient: {
    id: 'gradient',
    name: window.__t('clipEditorV2_e7c9fd', 'グラデーション'),
    desc: window.__t('clipEditorV2_aba4a1', '美容系におすすめ'),
    icon: '\u{1F308}',
    container: {},
    text: {
      color: '#fff',
      fontSize: 16,
      fontWeight: 700,
      textShadow: '0 2px 6px rgba(0,0,0,0.6)',
      background: 'linear-gradient(135deg, rgba(139,92,246,0.85), rgba(236,72,153,0.85))',
      padding: '8px 20px',
      borderRadius: 20,
      letterSpacing: 0.5,
      lineHeight: 1.5,
    },
  },
  karaoke: {
    id: 'karaoke',
    name: window.__t('clipEditorV2_00330b', 'カラオケ'),
    desc: window.__t('clipEditorV2_66c6d6', '喋りに合わせてハイライト'),
    icon: '♪',
    container: {},
    text: {
      color: 'rgba(255,255,255,0.5)',
      fontSize: 18,
      fontWeight: 700,
      textShadow: '0 2px 8px rgba(0,0,0,0.9)',
      backgroundColor: 'rgba(0,0,0,0.70)',
      padding: '8px 18px',
      borderRadius: 10,
      letterSpacing: 0.5,
      lineHeight: 1.5,
    },
    highlightColor: '#FFE135',
  },
};

const SUBTITLE_PRESET_ORDER = ['simple', 'box', 'outline', 'pop', 'gradient', 'karaoke'];

// ═══════════════════════════════════════════════════════════════════════════
const ClipEditorV2 = ({ videoId, clip, videoData, onClose, onClipUpdated }) => {
  const videoRef = useRef(null);
  const timelineRef = useRef(null);
  const waveformCanvasRef = useRef(null);
  const waveformContainerRef = useRef(null);

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [videoReady, setVideoReady] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);

  const [trimStart, setTrimStart] = useState(clip?.time_start || 0);
  const [trimEnd, setTrimEnd] = useState(clip?.time_end || 0);
  const origStart = clip?.time_start || 0;
  const origEnd = clip?.time_end || 0;
  const [dragging, setDragging] = useState(null);

  const [timelineData, setTimelineData] = useState(null);
  const [segments, setSegments] = useState([]);
  const [videoScore, setVideoScore] = useState(null);

  const [tab, setTab] = useState("captions");
  const [isTrimming, setIsTrimming] = useState(false);
  const [status, setStatus] = useState(null);
  const [captions, setCaptions] = useState([]);
  const [savingCaps, setSavingCaps] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [captionsLoaded, setCaptionsLoaded] = useState(false);
  const [targetLanguage, setTargetLanguage] = useState('ja'); // 'ja' | 'zh-TW'

  // Subtitle style & position
  const [subtitleStyle, setSubtitleStyle] = useState('box');
  const [subtitlePos, setSubtitlePos] = useState({ x: 50, y: 85 }); // percentage
  const [isDraggingSub, setIsDraggingSub] = useState(false);
  const [subtitleFontSize, setSubtitleFontSize] = useState(0); // 0 = use preset default
  const subtitleContainerRef = useRef(null);
  const videoContainerRef = useRef(null);

  // Subtitle feedback
  const [subtitleFeedback, setSubtitleFeedback] = useState(null); // 'up' | 'down' | null
  const [feedbackTags, setFeedbackTags] = useState([]);
  const [feedbackSaved, setFeedbackSaved] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState(0);

  // Subtitle timing offset (seconds): positive = delay subtitles, negative = advance
  const [captionOffset, setCaptionOffset] = useState(0);

  // ─── Waveform & Split state ──────────────────────────────────
  const [waveformData, setWaveformData] = useState(null); // Float32Array of amplitudes
  const [waveformLoading, setWaveformLoading] = useState(false);
  const [silentRegions, setSilentRegions] = useState([]); // [{start, end}] in seconds
  const [splitPoints, setSplitPoints] = useState([]); // [seconds] sorted
  const [disabledSegments, setDisabledSegments] = useState(new Set()); // Set of segment indices (legacy, kept for waveform compat)
  const [deletedRanges, setDeletedRanges] = useState([]); // [{start, end}] - CapCut-style deleted ranges
  const [hoveredSegIdx, setHoveredSegIdx] = useState(null);
  const [selectedSegIdx, setSelectedSegIdx] = useState(null);
  const [timelineCursorPos, setTimelineCursorPos] = useState(null); // mouse X position on timeline
  const mouseTimeRef = useRef(null); // mouse time position on timeline (seconds)

  const clipDur = trimEnd - trimStart;

  // ─── Editor State Persistence (localStorage) ─────────────────
  // Save/restore editing state so user doesn't lose work when navigating away
  const storageKey = useMemo(() => videoId && clip?.phase_index != null
    ? `clipEditor_${videoId}_${clip.phase_index}` : null, [videoId, clip?.phase_index]);

  // Restore editor state from localStorage on mount
  useEffect(() => {
    if (!storageKey) return;
    try {
      const saved = localStorage.getItem(storageKey);
      if (!saved) return;
      const state = JSON.parse(saved);
      if (state.deletedRanges?.length > 0) setDeletedRanges(state.deletedRanges);
      if (state.splitPoints?.length > 0) setSplitPoints(state.splitPoints);
      if (state.captionOffset != null && state.captionOffset !== 0) setCaptionOffset(state.captionOffset);
      if (state.subtitleFontSize != null) setSubtitleFontSize(state.subtitleFontSize);
      if (state.subtitleFeedback) setSubtitleFeedback(state.subtitleFeedback);
      if (state.feedbackTags?.length > 0) setFeedbackTags(state.feedbackTags);
      console.log('[ClipEditor] Restored editor state from localStorage');
    } catch (e) {
      console.warn('[ClipEditor] Failed to restore editor state:', e);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  // Auto-save editor state to localStorage on changes
  useEffect(() => {
    if (!storageKey) return;
    const state = {
      deletedRanges,
      splitPoints,
      captionOffset,
      subtitleFontSize,
      subtitleFeedback,
      feedbackTags,
      savedAt: Date.now(),
    };
    try {
      localStorage.setItem(storageKey, JSON.stringify(state));
    } catch (e) {
      // localStorage full or unavailable - ignore
    }
  }, [storageKey, deletedRanges, splitPoints, captionOffset, subtitleFontSize, subtitleFeedback, feedbackTags]);

  // Determine if we're playing a clip_url (local time 0-based) or full video
  const isClipVideo = !!(clip?.clip_url);

  // ─── Timeline local time helpers ─────────────────────────────
  // When playing clip_url, video.duration is clip length (0-based),
  // but trimStart/trimEnd are absolute timestamps from the full video.
  // Convert to local (0-based) time for timeline bar display.
  const tlTrimStart = isClipVideo ? trimStart - origStart : trimStart;
  const tlTrimEnd = isClipVideo ? trimEnd - origStart : trimEnd;
  const tlOrigStart = isClipVideo ? 0 : origStart;
  const tlOrigEnd = isClipVideo ? origEnd - origStart : origEnd;
  // Timeline denominator: use the actual video duration so waveform fills the full bar.
  // Previously used Math.max(duration, origEnd - origStart) which caused the waveform
  // to only fill partway when clip_url duration < phase duration.
  const tlDuration = duration || (origEnd - origStart) || 1;
  const rawVideoUrl = useMemo(() => {
    return clip?.clip_url || videoData?.video_url || clip?.video_url || null;
  }, [videoData, clip]);

  // If clip_url has no SAS token, fetch a fresh one from the backend
  const [videoUrl, setVideoUrl] = useState(null);
  useEffect(() => {
    if (!rawVideoUrl) { setVideoUrl(null); return; }
    // Check if URL already has SAS token
    if (rawVideoUrl.includes('sig=') || rawVideoUrl.includes('sv=')) {
      setVideoUrl(rawVideoUrl);
      return;
    }
    // No SAS token - fetch fresh URL from backend
    let cancelled = false;
    (async () => {
      try {
        const freshRes = await VideoService.getClipStatus(videoId, clip?.phase_index);
        if (!cancelled && freshRes?.clip_url) {
          setVideoUrl(freshRes.clip_url);
        } else if (!cancelled) {
          setVideoUrl(rawVideoUrl); // fallback
        }
      } catch (e) {
        console.warn('[ClipEditor] Failed to fetch fresh SAS URL:', e);
        if (!cancelled) setVideoUrl(rawVideoUrl);
      }
    })();
    return () => { cancelled = true; };
  }, [rawVideoUrl, videoId, clip?.phase_index]);

  // ─── Time offset logic ────────────────────────────────────────
  // When playing clip_url: video currentTime is 0-based (clip local time)
  // Captions may be in LOCAL time (Whisper/saved: 0-based) or ABSOLUTE time
  // (transcript/audio_text: e.g., 2490s for 41:30).
  // Auto-detect: if max(caption.start) < origStart, captions are already local.
  // This mirrors the backend logic in _build_drawtext_filter.

  // Auto-detect whether current captions are local or absolute
  const captionsAreLocal = useMemo(() => {
    if (!isClipVideo || !captions.length || origStart <= 0) return true;
    const maxStart = Math.max(...captions.map(c => c.start || 0));
    return maxStart < origStart;
  }, [isClipVideo, captions, origStart]);

  // Convert caption time to video-local time for matching
  const toLocalTime = useCallback((absTime) => {
    if (!isClipVideo) return absTime;
    if (captionsAreLocal) return absTime; // already 0-based, no conversion
    return absTime - origStart;
  }, [isClipVideo, captionsAreLocal, origStart]);

  // Convert video-local time to absolute time for display
  const toAbsTime = useCallback((localTime) => {
    if (!isClipVideo) return localTime;
    if (captionsAreLocal) return localTime; // already local, no conversion needed
    return localTime + origStart;
  }, [isClipVideo, captionsAreLocal, origStart]);

  // Current caption based on playback time (with offset correction)
  // Extend display: each caption stays visible until the next caption starts
  // or for a minimum of 3 seconds, whichever is longer.
  // captionOffset: positive = delay subtitle display (subtitle appears later),
  //                negative = advance subtitle display (subtitle appears earlier)
  //
  // ⚠️ PROTECTED: deletedRanges-aware caption matching.
  // When segments are deleted, captions inside deleted ranges are hidden,
  // and captions AFTER deleted ranges are NOT shown during the skip gap.
  // This prevents subtitle desync when scenes are removed.
  const currentCaption = useMemo(() => {
    if (!captions.length) return null;
    const t = currentTime;
    const MIN_DISPLAY = 3; // minimum display duration in seconds
    // Check if current time falls inside a deleted range - if so, show no caption
    if (deletedRanges.length > 0) {
      for (const dr of deletedRanges) {
        if (t >= dr.start && t < dr.end) return null;
      }
    }
    for (let i = 0; i < captions.length; i++) {
      const c = captions[i];
      const localStart = toLocalTime(c.start || 0) + captionOffset;
      const rawEnd = toLocalTime(c.end || (c.start + 5)) + captionOffset;
      // Skip captions that fall entirely within a deleted range
      if (deletedRanges.length > 0) {
        const capInDeleted = deletedRanges.some(dr => localStart >= dr.start && rawEnd <= dr.end);
        if (capInDeleted) continue;
      }
      // Extend end to at least MIN_DISPLAY seconds after start
      let extendedEnd = Math.max(rawEnd, localStart + MIN_DISPLAY);
      // But don't overlap with next caption's start
      if (i + 1 < captions.length) {
        const nextStart = toLocalTime(captions[i + 1].start || 0) + captionOffset;
        extendedEnd = Math.min(extendedEnd, nextStart);
      }
      if (t >= localStart && t < extendedEnd) return c;
    }
    return null;
  }, [captions, currentTime, toLocalTime, captionOffset, deletedRanges]);

  const currentPhase = useMemo(() => {
    if (!segments.length) return null;
    return segments.find((s) => {
      const st = s.start_sec ?? s.time_start ?? 0;
      const en = s.end_sec ?? s.time_end ?? 0;
      return currentTime >= st && currentTime <= en;
    });
  }, [segments, currentTime]);

  // ─── Load Data ─────────────────────────────────────────────────
  useEffect(() => {
    if (!videoId) return;
    (async () => {
      try {
        const [tl, seg, sc] = await Promise.all([
          VideoService.getTimelineData(videoId),
          VideoService.getSegmentScores(videoId),
          VideoService.getVideoScore(videoId),
        ]);
        setTimelineData(tl);
        setSegments(seg?.segments || []);
        setVideoScore(sc);
      } catch (e) {
        console.warn("Editor data load failed:", e);
      }
    })();
  }, [videoId]);

  // Helper: build captions from real speech transcripts (Whisper segments)
  const buildCaptionsFromTranscripts = useCallback((transcripts, clipData) => {
    if (!transcripts?.length || !clipData) return [];
    const tStart = clipData.time_start || 0;
    const tEnd = clipData.time_end || 0;
    const MAX_CHARS = 15; // Max characters per subtitle line
    const MAX_DUR = 4.0;  // Max seconds per subtitle line

    // Filter transcripts that overlap with this clip's time range
    const overlapping = transcripts.filter((t) => {
      const s = t.start ?? 0;
      const e = t.end ?? 0;
      return s < tEnd && e > tStart;
    });

    // Helper: split text into chunks of MAX_CHARS with proportional timestamps
    // ⚠️ PROTECTED: Japanese word-boundary-aware text splitting.
    // DO NOT revert to simple character-count splitting — it breaks Japanese words mid-token.
    // Splits at particles (は,が,を,に,で,と,も,や,か,ね,よ,な), punctuation (、,。), and
    // conjunctions (して,って,ので,から,けど,ても,たら,ながら) to keep subtitles readable.
    const splitTextChunks = (txt, start, end) => {
      const chars = [...txt];
      const dur = end - start;
      if (chars.length <= MAX_CHARS) {
        return [{ start: Math.round(start * 100) / 100, end: Math.round(end * 100) / 100, text: txt, source: 'master_transcript' }];
      }
      // Find natural break points in Japanese text
      // Priority: punctuation > conjunctions > particles
      const breakPoints = [];
      const PARTICLES = new Set([window.__t('clipEditorV2_b7a83b', 'は'),window.__t('clipEditorV2_2f9adf', 'が'),window.__t('clipEditorV2_96ac23', 'を'),window.__t('clipEditorV2_8b6362', 'に'),window.__t('clipEditorV2_3b9b37', 'で'),window.__t('and', 'と'),window.__t('clipEditorV2_ac50f8', 'も'),window.__t('clipEditorV2_3cb21c', 'や'),window.__t('clipEditorV2_b19ca8', 'か'),window.__t('clipEditorV2_448d36', 'ね'),window.__t('clipEditorV2_66cb79', 'よ'),window.__t('clipEditorV2_2b8cb2', 'な'),window.__t('clipEditorV2_40346e', 'へ'),window.__t('clipEditorV2_359ebe', 'の'),window.__t('clipEditorV2_66b649', 'て'),window.__t('clipEditorV2_14cc92', 'た')]);
      const CONJUNCTIONS = [window.__t('clipEditorV2_afb2d2', 'して'),window.__t('clipEditorV2_df7dab', 'って'),window.__t('clipEditorV2_d3020d', 'ので'),window.__t('momentClips_from', 'から'),window.__t('clipEditorV2_5d6a58', 'けど'),window.__t('clipEditorV2_a93e17', 'ても'),window.__t('clipEditorV2_345c97', 'たら'),window.__t('clipEditorV2_2ec369', 'ながら'),window.__t('clipEditorV2_e2e614', 'だけど'),window.__t('clipEditorV2_5c39fe', 'ですが')];
      for (let i = 1; i < chars.length - 1; i++) {
        // After punctuation: highest priority
        if ('、。！？，'.includes(chars[i])) {
          breakPoints.push({ pos: i + 1, priority: 3 });
          continue;
        }
        // After conjunction patterns
        let foundConj = false;
        for (const conj of CONJUNCTIONS) {
          const conjChars = [...conj];
          if (i + conjChars.length <= chars.length) {
            const slice = chars.slice(i, i + conjChars.length).join('');
            if (slice === conj) {
              breakPoints.push({ pos: i + conjChars.length, priority: 2 });
              foundConj = true;
              break;
            }
          }
        }
        if (foundConj) continue;
        // After particles (only if followed by non-particle)
        if (PARTICLES.has(chars[i]) && !PARTICLES.has(chars[i + 1])) {
          breakPoints.push({ pos: i + 1, priority: 1 });
        }
      }
      // Select break points that create chunks close to MAX_CHARS
      const numChunks = Math.max(1, Math.ceil(chars.length / MAX_CHARS));
      const idealChunkSize = Math.ceil(chars.length / numChunks);
      const selectedBreaks = [0];
      let lastBreak = 0;
      for (let target = idealChunkSize; target < chars.length; target += idealChunkSize) {
        // Find the best break point near the target position
        let bestBp = null;
        let bestDist = Infinity;
        const searchRange = Math.floor(idealChunkSize * 0.4); // Search within 40% of ideal size
        for (const bp of breakPoints) {
          if (bp.pos <= lastBreak) continue;
          if (bp.pos >= chars.length - 2) continue; // Don't break too close to end
          const dist = Math.abs(bp.pos - target);
          if (dist <= searchRange) {
            // Prefer higher priority breaks, then closer distance
            if (!bestBp || bp.priority > bestBp.priority || (bp.priority === bestBp.priority && dist < bestDist)) {
              bestBp = bp;
              bestDist = dist;
            }
          }
        }
        if (bestBp) {
          selectedBreaks.push(bestBp.pos);
          lastBreak = bestBp.pos;
        } else {
          // Fallback: break at target position (character-based)
          selectedBreaks.push(target);
          lastBreak = target;
        }
      }
      selectedBreaks.push(chars.length);
      // Build chunks from selected break points
      const chunks = [];
      for (let i = 0; i < selectedBreaks.length - 1; i++) {
        const chunkText = chars.slice(selectedBreaks[i], selectedBreaks[i + 1]).join('');
        if (!chunkText.trim()) continue;
        const ratioStart = selectedBreaks[i] / chars.length;
        const ratioEnd = selectedBreaks[i + 1] / chars.length;
        chunks.push({
          start: Math.round((start + dur * ratioStart) * 100) / 100,
          end: Math.round((start + dur * ratioEnd) * 100) / 100,
          text: chunkText,
          source: 'master_transcript',
        });
      }
      return chunks;
    };

    // Split large transcript chunks into readable subtitle lines
    const result = [];
    for (const t of overlapping) {
      const segStart = Math.max(t.start, tStart);
      const segEnd = Math.min(t.end, tEnd);
      const text = (t.text || '').trim();
      if (!text) continue;
      const segDur = segEnd - segStart;

      // If segment is short AND text is short, keep as-is
      if (segDur <= MAX_DUR && text.length <= MAX_CHARS) {
        result.push({ start: segStart, end: segEnd, text, source: 'master_transcript' });
        continue;
      }

      // Try splitting by sentence boundaries first
      const sentences = text.split(/[。！？\n]/).map(s => s.trim()).filter(Boolean);
      if (sentences.length > 1) {
        const perSentence = segDur / sentences.length;
        for (let i = 0; i < sentences.length; i++) {
          const sentStart = segStart + i * perSentence;
          const sentEnd = segStart + (i + 1) * perSentence;
          // If sentence itself is too long, split further
          if (sentences[i].length > MAX_CHARS) {
            result.push(...splitTextChunks(sentences[i], sentStart, sentEnd));
          } else {
            result.push({
              start: Math.round(sentStart * 100) / 100,
              end: Math.round(sentEnd * 100) / 100,
              text: sentences[i],
              source: 'master_transcript',
            });
          }
        }
      } else {
        // Single long text - split by character count
        result.push(...splitTextChunks(text, segStart, segEnd));
      }
    }
    return result;
  }, []);

  // Fallback: build subtitle-like captions from phase audio_text (raw speech text per phase)
  const buildCaptionsFromAudioText = useCallback((phases, clipData) => {
    if (!phases || !clipData) return [];
    const phaseIdx = clipData.phase_index;
    const tStart = clipData.time_start || 0;
    const tEnd = clipData.time_end || 0;
    const MAX_CHARS = 15;

    // Helper: split long text into MAX_CHARS chunks with proportional timestamps
    const splitLongText = (txt, start, end) => {
      const chars = [...txt];
      const dur = end - start;
      const numChunks = Math.max(1, Math.ceil(chars.length / MAX_CHARS));
      const charsPerChunk = Math.ceil(chars.length / numChunks);
      const chunks = [];
      for (let c = 0; c < numChunks; c++) {
        const chunkText = chars.slice(c * charsPerChunk, (c + 1) * charsPerChunk).join('');
        if (!chunkText.trim()) continue;
        const ratioStart = c * charsPerChunk / chars.length;
        const ratioEnd = Math.min((c + 1) * charsPerChunk / chars.length, 1.0);
        chunks.push({
          start: Math.round((start + dur * ratioStart) * 100) / 100,
          end: Math.round((start + dur * ratioEnd) * 100) / 100,
          text: chunkText,
          source: 'audio_text',
        });
      }
      return chunks;
    };

    // Find matching phase(s) for this clip's time range
    const matchingPhases = phases.filter((p) => {
      const pStart = p.time_start ?? 0;
      const pEnd = p.time_end ?? 0;
      return pStart < tEnd && pEnd > tStart;
    });

    if (matchingPhases.length === 0) {
      const exact = phases.find((p) => p.phase_index === phaseIdx);
      if (exact) matchingPhases.push(exact);
    }

    const result = [];
    for (const phase of matchingPhases) {
      // Use audio_text (actual speech) only, NOT description (AI summary)
      const txt = phase.audio_text;
      if (!txt) continue;
      const pStart = Math.max(phase.time_start ?? tStart, tStart);
      const pEnd = Math.min(phase.time_end ?? tEnd, tEnd);

      // Split text into sentences for better subtitle display
      const sentences = txt.split(/[。！？\n]/).map((s) => s.trim()).filter(Boolean);
      if (sentences.length === 0) {
        // Single block of text - split by character count
        if (txt.trim().length > MAX_CHARS) {
          result.push(...splitLongText(txt.trim(), pStart, pEnd));
        } else {
          result.push({ start: pStart, end: pEnd, text: txt.trim(), source: "audio_text" });
        }
      } else {
        const dur = pEnd - pStart;
        const perSentence = dur / sentences.length;
        sentences.forEach((sent, i) => {
          const sentStart = Math.round((pStart + i * perSentence) * 100) / 100;
          const sentEnd = Math.round((pStart + (i + 1) * perSentence) * 100) / 100;
          // If sentence is too long, split further
          if (sent.length > MAX_CHARS) {
            result.push(...splitLongText(sent, sentStart, sentEnd));
          } else {
            result.push({ start: sentStart, end: sentEnd, text: sent, source: "audio_text" });
          }
        });
      }
    }
    return result;
  }, []);

  useEffect(() => {
    if (!videoId || clip?.phase_index == null) return;

    // Priority 0 (HIGHEST): Fetch saved captions from DB via clip status API
    // This ensures user-edited/saved captions are always loaded first
    (async () => {
      try {
        const res = await VideoService.getClipStatus(videoId, clip.phase_index);
        // Restore saved subtitle style & position
        if (res?.subtitle_style) {
          setSubtitleStyle(res.subtitle_style);
          console.log(`[Subtitles] Restored style: ${res.subtitle_style}`);
        }
        if (res?.subtitle_position_x != null && res?.subtitle_position_y != null) {
          setSubtitlePos({ x: res.subtitle_position_x, y: res.subtitle_position_y });
          console.log(`[Subtitles] Restored position: (${res.subtitle_position_x}, ${res.subtitle_position_y})`);
        }
        if (res?.captions && res.captions.length > 0) {
          // Ensure saved captions have a source marker
          const saved = Array.isArray(res.captions) ? res.captions : [];
          // Auto-split any saved captions that are too long (legacy data protection)
          const MAX_CAP_CHARS = 15;
          const splitSaved = [];
          for (const c of saved) {
            const txt = (c.text || '').trim();
            if (txt.length <= MAX_CAP_CHARS) {
              splitSaved.push({ ...c, source: c.source || 'saved' });
            } else {
              // Split long saved caption into readable chunks
              const chars = [...txt];
              const dur = (c.end || 0) - (c.start || 0);
              const numChunks = Math.max(1, Math.ceil(chars.length / MAX_CAP_CHARS));
              const charsPerChunk = Math.ceil(chars.length / numChunks);
              for (let ci = 0; ci < numChunks; ci++) {
                const chunkText = chars.slice(ci * charsPerChunk, (ci + 1) * charsPerChunk).join('');
                if (!chunkText.trim()) continue;
                const ratioS = ci * charsPerChunk / chars.length;
                const ratioE = Math.min((ci + 1) * charsPerChunk / chars.length, 1.0);
                splitSaved.push({
                  ...c,
                  start: Math.round(((c.start || 0) + dur * ratioS) * 1000) / 1000,
                  end: Math.round(((c.start || 0) + dur * ratioE) * 1000) / 1000,
                  text: chunkText,
                  source: c.source || 'saved',
                  // Drop words array for split chunks (no longer accurate)
                  words: undefined,
                });
              }
            }
          }
          console.log(`[Subtitles] Loaded ${saved.length} saved captions from DB (split to ${splitSaved.length})`);
          setCaptions(splitSaved);
          setCaptionsLoaded(true);
          return;
        }
      } catch (e) {
        console.warn("Failed to fetch saved captions:", e);
      }

      // Priority 1: clip.captions (from generate_clip Whisper)
      if (clip?.captions && clip.captions.length > 0) {
        // Auto-split any clip captions that are too long
        const MAX_CAP_CHARS = 15;
        const splitClipCaps = [];
        for (const c of clip.captions) {
          const txt = (c.text || '').trim();
          if (txt.length <= MAX_CAP_CHARS) {
            splitClipCaps.push(c);
          } else if (c.words && c.words.length > 0) {
            // Use word-level timestamps to split
            let curText = '';
            let curWords = [];
            let curStart = null;
            for (const w of c.words) {
              const wt = (w.word || '').trim();
              if (!wt) continue;
              if (curStart === null) curStart = w.start;
              if ((curText + wt).length > MAX_CAP_CHARS && curText.length >= 4) {
                splitClipCaps.push({ start: curStart, end: curWords[curWords.length - 1].end, text: curText, words: [...curWords], source: c.source });
                curText = wt;
                curWords = [w];
                curStart = w.start;
              } else {
                curText += wt;
                curWords.push(w);
              }
            }
            if (curText && curWords.length) {
              splitClipCaps.push({ start: curStart, end: curWords[curWords.length - 1].end, text: curText, words: [...curWords], source: c.source });
            }
          } else {
            // No words - split by character count
            const chars = [...txt];
            const dur = (c.end || 0) - (c.start || 0);
            const numChunks = Math.max(1, Math.ceil(chars.length / MAX_CAP_CHARS));
            const cpc = Math.ceil(chars.length / numChunks);
            for (let ci = 0; ci < numChunks; ci++) {
              const ct = chars.slice(ci * cpc, (ci + 1) * cpc).join('');
              if (!ct.trim()) continue;
              const rs = ci * cpc / chars.length;
              const re = Math.min((ci + 1) * cpc / chars.length, 1.0);
              splitClipCaps.push({ start: (c.start || 0) + dur * rs, end: (c.start || 0) + dur * re, text: ct, source: c.source });
            }
          }
        }
        console.log(`[Subtitles] Using clip.captions (${clip.captions.length} -> ${splitClipCaps.length} after split)`);
        setCaptions(splitClipCaps);
        setCaptionsLoaded(true);
        return;
      }

      // Priority 2: Real speech transcripts from timeline API (Whisper segments)
      if (timelineData?.transcripts?.length > 0) {
        const fromTranscripts = buildCaptionsFromTranscripts(timelineData.transcripts, clip);
        if (fromTranscripts.length > 0) {
          console.log(`[Subtitles] Using ${fromTranscripts.length} real transcript segments (source: ${timelineData.transcript_source})`);
          setCaptions(fromTranscripts);
          setCaptionsLoaded(true);
          return;
        }
      }

      // Priority 3: Fallback to audio_text from phases (actual speech, NOT description)
      if (timelineData?.phases?.length > 0) {
        const fallback = buildCaptionsFromAudioText(timelineData.phases, clip);
        if (fallback.length > 0) {
          console.log(`[Subtitles] Using ${fallback.length} audio_text fallback captions`);
          setCaptions(fallback);
          setCaptionsLoaded(true);
        }
      }
      // Mark as loaded even if no captions found (so autoTranscribe can proceed)
      setCaptionsLoaded(true);
    })();
  }, [clip, videoId, timelineData, buildCaptionsFromTranscripts, buildCaptionsFromAudioText]);

  // ─── Auto-generate subtitles when clip editor opens ─────────────
  // If no Whisper-sourced captions exist, auto-trigger transcription
  // IMPORTANT: Wait for captionsLoaded=true before deciding to auto-transcribe
  // to avoid race condition where captions haven't loaded from DB yet
  const autoTranscribeTriggered = useRef(false);
  useEffect(() => {
    if (autoTranscribeTriggered.current) return;
    if (!videoId || !clip) return;
    if (transcribing) return;

    // CRITICAL: Wait for caption loading to complete before deciding
    if (!captionsLoaded) return;

    // Check if we already have good captions (saved, whisper, or transcript)
    const hasGoodCaptions = captions.some(
      (c) => c.source === "whisper" || c.source === "transcript" || c.source === "saved"
    );
    if (hasGoodCaptions) {
      console.log("[AutoTranscribe] Already have good captions (source: " + captions[0]?.source + "), skipping");
      return;
    }

    // Check if clip.captions exist (from generate_clip)
    if (clip?.captions && clip.captions.length > 0) {
      console.log("[AutoTranscribe] clip.captions exist, skipping");
      return;
    }

    // Check if any captions exist at all (audio_text fallback etc.)
    if (captions.length > 0) {
      console.log("[AutoTranscribe] Captions already loaded (" + captions.length + " items, source: " + captions[0]?.source + "), skipping");
      return;
    }

    // No captions found at all - auto-trigger transcription
    const clipUrl = clip.clip_url || videoData?.video_url || clip.video_url;
    if (!clipUrl) {
      console.log("[AutoTranscribe] No clip URL available, skipping");
      return;
    }

    console.log("[AutoTranscribe] No captions found after loading, auto-triggering transcription");
    autoTranscribeTriggered.current = true;
    generateSubtitles();
  }, [videoId, clip, captionsLoaded, captions, transcribing, videoData]);

  // ─── Video Handlers ────────────────────────────────────────────
  const onTimeUpdate = useCallback(() => {
    if (!videoRef.current) return;
    const t = videoRef.current.currentTime;
    setCurrentTime(t);
    // Skip over deleted ranges during playback
    if (deletedRanges.length > 0 && !videoRef.current.paused) {
      for (const dr of deletedRanges) {
        if (t >= dr.start && t < dr.end) {
          // Jump to end of deleted range
          videoRef.current.currentTime = dr.end;
          setCurrentTime(dr.end);
          console.log(`[Playback] Skipped deleted range ${dr.start.toFixed(1)}-${dr.end.toFixed(1)}`);
          return;
        }
      }
    }
  }, [deletedRanges]);

  const onMeta = useCallback(() => {
    if (videoRef.current) {
      setDuration(videoRef.current.duration);
      setVideoReady(true);
    }
  }, []);

  const toggle = useCallback(() => {
    if (!videoRef.current) return;
    isPlaying ? videoRef.current.pause() : videoRef.current.play();
    setIsPlaying(!isPlaying);
  }, [isPlaying]);

  const seek = useCallback((t) => {
    if (videoRef.current) {
      videoRef.current.currentTime = t;
      setCurrentTime(t);
    }
  }, []);

  const setSpeed = useCallback((r) => {
    setPlaybackRate(r);
    if (videoRef.current) videoRef.current.playbackRate = r;
  }, []);

  // ─── Waveform extraction (Web Audio API) ──────────────────────
  const extractWaveform = useCallback(async () => {
    if (!videoUrl || waveformLoading || waveformData) return;
    setWaveformLoading(true);
    try {
      const response = await fetch(videoUrl);
      const arrayBuffer = await response.arrayBuffer();
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
      const rawData = audioBuffer.getChannelData(0);
      const sampleRate = audioBuffer.sampleRate;
      const audioDuration = audioBuffer.duration;

      // Downsample to ~500 samples for display
      const SAMPLES = 500;
      const blockSize = Math.floor(rawData.length / SAMPLES);
      const peaks = new Float32Array(SAMPLES);
      for (let i = 0; i < SAMPLES; i++) {
        let sum = 0;
        const start = i * blockSize;
        for (let j = 0; j < blockSize; j++) {
          sum += Math.abs(rawData[start + j] || 0);
        }
        peaks[i] = sum / blockSize;
      }
      // Normalize to 0-1
      const maxPeak = Math.max(...peaks) || 1;
      for (let i = 0; i < SAMPLES; i++) peaks[i] /= maxPeak;
      setWaveformData(peaks);

      // Detect silent regions (amplitude < threshold for > 0.5s)
      const SILENCE_THRESHOLD = 0.05;
      const MIN_SILENCE_DURATION = 0.5; // seconds
      const silences = [];
      let silStart = null;
      const samplesPerSec = SAMPLES / audioDuration;
      for (let i = 0; i < SAMPLES; i++) {
        if (peaks[i] < SILENCE_THRESHOLD) {
          if (silStart === null) silStart = i;
        } else {
          if (silStart !== null) {
            const startSec = silStart / samplesPerSec;
            const endSec = i / samplesPerSec;
            if (endSec - startSec >= MIN_SILENCE_DURATION) {
              silences.push({ start: startSec, end: endSec });
            }
            silStart = null;
          }
        }
      }
      if (silStart !== null) {
        const startSec = silStart / samplesPerSec;
        const endSec = audioDuration;
        if (endSec - startSec >= MIN_SILENCE_DURATION) {
          silences.push({ start: startSec, end: endSec });
        }
      }
      setSilentRegions(silences);
      audioCtx.close();
      console.log(`[Waveform] Extracted ${SAMPLES} samples, ${silences.length} silent regions`);
    } catch (e) {
      console.warn('[Waveform] Extraction failed:', e);
    } finally {
      setWaveformLoading(false);
    }
  }, [videoUrl, waveformLoading, waveformData]);

  // Auto-extract waveform when video is ready
  useEffect(() => {
    if (videoReady && videoUrl && !waveformData && !waveformLoading) {
      extractWaveform();
    }
  }, [videoReady, videoUrl, waveformData, waveformLoading, extractWaveform]);

  // ─── Draw waveform on canvas ──────────────────────────────────
  useEffect(() => {
    const canvas = waveformCanvasRef.current;
    if (!canvas || !waveformData || !duration) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const samples = waveformData.length;
    const effDuration = tlDuration || duration;

    // Draw silent region backgrounds
    ctx.fillStyle = 'rgba(239, 68, 68, 0.12)';
    for (const sr of silentRegions) {
      const x1 = (sr.start / effDuration) * W;
      const x2 = (sr.end / effDuration) * W;
      ctx.fillRect(x1, 0, x2 - x1, H);
    }

    // Draw disabled segments (legacy)
    if (splitPoints.length > 0) {
      const sortedSp = [...splitPoints].sort((a, b) => a - b);
      const allPoints = [0, ...sortedSp, duration];
      for (let i = 0; i < allPoints.length - 1; i++) {
        if (disabledSegments.has(i)) {
          const x1 = (allPoints[i] / effDuration) * W;
          const x2 = (allPoints[i + 1] / effDuration) * W;
          ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
          ctx.fillRect(x1, 0, x2 - x1, H);
        }
      }
    }
    // Draw deleted ranges (CapCut-style)
    for (const dr of deletedRanges) {
      const x1 = (dr.start / effDuration) * W;
      const x2 = (dr.end / effDuration) * W;
      ctx.fillStyle = 'rgba(239, 68, 68, 0.3)';
      ctx.fillRect(x1, 0, x2 - x1, H);
      // Draw X pattern
      ctx.strokeStyle = 'rgba(239, 68, 68, 0.5)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x1, 0); ctx.lineTo(x2, H);
      ctx.moveTo(x2, 0); ctx.lineTo(x1, H);
      ctx.stroke();
    }

    // Draw waveform bars - position each bar based on its actual time mapped to tlDuration
    for (let i = 0; i < samples; i++) {
      const amp = waveformData[Math.min(i, waveformData.length - 1)] || 0;
      const timeSec = (i / samples) * duration; // actual time this sample represents
      const x = (timeSec / effDuration) * W;     // position on timeline (tlDuration-based)
      const barW2 = Math.max(1, (duration / effDuration) * W / samples);
      const barH = Math.max(1, amp * H * 0.9);

      // Check if this sample is in a silent region
      const isSilent = silentRegions.some(sr => timeSec >= sr.start && timeSec <= sr.end);
      // Check if this sample is in a deleted range
      let isDisabled = false;
      if (deletedRanges.length > 0) {
        for (const dr of deletedRanges) {
          if (timeSec >= dr.start && timeSec < dr.end) {
            isDisabled = true;
            break;
          }
        }
      }
      // Also check legacy disabledSegments
      if (!isDisabled && splitPoints.length > 0) {
        const sortedSp2 = [...splitPoints].sort((a, b) => a - b);
        const allPoints = [0, ...sortedSp2, duration];
        for (let si = 0; si < allPoints.length - 1; si++) {
          if (disabledSegments.has(si) && timeSec >= allPoints[si] && timeSec < allPoints[si + 1]) {
            isDisabled = true;
            break;
          }
        }
      }

      if (isDisabled) {
        ctx.fillStyle = 'rgba(100, 100, 120, 0.3)';
      } else if (isSilent) {
        ctx.fillStyle = 'rgba(239, 68, 68, 0.4)';
      } else {
        ctx.fillStyle = amp > 0.6 ? 'rgba(16, 185, 129, 0.8)' : amp > 0.3 ? 'rgba(99, 102, 241, 0.7)' : 'rgba(136, 136, 170, 0.5)';
      }
      ctx.fillRect(x, H - barH, barW2 - 0.5, barH);
    }

    // Draw split point lines
    ctx.strokeStyle = '#FFE135';
    ctx.lineWidth = 2;
    for (const sp of splitPoints) {
      const x = (sp / (tlDuration || duration)) * W;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, H);
      ctx.stroke();
    }

    // Draw playhead
    if (currentTime >= 0) {
      const px = (currentTime / (tlDuration || duration)) * W;
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(px, 0);
      ctx.lineTo(px, H);
      ctx.stroke();
    }

    // Draw cursor position (timelineCursorPos is a ratio 0-1)
    if (timelineCursorPos !== null) {
      const cursorPx = timelineCursorPos * W;
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(cursorPx, 0);
      ctx.lineTo(cursorPx, H);
      ctx.stroke();
      ctx.setLineDash([]);
      // Draw time label at cursor
      if (mouseTimeRef.current != null) {
        ctx.fillStyle = 'rgba(255, 255, 255, 0.8)';
        ctx.font = '10px sans-serif';
        const label = fmt(mouseTimeRef.current);
        const tw = ctx.measureText(label).width;
        const lx = Math.min(cursorPx + 4, W - tw - 4);
        ctx.fillText(label, lx, 10);
      }
    }
  }, [waveformData, duration, tlDuration, silentRegions, splitPoints, disabledSegments, deletedRanges, currentTime, timelineCursorPos]);

  // ─── Split segments helper ────────────────────────────────────
  const splitSegments = useMemo(() => {
    if (splitPoints.length === 0) return [];
    const sorted = [...splitPoints].sort((a, b) => a - b);
    const allPoints = [0, ...sorted, duration || 0];
    return allPoints.slice(0, -1).map((start, i) => ({
      index: i,
      start,
      end: allPoints[i + 1],
      enabled: !disabledSegments.has(i),
    }));
  }, [splitPoints, duration, disabledSegments]);

  // ─── Delete segment (CapCut-style: completely remove from timeline) ──────
  const deleteSegment = useCallback((segIdx) => {
    if (segIdx === null || segIdx === undefined) return;
    // Get current segments from splitPoints (use spread to avoid mutating state)
    const allPoints = [0, ...[...splitPoints].sort((a, b) => a - b), duration || 0];
    console.log('[DeleteSeg] segIdx:', segIdx, 'allPoints:', allPoints, 'deletedRanges:', deletedRanges.length);
    if (segIdx < 0 || segIdx >= allPoints.length - 1) {
      console.log('[DeleteSeg] segIdx out of range');
      return;
    }
    const segStart = allPoints[segIdx];
    const segEnd = allPoints[segIdx + 1];
    
    // Check if already deleted
    const alreadyDeleted = deletedRanges.some(r => Math.abs(r.start - segStart) < 0.05 && Math.abs(r.end - segEnd) < 0.05);
    if (alreadyDeleted) {
      console.log('[DeleteSeg] Already deleted:', segStart, '-', segEnd);
      return;
    }
    
    // Don't allow deleting ALL segments - must keep at least 1
    const totalSegs = allPoints.length - 1;
    const deletedCount = deletedRanges.length;
    if (deletedCount + 1 >= totalSegs) {
      console.log('[DeleteSeg] Cannot delete all segments. total:', totalSegs, 'deleted:', deletedCount);
      return;
    }
    
    console.log('[DeleteSeg] Deleting segment', segIdx, ':', segStart.toFixed(1), '-', segEnd.toFixed(1));
    // Record deleted range for export (keep splitPoints intact!)
    setDeletedRanges(prev => [...prev, { start: segStart, end: segEnd }]);
    
    setSelectedSegIdx(null);
    setHoveredSegIdx(null);
  }, [splitPoints, duration, deletedRanges]);

  // ─── Keyboard shortcuts ────────────────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Don't trigger if user is typing in an input/textarea
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      // Space = play/pause
      if (e.key === ' ' || e.code === 'Space') {
        e.preventDefault();
        if (videoRef.current) {
          if (videoRef.current.paused) {
            videoRef.current.play();
            setIsPlaying(true);
          } else {
            videoRef.current.pause();
            setIsPlaying(false);
          }
        }
        return;
      }

      // W = split at mouse position (if hovering) or current playhead position
      if (e.key === 'w' || e.key === 'W') {
        e.preventDefault();
        const splitTime = mouseTimeRef.current != null ? mouseTimeRef.current : currentTime;
        if (!duration || splitTime <= 0 || splitTime >= duration) return;
        // Don't add duplicate split points (within 0.5s)
        const isDuplicate = splitPoints.some(sp => Math.abs(sp - splitTime) < 0.5);
        if (isDuplicate) return;
        setSplitPoints(prev => [...prev, Math.round(splitTime * 10) / 10].sort((a, b) => a - b));
        console.log(`[Split] Added split at ${splitTime.toFixed(1)}s (${mouseTimeRef.current != null ? 'mouse' : 'playhead'})`);
        return;
      }

      // Left/Right arrow = seek ±5s
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        if (videoRef.current) {
          const t = Math.max(0, videoRef.current.currentTime - 5);
          videoRef.current.currentTime = t;
          setCurrentTime(t);
        }
        return;
      }
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (videoRef.current) {
          const t = Math.min(duration || 0, videoRef.current.currentTime + 5);
          videoRef.current.currentTime = t;
          setCurrentTime(t);
        }
        return;
      }

      // Delete/Backspace = delete selected segment (CapCut-style)
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        if (e.ctrlKey) {
          // Ctrl+Backspace = remove last split point
          setSplitPoints(prev => prev.slice(0, -1));
          setDisabledSegments(new Set());
          setDeletedRanges([]);
        } else if (selectedSegIdx !== null) {
          // Delete selected segment completely
          deleteSegment(selectedSegIdx);
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [duration, currentTime, splitPoints, selectedSegIdx, deleteSegment]);

  // ─── Remove a split point ─────────────────────────────────────
  const removeSplitPoint = useCallback((splitTime) => {
    setSplitPoints(prev => prev.filter(sp => Math.abs(sp - splitTime) > 0.3));
    setDisabledSegments(new Set());
    setDeletedRanges([]);
  }, []);

  // ─── Waveform click to seek ───────────────────────────────────
  const onWaveformClick = useCallback((e) => {
    const container = waveformContainerRef.current;
    if (!container || !duration) return;
    const rect = container.getBoundingClientRect();
    const effDur = tlDuration || duration;
    const t = Math.max(0, Math.min(duration, ((e.clientX - rect.left) / rect.width) * effDur));
    seek(t);
  }, [duration, tlDuration, seek]);

  const onWaveformMouseMove = useCallback((e) => {
    const container = waveformContainerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const xPx = e.clientX - rect.left;
    const ratio = xPx / rect.width;
    setTimelineCursorPos(ratio); // store as ratio (0-1) for both bars
    // Calculate time from mouse position
    const effDur = tlDuration || duration || 1;
    mouseTimeRef.current = Math.max(0, Math.min(duration || 0, ratio * effDur));
  }, [duration, tlDuration]);

  const onWaveformMouseLeave = useCallback(() => {
    setTimelineCursorPos(null);
    mouseTimeRef.current = null;
  }, []);

  // ─── Timeline ──────────────────────────────────────────────────
  const onTLClick = useCallback(
    (e) => {
      if (!timelineRef.current || !duration) return;
      const rect = timelineRef.current.getBoundingClientRect();
      seek(Math.max(0, Math.min(duration, ((e.clientX - rect.left) / rect.width) * tlDuration)));
    },
    [duration, tlDuration, seek]
  );

  // ─── Trim Drag ─────────────────────────────────────────────────
  const onTrimDrag = useCallback(
    (e) => {
      if (!dragging || !timelineRef.current || !tlDuration) return;
      const rect = timelineRef.current.getBoundingClientRect();
      const localT = Math.max(0, Math.min(tlDuration, ((e.clientX - rect.left) / rect.width) * tlDuration));
      // Convert local timeline position to absolute time for trimStart/trimEnd
      const offset = isClipVideo ? origStart : 0;
      const t = localT + offset;
      if (dragging === "s" && t < trimEnd - 1) setTrimStart(Math.round(t * 10) / 10);
      if (dragging === "e" && t > trimStart + 1) setTrimEnd(Math.round(t * 10) / 10);
    },
    [dragging, tlDuration, trimStart, trimEnd, isClipVideo, origStart]
  );

  const onTrimEnd = useCallback(() => setDragging(null), []);

  useEffect(() => {
    if (dragging) {
      window.addEventListener("mousemove", onTrimDrag);
      window.addEventListener("mouseup", onTrimEnd);
      return () => {
        window.removeEventListener("mousemove", onTrimDrag);
        window.removeEventListener("mouseup", onTrimEnd);
      };
    }
  }, [dragging, onTrimDrag, onTrimEnd]);

  // ─── Apply Trim ────────────────────────────────────────────────
  const applyTrim = async () => {
    if (!clip?.clip_id) return;
    setIsTrimming(true);
    setStatus(null);
    try {
      const res = await VideoService.trimClip(videoId, clip.clip_id, trimStart, trimEnd);
      setStatus({ ok: true, msg: window.__t('clipEditorV2_7bc801', 'トリム適用中...') });
      if (onClipUpdated) onClipUpdated(res);

      // Log trim edits for AI learning
      try {
        if (trimStart !== origStart) {
          await VideoService.logClipEdit(videoId, {
            clip_id: clip.clip_id,
            edit_type: 'trim_start',
            before_value: { time_start: origStart },
            after_value: { time_start: trimStart },
            delta_seconds: trimStart - origStart,
          });
        }
        if (trimEnd !== origEnd) {
          await VideoService.logClipEdit(videoId, {
            clip_id: clip.clip_id,
            edit_type: 'trim_end',
            before_value: { time_end: origEnd },
            after_value: { time_end: trimEnd },
            delta_seconds: trimEnd - origEnd,
          });
        }
      } catch (logErr) {
        console.warn('[ClipEditor] Failed to log trim edit:', logErr);
      }
    } catch (e) {
      setStatus({ ok: false, msg: `トリム失敗: ${e.message}` });
    } finally {
      setIsTrimming(false);
    }
  };

  // ─── Caption Edit ──────────────────────────────────────────────
  const editCap = (i, txt) => {
    setCaptions((p) => {
      const u = [...p];
      u[i] = { ...u[i], text: txt };
      return u;
    });
  };

  // Delete a single caption
  const deleteCap = (i) => {
    setCaptions((p) => p.filter((_, idx) => idx !== i));
  };

  // Clear all captions
  const clearAllCaptions = () => {
    if (!window.confirm(window.__t('clipEditorV2_66c01a', '全ての字幕を削除しますか？この操作は保存するまで取り消せます。'))) return;
    setCaptions([]);
  };

  // Add a new empty caption
  const addCaption = () => {
    const lastCap = captions[captions.length - 1];
    const newStart = lastCap ? (lastCap.end || lastCap.start + 3) : 0;
    setCaptions((p) => [...p, {
      start: Math.round(newStart * 100) / 100,
      end: Math.round((newStart + 3) * 100) / 100,
      text: '',
      source: 'manual',
    }]);
  };

  // Load captions from master timeline transcripts
  const loadFromMasterTranscripts = () => {
    if (!timelineData?.transcripts?.length) {
      setStatus({ ok: false, msg: window.__t('clipEditorV2_2c5b15', 'マスター文字起こしデータがありません') });
      return;
    }
    if (!clip) return;
    const fromTranscripts = buildCaptionsFromTranscripts(timelineData.transcripts, clip);
    if (fromTranscripts.length > 0) {
      setCaptions(fromTranscripts);
      setStatus({ ok: true, msg: `マスター文字起こしから${fromTranscripts.length}${window.__t('clipEditorV2_f8e82b', '件の字幕を反映しました')}` });
    } else {
      // Try audio_text fallback
      if (timelineData?.phases?.length > 0) {
        const fallback = buildCaptionsFromAudioText(timelineData.phases, clip);
        if (fallback.length > 0) {
          setCaptions(fallback);
          setStatus({ ok: true, msg: `フェーズ音声テキストから${fallback.length}${window.__t('clipEditorV2_f8e82b', '件の字幕を反映しました')}` });
          return;
        }
      }
      setStatus({ ok: false, msg: window.__t('clipEditorV2_419c33', 'この区間に対応する文字起こしが見つかりません') });
    }
  };

  const saveCaps = async () => {
    if (!clip?.clip_id) return;
    setSavingCaps(true);
    setStatus(null);
    try {
      // Handle empty captions (clear all)
      if (captions.length === 0) {
        await VideoService.updateClipCaptions(videoId, clip.clip_id, []);
        setStatus({ ok: true, msg: window.__t('clipEditorV2_a72c04', '字幕を全て削除しました') });
        setSavingCaps(false);
        return;
      }
      // Apply caption offset to timestamps before saving (bake in the timing adjustment)
      const capsToSave = captions.map(c => ({
        ...c,
        start: captionOffset !== 0 ? Math.max(0, (c.start || 0) + captionOffset) : (c.start || 0),
        end: captionOffset !== 0 ? Math.max(0, (c.end || 0) + captionOffset) : (c.end || 0),
        ...(c.words ? {
          words: c.words.map(w => ({
            ...w,
            start: captionOffset !== 0 ? Math.max(0, (w.start || 0) + captionOffset) : (w.start || 0),
            end: captionOffset !== 0 ? Math.max(0, (w.end || 0) + captionOffset) : (w.end || 0),
          }))
        } : {}),
        source: 'saved',
      }));
      await VideoService.updateClipCaptions(videoId, clip.clip_id, capsToSave);

      // Log caption edit for AI learning
      try {
        await VideoService.logClipEdit(videoId, {
          clip_id: clip.clip_id,
          edit_type: 'caption_edit',
          before_value: { captions: (clip.captions || []).map(c => ({ start: c.start, text: c.text })) },
          after_value: { captions: capsToSave.map(c => ({ start: c.start, text: c.text })) },
          delta_seconds: null,
        });
      } catch (logErr) {
        console.warn('[ClipEditor] Failed to log caption edit:', logErr);
      }

      setCaptions(capsToSave);
      // Reset offset after baking it into timestamps
      if (captionOffset !== 0) {
        setCaptionOffset(0);
        setStatus({ ok: true, msg: `字幕を保存しました（タイミング${captionOffset > 0 ? '+' : ''}${captionOffset.toFixed(1)}sを適用済み）` });
      } else {
        setStatus({ ok: true, msg: window.__t('clipEditorV2_ca265f', '字幕を保存しました（AI学習に反映）') });
      }
    } catch (e) {
      setStatus({ ok: false, msg: `字幕保存失敗: ${e.message}` });
    } finally {
      setSavingCaps(false);
    }
  };

  // ─── On-demand Whisper Transcription ───────────────────────────
  const generateSubtitles = async () => {
    if (!videoId || !clip) return;
    setTranscribing(true);
    setStatus(null);
    try {
      const clipUrl = clip.clip_url || videoData?.video_url || clip.video_url;
      if (!clipUrl) throw new Error(window.__t('clipEditorV2_302abd', '動画URLが見つかりません'));
      const res = await VideoService.transcribeClip(videoId, {
        clip_url: clipUrl,
        time_start: clip.time_start || origStart,
        time_end: clip.time_end || origEnd,
        phase_index: clip.phase_index,
        target_language: targetLanguage,
      });
      if (res?.segments?.length > 0) {
        // API now returns LOCAL times (0-based relative to clip start)
        // Verify: if max start time > clip duration, something is wrong
        const clipDuration = (clip.time_end || origEnd) - (clip.time_start || origStart);
        const maxSegStart = Math.max(...res.segments.map(s => s.start || 0));
        const needsConversion = clipDuration > 0 && maxSegStart > clipDuration * 1.5;
        if (needsConversion) {
          console.warn(`[Subtitles] API returned absolute times (max=${maxSegStart.toFixed(1)}s > clipDur=${clipDuration.toFixed(1)}s), converting to local`);
        }
        const offset = needsConversion ? (clip.time_start || origStart) : 0;

        const newCaps = res.segments.map((s) => ({
          start: Math.max(0, (s.start || 0) - offset),
          end: Math.max(0, (s.end || 0) - offset),
          text: s.text,
          source: s.source || "whisper",
          // Include word-level timestamps for karaoke-style highlighting
          ...(s.words && s.words.length > 0 ? {
            words: s.words.map(w => ({
              ...w,
              start: Math.max(0, (w.start || 0) - offset),
              end: Math.max(0, (w.end || 0) - offset),
            }))
          } : {}),
        }));
        setCaptions(newCaps);
        setStatus({ ok: true, msg: `${newCaps.length}${window.__t('clipEditorV2_e4d6f5', '件の字幕を生成しました')}` });
        // Auto-save generated subtitles so they persist on next load
        if (clip?.clip_id) {
          try {
            const capsToSave = newCaps.map(c => ({ ...c, source: 'saved' }));
            await VideoService.updateClipCaptions(videoId, clip.clip_id, capsToSave);
            setCaptions(capsToSave);
            console.log("[Subtitles] Auto-saved generated subtitles (local times)");
          } catch (saveErr) {
            console.warn("[Subtitles] Auto-save failed:", saveErr);
          }
        }
      } else {
        setStatus({ ok: false, msg: window.__t('clipEditorV2_c3d740', '音声が検出されませんでした') });
      }
    } catch (e) {
      setStatus({ ok: false, msg: `字幕生成失敗: ${e.message}` });
    } finally {
      setTranscribing(false);
    }
  };

    // ─── Pop style: alternate font sizes for visual rhythm ───
  const renderPopText = (text) => {
    if (!text) return null;
    // Split into characters and alternate sizes
    const chars = [...text];
    const popColors = ['#FFE135', '#FF6B35', '#FF3CAC', '#00F5D4', '#FFF'];
    return chars.map((ch, i) => {
      const sizeVariant = i % 3 === 0 ? 1.3 : i % 3 === 1 ? 0.85 : 1.1;
      const colorIdx = Math.floor(i / 2) % popColors.length;
      return (
        <span
          key={i}
          style={{
            fontSize: `${(SUBTITLE_PRESETS.pop.text.fontSize * sizeVariant)}px`,
            color: popColors[colorIdx],
            display: 'inline',
          }}
        >
          {ch}
        </span>
      );
    });
  };

  // ─── Karaoke style: word-by-word highlight synced to playback ───
  const renderKaraokeText = (caption) => {
    if (!caption) return null;
    const preset = SUBTITLE_PRESETS.karaoke;
    const highlightColor = preset.highlightColor || '#FFE135';
    const dimColor = preset.text.color || 'rgba(255,255,255,0.5)';
    const t = currentTime;

    // If word-level timestamps are available, use them
    if (caption.words && caption.words.length > 0) {
      return caption.words.map((w, i) => {
        const wStart = toLocalTime(w.start || 0) + captionOffset;
        const wEnd = toLocalTime(w.end || 0) + captionOffset;
        const isActive = t >= wStart && t <= wEnd;
        const isPast = t > wEnd;
        return (
          <span
            key={i}
            style={{
              color: isActive ? highlightColor : isPast ? '#fff' : dimColor,
              fontWeight: isActive ? 900 : 700,
              fontSize: isActive ? `${(preset.text.fontSize || 18) * 1.15}px` : `${preset.text.fontSize || 18}px`,
              transition: 'color 0.15s ease, font-size 0.15s ease',
              display: 'inline',
            }}
          >
            {w.word}
          </span>
        );
      });
    }

    // Fallback: estimate word timing from segment start/end
    const chars = [...caption.text];
    const capStart = toLocalTime(caption.start || 0) + captionOffset;
    const capEnd = toLocalTime(caption.end || (caption.start + 5)) + captionOffset;
    const capDuration = capEnd - capStart;
    if (capDuration <= 0) return caption.text;

    const progress = Math.max(0, Math.min(1, (t - capStart) / capDuration));
    const highlightIdx = Math.floor(progress * chars.length);

    return chars.map((ch, i) => (
      <span
        key={i}
        style={{
          color: i <= highlightIdx ? highlightColor : dimColor,
          fontWeight: i === highlightIdx ? 900 : 700,
          transition: 'color 0.1s ease',
          display: 'inline',
        }}
      >
        {ch}
      </span>
    ));
  };

  // ─── AI Recommended style based on video genre + user feedback history ───
  const getAiRecommendedStyleLocal = () => {
    // Local fallback: determine recommendation based on video metadata
    const tags = videoData?.tags || [];
    const title = videoData?.title || clip?.description || '';
    const titleLower = title.toLowerCase();

    if (tags.some(t => /美容|コスメ|スキンケア|beauty/i.test(t)) || /美容|コスメ/i.test(titleLower)) {
      return { style: 'gradient', reason: window.__t('clipEditorV2_a1f47e', '美容系コンテンツに最適'), source: 'local' };
    }
    if (tags.some(t => /エンタメ|お笑い|バラエティ|funny|viral/i.test(t)) || /バズ|爆笑/i.test(titleLower)) {
      return { style: 'pop', reason: window.__t('clipEditorV2_5cf462', 'エンタメ系に最適・インパクト大'), source: 'local' };
    }
    if (tags.some(t => /ビジネス|解説|教育|business/i.test(t)) || /解説|まとめ/i.test(titleLower)) {
      return { style: 'simple', reason: window.__t('clipEditorV2_ff35a8', 'ビジネス系・読みやすさ重視'), source: 'local' };
    }
    if (clip?.ai_score && clip.ai_score >= 80) {
      return { style: 'outline', reason: window.__t('clipEditorV2_e18bec', '高スコアクリップ・目立たせるスタイル'), source: 'local' };
    }
    return { style: 'box', reason: window.__t('clipEditorV2_73bc1e', '万能型・どんな動画にも合う'), source: 'local' };
  };

  const [aiRecommendation, setAiRecommendation] = useState(() => getAiRecommendedStyleLocal());

  // Fetch personalized recommendation from backend (uses feedback history)
  useEffect(() => {
    if (!videoId) return;
    (async () => {
      try {
        const res = await VideoService.getSubtitleRecommendation(videoId);
        if (res?.recommendation) {
          setAiRecommendation({
            style: res.recommendation.style,
            reason: res.recommendation.reason,
            source: res.recommendation.source || 'api',
            confidence: res.recommendation.confidence,
            feedbackCount: res.user_feedback_count || 0,
          });
          console.log(`[AI Recommend] From API: ${res.recommendation.style} (${res.recommendation.source}, confidence=${res.recommendation.confidence})`);
        }
      } catch (e) {
        console.warn('[AI Recommend] API failed, using local fallback:', e);
        // Keep local fallback
      }
    })();
  }, [videoId]);

  // ─── Feedback tags ───
  const FEEDBACK_TAGS = [
    window.__t('clipEditorV2_47eeff', '見やすい'), window.__t('clipEditorV2_9991e2', '目立つ'), window.__t('clipEditorV2_6cb6cb', 'おしゃれ'), window.__t('clipEditorV2_cdf397', 'ポップ'),
    window.__t('autoVideoPage_561ecb', '落ち着いた'), window.__t('clipEditorV2_698371', '文字が小さい'), window.__t('clipEditorV2_27cb24', '文字が大きい'), window.__t('clipEditorV2_0a3576', '色を変えたい'),
  ];

  const toggleFeedbackTag = (tag) => {
    setFeedbackTags(prev =>
      prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]
    );
    setFeedbackSaved(false);
  };

  const saveFeedback = async () => {
    try {
      if (!clip?.clip_id) throw new Error('clip_id not found');
      // Save feedback to backend via API
      await VideoService.saveSubtitleFeedback(videoId, clip.clip_id, {
        style: subtitleStyle,
        vote: subtitleFeedback,
        tags: feedbackTags,
        position: subtitlePos,
        ai_recommended_style: aiRecommendation?.style || null,
      });
      // Also persist the style & position to the clip
      await VideoService.saveSubtitleStyle(videoId, clip.clip_id, {
        style: subtitleStyle,
        position_x: subtitlePos.x,
        position_y: subtitlePos.y,
      });
      setFeedbackSaved(true);
      setStatus({ ok: true, msg: window.__t('clipEditorV2_5fbb86', 'フィードバックを保存しました') });
    } catch (e) {
      console.error('[SubtitleFeedback] Save failed:', e);
      setStatus({ ok: false, msg: `フィードバック保存失敗: ${e.message}` });
    }
  };

  // ═══════════════════════════════════════════════════════════
  // RENDER
  // ═════════════════════════════════════════════════════════════════
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: C.bg,
        zIndex: 1000,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {/* ═══ HEADER ═══ */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "6px 16px",
          borderBottom: `1px solid ${C.border}`,
          backgroundColor: C.surface,
          flexShrink: 0,
          height: 40,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", color: C.textMuted, fontSize: 20, cursor: "pointer" }}
          >
            ‹
          </button>
          <span style={{ color: C.text, fontSize: 13, fontWeight: 700, letterSpacing: 1 }}>CLIP EDITOR</span>
          <span
            style={{
              fontSize: 11,
              color: C.textDim,
              padding: "2px 8px",
              backgroundColor: C.surfaceLight,
              borderRadius: 4,
            }}
          >
            {(() => {
              const key = String(clip.phase_index ?? "");
              if (key.startsWith("moment_")) return "Moment Clip";
              if (key.startsWith("sales_")) return "Sales Spike";
              if (key.startsWith("hook")) return "Hook";
              if (key.startsWith("ai_")) return "AI\u63A8\u85A6";
              if (/^\d+$/.test(key)) return `Phase ${Number(key) + 1}`;
              return key || "?";
            })()} | {fmt(origStart)} - {fmt(origEnd)}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {clip.clip_url && captions.length > 0 && (
            <button
              onClick={async () => {
                if (exporting) return;
                setExporting(true);
                setExportProgress(0);
                setStatus({ ok: true, msg: window.__t('clipEditorV2_488d3c', '字幕付きMP4を生成中...') });
                const statusLabels = {
                  queued: window.__t('clipEditorV2_553740', '順番待ち中...（他のエクスポートが完了次第開始します）'),
                  downloading: window.__t('clipEditorV2_100c56', 'クリップをダウンロード中...'),
                  encoding: window.__t('clipEditorV2_87baf3', '字幕を焼き込み中...'),
                  uploading: window.__t('statusUploading', 'アップロード中...'),
                  done: window.__t('clipEditorV2_bfa9d2', '完了！'),
                };
                try {
                  // ⚠️ PROTECTED: Filter out captions inside deleted ranges before export.
                  // Without this, subtitles from deleted scenes appear in the exported video
                  // with wrong timing (because the video is shorter after concat).
                  const exportCaptions = deletedRanges.length > 0
                    ? captions.filter(c => {
                        const cStart = toLocalTime(c.start || 0);
                        const cEnd = toLocalTime(c.end || (c.start + 5));
                        return !deletedRanges.some(dr => cStart >= dr.start && cEnd <= dr.end);
                      })
                    : captions;
                  const exportPayload = {
                    clip_url: clip.clip_url,
                    captions: exportCaptions.map(c => ({
                      start: c.start,
                      end: c.end,
                      text: c.text,
                      ...(c.words ? { words: c.words } : {}),
                    })),
                    style: subtitleStyle,
                    position_x: subtitlePos.x,
                    position_y: subtitlePos.y,
                    time_start: clip.time_start || origStart,
                    ...(splitPoints.length > 0 || deletedRanges.length > 0 ? {
                      split_segments: [
                        ...splitSegments.map(s => ({
                          start: s.start,
                          end: s.end,
                          enabled: true,
                        })),
                        ...deletedRanges.map(r => ({
                          start: r.start,
                          end: r.end,
                          enabled: false,
                        })),
                      ]
                    } : {}),
                  };
                  const progressCb = {
                    onProgress: (st, pct) => {
                      if (pct === -1) {
                        setStatus({ ok: true, msg: window.__t('clipEditorV2_eab627', '接続を再試行中...') });
                      } else {
                        setExportProgress(Math.max(0, Math.min(100, pct || 0)));
                        setStatus({ ok: true, msg: statusLabels[st] || `処理中 (${st})...` });
                      }
                    },
                  };
                  // Auto-retry up to 3 times on failure (Azure cold-start can cause timeouts)
                  let res = null;
                  let lastErr = null;
                  for (let attempt = 1; attempt <= 3; attempt++) {
                    try {
                      res = await VideoService.exportSubtitledClip(videoId, exportPayload, progressCb);
                      lastErr = null;
                      break; // success
                    } catch (retryErr) {
                      lastErr = retryErr;
                      console.warn(`[Export] Attempt ${attempt}/3 failed:`, retryErr.message);
                      if (attempt < 3) {
                        setStatus({ ok: true, msg: `エクスポートを再試行中... (${attempt}/3)` });
                        setExportProgress(0);
                        await new Promise(r => setTimeout(r, 2000)); // wait 2s before retry
                      }
                    }
                  }
                  if (lastErr) throw lastErr;
                  if (res?.download_url) {
                    // Use <a> tag download to avoid popup blockers
                    const a = document.createElement('a');
                    a.href = res.download_url;
                    a.download = `clip_phase${clip.phase_index || ''}_subtitled.mp4`;
                    a.target = '_blank';
                    a.rel = 'noopener noreferrer';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    // Record download for ML training (non-blocking)
                    VideoService.recordClipDownload(videoId, {
                      phase_index: clip.phase_index,
                      time_start: clip.time_start || origStart,
                      time_end: clip.time_end || origEnd,
                      clip_id: clip.id || null,
                      export_type: 'subtitled',
                    });
                    setStatus({ ok: true, msg: window.__t('clipEditorV2_adfd89', '字幕付きMP4のダウンロードを開始しました！') });
                    setTimeout(() => setStatus(null), 5000);
                  } else {
                    setStatus({ ok: true, msg: window.__t('clipEditorV2_ae5b51', 'エクスポート完了') });
                    setTimeout(() => setStatus(null), 3000);
                  }
                } catch (e) {
                  const errMsg = (e.message || 'Unknown error').slice(-200);
                  setStatus({ ok: false, msg: `エクスポート失敗: ${errMsg}` });
                  // Keep error visible for 10 seconds
                  setTimeout(() => setStatus(null), 10000);
                } finally {
                  setExporting(false);
                  setExportProgress(0);
                }
              }}
              disabled={exporting}
              style={{
                padding: "4px 14px",
                backgroundColor: exporting ? C.surfaceLight : C.green,
                color: "#fff",
                borderRadius: 6,
                border: "none",
                fontSize: 12,
                fontWeight: 600,
                cursor: exporting ? 'wait' : 'pointer',
                opacity: exporting ? 0.7 : 1,
                position: 'relative',
                overflow: 'hidden',
                minWidth: exporting ? 120 : 'auto',
              }}
            >
              {exporting && (
                <span style={{
                  position: 'absolute',
                  left: 0,
                  top: 0,
                  bottom: 0,
                  width: `${exportProgress}%`,
                  backgroundColor: C.green,
                  opacity: 0.3,
                  transition: 'width 0.5s ease',
                  borderRadius: 6,
                }} />
              )}
              <span style={{ position: 'relative', zIndex: 1 }}>
                {exporting ? `${exportProgress}%` : window.__t('clipEditorV2_a04538', '字幕付き Export')}
              </span>
            </button>
          )}
          {clip.clip_url && (
            <button
              onClick={async () => {
                // Fetch fresh SAS URL before downloading to avoid expired token errors
                try {
                  const freshRes = await VideoService.getClipStatus(videoId, clip.phase_index);
                  const freshUrl = freshRes?.clip_url || clip.clip_url;
                  const a = document.createElement('a');
                  a.href = freshUrl;
                  a.download = `clip_phase${clip.phase_index || ''}.mp4`;
                  a.target = '_blank';
                  a.rel = 'noopener noreferrer';
                  document.body.appendChild(a);
                  a.click();
                  document.body.removeChild(a);
                } catch (e) {
                  console.warn('[ClipEditor] Failed to fetch fresh URL, using cached:', e);
                  window.open(clip.clip_url, '_blank', 'noopener,noreferrer');
                }
                // Record raw download for ML training (non-blocking)
                VideoService.recordClipDownload(videoId, {
                  phase_index: clip.phase_index,
                  time_start: clip.time_start || origStart,
                  time_end: clip.time_end || origEnd,
                  clip_id: clip.id || null,
                  export_type: 'raw',
                });
              }}
              style={{
                padding: "4px 14px",
                backgroundColor: C.purple,
                color: "#fff",
                borderRadius: 6,
                border: "none",
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Export MP4
            </button>
          )}
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", color: C.textMuted, fontSize: 18, cursor: "pointer" }}
          >
            ✕
          </button>
        </div>
      </div>

      {/* ═══ MAIN: LEFT VIDEO + RIGHT PANEL ═══ */}
      <div style={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>
        {/* ─── LEFT: Video ─── */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "#000",
            position: "relative",
            overflow: "hidden",
          }}
        >
          {/* Inner container maintains 9:16 aspect ratio, height-based */}
          <div
            ref={videoContainerRef}
            onMouseMove={(e) => {
              if (!isDraggingSub || !videoContainerRef.current) return;
              const rect = videoContainerRef.current.getBoundingClientRect();
              const x = Math.max(5, Math.min(95, ((e.clientX - rect.left) / rect.width) * 100));
              const y = Math.max(5, Math.min(95, ((e.clientY - rect.top) / rect.height) * 100));
              setSubtitlePos({ x, y });
            }}
            onMouseUp={() => setIsDraggingSub(false)}
            onMouseLeave={() => setIsDraggingSub(false)}
            style={{
              position: "relative",
              height: "100%",
              aspectRatio: "9 / 16",
              maxWidth: "100%",
              backgroundColor: "#000",
            }}
          >
            {videoUrl ? (
              <video
                ref={videoRef}
                src={videoUrl}
                onTimeUpdate={onTimeUpdate}
                onLoadedMetadata={onMeta}
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
                onClick={toggle}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  cursor: "pointer",
                }}
              />
            ) : (
              <div
                style={{
                  width: "100%",
                  height: "100%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: C.textDim,
                  fontSize: 14,
                }}
              >
                プレビューなし
              </div>
            )}

            {/* Play overlay */}
            {!isPlaying && videoReady && (
              <div
                onClick={toggle}
                style={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: "pointer",
                  backgroundColor: "rgba(0,0,0,0.15)",
                }}
              >
                <div
                  style={{
                    width: 52,
                    height: 52,
                    borderRadius: "50%",
                    backgroundColor: "rgba(255,107,53,0.85)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 20,
                    color: "#fff",
                  }}
                >
                  ▶
                </div>
              </div>
            )}

            {/* Time + Phase overlay (top-left) */}
            <div
              style={{
                position: "absolute",
                top: 8,
                left: 8,
                padding: "3px 10px",
                borderRadius: 4,
                backgroundColor: "rgba(0,0,0,0.7)",
                color: "#fff",
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              {fmt(origStart)} – {fmt(origEnd)}
              {duration > 0 && clipDur > duration + 1 && (
                <span style={{ marginLeft: 4, opacity: 0.7, fontSize: 10, color: '#FFE135' }}>
                  ({duration.toFixed(0)}s)
                </span>
              )}
              <span style={{ marginLeft: 6, opacity: 0.6, fontSize: 10 }}>
                Phase {clip.phase_index != null && !isNaN(Number(clip.phase_index)) ? clip.phase_index : (clip.phase_index || "?")}
              </span>
            </div>

            {/* ★ SUBTITLE OVERLAY ★ */}
            {currentCaption && (() => {
              const preset = SUBTITLE_PRESETS[subtitleStyle] || SUBTITLE_PRESETS.box;
              const presetText = preset.text || {};
              return (
                <div
                  ref={subtitleContainerRef}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setIsDraggingSub(true);
                  }}
                  style={{
                    position: "absolute",
                    left: `${subtitlePos.x}%`,
                    top: `${subtitlePos.y}%`,
                    transform: "translate(-50%, -50%)",
                    textAlign: "center",
                    pointerEvents: "auto",
                    zIndex: 10,
                    cursor: isDraggingSub ? "grabbing" : "grab",
                    maxWidth: "85%",
                    userSelect: "none",
                    overflow: "hidden",
                    wordBreak: "keep-all",
                    transition: isDraggingSub ? 'none' : 'left 0.1s ease, top 0.1s ease',
                  }}
                >
                  <span
                    style={{
                      display: "inline-block",
                      ...presetText,
                      ...(subtitleFontSize > 0 ? { fontSize: subtitleFontSize } : {}),
                      ...(currentCaption.emphasis && subtitleStyle !== 'pop' ? {
                        color: C.yellow,
                        fontWeight: 800,
                      } : {}),
                    }}
                  >
                    {subtitleStyle === 'karaoke'
                      ? renderKaraokeText(currentCaption)
                      : subtitleStyle === 'pop'
                        ? renderPopText(currentCaption.text)
                        : currentCaption.text}
                  </span>
                </div>
              );
            })()}
          </div>
        </div>

        {/* ─── RIGHT: Info Panel ─── */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            borderLeft: `1px solid ${C.border}`,
            backgroundColor: C.surface,
            overflow: "hidden",
          }}
        >
          {/* Tabs */}
          <div
            style={{
              display: "flex",
              flexShrink: 0,
              borderBottom: `1px solid ${C.border}`,
              backgroundColor: C.bg,
            }}
          >
            {[
              { k: "captions", l: window.__t('auto_334', '字幕') },
              { k: "info", l: window.__t('clipEditorV2_fcc18e', 'AI分析') },
              { k: "trim", l: "Trim" },
              { k: "feedback", l: window.__t('clipEditorV2_6745a8', '評価') },
            ].map((t) => (
              <button
                key={t.k}
                onClick={() => setTab(t.k)}
                style={{
                  flex: 1,
                  padding: "9px 0",
                  border: "none",
                  backgroundColor: tab === t.k ? C.surface : "transparent",
                  color: tab === t.k ? C.text : C.textDim,
                  cursor: "pointer",
                  fontSize: 13,
                  fontWeight: tab === t.k ? 600 : 400,
                  borderBottom: tab === t.k ? `2px solid ${C.accent}` : "2px solid transparent",
                }}
              >
                {t.l}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          <div style={{ flex: 1, overflow: "auto", padding: "14px 16px" }}>
            {/* ─── AI分析 ─── */}
            {tab === "info" && (
              <div>
                {/* Time badge */}
                <div
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "5px 14px",
                    borderRadius: 20,
                    backgroundColor: C.accent + "22",
                    border: `1px solid ${C.accent}44`,
                    marginBottom: 14,
                  }}
                >
                  <span style={{ fontSize: 12 }}>⏱</span>
                  <span style={{ color: C.accent, fontSize: 13, fontWeight: 600 }}>
                    {fmt(origStart)} – {fmt(origEnd)}
                  </span>
                </div>

                {/* Tags row */}
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 14 }}>
                  {clip.clip_type && (
                    <span style={tagStyle(C.yellow)}>{clip.clip_type.toUpperCase()}</span>
                  )}
                  {clip.ai_score != null && (
                    <span style={tagStyle(scoreColor(clip.ai_score))}>
                      Score: {Math.round(clip.ai_score)}
                    </span>
                  )}
                </div>

                {/* AI Score Cards */}
                <Section title={window.__t('clipEditorV2_a4a61c', 'AI 評価')}>
                  {[
                    { l: window.__t('clipEditorV2_eab3a2', 'バイラル度'), s: currentPhase?.viral_score, i: "🔥" },
                    { l: window.__t('clipEditorV2_5245c0', 'フック力'), s: currentPhase?.hook_score, i: "🎣" },
                    { l: window.__t('clipEditorV2_c9e6bc', 'エンゲージメント'), s: currentPhase?.engagement_score, i: "💬" },
                    { l: window.__t('clipEditorV2_af5ec3', '発話エネルギー'), s: currentPhase?.speech_energy, i: "🎤" },
                  ].map((x, idx) => (
                    <ScoreRow key={idx} icon={x.i} label={x.l} score={x.s} />
                  ))}
                </Section>

                {/* AI Summary */}
                {clip.description && (
                  <Section title={window.__t('clipEditorV2_bb6169', 'AI要約')}>
                    <p
                      style={{
                        color: C.text,
                        fontSize: 13,
                        lineHeight: 1.7,
                        margin: 0,
                        padding: 12,
                        backgroundColor: C.surfaceLight,
                        borderRadius: 8,
                      }}
                    >
                      {clip.description}
                    </p>
                  </Section>
                )}

                {/* Video Score */}
                {videoScore?.overall_score != null && (
                  <Section title={window.__t('clipEditorV2_b5c20b', '動画全体スコア')}>
                    <div
                      style={{
                        padding: 12,
                        backgroundColor: C.surfaceLight,
                        borderRadius: 8,
                        border: `1px solid ${scoreColor(videoScore.overall_score, 0.3)}`,
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ color: C.textMuted, fontSize: 12 }}>Overall</span>
                        <span
                          style={{
                            fontSize: 26,
                            fontWeight: 800,
                            color: scoreColor(videoScore.overall_score),
                          }}
                        >
                          {Math.round(videoScore.overall_score)}
                        </span>
                      </div>
                    </div>
                  </Section>
                )}

                {/* AI Markers */}
                {timelineData?.markers?.length > 0 && (
                  <Section title={`AI マーカー (${timelineData.markers.length})`}>
                    {timelineData.markers.slice(0, 8).map((m, i) => {
                      const mi = MARKERS[m.type] || MARKERS.sales;
                      return (
                        <div
                          key={i}
                          onClick={() => seek(m.time_start)}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                            padding: "5px 10px",
                            marginBottom: 3,
                            backgroundColor: C.bg,
                            borderRadius: 5,
                            cursor: "pointer",
                            fontSize: 12,
                            border: `1px solid ${C.border}`,
                          }}
                        >
                          <span>{mi.icon}</span>
                          <span style={{ color: C.accent, fontWeight: 600, minWidth: 38 }}>
                            {fmt(m.time_start)}
                          </span>
                          <span
                            style={{
                              color: C.text,
                              flex: 1,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {m.label || m.description || mi.label}
                          </span>
                        </div>
                      );
                    })}
                  </Section>
                )}
              </div>
            )}

            {/* ─── 字幕 ─── */}
            {tab === "captions" && (
              <div>
                {/* ═══ 字幕スタイル選択 ═══ */}
                <SectionTitle>{window.__t('clipEditorV2_163132', '字幕スタイル')}</SectionTitle>

                {/* AIおすすめバッジ */}
                <div
                  onClick={() => setSubtitleStyle(aiRecommendation.style)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '8px 12px',
                    marginBottom: 10,
                    borderRadius: 8,
                    backgroundColor: subtitleStyle === aiRecommendation.style ? C.accent + '22' : C.surfaceLight,
                    border: `1px solid ${subtitleStyle === aiRecommendation.style ? C.accent : C.border}`,
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                  }}
                >
                  <span style={{ fontSize: 14 }}>{'✨'}</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ color: C.accent, fontSize: 11, fontWeight: 700 }}>
                      AIおすすめ
                      {aiRecommendation.source === 'user_feedback' && (
                        <span style={{ marginLeft: 6, color: C.green, fontSize: 9, fontWeight: 500 }}>
                          パーソナライズ済み
                        </span>
                      )}
                      {aiRecommendation.confidence && (
                        <span style={{ marginLeft: 4, color: C.textMuted, fontSize: 9, fontWeight: 400 }}>
                          ({Math.round(aiRecommendation.confidence * 100)}%)
                        </span>
                      )}
                    </div>
                    <div style={{ color: C.textMuted, fontSize: 10 }}>
                      {SUBTITLE_PRESETS[aiRecommendation.style]?.name} — {aiRecommendation.reason}
                      {aiRecommendation.feedbackCount > 0 && (
                        <span style={{ marginLeft: 4, color: C.blue, fontSize: 9 }}>
                          ({aiRecommendation.feedbackCount}件のフィードバックに基づく)
                        </span>
                      )}
                    </div>
                  </div>
                  {subtitleStyle === aiRecommendation.style && (
                    <span style={{ color: C.accent, fontSize: 12, fontWeight: 700 }}>{'✓'}</span>
                  )}
                </div>

                {/* スタイルプリセットグリッド */}
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(6, 1fr)',
                  gap: 6,
                  marginBottom: 14,
                }}>
                  {SUBTITLE_PRESET_ORDER.map((key) => {
                    const p = SUBTITLE_PRESETS[key];
                    const isActive = subtitleStyle === key;
                    const isAiPick = aiRecommendation.style === key;
                    return (
                      <div
                        key={key}
                        onClick={() => setSubtitleStyle(key)}
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'center',
                          gap: 4,
                          padding: '8px 4px',
                          borderRadius: 8,
                          backgroundColor: isActive ? C.accent + '22' : C.surfaceLight,
                          border: `2px solid ${isActive ? C.accent : 'transparent'}`,
                          cursor: 'pointer',
                          transition: 'all 0.2s ease',
                          position: 'relative',
                        }}
                      >
                        {isAiPick && (
                          <span style={{
                            position: 'absolute',
                            top: -4,
                            right: -4,
                            fontSize: 10,
                            backgroundColor: C.accent,
                            color: '#fff',
                            borderRadius: 10,
                            padding: '0 4px',
                            fontWeight: 700,
                            lineHeight: '16px',
                          }}>AI</span>
                        )}
                        {/* Mini preview */}
                        <div style={{
                          width: '100%',
                          height: 32,
                          borderRadius: 4,
                          backgroundColor: '#000',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          overflow: 'hidden',
                        }}>
                          <span style={{
                            fontSize: 11,
                            fontWeight: p.text.fontWeight || 600,
                            color: p.text.color || '#fff',
                            textShadow: (p.text.textShadow || '').slice(0, 60),
                            backgroundColor: p.text.backgroundColor || 'transparent',
                            background: p.text.background || p.text.backgroundColor || 'transparent',
                            padding: '2px 6px',
                            borderRadius: p.text.borderRadius || 0,
                            WebkitTextStroke: p.text.WebkitTextStroke || 'none',
                            paintOrder: p.text.paintOrder || 'normal',
                          }}>{p.icon}</span>
                        </div>
                        <span style={{ color: isActive ? C.accent : C.textMuted, fontSize: 9, fontWeight: 600 }}>
                          {p.name}
                        </span>
                      </div>
                    );
                  })}
                </div>

                {/* 位置リセットボタン */}
                <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
                  <button
                    onClick={() => setSubtitlePos({ x: 50, y: 85 })}
                    style={{
                      flex: 1,
                      padding: '6px 8px',
                      border: `1px solid ${C.border}`,
                      borderRadius: 6,
                      backgroundColor: C.surfaceLight,
                      color: C.textMuted,
                      fontSize: 10,
                      cursor: 'pointer',
                    }}
                  >
                    {'↓'} 下配置
                  </button>
                  <button
                    onClick={() => setSubtitlePos({ x: 50, y: 50 })}
                    style={{
                      flex: 1,
                      padding: '6px 8px',
                      border: `1px solid ${C.border}`,
                      borderRadius: 6,
                      backgroundColor: C.surfaceLight,
                      color: C.textMuted,
                      fontSize: 10,
                      cursor: 'pointer',
                    }}
                  >
                    {'↔'} 中央配置
                  </button>
                  <button
                    onClick={() => setSubtitlePos({ x: 50, y: 15 })}
                    style={{
                      flex: 1,
                      padding: '6px 8px',
                      border: `1px solid ${C.border}`,
                      borderRadius: 6,
                      backgroundColor: C.surfaceLight,
                      color: C.textMuted,
                      fontSize: 10,
                      cursor: 'pointer',
                    }}
                  >
                    {'↑'} 上配置
                  </button>
                </div>

                <p style={{ color: C.textDim, fontSize: 9, margin: '0 0 14px', textAlign: 'center' }}>
                  プレビュー上の字幕をドラッグして位置を調整できます
                </p>

                {/* ═══ フォントサイズ ═══ */}
                <SectionTitle>{window.__t('clipEditorV2_61c13c', 'フォントサイズ')}</SectionTitle>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                  <span style={{ color: C.textDim, fontSize: 10, minWidth: 16 }}>A</span>
                  <input
                    type="range"
                    min={10}
                    max={48}
                    step={1}
                    value={subtitleFontSize || (SUBTITLE_PRESETS[subtitleStyle]?.text?.fontSize || 16)}
                    onChange={(e) => setSubtitleFontSize(parseInt(e.target.value))}
                    style={{
                      flex: 1,
                      height: 4,
                      accentColor: C.accent,
                      cursor: 'pointer',
                    }}
                  />
                  <span style={{ color: C.textDim, fontSize: 14, fontWeight: 700, minWidth: 16 }}>A</span>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                    backgroundColor: C.bg,
                    borderRadius: 6,
                    border: `1px solid ${C.border}`,
                    padding: '4px 8px',
                    minWidth: 48,
                    justifyContent: 'center',
                  }}>
                    <span style={{ color: C.text, fontSize: 12, fontWeight: 600 }}>
                      {subtitleFontSize || (SUBTITLE_PRESETS[subtitleStyle]?.text?.fontSize || 16)}
                    </span>
                  </div>
                  <button
                    onClick={() => setSubtitleFontSize(0)}
                    style={{
                      padding: '4px 8px',
                      border: `1px solid ${C.border}`,
                      borderRadius: 6,
                      backgroundColor: subtitleFontSize === 0 ? C.accent + '22' : C.surfaceLight,
                      color: subtitleFontSize === 0 ? C.accent : C.textMuted,
                      fontSize: 10,
                      cursor: 'pointer',
                    }}
                  >
                    リセット
                  </button>
                </div>

                {/* ═══ 字幕フィードバック ═══ */}
                <SectionTitle>{window.__t('clipEditorV2_a10aa2', '字幕フィードバック')}</SectionTitle>
                <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  {['up', 'down'].map((vote) => (
                    <button
                      key={vote}
                      onClick={() => { setSubtitleFeedback(prev => prev === vote ? null : vote); setFeedbackSaved(false); }}
                      style={{
                        flex: 1,
                        padding: '8px 12px',
                        border: `1px solid ${subtitleFeedback === vote ? (vote === 'up' ? C.green : C.red) : C.border}`,
                        borderRadius: 8,
                        backgroundColor: subtitleFeedback === vote
                          ? (vote === 'up' ? C.green + '22' : C.red + '22')
                          : C.surfaceLight,
                        color: subtitleFeedback === vote
                          ? (vote === 'up' ? C.green : C.red)
                          : C.textMuted,
                        fontSize: 16,
                        cursor: 'pointer',
                        transition: 'all 0.2s ease',
                      }}
                    >
                      {vote === 'up' ? '\uD83D\uDC4D' : '\uD83D\uDC4E'}
                    </button>
                  ))}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
                  {FEEDBACK_TAGS.map((tag) => {
                    const isSelected = feedbackTags.includes(tag);
                    return (
                      <button
                        key={tag}
                        onClick={() => toggleFeedbackTag(tag)}
                        style={{
                          padding: '4px 10px',
                          border: `1px solid ${isSelected ? C.accent : C.border}`,
                          borderRadius: 16,
                          backgroundColor: isSelected ? C.accent + '22' : 'transparent',
                          color: isSelected ? C.accent : C.textMuted,
                          fontSize: 10,
                          cursor: 'pointer',
                          transition: 'all 0.2s ease',
                        }}
                      >
                        {tag}
                      </button>
                    );
                  })}
                </div>
                {(subtitleFeedback || feedbackTags.length > 0) && !feedbackSaved && (
                  <button
                    onClick={saveFeedback}
                    style={{
                      width: '100%',
                      padding: '8px 16px',
                      border: 'none',
                      borderRadius: 8,
                      backgroundColor: C.green,
                      color: '#fff',
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: 'pointer',
                      marginBottom: 14,
                    }}
                  >
                    フィードバックを保存
                  </button>
                )}
                {feedbackSaved && (
                  <p style={{ color: C.green, fontSize: 10, textAlign: 'center', margin: '0 0 14px' }}>
                    {'✓'} フィードバックを保存しました。AIが学習します。
                  </p>
                )}

                <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 14, marginTop: 4 }} />

                {/* ═══ 字幕タイミング調整 ═══ */}
                {captions.length > 0 && (
                  <div style={{ marginBottom: 14 }}>
                    <SectionTitle>{window.__t('clipEditorV2_40fc77', '字幕タイミング調整')}</SectionTitle>
                    <p style={{ color: C.textMuted, fontSize: 11, margin: '0 0 8px', lineHeight: 1.5 }}>
                      字幕が音声とずれている場合に調整できます。
                      {captionOffset > 0 ? `+${captionOffset.toFixed(1)}s（字幕を遅らせる）` : captionOffset < 0 ? `${captionOffset.toFixed(1)}s（字幕を早める）` : window.__t('clipEditorV2_21491c', '0s（調整なし）')}
                    </p>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <span style={{ color: C.textDim, fontSize: 10, minWidth: 28 }}>-2s</span>
                      <input
                        type="range"
                        min={-2}
                        max={2}
                        step={0.1}
                        value={captionOffset}
                        onChange={(e) => setCaptionOffset(parseFloat(e.target.value))}
                        style={{
                          flex: 1,
                          height: 4,
                          accentColor: C.accent,
                          cursor: 'pointer',
                        }}
                      />
                      <span style={{ color: C.textDim, fontSize: 10, minWidth: 28, textAlign: 'right' }}>+2s</span>
                    </div>
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                      {[-0.5, -0.2, 0, 0.2, 0.5].map((v) => (
                        <button
                          key={v}
                          onClick={() => setCaptionOffset(v)}
                          style={{
                            padding: '4px 10px',
                            border: `1px solid ${captionOffset === v ? C.accent : C.border}`,
                            borderRadius: 6,
                            backgroundColor: captionOffset === v ? C.accent + '22' : C.surfaceLight,
                            color: captionOffset === v ? C.accent : C.textMuted,
                            fontSize: 11,
                            fontWeight: captionOffset === v ? 600 : 400,
                            cursor: 'pointer',
                          }}
                        >
                          {v === 0 ? '0' : v > 0 ? `+${v}` : v}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* ═══ 字幕編集 ═══ */}
                <SectionTitle>{window.__t('auto_336', '字幕編集')}</SectionTitle>
                <p style={{ color: C.textMuted, fontSize: 11, margin: "0 0 10px", lineHeight: 1.5 }}>
                  配信者の音声書き起こしです。テキストを直接編集・削除できます。タイムスタンプをクリックするとその位置にジャンプします。
                </p>

                {/* Language selector */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <span style={{ color: C.textMuted, fontSize: 11, whiteSpace: 'nowrap' }}>{window.__t('clipEditorV2_4d4921', '字幕言語:')}</span>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {[
                      { value: 'ja', label: window.__t('clipEditorV2_fd32f4', '🇯🇵 日本語') },
                      { value: 'zh-TW', label: window.__t('clipEditorV2_7b39f2', '🇹🇼 繁體中文') },
                    ].map(lang => (
                      <button
                        key={lang.value}
                        onClick={() => setTargetLanguage(lang.value)}
                        style={{
                          padding: '4px 10px',
                          border: `1px solid ${targetLanguage === lang.value ? C.accent : C.border}`,
                          borderRadius: 6,
                          backgroundColor: targetLanguage === lang.value ? C.accent + '22' : 'transparent',
                          color: targetLanguage === lang.value ? C.accent : C.textMuted,
                          fontSize: 11,
                          fontWeight: targetLanguage === lang.value ? 600 : 400,
                          cursor: 'pointer',
                          transition: 'all 0.15s',
                        }}
                      >
                        {lang.label}
                      </button>
                    ))}
                  </div>
                  {targetLanguage === 'zh-TW' && (
                    <span style={{ color: C.textDim, fontSize: 10 }}>{window.__t('clipEditorV2_155d2b', '台湾語音声→繁體中文字幕')}</span>
                  )}
                </div>
                {captions.length > 0 && captions[0]?.source && (
                  <p style={{ color: C.textDim, fontSize: 10, margin: "0 0 8px" }}>
                    データソース: {captions[0].source === "whisper" ? "Whisper音声認識（オンデマンド）" : captions[0].source === "transcript" ? "Whisper音声認識" : captions[0].source === "audio_text" ? "フェーズ音声テキスト" : captions[0].source === "saved" ? "保存済み" : captions[0].source === "manual" ? "手動追加" : captions[0].source === "master_transcript" ? "マスター文字起こし" : "クリップ字幕"}
                  </p>
                )}

                {/* Action buttons row */}
                <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
                  {/* Generate subtitles button */}
                  <button
                    onClick={generateSubtitles}
                    disabled={transcribing}
                    style={{
                      flex: '1 1 auto',
                      padding: '8px 12px',
                      border: `1px solid ${C.accent}66`,
                      borderRadius: 8,
                      backgroundColor: transcribing ? C.surfaceLight : C.accent + '22',
                      color: C.accent,
                      fontSize: 11,
                      fontWeight: 600,
                      cursor: transcribing ? 'not-allowed' : 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: 4,
                      opacity: transcribing ? 0.7 : 1,
                      minWidth: 0,
                    }}
                  >
                    {transcribing ? (
                      <><span style={{ animation: 'spin 1s linear infinite', display: 'inline-block' }}>{'⟳'}</span> {window.__t('script_generating', '生成中...')}</>
                    ) : (
                      <>{captions.length > 0 ? [window.__t('clipEditorV2_4b39ae', '🎤 AI再生成')] : window.__t('clipEditorV2_de99d4', '🎤 AI生成')}</>
                    )}
                  </button>

                  {/* Load from master transcripts button */}
                  <button
                    onClick={loadFromMasterTranscripts}
                    style={{
                      flex: '1 1 auto',
                      padding: '8px 12px',
                      border: `1px solid ${C.blue || '#3b82f6'}66`,
                      borderRadius: 8,
                      backgroundColor: (C.blue || '#3b82f6') + '18',
                      color: C.blue || '#3b82f6',
                      fontSize: 11,
                      fontWeight: 600,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: 4,
                      minWidth: 0,
                    }}
                  >
                    {'📝'} マスター反映
                  </button>

                  {/* Clear all captions button */}
                  {captions.length > 0 && (
                    <button
                      onClick={clearAllCaptions}
                      style={{
                        flex: '0 0 auto',
                        padding: '8px 12px',
                        border: `1px solid ${C.red || '#ef4444'}44`,
                        borderRadius: 8,
                        backgroundColor: (C.red || '#ef4444') + '12',
                        color: C.red || '#ef4444',
                        fontSize: 11,
                        fontWeight: 600,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 4,
                      }}
                    >
                      {'🗑'} 全削除
                    </button>
                  )}
                </div>

                {transcribing && (
                  <p style={{ color: C.textMuted, fontSize: 10, textAlign: 'center', margin: '0 0 10px' }}>
                    OpenAI Whisperで音声を書き起こしています。30秒〜1分程度かかります。
                  </p>
                )}
                {captions.length === 0 && !transcribing ? (
                  <div
                    style={{
                      color: C.textDim,
                      textAlign: 'center',
                      padding: 24,
                      fontSize: 13,
                      backgroundColor: C.surfaceLight,
                      borderRadius: 8,
                    }}
                  >
                    字幕がありません。
                    <br />
                    <span style={{ fontSize: 11 }}>{window.__t('clipEditorV2_4a1c40', '「AI生成」で音声認識、または「マスター反映」でタイムラインの文字起こしを使用できます。')}</span>
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {captions.map((cap, i) => {
                      const isActive = currentCaption === cap;
                      return (
                        <div
                          key={i}
                          style={{
                            display: 'flex',
                            gap: 6,
                            padding: '8px 10px',
                            backgroundColor: isActive ? C.accent + '18' : C.surfaceLight,
                            borderRadius: 6,
                            border: isActive ? `1px solid ${C.accent}55` : '1px solid transparent',
                            transition: 'all 0.2s ease',
                            alignItems: 'flex-start',
                          }}
                        >
                          <span
                            onClick={() => {
                              const localT = toLocalTime(cap.start);
                              seek(Math.max(0, localT));
                            }}
                            style={{
                              color: C.accent,
                              fontSize: 11,
                              minWidth: 42,
                              fontWeight: 600,
                              cursor: 'pointer',
                              paddingTop: 3,
                              flexShrink: 0,
                            }}
                          >
                            {fmt(cap.start)}
                          </span>
                          <textarea
                            value={cap.text}
                            onChange={(e) => editCap(i, e.target.value)}
                            rows={2}
                            style={{
                              flex: 1,
                              padding: '4px 8px',
                              backgroundColor: C.bg,
                              border: `1px solid ${C.border}`,
                              borderRadius: 5,
                              color: cap.emphasis ? C.yellow : C.text,
                              fontSize: 13,
                              fontWeight: cap.emphasis ? 700 : 400,
                              lineHeight: 1.5,
                              outline: 'none',
                              resize: 'vertical',
                              minHeight: 36,
                              fontFamily: 'inherit',
                              transition: 'border-color 0.2s ease',
                            }}
                            onFocus={(e) => {
                              e.target.style.borderColor = C.accent;
                            }}
                            onBlur={(e) => {
                              e.target.style.borderColor = C.border;
                            }}
                          />
                          {/* Delete single caption button */}
                          <button
                            onClick={() => deleteCap(i)}
                            title={window.__t('clipEditorV2_40dc07', 'この字幕を削除')}
                            style={{
                              flexShrink: 0,
                              width: 24,
                              height: 24,
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              border: 'none',
                              borderRadius: 4,
                              backgroundColor: 'transparent',
                              color: C.textDim,
                              fontSize: 14,
                              cursor: 'pointer',
                              padding: 0,
                              marginTop: 2,
                              transition: 'all 0.15s ease',
                            }}
                            onMouseEnter={(e) => {
                              e.target.style.backgroundColor = (C.red || '#ef4444') + '22';
                              e.target.style.color = C.red || '#ef4444';
                            }}
                            onMouseLeave={(e) => {
                              e.target.style.backgroundColor = 'transparent';
                              e.target.style.color = C.textDim;
                            }}
                          >
                            {'×'}
                          </button>
                        </div>
                      );
                    })}

                    {/* Add caption + Save buttons */}
                    <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                      <button
                        onClick={addCaption}
                        style={{
                          flex: 1,
                          padding: '8px 16px',
                          border: `1px dashed ${C.border}`,
                          borderRadius: 8,
                          backgroundColor: 'transparent',
                          color: C.textMuted,
                          fontSize: 12,
                          fontWeight: 500,
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          gap: 4,
                        }}
                      >
                        {'+'} 字幕を追加
                      </button>
                      <button
                        onClick={saveCaps}
                        disabled={savingCaps}
                        style={{
                          flex: 1,
                          padding: '8px 16px',
                          border: 'none',
                          borderRadius: 8,
                          backgroundColor: C.green,
                          color: '#fff',
                          fontSize: 13,
                          fontWeight: 600,
                          cursor: 'pointer',
                          opacity: savingCaps ? 0.6 : 1,
                        }}
                      >
                        {savingCaps ? [window.__t('auto_330', '保存中...')] : window.__t('clipEditorV2_811028', '字幕を保存')}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ─── Trim ─── */}
            {tab === "trim" && (
              <div>
                <SectionTitle>{window.__t('clipEditorV2_7ae953', 'トリム編集')}</SectionTitle>
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 14,
                    padding: 14,
                    backgroundColor: C.surfaceLight,
                    borderRadius: 8,
                  }}
                >
                  <TrimControl
                    label={window.__t('live_startTime', '開始時間')}
                    value={trimStart}
                    onChange={(v) => v < trimEnd - 1 && v >= 0 && setTrimStart(Math.round(v * 10) / 10)}
                  />
                  <TrimControl
                    label={window.__t('live_endTime', '終了時間')}
                    value={trimEnd}
                    onChange={(v) => v > trimStart + 1 && setTrimEnd(Math.round(v * 10) / 10)}
                  />
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      padding: "6px 10px",
                      backgroundColor: C.bg,
                      borderRadius: 6,
                    }}
                  >
                    <span style={{ color: C.textMuted, fontSize: 12 }}>{window.__t('clipEditorV2_d28cd4', 'クリップ長')}</span>
                    <span style={{ color: C.text, fontSize: 15, fontWeight: 700 }}>
                      {(duration > 0 ? Math.min(clipDur, duration) : clipDur).toFixed(1)}秒
                    </span>
                    {duration > 0 && clipDur > duration + 1 && (
                      <span style={{ color: C.textDim, fontSize: 10 }}>
                        (元: {clipDur.toFixed(0)}s → 無音除去後: {duration.toFixed(0)}s)
                      </span>
                    )}
                  </div>
                  <button
                    onClick={applyTrim}
                    disabled={isTrimming || (trimStart === origStart && trimEnd === origEnd)}
                    style={{
                      padding: "10px 20px",
                      border: "none",
                      borderRadius: 8,
                      backgroundColor:
                        trimStart === origStart && trimEnd === origEnd ? C.surfaceLight : C.accent,
                      color: "#fff",
                      fontSize: 13,
                      fontWeight: 600,
                      cursor:
                        trimStart === origStart && trimEnd === origEnd ? "not-allowed" : "pointer",
                      opacity: isTrimming ? 0.6 : 1,
                      width: "100%",
                    }}
                  >
                    {isTrimming ? [window.__t('script_generating', '生成中...')] : window.__t('clipEditorV2_4117f4', 'トリムを適用')}
                  </button>
                </div>
              </div>
            )}

            {/* ─── 評価 ─── */}
            {tab === "feedback" && (
              <ClipFeedbackPanel
                videoId={videoId}
                phaseIndex={clip.phase_index != null ? (isNaN(Number(clip.phase_index)) ? String(clip.phase_index) : Number(clip.phase_index)) : null}
                timeStart={clip.time_start || origStart}
                timeEnd={clip.time_end || origEnd}
                clipId={clip.clip_id}
                aiScore={clip.ai_score}
                scoreBreakdown={clip.score_breakdown}
              />
            )}
          </div>

          {/* Status */}
          {status && (
            <div
              style={{
                margin: "0 14px 10px",
                padding: "6px 10px",
                borderRadius: 6,
                flexShrink: 0,
                backgroundColor: status.ok ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)",
                color: status.ok ? C.green : C.red,
                fontSize: 12,
                border: `1px solid ${status.ok ? C.green : C.red}`,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ flex: 1 }}>{status.msg}</span>
                {exporting && <span style={{ fontWeight: 700, fontSize: 13 }}>{exportProgress}%</span>}
              </div>
              {exporting && exportProgress > 0 && (
                <div style={{
                  marginTop: 6,
                  height: 4,
                  borderRadius: 2,
                  backgroundColor: 'rgba(16,185,129,0.2)',
                  overflow: 'hidden',
                }}>
                  <div style={{
                    height: '100%',
                    width: `${exportProgress}%`,
                    backgroundColor: C.green,
                    borderRadius: 2,
                    transition: 'width 0.5s ease',
                  }} />
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ═══ BOTTOM: Timeline + Controls ═══ */}
      <div
        style={{
          padding: "6px 16px 8px",
          borderTop: `1px solid ${C.border}`,
          backgroundColor: C.surface,
          flexShrink: 0,
        }}
      >
        {/* Timeline bar */}
        <div
          ref={timelineRef}
          onClick={onTLClick}
          onMouseMove={(e) => {
            const rect = timelineRef.current?.getBoundingClientRect();
            if (!rect) return;
            const xPx = e.clientX - rect.left;
            const effDur = tlDuration || duration || 1;
            mouseTimeRef.current = Math.max(0, Math.min(duration || 0, (xPx / rect.width) * effDur));
            setTimelineCursorPos(xPx / rect.width); // store as ratio for both bars
          }}
          onMouseLeave={() => {
            mouseTimeRef.current = null;
            setTimelineCursorPos(null);
          }}
          style={{
            position: "relative",
            height: 32,
            backgroundColor: C.bg,
            borderRadius: '5px 5px 0 0',
            overflow: "hidden",
            cursor: "pointer",
            marginBottom: 0,
          }}
        >
          {/* Heatmap */}
          {(segments.length > 0 ? segments : timelineData?.phases || []).map((seg, i) => {
            let st = seg.start_sec ?? seg.time_start ?? 0;
            let en = seg.end_sec ?? seg.time_end ?? 0;
            if (!duration) return null;
            // Convert absolute timestamps to local for clip videos
            if (isClipVideo && st >= origStart) {
              st = st - origStart;
              en = en - origStart;
            }
            // Clamp to actual video duration to prevent overflow
            if (st >= duration) return null;
            en = Math.min(en, duration);
            if (en <= st) return null;
            const sc = seg.viral_score ?? seg.hook_score ?? 0;
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  top: 0,
                  bottom: 0,
                  left: `${(st / tlDuration) * 100}%`,
                  width: `${((en - st) / tlDuration) * 100}%`,
                  backgroundColor: scoreColor(sc, 0.6),
                  borderRight: `1px solid ${C.bg}`,
                }}
                title={`Phase ${seg.phase_index ?? i}: ${Math.round(sc)}`}
              />
            );
          })}

          {/* Trim region */}
          {duration > 0 && (
            <div
              style={{
                position: "absolute",
                top: 0,
                bottom: 0,
                left: `${(tlTrimStart / tlDuration) * 100}%`,
                width: `${((tlTrimEnd - tlTrimStart) / tlDuration) * 100}%`,
                backgroundColor: "rgba(255,107,53,0.2)",
                border: `2px solid ${C.accent}`,
                borderRadius: 3,
                pointerEvents: "none",
              }}
            />
          )}

          {/* Trim handles */}
          {duration > 0 && (
            <>
              <div
                onMouseDown={(e) => {
                  e.stopPropagation();
                  setDragging("s");
                }}
                style={handleStyle((tlTrimStart / tlDuration) * 100)}
              />
              <div
                onMouseDown={(e) => {
                  e.stopPropagation();
                  setDragging("e");
                }}
                style={handleStyle((tlTrimEnd / tlDuration) * 100)}
              />
            </>
          )}

          {/* AI Markers */}
          {timelineData?.markers?.map((m, i) => {
            if (!duration) return null;
            const mi = MARKERS[m.type] || MARKERS.sales;
            const mLocal = isClipVideo && m.time_start >= origStart ? m.time_start - origStart : m.time_start;
            return (
              <div
                key={`m${i}`}
                style={{
                  position: "absolute",
                  top: -2,
                  left: `${(mLocal / tlDuration) * 100}%`,
                  transform: "translateX(-6px)",
                  fontSize: 11,
                  zIndex: 3,
                  cursor: "pointer",
                  filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.5))",
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  seek(mLocal);
                }}
                title={m.label || mi.label}
              >
                {mi.icon}
              </div>
            );
          })}

          {/* Playhead */}
          {duration > 0 && (
            <div
              style={{
                position: "absolute",
                top: 0,
                bottom: 0,
                left: `${(currentTime / tlDuration) * 100}%`,
                width: 2,
                backgroundColor: "#fff",
                zIndex: 4,
                pointerEvents: "none",
                boxShadow: "0 0 4px rgba(255,255,255,0.5)",
              }}
            />
          )}
          {/* Split point lines on timeline bar (matching waveform) */}
          {splitPoints.map((sp, i) => (
            <div
              key={`tlsp${i}`}
              onClick={(e) => { e.stopPropagation(); removeSplitPoint(sp); }}
              style={{
                position: 'absolute',
                top: 0,
                bottom: 0,
                left: `${(sp / (tlDuration || duration || 1)) * 100}%`,
                width: 3,
                backgroundColor: '#FFE135',
                cursor: 'pointer',
                zIndex: 6,
                transform: 'translateX(-1.5px)',
              }}
              title={`分割点 ${fmt(sp)} (クリックで削除)`}
            />
          ))}
          {/* Mouse hover cursor line on timeline bar */}
          {timelineCursorPos !== null && (
            <div
              style={{
                position: 'absolute',
                top: 0,
                bottom: 0,
                left: `${timelineCursorPos * 100}%`,
                width: 1,
                borderLeft: '1px dashed rgba(255,255,255,0.5)',
                zIndex: 5,
                pointerEvents: 'none',
              }}
            />
          )}
        </div>

        {/* ═══ WAVEFORM + SPLIT UI ═══ */}
        <div
          ref={waveformContainerRef}
          onClick={onWaveformClick}
          onMouseMove={onWaveformMouseMove}
          onMouseLeave={onWaveformMouseLeave}
          style={{
            position: 'relative',
            height: 48,
            backgroundColor: C.bg,
            borderRadius: '0 0 4px 4px',
            overflow: 'hidden',
            cursor: 'pointer',
            marginBottom: 4,
            borderLeft: `1px solid ${C.border}`,
            borderRight: `1px solid ${C.border}`,
            borderBottom: `1px solid ${C.border}`,
            borderTop: 'none',
          }}
        >
          {waveformData ? (
            <canvas
              ref={waveformCanvasRef}
              width={1000}
              height={96}
              style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0, zIndex: 1, pointerEvents: 'none' }}
            />
          ) : (
            <div style={{
              width: '100%', height: '100%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: C.textDim, fontSize: 11,
            }}>
              {waveformLoading ? [window.__t('clipEditorV2_8c34af', '波形読み込み中...')] : window.__t('clipEditorV2_7f5005', '波形なし')}
            </div>
          )}
          {/* Split point markers on waveform */}
          {splitPoints.map((sp, i) => (
            <div
              key={`sp${i}`}
              onClick={(e) => { e.stopPropagation(); removeSplitPoint(sp); }}
              style={{
                position: 'absolute',
                top: 0,
                bottom: 0,
                left: `${(sp / (tlDuration || duration || 1)) * 100}%`,
                width: 3,
                backgroundColor: '#FFE135',
                cursor: 'pointer',
                zIndex: 25,
                transform: 'translateX(-1.5px)',
              }}
              title={`分割点 ${fmt(sp)} (クリックで削除)`}
            />
          ))}
          {/* Segment labels */}
          {/* Mouse hover cursor line on waveform (HTML overlay for visibility above segments) */}
          {timelineCursorPos !== null && (
            <div
              style={{
                position: 'absolute',
                top: 0,
                bottom: 0,
                left: `${timelineCursorPos * 100}%`,
                width: 1,
                borderLeft: '1px dashed rgba(255,255,255,0.5)',
                zIndex: 30,
                pointerEvents: 'none',
              }}
            />
          )}
          {splitSegments.map((seg) => {
            const isDeleted = deletedRanges.some(r => Math.abs(r.start - seg.start) < 0.05 && Math.abs(r.end - seg.end) < 0.05);
            return (
            <div
              key={`seg${seg.index}`}
              onClick={(e) => {
                e.stopPropagation();
                if (isDeleted) {
                  // Click on deleted segment = restore it
                  setDeletedRanges(prev => prev.filter(r => !(Math.abs(r.start - seg.start) < 0.05 && Math.abs(r.end - seg.end) < 0.05)));
                } else {
                  setSelectedSegIdx(seg.index === selectedSegIdx ? null : seg.index);
                }
              }}
              onMouseEnter={() => setHoveredSegIdx(seg.index)}
              onMouseLeave={() => setHoveredSegIdx(null)}
              onMouseMove={(e) => {
                // Propagate mouse position for cursor line
                const container = waveformContainerRef.current;
                if (!container) return;
                const rect = container.getBoundingClientRect();
                const ratio = (e.clientX - rect.left) / rect.width;
                const effDur = tlDuration || duration || 1;
                mouseTimeRef.current = Math.max(0, Math.min(duration || 0, ratio * effDur));
                setTimelineCursorPos(Math.max(0, Math.min(1, ratio)));
              }}
              style={{
                position: 'absolute',
                top: 0,
                left: `${(seg.start / (tlDuration || duration || 1)) * 100}%`,
                width: `${((seg.end - seg.start) / (tlDuration || duration || 1)) * 100}%`,
                height: '100%',
                display: 'flex',
                alignItems: 'flex-end',
                justifyContent: 'center',
                paddingBottom: 2,
                fontSize: 9,
                fontWeight: 600,
                color: isDeleted ? 'rgba(255,255,255,0.4)' : '#fff',
                backgroundColor: isDeleted
                  ? 'rgba(239, 68, 68, 0.35)'
                  : selectedSegIdx === seg.index
                    ? 'rgba(99, 102, 241, 0.45)'
                    : hoveredSegIdx === seg.index
                      ? 'rgba(99, 102, 241, 0.3)'
                      : 'transparent',
                outline: isDeleted
                  ? '2px solid rgba(239, 68, 68, 0.6)'
                  : selectedSegIdx === seg.index ? '2px solid rgba(99, 102, 241, 0.7)' : 'none',
                outlineOffset: -2,
                cursor: 'pointer',
                zIndex: selectedSegIdx === seg.index ? 22 : 20,
                borderRight: '1px solid rgba(255,255,255,0.08)',
                transition: 'background-color 0.15s ease',
                userSelect: 'none',
                overflow: 'hidden',
                textDecoration: isDeleted ? 'line-through' : 'none',
              }}
              title={isDeleted ? `クリックで復元: ${fmt(seg.start)}-${fmt(seg.end)}` : `クリックで選択 → Del で削除: ${fmt(seg.start)}-${fmt(seg.end)}`}
            >
              {isDeleted ? `✕ ${fmt(seg.start)}-${fmt(seg.end)}` : `${fmt(seg.start)}-${fmt(seg.end)}`}
            </div>
            );
          })}
        </div>

        {/* Split info bar */}
        {splitPoints.length > 0 && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 4,
            padding: '3px 8px',
            backgroundColor: C.surfaceLight,
            borderRadius: 4,
            fontSize: 10,
          }}>
            <span style={{ color: '#FFE135', fontWeight: 700 }}>✂ {splitPoints.length}分割</span>
            <span style={{ color: C.textMuted }}>
              {splitSegments.length}セグメント
            </span>
            {deletedRanges.length > 0 && (
              <span style={{ color: C.red, fontWeight: 600 }}>
                🗑 {deletedRanges.length}区間削除済 ({fmt(deletedRanges.reduce((sum, r) => sum + (r.end - r.start), 0))})
              </span>
            )}
            <div style={{ flex: 1 }} />
            {selectedSegIdx !== null && splitSegments.length > 0 && (
              <button
                onClick={() => deleteSegment(selectedSegIdx)}
                style={{
                  padding: '2px 8px',
                  border: `1px solid ${C.red}`,
                  borderRadius: 4,
                  backgroundColor: 'rgba(239,68,68,0.15)',
                  color: C.red,
                  fontSize: 10,
                  cursor: 'pointer',
                  fontWeight: 600,
                  marginRight: 4,
                }}
              >
                🗑 削除
              </button>
            )}
            <button
              onClick={() => { setSplitPoints([]); setDisabledSegments(new Set()); setDeletedRanges([]); setSelectedSegIdx(null); }}
              style={{
                padding: '2px 8px',
                border: `1px solid ${C.border}`,
                borderRadius: 4,
                backgroundColor: C.bg,
                color: C.textMuted,
                fontSize: 10,
                cursor: 'pointer',
              }}
            >
              全リセット
            </button>
          </div>
        )}

        {/* Shortcut hint */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginBottom: 4,
          fontSize: 10,
          color: C.textDim,
          flexWrap: 'wrap',
        }}>
          {[
            { key: 'Space', label: window.__t('clipEditorV2_2f7d75', '再生/停止'), color: C.green },
            { key: 'W', label: window.__t('clipEditorV2_76649a', 'カーソル位置で分割'), color: '#FFE135' },
            { key: 'Del', label: window.__t('clipEditorV2_721a73', '選択セグメント完全削除'), color: C.red },
            { key: '←→', label: window.__t('clipEditorV2_505aa4', '±5秒'), color: C.blue },
          ].map(({ key, label, color }) => (
            <span key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
              <kbd style={{
                padding: '1px 6px',
                backgroundColor: C.surfaceLight,
                borderRadius: 4,
                border: `1px solid ${C.border}`,
                fontSize: 10,
                fontWeight: 700,
                color: color,
                fontFamily: 'monospace',
              }}>{key}</kbd>
              <span>{label}</span>
            </span>
          ))}
          <span style={{ color: C.textMuted }}>{window.__t('clipEditorV2_35fc25', 'セグメントクリックで選択')}</span>
          <span style={{ color: C.textMuted }}>{window.__t('clipEditorV2_3d197e', '黄色線クリックで分割点削除')}</span>
          {deletedRanges.length > 0 && <span style={{ color: C.red }}>{window.__t('clipEditorV2_5bee1e', '赤セグメントクリックで復元')}</span>}
          {silentRegions.length > 0 && (
            <span style={{ color: C.red }}>赤 = 無音区間 ({silentRegions.length}箇所)</span>
          )}
        </div>

        {/* Controls row */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          {/* Left: trim range */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
            <span style={{ color: C.textDim }}>{fmt(0)}</span>
            <span style={{ color: C.accent, fontWeight: 600, fontSize: 12 }}>
              {fmt(trimStart)} — {fmt(trimEnd)} ({(duration > 0 ? Math.min(clipDur, duration) : clipDur).toFixed(1)}s)
            </span>
            <span style={{ color: C.textDim }}>{fmt(duration)}</span>
          </div>

          {/* Center: playback */}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Btn onClick={() => seek(Math.max(0, currentTime - 5))}>-5s</Btn>
            <button
              onClick={toggle}
              style={{
                width: 36,
                height: 36,
                borderRadius: "50%",
                backgroundColor: C.accent,
                border: "none",
                color: "#fff",
                fontSize: 15,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {isPlaying ? "⏸" : "▶"}
            </button>
            <Btn onClick={() => seek(Math.min(duration, currentTime + 5))}>+5s</Btn>
            <span style={{ color: C.textMuted, fontSize: 11, marginLeft: 4 }}>
              {fmt(currentTime)} / {fmt(duration)}
            </span>
          </div>

          {/* Right: speed */}
          <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
            {[1, 1.5, 2].map((r) => (
              <button
                key={r}
                onClick={() => setSpeed(r)}
                style={{
                  padding: "3px 9px",
                  border: `1px solid ${C.border}`,
                  borderRadius: 5,
                  fontSize: 11,
                  cursor: "pointer",
                  backgroundColor: playbackRate === r ? C.accent : C.surfaceLight,
                  color: playbackRate === r ? "#fff" : C.textMuted,
                  fontWeight: playbackRate === r ? 700 : 400,
                }}
              >
                {r}x
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// Sub-components
// ═══════════════════════════════════════════════════════════════════════════

const Section = ({ title, children }) => (
  <div style={{ marginBottom: 16 }}>
    <SectionTitle>{title}</SectionTitle>
    {children}
  </div>
);

const SectionTitle = ({ children }) => (
  <div
    style={{
      color: "#8888aa",
      fontSize: 11,
      marginBottom: 8,
      fontWeight: 600,
      textTransform: "uppercase",
      letterSpacing: 1,
    }}
  >
    {children}
  </div>
);

const ScoreRow = ({ icon, label, score }) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "7px 10px",
      marginBottom: 4,
      backgroundColor: "#252540",
      borderRadius: 6,
    }}
  >
    <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
      <span style={{ fontSize: 13 }}>{icon}</span>
      <span style={{ color: "#fff", fontSize: 12 }}>{label}</span>
    </div>
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ width: 60, height: 5, backgroundColor: "#0f0f1a", borderRadius: 3, overflow: "hidden" }}>
        <div
          style={{
            width: `${Math.min(100, score || 0)}%`,
            height: "100%",
            borderRadius: 3,
            backgroundColor: scoreColor(score),
          }}
        />
      </div>
      <span style={{ color: scoreColor(score), fontSize: 13, fontWeight: 700, minWidth: 24, textAlign: "right" }}>
        {score != null ? Math.round(score) : "—"}
      </span>
    </div>
  </div>
);

const TrimControl = ({ label, value, onChange }) => (
  <div>
    <span style={{ color: "#8888aa", fontSize: 12, marginBottom: 4, display: "block" }}>{label}</span>
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      {[-1, -0.5, 0.5, 1].map((d) => (
        <button
          key={d}
          onClick={() => onChange(value + d)}
          style={{
            padding: "4px 8px",
            border: "1px solid #333355",
            borderRadius: 5,
            backgroundColor: "#0f0f1a",
            color: "#8888aa",
            fontSize: 11,
            cursor: "pointer",
          }}
        >
          {d > 0 ? "+" : ""}
          {d}s
        </button>
      ))}
      <span style={{ color: "#fff", fontSize: 16, fontWeight: 700, marginLeft: 6 }}>{fmt(value)}</span>
    </div>
  </div>
);

const Btn = ({ onClick, children }) => (
  <button
    onClick={onClick}
    style={{
      padding: "4px 10px",
      border: "1px solid #333355",
      borderRadius: 6,
      backgroundColor: "#252540",
      color: "#fff",
      fontSize: 12,
      cursor: "pointer",
    }}
  >
    {children}
  </button>
);

const tagStyle = (color) => ({
  padding: "2px 8px",
  borderRadius: 4,
  fontSize: 11,
  fontWeight: 600,
  backgroundColor: typeof color === "string" && color.startsWith("rgba") ? color.replace(/[\d.]+\)$/, "0.15)") : color + "22",
  color: color,
  border: `1px solid ${typeof color === "string" && color.startsWith("rgba") ? color.replace(/[\d.]+\)$/, "0.3)") : color + "44"}`,
});

const handleStyle = (leftPct) => ({
  position: "absolute",
  top: 0,
  bottom: 0,
  left: `${leftPct}%`,
  width: 8,
  backgroundColor: "#FF6B35",
  cursor: "ew-resize",
  zIndex: 2,
  borderRadius: 2,
  transform: "translateX(-4px)",
});

export default ClipEditorV2;
