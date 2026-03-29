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
    name: 'シンプル',
    desc: 'ビジネス系におすすめ',
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
    name: 'ボックス',
    desc: '視認性重視におすすめ',
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
    name: '縁取り',
    desc: '目立たせたい時におすすめ',
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
    name: 'ポップ',
    desc: 'TikTok投稿におすすめ',
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
    name: 'グラデーション',
    desc: '美容系におすすめ',
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
    name: 'カラオケ',
    desc: '喋りに合わせてハイライト',
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

  // Subtitle style & position
  const [subtitleStyle, setSubtitleStyle] = useState('box');
  const [subtitlePos, setSubtitlePos] = useState({ x: 50, y: 85 }); // percentage
  const [isDraggingSub, setIsDraggingSub] = useState(false);
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
  const [disabledSegments, setDisabledSegments] = useState(new Set()); // Set of segment indices
  const [hoveredSegIdx, setHoveredSegIdx] = useState(null);
  const [timelineCursorPos, setTimelineCursorPos] = useState(null); // mouse X position on timeline

  const clipDur = trimEnd - trimStart;

  // Determine if we're playing a clip_url (local time 0-based) or full video
  const isClipVideo = !!(clip?.clip_url);
  const videoUrl = useMemo(() => {
    return clip?.clip_url || videoData?.video_url || clip?.video_url || null;
  }, [videoData, clip]);

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
  const currentCaption = useMemo(() => {
    if (!captions.length) return null;
    const t = currentTime;
    const MIN_DISPLAY = 3; // minimum display duration in seconds
    for (let i = 0; i < captions.length; i++) {
      const c = captions[i];
      const localStart = toLocalTime(c.start || 0) + captionOffset;
      const rawEnd = toLocalTime(c.end || (c.start + 5)) + captionOffset;
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
  }, [captions, currentTime, toLocalTime, captionOffset]);

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

    // Filter transcripts that overlap with this clip's time range
    return transcripts
      .filter((t) => {
        const s = t.start ?? 0;
        const e = t.end ?? 0;
        return s < tEnd && e > tStart;
      })
      .map((t) => ({
        start: Math.max(t.start, tStart),
        end: Math.min(t.end, tEnd),
        text: t.text || "",
        confidence: t.confidence,
        source: "transcript",
      }));
  }, []);

  // Fallback: build subtitle-like captions from phase audio_text (raw speech text per phase)
  const buildCaptionsFromAudioText = useCallback((phases, clipData) => {
    if (!phases || !clipData) return [];
    const phaseIdx = clipData.phase_index;
    const tStart = clipData.time_start || 0;
    const tEnd = clipData.time_end || 0;

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
        result.push({ start: pStart, end: pEnd, text: txt.trim(), source: "audio_text" });
      } else {
        const dur = pEnd - pStart;
        const perSentence = dur / sentences.length;
        sentences.forEach((sent, i) => {
          result.push({
            start: Math.round((pStart + i * perSentence) * 100) / 100,
            end: Math.round((pStart + (i + 1) * perSentence) * 100) / 100,
            text: sent,
            source: "audio_text",
          });
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
          const withSource = saved.map(c => ({ ...c, source: c.source || 'saved' }));
          console.log(`[Subtitles] Loaded ${withSource.length} saved captions from DB`);
          setCaptions(withSource);
          setCaptionsLoaded(true);
          return;
        }
      } catch (e) {
        console.warn("Failed to fetch saved captions:", e);
      }

      // Priority 1: clip.captions (from generate_clip Whisper)
      if (clip?.captions && clip.captions.length > 0) {
        console.log("[Subtitles] Using clip.captions");
        setCaptions(clip.captions);
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
    if (videoRef.current) setCurrentTime(videoRef.current.currentTime);
  }, []);

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
    const barW = W / samples;

    // Draw silent region backgrounds
    ctx.fillStyle = 'rgba(239, 68, 68, 0.12)';
    for (const sr of silentRegions) {
      const x1 = (sr.start / duration) * W;
      const x2 = (sr.end / duration) * W;
      ctx.fillRect(x1, 0, x2 - x1, H);
    }

    // Draw disabled segments
    if (splitPoints.length > 0) {
      const allPoints = [0, ...splitPoints, duration];
      for (let i = 0; i < allPoints.length - 1; i++) {
        if (disabledSegments.has(i)) {
          const x1 = (allPoints[i] / duration) * W;
          const x2 = (allPoints[i + 1] / duration) * W;
          ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
          ctx.fillRect(x1, 0, x2 - x1, H);
        }
      }
    }

    // Draw waveform bars
    for (let i = 0; i < samples; i++) {
      const amp = waveformData[i];
      const x = i * barW;
      const barH = Math.max(1, amp * H * 0.9);
      const timeSec = (i / samples) * duration;

      // Check if this sample is in a silent region
      const isSilent = silentRegions.some(sr => timeSec >= sr.start && timeSec <= sr.end);
      // Check if this sample is in a disabled segment
      let isDisabled = false;
      if (splitPoints.length > 0) {
        const allPoints = [0, ...splitPoints, duration];
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
      ctx.fillRect(x, H - barH, barW - 0.5, barH);
    }

    // Draw split point lines
    ctx.strokeStyle = '#FFE135';
    ctx.lineWidth = 2;
    for (const sp of splitPoints) {
      const x = (sp / duration) * W;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, H);
      ctx.stroke();
    }

    // Draw playhead
    if (currentTime >= 0) {
      const px = (currentTime / duration) * W;
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(px, 0);
      ctx.lineTo(px, H);
      ctx.stroke();
    }

    // Draw cursor position
    if (timelineCursorPos !== null) {
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(timelineCursorPos, 0);
      ctx.lineTo(timelineCursorPos, H);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }, [waveformData, duration, silentRegions, splitPoints, disabledSegments, currentTime, timelineCursorPos]);

  // ─── Split segments helper ────────────────────────────────────
  const splitSegments = useMemo(() => {
    if (splitPoints.length === 0) return [];
    const allPoints = [0, ...splitPoints, duration || 0];
    return allPoints.slice(0, -1).map((start, i) => ({
      index: i,
      start,
      end: allPoints[i + 1],
      enabled: !disabledSegments.has(i),
    }));
  }, [splitPoints, duration, disabledSegments]);

  // ─── Keyboard shortcut: W to split ────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Don't trigger if user is typing in an input/textarea
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === 'w' || e.key === 'W') {
        e.preventDefault();
        if (!duration || currentTime <= 0 || currentTime >= duration) return;
        // Don't add duplicate split points (within 0.5s)
        const isDuplicate = splitPoints.some(sp => Math.abs(sp - currentTime) < 0.5);
        if (isDuplicate) return;
        setSplitPoints(prev => [...prev, Math.round(currentTime * 10) / 10].sort((a, b) => a - b));
        console.log(`[Split] Added split at ${currentTime.toFixed(1)}s`);
      }
      // Delete/Backspace to remove last split point
      if (e.key === 'Backspace' && e.ctrlKey) {
        e.preventDefault();
        setSplitPoints(prev => prev.slice(0, -1));
        setDisabledSegments(new Set());
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [duration, currentTime, splitPoints]);

  // ─── Toggle segment enabled/disabled ──────────────────────────
  const toggleSegment = useCallback((segIdx) => {
    setDisabledSegments(prev => {
      const next = new Set(prev);
      if (next.has(segIdx)) next.delete(segIdx);
      else next.add(segIdx);
      return next;
    });
  }, []);

  // ─── Remove a split point ─────────────────────────────────────
  const removeSplitPoint = useCallback((splitTime) => {
    setSplitPoints(prev => prev.filter(sp => Math.abs(sp - splitTime) > 0.3));
    setDisabledSegments(new Set());
  }, []);

  // ─── Waveform click to seek ───────────────────────────────────
  const onWaveformClick = useCallback((e) => {
    const container = waveformContainerRef.current;
    if (!container || !duration) return;
    const rect = container.getBoundingClientRect();
    const t = Math.max(0, Math.min(duration, ((e.clientX - rect.left) / rect.width) * duration));
    seek(t);
  }, [duration, seek]);

  const onWaveformMouseMove = useCallback((e) => {
    const container = waveformContainerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    setTimelineCursorPos(e.clientX - rect.left);
  }, []);

  const onWaveformMouseLeave = useCallback(() => {
    setTimelineCursorPos(null);
  }, []);

  // ─── Timeline ──────────────────────────────────────────────────
  const onTLClick = useCallback(
    (e) => {
      if (!timelineRef.current || !duration) return;
      const rect = timelineRef.current.getBoundingClientRect();
      seek(Math.max(0, Math.min(duration, ((e.clientX - rect.left) / rect.width) * duration)));
    },
    [duration, seek]
  );

  // ─── Trim Drag ─────────────────────────────────────────────────
  const onTrimDrag = useCallback(
    (e) => {
      if (!dragging || !timelineRef.current || !duration) return;
      const rect = timelineRef.current.getBoundingClientRect();
      const t = Math.max(0, Math.min(duration, ((e.clientX - rect.left) / rect.width) * duration));
      if (dragging === "s" && t < trimEnd - 1) setTrimStart(Math.round(t * 10) / 10);
      if (dragging === "e" && t > trimStart + 1) setTrimEnd(Math.round(t * 10) / 10);
    },
    [dragging, duration, trimStart, trimEnd]
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
      setStatus({ ok: true, msg: "トリム適用中..." });
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

  const saveCaps = async () => {
    if (!clip?.clip_id) return;
    setSavingCaps(true);
    setStatus(null);
    try {
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
        setStatus({ ok: true, msg: "字幕を保存しました（AI学習に反映）" });
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
      if (!clipUrl) throw new Error("動画URLが見つかりません");
      const res = await VideoService.transcribeClip(videoId, {
        clip_url: clipUrl,
        time_start: clip.time_start || origStart,
        time_end: clip.time_end || origEnd,
        phase_index: clip.phase_index,
      });
      if (res?.segments?.length > 0) {
        const newCaps = res.segments.map((s) => ({
          start: s.start,
          end: s.end,
          text: s.text,
          source: "whisper",
          // Include word-level timestamps for karaoke-style highlighting
          ...(s.words && s.words.length > 0 ? { words: s.words } : {}),
        }));
        setCaptions(newCaps);
        setStatus({ ok: true, msg: `${newCaps.length}件の字幕を生成しました` });
        // Auto-save generated subtitles so they persist on next load
        if (clip?.clip_id) {
          try {
            const capsToSave = newCaps.map(c => ({ ...c, source: 'saved' }));
            await VideoService.updateClipCaptions(videoId, clip.clip_id, capsToSave);
            setCaptions(capsToSave);
            console.log("[Subtitles] Auto-saved generated subtitles");
          } catch (saveErr) {
            console.warn("[Subtitles] Auto-save failed:", saveErr);
          }
        }
      } else {
        setStatus({ ok: false, msg: "音声が検出されませんでした" });
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
      return { style: 'gradient', reason: '美容系コンテンツに最適', source: 'local' };
    }
    if (tags.some(t => /エンタメ|お笑い|バラエティ|funny|viral/i.test(t)) || /バズ|爆笑/i.test(titleLower)) {
      return { style: 'pop', reason: 'エンタメ系に最適・インパクト大', source: 'local' };
    }
    if (tags.some(t => /ビジネス|解説|教育|business/i.test(t)) || /解説|まとめ/i.test(titleLower)) {
      return { style: 'simple', reason: 'ビジネス系・読みやすさ重視', source: 'local' };
    }
    if (clip?.ai_score && clip.ai_score >= 80) {
      return { style: 'outline', reason: '高スコアクリップ・目立たせるスタイル', source: 'local' };
    }
    return { style: 'box', reason: '万能型・どんな動画にも合う', source: 'local' };
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
    '見やすい', '目立つ', 'おしゃれ', 'ポップ',
    '落ち着いた', '文字が小さい', '文字が大きい', '色を変えたい',
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
      setStatus({ ok: true, msg: 'フィードバックを保存しました' });
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
                setStatus({ ok: true, msg: '字幕付きMP4を生成中...' });
                const statusLabels = {
                  queued: '準備中...',
                  downloading: 'クリップをダウンロード中...',
                  encoding: '字幕を焼き込み中...',
                  uploading: 'アップロード中...',
                  done: '完了！',
                };
                try {
                  const res = await VideoService.exportSubtitledClip(videoId, {
                    clip_url: clip.clip_url,
                    captions: captions.map(c => ({
                      start: c.start,
                      end: c.end,
                      text: c.text,
                      ...(c.words ? { words: c.words } : {}),
                    })),
                    style: subtitleStyle,
                    position_x: subtitlePos.x,
                    position_y: subtitlePos.y,
                    time_start: clip.time_start || origStart,
                    ...(splitPoints.length > 0 ? {
                      split_segments: splitSegments.map(s => ({
                        start: s.start,
                        end: s.end,
                        enabled: s.enabled,
                      }))
                    } : {}),
                  }, {
                    onProgress: (st, pct) => {
                      setExportProgress(pct || 0);
                      setStatus({ ok: true, msg: statusLabels[st] || `処理中 (${st})...` });
                    },
                  });
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
                    setStatus({ ok: true, msg: '字幕付きMP4のダウンロードを開始しました！' });
                    setTimeout(() => setStatus(null), 5000);
                  } else {
                    setStatus({ ok: true, msg: 'エクスポート完了' });
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
                {exporting ? `${exportProgress}%` : '字幕付き Export'}
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
                    maxWidth: "95%",
                    userSelect: "none",
                    transition: isDraggingSub ? 'none' : 'left 0.1s ease, top 0.1s ease',
                  }}
                >
                  <span
                    style={{
                      display: "inline-block",
                      ...presetText,
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
              { k: "captions", l: "字幕" },
              { k: "info", l: "AI分析" },
              { k: "trim", l: "Trim" },
              { k: "feedback", l: "評価" },
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
                <Section title="AI 評価">
                  {[
                    { l: "バイラル度", s: currentPhase?.viral_score, i: "🔥" },
                    { l: "フック力", s: currentPhase?.hook_score, i: "🎣" },
                    { l: "エンゲージメント", s: currentPhase?.engagement_score, i: "💬" },
                    { l: "発話エネルギー", s: currentPhase?.speech_energy, i: "🎤" },
                  ].map((x, idx) => (
                    <ScoreRow key={idx} icon={x.i} label={x.l} score={x.s} />
                  ))}
                </Section>

                {/* AI Summary */}
                {clip.description && (
                  <Section title="AI要約">
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
                  <Section title="動画全体スコア">
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
                <SectionTitle>字幕スタイル</SectionTitle>

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

                {/* ═══ 字幕フィードバック ═══ */}
                <SectionTitle>字幕フィードバック</SectionTitle>
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
                    <SectionTitle>字幕タイミング調整</SectionTitle>
                    <p style={{ color: C.textMuted, fontSize: 11, margin: '0 0 8px', lineHeight: 1.5 }}>
                      字幕が音声とずれている場合に調整できます。
                      {captionOffset > 0 ? `+${captionOffset.toFixed(1)}s（字幕を遅らせる）` : captionOffset < 0 ? `${captionOffset.toFixed(1)}s（字幕を早める）` : '0s（調整なし）'}
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
                <SectionTitle>字幕編集</SectionTitle>
                <p style={{ color: C.textMuted, fontSize: 11, margin: "0 0 10px", lineHeight: 1.5 }}>
                  配信者の音声書き起こしです。テキストを直接編集できます。タイムスタンプをクリックするとその位置にジャンプします。
                </p>
                {captions.length > 0 && captions[0]?.source && (
                  <p style={{ color: C.textDim, fontSize: 10, margin: "0 0 8px" }}>
                    データソース: {captions[0].source === "whisper" ? "Whisper音声認識（オンデマンド）" : captions[0].source === "transcript" ? "Whisper音声認識" : captions[0].source === "audio_text" ? "フェーズ音声テキスト" : "クリップ字幕"}
                  </p>
                )}
                {/* Generate subtitles button - always visible */}
                <button
                  onClick={generateSubtitles}
                  disabled={transcribing}
                  style={{
                    width: "100%",
                    padding: "10px 16px",
                    border: `1px solid ${C.accent}66`,
                    borderRadius: 8,
                    backgroundColor: transcribing ? C.surfaceLight : C.accent + "22",
                    color: C.accent,
                    fontSize: 13,
                    fontWeight: 600,
                    cursor: transcribing ? "not-allowed" : "pointer",
                    marginBottom: 12,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 8,
                    opacity: transcribing ? 0.7 : 1,
                  }}
                >
                  {transcribing ? (
                    <>
                      <span style={{ animation: "spin 1s linear infinite", display: "inline-block" }}>⟳</span>
                      AI音声認識で字幕を生成中...
                    </>
                  ) : captions.length > 0 ? (
                    <>🎤 字幕を再生成（AI音声認識）</>
                  ) : (
                    <>🎤 字幕を生成（AI音声認識）</>
                  )}
                </button>
                {transcribing && (
                  <p style={{ color: C.textMuted, fontSize: 10, textAlign: "center", margin: "0 0 10px" }}>
                    OpenAI Whisperで音声を書き起こしています。30秒〜1分程度かかります。
                  </p>
                )}
                {captions.length === 0 && !transcribing ? (
                  <div
                    style={{
                      color: C.textDim,
                      textAlign: "center",
                      padding: 24,
                      fontSize: 13,
                      backgroundColor: C.surfaceLight,
                      borderRadius: 8,
                    }}
                  >
                    音声書き起こしデータがありません。
                    <br />
                    上のボタンをクリックしてAI音声認識で字幕を生成してください。
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {captions.map((cap, i) => {
                      const isActive = currentCaption === cap;
                      return (
                        <div
                          key={i}
                          style={{
                            display: "flex",
                            gap: 8,
                            padding: "8px 10px",
                            backgroundColor: isActive ? C.accent + "18" : C.surfaceLight,
                            borderRadius: 6,
                            border: isActive ? `1px solid ${C.accent}55` : `1px solid transparent`,
                            transition: "all 0.2s ease",
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
                              cursor: "pointer",
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
                              padding: "4px 8px",
                              backgroundColor: C.bg,
                              border: `1px solid ${C.border}`,
                              borderRadius: 5,
                              color: cap.emphasis ? C.yellow : C.text,
                              fontSize: 13,
                              fontWeight: cap.emphasis ? 700 : 400,
                              lineHeight: 1.5,
                              outline: "none",
                              resize: "vertical",
                              minHeight: 36,
                              fontFamily: "inherit",
                              transition: "border-color 0.2s ease",
                            }}
                            onFocus={(e) => {
                              e.target.style.borderColor = C.accent;
                            }}
                            onBlur={(e) => {
                              e.target.style.borderColor = C.border;
                            }}
                          />
                        </div>
                      );
                    })}
                    <button
                      onClick={saveCaps}
                      disabled={savingCaps}
                      style={{
                        padding: "10px 20px",
                        border: "none",
                        borderRadius: 8,
                        backgroundColor: C.green,
                        color: "#fff",
                        fontSize: 13,
                        fontWeight: 600,
                        cursor: "pointer",
                        opacity: savingCaps ? 0.6 : 1,
                        marginTop: 10,
                      }}
                    >
                      {savingCaps ? "保存中..." : "字幕を保存"}
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* ─── Trim ─── */}
            {tab === "trim" && (
              <div>
                <SectionTitle>トリム編集</SectionTitle>
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
                    label="開始時間"
                    value={trimStart}
                    onChange={(v) => v < trimEnd - 1 && v >= 0 && setTrimStart(Math.round(v * 10) / 10)}
                  />
                  <TrimControl
                    label="終了時間"
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
                    <span style={{ color: C.textMuted, fontSize: 12 }}>クリップ長</span>
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
                    {isTrimming ? "生成中..." : "トリムを適用"}
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
          style={{
            position: "relative",
            height: 32,
            backgroundColor: C.bg,
            borderRadius: 5,
            overflow: "hidden",
            cursor: "pointer",
            marginBottom: 5,
          }}
        >
          {/* Heatmap */}
          {(segments.length > 0 ? segments : timelineData?.phases || []).map((seg, i) => {
            const st = seg.start_sec ?? seg.time_start ?? 0;
            const en = seg.end_sec ?? seg.time_end ?? 0;
            if (!duration) return null;
            const sc = seg.viral_score ?? seg.hook_score ?? 0;
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  top: 0,
                  bottom: 0,
                  left: `${(st / duration) * 100}%`,
                  width: `${((en - st) / duration) * 100}%`,
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
                left: `${(trimStart / duration) * 100}%`,
                width: `${((trimEnd - trimStart) / duration) * 100}%`,
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
                style={handleStyle((trimStart / duration) * 100)}
              />
              <div
                onMouseDown={(e) => {
                  e.stopPropagation();
                  setDragging("e");
                }}
                style={handleStyle((trimEnd / duration) * 100)}
              />
            </>
          )}

          {/* AI Markers */}
          {timelineData?.markers?.map((m, i) => {
            if (!duration) return null;
            const mi = MARKERS[m.type] || MARKERS.sales;
            return (
              <div
                key={`m${i}`}
                style={{
                  position: "absolute",
                  top: -2,
                  left: `${(m.time_start / duration) * 100}%`,
                  transform: "translateX(-6px)",
                  fontSize: 11,
                  zIndex: 3,
                  cursor: "pointer",
                  filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.5))",
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  seek(m.time_start);
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
                left: `${(currentTime / duration) * 100}%`,
                width: 2,
                backgroundColor: "#fff",
                zIndex: 4,
                pointerEvents: "none",
                boxShadow: "0 0 4px rgba(255,255,255,0.5)",
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
            borderRadius: 4,
            overflow: 'hidden',
            cursor: 'pointer',
            marginBottom: 4,
            border: `1px solid ${C.border}`,
          }}
        >
          {waveformData ? (
            <canvas
              ref={waveformCanvasRef}
              width={1000}
              height={96}
              style={{ width: '100%', height: '100%' }}
            />
          ) : (
            <div style={{
              width: '100%', height: '100%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: C.textDim, fontSize: 11,
            }}>
              {waveformLoading ? '波形読み込み中...' : '波形なし'}
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
                left: `${(sp / (duration || 1)) * 100}%`,
                width: 3,
                backgroundColor: '#FFE135',
                cursor: 'pointer',
                zIndex: 3,
                transform: 'translateX(-1.5px)',
              }}
              title={`分割点 ${fmt(sp)} (クリックで削除)`}
            />
          ))}
          {/* Segment labels */}
          {splitSegments.map((seg) => (
            <div
              key={`seg${seg.index}`}
              onClick={(e) => { e.stopPropagation(); toggleSegment(seg.index); }}
              onMouseEnter={() => setHoveredSegIdx(seg.index)}
              onMouseLeave={() => setHoveredSegIdx(null)}
              style={{
                position: 'absolute',
                bottom: 0,
                left: `${(seg.start / (duration || 1)) * 100}%`,
                width: `${((seg.end - seg.start) / (duration || 1)) * 100}%`,
                height: 14,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 9,
                fontWeight: 600,
                color: seg.enabled ? '#fff' : '#888',
                backgroundColor: !seg.enabled
                  ? 'rgba(239, 68, 68, 0.3)'
                  : hoveredSegIdx === seg.index
                    ? 'rgba(99, 102, 241, 0.4)'
                    : 'rgba(99, 102, 241, 0.15)',
                cursor: 'pointer',
                borderRight: '1px solid rgba(255,255,255,0.1)',
                transition: 'background-color 0.15s ease',
                userSelect: 'none',
                overflow: 'hidden',
                textDecoration: !seg.enabled ? 'line-through' : 'none',
              }}
              title={seg.enabled ? `クリックで削除: ${fmt(seg.start)}-${fmt(seg.end)}` : `クリックで復元: ${fmt(seg.start)}-${fmt(seg.end)}`}
            >
              {fmt(seg.start)}-{fmt(seg.end)}
            </div>
          ))}
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
              {splitSegments.filter(s => s.enabled).length}/{splitSegments.length}セグメント有効
            </span>
            {disabledSegments.size > 0 && (
              <span style={{ color: C.red, fontWeight: 600 }}>
                削除: {splitSegments.filter(s => !s.enabled).map(s => `${fmt(s.start)}-${fmt(s.end)}`).join(', ')}
              </span>
            )}
            <div style={{ flex: 1 }} />
            <button
              onClick={() => { setSplitPoints([]); setDisabledSegments(new Set()); }}
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
          gap: 12,
          marginBottom: 4,
          fontSize: 10,
          color: C.textDim,
        }}>
          <span><kbd style={{ padding: '1px 5px', backgroundColor: C.surfaceLight, borderRadius: 3, border: `1px solid ${C.border}`, fontSize: 10, fontWeight: 700, color: '#FFE135' }}>W</kbd> 分割</span>
          <span>セグメントクリックで削除/復元</span>
          <span>黄色線クリックで分割点削除</span>
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
