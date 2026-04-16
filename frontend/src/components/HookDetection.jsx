import React, { useState } from "react";
import VideoService from "../base/services/videoService";
import { useSectionState } from "../base/hooks/useSectionState";
import { ErrorState } from "./SectionStateUI";

/**
 * HookDetection
 * =============
 * TikTok / Reels 向けに「最初3秒」で視聴者を引き付ける
 * フック（Hook）を検出して表示するコンポーネント。
 *
 * Props:
 *   videoData          – 動画詳細オブジェクト（id が必須）
 *   onSelectHook       – (hook) => void  フック選択時のコールバック
 */
export default function HookDetection({ videoData, onSelectHook }) {
  const { state, data, error, execute, retry } = useSectionState("HookDetection");
  const [collapsed, setCollapsed] = useState(false);

  const formatTime = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "--:--";
    const s = Math.round(Number(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const handleFetch = () => {
    if (!videoData?.id) return;
    execute(
      () => VideoService.getHookDetection(videoData.id, 10),
      {
        videoId: videoData.id,
        endpoint: `/api/v1/videos/${videoData.id}/hook-detection`,
        emptyCheck: (d) => !d?.hooks || d.hooks.length === 0,
      }
    );
  };

  const scoreColor = (score) => {
    if (score >= 60) return "text-red-600 bg-red-50 border-red-200";
    if (score >= 40) return "text-orange-600 bg-orange-50 border-orange-200";
    if (score >= 20) return "text-yellow-600 bg-yellow-50 border-yellow-200";
    return "text-gray-600 bg-gray-50 border-gray-200";
  };

  const scoreBadge = (score) => {
    if (score >= 60) return "bg-red-500";
    if (score >= 40) return "bg-orange-500";
    if (score >= 20) return "bg-yellow-500";
    return "bg-gray-400";
  };

  const isLoading = state === "loading";
  const hasData = state === "success" || state === "empty";

  return (
    <div className="mt-6">
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
        {/* ヘッダー */}
        <div className="px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-yellow-400 to-red-500 flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                <path d="M2 17l10 5 10-5"/>
                <path d="M2 12l10 5 10-5"/>
              </svg>
            </div>
            <div>
              <h3 className="text-sm font-bold text-gray-800 flex items-center gap-2">
                Hook Detection
                <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-yellow-100 text-yellow-700">
                  TikTok最適化
                </span>
              </h3>
              <p className="text-xs text-gray-500 mt-0.5">
                最初3秒で視聴者を引き付けるフレーズを検出
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {!hasData ? (
              <button
                type="button"
                onClick={handleFetch}
                disabled={isLoading}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-yellow-500 to-red-500 hover:from-yellow-600 hover:to-red-600 shadow-sm hover:shadow-md transition-all disabled:opacity-60"
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
                      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                    </svg>
                    フック検出
                  </>
                )}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={handleFetch}
                  disabled={isLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-yellow-600 bg-white border border-yellow-200 hover:bg-yellow-50 transition-colors disabled:opacity-60"
                >
                  {isLoading ? window.__t('auto_346', '検出中...') : window.__t('videoDetail_reDetect', '再検出')}
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
            <ErrorState error={error} onRetry={retry} sectionName="Hook Detection" compact />
          </div>
        )}

        {/* 空状態 */}
        {state === "empty" && !collapsed && (
          <div className="px-5 pb-5">
            <div className="text-center py-8 text-gray-400 text-sm">
              <div className="text-3xl mb-2">🎣</div>
              <div>{window.__t('auto_321', 'フック候補が検出されませんでした。')}</div>
              <div className="mt-1 text-xs">{window.__t('auto_318', 'トランスクリプトに強いキーワードや疑問文が含まれていない場合があります。')}</div>
            </div>
          </div>
        )}

        {/* フック候補一覧 */}
        {state === "success" && data && !collapsed && (
          <div className="px-5 pb-5">
            {/* 配置提案 */}
            {data.placement_suggestion?.should_reorder && (
              <div className="mb-3 px-4 py-3 rounded-xl bg-gradient-to-r from-yellow-50 to-orange-50 border border-yellow-200">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-yellow-600 font-bold text-xs">{window.__t('auto_358', '💡 配置提案')}</span>
                </div>
                <p className="text-sm text-gray-700">
                  {data.placement_suggestion.reason}
                </p>
                {data.placement_suggestion.best_hook_text && (
                  <p className="text-xs text-gray-500 mt-1">
                    推奨開始: {formatTime(data.placement_suggestion.suggested_start)}
                  </p>
                )}
              </div>
            )}

            {/* 統計 */}
            <div className="flex items-center gap-3 px-3 py-2 rounded-xl bg-yellow-50 border border-yellow-100 text-xs mb-3">
              <span className="text-yellow-700 font-semibold">
                {data.hook_count} フック検出
              </span>
            </div>

            {/* フック候補リスト */}
            <div className="space-y-2">
              {data.hooks?.map((hook, idx) => (
                <div
                  key={idx}
                  className={`rounded-xl border p-3 cursor-pointer hover:shadow-md transition-all ${scoreColor(hook.hook_score)}`}
                  onClick={() => onSelectHook && onSelectHook(hook)}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1">
                      {/* フレーズテキスト */}
                      <p className="text-sm font-bold leading-relaxed">
                        「{hook.text.length > 60 ? hook.text.slice(0, 60) + '...' : hook.text}」
                      </p>

                      {/* 時間 + 理由タグ */}
                      <div className="flex flex-wrap items-center gap-1.5 mt-2">
                        <span className="text-xs text-gray-500">
                          {formatTime(hook.start_sec)} – {formatTime(hook.end_sec)}
                        </span>
                        {hook.hook_reasons?.map((reason, i) => (
                          <span
                            key={i}
                            className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-white/80 border border-current/20"
                          >
                            {reason}
                          </span>
                        ))}
                      </div>
                    </div>

                    {/* スコアバッジ */}
                    <div className="flex flex-col items-center gap-1">
                      <span className={`${scoreBadge(hook.hook_score)} text-white text-xs font-bold px-2.5 py-1 rounded-full`}>
                        {hook.hook_score}pt
                      </span>
                      <div className="flex items-center gap-0.5">
                        {hook.is_question && (
                          <span className="text-[10px] font-bold text-purple-500" title={window.__t('auto_348', '疑問文')}>Q</span>
                        )}
                        {hook.has_number && (
                          <span className="text-[10px] font-bold text-blue-500" title={window.__t('auto_343', '数字を含む')}>#</span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
