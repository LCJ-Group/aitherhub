import { useState, useCallback, useEffect } from "react";
import VideoService from "../base/services/videoService";

/**
 * SalesClipCandidates
 * ===================
 * 「AIおすすめクリップ生成」ボタンと候補カード表示コンポーネント。
 * 採用/却下フィードバックボタンを含み、教師データを自動収集する。
 *
 * Props:
 *   videoData          – 動画詳細オブジェクト（id が必須）
 *   onRequestClip      – (candidate) => void  クリップ生成をリクエストする関数
 *   clipStates         – { [phaseIndex]: { status, clip_url } }
 */
export default function SalesClipCandidates({ videoData, onRequestClip, clipStates = {} }) {
  const [loading, setLoading] = useState(false);
  const [candidates, setCandidates] = useState(null);  // null = 未取得
  const [error, setError] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  // feedbackMap: { [phaseIndex]: "adopted" | "rejected" | "submitting" }
  const [feedbackMap, setFeedbackMap] = useState({});

  // 既存フィードバックを復元（ページロード時）
  useEffect(() => {
    if (!videoData?.id) return;
    VideoService.getClipFeedback(videoData.id).then((list) => {
      if (!Array.isArray(list)) return;
      const map = {};
      list.forEach((fb) => {
        map[fb.phase_index] = fb.feedback; // "adopted" | "rejected"
      });
      setFeedbackMap(map);
    });
  }, [videoData?.id]);

  const formatTime = (seconds) => {
    if (seconds == null || isNaN(seconds)) return "--:--";
    const s = Math.round(Number(seconds));
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const formatDuration = (sec) => {
    if (!sec || isNaN(sec)) return "";
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    if (m > 0) return `${m}分${s}秒`;
    return `${s}秒`;
  };

  const handleFetch = useCallback(async () => {
    if (!videoData?.id) return;
    setLoading(true);
    setError(null);
    try {
      const res = await VideoService.getSalesClipCandidates(videoData.id, 5);
      setCandidates(res.candidates || []);
    } catch (err) {
      setError("候補の取得に失敗しました。再試行してください。");
      console.error("[SalesClipCandidates] fetch error:", err);
    } finally {
      setLoading(false);
    }
  }, [videoData?.id]);

  const handleClipRequest = useCallback((candidate) => {
    if (onRequestClip) {
      onRequestClip(candidate);
    }
  }, [onRequestClip]);

  /**
   * 採用/却下フィードバックを送信する。
   * 同じ状態を再クリックすると取り消し（DELETE）。
   */
  const handleFeedback = useCallback(async (candidate, feedbackType) => {
    const phaseIndex = candidate.phase_index;
    const current = feedbackMap[phaseIndex];

    // 同じボタンを再クリック → 取り消し
    if (current === feedbackType) {
      setFeedbackMap(prev => ({ ...prev, [phaseIndex]: "submitting" }));
      try {
        await VideoService.deleteClipFeedback(videoData.id, phaseIndex);
        setFeedbackMap(prev => {
          const next = { ...prev };
          delete next[phaseIndex];
          return next;
        });
      } catch {
        setFeedbackMap(prev => ({ ...prev, [phaseIndex]: current }));
      }
      return;
    }

    setFeedbackMap(prev => ({ ...prev, [phaseIndex]: "submitting" }));
    try {
      await VideoService.submitClipFeedback(videoData.id, {
        phase_index: phaseIndex,
        time_start: candidate.time_start,
        time_end: candidate.time_end,
        feedback: feedbackType,
        ai_score_at_feedback: candidate.sales_score,
        score_breakdown: candidate.score_breakdown,
        ai_reasons_at_feedback: candidate.reasons,
      });
      setFeedbackMap(prev => ({ ...prev, [phaseIndex]: feedbackType }));
    } catch (err) {
      console.error("[SalesClipCandidates] feedback error:", err);
      setFeedbackMap(prev => {
        const next = { ...prev };
        delete next[phaseIndex];
        return next;
      });
    }
  }, [feedbackMap, videoData?.id]);

  // ランクに応じたグラデーション
  const rankStyle = (rank) => {
    if (rank === 1) return "from-yellow-400 to-orange-500";
    if (rank === 2) return "from-slate-400 to-slate-500";
    if (rank === 3) return "from-amber-600 to-amber-700";
    return "from-purple-400 to-pink-500";
  };

  const rankBg = (rank) => {
    if (rank === 1) return "bg-yellow-50 border-yellow-200";
    if (rank === 2) return "bg-slate-50 border-slate-200";
    if (rank === 3) return "bg-amber-50 border-amber-200";
    return "bg-purple-50 border-purple-200";
  };

  const getClipState = (candidate) => {
    return clipStates[candidate.phase_index] || null;
  };

  const renderClipButton = (candidate) => {
    const state = getClipState(candidate);

    if (state?.status === "completed" && state?.clip_url) {
      return (
        <a
          href={state.clip_url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-500 text-white text-xs font-medium hover:bg-green-600 transition-colors"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          ダウンロード
        </a>
      );
    }

    if (state?.status === "requesting" || state?.status === "pending" || state?.status === "processing") {
      return (
        <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-100 text-gray-500 text-xs font-medium cursor-not-allowed">
          <svg className="animate-spin" xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
          </svg>
          生成中...
        </span>
      );
    }

    return (
      <button
        type="button"
        onClick={() => handleClipRequest(candidate)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gradient-to-r from-purple-500 to-pink-500 text-white text-xs font-medium hover:from-purple-600 hover:to-pink-600 transition-all shadow-sm hover:shadow-md"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polygon points="5 3 19 12 5 21 5 3"/>
        </svg>
        クリップ生成
      </button>
    );
  };

  /**
   * 採用/却下フィードバックボタン
   */
  const renderFeedbackButtons = (candidate) => {
    const phaseIndex = candidate.phase_index;
    const current = feedbackMap[phaseIndex];
    const isSubmitting = current === "submitting";

    return (
      <div className="flex items-center gap-1.5">
        {/* 採用ボタン */}
        <button
          type="button"
          disabled={isSubmitting}
          onClick={() => handleFeedback(candidate, "adopted")}
          title={current === "adopted" ? "採用済み（クリックで取り消し）" : "採用する"}
          className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all border
            ${current === "adopted"
              ? "bg-green-500 text-white border-green-500 shadow-sm"
              : "bg-white text-gray-500 border-gray-200 hover:bg-green-50 hover:text-green-600 hover:border-green-300"
            }
            ${isSubmitting ? "opacity-50 cursor-not-allowed" : ""}
          `}
        >
          {isSubmitting ? (
            <svg className="animate-spin" xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
            </svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/>
              <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>
            </svg>
          )}
          {current === "adopted" ? "採用済み" : "採用"}
        </button>

        {/* 却下ボタン */}
        <button
          type="button"
          disabled={isSubmitting}
          onClick={() => handleFeedback(candidate, "rejected")}
          title={current === "rejected" ? "却下済み（クリックで取り消し）" : "却下する"}
          className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all border
            ${current === "rejected"
              ? "bg-red-500 text-white border-red-500 shadow-sm"
              : "bg-white text-gray-500 border-gray-200 hover:bg-red-50 hover:text-red-600 hover:border-red-300"
            }
            ${isSubmitting ? "opacity-50 cursor-not-allowed" : ""}
          `}
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/>
            <path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/>
          </svg>
          {current === "rejected" ? "却下済み" : "却下"}
        </button>
      </div>
    );
  };

  return (
    <div className="w-full mt-4 mx-auto">
      <div className="rounded-2xl bg-gradient-to-br from-indigo-50 to-purple-50 border border-indigo-200">
        {/* ヘッダー */}
        <div
          className="flex items-center justify-between p-5 cursor-pointer hover:bg-indigo-100/50 transition-all duration-200 rounded-t-2xl"
          onClick={() => candidates !== null && setCollapsed(s => !s)}
        >
          <div className="flex items-center gap-4">
            {/* アイコン */}
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-sm flex-shrink-0">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
              </svg>
            </div>
            <div>
              <div className="text-gray-900 text-xl font-semibold flex items-center gap-2">
                🔥 AIおすすめクリップ
                {candidates !== null && (
                  <span className="inline-flex items-center justify-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gradient-to-r from-indigo-500 to-purple-600 text-white">
                    {candidates.length}件
                  </span>
                )}
              </div>
              <div className="text-gray-500 text-sm mt-0.5">
                売上・注文・CTA から自動選定した売れるクリップ区間
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* 生成ボタン */}
            {candidates === null ? (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); handleFetch(); }}
                disabled={loading}
                className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold transition-all shadow-sm
                  ${loading
                    ? "bg-gray-100 text-gray-400 cursor-not-allowed"
                    : "bg-gradient-to-r from-indigo-500 to-purple-600 text-white hover:from-indigo-600 hover:to-purple-700 hover:shadow-md"
                  }`}
              >
                {loading ? (
                  <>
                    <svg className="animate-spin" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                    </svg>
                    分析中...
                  </>
                ) : (
                  <>
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
                    </svg>
                    AIで分析する
                  </>
                )}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); handleFetch(); }}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-indigo-600 bg-white border border-indigo-200 hover:bg-indigo-50 transition-colors"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-.08-4.56"/>
                  </svg>
                  再分析
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
        {candidates !== null && !collapsed && (
          <div className="px-5 pb-5">
            {candidates.length === 0 ? (
              <div className="text-center py-8 text-gray-400 text-sm">
                <div className="text-3xl mb-2">📊</div>
                <div>売上データが不足しているため候補を生成できませんでした。</div>
                <div className="mt-1 text-xs">動画に売上・注文データが紐付いている場合に候補が表示されます。</div>
              </div>
            ) : (
              <>
                {/* フィードバック集計バー */}
                <FeedbackSummaryBar feedbackMap={feedbackMap} total={candidates.length} />

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-3">
                  {candidates.map((candidate) => {
                    const fb = feedbackMap[candidate.phase_index];
                    const isAdopted = fb === "adopted";
                    const isRejected = fb === "rejected";

                    return (
                      <div
                        key={candidate.rank}
                        className={`rounded-xl border shadow-sm hover:shadow-md transition-all duration-200 overflow-hidden
                          ${isAdopted ? "ring-2 ring-green-400 ring-offset-1" : ""}
                          ${isRejected ? "opacity-60" : ""}
                          ${rankBg(candidate.rank)}
                        `}
                      >
                        {/* カードヘッダー */}
                        <div className={`bg-gradient-to-r ${rankStyle(candidate.rank)} px-4 py-2.5 flex items-center justify-between`}>
                          <div className="flex items-center gap-2">
                            <span className="text-white font-bold text-sm">{candidate.label}</span>
                            <span className="text-white/80 text-xs">
                              {formatTime(candidate.time_start)} – {formatTime(candidate.time_end)}
                            </span>
                          </div>
                          <div className="flex items-center gap-1.5">
                            <span className="text-white/90 text-xs font-medium">
                              {formatDuration(candidate.duration)}
                            </span>
                            {/* スコアバッジ */}
                            <span className="bg-white/20 text-white text-xs font-bold px-2 py-0.5 rounded-full">
                              {Math.round(candidate.sales_score)}pt
                            </span>
                            {/* フィードバック状態バッジ */}
                            {isAdopted && (
                              <span className="bg-green-500 text-white text-xs font-bold px-2 py-0.5 rounded-full">
                                ✓ 採用
                              </span>
                            )}
                            {isRejected && (
                              <span className="bg-red-400 text-white text-xs font-bold px-2 py-0.5 rounded-full">
                                ✕ 却下
                              </span>
                            )}
                          </div>
                        </div>

                        {/* カードボディ */}
                        <div className="p-3">
                          {/* スコアバー */}
                          <div className="mb-3">
                            <div className="flex justify-between items-center mb-1">
                              <span className="text-xs text-gray-500">Sales Score</span>
                              <span className="text-xs font-bold text-gray-700">{candidate.sales_score.toFixed(1)} / 100</span>
                            </div>
                            <div className="w-full bg-gray-200 rounded-full h-1.5">
                              <div
                                className={`h-1.5 rounded-full bg-gradient-to-r ${rankStyle(candidate.rank)}`}
                                style={{ width: `${Math.min(candidate.sales_score, 100)}%` }}
                              />
                            </div>
                          </div>

                          {/* 理由タグ */}
                          {candidate.reasons && candidate.reasons.length > 0 && (
                            <div className="flex flex-wrap gap-1.5 mb-3">
                              {candidate.reasons.map((reason, i) => (
                                <span
                                  key={i}
                                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-white border border-gray-200 text-gray-600"
                                >
                                  {i === 0 && "🏆"}
                                  {i === 1 && "📈"}
                                  {i === 2 && "👆"}
                                  {i === 3 && "👁️"}
                                  {reason}
                                </span>
                              ))}
                            </div>
                          )}

                          {/* スコア内訳（折りたたみ） */}
                          <ScoreBreakdown breakdown={candidate.score_breakdown} />

                          {/* アクションボタン行 */}
                          <div className="mt-3 flex items-center justify-between gap-2">
                            {/* 採用/却下ボタン */}
                            {renderFeedbackButtons(candidate)}
                            {/* クリップ生成ボタン */}
                            {renderClipButton(candidate)}
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

/**
 * フィードバック集計バー
 * 採用・却下・未評価の件数をまとめて表示する
 */
function FeedbackSummaryBar({ feedbackMap, total }) {
  const adopted = Object.values(feedbackMap).filter(v => v === "adopted").length;
  const rejected = Object.values(feedbackMap).filter(v => v === "rejected").length;
  const pending = total - adopted - rejected;

  if (adopted === 0 && rejected === 0) {
    return (
      <div className="text-xs text-gray-400 text-center py-1">
        各クリップに 👍 採用 / 👎 却下 を付けると AI が学習します
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 px-3 py-2 rounded-xl bg-white border border-gray-100 text-xs">
      <span className="text-gray-500 font-medium">フィードバック</span>
      {adopted > 0 && (
        <span className="flex items-center gap-1 text-green-600 font-semibold">
          <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/>
            <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>
          </svg>
          採用 {adopted}件
        </span>
      )}
      {rejected > 0 && (
        <span className="flex items-center gap-1 text-red-500 font-semibold">
          <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/>
            <path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/>
          </svg>
          却下 {rejected}件
        </span>
      )}
      {pending > 0 && (
        <span className="text-gray-400">未評価 {pending}件</span>
      )}
      <span className="ml-auto text-indigo-500 font-medium">
        AI学習データ収集中...
      </span>
    </div>
  );
}

/**
 * スコア内訳の折りたたみ表示
 */
function ScoreBreakdown({ breakdown }) {
  const [open, setOpen] = useState(false);
  if (!breakdown) return null;

  const items = [
    { key: "gmv", label: "売上", icon: "💰" },
    { key: "order", label: "注文", icon: "🛒" },
    { key: "click", label: "クリック", icon: "👆" },
    { key: "viewer", label: "視聴者", icon: "👁️" },
    { key: "moments", label: "売れた瞬間", icon: "⚡" },
    { key: "cta", label: "CTA", icon: "📢" },
    { key: "human_rating", label: "人間評価", icon: "⭐" },
    { key: "purchase_popup", label: "購入ポップアップ", icon: "🛍️" },
    { key: "price_mention", label: "価格提示", icon: "🏷️" },
  ].filter(item => breakdown[item.key] > 0);

  if (items.length === 0) return null;

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(s => !s)}
        className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 transition-colors"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          className={`w-3 h-3 transform transition-transform ${open ? "rotate-180" : ""}`}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
        スコア内訳
      </button>
      {open && (
        <div className="mt-2 space-y-1">
          {items.map(item => (
            <div key={item.key} className="flex items-center gap-2">
              <span className="text-xs w-4">{item.icon}</span>
              <span className="text-xs text-gray-500 flex-1">{item.label}</span>
              <div className="flex items-center gap-1.5">
                <div className="w-16 bg-gray-200 rounded-full h-1">
                  <div
                    className="h-1 rounded-full bg-indigo-400"
                    style={{ width: `${Math.min((breakdown[item.key] / 30) * 100, 100)}%` }}
                  />
                </div>
                <span className="text-xs font-medium text-gray-600 w-8 text-right">
                  {breakdown[item.key].toFixed(1)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
