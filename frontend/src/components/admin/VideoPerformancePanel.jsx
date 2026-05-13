import { useState, useEffect, useCallback, useRef } from "react";
import axios from "axios";

const API = import.meta.env.VITE_API_URL || "";

export default function VideoPerformancePanel({ adminKey }) {
  const [mode, setMode] = useState("upload"); // upload | results | history
  const [uploading, setUploading] = useState(false);
  const [ocrResult, setOcrResult] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [selectedVideo, setSelectedVideo] = useState(null);
  const [saving, setSaving] = useState(false);
  const [history, setHistory] = useState([]);
  const [stats, setStats] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [previewUrl, setPreviewUrl] = useState(null);
  const fileInputRef = useRef(null);

  const headers = { "X-Admin-Key": adminKey };

  // Fetch history and stats
  const fetchHistory = useCallback(async () => {
    try {
      const [histRes, statsRes] = await Promise.all([
        axios.get(`${API}/api/v1/video-performance/list?limit=20`, { headers }),
        axios.get(`${API}/api/v1/video-performance/stats`, { headers }),
      ]);
      setHistory(histRes.data.records || []);
      setStats(statsRes.data);
    } catch (e) {
      console.error("Failed to fetch performance history:", e);
    }
  }, [adminKey]);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Handle file upload (screenshot)
  const handleUpload = async (file) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      alert("画像ファイルを選択してください");
      return;
    }

    // Preview
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    setUploading(true);
    setOcrResult(null);
    setCandidates([]);
    setSelectedVideo(null);

    try {
      const formData = new FormData();
      formData.append("screenshot", file);

      const res = await axios.post(
        `${API}/api/v1/video-performance/ocr-screenshot`,
        formData,
        { headers: { ...headers, "Content-Type": "multipart/form-data" } }
      );

      setOcrResult(res.data.ocr_data);
      setCandidates(res.data.candidates || []);
      if (res.data.best_match) {
        setSelectedVideo(res.data.best_match.video_id);
      }
      setMode("results");
    } catch (e) {
      alert("OCR処理に失敗しました: " + (e.response?.data?.detail || e.message));
    } finally {
      setUploading(false);
    }
  };

  // Confirm and save
  const handleConfirm = async () => {
    if (!selectedVideo || !ocrResult) {
      alert("動画を選択してください");
      return;
    }
    setSaving(true);
    try {
      await axios.post(
        `${API}/api/v1/video-performance/confirm`,
        {
          video_id: selectedVideo,
          ocr_data: ocrResult,
          platform: "tiktok",
        },
        { headers }
      );
      alert("パフォーマンスデータを保存しました！");
      setMode("upload");
      setOcrResult(null);
      setCandidates([]);
      setSelectedVideo(null);
      setPreviewUrl(null);
      fetchHistory();
    } catch (e) {
      alert("保存に失敗しました: " + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  // Drag & drop
  const handleDragOver = (e) => { e.preventDefault(); setDragOver(true); };
  const handleDragLeave = () => { setDragOver(false); };
  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleUpload(file);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2">
            📊 動画パフォーマンス
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            TikTokのスクショをアップロード → 自動OCR → 動画マッチング → ML学習データ化
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setMode("upload")}
            className={`px-3 py-1.5 rounded-md text-sm font-medium ${
              mode === "upload" ? "bg-blue-100 text-blue-700" : "text-gray-500 hover:bg-gray-100"
            }`}
          >
            📸 アップロード
          </button>
          <button
            onClick={() => { setMode("history"); fetchHistory(); }}
            className={`px-3 py-1.5 rounded-md text-sm font-medium ${
              mode === "history" ? "bg-blue-100 text-blue-700" : "text-gray-500 hover:bg-gray-100"
            }`}
          >
            📋 履歴
          </button>
        </div>
      </div>

      {/* Stats Summary */}
      {stats && stats.total_records > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="登録済み動画" value={stats.unique_videos} unit="本" color="blue" />
          <StatCard label="平均エンゲージメント" value={stats.avg_engagement_rate ? (stats.avg_engagement_rate * 100).toFixed(1) + "%" : "-"} color="green" />
          <StatCard label="平均再生数" value={stats.avg_views ? Math.round(stats.avg_views).toLocaleString() : "-"} color="purple" />
          <StatCard label="総購入数" value={stats.total_purchases || 0} unit="件" color="orange" />
        </div>
      )}

      {/* Upload Mode */}
      {mode === "upload" && (
        <div
          className={`border-2 border-dashed rounded-xl p-12 text-center transition-all cursor-pointer ${
            dragOver ? "border-blue-500 bg-blue-50" : "border-gray-300 hover:border-gray-400"
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => handleUpload(e.target.files[0])}
          />
          {uploading ? (
            <div className="flex flex-col items-center gap-3">
              <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500"></div>
              <p className="text-gray-600">OCR処理中... GPT-4oで解析しています</p>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3">
              <span className="text-5xl">📱</span>
              <p className="text-lg font-medium text-gray-700">
                TikTokのスクリーンショットをドラッグ＆ドロップ
              </p>
              <p className="text-sm text-gray-500">
                または、クリックしてファイルを選択
              </p>
              <p className="text-xs text-gray-400 mt-2">
                対応形式: JPEG, PNG, WebP, HEIC（最大10MB）
              </p>
            </div>
          )}
        </div>
      )}

      {/* Results Mode */}
      {mode === "results" && ocrResult && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Left: Screenshot preview + OCR data */}
            <div className="space-y-4">
              {previewUrl && (
                <div className="rounded-lg overflow-hidden border shadow-sm max-h-96">
                  <img src={previewUrl} alt="Screenshot" className="w-full h-full object-contain" />
                </div>
              )}
              <div className="bg-white rounded-lg border p-4">
                <h3 className="font-semibold text-gray-700 mb-3">📊 抽出データ</h3>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <MetricRow label="再生数" value={ocrResult.views?.toLocaleString()} />
                  <MetricRow label="いいね" value={ocrResult.likes?.toLocaleString()} />
                  <MetricRow label="コメント" value={ocrResult.comments?.toLocaleString()} />
                  <MetricRow label="シェア" value={ocrResult.shares?.toLocaleString()} />
                  <MetricRow label="保存" value={ocrResult.saves?.toLocaleString()} />
                  <MetricRow label="購入" value={ocrResult.purchases?.toLocaleString()} />
                  {ocrResult.revenue && <MetricRow label="売上" value={`¥${ocrResult.revenue.toLocaleString()}`} />}
                </div>
                {ocrResult.caption && (
                  <div className="mt-3 pt-3 border-t">
                    <p className="text-xs text-gray-500">キャプション:</p>
                    <p className="text-sm text-gray-700 mt-1">{ocrResult.caption}</p>
                  </div>
                )}
                {ocrResult.hashtags && ocrResult.hashtags.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {ocrResult.hashtags.map((tag, i) => (
                      <span key={i} className="text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Right: Video candidates */}
            <div className="space-y-4">
              <div className="bg-white rounded-lg border p-4">
                <h3 className="font-semibold text-gray-700 mb-3">🎬 マッチング候補</h3>
                {candidates.length === 0 ? (
                  <p className="text-sm text-gray-500">候補が見つかりませんでした。手動で選択してください。</p>
                ) : (
                  <div className="space-y-2 max-h-80 overflow-y-auto">
                    {candidates.map((c) => (
                      <div
                        key={c.video_id}
                        onClick={() => setSelectedVideo(c.video_id)}
                        className={`p-3 rounded-lg border cursor-pointer transition-all ${
                          selectedVideo === c.video_id
                            ? "border-blue-500 bg-blue-50 ring-1 ring-blue-500"
                            : "border-gray-200 hover:border-gray-300"
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium text-gray-700 truncate max-w-[200px]">
                            {c.original_filename || "名称不明"}
                          </span>
                          <span className={`text-xs px-2 py-0.5 rounded-full ${
                            c.match_score >= 0.5 ? "bg-green-100 text-green-700" :
                            c.match_score >= 0.3 ? "bg-yellow-100 text-yellow-700" :
                            "bg-gray-100 text-gray-600"
                          }`}>
                            {Math.round(c.match_score * 100)}%
                          </span>
                        </div>
                        {c.match_reason && (
                          <p className="text-xs text-gray-500 mt-1">{c.match_reason}</p>
                        )}
                        {c.created_at && (
                          <p className="text-xs text-gray-400 mt-0.5">
                            {new Date(c.created_at).toLocaleDateString("ja-JP")}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Confirm button */}
              <div className="flex gap-3">
                <button
                  onClick={handleConfirm}
                  disabled={!selectedVideo || saving}
                  className="flex-1 px-4 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                >
                  {saving ? "保存中..." : "✅ この動画に紐づけて保存"}
                </button>
                <button
                  onClick={() => { setMode("upload"); setOcrResult(null); setCandidates([]); setPreviewUrl(null); }}
                  className="px-4 py-3 bg-gray-100 text-gray-600 rounded-lg font-medium hover:bg-gray-200 transition-all"
                >
                  キャンセル
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* History Mode */}
      {mode === "history" && (
        <div className="bg-white rounded-lg border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">動画</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">再生数</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">いいね</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">コメント</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">ER%</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">購入</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">日時</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {history.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-gray-400">
                    まだデータがありません。スクショをアップロードして始めましょう！
                  </td>
                </tr>
              ) : (
                history.map((r) => (
                  <tr key={r.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-700 truncate max-w-[200px]">
                        {r.original_filename || r.caption?.slice(0, 30) || "名称不明"}
                      </div>
                      {r.caption && (
                        <div className="text-xs text-gray-400 truncate max-w-[200px]">{r.caption}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">{r.views?.toLocaleString() || "-"}</td>
                    <td className="px-4 py-3 text-right text-gray-600">{r.likes?.toLocaleString() || "-"}</td>
                    <td className="px-4 py-3 text-right text-gray-600">{r.comments?.toLocaleString() || "-"}</td>
                    <td className="px-4 py-3 text-right">
                      <span className={`font-medium ${
                        r.engagement_rate > 0.05 ? "text-green-600" :
                        r.engagement_rate > 0.02 ? "text-yellow-600" :
                        "text-gray-600"
                      }`}>
                        {r.engagement_rate ? (r.engagement_rate * 100).toFixed(1) + "%" : "-"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">{r.purchases || "-"}</td>
                    <td className="px-4 py-3 text-right text-xs text-gray-400">
                      {r.recorded_at ? new Date(r.recorded_at).toLocaleDateString("ja-JP") : "-"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Helper components
function StatCard({ label, value, unit, color }) {
  const colors = {
    blue: "bg-blue-50 text-blue-700",
    green: "bg-green-50 text-green-700",
    purple: "bg-purple-50 text-purple-700",
    orange: "bg-orange-50 text-orange-700",
  };
  return (
    <div className={`rounded-lg p-3 ${colors[color] || colors.blue}`}>
      <p className="text-xs opacity-70">{label}</p>
      <p className="text-lg font-bold">
        {value} {unit && <span className="text-xs font-normal">{unit}</span>}
      </p>
    </div>
  );
}

function MetricRow({ label, value }) {
  return (
    <div className="flex justify-between items-center py-1">
      <span className="text-gray-500">{label}</span>
      <span className="font-medium text-gray-800">{value || "-"}</span>
    </div>
  );
}
