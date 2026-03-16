import React, { useState, useEffect } from "react";
import VideoService from "../base/services/videoService";
import { useSectionState } from "../base/hooks/useSectionState";
import { ErrorState } from "./SectionStateUI";
import AutoZoomPreview from "./AutoZoomPreview";
import ClipEditorV2 from "./ClipEditorV2";

/**
 * MomentClips
 * ===========
 * Moment-based Clipping UI
 *
 * ライブコマース画面収録から検出された各種モーメントを
 * カテゴリ別タブで表示し、ワンクリックでクリップ生成できるUI。
 *
 * カテゴリ:
 *   - Purchase Popup Clips     (購入ポップアップ)
 *   - Comment Explosion Clips  (コメント爆発)
 *   - Viewer Spike Clips       (視聴者急増)
 *   - Gift / Like Animation    (ギフト・いいね)
 *   - Product Reveal Clips     (商品見せ)
 *   - Chat Highlight Clips     (購入コメント集中)
 *   - Product Viewers Clips    (商品閲覧者)
 *
 * Props:
 *   videoData     – 動画詳細オブジェクト（id, duration が必須）
 *   onRequestClip – (clipData) => void  クリップ生成をリクエストする関数
 *   clipStates    – { [clipKey]: { status, clip_url } }
 */

const CATEGORY_CONFIG = {
  purchase_popup: {
    icon: "🛒",
    gradient: "from-amber-500 to-orange-500",
    bg: "bg-amber-50",
    border: "border-amber-200",
    text: "text-amber-700",
    badge: "bg-amber-100 text-amber-600",
  },
  comment_spike: {
    icon: "💬",
    gradient: "from-blue-500 to-cyan-500",
    bg: "bg-blue-50",
    border: "border-blue-200",
    text: "text-blue-700",
    badge: "bg-blue-100 text-blue-600",
  },
  viewer_spike: {
    icon: "👁️",
    gradient: "from-purple-500 to-pink-500",
    bg: "bg-purple-50",
    border: "border-purple-200",
    text: "text-purple-700",
    badge: "bg-purple-100 text-purple-600",
  },
  gift_animation: {
    icon: "🎁",
    gradient: "from-pink-500 to-rose-500",
    bg: "bg-pink-50",
    border: "border-pink-200",
    text: "text-pink-700",
    badge: "bg-pink-100 text-pink-600",
  },
  product_reveal: {
    icon: "📦",
    gradient: "from-green-500 to-emerald-500",
    bg: "bg-green-50",
    border: "border-green-200",
    text: "text-green-700",
    badge: "bg-green-100 text-green-600",
  },
  chat_purchase_highlight: {
    icon: "🗨️",
    gradient: "from-indigo-500 to-violet-500",
    bg: "bg-indigo-50",
    border: "border-indigo-200",
    text: "text-indigo-700",
    badge: "bg-indigo-100 text-indigo-600",
  },
  product_viewers_popup: {
    icon: "👥",
    gradient: "from-gray-500 to-slate-500",
    bg: "bg-gray-50",
    border: "border-gray-200",
    text: "text-gray-700",
    badge: "bg-gray-100 text-gray-600",
  },
  strong: {
    icon: "📈",
    gradient: "from-red-500 to-orange-500",
    bg: "bg-red-50",
    border: "border-red-200",
    text: "text-red-700",
    badge: "bg-red-100 text-red-600",
  },
  weak: {
    icon: "📊",
    gradient: "from-yellow-500 to-amber-500",
    bg: "bg-yellow-50",
    border: "border-yellow-200",
    text: "text-yellow-700",
    badge: "bg-yellow-100 text-yellow-600",
  },
};

