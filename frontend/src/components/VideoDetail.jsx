import { useState, useRef, useEffect, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import MarkdownWithTables from "./markdown/MarkdownWithTables";
import ChatInput from "./ChatInput";
import DockPlayer from "./DockPlayer";
import VideoService from "../base/services/videoService";
import "../assets/css/sidebar.css";
import AnalyticsSection from "./AnalyticsSection";
import ClipSection from "./ClipSection";
import SalesClipCandidates from "./SalesClipCandidates";
import SalesMomentClips from "./SalesMomentClips";
import MomentClips from "./MomentClips";
import HookDetection from "./HookDetection";
import LiveReportSection from "./LiveReportSection";
import SectionErrorBoundary from "./SectionErrorBoundary";
import CsvAssetPanel from "./CsvAssetPanel";
import CsvReplaceModal from "./CsvReplaceModal";
// ProductTimeline is now integrated into AnalyticsSection

export default function VideoDetail({ videoData }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const markdownTableStyles = `
  .markdown table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.5rem 0;
  }
  .markdown th,
  .markdown td {
    border: 1px solid rgba(0,0,0,0.1);
    padding: 0.5rem 0.65rem;
    text-align: left;
    vertical-align: top;
  }
  .markdown th {
    font-weight: 600;
    background: rgba(0,0,0,0.03);
  }
  .markdown tr:nth-child(even) td {
    background: rgba(0,0,0,0.02);
  }
  .markdown caption {
    caption-side: top;
    text-align: left;
    font-weight: 600;
    padding-bottom: 0.25rem;
  }
  .markdown p,
  .markdown li {
    line-height: 1.9;
    margin-top: 0.4rem;
    margin-bottom: 0.4rem;
  }
  .markdown ul,
  .markdown ol {
    margin: 0.4rem 0 0.4rem 1.25rem;
    padding-left: 1rem;
    list-style-position: outside;
  }
  .markdown ul {
    list-style-type: disc;
  }
  .markdown ol {
    list-style-type: decimal;
  }
  .markdown li {
    margin: 0.25rem 0;
    color: inherit;
  }
  .markdown li::marker {
    color: rgba(0,0,0,0.7);
    opacity: 0.95;
    font-size: 0.95em;
  }
  .markdown hr {
    border: none;
    border-top: 1px solid rgba(0,0,0,0.1);
    margin: 0.75rem 0;
  }
  .markdown h2 {
    font-size: 1.25rem;
    font-weight: 700;
    margin-top: 0.6rem;
    margin-bottom: 0.6rem;
    line-height: 1.2;
  }
  .markdown h3 {
    font-size: 1.05rem;
    font-weight: 600;
    margin-top: 0.45rem;
    margin-bottom: 0.45rem;
    line-height: 1.2;
  }
  .markdown strong {
    font-weight: 800;
  }
  `;
  const [loading] = useState(false);
  const [error] = useState(null);
  const [toast, setToast] = useState(null); // { message, type }
  const [chatMessages, setChatMessages] = useState([]);
  const [previewData, setPreviewData] = useState(null); // { url, timeStart, timeEnd, isClipPreview }
  const restoringFromUrlRef = useRef(false);
  const closingRef = useRef(false); // Prevent URL restore from re-opening DockPlayer after close
  const [, setPreviewLoading] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const hasAnswerStartedRef = useRef(false);
  const answerRef = useRef("");
  const streamCancelRef = useRef(null);
  const lastSentRef = useRef({ text: null, t: 0 });
  const reloadTimeoutRef = useRef(null);
  const chatEndRef = useRef(null);

  const [showCsvReplace, setShowCsvReplace] = useState(false);
  const [csvReplaceKey, setCsvReplaceKey] = useState(0); // force re-render after replace
  const [reportCollapsed, setReportCollapsed] = useState(false);
  const [timelineCollapsed, setTimelineCollapsed] = useState(true);
  const [expandedTimeline, setExpandedTimeline] = useState({});

  // Clip generation state: { [phaseIndex]: { status, clip_url, error } }
  const [clipStates, setClipStates] = useState({});
  const clipPollingRef = useRef({});

  // Phase rating state: { [phaseIndex]: { rating, comment, saving, saved } }
  const [phaseRatings, setPhaseRatings] = useState({});
  const [ratingComments, setRatingComments] = useState({});

  // Human-in-the-loop Sales Tags state
  // tagEditState: { [phaseIndex]: { editing: bool, saving: bool, saved: bool } }
  const [tagEditState, setTagEditState] = useState({});
  // humanTags: { [phaseIndex]: string[] } — confirmed/edited tags
  const [humanTags, setHumanTags] = useState({});

  // Sales Moments state (click_spike / order_spike / strong markers)
  const [salesMoments, setSalesMoments] = useState([]);

  // AI Event Scores state (sell-ability prediction per phase)
  const [eventScores, setEventScores] = useState([]);

  // Initialize ratings from existing data
  useEffect(() => {
    try {
      if (!videoData?.reports_1) return;
      const initialRatings = {};
      const initialComments = {};
      for (const item of videoData.reports_1) {
        const key = item?.phase_index ?? 0;
        if (item?.user_rating) {
          initialRatings[key] = { rating: item.user_rating, saving: false, saved: true };
        }
        if (item?.user_comment) {
          initialComments[key] = item.user_comment;
        }
      }
      if (Object.keys(initialRatings).length > 0) setPhaseRatings(initialRatings);
      if (Object.keys(initialComments).length > 0) setRatingComments(initialComments);
    } catch (err) {
      console.warn('[VideoDetail] Failed to init ratings:', err);
    }
  }, [videoData?.reports_1]);

  // Initialize human tags from existing data
  useEffect(() => {
    try {
      if (!videoData?.reports_1) return;
      const initial = {};
      for (const item of videoData.reports_1) {
        const key = item?.phase_index ?? 0;
        if (item?.human_sales_tags) {
          initial[key] = item.human_sales_tags;
        }
      }
      if (Object.keys(initial).length > 0) setHumanTags(initial);
    } catch (err) {
      console.warn('[VideoDetail] Failed to init human tags:', err);
    }
  }, [videoData?.reports_1]);

  // Load sales moments when video loads
  useEffect(() => {
    if (!videoData?.id) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await VideoService.getSalesMoments(videoData.id);
        if (!cancelled && res?.sales_moments) {
          setSalesMoments(res.sales_moments);
        }
      } catch (err) {
        console.warn('[VideoDetail] Failed to load sales moments:', err);
      }
    })();
    return () => { cancelled = true; };
  }, [videoData?.id]);

  // Load AI event scores when video loads
  useEffect(() => {
    if (!videoData?.id) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await VideoService.getEventScores(videoData.id);
        if (!cancelled && Array.isArray(res)) {
          setEventScores(res);
        }
      } catch (err) {
        console.warn('[VideoDetail] Failed to load event scores:', err);
      }
    })();
    return () => { cancelled = true; };
  }, [videoData?.id]);

  // Load existing clip statuses when video loads
  useEffect(() => {
    if (!videoData?.id) return;
    (async () => {
      try {
        const res = await VideoService.listClips(videoData.id);
        if (res?.clips && res.clips.length > 0) {
          const states = {};
          for (const clip of res.clips) {
            states[clip.phase_index] = {
              status: clip.status,
              clip_url: clip.clip_url || null,
            };
          }
          setClipStates(states);
        }
      } catch (e) {
        // ignore
      }
    })();
    return () => {
      // Cleanup polling on unmount
      Object.values(clipPollingRef.current).forEach(clearInterval);
    };
  }, [videoData?.id]);

  const showToast = (message, type = 'success') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 2000);
  };

  const getReviewerName = () => localStorage.getItem('aitherhub_reviewer_name') || '';

  const handleRatePhase = (phaseIndex, rating) => {
    if (!videoData?.id) return;
    const comment = ratingComments[phaseIndex] || '';
    const reviewerName = getReviewerName();
    // Optimistic update: immediately reflect in UI
    setPhaseRatings(prev => ({
      ...prev,
      [phaseIndex]: { rating, saving: false, saved: true },
    }));
    showToast('保存しました');
    // Fire-and-forget: save in background
    VideoService.ratePhase(videoData.id, phaseIndex, rating, comment, reviewerName)
      .catch(err => {
        console.error('Failed to rate phase:', err);
        showToast('保存に失敗しました', 'error');
        // Revert on failure
        setPhaseRatings(prev => ({
          ...prev,
          [phaseIndex]: { ...prev[phaseIndex], saved: false },
        }));
      });
  };

  const handleSaveComment = (phaseIndex) => {
    if (!videoData?.id) return;
    const comment = ratingComments[phaseIndex] || '';
    if (!comment.trim()) return;
    const currentRating = phaseRatings[phaseIndex]?.rating;
    // Optimistic update: immediately show saved
    setPhaseRatings(prev => ({
      ...prev,
      [phaseIndex]: { ...prev[phaseIndex], saving: false, saved: true },
    }));
    showToast('保存しました');
    // Use rating API if rating exists, otherwise use comment-only API
    const savePromise = currentRating
      ? VideoService.ratePhase(videoData.id, phaseIndex, currentRating, comment, getReviewerName())
      : VideoService.savePhaseComment(videoData.id, phaseIndex, comment, getReviewerName());
    savePromise.catch(err => {
        console.error('Failed to save comment:', err);
        showToast('保存に失敗しました', 'error');
        // Revert on failure
        setPhaseRatings(prev => ({
          ...prev,
          [phaseIndex]: { ...prev[phaseIndex], saved: false },
        }));
      });
  };

  // ---- Human-in-the-loop Sales Tags handlers ----
  const ALL_SALES_TAGS = [
    'HOOK', 'EMPATHY', 'PROBLEM', 'EDUCATION', 'SOLUTION',
    'DEMONSTRATION', 'COMPARISON', 'PROOF', 'TRUST', 'SOCIAL_PROOF',
    'OBJECTION_HANDLING', 'URGENCY', 'LIMITED_OFFER', 'BONUS', 'CTA',
  ];
  const SALES_TAG_CONFIG = {
    HOOK: { label: 'HOOK', color: 'bg-purple-100 text-purple-700 border-purple-300' },
    EMPATHY: { label: '共感', color: 'bg-pink-100 text-pink-700 border-pink-300' },
    PROBLEM: { label: '問題', color: 'bg-red-50 text-red-600 border-red-200' },
    EDUCATION: { label: '教育', color: 'bg-blue-100 text-blue-700 border-blue-300' },
    SOLUTION: { label: '解決', color: 'bg-green-100 text-green-700 border-green-300' },
    DEMONSTRATION: { label: 'デモ', color: 'bg-teal-100 text-teal-700 border-teal-300' },
    COMPARISON: { label: '比較', color: 'bg-indigo-100 text-indigo-700 border-indigo-300' },
    PROOF: { label: '証拠', color: 'bg-cyan-100 text-cyan-700 border-cyan-300' },
    TRUST: { label: '信頼', color: 'bg-emerald-100 text-emerald-700 border-emerald-300' },
    SOCIAL_PROOF: { label: '社会証明', color: 'bg-violet-100 text-violet-700 border-violet-300' },
    OBJECTION_HANDLING: { label: '反論処理', color: 'bg-amber-100 text-amber-700 border-amber-300' },
    URGENCY: { label: '緊急', color: 'bg-orange-100 text-orange-700 border-orange-300' },
    LIMITED_OFFER: { label: '限定', color: 'bg-rose-100 text-rose-700 border-rose-300' },
    BONUS: { label: '特典', color: 'bg-lime-100 text-lime-700 border-lime-300' },
    CTA: { label: 'CTA', color: 'bg-red-100 text-red-700 border-red-300' },
  };

  const handleTagEditStart = (phaseIndex, aiTags) => {
    // If no human tags yet, pre-fill with AI tags
    if (!humanTags[phaseIndex]) {
      setHumanTags(prev => ({ ...prev, [phaseIndex]: [...(aiTags || [])] }));
    }
    setTagEditState(prev => ({ ...prev, [phaseIndex]: { editing: true, saving: false, saved: false } }));
  };

  const handleTagToggle = (phaseIndex, tag) => {
    setHumanTags(prev => {
      const current = prev[phaseIndex] || [];
      if (current.includes(tag)) {
        return { ...prev, [phaseIndex]: current.filter(t => t !== tag) };
      } else {
        return { ...prev, [phaseIndex]: [...current, tag] };
      }
    });
  };

  const handleTagConfirm = async (phaseIndex, tags) => {
    // Accept tags (from AI confirm or DockPlayer toggle)
    const finalTags = tags || [];
    setHumanTags(prev => ({ ...prev, [phaseIndex]: [...finalTags] }));
    setTagEditState(prev => ({ ...prev, [phaseIndex]: { editing: false, saving: true, saved: false } }));
    try {
      await VideoService.updateHumanSalesTags(videoData.id, phaseIndex, finalTags, getReviewerName());
      setTagEditState(prev => ({ ...prev, [phaseIndex]: { editing: false, saving: false, saved: true } }));
      showToast('保存しました');
    } catch (err) {
      console.error('Failed to confirm tags:', err);
      showToast('保存に失敗しました', 'error');
      setTagEditState(prev => ({ ...prev, [phaseIndex]: { editing: false, saving: false, saved: false } }));
      throw err; // Re-throw so DockPlayer's autoSaveTags can catch it
    }
  };

  const handleTagSave = (phaseIndex) => {
    const tags = humanTags[phaseIndex] || [];
    setTagEditState(prev => ({ ...prev, [phaseIndex]: { editing: false, saving: true, saved: false } }));
    showToast('タグ保存中...');
    VideoService.updateHumanSalesTags(videoData.id, phaseIndex, tags, getReviewerName())
      .then(() => {
        setTagEditState(prev => ({ ...prev, [phaseIndex]: { editing: false, saving: false, saved: true } }));
        showToast('タグ保存済み');
      })
      .catch(err => {
        console.error('Failed to save tags:', err);
        showToast('保存に失敗しました', 'error');
        setTagEditState(prev => ({ ...prev, [phaseIndex]: { editing: true, saving: false, saved: false } }));
      });
  };

  const handleTagEditCancel = (phaseIndex) => {
    setTagEditState(prev => ({ ...prev, [phaseIndex]: { editing: false, saving: false, saved: prev[phaseIndex]?.saved || false } }));
  };

  const handleClipGeneration = async (item, phaseIndex) => {
    if (!videoData?.id) return;
    const timeStart = Number(item.time_start);
    const timeEnd = Number(item.time_end);
    if (isNaN(timeStart) || isNaN(timeEnd)) return;

    // Set loading state
    setClipStates(prev => ({
      ...prev,
      [phaseIndex]: { status: 'requesting' },
    }));

    try {
      const res = await VideoService.requestClipGeneration(videoData.id, phaseIndex, timeStart, timeEnd);

      if (res.status === 'completed' && res.clip_url) {
        // Show subtitle generation status while transcribing
        setClipStates(prev => ({
          ...prev,
          [phaseIndex]: { status: 'generating_subtitles', clip_url: res.clip_url, clip_id: res.clip_id },
        }));
        // Await subtitle generation before marking as completed
        try {
          console.log(`[AutoTranscribe] Clip immediately completed for phase ${phaseIndex}, triggering transcription`);
          const transcribeRes = await VideoService.transcribeClip(videoData.id, {
            clip_url: res.clip_url,
            time_start: timeStart,
            time_end: timeEnd,
            phase_index: phaseIndex,
          });
          if (transcribeRes?.segments?.length > 0) {
            console.log(`[AutoTranscribe] Generated ${transcribeRes.segments.length} subtitles for phase ${phaseIndex}`);
            const newCaps = transcribeRes.segments.map(s => ({
              start: s.start, end: s.end, text: s.text, source: 'whisper',
            }));
            if (res.clip_id) {
              await VideoService.updateClipCaptions(videoData.id, res.clip_id, newCaps);
              console.log(`[AutoTranscribe] Saved subtitles for phase ${phaseIndex}`);
            }
          }
        } catch (transcribeErr) {
          console.warn(`[AutoTranscribe] Transcription failed for phase ${phaseIndex}:`, transcribeErr);
        }
        // Mark as fully completed (video + subtitles)
        setClipStates(prev => ({
          ...prev,
          [phaseIndex]: { status: 'completed', clip_url: res.clip_url, clip_id: res.clip_id },
        }));
        return;
      }

      setClipStates(prev => ({
        ...prev,
        [phaseIndex]: { status: res.status || 'pending' },
      }));

      // Start polling for status
      if (clipPollingRef.current[phaseIndex]) {
        clearInterval(clipPollingRef.current[phaseIndex]);
      }
      clipPollingRef.current[phaseIndex] = setInterval(async () => {
        try {
          const statusRes = await VideoService.getClipStatus(videoData.id, phaseIndex);
          if (statusRes.status === 'completed' && statusRes.clip_url) {
            clearInterval(clipPollingRef.current[phaseIndex]);
            delete clipPollingRef.current[phaseIndex];
            // Show subtitle generation status
            setClipStates(prev => ({
              ...prev,
              [phaseIndex]: { status: 'generating_subtitles', clip_url: statusRes.clip_url, clip_id: statusRes.clip_id },
            }));
            // Await subtitle generation before marking as completed
            try {
              console.log(`[AutoTranscribe] Clip completed for phase ${phaseIndex}, triggering transcription`);
              const transcribeRes = await VideoService.transcribeClip(videoData.id, {
                clip_url: statusRes.clip_url,
                time_start: timeStart,
                time_end: timeEnd,
                phase_index: phaseIndex,
              });
              if (transcribeRes?.segments?.length > 0) {
                console.log(`[AutoTranscribe] Generated ${transcribeRes.segments.length} subtitles for phase ${phaseIndex}`);
                const newCaps = transcribeRes.segments.map(s => ({
                  start: s.start, end: s.end, text: s.text, source: 'whisper',
                }));
                if (statusRes.clip_id) {
                  await VideoService.updateClipCaptions(videoData.id, statusRes.clip_id, newCaps);
                  console.log(`[AutoTranscribe] Saved subtitles for phase ${phaseIndex}`);
                }
              }
            } catch (transcribeErr) {
              console.warn(`[AutoTranscribe] Transcription failed for phase ${phaseIndex}:`, transcribeErr);
            }
            // Mark as fully completed (video + subtitles)
            setClipStates(prev => ({
              ...prev,
              [phaseIndex]: { status: 'completed', clip_url: statusRes.clip_url, clip_id: statusRes.clip_id },
            }));
          } else if (statusRes.status === 'failed') {
            setClipStates(prev => ({
              ...prev,
              [phaseIndex]: { status: 'failed', error: statusRes.error_message },
            }));
            clearInterval(clipPollingRef.current[phaseIndex]);
            delete clipPollingRef.current[phaseIndex];
          }
        } catch (e) {
          // continue polling
        }
      }, 5000); // Poll every 5 seconds

    } catch (e) {
      setClipStates(prev => ({
        ...prev,
        [phaseIndex]: { status: 'failed', error: e.message },
      }));
    }
  };

  const scrollToBottom = (smooth = true) => {
    if (chatEndRef.current) {
      try {
        chatEndRef.current.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "end" });
      } catch {
        // Ignore scroll errors
        void 0;
      }
    }
  };

  // Detect old Safari iOS (<=16) - remark-gfm table parsing can crash/blank-screen on these versions.
  const isOldSafariIOS = (() => {
    if (typeof window === "undefined") return false;
    const ua = navigator.userAgent;
    const isSafariBrowser = /^((?!chrome|android).)*safari/i.test(ua);
    if (!isSafariBrowser) return false;
    const iosVersionMatch = ua.match(/OS (\d+)_/);
    if (iosVersionMatch) {
      const majorVersion = parseInt(iosVersionMatch[1], 10);
      return majorVersion <= 16;
    }
    return false;
  })();

  const formatTime = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const handlePhasePreview = async (phase) => {
    console.log('handlePhasePreview called with phase:', {
      time_start: phase?.time_start,
      time_end: phase?.time_end,
      phase_index: phase?.phase_index,
      video_clip_url: phase?.video_clip_url,
    });

    if (!phase?.time_start && !phase?.time_end) {
      return;
    }
    if (!videoData?.id) {
      return;
    }

    setPreviewLoading(true);
    try {
      // ── Build video URLs without pre-checking (avoids CORS/timeout issues) ──
      // Priority: clip URL > preview_url > backend download URL
      // DockPlayer handles video load errors via onerror fallback
      let url = null;
      let fullUrl = null;
      let isClip = false;

      // 1) Use clip URL directly if available (no fetch check — let <video> handle errors)
      if (phase?.video_clip_url) {
        url = phase.video_clip_url;
        isClip = true;
        console.log('Using clip URL for playback');
      }

      // 2) Fallback: compressed preview URL
      if (!url && videoData?.preview_url) {
        url = videoData.preview_url;
        console.log('Using compressed preview URL for playback');
      }

      // 3) Fallback: backend download URL (generates fresh SAS token)
      if (!url) {
        try {
          const downloadUrl = await VideoService.getDownloadUrl(videoData.id);
          if (downloadUrl) {
            url = downloadUrl;
            console.log('Using backend download URL for playback');
          }
        } catch (err) {
          console.error('Failed to get backend download URL', err);
        }
      }

      if (!url) {
        console.error('No preview URL available for this phase');
        return;
      }

      // Resolve full video URL for DockPlayer navigation
      // Use preview_url or backend download URL (don't pre-check, lazy resolve)
      if (isClip) {
        fullUrl = videoData?.preview_url || null;
        // If no preview_url, get download URL from backend
        if (!fullUrl) {
          try {
            fullUrl = await VideoService.getDownloadUrl(videoData.id);
          } catch (err) {
            console.error('Failed to get full video URL for DockPlayer fallback', err);
          }
        }
      }

      const previewDataObj = {
        url,
        fullVideoUrl: fullUrl || url,
        timeStart: Number(phase.time_start) || 0,
        timeEnd: phase.time_end != null ? Number(phase.time_end) : null,
        isClipPreview: isClip,
      };
      console.log('Setting previewData:', previewDataObj);

      setPreviewData(previewDataObj);

      // Update URL query params to persist DockPlayer state across reload
      const phaseIdx = phase.phase_index ?? (videoData?.reports_1 || []).findIndex(
        r => r.time_start === phase.time_start && r.time_end === phase.time_end
      );
      if (phaseIdx >= 0) {
        setSearchParams(prev => {
          const next = new URLSearchParams(prev);
          next.set('view', 'timeline');
          next.set('phase', String(phaseIdx));
          return next;
        }, { replace: true });
      }
    } catch (err) {
      console.error("Failed to load preview url", err);
    } finally {
      setPreviewLoading(false);
    }
  };

  // ── Close DockPlayer and clear URL params ──
  const handleCloseDockPlayer = useCallback(() => {
    closingRef.current = true;
    setPreviewData(null);
    setSearchParams(prev => {
      const next = new URLSearchParams(prev);
      next.delete('view');
      next.delete('phase');
      return next;
    }, { replace: true });
    // Keep closingRef true for a tick to prevent URL restore useEffect from re-opening
    setTimeout(() => { closingRef.current = false; }, 200);
  }, [setSearchParams]);

  // ── Restore DockPlayer from URL query params on reload ──
  useEffect(() => {
    if (restoringFromUrlRef.current) return;
    if (closingRef.current) return; // Don't re-open while closing
    const view = searchParams.get('view');
    const phaseParam = searchParams.get('phase');
    if (view !== 'timeline' || phaseParam == null) return;
    if (!videoData?.reports_1?.length) return;
    if (previewData) return; // already open

    const phaseIdx = parseInt(phaseParam, 10);
    if (isNaN(phaseIdx) || phaseIdx < 0 || phaseIdx >= videoData.reports_1.length) return;

    const phase = videoData.reports_1[phaseIdx];
    if (!phase) return;

    restoringFromUrlRef.current = true;
    handlePhasePreview(phase).finally(() => {
      restoringFromUrlRef.current = false;
    });
  }, [videoData?.reports_1, searchParams, previewData]);

  const reloadHistory = async () => {
    const vid = videoData?.id;
    if (!vid) return;
    try {
      const hist = await VideoService.getChatHistory(vid);
      if (Array.isArray(hist)) {
        setChatMessages(hist);
      } else if (hist && Array.isArray(hist.data)) {
        setChatMessages(hist.data);
      } else {
        setChatMessages([]);
      }
    } catch (err) {
      console.error("Failed to reload chat history:", err);
    }
  };

  const handleChatSend = (text) => {
    try {
      const vid = videoData?.id;
      if (streamCancelRef.current) {
        return;
      }
      const hasReport = !!(videoData && Array.isArray(videoData.reports_1) && videoData.reports_1.length > 0);
      const statusDone = (videoData && (String(videoData.status || "").toUpperCase() === "DONE")) || false;
      if (!vid || !(hasReport || statusDone)) {
        return;
      }
      const now = Date.now();
      if (lastSentRef.current.text === text && now - lastSentRef.current.t < 1000) {
        return;
      }
      lastSentRef.current = { text, t: now };
      if (streamCancelRef.current) {
        try {
          if (typeof streamCancelRef.current.cancel === "function") streamCancelRef.current.cancel();
          else if (typeof streamCancelRef.current === "function") streamCancelRef.current();
        } catch {
          void 0;
        }
        streamCancelRef.current = null;
      }
      if (reloadTimeoutRef.current) {
        clearTimeout(reloadTimeoutRef.current);
        reloadTimeoutRef.current = null;
      }
      answerRef.current = "";
      hasAnswerStartedRef.current = false;

      const localId = `local-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
      setChatMessages((prev) => [...prev, { id: localId, question: text, answer: "" }]);
      setIsThinking(true);

      const streamHandle = VideoService.streamChat({
        videoId: videoData?.id,
        messages: [{ role: "user", content: text }],
        onMessage: (chunk) => {
          try {
            if (!hasAnswerStartedRef.current) {
              hasAnswerStartedRef.current = true;
              setIsThinking(false);
            }
            let processed = chunk;
            try {
              processed = processed.replace(/\\r\\n/g, "\r\n").replace(/\\n/g, "\n");
              processed = processed.replace(/([.!?])\s+([A-ZÀ-ỸÂÊÔƠƯĂĐ])/g, "$1\n$2");
            } catch {
              void 0;
            }

            answerRef.current += processed;
            setChatMessages((prev) =>
              prev.map((it) => (it.id === localId ? { ...it, answer: (it.answer || "") + processed } : it))
            );
          } catch (e) {
            console.error("onMessage processing error", e);
          }
        },
        onDone: () => {
          streamCancelRef.current = null;
          setIsThinking(false);
          if (reloadTimeoutRef.current) clearTimeout(reloadTimeoutRef.current);
          reloadTimeoutRef.current = setTimeout(() => {
            reloadHistory();
            reloadTimeoutRef.current = null;
          }, 500);
        },
        onError: (err) => {
          console.error("Chat stream error:", err);
          streamCancelRef.current = null;
          setIsThinking(false);
        },
      });

      streamCancelRef.current = streamHandle;
    } catch (err) {
      console.error("handleChatSend error:", err);
      setIsThinking(false);
    }
  };

  useEffect(() => {
    const onGlobalSubmit = (ev) => {
      try {
        const text = ev?.detail?.text;
        if (text && !streamCancelRef.current) handleChatSend(text);
      } catch {
        void 0;
      }
    };
    window.addEventListener("videoInput:submitted", onGlobalSubmit);
    return () => {
      window.removeEventListener("videoInput:submitted", onGlobalSubmit);
      if (streamCancelRef.current) {
        try {
          if (typeof streamCancelRef.current.cancel === "function") streamCancelRef.current.cancel();
          else if (typeof streamCancelRef.current === "function") streamCancelRef.current();
        } catch {
          void 0;
        }
        streamCancelRef.current = null;
      }
      setIsThinking(false);
      if (reloadTimeoutRef.current) {
        clearTimeout(reloadTimeoutRef.current);
        reloadTimeoutRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    console.log("Loading chat history for video:", videoData);
    let cancelled = false;
    const vid = videoData?.id;
    if (!vid) {
      setChatMessages([]);
      return;
    }

    (async () => {
      try {
        setChatMessages([]);
        const hist = await VideoService.getChatHistory(vid);
        if (!cancelled) {
          if (Array.isArray(hist)) {
            setChatMessages(hist);
            setTimeout(() => scrollToBottom(false), 30);
          } else if (hist && Array.isArray(hist.data)) {
            setChatMessages(hist.data);
            setTimeout(() => scrollToBottom(false), 30);
          } else {
            setChatMessages([]);
            setTimeout(() => scrollToBottom(false), 30);
          }
        }
      } catch (err) {
        if (!cancelled) console.error("Failed to load chat history:", err);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [videoData]);

  useEffect(() => {
    if (chatEndRef.current) {
      try {
        chatEndRef.current.scrollIntoView({ behavior: "smooth" });
      } catch {
        void 0;
      }
    }
  }, [chatMessages]);

  if (loading) {
    return (
      <div className="w-full h-full flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-gray-600"></div>
      </div>
    );
  }

  if (error && !videoData) {
    return (
      <div className="w-full h-full flex items-center justify-center">
        <p className="text-red-500 text-lg">{error}</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden w-full h-full flex flex-col gap-6 p-0 md:overflow-auto lg:p-6">
      <style>{markdownTableStyles}</style>
      {/* Toast notification */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg shadow-lg text-sm font-medium transition-all duration-300 animate-fade-in ${
          toast.type === 'error' ? 'bg-red-500 text-white' : 'bg-green-500 text-white'
        }`}>
          {toast.message}
        </div>
      )}
      {/* Video Header */}
      <div className="flex flex-col overflow-hidden md:overflow-auto h-full w-full mx-auto">
        <div className="flex flex-col gap-2 items-center">
          <div className="px-4 py-2 rounded-full border border-gray-200 bg-gray-50 text-gray-700 text-xs flex items-center gap-2">
            <span>{videoData?.original_filename}</span>
            {(() => {
              // Calculate total video duration from phases
              const phases = videoData?.reports_1 || videoData?.reports_2 || [];
              if (phases.length === 0) return null;
              const maxEnd = Math.max(...phases.map(p => p.time_end || 0));
              if (!maxEnd || maxEnd <= 0) return null;
              const h = Math.floor(maxEnd / 3600);
              const m = Math.floor((maxEnd % 3600) / 60);
              const s = Math.floor(maxEnd % 60);
              const durStr = h > 0
                ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
                : `${m}:${String(s).padStart(2, '0')}`;
              return (
                <span className="text-gray-400 whitespace-nowrap" title="\u52d5\u753b\u306e\u518d\u751f\u6642\u9593">
                  {'🎬'} {durStr}
                </span>
              );
            })()}
          </div>
        </div>
        {/* Partial Error Warning Banner */}
        {videoData?.status === 'ERROR' && (
          <div className="mx-4 mt-3 mb-1 px-4 py-3 rounded-xl border border-amber-300/40 bg-amber-50 flex items-center gap-3">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-amber-500 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            <div className="flex-1">
              <p className="text-sm font-semibold text-amber-800">一部の処理が完了していません</p>
              <p className="text-xs text-amber-600 mt-0.5">解析結果は表示できますが、一部のレポートが未生成の可能性があります。</p>
            </div>
          </div>
        )}
        {/* SCROLL AREA */}
        <div className="flex-1 overflow-y-auto scrollbar-custom text-left px-0 md:px-4 md:mb-0">
          {/* CSV / Excel Info Panel */}
          <SectionErrorBoundary sectionName="CSVアセット管理">
            <CsvAssetPanel
              key={csvReplaceKey}
              videoData={videoData}
              onReplace={() => setShowCsvReplace(true)}
              onRefresh={() => setCsvReplaceKey((k) => k + 1)}
            />
          </SectionErrorBoundary>

          {/* CSV Replace Modal */}
          {showCsvReplace && (
            <CsvReplaceModal
              videoData={videoData}
              onClose={() => setShowCsvReplace(false)}
              onComplete={() => {
                setShowCsvReplace(false);
                setCsvReplaceKey((k) => k + 1);
              }}
            />
          )}

          {/* Clip Section - show generated clips at the top */}
          <SectionErrorBoundary sectionName="切り抜き動画">
            <ClipSection videoData={videoData} clipStates={clipStates} reports1={videoData?.reports_1} />
          </SectionErrorBoundary>

          {/* AI Sales Clip Candidates */}
          <SectionErrorBoundary sectionName="AIおすすめクリップ">
            <SalesClipCandidates
              videoData={videoData}
              clipStates={clipStates}
              onRequestClip={(candidate) => {
                handleClipGeneration(
                  { time_start: candidate.time_start, time_end: candidate.time_end },
                  candidate.phase_index
                );
              }}
            />
          </SectionErrorBoundary>

          {/* Sales Moment Clips - spike-based */}
          <SectionErrorBoundary sectionName="Sales Moment Clips">
            <SalesMomentClips
              videoData={videoData}
              clipStates={clipStates}
              onRequestClip={(candidate) => {
                handleClipGeneration(
                  { time_start: candidate.time_start, time_end: candidate.time_end },
                  candidate.phase_index
                );
              }}
            />
          </SectionErrorBoundary>

          {/* Moment Clips - category-based (screen recording) */}
          <SectionErrorBoundary sectionName="Moment Clips">
            <MomentClips
              videoData={videoData}
              clipStates={clipStates}
              onRequestClip={(candidate) => {
                handleClipGeneration(
                  { time_start: candidate.time_start, time_end: candidate.time_end },
                  candidate.phase_index
                );
              }}
            />
          </SectionErrorBoundary>

          {/* Hook Detection */}
          <SectionErrorBoundary sectionName="Hook Detection">
            <HookDetection
              videoData={videoData}
              onSelectHook={(hook) => {
                // フック選択時にプレビュー
                handlePhasePreview({ time_start: hook.start_sec, time_end: hook.end_sec });
              }}
            />
          </SectionErrorBoundary>

          {/* Analytics Section - above report */}
          <SectionErrorBoundary sectionName="配信パフォーマンス">
            <AnalyticsSection reports1={videoData?.reports_1} videoData={videoData}
              onPreviewSegment={(timeStart, timeEnd) => {
                handlePhasePreview({ time_start: timeStart, time_end: timeEnd });
              }}
            />
          </SectionErrorBoundary>

          {/* Live Report v1 - 3-layer report */}
          <SectionErrorBoundary sectionName="ライブレポート">
            <div className="w-full mt-2 mx-auto">
              <div className="rounded-2xl bg-gray-50 border border-gray-200 p-4 md:p-5">
                <LiveReportSection videoData={videoData} />
              </div>
            </div>
          </SectionErrorBoundary>

          {/* Product Timeline is now integrated into AnalyticsSection above */}

          <SectionErrorBoundary sectionName="レポート：全体戦略">
          <div className="w-full mt-6 mx-auto">
            <div className="rounded-2xl bg-gray-50 border border-gray-200">
              <div onClick={() => setReportCollapsed((s) => !s)} className="flex items-center justify-between p-5 cursor-pointer hover:bg-gray-100 transform transition-all duration-200">
                <div className="flex items-center gap-4">
                  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-target w-5 h-5 text-gray-700"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>
                  <div>
                    <div className="text-gray-900 text-xl font-semibold">{'レポート：全体戦略'}</div>
                    <div className="text-gray-500 text-sm mt-1">{videoData?.created_at ? new Date(videoData.created_at).toLocaleString() : ''}</div>
                  </div>
                </div>

                <button
                  type="button"
                  aria-expanded={!reportCollapsed}
                  aria-label={reportCollapsed ? (window.__t ? window.__t('expand') : 'expand') : (window.__t ? window.__t('collapse') : 'collapse')}
                  className="text-gray-400 p-2 rounded focus:outline-none transition-colors"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={`w-6 h-6 transform transition-transform duration-200 ${!reportCollapsed ? 'rotate-180' : ''}`}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              </div>
              {/* Inner panels: tag + summary + suggestion */}
              {!reportCollapsed && (
                <div className="px-5 flex flex-col gap-4">
                  {videoData?.report3 && Array.isArray(videoData.report3) && videoData.report3.length > 0 && videoData.report3.map((r, i) => (
                    <div key={`report3-${i}`} className="rounded-xl p-6 bg-white border border-gray-200 shadow-sm">
                      <div className="flex items-start gap-4">
                        <div className="flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium bg-orange-100 text-orange-600">
                          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-flame w-3.5 h-3.5"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"></path></svg>
                          <span>高インパクト</span>
                        </div>
                      </div>

                      <div className="mt-4 grid grid-cols-1 gap-4">
                        <div className="flex items-start gap-4">
                          <div className="text-blue-500">
                            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-file-text w-4 h-4 flex-shrink-0"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"></path><path d="M14 2v4a2 2 0 0 0 2 2h4"></path><path d="M10 9H8"></path><path d="M16 13H8"></path><path d="M16 17H8"></path></svg>
                          </div>
                          <div className="min-w-0">
                            <div className="text-blue-600 font-medium text-xs">概要</div>
                            <div className="text-gray-700 mt-2 text-sm">
                              <div className="markdown">
                                <MarkdownWithTables
                                  markdown={r.title || window.__t('noDescription')}
                                  isOldSafariIOS={isOldSafariIOS}
                                  keyPrefix={`report3-title-${i}`}
                                />
                              </div>
                            </div>
                          </div>
                        </div>

                        <div className="flex items-start gap-4">
                          <div className="text-green-500">
                            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-circle-check w-4 h-4 flex-shrink-0"><circle cx="12" cy="12" r="10"></circle><path d="m9 12 2 2 4-4"></path></svg>
                          </div>
                          <div className="min-w-0">
                            <div className="text-green-600 font-medium text-xs">提案</div>
                            <div className="text-gray-700 mt-2 text-sm">
                              <div className="markdown">
                                <MarkdownWithTables
                                  markdown={r.content || window.__t('noDescription')}
                                  isOldSafariIOS={isOldSafariIOS}
                                  keyPrefix={`report3-content-${i}`}
                                />
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                  {/* Divider and Timeline Section */}
                  <div className="space-y-3 pt-2 border-t border-gray-200">
                    <div
                      className="flex items-center justify-between p-3 rounded-lg cursor-pointer hover:bg-gray-100 transform transition-all duration-200"
                      onClick={() => setTimelineCollapsed((s) => !s)}
                    >
                      <div className="flex items-center gap-4">
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-clock w-4 h-4 text-gray-400"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                        <div>
                          <div className="text-gray-600 text-base font-semibold">詳細分析（タイムライン）</div>
                        </div>
                      </div>

                      <button
                        type="button"
                        className="text-gray-400 rounded focus:outline-none transition-colors"
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={`w-6 h-6 transform transition-transform duration-200 cursor-pointer ${!timelineCollapsed ? 'rotate-180' : ''}`}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                        </svg>
                      </button>
                    </div>
                    {/* Report Section */}
                    <div className="pb-4">
                      {!timelineCollapsed && videoData?.reports_1 && videoData.reports_1.map((item, index) => {
                        const itemKey = item.phase_index ?? index;
                        return (
                          <div key={`timeline-${itemKey}`}>
                            <div className="mt-4 rounded-xl bg-white border border-gray-200 shadow-sm mx-5">
                              <div
                                className={`flex items-start justify-between flex-col md:flex-row gap-4 px-4 py-3 border-l-4 border-orange-400 rounded-xl transition-colors cursor-pointer ${expandedTimeline[itemKey] ? 'bg-orange-50 hover:bg-orange-50' : 'hover:bg-gray-50'
                                  }`}
                                role="button"
                                tabIndex={0}
                                aria-expanded={!!expandedTimeline[itemKey]}
                                onClick={() => setExpandedTimeline((prev) => ({ ...prev, [itemKey]: !prev[itemKey] }))}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    setExpandedTimeline((prev) => ({ ...prev, [itemKey]: !prev[itemKey] }));
                                  }
                                }}
                              >
                                <div className="w-full flex items-start justify-between gap-4">
                                  <div className="flex flex-1 min-w-0 items-start gap-3">
                                    <div
                                      className="flex items-start gap-3 cursor-pointer hover:opacity-80 transition-opacity"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        handlePhasePreview(item);
                                      }}
                                    >
                                      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                                        className="lucide lucide-play w-4 h-4 text-gray-500 flex-shrink-0 mt-0.5"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                                      <div className="text-gray-700 text-sm font-semibold whitespace-nowrap">
                                        {item.time_start != null || item.time_end != null ? (
                                          <>
                                            {formatTime(item.time_start)}
                                            {" – "}
                                            {formatTime(item.time_end)}
                                          </>
                                        ) : (
                                          <span className="text-gray-400">-</span>
                                        )}
                                      </div>
                                    </div>
                                    <div
                                      className={`hidden flex-1 min-w-0 text-gray-600 text-sm ${expandedTimeline[itemKey] ? 'md:block' : 'md:line-clamp-1'
                                        }`}
                                    >
                                      {item.phase_description || window.__t('noDescription')}
                                    </div>
                                  </div>
                                  <div className="flex items-start gap-2 flex-shrink-0 mt-0.5">
                                    {/* Memo mark - shown when user has a comment */}
                                    {(ratingComments[itemKey] || item.user_comment) && (
                                      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-blue-100 text-blue-600 border border-blue-200" title="メモあり">
                                        <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                        </svg>
                                        メモ
                                      </span>
                                    )}
                                    {item.cta_score != null && item.cta_score >= 3 && (
                                      <span
                                        className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-bold border ${
                                          item.cta_score >= 5
                                            ? 'bg-red-100 text-red-700 border-red-300'
                                            : item.cta_score >= 4
                                            ? 'bg-orange-100 text-orange-700 border-orange-300'
                                            : 'bg-yellow-100 text-yellow-700 border-yellow-300'
                                        }`}
                                        title={`CTA強度: ${item.cta_score}/5`}
                                      >
                                        <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                                          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                                        </svg>
                                        CTA {item.cta_score}
                                      </span>
                                    )}
                                    {item.sales_psychology_tags && item.sales_psychology_tags.length > 0 && (
                                      <div className="flex flex-wrap gap-0.5">
                                        {item.sales_psychology_tags.map((tag) => {
                                          const tagConfig = {
                                            HOOK: { label: 'HOOK', color: 'bg-purple-100 text-purple-700 border-purple-300' },
                                            EMPATHY: { label: '共感', color: 'bg-pink-100 text-pink-700 border-pink-300' },
                                            PROBLEM: { label: '問題', color: 'bg-red-50 text-red-600 border-red-200' },
                                            EDUCATION: { label: '教育', color: 'bg-blue-100 text-blue-700 border-blue-300' },
                                            SOLUTION: { label: '解決', color: 'bg-green-100 text-green-700 border-green-300' },
                                            DEMONSTRATION: { label: 'デモ', color: 'bg-teal-100 text-teal-700 border-teal-300' },
                                            COMPARISON: { label: '比較', color: 'bg-indigo-100 text-indigo-700 border-indigo-300' },
                                            PROOF: { label: '証拠', color: 'bg-cyan-100 text-cyan-700 border-cyan-300' },
                                            TRUST: { label: '信頼', color: 'bg-emerald-100 text-emerald-700 border-emerald-300' },
                                            SOCIAL_PROOF: { label: '社会証明', color: 'bg-violet-100 text-violet-700 border-violet-300' },
                                            OBJECTION_HANDLING: { label: '反論処理', color: 'bg-amber-100 text-amber-700 border-amber-300' },
                                            URGENCY: { label: '緊急', color: 'bg-orange-100 text-orange-700 border-orange-300' },
                                            LIMITED_OFFER: { label: '限定', color: 'bg-rose-100 text-rose-700 border-rose-300' },
                                            BONUS: { label: '特典', color: 'bg-lime-100 text-lime-700 border-lime-300' },
                                            CTA: { label: 'CTA', color: 'bg-red-100 text-red-700 border-red-300' },
                                          };
                                          const cfg = tagConfig[tag] || { label: tag, color: 'bg-gray-100 text-gray-600 border-gray-300' };
                                          return (
                                            <span
                                              key={tag}
                                              className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[8px] font-semibold border ${cfg.color}`}
                                              title={tag}
                                            >
                                              {cfg.label}
                                            </span>
                                          );
                                        })}
                                      </div>
                                    )}
                                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-flame w-4 h-4 text-orange-500"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"></path></svg>
                                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
                                      stroke="currentColor" strokeWidth="1.5"
                                      className={`w-5 h-5 text-gray-400 transition-transform duration-200
                                  cursor-pointer 
                                  ${expandedTimeline[itemKey] ? 'rotate-180' : ''}`}
                                    >
                                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                                    </svg>
                                  </div>
                                </div>
                                <div
                                  className={`md:hidden min-w-0 text-gray-600 text-sm ${!expandedTimeline[itemKey] ? 'line-clamp-1' : ''
                                    }`}
                                >
                                  {item.phase_description || window.__t('noDescription')}
                                </div>
                                {/* Inline Phase Rating - always visible (collapsed or expanded) */}
                                <div className="flex items-center gap-2 mt-2" onClick={(e) => e.stopPropagation()}>
                                  <div className="flex items-center gap-0.5">
                                    {[1, 2, 3, 4, 5].map((star) => {
                                      const currentRating = phaseRatings[itemKey]?.rating || 0;
                                      const isSaving = phaseRatings[itemKey]?.saving;
                                      return (
                                        <button
                                          key={star}
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            if (!isSaving) handleRatePhase(itemKey, star);
                                          }}
                                          disabled={isSaving}
                                          className={`p-0 transition-all duration-150 ${
                                            isSaving ? 'opacity-50 cursor-not-allowed' : 'hover:scale-125 cursor-pointer'
                                          }`}
                                          title={`${star}点`}
                                        >
                                          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"
                                            fill={star <= currentRating ? '#f59e0b' : 'none'}
                                            stroke={star <= currentRating ? '#f59e0b' : '#d1d5db'}
                                            strokeWidth="1.5"
                                            className="w-4 h-4"
                                          >
                                            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
                                          </svg>
                                        </button>
                                      );
                                    })}
                                  </div>
                                  {phaseRatings[itemKey]?.saving && (
                                    <div className="w-3 h-3 rounded-full border-2 border-gray-300 border-t-amber-500 animate-spin" />
                                  )}
                                  {phaseRatings[itemKey]?.saved && !phaseRatings[itemKey]?.saving && (
                                    <span className="text-[9px] text-green-500 font-medium">保存済み</span>
                                  )}
                                  {!phaseRatings[itemKey]?.rating && (
                                    <span className="text-[10px] text-gray-400">採点する</span>
                                  )}
                                </div>
                                {/* CSV Metrics Badges */}
                                {item.csv_metrics && (
                                  (() => {
                                    const m = item.csv_metrics;
                                    const hasAnyData = m.gmv > 0 || m.order_count > 0 || m.viewer_count > 0 || m.like_count > 0 || m.comment_count > 0 || m.new_followers > 0 || m.product_clicks > 0;
                                    if (!hasAnyData) return null;
                                    return (
                                      <div className="flex flex-wrap gap-1.5 mt-2">
                                        {m.gmv > 0 && (
                                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-yellow-100 text-yellow-700 border border-yellow-300">
                                            <span>{'\u00A5'}</span>{Math.round(m.gmv).toLocaleString()}
                                          </span>
                                        )}
                                        {m.order_count > 0 && (
                                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-100 text-green-700 border border-green-300">
                                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
                                            {m.order_count}件
                                          </span>
                                        )}
                                        {m.viewer_count > 0 && (
                                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-blue-100 text-blue-700 border border-blue-300">
                                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                                            {m.viewer_count.toLocaleString()}
                                          </span>
                                        )}
                                        {m.like_count > 0 && (
                                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-pink-100 text-pink-700 border border-pink-300">
                                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
                                            {m.like_count.toLocaleString()}
                                          </span>
                                        )}
                                        {m.comment_count > 0 && (
                                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-purple-100 text-purple-700 border border-purple-300">
                                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                                            {m.comment_count}
                                          </span>
                                        )}
                                        {m.new_followers > 0 && (
                                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-cyan-100 text-cyan-700 border border-cyan-300">
                                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/></svg>
                                            +{m.new_followers}
                                          </span>
                                        )}
                                        {m.product_clicks > 0 && (
                                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-orange-100 text-orange-700 border border-orange-300">
                                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
                                            {m.product_clicks}クリック
                                          </span>
                                        )}
                                        {m.product_names && m.product_names.length > 0 && (
                                          m.product_names.map((name, idx) => (
                                            <span key={idx} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-100 text-indigo-700 border border-indigo-300">
                                              <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
                                              {name}
                                            </span>
                                          ))
                                        )}
                                      </div>
                                    );
                                  })()
                                )}
                              </div>
                            </div>
                            {/* Expanded content sections */}
                            {expandedTimeline[itemKey] && (
                              <div className="px-4 pb-4 mt-4 ml-15 flex flex-col gap-4 rounded-xl py-3 bg-gray-50 border border-gray-200 mr-5">
                                {/* 概要 (Overview) section */}
                                <div className="flex items-start gap-4">
                                  <div className="text-blue-500">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-file-text w-4 h-4 flex-shrink-0"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"></path><path d="M14 2v4a2 2 0 0 0 2 2h4"></path><path d="M10 9H8"></path><path d="M16 13H8"></path><path d="M16 17H8"></path></svg>
                                  </div>
                                  <div className="min-w-0">
                                    <div className="text-blue-600 font-medium text-xs">概要</div>
                                    <div className="text-gray-700 mt-2 text-sm">
                                      <div className="markdown">
                                        <MarkdownWithTables
                                          markdown={item.phase_description || window.__t('noDescription')}
                                          isOldSafariIOS={isOldSafariIOS}
                                          keyPrefix={`timeline-overview-${itemKey}`}
                                        />
                                      </div>
                                    </div>
                                  </div>
                                </div>

                                {/* 提案 (Suggestion) section */}
                                {item.insight && (
                                  <div className="flex items-start gap-4">
                                    <div className="text-green-500">
                                      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-circle-check w-4 h-4 flex-shrink-0"><circle cx="12" cy="12" r="10"></circle><path d="m9 12 2 2 4-4"></path></svg>
                                    </div>
                                    <div className="min-w-0">
                                      <div className="text-green-600 font-medium text-xs">提案</div>
                                      <div className="text-gray-700 mt-2 text-sm">
                                        <div className="markdown">
                                          <MarkdownWithTables
                                            markdown={item.insight || window.__t('noInsight')}
                                            isOldSafariIOS={isOldSafariIOS}
                                            keyPrefix={`timeline-insight-${itemKey}`}
                                          />
                                        </div>
                                      </div>
                                    </div>
                                  </div>
                                )}

                                {/* Audio Features Section */}
                                {item.audio_features && (
                                  <div className="flex items-start gap-4">
                                    <div className="text-purple-500">
                                      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4 flex-shrink-0">
                                        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
                                        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                                        <line x1="12" x2="12" y1="19" y2="22"/>
                                      </svg>
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-purple-600 font-medium text-xs mb-2">音声分析</div>
                                      <div className="flex flex-wrap gap-2">
                                        {item.audio_features.energy_mean != null && (
                                          <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-purple-50 border border-purple-200">
                                            <span className="text-[10px] text-purple-500">熱量</span>
                                            <div className="w-16 h-1.5 bg-purple-100 rounded-full overflow-hidden">
                                              <div
                                                className="h-full bg-purple-500 rounded-full"
                                                style={{ width: `${Math.min(100, (item.audio_features.energy_mean / 0.05) * 100)}%` }}
                                              />
                                            </div>
                                            <span className="text-[10px] font-medium text-purple-700">
                                              {item.audio_features.energy_mean >= 0.03 ? '高' : item.audio_features.energy_mean >= 0.015 ? '中' : '低'}
                                            </span>
                                          </div>
                                        )}
                                        {item.audio_features.pitch_std != null && (
                                          <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-indigo-50 border border-indigo-200">
                                            <span className="text-[10px] text-indigo-500">抑揚</span>
                                            <div className="w-16 h-1.5 bg-indigo-100 rounded-full overflow-hidden">
                                              <div
                                                className="h-full bg-indigo-500 rounded-full"
                                                style={{ width: `${Math.min(100, (item.audio_features.pitch_std / 80) * 100)}%` }}
                                              />
                                            </div>
                                            <span className="text-[10px] font-medium text-indigo-700">
                                              {item.audio_features.pitch_std >= 50 ? '豊か' : item.audio_features.pitch_std >= 25 ? '普通' : '単調'}
                                            </span>
                                          </div>
                                        )}
                                        {item.audio_features.speech_rate != null && (
                                          <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-teal-50 border border-teal-200">
                                            <span className="text-[10px] text-teal-500">話速</span>
                                            <span className="text-[10px] font-medium text-teal-700">
                                              {item.audio_features.speech_rate.toFixed(1)}字/秒
                                            </span>
                                            <span className={`text-[9px] px-1 py-0.5 rounded ${
                                              item.audio_features.speech_rate > 7 ? 'bg-red-100 text-red-600' :
                                              item.audio_features.speech_rate < 3 ? 'bg-blue-100 text-blue-600' :
                                              'bg-green-100 text-green-600'
                                            }`}>
                                              {item.audio_features.speech_rate > 7 ? '速い' :
                                               item.audio_features.speech_rate < 3 ? '遅い' : '適切'}
                                            </span>
                                          </div>
                                        )}
                                        {item.audio_features.silence_ratio != null && (
                                          <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-gray-50 border border-gray-200">
                                            <span className="text-[10px] text-gray-500">沈黙率</span>
                                            <span className="text-[10px] font-medium text-gray-700">
                                              {(item.audio_features.silence_ratio * 100).toFixed(0)}%
                                            </span>
                                          </div>
                                        )}
                                        {item.audio_features.energy_trend && (
                                          <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-amber-50 border border-amber-200">
                                            <span className="text-[10px] text-amber-500">トレンド</span>
                                            <span className="text-[10px] font-medium text-amber-700">
                                              {item.audio_features.energy_trend === 'rising' ? '↗ 上昇' :
                                               item.audio_features.energy_trend === 'falling' ? '↘ 下降' : '→ 安定'}
                                            </span>
                                          </div>
                                        )}
                                      </div>
                                    </div>
                                  </div>
                                )}

                                {/* Phase Rating Section */}
                                <div className="pt-3 mt-3 border-t border-gray-200">
                                  <div className="flex items-start gap-4">
                                    <div className="text-amber-500">
                                      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" stroke="none" className="w-4 h-4 flex-shrink-0">
                                        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
                                      </svg>
                                    </div>
                                    <div className="flex-1 min-w-0">
                                      <div className="text-amber-600 font-medium text-xs mb-2">このフェーズを採点</div>
                                      <div className="flex items-center gap-1">
                                        {[1, 2, 3, 4, 5].map((star) => {
                                          const currentRating = phaseRatings[itemKey]?.rating || 0;
                                          const isSaving = phaseRatings[itemKey]?.saving;
                                          return (
                                            <button
                                              key={star}
                                              onClick={(e) => {
                                                e.stopPropagation();
                                                if (!isSaving) handleRatePhase(itemKey, star);
                                              }}
                                              disabled={isSaving}
                                              className={`p-0.5 transition-all duration-150 ${
                                                isSaving ? 'opacity-50 cursor-not-allowed' : 'hover:scale-110 cursor-pointer'
                                              }`}
                                              title={`${star}点`}
                                            >
                                              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24"
                                                fill={star <= currentRating ? '#f59e0b' : 'none'}
                                                stroke={star <= currentRating ? '#f59e0b' : '#d1d5db'}
                                                strokeWidth="1.5"
                                                className="w-5 h-5"
                                              >
                                                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
                                              </svg>
                                            </button>
                                          );
                                        })}
                                        {phaseRatings[itemKey]?.saving && (
                                          <div className="ml-2 w-3 h-3 rounded-full border-2 border-gray-300 border-t-amber-500 animate-spin" />
                                        )}
                                        {phaseRatings[itemKey]?.saved && !phaseRatings[itemKey]?.saving && (
                                          <span className="ml-2 text-[10px] text-green-500 font-medium">保存済み</span>
                                        )}
                                      </div>
                                      {/* Comment input - shown after rating */}
                                      {phaseRatings[itemKey]?.rating && (
                                        <div className="mt-2 flex items-start gap-2">
                                          <input
                                            type="text"
                                            placeholder="コメント（任意）"
                                            value={ratingComments[itemKey] || ''}
                                            onChange={(e) => {
                                              e.stopPropagation();
                                              setRatingComments(prev => ({ ...prev, [itemKey]: e.target.value }));
                                            }}
                                            onClick={(e) => e.stopPropagation()}
                                            onKeyDown={(e) => {
                                              e.stopPropagation();
                                              if (e.key === 'Enter') handleSaveComment(itemKey);
                                            }}
                                            className="flex-1 text-xs px-3 py-1.5 rounded-lg border border-gray-200 bg-white focus:outline-none focus:ring-1 focus:ring-amber-400 focus:border-amber-400 placeholder-gray-300"
                                          />
                                          <button
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              handleSaveComment(itemKey);
                                            }}
                                            disabled={phaseRatings[itemKey]?.saving}
                                            className="text-xs px-3 py-1.5 rounded-lg bg-amber-500 text-white font-medium hover:bg-amber-600 transition-colors disabled:opacity-50"
                                          >
                                            保存
                                          </button>
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                </div>

                                {/* Sales Psychology Tags - Human-in-the-loop Edit Section */}
                                {item.sales_psychology_tags && item.sales_psychology_tags.length > 0 && (
                                  <div className="pt-3 mt-3 border-t border-gray-200" onClick={(e) => e.stopPropagation()}>
                                    <div className="flex items-start gap-4">
                                      <div className="text-indigo-500">
                                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4 flex-shrink-0">
                                          <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/>
                                        </svg>
                                      </div>
                                      <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2 mb-2">
                                          <span className="text-indigo-600 font-medium text-xs">販売心理タグ</span>
                                          {tagEditState[itemKey]?.saved && !tagEditState[itemKey]?.editing && (
                                            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[8px] font-medium bg-green-100 text-green-600 border border-green-200">
                                              <svg xmlns="http://www.w3.org/2000/svg" width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                                              確認済み
                                            </span>
                                          )}
                                          {humanTags[itemKey] && !tagEditState[itemKey]?.editing && (
                                            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[8px] font-medium bg-blue-100 text-blue-600 border border-blue-200">
                                              人間修正
                                            </span>
                                          )}
                                          {tagEditState[itemKey]?.saving && (
                                            <div className="w-3 h-3 rounded-full border-2 border-gray-300 border-t-indigo-500 animate-spin" />
                                          )}
                                        </div>

                                        {/* Display mode: show tags with correct/edit buttons */}
                                        {!tagEditState[itemKey]?.editing && (
                                          <div>
                                            <div className="flex flex-wrap gap-1 mb-2">
                                              {(humanTags[itemKey] || item.sales_psychology_tags).map((tag) => {
                                                const cfg = SALES_TAG_CONFIG[tag] || { label: tag, color: 'bg-gray-100 text-gray-600 border-gray-300' };
                                                return (
                                                  <span key={tag} className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-semibold border ${cfg.color}`}>
                                                    {cfg.label}
                                                  </span>
                                                );
                                              })}
                                            </div>
                                            {!tagEditState[itemKey]?.saved && !humanTags[itemKey] && (
                                              <div className="flex items-center gap-2">
                                                <button
                                                  onClick={() => handleTagConfirm(itemKey, item.sales_psychology_tags)}
                                                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-medium bg-green-50 text-green-700 border border-green-200 hover:bg-green-100 transition-colors"
                                                >
                                                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                                                  正しい
                                                </button>
                                                <button
                                                  onClick={() => handleTagEditStart(itemKey, item.sales_psychology_tags)}
                                                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 transition-colors"
                                                >
                                                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                                                  修正
                                                </button>
                                              </div>
                                            )}
                                            {(tagEditState[itemKey]?.saved || humanTags[itemKey]) && (
                                              <button
                                                onClick={() => handleTagEditStart(itemKey, humanTags[itemKey] || item.sales_psychology_tags)}
                                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-medium bg-gray-50 text-gray-600 border border-gray-200 hover:bg-gray-100 transition-colors"
                                              >
                                                <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                                                再修正
                                              </button>
                                            )}
                                          </div>
                                        )}

                                        {/* Edit mode: checkbox grid */}
                                        {tagEditState[itemKey]?.editing && (
                                          <div>
                                            <div className="grid grid-cols-3 sm:grid-cols-5 gap-1.5 mb-3">
                                              {ALL_SALES_TAGS.map((tag) => {
                                                const cfg = SALES_TAG_CONFIG[tag];
                                                const isSelected = (humanTags[itemKey] || []).includes(tag);
                                                return (
                                                  <button
                                                    key={tag}
                                                    onClick={() => handleTagToggle(itemKey, tag)}
                                                    className={`flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-[9px] font-semibold border transition-all duration-150 ${
                                                      isSelected
                                                        ? `${cfg.color} ring-2 ring-offset-1 ring-indigo-400`
                                                        : 'bg-gray-50 text-gray-400 border-gray-200 hover:bg-gray-100'
                                                    }`}
                                                  >
                                                    {isSelected && (
                                                      <svg xmlns="http://www.w3.org/2000/svg" width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                                                    )}
                                                    {cfg.label}
                                                  </button>
                                                );
                                              })}
                                            </div>
                                            <div className="flex items-center gap-2">
                                              <button
                                                onClick={() => handleTagSave(itemKey)}
                                                className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[10px] font-medium bg-indigo-600 text-white hover:bg-indigo-700 transition-colors"
                                              >
                                                保存
                                              </button>
                                              <button
                                                onClick={() => handleTagEditCancel(itemKey)}
                                                className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[10px] font-medium bg-gray-100 text-gray-600 border border-gray-200 hover:bg-gray-200 transition-colors"
                                              >
                                                キャンセル
                                              </button>
                                            </div>
                                          </div>
                                        )}
                                      </div>
                                    </div>
                                  </div>
                                )}

                                {/* TikTok Clip Generation Button */}
                                {item.time_start != null && item.time_end != null && (() => {
                                  const clipState = clipStates[itemKey];
                                  const isLoading = clipState?.status === 'requesting' || clipState?.status === 'pending' || clipState?.status === 'processing';
                                  const isGeneratingSubtitles = clipState?.status === 'generating_subtitles';
                                  const isCompleted = clipState?.status === 'completed' && clipState?.clip_url;
                                  const isFailed = clipState?.status === 'failed' || clipState?.status === 'dead';

                                  return (
                                    <div className="flex items-center gap-3 pt-3 mt-3 border-t border-gray-200">
                                      <div className="text-purple-500">
                                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4 flex-shrink-0">
                                          <path d="M12 2v4"/><path d="m15.2 7.6 2.4-2.4"/><path d="M18 12h4"/><path d="m15.2 16.4 2.4 2.4"/><path d="M12 18v4"/><path d="m4.4 19.6 2.4-2.4"/><path d="M2 12h4"/><path d="m4.4 4.4 2.4 2.4"/>
                                        </svg>
                                      </div>
                                      {isCompleted ? (
                                        <a
                                          href={clipState.clip_url}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-purple-500 to-pink-500 text-white text-xs font-medium hover:from-purple-600 hover:to-pink-600 transition-all shadow-sm"
                                          onClick={(e) => e.stopPropagation()}
                                        >
                                          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                                          </svg>
                                          切り抜きをダウンロード
                                        </a>
                                      ) : isGeneratingSubtitles ? (
                                        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-purple-50 text-purple-600 text-xs font-medium border border-purple-200">
                                          <div className="w-3 h-3 rounded-full border-2 border-purple-200 border-t-purple-500 animate-spin" />
                                          字幕を生成中...
                                        </div>
                                      ) : isLoading ? (
                                        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-100 text-gray-500 text-xs font-medium">
                                          <div className="w-3 h-3 rounded-full border-2 border-gray-300 border-t-purple-500 animate-spin" />
                                          切り抜き生成中...
                                        </div>
                                      ) : (
                                        <>
                                          <button
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              handleClipGeneration(item, itemKey);
                                            }}
                                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-purple-500 to-pink-500 text-white text-xs font-medium hover:from-purple-600 hover:to-pink-600 transition-all shadow-sm hover:shadow-md"
                                          >
                                            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                              <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/><line x1="17" y1="17" x2="22" y2="17"/>
                                            </svg>
                                            TikTok切り抜きを生成
                                          </button>
                                          {isFailed && (
                                            <span className="text-red-500 text-xs">生成に失敗しました。再試行してください。</span>
                                          )}
                                        </>
                                      )}
                                    </div>
                                  );
                                })()}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>

                  </div>
                </div>
              )}

            </div>

          </div>
          </SectionErrorBoundary>

          {/* Questions and Answers Section */}
          <SectionErrorBoundary sectionName="質問と回答">
          <div className="space-y-3 pt-4 mt-4 border-t border-gray-200">
            <p className="text-gray-400 text-xs text-center">質問と回答</p>
            <div className="rounded-2xl p-4 max-w-[85%] bg-gray-50 border border-gray-200">
              <p className="text-gray-700 text-sm">
                この動画の解析が完了しました。全体的に良い構成ですが、いくつかの改善点が見つかりました。詳細について質問があれば、お聞きください。
              </p>
            </div>
            {/* Chat Section */}
            {chatMessages && chatMessages.length > 0 && (
              <div className="mt-6 flex flex-col gap-4">
                {chatMessages.map((item) => (
                  <div key={item.id || `${item.question}-${item.created_at || ''}`} className="flex flex-col gap-4">
                    <div className="w-[80%] mx-auto rounded-2xl bg-blue-600 px-6 py-4">
                      <p className="text-white text-sm font-medium">{item.question}</p>
                    </div>
                    {item.answer && (
                      <div className="w-[80%] rounded-2xl p-6 bg-gray-50 border border-gray-200">
                        <div className="text-gray-700 text-sm leading-relaxed">
                          <div className="markdown">
                            <MarkdownWithTables
                              markdown={item.answer || ""}
                              isOldSafariIOS={isOldSafariIOS}
                              keyPrefix={`chat-${item.id || item.created_at || ""}`}
                            />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
                {isThinking && (
                  <div className="w-[80%] rounded-2xl p-4 bg-gray-50 border border-gray-200">
                    <div className="flex items-center gap-3">
                      <div className="w-4 h-4 rounded-full border-2 border-gray-300 border-t-gray-600 animate-spin" />
                      <p className="text-gray-500 text-sm">{window.__t ? window.__t("aiThinking") : "AI is thinking..."}</p>
                    </div>
                  </div>
                )}
                <div ref={chatEndRef} />
              </div>
            )}
          </div>
          </SectionErrorBoundary>
        </div>

        <div className="w-full mx-auto hidden md:block mt-4 pb-4">
          <ChatInput onSend={handleChatSend} disabled={!!streamCancelRef.current} />
        </div>
      </div>

      <SectionErrorBoundary sectionName="動画プレビュー">
      <DockPlayer
        open={!!previewData}
        onClose={handleCloseDockPlayer}
        videoUrl={previewData?.url}
        fullVideoUrl={previewData?.fullVideoUrl}
        timeStart={previewData?.timeStart}
        timeEnd={previewData?.timeEnd}
        isClipPreview={previewData?.isClipPreview}
        reports1={videoData?.reports_1 || []}
        phaseRatings={phaseRatings}
        onRatePhase={handleRatePhase}
        ratingComments={ratingComments}
        onCommentChange={(idx, val) => setRatingComments(prev => ({ ...prev, [idx]: val }))}
        onSaveComment={handleSaveComment}
        onPhaseNavigate={(phase) => {
          // When DockPlayer navigates internally, only update URL params
          // (DockPlayer already handles video.currentTime directly)
          const phaseIdx = phase.phase_index ?? (videoData?.reports_1 || []).findIndex(
            r => r.time_start === phase.time_start && r.time_end === phase.time_end
          );
          if (phaseIdx >= 0) {
            setSearchParams(prev => {
              const next = new URLSearchParams(prev);
              next.set('view', 'timeline');
              next.set('phase', String(phaseIdx));
              return next;
            }, { replace: true });
          }
        }}
        humanTags={humanTags}
        tagEditState={tagEditState}
        onTagConfirm={handleTagConfirm}
        onTagEditStart={handleTagEditStart}
        onTagToggle={handleTagToggle}
        onTagSave={handleTagSave}
        onTagEditCancel={(phaseIndex) => setTagEditState(prev => ({ ...prev, [phaseIndex]: { ...prev[phaseIndex], editing: false } }))}
        clipStates={clipStates}
        onClipGenerate={handleClipGeneration}
        videoData={videoData}
        salesMoments={salesMoments}
        eventScores={eventScores}
      />
      </SectionErrorBoundary>
    </div>
  );
}
// deploy trigger 1772731801
