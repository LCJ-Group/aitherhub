import { useState, useEffect, useRef, useCallback, useMemo } from "react";

/**
 * DockPlayer – Sales Intelligence Player (方法2: LCJ分析ツール型)
 *
 * フルスクリーン分析モード:
 *   左: 9:16動画を最大化（黒余白ゼロ）
 *   右: 区間データパネル（売上/注文/視聴者/いいね/商品/Tags/AI分析/改善提案/★採点）
 *   下: コンパクト操作バー
 *   速度表示: 動画上にYouTube型オーバーレイ
 */

// ── Phase Behavior Tag Config (常時展開・1タップ選択・即保存) ──────
const PHASE_TAG_CONFIG = {
  HOOK: { label: "つかみ", color: "bg-purple-100 text-purple-700 border-purple-300" },
  CHAT: { label: "雑談", color: "bg-pink-100 text-pink-700 border-pink-300" },
  PREP: { label: "準備", color: "bg-blue-100 text-blue-700 border-blue-300" },
  PHONE_OP: { label: "スマホ操作", color: "bg-cyan-100 text-cyan-700 border-cyan-300" },
  LONG_GREET: { label: "挨拶長い", color: "bg-amber-100 text-amber-700 border-amber-300" },
  COMMENT_READ: { label: "コメント読み", color: "bg-green-100 text-green-700 border-green-300" },
  SILENCE: { label: "沈黙", color: "bg-gray-100 text-gray-700 border-gray-300" },
  PRICE_SHOW: { label: "価格提示", color: "bg-red-100 text-red-700 border-red-300" },
};

// Sales Psychology Tag Config (常時展開・1タップ選択・即保存)
const SALES_TAG_CONFIG = {
  HOOK: { label: "HOOK", color: "bg-purple-100 text-purple-700 border-purple-300" },
  EMPATHY: { label: "共感", color: "bg-pink-100 text-pink-700 border-pink-300" },
  PROBLEM: { label: "問題", color: "bg-red-50 text-red-600 border-red-200" },
  EDUCATION: { label: "教育", color: "bg-blue-100 text-blue-700 border-blue-300" },
  SOLUTION: { label: "解決", color: "bg-green-100 text-green-700 border-green-300" },
  DEMONSTRATION: { label: "デモ", color: "bg-teal-100 text-teal-700 border-teal-300" },
  COMPARISON: { label: "比較", color: "bg-indigo-100 text-indigo-700 border-indigo-300" },
  PROOF: { label: "証拠", color: "bg-cyan-100 text-cyan-700 border-cyan-300" },
  TRUST: { label: "信頼", color: "bg-emerald-100 text-emerald-700 border-emerald-300" },
  SOCIAL_PROOF: { label: "社会証明", color: "bg-violet-100 text-violet-700 border-violet-300" },
  OBJECTION_HANDLING: { label: "反論処理", color: "bg-amber-100 text-amber-700 border-amber-300" },
  URGENCY: { label: "緊急", color: "bg-orange-100 text-orange-700 border-orange-300" },
  LIMITED_OFFER: { label: "限定", color: "bg-rose-100 text-rose-700 border-rose-300" },
  BONUS: { label: "特典", color: "bg-lime-100 text-lime-700 border-lime-300" },
  CTA: { label: "CTA", color: "bg-red-100 text-red-700 border-red-300" },
};

