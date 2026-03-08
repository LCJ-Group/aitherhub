import { useState, useEffect, useCallback } from "react";
import axios from "axios";

/**
 * AdminDiagnostics – Frontend エラー診断画面
 *
 * 表示内容:
 *   - サマリー: 直近24hのエラー件数 / セクション別 / タイプ別
 *   - エラーログ一覧: video_id / section_name / endpoint / error_type / request_id / created_at
 *   - フィルタ: video_id / section_name / error_type
 */

const ERROR_TYPE_COLORS = {
  auth: "bg-orange-100 text-orange-700",
  not_found: "bg-gray-100 text-gray-600",
  timeout: "bg-yellow-100 text-yellow-700",
  network: "bg-yellow-100 text-yellow-700",
  server: "bg-red-100 text-red-700",
  rate_limit: "bg-yellow-100 text-yellow-700",
  parse: "bg-red-100 text-red-600",
  unknown: "bg-gray-100 text-gray-500",
};

const ERROR_TYPE_LABELS = {
  auth: "認証",
  not_found: "未生成",
  timeout: "タイムアウト",
  network: "ネットワーク",
  server: "サーバー",
  rate_limit: "レート制限",
  parse: "パース",
  unknown: "不明",
};

export default function AdminDiagnostics({ adminKey }) {
  const [summary, setSummary] = useState(null);
  const [errors, setErrors] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);

  // Filters
  const [filterVideoId, setFilterVideoId] = useState("");
  const [filterSection, setFilterSection] = useState("");
  const [filterType, setFilterType] = useState("");
  const [page, setPage] = useState(0);
  const LIMIT = 50;

  const baseURL = import.meta.env.VITE_API_BASE_URL;

  // Fetch summary
  const fetchSummary = useCallback(async () => {
    setSummaryLoading(true);
    try {
      const res = await axios.get(`${baseURL}/api/v1/admin/frontend-diagnostics/summary`, {
        headers: { "X-Admin-Key": adminKey },
        params: { hours: 24 },
      });
      setSummary(res.data);
    } catch (e) {
      console.error("Failed to fetch diagnostics summary:", e);
    }
    setSummaryLoading(false);
  }, [baseURL, adminKey]);

  // Fetch error logs
  const fetchErrors = useCallback(async (offset = 0) => {
    setLoading(true);
    try {
      const params = { limit: LIMIT, offset };
      if (filterVideoId) params.video_id = filterVideoId;
      if (filterSection) params.section_name = filterSection;
      if (filterType) params.error_type = filterType;

      const res = await axios.get(`${baseURL}/api/v1/admin/frontend-diagnostics`, {
        headers: { "X-Admin-Key": adminKey },
        params,
      });
      setErrors(res.data.errors || []);
      setTotal(res.data.total || 0);
    } catch (e) {
      console.error("Failed to fetch diagnostics:", e);
    }
    setLoading(false);
  }, [baseURL, adminKey, filterVideoId, filterSection, filterType]);

  useEffect(() => {
    fetchSummary();
  }, [fetchSummary]);

  useEffect(() => {
    fetchErrors(page * LIMIT);
  }, [fetchErrors, page]);

  const handleSearch = () => {
    setPage(0);
    fetchErrors(0);
  };

  const handleClear = () => {
    setFilterVideoId("");
    setFilterSection("");
    setFilterType("");
    setPage(0);
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return "-";
    try {
      const d = new Date(dateStr);
      return d.toLocaleString("ja-JP", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return dateStr;
    }
  };

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <section>
        <div className="flex items-center gap-2 mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-red-500">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          <h2 className="text-lg font-semibold text-gray-700">Frontend Diagnostics</h2>
          <span className="text-xs text-gray-400">直近24時間</span>
          <button
            onClick={fetchSummary}
            className="ml-auto text-xs text-gray-400 hover:text-gray-600 transition-colors"
          >
            更新
          </button>
        </div>

        {summaryLoading ? (
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-red-500"></div>
          </div>
        ) : summary ? (
          <div className="space-y-4">
            {/* Total errors */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <div className="text-xs text-gray-400 mb-1">総エラー数</div>
                <div className="text-2xl font-bold text-red-600">{summary.total_errors}</div>
              </div>
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <div className="text-xs text-gray-400 mb-1">セクション数</div>
                <div className="text-2xl font-bold text-gray-700">
                  {Object.keys(summary.by_section || {}).length}
                </div>
              </div>
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <div className="text-xs text-gray-400 mb-1">エラータイプ数</div>
                <div className="text-2xl font-bold text-gray-700">
                  {Object.keys(summary.by_error_type || {}).length}
                </div>
              </div>
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <div className="text-xs text-gray-400 mb-1">期間</div>
                <div className="text-lg font-bold text-gray-500">{summary.period_hours}h</div>
              </div>
            </div>

            {/* By Section & By Type */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* By Section */}
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h3 className="text-sm font-medium text-gray-600 mb-3">セクション別</h3>
                {Object.keys(summary.by_section || {}).length === 0 ? (
                  <p className="text-xs text-gray-400">エラーなし</p>
                ) : (
                  <div className="space-y-2">
                    {Object.entries(summary.by_section || {})
                      .sort(([, a], [, b]) => b - a)
                      .map(([section, count]) => (
                        <div key={section} className="flex items-center justify-between">
                          <span className="text-sm text-gray-700 truncate max-w-[200px]">{section}</span>
                          <span className="text-sm font-medium text-red-600 bg-red-50 px-2 py-0.5 rounded">
                            {count}
                          </span>
                        </div>
                      ))}
                  </div>
                )}
              </div>

              {/* By Type */}
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h3 className="text-sm font-medium text-gray-600 mb-3">エラータイプ別</h3>
                {Object.keys(summary.by_error_type || {}).length === 0 ? (
                  <p className="text-xs text-gray-400">エラーなし</p>
                ) : (
                  <div className="space-y-2">
                    {Object.entries(summary.by_error_type || {})
                      .sort(([, a], [, b]) => b - a)
                      .map(([type, count]) => (
                        <div key={type} className="flex items-center justify-between">
                          <span className={`text-xs px-2 py-0.5 rounded-full ${ERROR_TYPE_COLORS[type] || ERROR_TYPE_COLORS.unknown}`}>
                            {ERROR_TYPE_LABELS[type] || type}
                          </span>
                          <span className="text-sm font-medium text-gray-700">{count}</span>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            </div>

            {/* Recent errors */}
            {summary.recent_errors && summary.recent_errors.length > 0 && (
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h3 className="text-sm font-medium text-gray-600 mb-3">直近のエラー</h3>
                <div className="space-y-1.5">
                  {summary.recent_errors.map((e, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span className="text-gray-400 w-24 shrink-0">{formatDate(e.created_at)}</span>
                      <span className={`px-1.5 py-0.5 rounded ${ERROR_TYPE_COLORS[e.error_type] || ERROR_TYPE_COLORS.unknown}`}>
                        {ERROR_TYPE_LABELS[e.error_type] || e.error_type}
                      </span>
                      <span className="text-gray-600 truncate">{e.section_name}</span>
                      {e.video_id && (
                        <span className="text-gray-400 truncate max-w-[120px]" title={e.video_id}>
                          {e.video_id.substring(0, 12)}...
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm text-gray-400">サマリーの取得に失敗しました</p>
        )}
      </section>

      {/* Filter & Error Log Table */}
      <section>
        <div className="flex items-center gap-2 mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-gray-500">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /><polyline points="10 9 9 9 8 9" />
          </svg>
          <h2 className="text-lg font-semibold text-gray-700">エラーログ</h2>
          <span className="text-xs text-gray-400">{total}件</span>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2 mb-4">
          <input
            type="text"
            placeholder="video_id"
            value={filterVideoId}
            onChange={(e) => setFilterVideoId(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-orange-400"
          />
          <input
            type="text"
            placeholder="section_name"
            value={filterSection}
            onChange={(e) => setFilterSection(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-orange-400"
          />
          <select
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
          >
            <option value="">全タイプ</option>
            <option value="auth">認証</option>
            <option value="not_found">未生成</option>
            <option value="timeout">タイムアウト</option>
            <option value="network">ネットワーク</option>
            <option value="server">サーバー</option>
            <option value="rate_limit">レート制限</option>
            <option value="parse">パース</option>
            <option value="unknown">不明</option>
          </select>
          <button
            onClick={handleSearch}
            className="px-4 py-1.5 text-sm bg-orange-500 text-white rounded-lg hover:bg-orange-600 transition-colors"
          >
            検索
          </button>
          <button
            onClick={handleClear}
            className="px-4 py-1.5 text-sm bg-gray-200 text-gray-600 rounded-lg hover:bg-gray-300 transition-colors"
          >
            クリア
          </button>
        </div>

        {/* Error Table */}
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-orange-500"></div>
          </div>
        ) : errors.length === 0 ? (
          <div className="text-center py-8 text-gray-400 text-sm">
            エラーログがありません
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left">
                  <th className="py-2 px-2 text-xs text-gray-500 font-medium">日時</th>
                  <th className="py-2 px-2 text-xs text-gray-500 font-medium">タイプ</th>
                  <th className="py-2 px-2 text-xs text-gray-500 font-medium">セクション</th>
                  <th className="py-2 px-2 text-xs text-gray-500 font-medium">video_id</th>
                  <th className="py-2 px-2 text-xs text-gray-500 font-medium">endpoint</th>
                  <th className="py-2 px-2 text-xs text-gray-500 font-medium">HTTP</th>
                  <th className="py-2 px-2 text-xs text-gray-500 font-medium">request_id</th>
                  <th className="py-2 px-2 text-xs text-gray-500 font-medium">メッセージ</th>
                </tr>
              </thead>
              <tbody>
                {errors.map((err) => (
                  <tr key={err.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-2 px-2 text-xs text-gray-400 whitespace-nowrap">
                      {formatDate(err.created_at)}
                    </td>
                    <td className="py-2 px-2">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap ${ERROR_TYPE_COLORS[err.error_type] || ERROR_TYPE_COLORS.unknown}`}>
                        {ERROR_TYPE_LABELS[err.error_type] || err.error_type}
                      </span>
                    </td>
                    <td className="py-2 px-2 text-xs text-gray-700 max-w-[120px] truncate" title={err.section_name}>
                      {err.section_name}
                    </td>
                    <td className="py-2 px-2 text-xs text-gray-500 max-w-[100px] truncate font-mono" title={err.video_id}>
                      {err.video_id ? err.video_id.substring(0, 12) + "..." : "-"}
                    </td>
                    <td className="py-2 px-2 text-xs text-gray-500 max-w-[120px] truncate" title={err.endpoint}>
                      {err.endpoint || "-"}
                    </td>
                    <td className="py-2 px-2 text-xs text-gray-500">
                      {err.http_status || "-"}
                    </td>
                    <td className="py-2 px-2 text-xs text-gray-400 max-w-[100px] truncate font-mono" title={err.request_id}>
                      {err.request_id ? err.request_id.substring(0, 16) + "..." : "-"}
                    </td>
                    <td className="py-2 px-2 text-xs text-gray-500 max-w-[200px] truncate" title={err.error_message}>
                      {err.error_message || "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Pagination */}
            {total > LIMIT && (
              <div className="flex items-center justify-between mt-4">
                <span className="text-xs text-gray-400">
                  {page * LIMIT + 1} - {Math.min((page + 1) * LIMIT, total)} / {total}件
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={page === 0}
                    className="px-3 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200 disabled:opacity-50"
                  >
                    前へ
                  </button>
                  <button
                    onClick={() => setPage((p) => p + 1)}
                    disabled={(page + 1) * LIMIT >= total}
                    className="px-3 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200 disabled:opacity-50"
                  >
                    次へ
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
