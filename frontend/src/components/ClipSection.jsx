import { useState, useMemo, useEffect, useRef } from "react";
import VideoService from "../base/services/videoService";
import ClipEditorV2 from "./ClipEditorV2";

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
    return { label: "AI推薦", color: "#D97706", bgColor: "#FFFBEB" };
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
  useEffect(() => {
    if (!editorParams || editorAutoOpenedRef.current || visibleClips.length === 0) return;
    editorAutoOpenedRef.current = true;

    // Find the clip that matches the editorParams (by phase_index or time range)
    let targetClip = null;
    if (editorParams.phase_index != null) {
      targetClip = visibleClips.find(
        (c) => String(c.phaseIndex) === String(editorParams.phase_index)
      );
    }
    // Fallback: match by time range overlap
    if (!targetClip && editorParams.time_start != null && editorParams.time_end != null) {
      targetClip = visibleClips.find((c) => {
        const cStart = c.time_start ?? 0;
        const cEnd = c.time_end ?? 0;
        return cStart < editorParams.time_end && cEnd > editorParams.time_start;
      });
    }
    // Fallback: open the first clip
    if (!targetClip && visibleClips.length > 0) {
      targetClip = visibleClips[0];
    }
    if (targetClip) {
      handleOpenEditor(targetClip);
    }
  }, [editorParams, visibleClips]);

  // Don't render if no visible clips
  if (visibleClips.length === 0) return null;

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
    if (m > 0) return `${m}分${s}秒`;
    return `${s}秒`;
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
    "AI推薦": "from-amber-500 to-orange-500",
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
                      </div>

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
                            <span className="text-purple-600 text-sm font-medium">字幕を生成中...</span>
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
                          編集
                        </button>
                        {/* Download button */}
                        <a
                          href={clip.clip_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-gradient-to-r from-purple-500 to-pink-500 text-white text-sm font-medium hover:from-purple-600 hover:to-pink-600 transition-all shadow-sm hover:shadow-md group-hover:shadow-lg"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                          </svg>
                          ダウンロード
                        </a>
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

      {/* ClipEditorV2 Modal */}
      {editorClip && (
        <ClipEditorV2
          videoId={videoData?.id}
          videoData={videoData}
          clip={editorClip}
          phases={reports1}
          onClose={() => setEditorClip(null)}
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
