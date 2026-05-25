import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import Highcharts from "highcharts";
import HighchartsReact from "highcharts-react-official";
const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

// ── Number formatter ──
function fmtNum(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return n.toLocaleString();
}

// ── Relative time ──
function timeAgo(dateStr) {
  if (!dateStr) return "—";
  const d = new Date(dateStr);
  const now = new Date();
  const diff = Math.floor((now - d) / 1000);
  if (diff < 60) return `${diff}秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}分前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}時間前`;
  return `${Math.floor(diff / 86400)}日前`;
}

// ── Frequency helper text ──
function getFrequencyLabel(createdAt) {
  if (!createdAt) return "—";
  const days = Math.floor((new Date() - new Date(createdAt)) / 86400000);
  if (days <= 3) return "毎日";
  if (days <= 7) return "2日に1回";
  if (days <= 30) return "週1回";
  return "自動停止";
}

const FREQUENCY_HELP = `📊 推奨取得頻度（自動適用）
• 投稿後 1〜3日：毎日取得
• 投稿後 4〜7日：2日に1回
• 投稿後 8〜30日：週1回
• 投稿後 31日以上：自動停止

🔗 自動マッチング
• TikTok動画の秒数でClipDBクリップと自動紐付け
• 音声フィンガープリントで精度向上（要事前生成）

📥 アカウント一括インポート
• TikTokアカウントURLを入力するだけで全投稿を自動取得
• 各投稿をClipDBクリップと自動マッチング`;

