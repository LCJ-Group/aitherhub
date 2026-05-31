import { useState, useEffect } from "react";
import { RefreshCw, ThumbsUp, ThumbsDown, RotateCcw, ArrowLeft, Sparkles, Clock, TrendingUp, Filter } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_URL || "";

export default function RegenList({ adminKey, onBack }) {
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [gradeFilter, setGradeFilter] = useState(null);
  const [gradingJob, setGradingJob] = useState(null);

  const perPage = 12;
  const totalPages = Math.ceil(total / perPage);

  useEffect(() => {
    loadRegens();
  }, [page, gradeFilter]);

  async function loadRegens() {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: page.toString(),
        per_page: perPage.toString(),
        sort_order: "desc",
      });
      if (gradeFilter) params.set("grade", gradeFilter);

      const res = await fetch(`${API_BASE}/api/v1/clip-db/regenerations?${params}`, {
        headers: { "X-Admin-Key": adminKey },
      });
      if (!res.ok) throw new Error(`API error ${res.status}`);
      const data = await res.json();
      setItems(data.items || []);
      setTotal(data.total || 0);
      setStats(data.stats || null);
    } catch (e) {
      console.error("[RegenList] Load failed:", e);
    } finally {
      setLoading(false);
    }
  }

  async function gradeItem(jobId, grade) {
    setGradingJob(jobId);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/clip-db/regenerations/${jobId}/grade?grade=${grade}`,
        { method: "POST", headers: { "X-Admin-Key": adminKey } }
      );
      if (!res.ok) throw new Error(`API error ${res.status}`);
      // Update local state
      setItems((prev) =>
        prev.map((item) =>
          item.job_id === jobId ? { ...item, grade } : item
        )
      );
      // Refresh stats
      loadRegens();
    } catch (e) {
      alert(`採点エラー: ${e.message}`);
    } finally {
      setGradingJob(null);
    }
  }

  function ScoreChange({ before, after }) {
    if (before == null || after == null) return null;
    const diff = after - before;
    const color = diff > 0 ? "text-green-600" : diff < 0 ? "text-red-600" : "text-gray-500";
    return (
      <div className="flex items-center gap-2 text-sm">
        <span className="text-orange-500 font-bold">{before.toFixed(1)}</span>
        <span className="text-gray-400">→</span>
        <span className="text-green-600 font-bold">{after.toFixed(1)}</span>
        <span className={`text-xs ${color} font-medium`}>
          ({diff > 0 ? "+" : ""}{diff.toFixed(1)})
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="p-2 rounded-lg hover:bg-gray-100 text-gray-600"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <RefreshCw className="w-5 h-5 text-blue-600" />
            再生成一覧
          </h2>
        </div>
        <button
          onClick={loadRegens}
          className="px-3 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 rounded-lg text-gray-700"
        >
          <RefreshCw className="w-3 h-3 inline mr-1" /> 更新
        </button>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
          <div className="bg-white rounded-xl border border-gray-200 p-3 text-center">
            <div className="text-xl font-bold text-gray-900">{stats.total}</div>
            <div className="text-[10px] text-gray-500">総再生成</div>
          </div>
          <div className="bg-white rounded-xl border border-green-200 p-3 text-center">
            <div className="text-xl font-bold text-green-600">{stats.ok}</div>
            <div className="text-[10px] text-gray-500">OK</div>
          </div>
          <div className="bg-white rounded-xl border border-red-200 p-3 text-center">
            <div className="text-xl font-bold text-red-600">{stats.ng}</div>
            <div className="text-[10px] text-gray-500">NG</div>
          </div>
          <div className="bg-white rounded-xl border border-yellow-200 p-3 text-center">
            <div className="text-xl font-bold text-yellow-600">{stats.retry}</div>
            <div className="text-[10px] text-gray-500">再トライ</div>
          </div>
          <div className="bg-white rounded-xl border border-purple-200 p-3 text-center">
            <div className="text-xl font-bold text-purple-600">{stats.ungraded}</div>
            <div className="text-[10px] text-gray-500">未採点</div>
          </div>
          <div className="bg-white rounded-xl border border-blue-200 p-3 text-center">
            <div className="text-xl font-bold text-blue-600">+{stats.avg_score_improvement}</div>
            <div className="text-[10px] text-gray-500">平均改善</div>
          </div>
        </div>
      )}

      {/* Grade Filter */}
      <div className="flex gap-2 flex-wrap">
        {[
          { key: null, label: "すべて", color: "gray" },
          { key: "ungraded", label: "未採点", color: "purple" },
          { key: "ok", label: "OK", color: "green" },
          { key: "ng", label: "NG", color: "red" },
          { key: "retry", label: "再トライ", color: "yellow" },
        ].map(({ key, label, color }) => (
          <button
            key={label}
            onClick={() => { setGradeFilter(key); setPage(1); }}
            className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
              gradeFilter === key
                ? `bg-${color}-100 border-${color}-400 text-${color}-700 shadow-sm`
                : "bg-white border-gray-200 text-gray-600 hover:border-gray-300"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex justify-center py-8">
          <RefreshCw className="w-6 h-6 animate-spin text-blue-500" />
        </div>
      )}

      {/* Items Grid */}
      {!loading && items.length === 0 && (
        <div className="text-center py-12 text-gray-400">
          <Sparkles className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p>再生成されたクリップがありません</p>
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="space-y-4">
          {items.map((item) => (
            <div
              key={item.job_id}
              className={`bg-white rounded-xl border p-4 ${
                item.grade === "ok" ? "border-green-200" :
                item.grade === "ng" ? "border-red-200" :
                item.grade === "retry" ? "border-yellow-200" :
                "border-gray-200"
              }`}
            >
              {/* Before / After comparison */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* BEFORE */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-orange-100 text-orange-700">BEFORE</span>
                    <span className="text-xs text-gray-400">
                      スコア: <span className="font-bold text-orange-600">{item.before?.quality_score?.toFixed(1) || "--"}</span>
                    </span>
                  </div>
                  {item.before?.thumbnail_url ? (
                    <img
                      src={item.before.thumbnail_url}
                      alt="Before"
                      className="w-full h-40 object-cover rounded-lg bg-gray-100"
                    />
                  ) : (
                    <div className="w-full h-40 bg-gray-100 rounded-lg flex items-center justify-center text-gray-400 text-xs">
                      サムネイルなし
                    </div>
                  )}
                  <div className="text-xs text-gray-500 space-y-1">
                    {item.before?.product_name && <div>商品: {item.before.product_name}</div>}
                    {item.before?.liver_name && <div>ライバー: {item.before.liver_name}</div>}
                    {item.before?.duration_sec && <div>長さ: {item.before.duration_sec}秒</div>}
                    {item.before?.transcript_text && (
                      <div className="text-[10px] text-gray-400 line-clamp-2">{item.before.transcript_text}</div>
                    )}
                  </div>
                </div>

                {/* AFTER */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-green-100 text-green-700">AFTER</span>
                    <span className="text-xs text-gray-400">
                      スコア: <span className="font-bold text-green-600">{item.after?.quality_score?.toFixed(1) || "--"}</span>
                    </span>
                    <ScoreChange before={item.before?.quality_score} after={item.after?.quality_score} />
                  </div>
                  {item.after?.download_url ? (
                    <video
                      src={item.after.download_url}
                      className="w-full h-40 object-cover rounded-lg bg-gray-900"
                      controls
                      preload="metadata"
                    />
                  ) : (
                    <div className="w-full h-40 bg-gray-900 rounded-lg flex items-center justify-center text-gray-400 text-xs">
                      動画なし
                    </div>
                  )}
                  <div className="text-xs text-gray-500 space-y-1">
                    {item.after?.duration_sec && <div>長さ: {item.after.duration_sec.toFixed(1)}秒</div>}
                    {item.after?.hook_text && <div>フック: {item.after.hook_text}</div>}
                    {item.after?.cta_text && <div>CTA: {item.after.cta_text}</div>}
                    {item.after?.captions_count > 0 && <div>字幕: {item.after.captions_count}件</div>}
                    {item.after?.effects_applied && (
                      <div className="flex gap-1 flex-wrap">
                        {item.after.effects_applied.subtitle_style && (
                          <span className="px-1.5 py-0.5 bg-blue-50 text-blue-600 rounded text-[9px]">
                            字幕: {item.after.effects_applied.subtitle_style}
                          </span>
                        )}
                        {item.after.effects_applied.overlays > 0 && (
                          <span className="px-1.5 py-0.5 bg-purple-50 text-purple-600 rounded text-[9px]">
                            オーバーレイ: {item.after.effects_applied.overlays}
                          </span>
                        )}
                      </div>
                    )}
                    {item.after?.transcript_text && (
                      <div className="text-[10px] text-gray-400 line-clamp-2">{item.after.transcript_text}</div>
                    )}
                  </div>
                </div>
              </div>

              {/* Footer: Grade buttons + metadata */}
              <div className="mt-4 pt-3 border-t border-gray-100 flex items-center justify-between">
                <div className="flex items-center gap-2 text-xs text-gray-400">
                  <Clock className="w-3 h-3" />
                  {item.created_at ? new Date(item.created_at).toLocaleString("ja-JP") : "--"}
                  {item.current_version && (
                    <span className="px-1.5 py-0.5 bg-blue-50 text-blue-600 rounded text-[9px] font-medium">
                      {item.current_version}
                    </span>
                  )}
                </div>

                {/* Grade buttons */}
                <div className="flex items-center gap-2">
                  {item.grade && (
                    <span className={`px-2 py-1 rounded text-xs font-bold ${
                      item.grade === "ok" ? "bg-green-100 text-green-700" :
                      item.grade === "ng" ? "bg-red-100 text-red-700" :
                      "bg-yellow-100 text-yellow-700"
                    }`}>
                      {item.grade === "ok" ? "✅ OK" : item.grade === "ng" ? "❌ NG" : "🔄 再トライ"}
                    </span>
                  )}
                  <button
                    onClick={() => gradeItem(item.job_id, "ok")}
                    disabled={gradingJob === item.job_id}
                    className={`p-2 rounded-lg border transition-all ${
                      item.grade === "ok"
                        ? "bg-green-100 border-green-400 text-green-700"
                        : "bg-white border-gray-200 text-gray-500 hover:border-green-400 hover:bg-green-50"
                    }`}
                    title="OK - 品質良好"
                  >
                    <ThumbsUp className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => gradeItem(item.job_id, "ng")}
                    disabled={gradingJob === item.job_id}
                    className={`p-2 rounded-lg border transition-all ${
                      item.grade === "ng"
                        ? "bg-red-100 border-red-400 text-red-700"
                        : "bg-white border-gray-200 text-gray-500 hover:border-red-400 hover:bg-red-50"
                    }`}
                    title="NG - 品質不良"
                  >
                    <ThumbsDown className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => gradeItem(item.job_id, "retry")}
                    disabled={gradingJob === item.job_id}
                    className={`p-2 rounded-lg border transition-all ${
                      item.grade === "retry"
                        ? "bg-yellow-100 border-yellow-400 text-yellow-700"
                        : "bg-white border-gray-200 text-gray-500 hover:border-yellow-400 hover:bg-yellow-50"
                    }`}
                    title="再トライ - パラメータ変更して再生成"
                  >
                    <RotateCcw className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center gap-2 mt-4">
          <button
            onClick={() => setPage(Math.max(1, page - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 rounded-lg border text-sm disabled:opacity-50"
          >
            前へ
          </button>
          <span className="px-3 py-1.5 text-sm text-gray-600">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage(Math.min(totalPages, page + 1))}
            disabled={page === totalPages}
            className="px-3 py-1.5 rounded-lg border text-sm disabled:opacity-50"
          >
            次へ
          </button>
        </div>
      )}
    </div>
  );
}
