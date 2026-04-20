import React, { useState, useMemo, useEffect, useRef, useCallback } from "react";
import VideoService from "../base/services/videoService";
import ClipEditorV2 from "./ClipEditorV2";

/**
 * Download a clip by fetching a fresh SAS URL from the API first.
 * Falls back to the cached URL if the API call fails.
 */
async function handleDownloadClip(videoId, phaseIndex, fallbackUrl, clip, onDownloaded) {
  const doDownload = async (url) => {
    // Force download via fetch→blob to avoid opening in new tab
    try {
      const resp = await fetch(url);
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = `clip_phase${phaseIndex || ''}.mp4`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    } catch (dlErr) {
      console.warn('[ClipSection] Blob download failed, falling back to direct link:', dlErr);
      const a = document.createElement('a');
      a.href = url;
      a.download = `clip_phase${phaseIndex || ''}.mp4`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }
  };
  try {
    const res = await VideoService.getClipStatus(videoId, phaseIndex);
    const freshUrl = res?.clip_url || fallbackUrl;
    if (freshUrl) {
      await doDownload(freshUrl);
      // Record download for ML training (non-blocking)
      VideoService.recordClipDownload(videoId, {
        phase_index: phaseIndex,
        time_start: clip?.time_start || null,
        time_end: clip?.time_end || null,
        clip_id: clip?.clip_id || null,
        export_type: 'raw',
      });
      if (onDownloaded) onDownloaded(phaseIndex);
    }
  } catch (e) {
    console.warn('[ClipSection] Failed to fetch fresh download URL, using cached:', e);
    if (fallbackUrl) {
      await doDownload(fallbackUrl);
      // Still record the download attempt
      VideoService.recordClipDownload(videoId, {
        phase_index: phaseIndex,
        time_start: clip?.time_start || null,
        time_end: clip?.time_end || null,
        clip_id: clip?.clip_id || null,
        export_type: 'raw',
      });
      if (onDownloaded) onDownloaded(phaseIndex);
    }
  }
}

/**
 * Detect the source/detection method from phase_index naming convention.
 * Returns { label, color, bgColor } for rendering a badge.
 */
function detectSource(phaseIndex) {
  const key = String(phaseIndex ?? "");
  if (key.startsWith("moment_strong") || key.startsWith("moment_subtle") || key.startsWith("moment_")) {
    return { label: "Moment", color: "#7C3AED", bgColor: "#EDE9FE" };
  }
  if (key.startsWith("sales_spike") || key.startsWith("sales_")) {
    return { label: "Sales Spike", color: "#EA580C", bgColor: "#FFF7ED" };
  }
  if (key.startsWith("hook_") || key.startsWith("hook")) {
    return { label: "Hook", color: "#DC2626", bgColor: "#FEF2F2" };
  }
  if (key.startsWith("ai_recommend") || key.startsWith("ai_")) {
    return { label: window.__t('clipSectionAiRecommend'), color: "#D97706", bgColor: "#FFFBEB" };
  }
  // Numeric phase index = standard phase-based clip
  if (/^\d+$/.test(key)) {
    return { label: `Phase ${Number(key) + 1}`, color: "#6366F1", bgColor: "#EEF2FF" };
  }
  return { label: key, color: "#6B7280", bgColor: "#F3F4F6" };
}

/**
 * ClipSection – displays generated clip videos at the top of the video detail page.
 * Shows clip cards with download buttons, edit buttons, status indicators, and metadata.
 *
 * Props:
 *   videoData – the full video detail object
 *   clipStates – current clip generation states from parent (keyed by phase_index)
 *   reports1 – array of phase objects (for phase labels)
 */
