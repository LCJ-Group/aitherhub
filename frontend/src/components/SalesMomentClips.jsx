import React, { useState } from "react";
import VideoService from "../base/services/videoService";
import { useSectionState } from "../base/hooks/useSectionState";
import { ErrorState } from "./SectionStateUI";
import ClipEditorV2 from "./ClipEditorV2";

/**
 * SalesMomentClips
 * ================
 * 売上・注文・クリック・視聴者のスパイク（急増）を検出し、
 * その瞬間を中心にクリップ候補を表示するコンポーネント。
 *
 * Props:
 *   videoData          – 動画詳細オブジェクト（id が必須）
 *   onRequestClip      – (candidate) => void  クリップ生成をリクエストする関数
 *   clipStates         – { [phaseIndex]: { status, clip_url } }
 */
export default function SalesMomentClips({ videoData, onRequestClip, clipStates = {} }) {
  const { state, data, error, execute, retry } = useSectionState("SalesMomentClips");
  const [collapsed, setCollapsed] = useState(false);
  const [editorClip, setEditorClip] = useState(null);

  const formatTime = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "--:--";
    const s = Math.round(Number(seconds));
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const handleFetch = () => {
    if (!videoData?.id) return;
    execute(
      () => VideoService.getSalesMomentClips(videoData.id, 5),
      {
        videoId: videoData.id,
        endpoint: `/api/v1/videos/${videoData.id}/sales-moment-clips`,
        emptyCheck: (d) => !d?.candidates || d.candidates.length === 0,
      }
    );
  };

  const handleClipRequest = (candidate) => {
    if (onRequestClip) {
      onRequestClip({
        phase_index: candidate.phase_index,
        time_start: candidate.time_start,
        time_end: candidate.time_end,
        label: candidate.label,
      });
    }
  };

  const handleOpenEditor = (candidate) => {
    const clipState = getClipState(candidate);
    if (!clipState) return;
    setEditorClip({
      clip_url: clipState.clip_url,
      clip_id: clipState.clip_id || clipState.id,
      phase_index: candidate.phase_index,
      time_start: candidate.time_start,
      time_end: candidate.time_end,
      label: candidate.label,
    });
  };

  const metricIcon = (metric) => {
    switch (metric) {
      case "gmv": return "\u{1F4B0}";
      case "orders": return "\u{1F6D2}";
      case "clicks": return "\u{1F446}";
      case "viewers": return "\u{1F441}\uFE0F";
      default: return "\u{1F4CA}";
    }
  };

  const metricColor = (metric) => {
    switch (metric) {
      case "gmv": return "from-amber-500 to-orange-500";
      case "orders": return "from-green-500 to-emerald-500";
      case "clicks": return "from-blue-500 to-cyan-500";
      case "viewers": return "from-purple-500 to-pink-500";
      default: return "from-gray-500 to-gray-600";
    }
  };

  const getClipState = (candidate) => {
    return clipStates[candidate.phase_index] || null;
  };

  const isLoading = state === "loading";
  const hasData = state === "success" || state === "empty";

  return (
    <div className="mt-6">
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
        {/* ヘッダー */}
        <div className="px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-red-500 to-orange-500 flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
              </svg>
            </div>
            <div>
              <h3 className="text-sm font-bold text-gray-800 flex items-center gap-2">
                Sales Moment Clips
                <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-red-100 text-red-600">
                  Spike Detection
                </span>
              </h3>
              <p className="text-xs text-gray-500 mt-0.5">
                売上スパイクの瞬間からクリップ候補を自動生成
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {!hasData ? (
              <button
                type="button"
                onClick={handleFetch}
                disabled={isLoading}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-red-500 to-orange-500 hover:from-red-600 hover:to-orange-600 shadow-sm hover:shadow-md transition-all disabled:opacity-60"
              >
                {isLoading ? (
                  <>
                    <svg className="animate-spin" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                    </svg>
                    検出中...
                  </>
                ) : (
                  <>
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                    </svg>
                    スパイク検出
                  </>
                )}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={handleFetch}
                  disabled={isLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-red-600 bg-white border border-red-200 hover:bg-red-50 transition-colors disabled:opacity-60"
                >
                  {isLoading ? "検出中..." : "再検出"}
                </button>
                <button
                  type="button"
                  onClick={() => setCollapsed(s => !s)}
                  className="text-gray-400 p-2 rounded focus:outline-none transition-colors"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
                    className={`w-5 h-5 transform transition-transform duration-200 ${!collapsed ? "rotate-180" : ""}`}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              </>
            )}
          </div>
        </div>

        {/* エラー表示 - SectionStateUI統一 */}
        {state === "error" && (
          <div className="mx-5 mb-4">
            <ErrorState error={error} onRetry={retry} sectionName="Sales Moment Clips" compact />
          </div>
        )}

        {/* 空状態 */}
        {state === "empty" && !collapsed && (
          <div className="px-5 pb-5">
            <div className="text-center py-8 text-gray-400 text-sm">
              <div className="text-3xl mb-2">{"\u{1F4CA}"}</div>
              <div>スパイクが検出されませんでした。</div>
              <div className="mt-1 text-xs">売上データが均一な場合、スパイクは検出されません。</div>
            </div>
          </div>
        )}

        {/* 候補カード一覧 */}
        {state === "success" && data && !collapsed && (
          <div className="px-5 pb-5">
            {/* スパイク統計 */}
            <div className="flex items-center gap-3 px-3 py-2 rounded-xl bg-orange-50 border border-orange-100 text-xs mb-3">
              <span className="text-orange-600 font-semibold">
                {data.spike_count} スパイク検出
              </span>
              <span className="text-gray-400">{"\u2192"}</span>
              <span className="text-gray-600">
                {data.candidates?.length} クリップ候補
              </span>
            </div>

            <div className="space-y-3">
              {data.candidates?.map((candidate) => {
                const clipState = getClipState(candidate);

                return (
                  <div
                    key={candidate.rank}
                    className="rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-all duration-200 overflow-hidden"
                  >
                    {/* カードヘッダー */}
                    <div className={`bg-gradient-to-r ${metricColor(candidate.primary_metric)} px-4 py-2.5 flex items-center justify-between`}>
                      <div className="flex items-center gap-2">
                        <span className="text-white text-lg">{metricIcon(candidate.primary_metric)}</span>
                        <span className="text-white font-bold text-sm">{candidate.label}</span>
                        <span className="text-white/80 text-xs">
                          {formatTime(candidate.time_start)} {"\u2013"} {formatTime(candidate.time_end)}
                        </span>
                      </div>
                      <span className="bg-white/20 text-white text-xs font-bold px-2 py-0.5 rounded-full">
                        {candidate.score.toFixed(1)}pt
                      </span>
                    </div>

                    {/* カードボディ */}
                    <div className="p-3">
                      {/* サマリー */}
                      <p className="text-sm text-gray-700 font-medium mb-2">
                        {candidate.summary}
                      </p>

                      {/* スパイクイベント */}
                      {candidate.spike_events?.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 mb-3">
                          {candidate.spike_events.map((se, i) => (
                            <span
                              key={i}
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-white border border-gray-200 text-gray-600"
                            >
                              {metricIcon(se.metric)}
                              {formatTime(se.video_sec)} ({se.spike_ratio}x)
                            </span>
                          ))}
                        </div>
                      )}

                      {/* アクションボタン */}
                      <div className="flex items-center justify-end gap-2">
                        {clipState?.status === "completed" && clipState?.clip_url ? (
                          <>
                            {/* 編集ボタン */}
                            <button
                              type="button"
                              onClick={() => handleOpenEditor(candidate)}
                              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border-2 border-orange-400 text-orange-600 text-xs font-medium hover:bg-orange-50 transition-colors"
                            >
                              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                              </svg>
                              編集
                            </button>
                            {/* ダウンロードボタン */}
                            <a
                              href={clipState.clip_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-500 text-white text-xs font-medium hover:bg-green-600 transition-colors"
                            >
                              ダウンロード
                            </a>
                          </>
                        ) : clipState?.status === "generating_subtitles" ? (
                          <div className="flex-1 flex flex-col gap-1">
                            <div className="flex items-center justify-between">
                              <span className="text-purple-600 text-xs font-medium">字幕生成中...</span>
                              <span className="text-purple-500 text-xs font-bold">95%</span>
                            </div>
                            <div className="w-full h-1.5 bg-purple-100 rounded-full overflow-hidden">
                              <div className="h-full bg-gradient-to-r from-orange-500 to-red-500 rounded-full transition-all duration-500 ease-out" style={{ width: '95%' }} />
                            </div>
                          </div>
                        ) : clipState?.status === "requesting" || clipState?.status === "pending" || clipState?.status === "processing" ? (
                          (() => {
                            const pct = clipState?.progress_pct || 0;
                            const step = clipState?.progress_step || '';
                            const stepLabels = {
                              downloading: '取得中',
                              speech_boundary: '音声検出',
                              cutting: 'カット中',
                              person_detection: '人物検出',
                              silence_removal: '無音除去',
                              transcribing: '文字起こし',
                              refining_subtitles: '字幕最適化',
                              creating_clip: '動画作成',
                              uploading: 'アップロード',
                            };
                            const label = stepLabels[step] || '生成中';
                            return (
                              <div className="flex-1 flex flex-col gap-1">
                                <div className="flex items-center justify-between">
                                  <span className="text-gray-600 text-xs font-medium">{label}...</span>
                                  <span className="text-orange-600 text-xs font-bold">{pct}%</span>
                                </div>
                                <div className="w-full h-1.5 bg-gray-200 rounded-full overflow-hidden">
                                  <div
                                    className="h-full bg-gradient-to-r from-orange-500 to-red-500 rounded-full transition-all duration-700 ease-out"
                                    style={{ width: `${Math.max(pct, 2)}%` }}
                                  />
                                </div>
                              </div>
                            );
                          })()
                        ) : (
                          <button
                            type="button"
                            onClick={() => handleClipRequest(candidate)}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gradient-to-r from-red-500 to-orange-500 text-white text-xs font-medium hover:from-red-600 hover:to-orange-600 transition-all shadow-sm hover:shadow-md"
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                              <polygon points="5 3 19 12 5 21 5 3"/>
                            </svg>
                            クリップ生成
                          </button>
                        )}
                      </div>
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
          clip={editorClip}
          videoData={videoData}
          onClose={() => setEditorClip(null)}
          onClipUpdated={(res) => {
            if (res?.clip_id) {
              setEditorClip(null);
            }
          }}
        />
      )}
    </div>
  );
}