export default function MomentClips({ videoData, onRequestClip, clipStates = {} }) {
  const { state, data, error, execute, retry, setData } = useSectionState("MomentClips");
  const [activeTab, setActiveTab] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  const [autoLoaded, setAutoLoaded] = useState(false);
  const [editorClip, setEditorClip] = useState(null);

  const formatTime = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "--:--";
    const s = Math.round(Number(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const formatDuration = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "--s";
    return `${Math.round(Number(seconds))}s`;
  };

  const handleFetch = () => {
    if (!videoData?.id) return;
    execute(
      () => VideoService.getMomentClips(videoData.id),
      {
        videoId: videoData.id,
        endpoint: `/api/v1/videos/${videoData.id}/moment-clips`,
        emptyCheck: (d) => !d?.categories || d.categories.length === 0,
      }
    ).then(({ data: result }) => {
      if (result?.categories?.length > 0) {
        setActiveTab(result.categories[0].category);
      }
    });
  };

  // 画面収録の場合は自動ロード
  useEffect(() => {
    if (videoData?.id && videoData?.upload_type === "screen_recording" && !autoLoaded) {
      setAutoLoaded(true);
      handleFetch();
    }
  }, [videoData?.id, videoData?.upload_type]);

  const handleClipRequest = (clip, category) => {
    if (onRequestClip) {
      onRequestClip({
        phase_index: `moment_${category}_${clip.id}`,
        time_start: clip.time_start,
        time_end: clip.time_end,
        label: `${CATEGORY_CONFIG[category]?.icon || "📊"} ${clip.video_sec ? formatTime(clip.video_sec) : ""}`,
        moment_category: category,
        frame_meta: clip.frame_meta,
      });
    }
  };

  const getClipKey = (clip, category) => `moment_${category}_${clip.id}`;

  const activeCategory = data?.categories?.find((c) => c.category === activeTab);
  const isLoading = state === "loading";
  const hasData = state === "success" || state === "empty";

  return (
    <div className="mt-6">
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
        {/* ヘッダー */}
        <div className="px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/>
                <polygon points="10 8 16 12 10 16 10 8"/>
              </svg>
            </div>
            <div>
              <h3 className="text-sm font-bold text-gray-800 flex items-center gap-2">
                Moment Clips
                <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-violet-100 text-violet-600">
                  AI Auto-Detect
                </span>
              </h3>
              <p className="text-xs text-gray-500 mt-0.5">
                ライブの盛り上がり瞬間を自動検出してクリップ候補を生成
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {!hasData ? (
              <button
                type="button"
                onClick={handleFetch}
                disabled={isLoading}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-violet-500 to-fuchsia-500 hover:from-violet-600 hover:to-fuchsia-600 shadow-sm hover:shadow-md transition-all disabled:opacity-60"
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
                      <circle cx="12" cy="12" r="10"/>
                      <polygon points="10 8 16 12 10 16 10 8"/>
                    </svg>
                    モーメント検出
                  </>
                )}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={handleFetch}
                  disabled={isLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-violet-600 bg-white border border-violet-200 hover:bg-violet-50 transition-colors disabled:opacity-60"
                >
                  {isLoading ? "検出中..." : "再検出"}
                </button>
                <button
                  type="button"
                  onClick={() => setCollapsed((s) => !s)}
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
            <ErrorState error={error} onRetry={retry} sectionName="Moment Clips" compact />
          </div>
        )}

        {/* 空状態 */}
        {state === "empty" && !collapsed && (
          <div className="px-5 pb-5">
            <div className="text-center py-8 text-gray-400 text-sm">
              <div className="text-3xl mb-2">🔍</div>
              <div>モーメントが検出されませんでした。</div>
              <div className="mt-1 text-xs">画面収録の解析が完了していない可能性があります。</div>
            </div>
          </div>
        )}

        {/* メインコンテンツ */}
        {state === "success" && data && !collapsed && (
          <div className="px-5 pb-5">
            {/* サマリー統計 */}
            <div className="flex items-center gap-3 px-3 py-2 rounded-xl bg-violet-50 border border-violet-100 text-xs mb-4">
              <span className="text-violet-600 font-semibold">
                {data.total_moments} モーメント検出
              </span>
              <span className="text-gray-400">|</span>
              <span className="text-gray-600">
                {data.categories?.length} カテゴリ
              </span>
              <span className="text-gray-400">|</span>
              <span className="text-gray-600">
                {data.categories?.reduce((sum, c) => sum + c.count, 0)} クリップ候補
              </span>
              {data.auto_zoom_data?.length > 0 && (
                <>
                  <span className="text-gray-400">|</span>
                  <span className="text-emerald-600 font-medium">
                    Auto Zoom対応
                  </span>
                </>
              )}
            </div>

            {/* カテゴリタブ */}
            <div className="flex flex-wrap gap-2 mb-4">
              {data.categories?.map((cat) => {
                const config = CATEGORY_CONFIG[cat.category] || CATEGORY_CONFIG.product_viewers_popup;
                const isActive = activeTab === cat.category;
                return (
                  <button
                    key={cat.category}
                    type="button"
                    onClick={() => setActiveTab(cat.category)}
                    className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all duration-200 ${
                      isActive
                        ? `bg-gradient-to-r ${config.gradient} text-white shadow-md`
                        : `${config.bg} ${config.border} border ${config.text} hover:shadow-sm`
                    }`}
                  >
                    <span>{config.icon}</span>
                    <span>{cat.label}</span>
                    <span className={`ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-bold ${
                      isActive ? "bg-white/20 text-white" : config.badge
                    }`}>
                      {cat.count}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* アクティブカテゴリのクリップ一覧 */}
            {activeCategory && (
              <div className="space-y-3">
                {/* カテゴリ説明 */}
                <div className={`px-3 py-2 rounded-lg ${CATEGORY_CONFIG[activeCategory.category]?.bg || "bg-gray-50"} ${CATEGORY_CONFIG[activeCategory.category]?.border || "border-gray-200"} border text-xs ${CATEGORY_CONFIG[activeCategory.category]?.text || "text-gray-600"}`}>
                  {activeCategory.description}
                </div>

                {/* クリップカード */}
                {activeCategory.clips?.map((clip) => {
                  const config = CATEGORY_CONFIG[activeCategory.category] || CATEGORY_CONFIG.product_viewers_popup;
                  const clipKey = getClipKey(clip, activeCategory.category);
                  const clipState = clipStates[clipKey] || null;

                  return (
                    <div
                      key={clip.id}
                      className="rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-all duration-200 overflow-hidden"
                    >
                      {/* クリップヘッダー */}
                      <div className={`bg-gradient-to-r ${config.gradient} px-4 py-2.5 flex items-center justify-between`}>
                        <div className="flex items-center gap-2">
                          <span className="text-white text-lg">{config.icon}</span>
                          <span className="text-white font-bold text-sm">
                            #{clip.id}
                          </span>
                          <span className="text-white/80 text-xs">
                            {formatTime(clip.time_start)} – {formatTime(clip.time_end)}
                          </span>
                          <span className="text-white/70 text-xs">
                            ({formatDuration(clip.duration)})
                          </span>
                        </div>
                        <div className="flex items-center gap-2">
                          {clip.moment_count > 1 && (
                            <span className="bg-white/20 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                              {clip.moment_count}件
                            </span>
                          )}
                          <span className="bg-white/20 text-white text-xs font-bold px-2 py-0.5 rounded-full">
                            {(clip.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                      </div>

                      {/* クリップボディ */}
                      <div className="p-3">
                        {/* メトリクス */}
                        <div className="flex flex-wrap gap-2 mb-2">
                          {clip.order_value > 0 && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 border border-amber-200 text-amber-700">
                              🛒 購入 {clip.order_value}件
                            </span>
                          )}
                          {clip.click_value > 0 && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 border border-blue-200 text-blue-700">
                              👆 クリック {clip.click_value}
                            </span>
                          )}
                          {clip.frame_meta?.face_region && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 border border-green-200 text-green-700">
                              🎯 Auto Zoom対応
                            </span>
                          )}
                        </div>

                        {/* 理由 */}
                        {clip.reasons?.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 mb-3">
                            {clip.reasons.map((reason, i) => (
                              <span
                                key={i}
                                className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-white border border-gray-200 text-gray-600"
                              >
                                {reason}
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
                                onClick={() => setEditorClip({
                                  clip_url: clipState.clip_url,
                                  clip_id: clipState.clip_id || clipState.id,
                                  phase_index: clipKey,
                                  time_start: clip.time_start,
                                  time_end: clip.time_end,
                                })}
                                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border-2 ${config.border} ${config.text} text-xs font-medium hover:bg-gray-50 transition-colors`}
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
                                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                  <polyline points="7 10 12 15 17 10"/>
                                  <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
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
                                <div className="h-full bg-gradient-to-r from-purple-500 to-pink-500 rounded-full transition-all duration-500 ease-out" style={{ width: '95%' }} />
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
                                    <span className="text-purple-600 text-xs font-bold">{pct}%</span>
                                  </div>
                                  <div className="w-full h-1.5 bg-gray-200 rounded-full overflow-hidden">
                                    <div
                                      className="h-full bg-gradient-to-r from-purple-500 to-pink-500 rounded-full transition-all duration-700 ease-out"
                                      style={{ width: `${Math.max(pct, 2)}%` }}
                                    />
                                  </div>
                                </div>
                              );
                            })()
                          ) : (
                            <button
                              type="button"
                              onClick={() => handleClipRequest(clip, activeCategory.category)}
                              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gradient-to-r ${config.gradient} text-white text-xs font-medium hover:shadow-md transition-all shadow-sm`}
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
            )}
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

      {/* Auto Zoom Preview */}
      {data?.auto_zoom_data?.length > 0 && !collapsed && (
        <AutoZoomPreview
          autoZoomData={data.auto_zoom_data}
          videoData={videoData}
          onApplyZoom={(zoomConfig) => {
            if (onRequestClip) {
              onRequestClip({
                phase_index: `auto_zoom_${zoomConfig.video_sec}`,
                time_start: Math.max(0, zoomConfig.video_sec - 10),
                time_end: zoomConfig.video_sec + 10,
                label: `🔍 Auto Zoom ${formatTime(zoomConfig.video_sec)}`,
                zoom_config: zoomConfig,
              });
            }
          }}
        />
      )}
    </div>
  );
}
