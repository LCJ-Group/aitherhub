import { useEffect, useRef, useState, useCallback, useMemo } from "react";

/**
 * DockPlayer – Sales Intelligence Player (方法2: LCJ分析ツール型)
 *
 * フルスクリーン分析モード:
 *   左: 9:16動画を最大化（黒余白ゼロ）
 *   右: 区間データパネル（売上/注文/視聴者/いいね/商品/Tags/AI分析/改善提案/★採点）
 *   下: コンパクト操作バー
 *   速度表示: 動画上にYouTube型オーバーレイ
 */

// ── Sales Psychology Tag Config ──────────────────────────
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

export default function DockPlayer({
  open,
  onClose,
  videoUrl,
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
}) {
  const videoRef = useRef(null);
  const hasSetupRef = useRef(false);
  const prevVideoUrlRef = useRef(null);
  const prevTimeStartRef = useRef(null);

  const [isLoading, setIsLoading] = useState(true);
  const [showCustomLoading, setShowCustomLoading] = useState(true);
  const [isBuffering, setIsBuffering] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1.5);
  const [currentPhaseIndex, setCurrentPhaseIndex] = useState(-1);
  const [isMinimized, setIsMinimized] = useState(false);
  const [showSpeedOverlay, setShowSpeedOverlay] = useState(false);
  const speedOverlayTimer = useRef(null);

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

  // ── Reset on close ────────────────────────────────────────
  useEffect(() => {
    if (!open) {
      hasSetupRef.current = false;
      setIsLoading(true);
      setShowCustomLoading(true);
      setIsBuffering(false);
      setCurrentPhaseIndex(-1);
    }
  }, [open]);

  // ── Setup seek/play when URL or timeStart changes ─────────
  useEffect(() => {
    if (!open || !videoUrl) return;
    const vid = videoRef.current;
    if (!vid) return;

    const urlChanged = videoUrl !== prevVideoUrlRef.current;
    const timeChanged = timeStart !== prevTimeStartRef.current;

    prevVideoUrlRef.current = videoUrl;
    prevTimeStartRef.current = timeStart;

    if (!urlChanged && timeChanged && !isClipPreview) {
      vid.currentTime = timeStart;
      setCurrentPhaseIndex(findPhaseIndex(timeStart));
      if (vid.paused) vid.play().catch(() => {});
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

        if (!isClipPreview && timeStart > 0) {
          vid.currentTime = timeStart;
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
  }, [videoUrl, open, timeStart, isClipPreview, findPhaseIndex]);

  // ── Timeupdate: track current phase ───────────────────────
  useEffect(() => {
    if (!open) return;
    const vid = videoRef.current;
    if (!vid) return;

    const onTimeUpdate = () => {
      const idx = findPhaseIndex(vid.currentTime);
      setCurrentPhaseIndex((prev) => (idx !== prev ? idx : prev));
    };

    vid.addEventListener("timeupdate", onTimeUpdate);
    return () => vid.removeEventListener("timeupdate", onTimeUpdate);
  }, [open, findPhaseIndex]);

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
        default:
          break;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, handleSpeedChange, currentPhaseIndex, reports1]);

  // ── Navigate to prev/next phase ───────────────────────────
  const navigatePhase = useCallback(
    (direction) => {
      if (!reports1 || reports1.length === 0) return;
      let targetIdx = currentPhaseIndex + direction;
      if (targetIdx < 0) targetIdx = 0;
      if (targetIdx >= reports1.length) targetIdx = reports1.length - 1;
      if (targetIdx === currentPhaseIndex) return;

      const targetPhase = reports1[targetIdx];
      setCurrentPhaseIndex(targetIdx);

      if (onPhaseNavigate) {
        onPhaseNavigate(targetPhase);
      } else {
        const vid = videoRef.current;
        if (vid && !isClipPreview) {
          vid.currentTime = Number(targetPhase.time_start) || 0;
          if (vid.paused) vid.play().catch(() => {});
        }
      }
    },
    [currentPhaseIndex, reports1, isClipPreview, onPhaseNavigate]
  );

  // ── Buffering handlers ────────────────────────────────────
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
            <video ref={videoRef} src={videoUrl} className="w-full h-full object-cover" muted />
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
      {/* ─── Top Bar ──────────────────────────────────────── */}
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
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-white/10 transition-colors"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      {/* ─── Main Content: Video (left) + Analysis (right) ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* LEFT: Video — 9:16 maximized, centered */}
        <div className="relative bg-black flex items-center justify-center" style={{ width: "45%" }}>
          {videoUrl ? (
            <>
              <video
                ref={videoRef}
                key={videoUrl}
                src={videoUrl}
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
            </>
          ) : (
            <div className="w-full h-full flex items-center justify-center text-white/50 text-sm">
              動画を読み込み中...
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
                </div>

                {/* Tags */}
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

            {/* ── Product Names ────────────────────────────── */}
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
                onClick={() => navigatePhase(-1)}
                disabled={currentPhaseIndex <= 0}
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
                onClick={() => navigatePhase(1)}
                disabled={currentPhaseIndex >= reports1.length - 1}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/10 text-white/70 hover:bg-white/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              >
                次
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </button>
            </div>

            {/* Keyboard hints */}
            <div className="hidden lg:flex items-center gap-2 text-[9px] text-white/20">
              <span>1/2/3=速度</span>
              <span>Shift+矢印=移動</span>
              <span>Esc=閉じる</span>
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
      `}</style>
    </div>
  );
}