export default function ClipSection({ videoData, clipStates, reports1, editorParams }) {
  const [collapsed, setCollapsed] = useState(false);
  const [editorClip, setEditorClip] = useState(null);
  const editorAutoOpenedRef = useRef(false);
  const [clipRatings, setClipRatings] = useState({});
  const [clipDownloads, setClipDownloads] = useState({});
  // Auto-generation state for when editorParams targets a clip that doesn't exist yet
  const [autoGenerating, setAutoGenerating] = useState(false);
  const [autoGenerateStatus, setAutoGenerateStatus] = useState('');
  const autoGenPollingRef = useRef(null);

  // Fetch clip ratings for badge display
  const fetchClipRatings = useCallback(async () => {
    if (!videoData?.id) return;
    try {
      const resp = await VideoService.getClipRatings(videoData.id);
      if (resp?.ratings) {
        const map = {};
        resp.ratings.forEach((r) => {
          map[String(r.phase_index)] = r.rating; // 'good' | 'bad'
        });
        setClipRatings(map);
      }
    } catch (e) {
      console.warn('[ClipSection] Failed to fetch clip ratings:', e);
    }
  }, [videoData?.id]);

  // Fetch clip download counts for badge display
  const fetchClipDownloads = useCallback(async () => {
    if (!videoData?.id) return;
    try {
      const resp = await VideoService.getClipDownloads(videoData.id);
      if (resp?.downloads) {
        setClipDownloads(resp.downloads);
      }
    } catch (e) {
      console.warn('[ClipSection] Failed to fetch clip downloads:', e);
    }
  }, [videoData?.id]);

  useEffect(() => {
    fetchClipRatings();
    fetchClipDownloads();
  }, [fetchClipRatings, fetchClipDownloads]);

  // Get clips that are completed or generating subtitles
  const visibleClips = useMemo(() => {
    if (!clipStates) return [];
    return Object.entries(clipStates)
      .filter(([, state]) => (state.status === "completed" || state.status === "generating_subtitles") && state.clip_url)
      .map(([phaseIndex, state]) => {
        // phase_index can be numeric ("63") or string ("moment_strong_1")
        const numIdx = parseInt(phaseIndex, 10);
        const isNumeric = !isNaN(numIdx);

        // Try to find matching phase from reports1
        let phase = null;
        if (isNumeric && reports1?.[numIdx]) {
          phase = reports1[numIdx];
        } else if (reports1?.length) {
          // For non-numeric phase_index, try to find phase by time range overlap
          const tStart = state.time_start;
          const tEnd = state.time_end;
          if (tStart != null && tEnd != null) {
            phase = reports1.find((p) => {
              const pStart = p?.time_start ?? 0;
              const pEnd = p?.time_end ?? 0;
              return pStart < tEnd && pEnd > tStart;
            });
          }
        }

        const source = detectSource(phaseIndex);

        return {
          phaseIndex,
          phaseIndexNum: isNumeric ? numIdx : null,
          // displayPhaseIndex = reports_1[arrayIndex].phase_index (the user-facing phase number)
          displayPhaseIndex: phase?.phase_index ?? null,
          clip_url: state.clip_url,
          clip_id: state.clip_id || state.id,
          time_start: state.time_start ?? phase?.time_start,
          time_end: state.time_end ?? phase?.time_end,
          insight: phase?.insight,
          phase,
          source,
          captions: state.captions,
          isGeneratingSubtitles: state.status === "generating_subtitles",
        };
      })
      .sort((a, b) => {
        // Sort by time_start, then by phaseIndex string
        const aStart = a.time_start ?? 0;
        const bStart = b.time_start ?? 0;
        if (aStart !== bStart) return aStart - bStart;
        return String(a.phaseIndex).localeCompare(String(b.phaseIndex));
      });
  }, [clipStates, reports1]);

  // Auto-open editor when editorParams are provided (from feedback card click)
  // If no matching clip exists, auto-generate it and open the editor when ready.
  useEffect(() => {
    if (!editorParams || editorAutoOpenedRef.current) return;
    // Allow running even when visibleClips is empty (we may need to generate the clip)
    if (!videoData?.id) return;

    // Helper: search for a matching clip in visibleClips
    const findMatchingClip = () => {
      let target = null;
      if (editorParams.phase_index != null && visibleClips.length > 0) {
        // 1) Match by clipStates key (array index used in video_clips table)
        target = visibleClips.find(
          (c) => String(c.phaseIndex) === String(editorParams.phase_index)
        );
        // 2) Match by displayPhaseIndex (reports_1[].phase_index, the user-facing phase number)
        if (!target) {
          target = visibleClips.find(
            (c) => c.displayPhaseIndex != null && String(c.displayPhaseIndex) === String(editorParams.phase_index)
          );
        }
        // 3) Convert: find the array index for the given phase_index in reports1
        if (!target && reports1?.length) {
          const arrayIdx = reports1.findIndex(
            (r) => r?.phase_index != null && String(r.phase_index) === String(editorParams.phase_index)
          );
          if (arrayIdx >= 0) {
            target = visibleClips.find(
              (c) => String(c.phaseIndex) === String(arrayIdx)
            );
          }
        }
      }
      // Fallback: match by time range overlap (generous 5-second tolerance)
      if (!target && editorParams.time_start != null && editorParams.time_end != null && visibleClips.length > 0) {
        const tolerance = 5;
        target = visibleClips.find((c) => {
          const cStart = (c.time_start ?? 0) - tolerance;
          const cEnd = (c.time_end ?? 0) + tolerance;
          return cStart < editorParams.time_end && cEnd > editorParams.time_start;
        });
      }
      return target;
    };

    const targetClip = findMatchingClip();

    if (targetClip) {
      // Found a matching clip - open editor immediately
      editorAutoOpenedRef.current = true;
      handleOpenEditor(targetClip);
      return;
    }

    // No matching clip found - auto-generate it
    if (editorParams.time_start == null || editorParams.time_end == null) return;
    if (autoGenerating) return; // already generating

    const phaseIdx = editorParams.phase_index ?? `auto_${editorParams.time_start}`;
    const timeStart = Number(editorParams.time_start);
    const timeEnd = Number(editorParams.time_end);
    if (isNaN(timeStart) || isNaN(timeEnd)) return;

    editorAutoOpenedRef.current = true;
    setAutoGenerating(true);
    setAutoGenerateStatus(window.__t('clipSectionGeneratingClip'));
    console.log(`[ClipSection] No matching clip for phase=${phaseIdx} t=${timeStart}-${timeEnd}, auto-generating`);

    (async () => {
      try {
        // Get user's UI language for subtitle generation
        const uiLang = (localStorage.getItem('aitherhub_language') || 'ja');
        const subtitleLang = uiLang === 'zh-TW' ? 'zh-TW' : uiLang === 'en' ? 'auto' : 'ja';
        const res = await VideoService.requestClipGeneration(videoData.id, phaseIdx, timeStart, timeEnd, 1.2, subtitleLang);
        if (res.status === 'completed' && res.clip_url) {
          // Already generated (cache hit)
          setAutoGenerating(false);
          setAutoGenerateStatus('');
          setEditorClip({
            clip_url: res.clip_url,
            clip_id: res.clip_id,
            phase_index: phaseIdx,
            time_start: timeStart,
            time_end: timeEnd,
          });
          return;
        }

        // Poll for completion
        setAutoGenerateStatus(window.__t('clipSection_84e712', 'クリップを生成中... (0%)'));
        const pollId = setInterval(async () => {
          try {
            const statusRes = await VideoService.getClipStatus(videoData.id, phaseIdx);
            if (statusRes.status === 'completed' && statusRes.clip_url) {
              clearInterval(pollId);
              autoGenPollingRef.current = null;
              setAutoGenerating(false);
              setAutoGenerateStatus('');
              setEditorClip({
                clip_url: statusRes.clip_url,
                clip_id: statusRes.clip_id,
                phase_index: phaseIdx,
                time_start: statusRes.time_start ?? timeStart,
                time_end: statusRes.time_end ?? timeEnd,
              });
            } else if (statusRes.status === 'failed' || statusRes.status === 'dead') {
              clearInterval(pollId);
              autoGenPollingRef.current = null;
              setAutoGenerating(false);
              setAutoGenerateStatus(window.__t('clipSection_f80a03', 'クリップ生成に失敗しました'));
            } else {
              const pct = statusRes.progress_pct || 0;
              const step = statusRes.progress_step || '';
              setAutoGenerateStatus(`クリップを生成中... (${pct}%) ${step}`);
            }
          } catch (e) {
            // continue polling
          }
        }, 4000);
        autoGenPollingRef.current = pollId;
      } catch (e) {
        console.error('[ClipSection] Auto-generate failed:', e);
        setAutoGenerating(false);
        setAutoGenerateStatus(window.__t('clipSection_c34c5d', 'クリップ生成リクエストに失敗しました'));
      }
    })();

    return () => {
      if (autoGenPollingRef.current) {
        clearInterval(autoGenPollingRef.current);
        autoGenPollingRef.current = null;
      }
    };
  }, [editorParams, visibleClips, reports1, videoData?.id, autoGenerating]);

  // Don't render if no visible clips AND not auto-generating
  if (visibleClips.length === 0 && !autoGenerating && !autoGenerateStatus && !editorClip) return null;

  const formatTime = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "--:--";
    const s = Math.round(Number(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const formatDuration = (start, end) => {
    if (start == null || end == null) return "";
    const dur = Math.round(Number(end) - Number(start));
    if (dur <= 0) return "";
    const m = Math.floor(dur / 60);
    const s = dur % 60;
    if (m > 0) return `${m}${window.__t('common_minutes', '分')}${s}${window.__t('common_seconds', '秒')}`;
    return `${s}${window.__t('common_seconds', '秒')}`;
  };

  const handleOpenEditor = (clip) => {
    setEditorClip({
      clip_url: clip.clip_url,
      clip_id: clip.clip_id,
      phase_index: clip.phaseIndex,
      time_start: clip.time_start,
      time_end: clip.time_end,
      insight: clip.insight,
      captions: clip.captions,
    });
  };

  // Gradient colors per source type
  const sourceGradients = {
    "Moment": "from-purple-500 to-pink-500",
    "Sales Spike": "from-orange-500 to-red-500",
    "Hook": "from-red-500 to-rose-500",
    [window.__t('clipSectionAiRecommend', 'AI推薦')]: "from-amber-500 to-orange-500",
  };

  return (
    <div className="w-full mt-6 mx-auto mb-4">
      <div className="rounded-2xl bg-gradient-to-br from-purple-50 to-pink-50 border border-purple-200">
        {/* Header */}
        <div
          onClick={() => setCollapsed((s) => !s)}
          className="flex items-center justify-between p-5 cursor-pointer hover:bg-purple-100/50 transition-all duration-200 rounded-t-2xl"
        >
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center shadow-sm">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>
              </svg>
            </div>
            <div>
              <div className="text-gray-900 text-xl font-semibold flex items-center gap-2">
                切り抜き動画
                <span className="inline-flex items-center justify-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gradient-to-r from-purple-500 to-pink-500 text-white">
                  {visibleClips.length}件
                </span>
              </div>
              <div className="text-gray-500 text-sm mt-1">
                TikTok・Reels向け縦型ショート動画
              </div>
            </div>
          </div>
          <button type="button" className="text-gray-400 p-2 rounded focus:outline-none transition-colors">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="1.5"
              className={`w-6 h-6 transform transition-transform duration-200 ${!collapsed ? "rotate-180" : ""}`}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>

        {/* Content */}
        {!collapsed && (
          <div className="px-5 pb-5">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {visibleClips.map((clip) => {
                const gradient = sourceGradients[clip.source.label] || "from-indigo-500 to-purple-500";
                return (
                  <div
                    key={clip.phaseIndex}
                    className="bg-white rounded-xl border border-purple-100 shadow-sm hover:shadow-md transition-all duration-200 overflow-hidden group"
                  >
                    {/* Clip card header - source + time */}
                    <div className={`bg-gradient-to-r ${gradient} px-4 py-2 flex items-center justify-between`}>
                      <span className="text-white text-xs font-medium">
                        {clip.source.label}
                      </span>
                      <span className="text-white/80 text-xs">
                        {formatTime(clip.time_start)} - {formatTime(clip.time_end)}
                      </span>
                    </div>

                    {/* Clip card body */}
                    <div className="p-4">
                      {/* Duration + source badges */}
                      <div className="flex items-center gap-2 mb-3 flex-wrap">
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-purple-50 text-purple-600 text-xs">
                          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
                          </svg>
                          {formatDuration(clip.time_start, clip.time_end)}
                        </span>
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-pink-50 text-pink-600 text-xs">
                          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <rect x="2" y="2" width="20" height="20" rx="5" ry="5"/><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"/><line x1="17.5" y1="6.5" x2="17.51" y2="6.5"/>
                          </svg>
                          9:16
                        </span>
                        {/* Source badge */}
                        <span
                          className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
                          style={{ color: clip.source.color, backgroundColor: clip.source.bgColor }}
                        >
                          {clip.source.label}
                        </span>
                        {/* Rating badge */}
                        {clipRatings[String(clip.phaseIndex)] === 'good' && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-700 border border-emerald-200">
                            <span>👍</span> 使える
                          </span>
                        )}
                        {clipRatings[String(clip.phaseIndex)] === 'bad' && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-600 border border-red-200">
                            <span>👎</span> 微妙
                          </span>
                        )}
                        {/* Download status badge */}
                        {clipDownloads[String(clip.phaseIndex)]?.total > 0 && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold bg-blue-100 text-blue-700 border border-blue-200">
                            <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                            </svg>
                            DL済{clipDownloads[String(clip.phaseIndex)].total > 1 ? ` x${clipDownloads[String(clip.phaseIndex)].total}` : ''}
                          </span>
                        )}
                      </div>

                      {/* Sales psychology tags from phase */}
                      {clip.phase?.sales_psychology_tags && (() => {
                        let tags = clip.phase.sales_psychology_tags;
                        if (typeof tags === 'string') { try { tags = JSON.parse(tags); } catch { tags = []; } }
                        if (!Array.isArray(tags) || tags.length === 0) return null;
                        const TAG_COLORS = {
                          [window.__t('empathy', '共感')]: '#92400E', [window.__t('adminDashboard_f983c2', '権威')]: '#1E40AF', [window.__t('adminDashboard_f8cd5e', '限定性')]: '#9D174D',
                          [window.__t('adminDashboard_f5b486', '実演')]: '#065F46', [window.__t('comparison', '比較')]: '#3730A3', [window.__t('adminDashboard_cffaf2', 'ストーリー')]: '#991B1B',
                          [window.__t('adminDashboard_1d9246', 'テンション')]: '#9A3412', [window.__t('adminDashboard_2ae709', '緊急性')]: '#854D0E', [window.__t('adminDashboard_fe9111', '社会的証明')]: '#166534',
                          [window.__t('adminDashboard_6049bc', '価格訴求')]: '#047857', [window.__t('adminDashboard_3b0c9b', '問題提起')]: '#9F1239', [window.__t('adminDashboard_7c11e2', '解決提示')]: '#0C4A6E',
                        };
                        return (
                          <div className="flex flex-wrap gap-1 mb-2">
                            {tags.slice(0, 4).map((tag, i) => (
                              <span
                                key={i}
                                className="px-1.5 py-0.5 rounded text-[10px] font-medium border"
                                style={{
                                  color: TAG_COLORS[tag] || '#374151',
                                  backgroundColor: (TAG_COLORS[tag] || '#374151') + '15',
                                  borderColor: (TAG_COLORS[tag] || '#374151') + '30',
                                }}
                              >
                                {tag}
                              </span>
                            ))}
                            {tags.length > 4 && <span className="text-[10px] text-gray-400">+{tags.length - 4}</span>}
                          </div>
                        );
                      })()}

                      {/* GMV & product info from phase */}
                      {clip.phase && (clip.phase.gmv > 0 || clip.phase.product_names) && (
                        <div className="flex items-center gap-2 mb-2 text-[11px]">
                          {clip.phase.gmv > 0 && (
                            <span className="font-bold text-green-600">
                              ¥{clip.phase.gmv >= 10000 ? `${(clip.phase.gmv / 10000).toFixed(1)}${window.__t('tenThousand', '万')}` : Math.round(clip.phase.gmv).toLocaleString()}
                            </span>
                          )}
                          {clip.phase.product_names && (
                            <span className="text-gray-500 truncate max-w-[150px]">{clip.phase.product_names}</span>
                          )}
                        </div>
                      )}

                      {/* Insight preview */}
                      {clip.insight && (
                        <p className="text-gray-600 text-xs leading-relaxed line-clamp-2 mb-3">
                          {clip.insight.substring(0, 80)}{clip.insight.length > 80 ? "..." : ""}
                        </p>
                      )}

                      {/* Action buttons or subtitle generation indicator */}
                      {clip.isGeneratingSubtitles ? (
                        <div className="flex flex-col gap-1.5 px-4 py-3 rounded-lg bg-gradient-to-r from-purple-50 to-pink-50 border border-purple-200">
                          <div className="flex items-center justify-between">
                            <span className="text-purple-600 text-sm font-medium">{window.__t('generatingSubtitles')}</span>
                            <span className="text-purple-500 text-sm font-bold">95%</span>
                          </div>
                          <div className="w-full h-2 bg-purple-100 rounded-full overflow-hidden">
                            <div className="h-full bg-gradient-to-r from-purple-500 to-pink-500 rounded-full transition-all duration-500 ease-out" style={{ width: '95%' }} />
                          </div>
                        </div>
                      ) : (
                      <div className="flex gap-2">
                        {/* Edit button */}
                        <button
                          onClick={() => handleOpenEditor(clip)}
                          className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-white border-2 border-purple-400 text-purple-600 text-sm font-medium hover:bg-purple-50 transition-all shadow-sm"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                          </svg>
                          {window.__t('clip_edit')}
                        </button>
                        {/* Download button - fetches fresh SAS URL on click */}
                        <button
                          onClick={() => handleDownloadClip(videoData?.id, clip.phaseIndex, clip.clip_url, clip, (pi) => {
                            // Update local download state immediately
                            setClipDownloads(prev => {
                              const existing = prev[String(pi)] || { total: 0, raw: 0, subtitled: 0, last_downloaded_at: null };
                              return { ...prev, [String(pi)]: { ...existing, total: existing.total + 1, raw: existing.raw + 1, last_downloaded_at: new Date().toISOString() } };
                            });
                          })}
                          className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-gradient-to-r from-purple-500 to-pink-500 text-white text-sm font-medium hover:from-purple-600 hover:to-pink-600 transition-all shadow-sm hover:shadow-md group-hover:shadow-lg"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                          </svg>
                          {window.__t('clip_download')}
                        </button>
                      </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Auto-generation loading overlay */}
      {(autoGenerating || autoGenerateStatus) && !editorClip && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="bg-white rounded-2xl p-8 shadow-2xl max-w-sm mx-4 text-center">
            {autoGenerating ? (
              <>
                <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center animate-pulse">
                  <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>
                  </svg>
                </div>
                <div className="text-gray-900 text-lg font-semibold mb-2">{autoGenerateStatus}</div>
                <div className="text-gray-500 text-sm">{window.__t('clipSectionAutoGeneratingReason')}</div>
              </>
            ) : (
              <>
                <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-red-100 flex items-center justify-center">
                  <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#DC2626" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
                  </svg>
                </div>
                <div className="text-gray-900 text-lg font-semibold mb-2">{autoGenerateStatus}</div>
                <button
                  onClick={() => { setAutoGenerateStatus(''); }}
                  className="mt-4 px-6 py-2 bg-gray-100 hover:bg-gray-200 rounded-lg text-gray-700 text-sm transition-colors"
                >
                  {window.__t('common_close')}
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {/* ClipEditorV2 Modal */}
      {editorClip && (
        <ClipEditorV2
          videoId={videoData?.id}
          videoData={videoData}
          clip={editorClip}
          phases={reports1}
          onClose={() => {
            setEditorClip(null);
            // Refresh download/rating badges after editor closes
            fetchClipDownloads();
            fetchClipRatings();
          }}
          onClipUpdated={(res) => {
            // Keep editor open after trim - update clip data instead of closing
            if (res && typeof res === 'object') {
              setEditorClip(prev => ({ ...prev, ...res }));
            }
            // Note: clip list will refresh on next page load
          }}
        />
      )}
    </div>
  );
}