// ── Video Player Modal ──
function VideoPlayerModal({ video, onClose }) {
  if (!video) return null;
  const videoUrl = video.clip_exported_url || video.clip_original_url || null;
  const tiktokUrl = video.tiktok_url;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full mx-4 overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <div className="min-w-0">
            <div className="text-sm font-bold text-gray-800 truncate">{video.label || video.title || "無題"}</div>
            <div className="text-[10px] text-gray-400">@{video.account_name || "—"}</div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg ml-2 flex-shrink-0">✕</button>
        </div>
        <div className="bg-black">
          {videoUrl ? (
            <video
              src={videoUrl}
              controls
              autoPlay
              playsInline
              className="w-full max-h-[70vh] object-contain"
              onError={(e) => {
                e.target.style.display = "none";
                e.target.nextSibling && (e.target.nextSibling.style.display = "flex");
              }}
            />
          ) : null}
          {/* Fallback: TikTok embed or message */}
          <div className={`${videoUrl ? "hidden" : "flex"} flex-col items-center justify-center py-12 px-4`}>
            {tiktokUrl ? (
              <>
                <div className="text-gray-400 text-sm mb-3">動画ファイルがありません</div>
                <a
                  href={tiktokUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-4 py-2 bg-gradient-to-r from-pink-500 to-rose-500 text-white text-sm font-medium rounded-lg hover:from-pink-600 hover:to-rose-600 transition-colors"
                >
                  TikTokで見る ↗
                </a>
              </>
            ) : (
              <div className="text-gray-400 text-sm">動画URLが見つかりません</div>
            )}
          </div>
        </div>
        {/* Bottom actions */}
        <div className="px-4 py-3 border-t border-gray-100 flex gap-2 flex-wrap">
          {video.clip_exported_url && (
            <a href={video.clip_exported_url} target="_blank" rel="noopener noreferrer"
              className="px-3 py-1.5 text-[10px] font-medium bg-emerald-50 text-emerald-600 rounded-md hover:bg-emerald-100 transition-colors">
              ✅ AH編集版
            </a>
          )}
          {video.clip_original_url && (
            <a href={video.clip_original_url} target="_blank" rel="noopener noreferrer"
              className="px-3 py-1.5 text-[10px] font-medium bg-blue-50 text-blue-600 rounded-md hover:bg-blue-100 transition-colors">
              📎 元クリップ
            </a>
          )}
          {tiktokUrl && (
            <a href={tiktokUrl} target="_blank" rel="noopener noreferrer"
              className="px-3 py-1.5 text-[10px] font-medium bg-pink-50 text-pink-600 rounded-md hover:bg-pink-100 transition-colors">
              🎵 TikTokで開く
            </a>
          )}
          {video.clip_db_id && (
            <button
              onClick={() => {
                const url = new URL(window.location.href);
                url.searchParams.set('tab', 'clip-db');
                url.searchParams.set('clip_id', video.clip_db_id);
                window.location.href = url.toString();
              }}
              className="px-3 py-1.5 text-[10px] font-medium bg-indigo-50 text-indigo-600 rounded-md hover:bg-indigo-100 transition-colors">
              🎬 クリップDBで見る
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function TikTokTrackingPanel({ adminKey }) {
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState("active");

  // Sub-view: "list" | "register" | "import" | "accounts"
  const [subView, setSubView] = useState("list");

  // Register form
  const [registerUrl, setRegisterUrl] = useState("");
  const [registerLabel, setRegisterLabel] = useState("");
  const [registerClipDbId, setRegisterClipDbId] = useState("");
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState(null);
  const [registerResult, setRegisterResult] = useState(null);

  // Account import
  const [importUsername, setImportUsername] = useState("");
  const [importMaxVideos, setImportMaxVideos] = useState(0);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState(null);
  const [importResult, setImportResult] = useState(null);
  const [importStatus, setImportStatus] = useState(null);

  // Account management
  const [accounts, setAccounts] = useState([]);
  const [accountsLoading, setAccountsLoading] = useState(false);
  const [accountFilter, setAccountFilter] = useState("");

  // Fingerprint status
  const [fpStatus, setFpStatus] = useState(null);
  const [fpGenerating, setFpGenerating] = useState(false);

  // Detail / chart
  const [selectedVideo, setSelectedVideo] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [snapshotsLoading, setSnapshotsLoading] = useState(false);

  // Video player modal
  const [playerVideo, setPlayerVideo] = useState(null);

  // Fetch now
  const [fetchingId, setFetchingId] = useState(null);

  // Help tooltip
  const [showHelp, setShowHelp] = useState(false);

  const headers = { "X-Admin-Key": adminKey };

  // ── Fetch tracked videos ──
  const fetchVideos = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/videos?status=${statusFilter}&limit=200`,
        { headers }
      );
      if (!res.ok) throw new Error(`API error ${res.status}`);
      const data = await res.json();
      setVideos(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [adminKey, statusFilter]);

  useEffect(() => { fetchVideos(); }, [fetchVideos]);

  // ── Fetch accounts list ──
  const fetchAccounts = useCallback(async () => {
    setAccountsLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/accounts`,
        { headers }
      );
      if (res.ok) setAccounts(await res.json());
    } catch (e) {
      console.error("Failed to fetch accounts:", e);
    } finally {
      setAccountsLoading(false);
    }
  }, [adminKey]);

  useEffect(() => {
    if (subView === "accounts") fetchAccounts();
  }, [subView, fetchAccounts]);

  // ── Account actions ──
  const handleAccountAction = async (accountName, action) => {
    const actionMap = {
      "stop-all": { method: "PATCH", url: `${API_BASE}/api/v1/tiktok-tracking/accounts/${encodeURIComponent(accountName)}/stop-all`, confirm: `@${accountName} の全動画を停止しますか？` },
      "resume-all": { method: "PATCH", url: `${API_BASE}/api/v1/tiktok-tracking/accounts/${encodeURIComponent(accountName)}/resume-all`, confirm: `@${accountName} の全動画を再開しますか？` },
      "delete": { method: "DELETE", url: `${API_BASE}/api/v1/tiktok-tracking/accounts/${encodeURIComponent(accountName)}`, confirm: `@${accountName} の全追跡データを削除しますか？この操作は取り消せません。` },
    };
    const cfg = actionMap[action];
    if (!cfg) return;
    if (!confirm(cfg.confirm)) return;
    try {
      const res = await fetch(cfg.url, { method: cfg.method, headers });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      const result = await res.json();
      alert(`完了: ${JSON.stringify(result)}`);
      fetchAccounts();
      fetchVideos();
    } catch (e) {
      alert(`操作失敗: ${e.message}`);
    }
  };

  // ── Fetch fingerprint status ──
  const fetchFpStatus = useCallback(async () => {
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/fingerprints/status`,
        { headers }
      );
      if (res.ok) setFpStatus(await res.json());
    } catch (e) {
      console.error("Failed to fetch fingerprint status:", e);
    }
  }, [adminKey]);

  useEffect(() => { fetchFpStatus(); }, [fetchFpStatus]);

  // ── Generate fingerprints batch ──
  const handleGenerateFp = async () => {
    setFpGenerating(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/fingerprints/generate-batch?limit=50`,
        { method: "POST", headers }
      );
      if (!res.ok) throw new Error(`Error ${res.status}`);
      const data = await res.json();
      alert(`フィンガープリント生成完了\n処理: ${data.processed}件\n成功: ${data.success}件\n失敗: ${data.failed}件\n残り: ${data.remaining}件`);
      fetchFpStatus();
    } catch (e) {
      alert(`生成失敗: ${e.message}`);
    } finally {
      setFpGenerating(false);
    }
  };

  // ── Register single video ──
  const handleRegister = async (e) => {
    e.preventDefault();
    if (!registerUrl.trim()) return;
    setRegistering(true);
    setRegisterError(null);
    try {
      const body = {
        tiktok_url: registerUrl.trim(),
        label: registerLabel.trim() || null,
        clip_db_id: registerClipDbId.trim() || null,
        auto_match: !registerClipDbId.trim(),
      };
      const res = await fetch(`${API_BASE}/api/v1/tiktok-tracking/register`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Error ${res.status}`);
      }
      const result = await res.json();
      setRegisterResult(result);
      setRegisterUrl("");
      setRegisterLabel("");
      setRegisterClipDbId("");
      fetchVideos();
    } catch (e) {
      setRegisterError(e.message);
    } finally {
      setRegistering(false);
    }
  };

  // ── Import account ──
  const handleImportAccount = async (e) => {
    e.preventDefault();
    if (!importUsername.trim()) return;
    setImporting(true);
    setImportError(null);
    setImportResult(null);
    try {
      const body = {
        tiktok_username: importUsername.trim(),
        auto_match: true,
        max_videos: importMaxVideos || 0,
      };
      const res = await fetch(`${API_BASE}/api/v1/tiktok-tracking/import-account`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Error ${res.status}`);
      }
      const result = await res.json();
      setImportResult(result);
      fetchVideos();
    } catch (e) {
      setImportError(e.message);
    } finally {
      setImporting(false);
    }
  };

  // ── Check import status for a username ──
  const checkImportStatus = async (username) => {
    try {
      const clean = username.replace("@", "").trim();
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/import-account/status?username=${encodeURIComponent(clean)}`,
        { headers }
      );
      if (res.ok) setImportStatus(await res.json());
    } catch (e) {
      console.error("Failed to check import status:", e);
    }
  };

  // ── Fetch snapshots for chart ──
  const loadSnapshots = async (videoId) => {
    setSnapshotsLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/videos/${videoId}/snapshots?days=90`,
        { headers }
      );
      if (!res.ok) throw new Error(`API error ${res.status}`);
      const data = await res.json();
      setSnapshots(data.snapshots || []);
    } catch (e) {
      console.error("Failed to load snapshots:", e);
      setSnapshots([]);
    } finally {
      setSnapshotsLoading(false);
    }
  };

  const handleSelectVideo = (video) => {
    if (selectedVideo?.id === video.id) {
      setSelectedVideo(null);
      setSnapshots([]);
    } else {
      setSelectedVideo(video);
      loadSnapshots(video.id);
    }
  };

  // ── Fetch now ──
  const handleFetchNow = async (videoId) => {
    setFetchingId(videoId);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/videos/${videoId}/fetch-now`,
        { method: "POST", headers }
      );
      if (!res.ok) throw new Error(`Error ${res.status}`);
      fetchVideos();
      if (selectedVideo?.id === videoId) loadSnapshots(videoId);
    } catch (e) {
      alert(`取得失敗: ${e.message}`);
    } finally {
      setFetchingId(null);
    }
  };

  // ── Rematch ──
  const [rematchingId, setRematchingId] = useState(null);
  const handleRematch = async (videoId) => {
    setRematchingId(videoId);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/videos/${videoId}/rematch`,
        { method: "POST", headers }
      );
      const data = await res.json();
      if (data.success) {
        alert(`マッチ成功！ (${data.match_method}, 類似度: ${(data.similarity * 100).toFixed(0)}%)`);
        fetchVideos();
      } else {
        alert(`マッチ失敗: ${data.message || '該当クリップが見つかりません'}`);
      }
    } catch (e) {
      alert(`エラー: ${e.message}`);
    } finally {
      setRematchingId(null);
    }
  };

  const handleRematchAll = async () => {
    if (!confirm('未マッチ/対象外の全動画を再マッチしますか？')) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/videos/rematch-all`,
        { method: "POST", headers }
      );
      const data = await res.json();
      alert(`再マッチ完了: ${data.matched}件マッチ / ${data.failed}件失敗 / ${data.skipped}件スキップ`);
      fetchVideos();
    } catch (e) {
      alert(`エラー: ${e.message}`);
    }
  };

  // ── Stop / Resume ──
  const handleToggleStatus = async (videoId, currentStatus) => {
    const action = currentStatus === "active" ? "stop" : "resume";
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/videos/${videoId}/${action}`,
        { method: "PATCH", headers }
      );
      if (!res.ok) throw new Error(`Error ${res.status}`);
      fetchVideos();
    } catch (e) {
      alert(`操作失敗: ${e.message}`);
    }
  };

  // ── Delete ──
  const handleDelete = async (videoId) => {
    if (!confirm("この動画の追跡データをすべて削除しますか？")) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/tiktok-tracking/videos/${videoId}`,
        { method: "DELETE", headers }
      );
      if (!res.ok) throw new Error(`Error ${res.status}`);
      if (selectedVideo?.id === videoId) {
        setSelectedVideo(null);
        setSnapshots([]);
      }
      fetchVideos();
    } catch (e) {
      alert(`削除失敗: ${e.message}`);
    }
  };

  // ── Highcharts options ──
  const chartOptions = {
    chart: { height: 320, backgroundColor: "transparent", style: { fontFamily: "inherit" } },
    title: { text: null },
    credits: { enabled: false },
    legend: {
      align: "right", verticalAlign: "top", floating: true,
      itemStyle: { fontSize: "11px", color: "#6b7280" },
    },
    xAxis: {
      type: "datetime",
      crosshair: { width: 1, color: "#d1d5db", dashStyle: "Dash" },
      labels: { style: { fontSize: "10px", color: "#9ca3af" }, format: "{value:%m/%d}" },
      lineColor: "#e5e7eb", tickColor: "#e5e7eb",
    },
    yAxis: [
      {
        title: { text: "再生数", style: { fontSize: "10px", color: "#ef4444" } },
        labels: { formatter: function () { return fmtNum(this.value); }, style: { fontSize: "10px", color: "#ef4444" } },
        gridLineColor: "#f3f4f6",
      },
      {
        title: { text: "いいね / コメント / シェア", style: { fontSize: "10px", color: "#3b82f6" } },
        opposite: true,
        labels: { formatter: function () { return fmtNum(this.value); }, style: { fontSize: "10px", color: "#3b82f6" } },
        gridLineWidth: 0,
      },
    ],
    tooltip: {
      shared: true, useHTML: true,
      headerFormat: '<span style="font-size:11px;font-weight:600">{point.key:%Y/%m/%d %H:%M}</span><br/>',
      pointFormat: '<span style="color:{series.color}">●</span> {series.name}: <b>{point.y:,.0f}</b><br/>',
    },
    plotOptions: { series: { marker: { radius: 3, enabled: true }, lineWidth: 2 } },
    series: [
      { name: "再生数", data: snapshots.map((s) => [new Date(s.fetched_at).getTime(), s.play_count]), color: "#ef4444", yAxis: 0 },
      { name: "いいね", data: snapshots.map((s) => [new Date(s.fetched_at).getTime(), s.digg_count]), color: "#ec4899", yAxis: 1 },
      { name: "コメント", data: snapshots.map((s) => [new Date(s.fetched_at).getTime(), s.comment_count]), color: "#3b82f6", yAxis: 1 },
      { name: "シェア", data: snapshots.map((s) => [new Date(s.fetched_at).getTime(), s.share_count]), color: "#10b981", yAxis: 1 },
      { name: "保存", data: snapshots.map((s) => [new Date(s.fetched_at).getTime(), s.collect_count]), color: "#f59e0b", yAxis: 1 },
    ],
  };

  // ── Summary stats ──
  const totalPlay = videos.reduce((sum, v) => {
    const snap = v.latest_snapshot ? (typeof v.latest_snapshot === 'string' ? JSON.parse(v.latest_snapshot) : v.latest_snapshot) : null;
    return sum + (snap?.play_count || 0);
  }, 0);
  const totalDigg = videos.reduce((sum, v) => {
    const snap = v.latest_snapshot ? (typeof v.latest_snapshot === 'string' ? JSON.parse(v.latest_snapshot) : v.latest_snapshot) : null;
    return sum + (snap?.digg_count || 0);
  }, 0);
  const matchedCount = videos.filter(v => v.clip_db_id).length;
  const ahEditedCount = videos.filter(v => v.clip_db_id && v.is_aitherhub_edited).length;

  // ── Filter videos by account ──
  const filteredVideos = accountFilter
    ? videos.filter(v => v.account_name === accountFilter)
    : videos;

  // ── Build rows with inline detail panel ──
  // Group videos into rows of 3 (matching xl:grid-cols-3), insert detail panel after selected row
  const gridItems = useMemo(() => {
    const items = [];
    const cols = 3; // xl:grid-cols-3
    for (let i = 0; i < filteredVideos.length; i++) {
      items.push({ type: "card", video: filteredVideos[i] });
      // After each row of 3, check if selected video is in this row
      if ((i + 1) % cols === 0 || i === filteredVideos.length - 1) {
        const rowStart = Math.floor(i / cols) * cols;
        const rowEnd = Math.min(rowStart + cols, filteredVideos.length);
        const rowVideos = filteredVideos.slice(rowStart, rowEnd);
        if (selectedVideo && rowVideos.some(v => v.id === selectedVideo.id)) {
          items.push({ type: "detail" });
        }
      }
    }
    return items;
  }, [filteredVideos, selectedVideo]);

  // ── Unique account names for filter dropdown ──
  const uniqueAccounts = useMemo(() => {
    const names = [...new Set(videos.map(v => v.account_name).filter(Boolean))];
    return names.sort();
  }, [videos]);

  // ── Render ──
  return (
    <div className="space-y-4">
      {/* Video Player Modal */}
      {playerVideo && <VideoPlayerModal video={playerVideo} onClose={() => setPlayerVideo(null)} />}

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-bold text-gray-800">📊 TikTok動画パフォーマンス</h2>
          <div className="relative">
            <button
              onClick={() => setShowHelp(!showHelp)}
              className="w-5 h-5 rounded-full bg-gray-200 text-gray-500 text-xs font-bold flex items-center justify-center hover:bg-gray-300 transition-colors"
              title="ヘルプ"
            >?</button>
            {showHelp && (
              <div className="absolute left-0 top-7 z-50 w-80 bg-white border border-gray-200 rounded-lg shadow-xl p-4 text-xs text-gray-600 whitespace-pre-line">
                {FREQUENCY_HELP}
                <button onClick={() => setShowHelp(false)} className="mt-2 text-blue-500 hover:text-blue-700 text-[10px]">閉じる</button>
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white text-gray-600"
          >
            <option value="active">追跡中</option>
            <option value="stopped">停止中</option>
            <option value="all">すべて</option>
          </select>
          {/* Account filter */}
          {uniqueAccounts.length > 1 && (
            <select
              value={accountFilter}
              onChange={(e) => setAccountFilter(e.target.value)}
              className="text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white text-gray-600"
            >
              <option value="">全アカウント</option>
              {uniqueAccounts.map(name => (
                <option key={name} value={name}>@{name}</option>
              ))}
            </select>
          )}
          <button
            onClick={() => { setSubView(subView === "accounts" ? "list" : "accounts"); }}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-all shadow-sm ${
              subView === "accounts"
                ? "bg-teal-600 text-white"
                : "bg-gradient-to-r from-teal-500 to-cyan-500 text-white hover:from-teal-600 hover:to-cyan-600"
            }`}
          >
            👥 アカウント管理
          </button>
          <button
            onClick={() => { setSubView(subView === "import" ? "list" : "import"); setImportError(null); }}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-all shadow-sm ${
              subView === "import"
                ? "bg-indigo-600 text-white"
                : "bg-gradient-to-r from-indigo-500 to-purple-500 text-white hover:from-indigo-600 hover:to-purple-600"
            }`}
          >
            📥 アカウント一括
          </button>
          <button
            onClick={() => { setSubView(subView === "register" ? "list" : "register"); setRegisterError(null); }}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-all shadow-sm ${
              subView === "register"
                ? "bg-pink-600 text-white"
                : "bg-gradient-to-r from-pink-500 to-rose-500 text-white hover:from-pink-600 hover:to-rose-600"
            }`}
          >
            + URL登録
          </button>
          <button
            onClick={fetchVideos}
            className="px-3 py-1.5 bg-gray-100 text-gray-600 text-xs font-medium rounded-lg hover:bg-gray-200 transition-colors"
          >
            ↻ 更新
          </button>
        </div>
      </div>

      {/* Summary stats bar */}
      {!loading && videos.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <div className="bg-white rounded-xl border border-gray-200 p-3 text-center">
            <div className="text-[10px] text-gray-400 mb-1">追跡動画数</div>
            <div className="text-lg font-bold text-gray-800">{filteredVideos.length}{accountFilter ? `/${videos.length}` : ""}</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-3 text-center">
            <div className="text-[10px] text-gray-400 mb-1">合計再生数</div>
            <div className="text-lg font-bold text-red-500">{fmtNum(totalPlay)}</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-3 text-center">
            <div className="text-[10px] text-gray-400 mb-1">合計いいね</div>
            <div className="text-lg font-bold text-pink-500">{fmtNum(totalDigg)}</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-3 text-center">
            <div className="text-[10px] text-gray-400 mb-1">AH編集済</div>
            <div className="text-lg font-bold text-emerald-500">{ahEditedCount}/{videos.length}</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-3 text-center">
            <div className="text-[10px] text-gray-400 mb-1">対象外</div>
            <div className="text-lg font-bold text-gray-400">{matchedCount - ahEditedCount}</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-3 text-center">
            <div className="text-[10px] text-gray-400 mb-1">アクティブ</div>
            <div className="text-lg font-bold text-green-500">{videos.filter(v => v.status === "active").length}</div>
          </div>
        </div>
      )}

      {/* Account Management Panel */}
      {subView === "accounts" && (
        <div className="bg-gradient-to-r from-teal-50 to-cyan-50 border border-teal-200 rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold text-teal-700">👥 登録済みアカウント管理</div>
            <div className="flex gap-2">
              <button onClick={fetchAccounts} className="px-3 py-1.5 text-[10px] font-medium bg-teal-100 text-teal-600 rounded-lg hover:bg-teal-200 transition-colors">
                ↻ 更新
              </button>
              <button onClick={() => setSubView("list")} className="px-3 py-1.5 text-[10px] font-medium bg-gray-100 text-gray-600 rounded-lg hover:bg-gray-200 transition-colors">
                閉じる
              </button>
            </div>
          </div>

          {accountsLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-teal-500"></div>
            </div>
          ) : accounts.length === 0 ? (
            <div className="text-center py-6 text-gray-400 text-sm">
              登録済みアカウントはありません。「📥 アカウント一括」でインポートしてください。
            </div>
          ) : (
            <div className="space-y-2">
              {accounts.map((acc) => (
                <div key={acc.account_name} className="bg-white rounded-lg border border-teal-100 p-3">
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-8 h-8 rounded-full bg-gradient-to-br from-teal-400 to-cyan-500 flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
                        {acc.account_name?.charAt(0)?.toUpperCase() || "?"}
                      </div>
                      <div className="min-w-0">
                        <button
                          onClick={() => { setAccountFilter(acc.account_name); setSubView("list"); }}
                          className="text-sm font-semibold text-teal-700 hover:text-teal-900 hover:underline"
                        >
                          @{acc.account_name}
                        </button>
                        <div className="text-[10px] text-gray-400">
                          登録: {acc.first_registered ? new Date(acc.first_registered).toLocaleDateString("ja-JP") : "—"}
                          {acc.last_fetched && ` | 最終取得: ${timeAgo(acc.last_fetched)}`}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      <button
                        onClick={() => { setAccountFilter(acc.account_name); setSubView("list"); }}
                        className="px-2 py-1 text-[10px] font-medium bg-blue-50 text-blue-600 rounded-md hover:bg-blue-100 transition-colors"
                      >
                        📋 動画一覧
                      </button>
                      <button
                        onClick={() => handleAccountAction(acc.account_name, "stop-all")}
                        className="px-2 py-1 text-[10px] font-medium bg-yellow-50 text-yellow-600 rounded-md hover:bg-yellow-100 transition-colors"
                      >
                        ⏸ 全停止
                      </button>
                      <button
                        onClick={() => handleAccountAction(acc.account_name, "resume-all")}
                        className="px-2 py-1 text-[10px] font-medium bg-green-50 text-green-600 rounded-md hover:bg-green-100 transition-colors"
                      >
                        ▶ 全再開
                      </button>
                      <button
                        onClick={() => handleAccountAction(acc.account_name, "delete")}
                        className="px-2 py-1 text-[10px] font-medium bg-red-50 text-red-500 rounded-md hover:bg-red-100 transition-colors"
                      >
                        🗑
                      </button>
                    </div>
                  </div>
                  {/* Account stats */}
                  <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mt-2">
                    <div className="text-center bg-gray-50 rounded-lg p-1.5">
                      <div className="text-[9px] text-gray-400">動画数</div>
                      <div className="text-xs font-bold text-gray-700">{acc.total_videos}</div>
                    </div>
                    <div className="text-center bg-green-50 rounded-lg p-1.5">
                      <div className="text-[9px] text-gray-400">アクティブ</div>
                      <div className="text-xs font-bold text-green-600">{acc.active_videos}</div>
                    </div>
                    <div className="text-center bg-gray-50 rounded-lg p-1.5">
                      <div className="text-[9px] text-gray-400">停止</div>
                      <div className="text-xs font-bold text-gray-500">{acc.stopped_videos}</div>
                    </div>
                    <div className="text-center bg-indigo-50 rounded-lg p-1.5">
                      <div className="text-[9px] text-gray-400">ClipDB紐付</div>
                      <div className="text-xs font-bold text-indigo-600">{acc.matched_videos}</div>
                    </div>
                    <div className="text-center bg-red-50 rounded-lg p-1.5">
                      <div className="text-[9px] text-gray-400">合計再生</div>
                      <div className="text-xs font-bold text-red-500">{fmtNum(acc.total_plays)}</div>
                    </div>
                    <div className="text-center bg-pink-50 rounded-lg p-1.5">
                      <div className="text-[9px] text-gray-400">合計いいね</div>
                      <div className="text-xs font-bold text-pink-500">{fmtNum(acc.total_diggs)}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Account Import form */}
      {subView === "import" && (
        <div className="bg-gradient-to-r from-indigo-50 to-purple-50 border border-indigo-200 rounded-xl p-4 space-y-3">
          <div className="text-sm font-semibold text-indigo-700">📥 TikTokアカウント一括インポート</div>
          <p className="text-xs text-indigo-600/70">
            TikTokのユーザーID（@の後の英数字）、プロフィールURL、またはニックネーム（表示名）を入力すると、全投稿を自動取得してClipDBクリップと自動マッチングします。
          </p>
          <p className="text-[10px] text-indigo-500/60">
            ✨ ニックネーム入力時は自動検索で正しいアカウントを特定します。例: 「世界一の美容師京極琉」→ @ryukyogoku
          </p>
          <form onSubmit={handleImportAccount} className="space-y-2">
            <div className="flex gap-2">
              <input
                type="text"
                value={importUsername}
                onChange={(e) => setImportUsername(e.target.value)}
                placeholder="@ryukyogoku / ニックネーム / https://www.tiktok.com/@kyogokuprofessional"
                className="flex-1 px-3 py-2 text-sm border border-indigo-200 rounded-lg focus:ring-2 focus:ring-indigo-300 focus:border-indigo-400 outline-none"
                required
              />
              <input
                type="number"
                value={importMaxVideos || ""}
                onChange={(e) => setImportMaxVideos(parseInt(e.target.value) || 0)}
                placeholder="件数（0=全件）"
                className="w-32 px-3 py-2 text-sm border border-indigo-200 rounded-lg focus:ring-2 focus:ring-indigo-300 focus:border-indigo-400 outline-none"
                min="0"
              />
            </div>
            {importError && (
              <div className="text-xs text-red-500 bg-red-50 px-3 py-1.5 rounded-lg">{importError}</div>
            )}
            <div className="flex gap-2 items-center">
              <button
                type="submit"
                disabled={importing}
                className="px-4 py-2 bg-indigo-500 text-white text-sm font-medium rounded-lg hover:bg-indigo-600 disabled:opacity-50 transition-colors"
              >
                {importing ? (
                  <span className="flex items-center gap-2">
                    <span className="animate-spin inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full"></span>
                    インポート中...（数分かかります）
                  </span>
                ) : "一括インポート開始"}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (importUsername.trim()) checkImportStatus(importUsername);
                }}
                className="px-3 py-2 bg-indigo-100 text-indigo-600 text-sm font-medium rounded-lg hover:bg-indigo-200 transition-colors"
              >
                状態確認
              </button>
              <button
                type="button"
                onClick={() => { setSubView("list"); setImportError(null); }}
                className="px-4 py-2 bg-gray-100 text-gray-600 text-sm font-medium rounded-lg hover:bg-gray-200 transition-colors"
              >
                閉じる
              </button>
            </div>
          </form>

          {/* Import status */}
          {importStatus && (
            <div className="bg-white/60 rounded-lg p-3 text-xs space-y-1">
              <div className="font-semibold text-indigo-700">@{importStatus.account} のインポート状況</div>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mt-2">
                <div className="text-center">
                  <div className="text-[10px] text-gray-400">TikTok上</div>
                  <div className="font-bold text-gray-700">{importStatus.total_on_tiktok ?? "—"}</div>
                </div>
                <div className="text-center">
                  <div className="text-[10px] text-gray-400">追跡中</div>
                  <div className="font-bold text-green-600">{importStatus.tracked}</div>
                </div>
                <div className="text-center">
                  <div className="text-[10px] text-gray-400">アクティブ</div>
                  <div className="font-bold text-blue-600">{importStatus.active}</div>
                </div>
                <div className="text-center">
                  <div className="text-[10px] text-gray-400">停止</div>
                  <div className="font-bold text-gray-500">{importStatus.stopped}</div>
                </div>
                <div className="text-center">
                  <div className="text-[10px] text-gray-400">ClipDB紐付け</div>
                  <div className="font-bold text-indigo-600">{importStatus.matched_to_clipdb}</div>
                </div>
              </div>
              {importStatus.total_on_tiktok && importStatus.tracked < importStatus.total_on_tiktok && (
                <div className="mt-2 text-[10px] text-amber-600">
                  ⚠ 未インポート: {importStatus.total_on_tiktok - importStatus.tracked}件
                </div>
              )}
            </div>
          )}

          {/* Import result */}
          {importResult && (
            <div className="bg-white/60 rounded-lg p-3 text-xs space-y-2">
              <div className="flex items-center justify-between">
                <div className="font-semibold text-green-700">✅ インポート完了</div>
                <button onClick={() => setImportResult(null)} className="text-gray-400 hover:text-gray-600 text-sm">✕</button>
              </div>
              <div className="text-gray-600">
                @{importResult.user?.unique_id} ({importResult.user?.nickname})
              </div>
              <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mt-1">
                <div className="text-center bg-green-50 rounded-lg p-2">
                  <div className="text-[10px] text-gray-400">インポート</div>
                  <div className="font-bold text-green-600">{importResult.imported}</div>
                </div>
                <div className="text-center bg-blue-50 rounded-lg p-2">
                  <div className="text-[10px] text-gray-400">マッチ済</div>
                  <div className="font-bold text-blue-600">{importResult.matched}</div>
                </div>
                <div className="text-center bg-amber-50 rounded-lg p-2">
                  <div className="text-[10px] text-gray-400">未マッチ</div>
                  <div className="font-bold text-amber-600">{importResult.unmatched}</div>
                </div>
                <div className="text-center bg-gray-50 rounded-lg p-2">
                  <div className="text-[10px] text-gray-400">既存スキップ</div>
                  <div className="font-bold text-gray-500">{importResult.skipped_existing}</div>
                </div>
                <div className="text-center bg-red-50 rounded-lg p-2">
                  <div className="text-[10px] text-gray-400">エラー</div>
                  <div className="font-bold text-red-500">{importResult.errors}</div>
                </div>
                <div className="text-center bg-indigo-50 rounded-lg p-2">
                  <div className="text-[10px] text-gray-400">処理合計</div>
                  <div className="font-bold text-indigo-600">{importResult.total_processed}</div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Register single video form */}
      {subView === "register" && (
        <div className="bg-gradient-to-r from-pink-50 to-rose-50 border border-pink-200 rounded-xl p-4 space-y-3">
          <div className="text-sm font-semibold text-pink-700">TikTok動画URLを登録</div>
          <form onSubmit={handleRegister} className="space-y-2">
            <input
              type="url"
              value={registerUrl}
              onChange={(e) => setRegisterUrl(e.target.value)}
              placeholder="https://www.tiktok.com/@user/video/1234567890"
              className="w-full px-3 py-2 text-sm border border-pink-200 rounded-lg focus:ring-2 focus:ring-pink-300 focus:border-pink-400 outline-none"
              required
            />
            <div className="flex gap-2">
              <input
                type="text"
                value={registerLabel}
                onChange={(e) => setRegisterLabel(e.target.value)}
                placeholder="ラベル（任意：例「新商品PR」）"
                className="flex-1 px-3 py-2 text-sm border border-pink-200 rounded-lg focus:ring-2 focus:ring-pink-300 focus:border-pink-400 outline-none"
              />
              <input
                type="text"
                value={registerClipDbId}
                onChange={(e) => setRegisterClipDbId(e.target.value)}
                placeholder="Clip DB ID（任意）"
                className="w-48 px-3 py-2 text-sm border border-pink-200 rounded-lg focus:ring-2 focus:ring-pink-300 focus:border-pink-400 outline-none"
              />
            </div>
            {registerError && (
              <div className="text-xs text-red-500 bg-red-50 px-3 py-1.5 rounded-lg">{registerError}</div>
            )}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={registering}
                className="px-4 py-2 bg-pink-500 text-white text-sm font-medium rounded-lg hover:bg-pink-600 disabled:opacity-50 transition-colors"
              >
                {registering ? "登録中..." : "追跡開始"}
              </button>
              <button
                type="button"
                onClick={() => { setSubView("list"); setRegisterError(null); }}
                className="px-4 py-2 bg-gray-100 text-gray-600 text-sm font-medium rounded-lg hover:bg-gray-200 transition-colors"
              >
                キャンセル
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Auto-match result notification */}
      {registerResult && (
        <div className={`rounded-xl border p-4 space-y-2 ${
          registerResult.auto_match
            ? "bg-gradient-to-r from-green-50 to-emerald-50 border-green-200"
            : registerResult.auto_match === null
              ? "bg-gradient-to-r from-yellow-50 to-amber-50 border-yellow-200"
              : "bg-gradient-to-r from-blue-50 to-indigo-50 border-blue-200"
        }`}>
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold">
              {registerResult.auto_match
                ? "🎯 自動マッチング成功！"
                : registerResult.auto_match === null
                  ? "⚠️ 一致するクリップが見つかりませんでした"
                  : "✅ 動画を登録しました"}
            </div>
            <button onClick={() => setRegisterResult(null)} className="text-gray-400 hover:text-gray-600 text-sm">✕</button>
          </div>
          {registerResult.auto_match && (
            <div className="text-xs space-y-1">
              <div className="text-green-700">
                <span className="font-medium">マッチしたClip ID:</span> {registerResult.auto_match.clip_db_id}
              </div>
              <div className="text-green-600">
                <span className="font-medium">類似度:</span> {(registerResult.auto_match.similarity * 100).toFixed(1)}%
                <span className="ml-2 font-medium">方法:</span> {registerResult.auto_match.match_method === 'fingerprint' ? '音声フィンガープリント' : registerResult.auto_match.match_method === 'duration' ? '秒数マッチ' : registerResult.auto_match.match_method}
                {registerResult.auto_match.clip_duration && (
                  <span className="ml-2"><span className="font-medium">クリップ秒数:</span> {registerResult.auto_match.clip_duration}秒</span>
                )}
              </div>
            </div>
          )}
          <div className="text-xs text-gray-500">
            TikTok: @{registerResult.account_name} | 再生: {fmtNum(registerResult.initial_snapshot?.play_count)} | 秒数: {registerResult.duration}秒
          </div>
        </div>
      )}

      {/* Account filter indicator */}
      {accountFilter && (
        <div className="flex items-center gap-2 bg-teal-50 border border-teal-200 rounded-lg px-3 py-2">
          <span className="text-xs text-teal-700">🔍 @{accountFilter} の動画を表示中</span>
          <button
            onClick={() => setAccountFilter("")}
            className="text-xs text-teal-500 hover:text-teal-700 underline"
          >
            フィルター解除
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-600">
          {error}
          <button onClick={fetchVideos} className="ml-2 text-red-500 underline text-xs">リトライ</button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-pink-500"></div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && videos.length === 0 && (
        <div className="text-center py-12 text-gray-400">
          <div className="text-4xl mb-3">📊</div>
          <p className="text-sm">追跡中のTikTok動画はありません</p>
          <p className="text-xs mt-1">「📥 アカウント一括」で全投稿を自動インポートするか、「+ URL登録」で個別に追加してください</p>
        </div>
      )}

      {/* Video cards grid with inline detail panel */}
      {!loading && filteredVideos.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {gridItems.map((item, idx) => {
            if (item.type === "detail") {
              // Inline detail panel spanning full width
              return (
                <div key="detail-panel" className="col-span-1 md:col-span-2 xl:col-span-3">
                  <div className="bg-white rounded-xl border border-pink-200 p-4 shadow-md">
                    <div className="flex items-center justify-between mb-3">
                      <div>
                        <h3 className="text-sm font-bold text-gray-800">
                          📈 {selectedVideo.label || selectedVideo.title || "無題"} — パフォーマンス推移
                        </h3>
                        <div className="text-[10px] text-gray-400 mt-0.5">
                          @{selectedVideo.account_name}
                          {selectedVideo.clip_db_id && (
                            <>
                              <span className="ml-2 text-indigo-500">🔗 Clip: {selectedVideo.clip_db_id.slice(0, 8)}...</span>
                              {selectedVideo.is_aitherhub_edited
                                ? <span className="ml-1 px-1.5 py-0.5 rounded text-[9px] font-medium bg-emerald-100 text-emerald-700">✅ AH編集済</span>
                                : <span className="ml-1 px-1.5 py-0.5 rounded text-[9px] font-medium bg-gray-100 text-gray-400">📎 対象外</span>
                              }
                            </>
                          )}
                          {selectedVideo.tiktok_url && (
                            <>
                              {" | "}
                              <a href={selectedVideo.tiktok_url} target="_blank" rel="noopener noreferrer"
                                className="text-blue-400 hover:text-blue-600 underline" onClick={(e) => e.stopPropagation()}>
                                TikTokで開く ↗
                              </a>
                            </>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setPlayerVideo(selectedVideo)}
                          className="px-2 py-1 text-[10px] font-medium bg-purple-50 text-purple-600 rounded-md hover:bg-purple-100 transition-colors"
                        >
                          ▶ 動画を見る
                        </button>
                        <button onClick={() => { setSelectedVideo(null); setSnapshots([]); }} className="text-gray-400 hover:text-gray-600 text-lg">✕</button>
                      </div>
                    </div>

                    {snapshotsLoading ? (
                      <div className="flex items-center justify-center py-12">
                        <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-pink-500"></div>
                      </div>
                    ) : snapshots.length === 0 ? (
                      <div className="text-center py-8 text-gray-400 text-sm">
                        スナップショットデータがありません。「今すぐ取得」でデータを追加してください。
                      </div>
                    ) : (
                      <HighchartsReact highcharts={Highcharts} options={chartOptions} />
                    )}

                    {/* Snapshot table */}
                    {snapshots.length > 0 && (
                      <div className="mt-4 overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-gray-100 text-gray-400">
                              <th className="text-left py-1.5 px-2 font-medium">日時</th>
                              <th className="text-right py-1.5 px-2 font-medium">再生数</th>
                              <th className="text-right py-1.5 px-2 font-medium">いいね</th>
                              <th className="text-right py-1.5 px-2 font-medium">コメント</th>
                              <th className="text-right py-1.5 px-2 font-medium">シェア</th>
                              <th className="text-right py-1.5 px-2 font-medium">保存</th>
                            </tr>
                          </thead>
                          <tbody>
                            {snapshots.slice().reverse().slice(0, 20).map((s, i) => (
                              <tr key={s.id || i} className="border-b border-gray-50 hover:bg-gray-50">
                                <td className="py-1.5 px-2 text-gray-500">
                                  {new Date(s.fetched_at).toLocaleString("ja-JP", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                                </td>
                                <td className="text-right py-1.5 px-2 font-medium text-red-500">{fmtNum(s.play_count)}</td>
                                <td className="text-right py-1.5 px-2 font-medium text-pink-500">{fmtNum(s.digg_count)}</td>
                                <td className="text-right py-1.5 px-2 font-medium text-blue-500">{fmtNum(s.comment_count)}</td>
                                <td className="text-right py-1.5 px-2 font-medium text-green-500">{fmtNum(s.share_count)}</td>
                                <td className="text-right py-1.5 px-2 font-medium text-amber-500">{fmtNum(s.collect_count)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {snapshots.length > 20 && (
                          <div className="text-center text-[10px] text-gray-400 mt-2">
                            最新20件を表示中（全{snapshots.length}件）
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            }

            // Video card
            const video = item.video;
            const snap = video.latest_snapshot ? (typeof video.latest_snapshot === 'string' ? JSON.parse(video.latest_snapshot) : video.latest_snapshot) : null;
            const isSelected = selectedVideo?.id === video.id;
            return (
              <div
                key={video.id}
                onClick={() => handleSelectVideo(video)}
                className={`bg-white rounded-xl border transition-all cursor-pointer hover:shadow-md ${
                  isSelected ? "border-pink-400 shadow-lg ring-2 ring-pink-100" : "border-gray-200"
                }`}
              >
                {/* Card header */}
                <div className="p-3 flex gap-3">
                  {video.cover_url && (
                    <div className="w-16 h-20 rounded-lg overflow-hidden flex-shrink-0 bg-gray-100">
                      <img src={video.cover_url} alt="" className="w-full h-full object-cover" onError={(e) => { e.target.style.display = "none"; }} />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-1">
                      <div className="min-w-0">
                        <div className="text-xs font-semibold text-gray-800 truncate">{video.label || video.title || "無題"}</div>
                        <div className="text-[10px] text-gray-400 mt-0.5">@{video.account_name || "—"}</div>
                      </div>
                      <div className="flex flex-col items-end gap-0.5 flex-shrink-0">
                        <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${
                          video.status === "active" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
                        }`}>
                          {video.status === "active" ? "追跡中" : "停止"}
                        </span>
                        {video.clip_db_id && video.is_aitherhub_edited && (
                          <span className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-emerald-100 text-emerald-700">
                            ✅ AH編集済
                          </span>
                        )}
                        {video.clip_db_id && !video.is_aitherhub_edited && (
                          <button
                            onClick={(e) => { e.stopPropagation(); handleRematch(video.id); }}
                            disabled={rematchingId === video.id}
                            className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-orange-100 text-orange-600 hover:bg-orange-200 disabled:opacity-50 transition-colors"
                          >
                            {rematchingId === video.id ? "🔄..." : "🔄 再マッチ"}
                          </button>
                        )}
                        {!video.clip_db_id && (
                          <button
                            onClick={(e) => { e.stopPropagation(); handleRematch(video.id); }}
                            disabled={rematchingId === video.id}
                            className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-orange-100 text-orange-600 hover:bg-orange-200 disabled:opacity-50 transition-colors"
                          >
                            {rematchingId === video.id ? "🔄..." : "🔄 マッチ"}
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="text-[10px] text-gray-400 mt-1">
                      取得頻度: {getFrequencyLabel(video.created_at)}
                    </div>
                  </div>
                </div>

                {/* Stats row */}
                {snap && (
                  <div className="px-3 pb-2 grid grid-cols-5 gap-1 text-center">
                    <div>
                      <div className="text-[10px] text-gray-400">再生</div>
                      <div className="text-xs font-bold text-red-500">{fmtNum(snap.play_count)}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-gray-400">いいね</div>
                      <div className="text-xs font-bold text-pink-500">{fmtNum(snap.digg_count)}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-gray-400">コメント</div>
                      <div className="text-xs font-bold text-blue-500">{fmtNum(snap.comment_count)}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-gray-400">シェア</div>
                      <div className="text-xs font-bold text-green-500">{fmtNum(snap.share_count)}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-gray-400">保存</div>
                      <div className="text-xs font-bold text-amber-500">{fmtNum(snap.collect_count)}</div>
                    </div>
                  </div>
                )}

                {/* Actions */}
                <div className="px-3 pb-3 flex gap-1.5 border-t border-gray-50 pt-2">
                  <button
                    onClick={(e) => { e.stopPropagation(); handleFetchNow(video.id); }}
                    disabled={fetchingId === video.id}
                    className="flex-1 py-1 text-[10px] font-medium bg-blue-50 text-blue-600 rounded-md hover:bg-blue-100 disabled:opacity-50 transition-colors"
                  >
                    {fetchingId === video.id ? "取得中..." : "📡 今すぐ取得"}
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setPlayerVideo(video); }}
                    className="flex-1 py-1 text-[10px] font-medium bg-purple-50 text-purple-600 rounded-md hover:bg-purple-100 transition-colors"
                  >
                    ▶ 見る
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleToggleStatus(video.id, video.status); }}
                    className={`flex-1 py-1 text-[10px] font-medium rounded-md transition-colors ${
                      video.status === "active"
                        ? "bg-yellow-50 text-yellow-600 hover:bg-yellow-100"
                        : "bg-green-50 text-green-600 hover:bg-green-100"
                    }`}
                  >
                    {video.status === "active" ? "⏸ 停止" : "▶ 再開"}
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(video.id); }}
                    className="px-2 py-1 text-[10px] font-medium bg-red-50 text-red-500 rounded-md hover:bg-red-100 transition-colors"
                  >
                    🗑
                  </button>
                </div>

                {/* Clip DB badges (compact) */}
                {video.clip_db_id && (
                  <div className="px-3 pb-1 flex gap-1.5">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        const url = new URL(window.location.href);
                        url.searchParams.set('tab', 'clip-db');
                        url.searchParams.set('clip_id', video.clip_db_id);
                        window.location.href = url.toString();
                      }}
                      className="flex-1 py-1 text-[10px] font-medium bg-indigo-50 text-indigo-600 rounded-md hover:bg-indigo-100 transition-colors"
                    >
                      🎬 クリップDBで見る
                    </button>
                  </div>
                )}

                {/* Last fetched */}
                <div className="px-3 pb-2 text-[9px] text-gray-300">
                  {video.posted_at && <><span className="text-yellow-400">投稿: {new Date(video.posted_at).toLocaleDateString("ja-JP")}</span>{" | "}</>}
                  最終取得: {video.last_fetched_at ? timeAgo(video.last_fetched_at) : "未取得"}
                  {video.created_at && ` | 登録: ${new Date(video.created_at).toLocaleDateString("ja-JP")}`}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Rematch all button */}
      <div className="bg-white rounded-xl border border-gray-200 p-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-gray-600">🔄 クリップマッチング</span>
          <span className="text-[10px] text-gray-400">
            対象外・未マッチの動画をAIクリップと再紐付け
          </span>
        </div>
        <button
          onClick={handleRematchAll}
          className="px-3 py-1.5 text-[10px] font-medium bg-orange-50 text-orange-600 rounded-lg hover:bg-orange-100 transition-colors"
        >
          🔄 一括再マッチ
        </button>
      </div>

      {/* Fingerprint status bar */}
      {fpStatus && (
        <div className="bg-white rounded-xl border border-gray-200 p-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xs font-medium text-gray-600">🔊 音声フィンガープリント</span>
            <div className="flex items-center gap-2">
              <div className="w-32 h-2 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-green-400 to-emerald-500 rounded-full transition-all"
                  style={{ width: `${fpStatus.coverage_pct}%` }}
                />
              </div>
              <span className="text-[10px] text-gray-500">
                {fpStatus.with_fingerprint}/{fpStatus.total_clips} ({fpStatus.coverage_pct}%)
              </span>
            </div>
          </div>
          {fpStatus.without_fingerprint > 0 && (
            <button
              onClick={handleGenerateFp}
              disabled={fpGenerating}
              className="px-3 py-1.5 text-[10px] font-medium bg-emerald-50 text-emerald-600 rounded-lg hover:bg-emerald-100 disabled:opacity-50 transition-colors"
            >
              {fpGenerating ? "生成中..." : `🔄 未生成${fpStatus.without_fingerprint}件を処理`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── Exported helper: Register from ClipDB ──
export function TikTokUrlRegisterButton({ clipId, adminKey, onSuccess }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const headers = { "X-Admin-Key": adminKey, "Content-Type": "application/json" };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!url.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/tiktok-tracking/register`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          tiktok_url: url.trim(),
          clip_db_id: clipId || null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Error ${res.status}`);
      }
      setUrl("");
      setOpen(false);
      if (onSuccess) onSuccess();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}
        className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-pink-600 bg-pink-50 hover:bg-pink-100 rounded-md transition-colors"
        title="TikTok URLを紐付けて追跡"
      >
        🎵 TikTok追跡
      </button>
    );
  }

  return (
    <div onClick={(e) => e.stopPropagation()} className="mt-1 bg-pink-50 border border-pink-200 rounded-lg p-2 space-y-1.5">
      <form onSubmit={handleSubmit} className="flex gap-1.5">
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="TikTok URL"
          className="flex-1 px-2 py-1 text-[10px] border border-pink-200 rounded-md outline-none focus:ring-1 focus:ring-pink-300"
          required
          autoFocus
        />
        <button type="submit" disabled={loading} className="px-2 py-1 text-[10px] font-medium bg-pink-500 text-white rounded-md hover:bg-pink-600 disabled:opacity-50">
          {loading ? "..." : "登録"}
        </button>
        <button type="button" onClick={() => { setOpen(false); setError(null); }} className="px-1.5 py-1 text-[10px] text-gray-400 hover:text-gray-600">✕</button>
      </form>
      {error && <div className="text-[9px] text-red-500">{error}</div>}
    </div>
  );
}