function formatTime(seconds) {
  if (seconds == null || isNaN(seconds)) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

// Behavior tags list
const ALL_PHASE_TAGS = [
  'HOOK', 'CHAT', 'PREP', 'PHONE_OP', 'LONG_GREET',
  'COMMENT_READ', 'SILENCE', 'PRICE_SHOW',
];

// Sales psychology tags list
const ALL_SALES_TAGS = [
  'EMPATHY', 'PROBLEM', 'EDUCATION', 'SOLUTION',
  'DEMONSTRATION', 'COMPARISON', 'PROOF', 'TRUST', 'SOCIAL_PROOF',
  'OBJECTION_HANDLING', 'URGENCY', 'LIMITED_OFFER', 'BONUS', 'CTA',
];

// Dark-mode tag colors for DockPlayer
const DARK_TAG_COLORS = {
  // Phase behavior tags
  HOOK: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  CHAT: "bg-pink-500/20 text-pink-300 border-pink-500/30",
  PREP: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  PHONE_OP: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
  LONG_GREET: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  COMMENT_READ: "bg-green-500/20 text-green-300 border-green-500/30",
  SILENCE: "bg-gray-500/20 text-gray-300 border-gray-500/30",
  PRICE_SHOW: "bg-red-500/20 text-red-300 border-red-500/30",
  // Sales psychology tags
  EMPATHY: "bg-pink-500/20 text-pink-300 border-pink-500/30",
  PROBLEM: "bg-red-500/20 text-red-300 border-red-500/30",
  EDUCATION: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  SOLUTION: "bg-green-500/20 text-green-300 border-green-500/30",
  DEMONSTRATION: "bg-teal-500/20 text-teal-300 border-teal-500/30",
  COMPARISON: "bg-indigo-500/20 text-indigo-300 border-indigo-500/30",
  PROOF: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
  TRUST: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  SOCIAL_PROOF: "bg-violet-500/20 text-violet-300 border-violet-500/30",
  OBJECTION_HANDLING: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  URGENCY: "bg-orange-500/20 text-orange-300 border-orange-500/30",
  LIMITED_OFFER: "bg-rose-500/20 text-rose-300 border-rose-500/30",
  BONUS: "bg-lime-500/20 text-lime-300 border-lime-500/30",
  CTA: "bg-red-500/20 text-red-300 border-red-500/30",
};

export default function DockPlayer({
  open,
  onClose,
  videoUrl,
  fullVideoUrl,
  timeStart = 0,
  timeEnd = null,
  isClipPreview = false,
  reports1 = [],
  phaseRatings = {},
  onRatePhase,
  ratingComments = {},
  onCommentChange,
  onSaveComment,
  onPhaseNavigate,
  // New props for tags, clips
  humanTags = {},
  tagEditState = {},
  onTagConfirm,
  onTagEditStart,
  onTagToggle,
  onTagSave,
  onTagEditCancel,
  clipStates = {},
  onClipGenerate,
  videoData,
  salesMoments = [],
  eventScores = [],
}) {
  const videoRef = useRef(null);
  const hasSetupRef = useRef(false);
  const prevVideoUrlRef = useRef(null);
  const prevTimeStartRef = useRef(null);
  const navLockRef = useRef(false);  // Lock to prevent timeupdate from overriding navigatePhase
  const navTokenRef = useRef(0);     // Token to handle rapid navigation (only latest wins)

  // ── Active video source management ──────────────────────────
  // Start with clip URL (fast), switch to full video on navigation
  const [activeVideoUrl, setActiveVideoUrl] = useState(videoUrl);
  const [usingFullVideo, setUsingFullVideo] = useState(!isClipPreview);
  const [navDisabled, setNavDisabled] = useState(false); // Temporary disable during video switch

  // Sync activeVideoUrl when parent changes videoUrl (new phase opened from outside)
  // BUT: if we already switched to full video, don't revert to clip URL
  useEffect(() => {
    setUsingFullVideo((prev) => {
      if (prev) {
        // Already on full video — keep it, don't revert to clip
        // Only update activeVideoUrl if fullVideoUrl changed
        return true;
      }
      // Not yet on full video — follow parent's URL
      setActiveVideoUrl(videoUrl);
      return !isClipPreview;
    });
  }, [videoUrl, isClipPreview]);

  const [isLoading, setIsLoading] = useState(true);
  const [showCustomLoading, setShowCustomLoading] = useState(true);
  const [isBuffering, setIsBuffering] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1.5);
  const [currentPhaseIndex, setCurrentPhaseIndex] = useState(-1);
  const [isMinimized, setIsMinimized] = useState(false);
  const [showSpeedOverlay, setShowSpeedOverlay] = useState(false);
  const speedOverlayTimer = useRef(null);

  // ── Phase Lock Mode ──────────────────────────────────────
  // 'phase' = フェーズロック再生（TikTok型自動ループ）
  // 'full'  = 自由再生（フェーズロック解除）
  const [playMode, setPlayMode] = useState('phase');
  const [loopFade, setLoopFade] = useState(false); // ループ暗転演出

  // Combined tags: { phaseKey: ['HOOK', 'CHAT', 'EMPATHY', ...] }
  const [selectedPhaseTags, setSelectedPhaseTags] = useState({});
  const [phaseTagsSaving, setPhaseTagsSaving] = useState({});
  const [phaseTagsSaved, setPhaseTagsSaved] = useState({});
  // Debounce timer for auto-save
  const saveTimerRef = useRef({});

  // ── Reviewer name (stored in localStorage per browser) ──────
  const [reviewerName, setReviewerName] = useState(() => localStorage.getItem('aitherhub_reviewer_name') || '');
  const [isEditingReviewer, setIsEditingReviewer] = useState(false);
  const [reviewerInput, setReviewerInput] = useState('');

  // ── DockPlayer internal toast ──────────────────────────────
  const [dockToast, setDockToast] = useState(null);
  const dockToastTimer = useRef(null);
  const showDockToast = useCallback((message, type = 'success') => {
    if (dockToastTimer.current) clearTimeout(dockToastTimer.current);
    setDockToast({ message, type });
    dockToastTimer.current = setTimeout(() => setDockToast(null), 2000);
  }, []);

  // ── Robust seekTo helper (handles readyState, retries) ──────
  const seekTo = useCallback((t) => {
    const vid = videoRef.current;
    if (!vid) return;

    const doSeek = () => {
      try {
        vid.currentTime = t;
        vid.playbackRate = playbackRate;
        if (vid.paused) vid.play().catch(() => {});
      } catch (e) {
        console.warn('seekTo failed:', e);
      }
    };

    if (vid.readyState >= 1) {
      doSeek();
    } else {
      // Wait for metadata to load before seeking
      const onMeta = () => {
        vid.removeEventListener('loadedmetadata', onMeta);
        doSeek();
      };
      vid.addEventListener('loadedmetadata', onMeta);
      // Fallback: retry after 300ms in case event doesn't fire
      setTimeout(doSeek, 300);
    }
  }, [playbackRate]);

  // ── Find current phase based on video currentTime ─────────
  const findPhaseIndex = useCallback(
    (time) => {
      if (!reports1 || reports1.length === 0) return -1;
      for (let i = 0; i < reports1.length; i++) {
        const p = reports1[i];
        const start = Number(p.time_start) || 0;
        const end = p.time_end != null ? Number(p.time_end) : Infinity;
        if (time >= start && time <= end) return i;
      }
      return -1;
    },
    [reports1]
  );

  // ── Current phase object ──────────────────────────────────
  const currentPhase = useMemo(() => {
    if (currentPhaseIndex >= 0 && currentPhaseIndex < reports1.length) {
      return reports1[currentPhaseIndex];
    }
    return null;
  }, [currentPhaseIndex, reports1]);

  const phaseKey = currentPhase?.phase_index ?? currentPhaseIndex;

  // ── Initialize tags from existing human_sales_tags when phase changes ──
  useEffect(() => {
    if (phaseKey < 0 || selectedPhaseTags[phaseKey] !== undefined) return;
    // Load from existing human_sales_tags or AI tags
    const existingHuman = humanTags[phaseKey];
    if (existingHuman && existingHuman.length > 0) {
      setSelectedPhaseTags(prev => ({ ...prev, [phaseKey]: [...existingHuman] }));
      setPhaseTagsSaved(prev => ({ ...prev, [phaseKey]: true }));
    }
  }, [phaseKey, humanTags]);

  // ── Reset on close ────────────────────────────────────────
  useEffect(() => {
    if (!open) {
      hasSetupRef.current = false;
      setIsLoading(true);
      setShowCustomLoading(true);
      setIsBuffering(false);
      setCurrentPhaseIndex(-1);
      setNavDisabled(false);
      navLockRef.current = false;
      navTokenRef.current = 0;
      setPlayMode('phase');
      setLoopFade(false);
      // Reset video source to prop values for next open
      setActiveVideoUrl(videoUrl);
      setUsingFullVideo(!isClipPreview);
    }
  }, [open]);

  // ── Setup seek/play when URL or timeStart changes ─────────
  useEffect(() => {
    if (!open || !activeVideoUrl) return;
    const vid = videoRef.current;
    if (!vid) return;

    const urlChanged = activeVideoUrl !== prevVideoUrlRef.current;
    const timeChanged = timeStart !== prevTimeStartRef.current;

    prevVideoUrlRef.current = activeVideoUrl;
    prevTimeStartRef.current = timeStart;

    // If navigatePhase is handling the seek, skip this effect
    if (navLockRef.current) return;

    if (!urlChanged && timeChanged) {
      // Same video, different time → just seek
      seekTo(timeStart);
      setCurrentPhaseIndex(findPhaseIndex(timeStart));
      return;
    }

    if (!urlChanged && !timeChanged && hasSetupRef.current) return;

    hasSetupRef.current = true;
    setIsLoading(true);
    setShowCustomLoading(true);

    const seekAndPlay = async () => {
      try {
        vid.defaultMuted = false;
        vid.muted = false;
        vid.playbackRate = playbackRate;

        // For clip preview (initial load), start from 0
        // For full video, seek to timeStart
        if (usingFullVideo && timeStart > 0) {
          seekTo(timeStart);
        }
        setCurrentPhaseIndex(findPhaseIndex(timeStart));

        await new Promise((resolve) => {
          const timeout = setTimeout(resolve, 5000);
          const check = () => {
            if (!vid || vid.readyState >= 4) {
              clearTimeout(timeout);
              resolve();
              return;
            }
            setTimeout(check, 200);
          };
          check();
        });

        try {
          vid.muted = false;
          await vid.play();
        } catch {
          try {
            vid.muted = true;
            await vid.play();
          } catch {
            // silent
          }
        }

        setIsLoading(false);
        setIsBuffering(false);
        setTimeout(() => setShowCustomLoading(false), 300);
      } catch (e) {
        console.error("DockPlayer seek error:", e);
        setIsLoading(false);
        setShowCustomLoading(false);
      }
    };

    const handleCanPlay = () => {
      seekAndPlay();
      vid.removeEventListener("canplay", handleCanPlay);
    };

    if (vid.readyState >= 3) {
      seekAndPlay();
    } else {
      vid.addEventListener("canplay", handleCanPlay);
    }

    return () => {
      vid.removeEventListener("canplay", handleCanPlay);
    };
  }, [activeVideoUrl, open, timeStart, usingFullVideo, findPhaseIndex, seekTo]);

  // ── Timeupdate: track current phase + Phase Lock ───────────
  useEffect(() => {
    if (!open) return;
    const vid = videoRef.current;
    if (!vid) return;

    const onTimeUpdate = () => {
      // Skip timeupdate if navigatePhase just fired (prevents race condition)
      if (navLockRef.current) return;

      // For clip preview: video.currentTime is relative (0-based),
      // but phases use absolute time, so add timeStart as offset
      const absoluteTime = usingFullVideo ? vid.currentTime : vid.currentTime + timeStart;
      const idx = findPhaseIndex(absoluteTime);
      setCurrentPhaseIndex((prev) => (idx !== prev ? idx : prev));

      // ── Phase Lock: auto-loop within phase boundaries ──
      if (playMode === 'phase' && idx >= 0 && idx < reports1.length) {
        const phase = reports1[idx];
        const phaseEnd = Number(phase.time_end) || Infinity;
        const phaseStart = Number(phase.time_start) || 0;

        if (absoluteTime >= phaseEnd - 0.05) {
          // Loop back to phase start with brief fade
          setLoopFade(true);
          setTimeout(() => setLoopFade(false), 150);

          if (usingFullVideo) {
            vid.currentTime = phaseStart;
          } else {
            vid.currentTime = 0; // clip starts at 0
          }
          if (vid.paused) vid.play().catch(() => {});
        }
      }
    };

    // ── Seeking control: clamp to phase boundaries in Phase Mode ──
    const onSeeking = () => {
      if (playMode !== 'phase' || navLockRef.current) return;
      if (currentPhaseIndex < 0 || currentPhaseIndex >= reports1.length) return;

      const phase = reports1[currentPhaseIndex];
      const phaseStart = Number(phase.time_start) || 0;
      const phaseEnd = Number(phase.time_end) || Infinity;
      const baseOffset = usingFullVideo ? 0 : timeStart;

      const target = vid.currentTime + baseOffset;
      if (target < phaseStart || target > phaseEnd) {
        const clamped = Math.max(phaseStart, Math.min(phaseEnd, target));
        vid.currentTime = clamped - baseOffset;
      }
    };

    vid.addEventListener('timeupdate', onTimeUpdate);
    vid.addEventListener('seeking', onSeeking);
    return () => {
      vid.removeEventListener('timeupdate', onTimeUpdate);
      vid.removeEventListener('seeking', onSeeking);
    };
  }, [open, findPhaseIndex, usingFullVideo, timeStart, playMode, reports1, currentPhaseIndex]);

  // ── Playback rate change with overlay ─────────────────────
  const handleSpeedChange = useCallback(
    (rate) => {
      setPlaybackRate(rate);
      if (videoRef.current) {
        videoRef.current.playbackRate = rate;
      }
      // Show speed overlay on video
      setShowSpeedOverlay(true);
      if (speedOverlayTimer.current) clearTimeout(speedOverlayTimer.current);
      speedOverlayTimer.current = setTimeout(() => setShowSpeedOverlay(false), 800);
    },
    []
  );

  // ── Keyboard shortcuts ────────────────────────────────────
  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

      switch (e.key) {
        case "1":
          handleSpeedChange(1);
          break;
        case "2":
          handleSpeedChange(1.5);
          break;
        case "3":
          handleSpeedChange(2);
          break;
        case "ArrowLeft":
          if (e.shiftKey) navigatePhase(-1);
          break;
        case "ArrowRight":
          if (e.shiftKey) navigatePhase(1);
          break;
        case "Escape":
          onClose?.();
          break;
        case "p":
        case "P":
          setPlayMode('phase');
          break;
        case "f":
        case "F":
          setPlayMode('full');
          break;
        default:
          break;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, handleSpeedChange, currentPhaseIndex, reports1]);

  // ── Navigate to prev/next phase ───────────────────────────
  // On navigation: always switch to full video, seek to target phase start_sec
  const navigatePhase = useCallback(
    (direction) => {
      if (!reports1 || reports1.length === 0) return;
      if (navDisabled) return; // Prevent rapid clicks during video switch

      let targetIdx = currentPhaseIndex + direction;
      if (targetIdx < 0) targetIdx = 0;
      if (targetIdx >= reports1.length) targetIdx = reports1.length - 1;
      if (targetIdx === currentPhaseIndex) return;

      const targetPhase = reports1[targetIdx];
      const targetTime = Number(targetPhase.time_start) || 0;

      // Increment token so only the latest navigation wins
      const token = ++navTokenRef.current;

      // Lock timeupdate to prevent it from overriding our index
      navLockRef.current = true;
      setCurrentPhaseIndex(targetIdx);

      // Determine if we need to switch from clip to full video
      const needsSwitch = !usingFullVideo && fullVideoUrl && fullVideoUrl !== activeVideoUrl;

      if (needsSwitch) {
        // Switch to full video: disable nav buttons temporarily
        setNavDisabled(true);
        setActiveVideoUrl(fullVideoUrl);
        setUsingFullVideo(true);

        // After video element loads new src, seek to target
        // We use a short timeout + loadedmetadata listener approach
        const waitAndSeek = () => {
          const vid = videoRef.current;
          if (!vid) return;

          const doSeek = () => {
            if (token !== navTokenRef.current) return;
            try {
              vid.currentTime = targetTime;
              vid.playbackRate = playbackRate;
              if (vid.paused) vid.play().catch(() => {});
            } catch (e) {
              console.warn('navigatePhase switch seek failed:', e);
            }
            setNavDisabled(false);
            navLockRef.current = false;
          };

          if (vid.readyState >= 1) {
            doSeek();
          } else {
            const onMeta = () => {
              vid.removeEventListener('loadedmetadata', onMeta);
              doSeek();
            };
            vid.addEventListener('loadedmetadata', onMeta);
            // Fallback timeout
            setTimeout(() => {
              vid.removeEventListener('loadedmetadata', onMeta);
              doSeek();
            }, 2000);
          }
        };

        // Wait for React to update the <video src> via state change
        requestAnimationFrame(() => {
          requestAnimationFrame(waitAndSeek);
        });
      } else {
        // Already on full video: just seek directly
        const vid = videoRef.current;
        if (vid) {
          requestAnimationFrame(() => {
            if (token !== navTokenRef.current) return;
            try {
              vid.currentTime = targetTime;
              vid.playbackRate = playbackRate;
              if (vid.paused) vid.play().catch(() => {});
            } catch (e) {
              console.warn('navigatePhase seek failed:', e);
            }
            setTimeout(() => { navLockRef.current = false; }, 300);
          });
        } else {
          navLockRef.current = false;
        }
      }

      // Notify parent for URL params sync
      if (onPhaseNavigate) {
        onPhaseNavigate(targetPhase);
      }
    },
    [currentPhaseIndex, reports1, onPhaseNavigate, navDisabled, usingFullVideo, fullVideoUrl, activeVideoUrl, playbackRate]
  );

  // ── Auto-save function (debounced) ──────────────────────────
  const autoSaveTags = useCallback((pk, newTags) => {
    // Clear previous debounce timer for this phase
    if (saveTimerRef.current[pk]) {
      clearTimeout(saveTimerRef.current[pk]);
    }
    // Set saving indicator immediately
    setPhaseTagsSaving(prev => ({ ...prev, [pk]: true }));
    setPhaseTagsSaved(prev => ({ ...prev, [pk]: false }));

    // Debounce: save after 500ms of no further changes
    saveTimerRef.current[pk] = setTimeout(async () => {
      try {
        if (onTagConfirm) {
          await onTagConfirm(pk, newTags);
        }
        setPhaseTagsSaved(prev => ({ ...prev, [pk]: true }));
        showDockToast('保存しました');
      } catch (e) {
        console.error('Failed to auto-save tags:', e);
        showDockToast('保存に失敗しました', 'error');
      } finally {
        setPhaseTagsSaving(prev => ({ ...prev, [pk]: false }));
      }
    }, 500);
  }, [onTagConfirm, showDockToast]);

  // ── Tag toggle (1-tap select/deselect → auto-save) ──────────
  const togglePhaseTag = useCallback((pk, tag) => {
    setSelectedPhaseTags(prev => {
      const current = prev[pk] || [];
      const isSelected = current.includes(tag);
      const newTags = isSelected ? current.filter(t => t !== tag) : [...current, tag];
      // Trigger auto-save with new tags
      autoSaveTags(pk, newTags);
      return {
        ...prev,
        [pk]: newTags,
      };
    });
  }, [autoSaveTags]);

  // ── Buffering handlers ────────────────────────────────
  const handleWaiting = useCallback(() => setIsBuffering(true), []);
  const handlePlaying = useCallback(() => {
    setIsBuffering(false);
    setIsLoading(false);
    setShowCustomLoading(false);
  }, []);

  // ── Don't render if not open ──────────────────────────────
  if (!open) return null;

  // ── Compute phase data for right panel ────────────────────
  const csv = currentPhase?.csv_metrics;
  const hasMetrics = csv && (csv.gmv > 0 || csv.order_count > 0 || csv.viewer_count > 0 || csv.like_count > 0);
  const tags = currentPhase?.sales_psychology_tags || [];
  const productNames = csv?.product_names || [];
  const phaseDesc = currentPhase?.phase_description || "";
  const ctaScore = currentPhase?.cta_score;
  const currentRating = phaseRatings[phaseKey]?.rating || 0;
  const isSavingRating = phaseRatings[phaseKey]?.saving;

  // ── Minimized bar ─────────────────────────────────────────
  if (isMinimized) {
    return (
      <div className="fixed bottom-0 left-0 right-0 z-50" style={{ boxShadow: "0 -2px 20px rgba(0,0,0,0.4)" }}>
        <div
          className="bg-gray-950 text-white flex items-center h-12 px-4 gap-3 cursor-pointer hover:bg-gray-900 transition-colors rounded-t-xl"
          onClick={() => setIsMinimized(false)}
        >
          <div className="w-8 h-8 rounded-lg overflow-hidden bg-gray-800 flex-shrink-0">
            <video ref={videoRef} src={activeVideoUrl} className="w-full h-full object-cover" muted />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs text-white/80 truncate">
              {currentPhase
                ? `${formatTime(currentPhase.time_start)} – ${formatTime(currentPhase.time_end)}`
                : "再生中..."}
            </div>
          </div>
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-white/10 text-white/70">{playbackRate}x</span>
          <button
            onClick={(e) => { e.stopPropagation(); onClose?.(); }}
            className="p-1 rounded-full hover:bg-white/10 transition-colors"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
    );
  }

  // ── Full Analysis Mode ────────────────────────────────────
  return (
    <div className="fixed inset-0 z-50 bg-gray-950 flex flex-col">
      {/* ─── Toast Notification ────────────────────────────── */}
      {dockToast && (
        <div
          className={`fixed top-4 left-1/2 -translate-x-1/2 z-[9999] px-4 py-2 rounded-lg text-sm font-medium shadow-lg transition-all duration-300 ${
            dockToast.type === 'error'
              ? 'bg-red-500/90 text-white'
              : 'bg-emerald-500/90 text-white'
          }`}
          style={{ animation: 'fadeInDown 0.3s ease-out' }}
        >
          {dockToast.type !== 'error' && (
            <span className="mr-1.5">✓</span>
          )}
          {dockToast.message}
        </div>
      )}
      {/* ─── Top Bar ────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/10 flex-shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setIsMinimized(true)}
            className="p-1.5 rounded-lg hover:bg-white/10 transition-colors"
            title="最小化"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
          <span className="text-[11px] text-white/40 uppercase tracking-widest font-medium">Sales Intelligence Player</span>
        </div>
        <div className="flex items-center gap-2">
          {/* Phase counter */}
          <span className="text-[11px] text-white/40">
            {currentPhaseIndex >= 0 ? `${currentPhaseIndex + 1} / ${reports1.length}` : `– / ${reports1.length}`}
          </span>
          <button
            onClick={(e) => { e.stopPropagation(); e.preventDefault(); onClose?.(); }}
            className="flex items-center justify-center rounded-lg hover:bg-white/10 transition-colors"
            style={{ position: 'relative', zIndex: 9999, width: 44, height: 44, minWidth: 44, pointerEvents: 'auto' }}
            title="閉じる (Esc)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      {/* ─── Main Content: Video (left) + Analysis (right) ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* LEFT: Video — 9:16 maximized, centered */}
        <div className="relative bg-black flex items-center justify-center" style={{ width: "45%" }}>
          {activeVideoUrl ? (
            <>
              <video
                ref={videoRef}
                src={activeVideoUrl}
                controls
                autoPlay
                playsInline
                preload="auto"
                className="h-full object-contain"
                style={{
                  maxHeight: "calc(100vh - 100px)",
                  maxWidth: "100%",
                  aspectRatio: "9/16",
                }}
                onWaiting={handleWaiting}
                onPlaying={handlePlaying}
                onError={(e) => console.error("DockPlayer video error:", e)}
              />

              {/* Speed overlay on video (YouTube style) */}
              {showSpeedOverlay && (
                <div className="absolute top-4 left-1/2 -translate-x-1/2 pointer-events-none z-10 animate-fade-in">
                  <div className="bg-black/70 backdrop-blur-sm text-white text-lg font-bold px-4 py-2 rounded-xl">
                    {playbackRate}x
                  </div>
                </div>
              )}

              {/* Loading overlay */}
              {isLoading && showCustomLoading && (
                <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
                  <div className="flex flex-col items-center gap-2">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                    <p className="text-white/70 text-xs">動画を準備中...</p>
                  </div>
                </div>
              )}

              {/* Buffering overlay */}
              {isBuffering && !isLoading && (
                <div className="absolute inset-0 bg-black/30 flex items-center justify-center pointer-events-none">
                  <div className="animate-spin rounded-full h-10 w-10 border-2 border-white/30 border-t-white" />
                </div>
              )}

              {/* Loop fade overlay (TikTok-style brief blackout) */}
              {loopFade && (
                <div className="absolute inset-0 bg-black pointer-events-none z-30" style={{ opacity: 0.7, transition: 'opacity 0.1s ease-out' }} />
              )}

              {/* Phase Lock indicator overlay */}
              {currentPhase && (
                <div className="absolute top-3 left-3 z-20 pointer-events-none">
                  <div className="flex items-center gap-2 bg-black/70 backdrop-blur-sm rounded-lg px-3 py-1.5">
                    <span className="text-white/90 text-xs font-bold">
                      {formatTime(currentPhase.time_start)} – {formatTime(currentPhase.time_end)}
                    </span>
                    <span className="text-white/50 text-[10px]">
                      Phase {currentPhaseIndex + 1} / {reports1.length}
                    </span>
                    {playMode === 'phase' && (
                      <span className="text-amber-400 text-[9px] font-semibold uppercase">LOOP</span>
                    )}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="w-full h-full flex items-center justify-center text-white/50 text-sm">
              動画を読み込み中...
            </div>
          )}

          {/* ─── Sales Moment Timeline Markers ──────────────── */}
          {salesMoments.length > 0 && (
            <div className="absolute bottom-0 left-0 right-0 z-20">
              {/* Label */}
              <div className="flex items-center gap-2 px-2 py-0.5 bg-black/60 backdrop-blur-sm">
                <span className="text-[9px] text-white/40 uppercase tracking-wider font-medium">Sales Moments</span>
                <div className="flex items-center gap-2 ml-auto">
                  <span className="flex items-center gap-1 text-[8px] text-white/30">
                    <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>Strong
                  </span>
                  <span className="flex items-center gap-1 text-[8px] text-white/30">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>Click
                  </span>
                  <span className="flex items-center gap-1 text-[8px] text-white/30">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500"></span>Order
                  </span>
                </div>
              </div>
              {/* Timeline bar */}
              <div className="relative h-6 bg-black/80 cursor-pointer group"
                onClick={(e) => {
                  const vid = videoRef.current;
                  if (!vid || !vid.duration) return;
                  const rect = e.currentTarget.getBoundingClientRect();
                  const pct = (e.clientX - rect.left) / rect.width;
                  vid.currentTime = pct * vid.duration;
                  if (vid.paused) vid.play().catch(() => {});
                }}
              >
                {/* Background track */}
                <div className="absolute inset-x-1 top-1/2 -translate-y-1/2 h-1 bg-white/10 rounded-full" />
                {/* Moment markers */}
                {salesMoments.map((m, i) => {
                  const vid = videoRef.current;
                  const dur = vid?.duration || 1;
                  const pct = Math.min(100, Math.max(0, (m.video_sec / dur) * 100));
                  const isStrong = m.moment_type === 'strong';
                  const isClick = m.moment_type === 'click_spike';
                  const color = isStrong ? 'bg-red-500' : isClick ? 'bg-blue-500' : 'bg-green-500';
                  const ringColor = isStrong ? 'ring-red-400/50' : isClick ? 'ring-blue-400/50' : 'ring-green-400/50';
                  const size = isStrong ? 'w-3 h-3' : 'w-2 h-2';
                  return (
                    <button
                      key={`sm-${i}`}
                      className={`absolute top-1/2 -translate-y-1/2 -translate-x-1/2 ${size} ${color} rounded-full ring-2 ${ringColor} hover:scale-150 transition-all duration-150 z-10 group/marker`}
                      style={{ left: `${pct}%` }}
                      title={`${m.moment_type} @ ${formatTime(m.video_sec)}\nClick: ${m.click_value ?? 0} | Order: ${m.order_value ?? 0}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        const v = videoRef.current;
                        if (v) {
                          v.currentTime = m.video_sec;
                          if (v.paused) v.play().catch(() => {});
                        }
                      }}
                    />
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* RIGHT: Analysis Panel */}
        <div className="flex-1 flex flex-col min-h-0 border-l border-white/10" style={{ width: "55%" }}>
          {/* Scrollable analysis content */}
          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            {/* ── Phase Badge ─────────────────────────────── */}
            {currentPhase && (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold bg-orange-500/20 text-orange-300 border border-orange-500/30">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
                    </svg>
                    {formatTime(currentPhase.time_start)} – {formatTime(currentPhase.time_end)}
                  </span>
                  {ctaScore != null && ctaScore >= 3 && (
                    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-bold border ${
                      ctaScore >= 5 ? "bg-red-500/20 text-red-300 border-red-500/30"
                      : ctaScore >= 4 ? "bg-orange-500/20 text-orange-300 border-orange-500/30"
                      : "bg-yellow-500/20 text-yellow-300 border-yellow-500/30"
                    }`}>
                      CTA {ctaScore}
                    </span>
                  )}
                  {/* AI Score Badge - 3 scores */}
                  {(() => {
                    const scoreData = eventScores.find(s => s.phase_index === currentPhase?.phase_index);
                    if (!scoreData) return null;
                    const combined = scoreData.score_combined ?? scoreData.ai_score;
                    if (combined == null) return null;
                    const rank = scoreData.rank;
                    const pct = Math.round(combined * 100);
                    const isHot = combined >= 0.7;
                    const isWarm = combined >= 0.4;
                    return (
                      <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-bold border ${
                        isHot ? "bg-gradient-to-r from-red-500/20 to-orange-500/20 text-orange-200 border-orange-500/40 animate-pulse"
                        : isWarm ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
                        : "bg-gray-500/15 text-gray-400 border-gray-500/30"
                      }`}>
                        {isHot && (
                          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M12 23c-3.866 0-7-3.134-7-7 0-3.866 3.134-7 7-7s7 3.134 7 7c0 3.866-3.134 7-7 7zm0-12c-2.761 0-5 2.239-5 5s2.239 5 5 5 5-2.239 5-5-2.239-5-5-5zm0-2c1.657 0 3-1.343 3-3s-1.343-3-3-3-3 1.343-3 3 1.343 3 3 3z" />
                          </svg>
                        )}
                        AI {pct}%{rank && rank <= 3 ? ` #${rank}` : ""}
                      </span>
                    );
                  })()}
                </div>

                {/* AI-detected tags (read-only inline display) */}
                {tags.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {tags.map((tag) => {
                      const cfg = SALES_TAG_CONFIG[tag] || { label: tag, color: "bg-gray-700 text-gray-300 border-gray-600" };
                      return (
                        <span key={tag} className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold border ${cfg.color}`}>
                          {cfg.label}
                        </span>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* ── Metrics Cards ────────────────────────────── */}
            {hasMetrics && (
              <div className="grid grid-cols-2 xl:grid-cols-4 gap-2">
                {csv.gmv > 0 && (
                  <div className="rounded-xl bg-yellow-500/10 border border-yellow-500/20 px-4 py-3">
                    <div className="text-[10px] text-yellow-400/70 mb-0.5">売上</div>
                    <div className="text-base font-bold text-yellow-300">{"\u00A5"}{Math.round(csv.gmv).toLocaleString()}</div>
                  </div>
                )}
                {csv.order_count > 0 && (
                  <div className="rounded-xl bg-green-500/10 border border-green-500/20 px-4 py-3">
                    <div className="text-[10px] text-green-400/70 mb-0.5">注文</div>
                    <div className="text-base font-bold text-green-300">{csv.order_count}件</div>
                  </div>
                )}
                {csv.viewer_count > 0 && (
                  <div className="rounded-xl bg-blue-500/10 border border-blue-500/20 px-4 py-3">
                    <div className="text-[10px] text-blue-400/70 mb-0.5">視聴者</div>
                    <div className="text-base font-bold text-blue-300">{csv.viewer_count.toLocaleString()}</div>
                  </div>
                )}
                {csv.like_count > 0 && (
                  <div className="rounded-xl bg-pink-500/10 border border-pink-500/20 px-4 py-3">
                    <div className="text-[10px] text-pink-400/70 mb-0.5">いいね</div>
                    <div className="text-base font-bold text-pink-300">{csv.like_count.toLocaleString()}</div>
                  </div>
                )}
              </div>
            )}

            {/* ── Sales Moments in Current Phase ─────────── */}
            {(() => {
              if (!currentPhase || salesMoments.length === 0) return null;
              const phaseStart = Number(currentPhase.time_start) || 0;
              const phaseEnd = currentPhase.time_end != null ? Number(currentPhase.time_end) : Infinity;
              const phaseMoments = salesMoments.filter(m => m.video_sec >= phaseStart && m.video_sec <= phaseEnd);
              if (phaseMoments.length === 0) return null;
              return (
                <div>
                  <div className="text-[11px] text-white/40 mb-1.5 font-medium flex items-center gap-1.5">
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                    </svg>
                    Sales Moments ({phaseMoments.length})
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {phaseMoments.map((m, i) => {
                      const isStrong = m.moment_type === 'strong';
                      const isClick = m.moment_type === 'click_spike';
                      const btnColor = isStrong
                        ? 'bg-red-500/15 text-red-300 border-red-500/25 hover:bg-red-500/25'
                        : isClick
                        ? 'bg-blue-500/15 text-blue-300 border-blue-500/25 hover:bg-blue-500/25'
                        : 'bg-green-500/15 text-green-300 border-green-500/25 hover:bg-green-500/25';
                      const icon = isStrong ? '⚡' : isClick ? '👆' : '💰';
                      return (
                        <button
                          key={`pm-${i}`}
                          onClick={() => {
                            const v = videoRef.current;
                            if (v) {
                              v.currentTime = m.video_sec;
                              if (v.paused) v.play().catch(() => {});
                            }
                          }}
                          className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium border transition-all duration-150 cursor-pointer ${btnColor}`}
                          title={`Click: ${m.click_value ?? 0} | Order: ${m.order_value ?? 0}`}
                        >
                          <span>{icon}</span>
                          <span>{formatTime(m.video_sec)}</span>
                          <span className="text-[9px] opacity-60">{m.moment_type === 'strong' ? 'STRONG' : m.moment_type === 'click_spike' ? 'CLICK' : 'ORDER'}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })()}

               {/* ── Product Names ──────────────────────────── */}
            {productNames.length > 0 && (
              <div>
                <div className="text-[11px] text-white/40 mb-1.5 font-medium">商品</div>
                <div className="flex flex-wrap gap-1.5">
                  {productNames.slice(0, 3).map((name, idx) => (
                    <span key={idx} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium bg-indigo-500/15 text-indigo-300 border border-indigo-500/25">
                      {name}
                    </span>
                  ))}
                  {productNames.length > 3 && (
                    <span className="text-[11px] text-white/40 self-center">+{productNames.length - 3}</span>
                  )}
                </div>
              </div>
            )}

            {/* ── AI Summary ───────────────────────────────── */}
            {phaseDesc && (
              <div>
                <div className="text-[11px] text-white/40 mb-1.5 font-medium">AI要約</div>
                <p className="text-sm text-white/75 leading-relaxed">{phaseDesc}</p>
              </div>
            )}

            {/* ── Improvement Suggestion ────────────────────── */}
            {currentPhase?.insight && (
              <div className="rounded-xl bg-green-500/5 border border-green-500/15 p-4">
                <div className="text-[11px] text-green-400/80 mb-1.5 font-medium flex items-center gap-1.5">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                  </svg>
                  改善提案
                </div>
                <p className="text-sm text-white/65 leading-relaxed">{currentPhase.insight}</p>
              </div>
            )}

            {/* ── AI Sell Score Detail ─────────────────────── */}
            {(() => {
              const scoreData = eventScores.find(s => s.phase_index === currentPhase?.phase_index);
              if (!scoreData) return null;
              const combined = scoreData.score_combined ?? scoreData.ai_score;
              if (combined == null) return null;
              const clickScore = scoreData.score_click;
              const orderScore = scoreData.score_order;
              const rank = scoreData.rank;
              const total = eventScores.length;
              const pct = Math.round(combined * 100);
              const isHot = combined >= 0.7;
              const barColor = isHot ? 'bg-gradient-to-r from-orange-500 to-red-500' : combined >= 0.4 ? 'bg-amber-500' : 'bg-gray-500';
              const reasons = scoreData.reasons || [];
              const modelVersion = scoreData.model_version;
              const scoreSource = scoreData.score_source;
              return (
                <div className={`rounded-xl p-4 border ${
                  isHot ? 'bg-gradient-to-br from-red-500/10 to-orange-500/10 border-orange-500/25' : 'bg-white/5 border-white/10'
                }`}>
                  <div className="text-[11px] text-white/40 mb-2 font-medium flex items-center gap-1.5">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2L15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2z" />
                    </svg>
                    AI売れやすさスコア
                    <span className="ml-auto text-[10px] text-white/25">
                      {scoreSource === 'model' ? 'ML Model' : 'Heuristic'}
                      {modelVersion && ` ${modelVersion}`}
                    </span>
                  </div>
                  {/* Combined Score */}
                  <div className="flex items-center gap-3 mb-3">
                    <div className={`text-2xl font-black ${
                      isHot ? 'text-orange-300' : combined >= 0.4 ? 'text-amber-300' : 'text-gray-400'
                    }`}>
                      {pct}%
                    </div>
                    <div className="flex-1">
                      <div className="w-full h-2 rounded-full bg-white/10 overflow-hidden">
                        <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  </div>
                  {/* Click / Order sub-scores */}
                  {(clickScore != null || orderScore != null) && (
                    <div className="grid grid-cols-2 gap-2 mb-3">
                      {clickScore != null && (
                        <div className="bg-white/5 rounded-lg p-2">
                          <div className="text-[9px] text-blue-400/60 mb-0.5">Click興味</div>
                          <div className="flex items-center gap-1.5">
                            <span className="text-sm font-bold text-blue-300">{Math.round(clickScore * 100)}%</span>
                            <div className="flex-1 h-1 rounded-full bg-white/10 overflow-hidden">
                              <div className="h-full rounded-full bg-blue-500 transition-all duration-500" style={{ width: `${Math.round(clickScore * 100)}%` }} />
                            </div>
                          </div>
                        </div>
                      )}
                      {orderScore != null && (
                        <div className="bg-white/5 rounded-lg p-2">
                          <div className="text-[9px] text-green-400/60 mb-0.5">Order決断</div>
                          <div className="flex items-center gap-1.5">
                            <span className="text-sm font-bold text-green-300">{Math.round(orderScore * 100)}%</span>
                            <div className="flex-1 h-1 rounded-full bg-white/10 overflow-hidden">
                              <div className="h-full rounded-full bg-green-500 transition-all duration-500" style={{ width: `${Math.round(orderScore * 100)}%` }} />
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                  {/* Reasons */}
                  {reasons.length > 0 && (
                    <div className="space-y-1 mb-2">
                      {reasons.slice(0, 3).map((reason, i) => (
                        <div key={i} className="flex items-start gap-1.5 text-[10px] text-white/50">
                          <span className="text-yellow-500/70 mt-0.5">{'\u2022'}</span>
                          <span>{reason}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center justify-between text-[10px] text-white/35">
                    <span>ランク: {rank} / {total}フェーズ</span>
                    {isHot && <span className="text-orange-400 font-semibold">売れやすい瞬間</span>}
                  </div>
                </div>
              );
            })()}

            {/* ── Reviewer Name ─────────────────────────────── */}
            {phaseKey >= 0 && (
              <div className="rounded-xl bg-white/5 border border-white/10 p-3">
                <div className="flex items-center gap-2">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white/40 flex-shrink-0">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />
                  </svg>
                  {!reviewerName || isEditingReviewer ? (
                    <div className="flex items-center gap-1.5 flex-1">
                      <input
                        type="text"
                        value={reviewerInput}
                        onChange={(e) => setReviewerInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && reviewerInput.trim()) {
                            localStorage.setItem('aitherhub_reviewer_name', reviewerInput.trim());
                            setReviewerName(reviewerInput.trim());
                            setIsEditingReviewer(false);
                          }
                        }}
                        placeholder="あなたの名前を入力"
                        autoFocus
                        className="flex-1 bg-white/5 border border-white/15 rounded-lg px-2.5 py-1.5 text-xs text-white/80 placeholder-white/25 focus:outline-none focus:border-white/30 focus:ring-1 focus:ring-white/20"
                      />
                      <button
                        onClick={() => {
                          if (reviewerInput.trim()) {
                            localStorage.setItem('aitherhub_reviewer_name', reviewerInput.trim());
                            setReviewerName(reviewerInput.trim());
                            setIsEditingReviewer(false);
                          }
                        }}
                        disabled={!reviewerInput.trim()}
                        className="px-2.5 py-1.5 rounded-lg text-[10px] font-medium bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/30 transition-colors disabled:opacity-30"
                      >
                        決定
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 flex-1">
                      <span className="text-xs text-white/70 font-medium">{reviewerName}</span>
                      <button
                        onClick={() => { setReviewerInput(reviewerName); setIsEditingReviewer(true); }}
                        className="text-[10px] text-white/25 hover:text-white/50 transition-colors"
                      >
                        変更
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* ── Star Rating (inline in panel) ─────────────── */}
            {phaseKey >= 0 && (
              <div className="rounded-xl bg-white/5 border border-white/10 p-4">
                <div className="text-[11px] text-white/40 mb-2 font-medium">この区間を採点</div>
                <div className="flex items-center gap-2">
                  <div className="flex items-center gap-1">
                    {[1, 2, 3, 4, 5].map((star) => (
                      <button
                        key={star}
                        onClick={() => {
                          if (!isSavingRating && onRatePhase && phaseKey >= 0) {
                            onRatePhase(phaseKey, star);
                            showDockToast('保存しました');
                          }
                        }}
                        disabled={isSavingRating || phaseKey < 0}
                        className={`p-0.5 transition-all duration-150 ${
                          isSavingRating || phaseKey < 0
                            ? "opacity-30 cursor-not-allowed"
                            : "hover:scale-125 cursor-pointer"
                        }`}
                        title={`${star}点`}
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"
                          fill={star <= currentRating ? "#f59e0b" : "none"}
                          stroke={star <= currentRating ? "#f59e0b" : "#4b5563"}
                          strokeWidth="1.5"
                        >
                          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                        </svg>
                      </button>
                    ))}
                  </div>
                  {isSavingRating && (
                    <div className="w-4 h-4 rounded-full border-2 border-gray-500 border-t-amber-500 animate-spin" />
                  )}
                  {phaseRatings[phaseKey]?.saved && !isSavingRating && (
                    <span className="text-xs text-green-400 font-medium">保存済</span>
                  )}
                  {!currentRating && (
                    <span className="text-xs text-white/30">タップで採点</span>
                  )}
                </div>
              </div>
            )}

            {/* ── Comment Input (optional) ──────────────────── */}
            {phaseKey >= 0 && (
              <div className="rounded-xl bg-white/5 border border-white/10 p-4">
                <div className="text-[11px] text-white/40 mb-2 font-medium flex items-center gap-1.5">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                  </svg>
                  コメント入力
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={ratingComments[phaseKey] || ''}
                    onChange={(e) => onCommentChange?.(phaseKey, e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && ratingComments[phaseKey]?.trim()) {
                        e.preventDefault();
                        onSaveComment?.(phaseKey);
                        showDockToast('保存しました');
                      }
                    }}
                    placeholder="この区間へのメモ（任意）"
                    className="flex-1 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white/80 placeholder-white/25 focus:outline-none focus:border-white/30 focus:ring-1 focus:ring-white/20 transition-colors"
                  />
                  <button
                    onClick={() => {
                      onSaveComment?.(phaseKey);
                      if (ratingComments[phaseKey]?.trim()) showDockToast('保存しました');
                    }}
                    disabled={!ratingComments[phaseKey]?.trim()}
                    className="px-3 py-2 rounded-lg text-xs font-medium bg-white/10 text-white/70 hover:bg-white/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0"
                  >
                    保存
                  </button>
                </div>
              </div>
            )}

            {/* ── Tags Section: 行動タグ + 販売心理タグ (常時展開・1タップ・即保存) ────── */}
            {phaseKey >= 0 && (
              <div className="rounded-xl bg-white/5 border border-white/10 p-4">
                <div className="text-[11px] text-white/40 mb-3 font-medium flex items-center gap-1.5">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                    <line x1="7" y1="7" x2="7.01" y2="7" />
                  </svg>
                  タグ
                  {phaseTagsSaving[phaseKey] && (
                    <div className="w-3 h-3 rounded-full border-2 border-gray-500 border-t-amber-500 animate-spin ml-1" />
                  )}
                  {phaseTagsSaved[phaseKey] && !phaseTagsSaving[phaseKey] && (
                    <span className="text-green-400 text-[10px] ml-1 flex items-center gap-0.5">
                      <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                      保存済
                    </span>
                  )}
                </div>

                {/* ── 行動タグ (Behavior Tags) ──────────────── */}
                <div className="mb-3">
                  <div className="text-[10px] text-white/25 mb-1.5 uppercase tracking-wider">行動</div>
                  <div className="flex flex-wrap gap-1.5">
                    {ALL_PHASE_TAGS.map((tag) => {
                      const cfg = PHASE_TAG_CONFIG[tag] || { label: tag };
                      const isSelected = (selectedPhaseTags[phaseKey] || []).includes(tag);
                      const darkColor = DARK_TAG_COLORS[tag] || "bg-gray-500/20 text-gray-300 border-gray-500/30";
                      return (
                        <button
                          key={tag}
                          onClick={() => togglePhaseTag(phaseKey, tag)}
                          className={`inline-flex items-center px-2.5 py-1 rounded-full text-[10px] font-semibold border transition-all duration-150 ${
                            isSelected
                              ? `${darkColor} ring-1 ring-white/30 shadow-sm`
                              : "bg-white/5 text-white/30 border-white/10 hover:bg-white/10 hover:text-white/50"
                          }`}
                        >
                          {isSelected && (
                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="mr-1">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          )}
                          {cfg.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* ── 販売心理タグ (Sales Psychology Tags) ──── */}
                <div>
                  <div className="text-[10px] text-white/25 mb-1.5 uppercase tracking-wider">販売心理</div>
                  <div className="flex flex-wrap gap-1.5">
                    {ALL_SALES_TAGS.map((tag) => {
                      const cfg = SALES_TAG_CONFIG[tag] || { label: tag };
                      const isSelected = (selectedPhaseTags[phaseKey] || []).includes(tag);
                      const darkColor = DARK_TAG_COLORS[tag] || "bg-gray-500/20 text-gray-300 border-gray-500/30";
                      return (
                        <button
                          key={tag}
                          onClick={() => togglePhaseTag(phaseKey, tag)}
                          className={`inline-flex items-center px-2.5 py-1 rounded-full text-[10px] font-semibold border transition-all duration-150 ${
                            isSelected
                              ? `${darkColor} ring-1 ring-white/30 shadow-sm`
                              : "bg-white/5 text-white/30 border-white/10 hover:bg-white/10 hover:text-white/50"
                          }`}
                        >
                          {isSelected && (
                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="mr-1">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          )}
                          {cfg.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}

            {/* ── Clip Generation Button ────────────────────────── */}
            {phaseKey >= 0 && currentPhase && (
              <div className="rounded-xl bg-white/5 border border-white/10 p-4">
                <div className="text-[11px] text-white/40 mb-2 font-medium flex items-center gap-1.5">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
                    <line x1="7" y1="2" x2="7" y2="22" />
                    <line x1="17" y1="2" x2="17" y2="22" />
                    <line x1="2" y1="12" x2="22" y2="12" />
                    <line x1="2" y1="7" x2="7" y2="7" />
                    <line x1="2" y1="17" x2="7" y2="17" />
                    <line x1="17" y1="7" x2="22" y2="7" />
                    <line x1="17" y1="17" x2="22" y2="17" />
                  </svg>
                  切り抜き生成
                </div>
                {(() => {
                  const clipState = clipStates[phaseKey];
                  const isClipLoading = clipState?.status === 'requesting' || clipState?.status === 'pending' || clipState?.status === 'processing';
                  const isClipCompleted = clipState?.status === 'completed' && clipState?.clip_url;
                  const isClipFailed = clipState?.status === 'failed';

                  if (isClipCompleted) {
                    return (
                      <div className="flex items-center gap-2">
                        <a
                          href={clipState.clip_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-green-500/15 text-green-300 border border-green-500/25 hover:bg-green-500/25 transition-colors"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                            <polyline points="7 10 12 15 17 10" />
                            <line x1="12" y1="15" x2="12" y2="3" />
                          </svg>
                          ダウンロード
                        </a>
                        <span className="text-[10px] text-green-400">生成完了</span>
                      </div>
                    );
                  }

                  if (isClipLoading) {
                    return (
                      <div className="flex items-center gap-2">
                        <div className="w-4 h-4 rounded-full border-2 border-gray-500 border-t-purple-400 animate-spin" />
                        <span className="text-xs text-white/50">切り抜き生成中...</span>
                      </div>
                    );
                  }

                  return (
                    <div className="space-y-2">
                      <button
                        onClick={() => onClipGenerate?.(currentPhase, phaseKey)}
                        className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-purple-500/15 text-purple-300 border border-purple-500/25 hover:bg-purple-500/25 transition-colors"
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polygon points="23 7 16 12 23 17 23 7" />
                          <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
                        </svg>
                        この区間を切り抜き
                      </button>
                      <div className="text-[10px] text-white/25">
                        {formatTime(currentPhase.time_start)} – {formatTime(currentPhase.time_end)} の区間を切り抜き動画として生成
                      </div>
                      {isClipFailed && (
                        <div className="text-[10px] text-red-400">生成に失敗しました。再度お試しください。</div>
                      )}
                    </div>
                  );
                })()}
              </div>
            )}

            {/* ── No phase selected ─────────────────────────── */}
            {!currentPhase && reports1.length > 0 && (
              <div className="flex flex-col items-center justify-center h-full text-white/30 gap-2">
                <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                <span className="text-sm">動画を再生すると区間データが表示されます</span>
              </div>
            )}
          </div>

          {/* ─── Bottom Control Bar (inside right panel) ──── */}
          <div className="flex items-center justify-between px-4 py-2.5 border-t border-white/10 bg-gray-900/60 flex-shrink-0">
            {/* Speed buttons */}
            <div className="flex items-center gap-1">
              {[1, 1.5, 2].map((rate) => (
                <button
                  key={rate}
                  onClick={() => handleSpeedChange(rate)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all duration-150 ${
                    playbackRate === rate
                      ? "bg-white text-gray-900 shadow-sm"
                      : "bg-white/10 text-white/60 hover:bg-white/20 hover:text-white/80"
                  }`}
                >
                  {rate}x
                </button>
              ))}
            </div>

            {/* Phase navigation */}
            <div className="flex items-center gap-2">
              <button
                onClick={(e) => { e.stopPropagation(); navigatePhase(-1); }}
                disabled={navDisabled || currentPhaseIndex <= 0}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/10 text-white/70 hover:bg-white/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="15 18 9 12 15 6" />
                </svg>
                前
              </button>
              <span className="text-[11px] text-white/40 min-w-[60px] text-center">
                {currentPhaseIndex >= 0 ? `${currentPhaseIndex + 1} / ${reports1.length}` : `–`}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); navigatePhase(1); }}
                disabled={navDisabled || currentPhaseIndex >= reports1.length - 1}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/10 text-white/70 hover:bg-white/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              >
                次
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </button>
            </div>

            {/* Play Mode toggle */}
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setPlayMode('phase')}
                className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[10px] font-semibold transition-all duration-150 ${
                  playMode === 'phase'
                    ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                    : 'bg-white/5 text-white/30 border border-white/10 hover:bg-white/10 hover:text-white/50'
                }`}
                title="Phase Mode: フェーズロック再生（自動ループ）"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 1l4 4-4 4" /><path d="M3 11V9a4 4 0 0 1 4-4h14" /><path d="M7 23l-4-4 4-4" /><path d="M21 13v2a4 4 0 0 1-4 4H3" />
                </svg>
                Phase
              </button>
              <button
                onClick={() => setPlayMode('full')}
                className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[10px] font-semibold transition-all duration-150 ${
                  playMode === 'full'
                    ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                    : 'bg-white/5 text-white/30 border border-white/10 hover:bg-white/10 hover:text-white/50'
                }`}
                title="Full Mode: 自由再生（フェーズロック解除）"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                Full
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ─── CSS for fade-in animation ────────────────────── */}
      <style>{`
        @keyframes fade-in {
          from { opacity: 0; transform: translate(-50%, -8px); }
          to { opacity: 1; transform: translate(-50%, 0); }
        }
        .animate-fade-in {
          animation: fade-in 0.15s ease-out;
        }
        @keyframes fadeInDown {
          from { opacity: 0; transform: translate(-50%, -16px); }
          to { opacity: 1; transform: translate(-50%, 0); }
        }
      `}</style>
    </div>
  );
}
