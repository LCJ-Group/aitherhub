import { useState, useEffect } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL;

/**
 * AdminScriptGenerations - Admin panel for viewing script generation history & ratings.
 * Shows stats, list of generations, and detailed view with full script + rating info.
 */
export default function AdminScriptGenerations({ adminKey }) {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [ratedOnly, setRatedOnly] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [page, setPage] = useState(0);
  const LIMIT = 20;

  useEffect(() => {
    fetchList();
  }, [ratedOnly, page]);

  const fetchList = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(
        `${API_BASE}/api/v1/script-generator/admin/generations`,
        {
          headers: { "X-Admin-Key": adminKey },
          params: { limit: LIMIT, offset: page * LIMIT, rated_only: ratedOnly },
        }
      );
      setData(res.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  const fetchDetail = async (id) => {
    setDetailLoading(true);
    setSelectedId(id);
    try {
      const res = await axios.get(
        `${API_BASE}/api/v1/script-generator/admin/generations/${id}`,
        { headers: { "X-Admin-Key": adminKey } }
      );
      setDetail(res.data);
    } catch (e) {
      console.error("Failed to fetch detail:", e);
    } finally {
      setDetailLoading(false);
    }
  };

  const renderStars = (rating) => {
    if (!rating) return <span className="text-gray-400 text-xs">未評価</span>;
    return (
      <span className="flex gap-0.5">
        {[1, 2, 3, 4, 5].map((s) => (
          <span
            key={s}
            className={`text-sm ${s <= rating ? "text-yellow-400" : "text-gray-300"}`}
          >
            ★
          </span>
        ))}
      </span>
    );
  };

  const formatDate = (iso) => {
    if (!iso) return "-";
    const d = new Date(iso);
    return d.toLocaleDateString("ja-JP", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  // ── Detail View ──
  if (selectedId) {
    return (
      <div>
        <button
          onClick={() => { setSelectedId(null); setDetail(null); }}
          className="mb-4 text-sm text-blue-600 hover:text-blue-800 flex items-center gap-1"
        >
          ← 一覧に戻る
        </button>

        {detailLoading ? (
          <div className="text-center py-8 text-gray-500">読み込み中...</div>
        ) : detail ? (
          <div className="space-y-4">
            {/* Header */}
            <div className="bg-white rounded-xl border border-gray-200 p-4">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="text-lg font-bold text-gray-900">{detail.product_name}</h3>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {detail.user_email} | {formatDate(detail.created_at)} | {detail.model_used}
                  </p>
                </div>
                <div className="text-right">
                  {renderStars(detail.rating)}
                  {detail.rated_at && (
                    <p className="text-xs text-gray-400 mt-0.5">評価日: {formatDate(detail.rated_at)}</p>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                {detail.original_price && (
                  <div className="bg-gray-50 px-3 py-2 rounded">
                    <span className="text-gray-500">販売価格:</span>{" "}
                    <span className="font-medium">{detail.original_price}</span>
                  </div>
                )}
                {detail.discounted_price && (
                  <div className="bg-red-50 px-3 py-2 rounded">
                    <span className="text-red-500">割引後:</span>{" "}
                    <span className="font-medium text-red-600">{detail.discounted_price}</span>
                  </div>
                )}
                <div className="bg-blue-50 px-3 py-2 rounded">
                  <span className="text-blue-500">文字数:</span>{" "}
                  <span className="font-medium">{detail.char_count}</span>
                </div>
                <div className="bg-purple-50 px-3 py-2 rounded">
                  <span className="text-purple-500">長さ:</span>{" "}
                  <span className="font-medium">{detail.duration_minutes}分</span>
                </div>
              </div>

              {detail.benefits && (
                <div className="mt-3 text-xs bg-yellow-50 px-3 py-2 rounded">
                  <span className="text-yellow-600 font-medium">特典:</span> {detail.benefits}
                </div>
              )}
              {detail.target_audience && (
                <div className="mt-2 text-xs bg-green-50 px-3 py-2 rounded">
                  <span className="text-green-600 font-medium">ターゲット:</span> {detail.target_audience}
                </div>
              )}
              {detail.product_description && (
                <div className="mt-2 text-xs bg-gray-50 px-3 py-2 rounded">
                  <span className="text-gray-500 font-medium">商品説明:</span> {detail.product_description}
                </div>
              )}
            </div>

            {/* Rating Details */}
            {detail.rating && (
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">評価詳細</h4>
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    {renderStars(detail.rating)}
                    <span className="text-sm font-medium text-gray-700">{detail.rating}/5</span>
                  </div>
                  {detail.rating_good_tags?.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {detail.rating_good_tags.map((tag, i) => (
                        <span key={i} className="px-2 py-0.5 bg-green-100 text-green-700 rounded-full text-xs">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                  {detail.rating_bad_tags?.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {detail.rating_bad_tags.map((tag, i) => (
                        <span key={i} className="px-2 py-0.5 bg-red-100 text-red-700 rounded-full text-xs">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                  {detail.rating_comment && (
                    <div className="bg-gray-50 px-3 py-2 rounded text-sm text-gray-700">
                      {detail.rating_comment}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Generated Script */}
            <div className="bg-white rounded-xl border border-gray-200 p-4">
              <h4 className="text-sm font-semibold text-gray-700 mb-3">生成された台本</h4>
              <div className="max-h-[500px] overflow-y-auto bg-gray-50 rounded-lg p-4 text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
                {detail.generated_script}
              </div>
            </div>

            {/* Patterns Used */}
            {detail.patterns_used && (
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">使用パターン</h4>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
                  <div className="bg-gray-50 px-3 py-2 rounded">
                    配信データ: {detail.patterns_used.cross_video_patterns ? "反映" : "未使用"}
                  </div>
                  <div className="bg-gray-50 px-3 py-2 rounded">
                    分析動画数: {detail.patterns_used.videos_in_cross_analysis || 0}本
                  </div>
                  <div className="bg-gray-50 px-3 py-2 rounded">
                    CTAパターン: {detail.patterns_used.cta_patterns_found || 0}
                  </div>
                  <div className="bg-gray-50 px-3 py-2 rounded">
                    フィードバック: {detail.patterns_used.feedback_knowledge_used ? "反映" : "未使用"}
                  </div>
                  <div className="bg-gray-50 px-3 py-2 rounded">
                    画像分析: {detail.patterns_used.images_analyzed_count || 0}枚
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-center py-8 text-red-500">データの取得に失敗しました</div>
        )}
      </div>
    );
  }

  // ── List View ──
  return (
    <div>
      {/* Stats Cards */}
      {data?.stats && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-6">
          <div className="bg-white rounded-lg border border-gray-200 p-3 text-center">
            <div className="text-2xl font-bold text-gray-800">{data.stats.total_generated}</div>
            <div className="text-xs text-gray-500">総生成数</div>
          </div>
          <div className="bg-white rounded-lg border border-gray-200 p-3 text-center">
            <div className="text-2xl font-bold text-blue-600">{data.stats.total_rated}</div>
            <div className="text-xs text-gray-500">評価済み</div>
          </div>
          <div className="bg-white rounded-lg border border-gray-200 p-3 text-center">
            <div className="text-2xl font-bold text-yellow-500">
              {data.stats.avg_rating ? `${data.stats.avg_rating}` : "-"}
            </div>
            <div className="text-xs text-gray-500">平均評価</div>
          </div>
          <div className="bg-white rounded-lg border border-gray-200 p-3 text-center">
            <div className="text-2xl font-bold text-green-600">{data.stats.good_count}</div>
            <div className="text-xs text-gray-500">高評価 (4-5★)</div>
          </div>
          <div className="bg-white rounded-lg border border-gray-200 p-3 text-center">
            <div className="text-2xl font-bold text-red-500">{data.stats.bad_count}</div>
            <div className="text-xs text-gray-500">低評価 (1-2★)</div>
          </div>
          <div className="bg-white rounded-lg border border-gray-200 p-3 text-center">
            <div className="text-2xl font-bold text-purple-600">
              {data.stats.avg_char_count || "-"}
            </div>
            <div className="text-xs text-gray-500">平均文字数</div>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={() => { setRatedOnly(false); setPage(0); }}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            !ratedOnly ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          すべて ({data?.total || 0})
        </button>
        <button
          onClick={() => { setRatedOnly(true); setPage(0); }}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            ratedOnly ? "bg-yellow-100 text-yellow-700" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          評価済みのみ
        </button>
        <button
          onClick={fetchList}
          className="ml-auto px-3 py-1.5 bg-gray-100 hover:bg-gray-200 rounded-lg text-sm text-gray-600 transition-colors"
        >
          更新
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-sm text-red-600">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading ? (
        <div className="text-center py-8 text-gray-500">読み込み中...</div>
      ) : (
        <>
          {/* Table */}
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">商品名</th>
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">ユーザー</th>
                  <th className="text-center px-4 py-2.5 text-xs font-medium text-gray-500">文字数</th>
                  <th className="text-center px-4 py-2.5 text-xs font-medium text-gray-500">評価</th>
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">タグ</th>
                  <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">日時</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data?.generations?.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="text-center py-8 text-gray-400">
                      データがありません
                    </td>
                  </tr>
                ) : (
                  data?.generations?.map((gen) => (
                    <tr
                      key={gen.id}
                      onClick={() => fetchDetail(gen.id)}
                      className="hover:bg-gray-50 cursor-pointer transition-colors"
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-800 truncate max-w-[200px]">
                          {gen.product_name}
                        </div>
                        {gen.discounted_price && (
                          <span className="text-xs text-red-500">{gen.discounted_price}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500 truncate max-w-[150px]">
                        {gen.user_email}
                      </td>
                      <td className="px-4 py-3 text-center text-xs text-gray-600">
                        {gen.char_count}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {renderStars(gen.rating)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1 max-w-[200px]">
                          {gen.rating_good_tags?.map((tag, i) => (
                            <span key={`g${i}`} className="px-1.5 py-0.5 bg-green-50 text-green-600 rounded text-[10px]">
                              {tag}
                            </span>
                          ))}
                          {gen.rating_bad_tags?.map((tag, i) => (
                            <span key={`b${i}`} className="px-1.5 py-0.5 bg-red-50 text-red-600 rounded text-[10px]">
                              {tag}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                        {formatDate(gen.created_at)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {data && data.total > LIMIT && (
            <div className="flex items-center justify-between mt-4">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 rounded-lg text-sm disabled:opacity-50 disabled:cursor-not-allowed"
              >
                ← 前へ
              </button>
              <span className="text-xs text-gray-500">
                {page * LIMIT + 1} - {Math.min((page + 1) * LIMIT, data.total)} / {data.total}件
              </span>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={(page + 1) * LIMIT >= data.total}
                className="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 rounded-lg text-sm disabled:opacity-50 disabled:cursor-not-allowed"
              >
                次へ →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
