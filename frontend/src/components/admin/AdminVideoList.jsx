import { useState, useEffect } from "react";
import axios from "axios";

const STATUS_COLORS = {
  DONE: "bg-green-100 text-green-800",
  ERROR: "bg-red-100 text-red-800",
  uploaded: "bg-blue-100 text-blue-800",
};

const DATASET_COLORS = {
  included: "bg-emerald-100 text-emerald-800",
  excluded: "bg-gray-100 text-gray-600",
  pending: "bg-yellow-100 text-yellow-800",
};

function StatusBadge({ status }) {
  const color = STATUS_COLORS[status] || "bg-orange-100 text-orange-800";
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${color}`}>
      {status}
    </span>
  );
}

function DatasetBadge({ status, reason }) {
  const color = DATASET_COLORS[status] || "bg-gray-100 text-gray-600";
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${color}`} title={reason || ""}>
      {status}
    </span>
  );
}

export default function AdminVideoList({ adminKey, onSelectVideo }) {
  const [videos, setVideos] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [uploadTypeFilter, setUploadTypeFilter] = useState("");
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 30;

  useEffect(() => {
    fetchVideos();
  }, [statusFilter, uploadTypeFilter, page]);

  const fetchVideos = async () => {
    try {
      setLoading(true);
      setError(null);
      const baseURL = import.meta.env.VITE_API_BASE_URL;
      const params = new URLSearchParams();
      params.set("limit", PAGE_SIZE);
      params.set("offset", page * PAGE_SIZE);
      if (statusFilter) params.set("status_filter", statusFilter);
      if (uploadTypeFilter) params.set("upload_type_filter", uploadTypeFilter);

      const res = await axios.get(`${baseURL}/api/v1/admin/videos?${params}`, {
        headers: { "X-Admin-Key": adminKey },
      });
      setVideos(res.data.videos);
      setTotal(res.data.total);
    } catch (err) {
      setError("動画一覧の取得に失敗しました");
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div>
      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white"
        >
          <option value="">全ステータス</option>
          <option value="DONE">DONE</option>
          <option value="ERROR">ERROR</option>
          <option value="uploaded">uploaded</option>
          <option value="STEP_0_EXTRACT_FRAMES">処理中</option>
        </select>
        <select
          value={uploadTypeFilter}
          onChange={(e) => { setUploadTypeFilter(e.target.value); setPage(0); }}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white"
        >
          <option value="">全タイプ</option>
          <option value="screen_recording">画面収録</option>
          <option value="clean_video">クリーン動画</option>
        </select>
        <div className="ml-auto text-sm text-gray-500 self-center">
          {total} 件
        </div>
      </div>

      {error && <p className="text-red-500 text-sm mb-4">{error}</p>}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-orange-500"></div>
        </div>
      ) : (
        <>
          {/* Table */}
          <div className="overflow-x-auto rounded-xl border border-gray-200">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-left text-gray-600">
                  <th className="px-3 py-2.5 font-medium">動画名</th>
                  <th className="px-3 py-2.5 font-medium">タイプ</th>
                  <th className="px-3 py-2.5 font-medium">ステータス</th>
                  <th className="px-3 py-2.5 font-medium text-center">Phases</th>
                  <th className="px-3 py-2.5 font-medium text-center">Moments</th>
                  <th className="px-3 py-2.5 font-medium text-center">Source</th>
                  <th className="px-3 py-2.5 font-medium text-center">人間ラベル</th>
                  <th className="px-3 py-2.5 font-medium text-center">Dataset</th>
                  <th className="px-3 py-2.5 font-medium">作成日</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {videos.map((v) => (
                  <tr
                    key={v.id}
                    className="hover:bg-orange-50 cursor-pointer transition-colors"
                    onClick={() => onSelectVideo(v.id)}
                  >
                    <td className="px-3 py-2.5 max-w-[200px] truncate" title={v.filename}>
                      <span className="font-medium text-gray-800">
                        {v.filename || v.id.slice(0, 8)}
                      </span>
                      <br />
                      <span className="text-xs text-gray-400">{v.user_email}</span>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className={`px-2 py-0.5 rounded text-xs ${
                        v.upload_type === "clean_video"
                          ? "bg-purple-100 text-purple-700"
                          : "bg-blue-100 text-blue-700"
                      }`}>
                        {v.upload_type === "clean_video" ? "CSV" : "画面収録"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <StatusBadge status={v.status} />
                      {v.status !== "DONE" && v.status !== "ERROR" && v.step_progress > 0 && (
                        <span className="ml-1 text-xs text-gray-400">{v.step_progress}%</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-center font-mono">{v.phase_count}</td>
                    <td className="px-3 py-2.5 text-center font-mono">{v.moment_count}</td>
                    <td className="px-3 py-2.5 text-center">
                      {v.moment_sources ? (
                        <span className="text-xs text-gray-600">{v.moment_sources}</span>
                      ) : (
                        <span className="text-xs text-gray-300">-</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      {(v.rating_count > 0 || v.tag_count > 0 || v.comment_count > 0) ? (
                        <div className="flex gap-1 justify-center">
                          {v.rating_count > 0 && (
                            <span className="text-xs bg-yellow-100 text-yellow-700 px-1.5 rounded" title="評価済み">
                              ★{v.rating_count}
                            </span>
                          )}
                          {v.tag_count > 0 && (
                            <span className="text-xs bg-blue-100 text-blue-700 px-1.5 rounded" title="タグ済み">
                              T{v.tag_count}
                            </span>
                          )}
                          {v.comment_count > 0 && (
                            <span className="text-xs bg-green-100 text-green-700 px-1.5 rounded" title="コメント済み">
                              C{v.comment_count}
                            </span>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-gray-300">-</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <DatasetBadge status={v.dataset_status} reason={v.dataset_excluded_reason} />
                    </td>
                    <td className="px-3 py-2.5 text-xs text-gray-500 whitespace-nowrap">
                      {v.created_at ? new Date(v.created_at).toLocaleDateString("ja-JP") : "-"}
                    </td>
                  </tr>
                ))}
                {videos.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-3 py-8 text-center text-gray-400">
                      動画が見つかりません
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-4">
              <button
                onClick={() => setPage(Math.max(0, page - 1))}
                disabled={page === 0}
                className="px-3 py-1.5 text-sm border rounded-lg disabled:opacity-30 hover:bg-gray-50"
              >
                前へ
              </button>
              <span className="text-sm text-gray-500">
                {page + 1} / {totalPages}
              </span>
              <button
                onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                disabled={page >= totalPages - 1}
                className="px-3 py-1.5 text-sm border rounded-lg disabled:opacity-30 hover:bg-gray-50"
              >
                次へ
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
