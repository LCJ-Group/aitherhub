import React, { useState } from "react";
import VideoService from "../base/services/videoService";

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
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [collapsed, setCollapsed] = useState(false);

  const formatTime = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "--:--";
    const s = Math.round(Number(seconds));
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const handleFetch = async () => {
    if (!videoData?.id) return;
    setLoading(true);
    setError(null);
    try {
      const result = await VideoService.getSalesMomentClips(videoData.id, 5);
      setData(result);
    } catch (err) {
      setError(err?.message || "スパイク検出に失敗しました");
    } finally {
      setLoading(false);
    }
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

  const metricIcon = (metric) => {
    switch (metric) {
      case "gmv": return "💰";
      case "orders": return "🛒";
      case "clicks": return "👆";
      case "viewers": return "👁️";
      default: return "📊";
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
            {!data ? (
              <button
                type="button"
                onClick={handleFetch}
                disabled={loading}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-red-500 to-orange-500 hover:from-red-600 hover:to-orange-600 shadow-sm hover:shadow-md transition-all disabled:opacity-60"
              >
                {loading ? (
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
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-red-600 bg-white border border-red-200 hover:bg-red-50 transition-colors"
                >
                  再検出
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

        {/* エラー表示 */}
        {error && (
          <div className="mx-5 mb-4 px-4 py-3 rounded-xl bg-red-50 border border-red-200 text-red-600 text-sm">
            {error}
          </div>
        )}

        {/* 候補カード一覧 */}
        {data && !collapsed && (
          <div className="px-5 pb-5">
            {data.candidates?.length === 0 ? (
              <div className="text-center py-8 text-gray-400 text-sm">
                <div className="text-3xl mb-2">📊</div>
                <div>スパイクが検出されませんでした。</div>
                <div className="mt-1 text-xs">売上データが均一な場合、スパイクは検出されません。</div>
              </div>
            ) : (
              <>
                {/* スパイク統計 */}
                <div className="flex items-center gap-3 px-3 py-2 rounded-xl bg-orange-50 border border-orange-100 text-xs mb-3">
                  <span className="text-orange-600 font-semibold">
                    {data.spike_count} スパイク検出
                  </span>
                  <span className="text-gray-400">→</span>
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
                              {formatTime(candidate.time_start)} – {formatTime(candidate.time_end)}
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
                              <a
                                href={clipState.clip_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-500 text-white text-xs font-medium hover:bg-green-600 transition-colors"
                              >
                                ダウンロード
                              </a>
                            ) : clipState?.status === "requesting" || clipState?.status === "pending" || clipState?.status === "processing" ? (
                              <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-100 text-gray-500 text-xs font-medium cursor-not-allowed">
                                <svg className="animate-spin" xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                                  <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                                </svg>
                                生成中...
                              </span>
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
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
