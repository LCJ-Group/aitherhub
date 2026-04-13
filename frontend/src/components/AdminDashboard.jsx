import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import AdminVideoList from "./admin/AdminVideoList";
import AdminVideoDetail from "./admin/AdminVideoDetail";
import AdminDiagnostics from "./admin/AdminDiagnostics";
import AdminSystemErrors from "./admin/AdminSystemErrors";
import AdminBugReports from "./admin/AdminBugReports";
import AdminWorkLogs from "./admin/AdminWorkLogs";
import AdminLessons from "./admin/AdminLessons";
import AdminScriptGenerations from "./admin/AdminScriptGenerations";

const ADMIN_ID = "aither";
const ADMIN_PASS = "hub";
const SESSION_KEY = "aitherhub_admin_auth";

export default function AdminDashboard() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loginId, setLoginId] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [loginError, setLoginError] = useState("");

  const [stats, setStats] = useState(null);
  const [feedbackData, setFeedbackData] = useState(null);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState("dashboard"); // "dashboard" | "feedbacks" | "videos"
  const [selectedVideoId, setSelectedVideoId] = useState(null);
  const [uploadHealth, setUploadHealth] = useState(null);
  const [uploadHealthLoading, setUploadHealthLoading] = useState(false);

  // Check session on mount
  useEffect(() => {
    if (sessionStorage.getItem(SESSION_KEY) === "true") {
      setAuthenticated(true);
    }
  }, []);

  // Fetch dashboard data after authentication
  const fetchDashboard = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const baseURL = import.meta.env.VITE_API_BASE_URL;
      const res = await axios.get(`${baseURL}/api/v1/admin/dashboard-public`, {
        headers: { "X-Admin-Key": `${ADMIN_ID}:${ADMIN_PASS}` },
        timeout: 30000, // 30s timeout to handle Azure cold start
      });
      setStats(res.data);
    } catch (err) {
      console.error("Dashboard fetch failed:", err);
      const msg = err.code === 'ECONNABORTED'
        ? 'サーバー接続タイムアウト。リトライしてください。'
        : err.response?.status === 401 || err.response?.status === 403
          ? '認証エラー。再ログインしてください。'
          : `データの取得に失敗しました (${err.message || 'Unknown'})`;
      setError(msg);
      // Auto-logout on auth error
      if (err.response?.status === 401 || err.response?.status === 403) {
        sessionStorage.removeItem(SESSION_KEY);
        setAuthenticated(false);
        setStats(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authenticated) return;
    fetchDashboard();
  }, [authenticated, fetchDashboard]);

  // Safety timeout: if loading takes more than 45s, force stop
  useEffect(() => {
    if (!loading) return;
    const timer = setTimeout(() => {
      setLoading(false);
      if (!stats) {
        setError('読み込みがタイムアウトしました。リトライしてください。');
      }
    }, 45000);
    return () => clearTimeout(timer);
  }, [loading, stats]);

  // Fetch feedbacks when tab switches
  useEffect(() => {
    if (!authenticated || activeTab !== "feedbacks" || feedbackData) return;
    let cancelled = false;
    (async () => {
      try {
        setFeedbackLoading(true);
        const baseURL = import.meta.env.VITE_API_BASE_URL;
        const res = await axios.get(`${baseURL}/api/v1/admin/feedbacks`, {
          headers: { "X-Admin-Key": `${ADMIN_ID}:${ADMIN_PASS}` },
          timeout: 30000,
        });
        if (!cancelled) setFeedbackData(res.data);
      } catch (err) {
        console.error("Failed to fetch feedbacks:", err);
      } finally {
        if (!cancelled) setFeedbackLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [authenticated, activeTab, feedbackData]);

  // Fetch upload health when tab switches
  useEffect(() => {
    if (!authenticated || activeTab !== "upload-health") return;
    let cancelled = false;
    (async () => {
      try {
        setUploadHealthLoading(true);
        const baseURL = import.meta.env.VITE_API_BASE_URL;
        const res = await axios.get(`${baseURL}/api/v1/admin/upload-health`, {
          headers: { "X-Admin-Key": `${ADMIN_ID}:${ADMIN_PASS}` },
          timeout: 30000,
        });
        if (!cancelled) setUploadHealth(res.data);
      } catch (err) {
        console.error("Failed to fetch upload health:", err);
      } finally {
        if (!cancelled) setUploadHealthLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [authenticated, activeTab]);

  const handleLogin = (e) => {
    e.preventDefault();
    if (loginId === ADMIN_ID && loginPass === ADMIN_PASS) {
      sessionStorage.setItem(SESSION_KEY, "true");
      setAuthenticated(true);
      setLoginError("");
    } else {
      setLoginError("IDまたはパスワードが正しくありません");
    }
  };

  // ── Login Screen ──
  if (!authenticated) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-sm">
          <div className="text-center mb-6">
            <h1 className="text-xl font-bold text-gray-800">Aitherhub Admin</h1>
            <p className="text-sm text-gray-400 mt-1">管理者ログイン</p>
          </div>
          <form onSubmit={handleLogin}>
            <div className="mb-4">
              <label className="block text-sm text-gray-600 mb-1">ID</label>
              <input
                type="text"
                value={loginId}
                onChange={(e) => setLoginId(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
                autoFocus
              />
            </div>
            <div className="mb-4">
              <label className="block text-sm text-gray-600 mb-1">パスワード</label>
              <input
                type="password"
                value={loginPass}
                onChange={(e) => setLoginPass(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
              />
            </div>
            {loginError && (
              <p className="text-red-500 text-xs mb-3">{loginError}</p>
            )}
            <button
              type="submit"
              className="w-full bg-orange-500 hover:bg-orange-600 text-white font-medium py-2 rounded-lg transition-colors"
            >
              ログイン
            </button>
          </form>
        </div>
      </div>
    );
  }

  // ── Loading ──
  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center gap-4">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-orange-500"></div>
        <p className="text-gray-400 text-sm">ダッシュボードを読み込み中...</p>
      </div>
    );
  }

  // ── Error ──
  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center gap-4">
        <p className="text-red-500 text-lg">{error}</p>
        <button
          onClick={fetchDashboard}
          className="px-6 py-2 bg-orange-500 hover:bg-orange-600 text-white rounded-lg transition-colors text-sm font-medium"
        >
          リトライ
        </button>
        <button
          onClick={() => {
            sessionStorage.removeItem(SESSION_KEY);
            setAuthenticated(false);
            setStats(null);
            setError(null);
          }}
          className="text-sm text-gray-400 hover:text-gray-600 transition-colors"
        >
          ログイン画面に戻る
        </button>
      </div>
    );
  }

  if (!stats) return null;

  const { data_volume, video_types, user_scale } = stats;

  // ── Dashboard ──
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="w-full max-w-5xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-gray-800">
            Aitherhub マスターダッシュボード
          </h1>
          <button
            onClick={() => {
              sessionStorage.removeItem(SESSION_KEY);
              setAuthenticated(false);
              setStats(null);
              setFeedbackData(null);
            }}
            className="text-sm text-gray-400 hover:text-gray-600 transition-colors"
          >
            ログアウト
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex gap-1 mb-8 bg-gray-100 rounded-lg p-1 w-fit">
          <button
            onClick={() => setActiveTab("dashboard")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "dashboard"
                ? "bg-white text-gray-800 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            ダッシュボード
          </button>
          <button
            onClick={() => setActiveTab("feedbacks")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "feedbacks"
                ? "bg-white text-gray-800 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            フィードバック
          </button>
          <button
            onClick={() => { setActiveTab("videos"); setSelectedVideoId(null); }}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "videos"
                ? "bg-white text-gray-800 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            動画ログ
          </button>
          <button
            onClick={() => setActiveTab("upload-health")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "upload-health"
                ? "bg-white text-gray-800 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            Upload Health
          </button>
          <button
            onClick={() => setActiveTab("diagnostics")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "diagnostics"
                ? "bg-white text-red-600 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            Diagnostics
          </button>
          <button
            onClick={() => setActiveTab("system-errors")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "system-errors"
                ? "bg-white text-red-600 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            System Errors
          </button>
          <button
            onClick={() => setActiveTab("bug-reports")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "bug-reports"
                ? "bg-white text-orange-600 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            Bug Reports
          </button>
          <button
            onClick={() => setActiveTab("work-logs")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "work-logs"
                ? "bg-white text-blue-600 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            Work Logs
          </button>
          <button
            onClick={() => setActiveTab("lessons")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "lessons"
                ? "bg-white text-blue-600 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            🧠 Lessons
          </button>
          <button
            onClick={() => setActiveTab("script-generations")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "script-generations"
                ? "bg-white text-orange-600 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            📝 台本学習
          </button>
        </div>

        {activeTab === "dashboard" && (
          <>
            {/* データ量 (AI資産量) */}
            <section className="mb-8">
              <div className="flex items-center gap-2 mb-4">
                <span className="text-lg">📊</span>
                <h2 className="text-lg font-semibold text-gray-700">データ量</h2>
                <span className="text-xs text-gray-400 ml-1">AI資産量</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard label="総動画数" value={data_volume.total_videos} unit="本" color="orange" />
                <StatCard label="解析済" value={data_volume.analyzed_videos} unit="本" color="green" />
                <StatCard label="解析待ち" value={data_volume.pending_videos} unit="本" color="yellow" />
                <StatCard label="総動画時間" value={data_volume.total_duration_display} color="blue" />
              </div>
            </section>

            {/* 動画タイプ (データ構造) */}
            <section className="mb-8">
              <div className="flex items-center gap-2 mb-4">
                <span className="text-lg">🎬</span>
                <h2 className="text-lg font-semibold text-gray-700">動画タイプ</h2>
                <span className="text-xs text-gray-400 ml-1">データ構造</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <StatCard label="画面収録数" value={video_types.screen_recording_count} unit="本" color="purple" />
                <StatCard label="クリーン動画数" value={video_types.clean_video_count} unit="本" color="indigo" />
                <StatCard label="最新アップ日" value={formatDate(video_types.latest_upload)} color="gray" small />
              </div>
            </section>

            {/* 会員規模 (母数) */}
            <section className="mb-8">
              <div className="flex items-center gap-2 mb-4">
                <span className="text-lg">👥</span>
                <h2 className="text-lg font-semibold text-gray-700">会員規模</h2>
                <span className="text-xs text-gray-400 ml-1">母数</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <StatCard label="総ユーザー" value={user_scale.total_users} unit="人" color="orange" />
                <StatCard label="配信者数" value={user_scale.total_streamers} unit="人" color="red" />
                <StatCard label="今月アップ人数" value={user_scale.this_month_uploaders} unit="人" color="teal" />
              </div>
            </section>

            {/* クリップDB (売れる瞬間) */}
            <ClipDBStatsSection />
          </>
        )}

        {activeTab === "feedbacks" && (
          <FeedbackSection data={feedbackData} loading={feedbackLoading} />
        )}

        {activeTab === "videos" && (
          selectedVideoId ? (
            <AdminVideoDetail
              videoId={selectedVideoId}
              adminKey={`${ADMIN_ID}:${ADMIN_PASS}`}
              onBack={() => setSelectedVideoId(null)}
            />
          ) : (
            <AdminVideoList
              adminKey={`${ADMIN_ID}:${ADMIN_PASS}`}
              onSelectVideo={(id) => setSelectedVideoId(id)}
            />
          )
        )}
        {activeTab === "upload-health" && (
          <UploadHealthSection data={uploadHealth} loading={uploadHealthLoading} />
        )}
        {activeTab === "diagnostics" && (
          <AdminDiagnostics adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
        )}
        {activeTab === "system-errors" && (
          <AdminSystemErrors adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
        )}
        {activeTab === "bug-reports" && (
          <AdminBugReports adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
        )}
        {activeTab === "work-logs" && (
          <AdminWorkLogs adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
        )}
        {activeTab === "lessons" && (
          <AdminLessons adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
        )}
        {activeTab === "script-generations" && (
          <AdminScriptGenerations adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
        )}
      </div>
    </div>
  );
}

// ── Upload Health Section ──
function UploadHealthSection({ data, loading }) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-orange-500"></div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="text-center py-16 text-gray-400">
        Upload Healthデータの取得に失敗しました
      </div>
    );
  }

  const { overall, last_24h, last_7d, stuck_videos, status_distribution, recent_uploads, recent_errors, enqueue_stats, pipeline_stages, retry_candidates, recent_stage_events, failed_stage_videos } = data;

  const statusColor = (status) => {
    const map = { DONE: "text-green-600 bg-green-50", ERROR: "text-red-600 bg-red-50", uploaded: "text-blue-600 bg-blue-50", NEW: "text-gray-600 bg-gray-50" };
    return map[status] || "text-yellow-600 bg-yellow-50";
  };

  return (
    <div>
      {/* Overall */}
      <section className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <span className="text-lg">&#x2705;</span>
          <h2 className="text-lg font-semibold text-gray-700">Upload Health 概要</h2>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="総アップロード" value={overall.total_uploads} unit="件" color="orange" />
          <StatCard label="成功" value={overall.done} unit="件" color="green" />
          <StatCard label="エラー" value={overall.error} unit="件" color="red" />
          <StatCard label="処理中" value={overall.processing} unit="件" color="yellow" />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mt-4">
          <StatCard label="成功率" value={`${overall.success_rate_pct}%`} color="green" />
          <StatCard label="エラー率" value={`${overall.error_rate_pct}%`} color="red" />
          <StatCard label="スタック" value={stuck_videos} unit="件" color={stuck_videos > 0 ? "red" : "gray"} />
        </div>
      </section>

      {/* Time-based */}
      <section className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <span className="text-lg">&#x23F0;</span>
          <h2 className="text-lg font-semibold text-gray-700">期間別</h2>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-xl border border-blue-200 bg-blue-50 p-4">
            <p className="text-xs text-gray-500 mb-2">過去24時間</p>
            <div className="flex gap-4">
              <div><span className="text-sm text-gray-500">UP</span> <span className="text-lg font-bold text-blue-600">{last_24h.uploads}</span></div>
              <div><span className="text-sm text-gray-500">OK</span> <span className="text-lg font-bold text-green-600">{last_24h.done}</span></div>
              <div><span className="text-sm text-gray-500">NG</span> <span className="text-lg font-bold text-red-600">{last_24h.error}</span></div>
            </div>
          </div>
          <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-4">
            <p className="text-xs text-gray-500 mb-2">過去7日間</p>
            <div className="flex gap-4">
              <div><span className="text-sm text-gray-500">UP</span> <span className="text-lg font-bold text-indigo-600">{last_7d.uploads}</span></div>
              <div><span className="text-sm text-gray-500">OK</span> <span className="text-lg font-bold text-green-600">{last_7d.done}</span></div>
              <div><span className="text-sm text-gray-500">NG</span> <span className="text-lg font-bold text-red-600">{last_7d.error}</span></div>
            </div>
          </div>
        </div>
      </section>

      {/* Status Distribution */}
      <section className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <span className="text-lg">&#x1F4CA;</span>
          <h2 className="text-lg font-semibold text-gray-700">ステータス分布</h2>
        </div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(status_distribution).map(([status, count]) => (
            <span key={status} className={`px-3 py-1 rounded-full text-sm font-medium ${statusColor(status)}`}>
              {status}: {count}
            </span>
          ))}
        </div>
      </section>

      {/* Recent Uploads */}
      <section className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <span className="text-lg">&#x1F4C4;</span>
          <h2 className="text-lg font-semibold text-gray-700">最近のアップロード</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left py-2 px-3 text-gray-500 font-medium">ファイル名</th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">ステータス</th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">タイプ</th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">ユーザー</th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">日時</th>
              </tr>
            </thead>
            <tbody>
              {recent_uploads.map((u, i) => (
                <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="py-2 px-3 truncate max-w-[200px]" title={u.filename}>{u.filename || "--"}</td>
                  <td className="py-2 px-3"><span className={`px-2 py-0.5 rounded text-xs font-medium ${statusColor(u.status)}`}>{u.status}</span></td>
                  <td className="py-2 px-3 text-gray-500">{u.upload_type || "--"}</td>
                  <td className="py-2 px-3 text-gray-500 truncate max-w-[150px]" title={u.user_email}>{u.user_email || "--"}</td>
                  <td className="py-2 px-3 text-gray-400 text-xs">{u.created_at || "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Enqueue Stats */}
      {enqueue_stats && (
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">&#x1F4E8;</span>
            <h2 className="text-lg font-semibold text-gray-700">Enqueue 統計</h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="Enqueue OK" value={enqueue_stats.total_ok} unit="件" color="green" />
            <StatCard label="Enqueue FAILED" value={enqueue_stats.total_failed} unit="件" color="red" />
            <StatCard label="OK (24h)" value={enqueue_stats.ok_last_24h} unit="件" color="green" />
            <StatCard label="FAILED (24h)" value={enqueue_stats.failed_last_24h} unit="件" color="red" />
          </div>
          {enqueue_stats.enqueue_success_rate_pct != null && (
            <div className="mt-3">
              <StatCard label="Enqueue 成功率" value={`${enqueue_stats.enqueue_success_rate_pct}%`} color={enqueue_stats.enqueue_success_rate_pct >= 95 ? "green" : "red"} />
            </div>
          )}
        </section>
      )}

      {/* Pipeline Stages */}
      {pipeline_stages && (
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">&#x2699;&#xFE0F;</span>
            <h2 className="text-lg font-semibold text-gray-700">パイプラインステージ</h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <StatCard label="アップロード待ち" value={pipeline_stages.uploaded_waiting} unit="件" color="blue" />
            <StatCard label="処理中" value={pipeline_stages.processing} unit="件" color="yellow" />
            <StatCard label="完了" value={pipeline_stages.done} unit="件" color="green" />
            <StatCard label="エラー" value={pipeline_stages.error} unit="件" color="red" />
            <StatCard label="Enqueue失敗" value={pipeline_stages.enqueue_failed} unit="件" color="red" />
            <StatCard label="スタック(>2h)" value={pipeline_stages.stuck_gt_2h} unit="件" color={pipeline_stages.stuck_gt_2h > 0 ? "red" : "gray"} />
          </div>
        </section>
      )}

      {/* Retry Candidates */}
      {retry_candidates && retry_candidates.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">&#x1F504;</span>
            <h2 className="text-lg font-semibold text-orange-600">リトライ候補 (Enqueue失敗)</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">ファイル名</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">ステータス</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">エラー内容</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">ユーザー</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">日時</th>
                </tr>
              </thead>
              <tbody>
                {retry_candidates.map((r, i) => (
                  <tr key={i} className="border-b border-gray-100 hover:bg-orange-50">
                    <td className="py-2 px-3 truncate max-w-[200px]" title={r.filename}>{r.filename || "--"}</td>
                    <td className="py-2 px-3"><span className={`px-2 py-0.5 rounded text-xs font-medium ${statusColor(r.status)}`}>{r.status}</span></td>
                    <td className="py-2 px-3 text-red-500 text-xs truncate max-w-[200px]" title={r.enqueue_error}>{r.enqueue_error || "--"}</td>
                    <td className="py-2 px-3 text-gray-500 truncate max-w-[150px]" title={r.user_email}>{r.user_email || "--"}</td>
                    <td className="py-2 px-3 text-gray-400 text-xs">{r.created_at || "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Failed Stage Videos */}
      {failed_stage_videos && failed_stage_videos.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">&#x1F6A8;</span>
            <h2 className="text-lg font-semibold text-red-600">パイプラインステージエラー</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">ファイル名</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">エラーステージ</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">最終ステージ</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">エラー内容</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">ユーザー</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">日時</th>
                </tr>
              </thead>
              <tbody>
                {failed_stage_videos.map((v, i) => (
                  <tr key={i} className="border-b border-gray-100 hover:bg-red-50">
                    <td className="py-2 px-3 truncate max-w-[180px]" title={v.filename}>{v.filename || "--"}</td>
                    <td className="py-2 px-3"><span className="px-2 py-0.5 rounded text-xs font-medium text-red-700 bg-red-100">{v.error_stage}</span></td>
                    <td className="py-2 px-3"><span className="px-2 py-0.5 rounded text-xs font-medium text-blue-700 bg-blue-100">{v.last_stage}</span></td>
                    <td className="py-2 px-3 text-red-500 text-xs truncate max-w-[200px]" title={v.error_message}>{v.error_message || "--"}</td>
                    <td className="py-2 px-3 text-gray-500 truncate max-w-[120px]" title={v.user_email}>{v.user_email || "--"}</td>
                    <td className="py-2 px-3 text-gray-400 text-xs">{v.created_at || "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Recent Stage Events (errors only) */}
      {recent_stage_events && recent_stage_events.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">&#x1F4DD;</span>
            <h2 className="text-lg font-semibold text-gray-700">最近のステージエラーログ</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">ステージ</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">エラータイプ</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">エラー内容</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">所要時間</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">日時</th>
                </tr>
              </thead>
              <tbody>
                {recent_stage_events.map((e, i) => (
                  <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-2 px-3"><span className="px-2 py-0.5 rounded text-xs font-medium text-red-700 bg-red-100">{e.stage}</span></td>
                    <td className="py-2 px-3 text-gray-600 text-xs">{e.error_type || "--"}</td>
                    <td className="py-2 px-3 text-red-500 text-xs truncate max-w-[250px]" title={e.error_message}>{e.error_message || "--"}</td>
                    <td className="py-2 px-3 text-gray-500 text-xs">{e.duration_ms != null ? `${e.duration_ms}ms` : "--"}</td>
                    <td className="py-2 px-3 text-gray-400 text-xs">{e.created_at || "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Recent Errors */}
      {recent_errors.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">&#x26A0;&#xFE0F;</span>
            <h2 className="text-lg font-semibold text-red-600">最近のエラー (7日間)</h2>
          </div>
          <div className="space-y-2">
            {recent_errors.map((e, i) => (
              <div key={i} className="rounded-lg border border-red-200 bg-red-50 p-3 flex justify-between items-center">
                <div>
                  <p className="text-sm font-medium text-red-700 truncate max-w-[300px]">{e.filename || e.video_id}</p>
                  <p className="text-xs text-red-400">{e.user_email}</p>
                </div>
                <p className="text-xs text-red-400">{e.created_at}</p>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ── Feedback Section ──
function FeedbackSection({ data, loading }) {
  const [filterRating, setFilterRating] = useState(0); // 0 = all

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-orange-500"></div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="text-center py-16 text-gray-400">
        フィードバックデータの取得に失敗しました
      </div>
    );
  }

  const { summary, feedbacks } = data;
  const filtered = filterRating === 0
    ? feedbacks
    : feedbacks.filter((f) => f.user_rating === filterRating);

  return (
    <div>
      {/* Summary Cards */}
      <section className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <span className="text-lg">⭐</span>
          <h2 className="text-lg font-semibold text-gray-700">フィードバック概要</h2>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <StatCard label="総採点数" value={summary.total_feedbacks} unit="件" color="orange" />
          <StatCard label="平均スコア" value={summary.average_rating} unit="/ 5" color="blue" />
          <StatCard label="コメント付き" value={summary.with_comments} unit="件" color="green" />
          <StatCard label="ダウンロード済" value={summary.downloaded_clips || 0} unit="件" color="teal" />
          <div className="rounded-xl border p-4 border-purple-300 bg-purple-50 transition-all duration-200 hover:shadow-md">
            <p className="text-xs text-gray-500 mb-2">スコア分布</p>
            <div className="flex items-end gap-1 h-8">
              {[1, 2, 3, 4, 5].map((star) => {
                const count = summary.rating_distribution[star] || 0;
                const maxCount = Math.max(...Object.values(summary.rating_distribution), 1);
                const height = Math.max((count / maxCount) * 100, 8);
                return (
                  <div key={star} className="flex flex-col items-center flex-1">
                    <div
                      className="w-full bg-purple-400 rounded-t"
                      style={{ height: `${height}%` }}
                      title={`${star}点: ${count}件`}
                    />
                    <span className="text-[10px] text-gray-500 mt-1">{star}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      {/* Filter */}
      <div className="flex items-center gap-2 mb-4">
        <span className="text-sm text-gray-500">フィルタ:</span>
        <button
          onClick={() => setFilterRating(0)}
          className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
            filterRating === 0
              ? "bg-orange-500 text-white"
              : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          すべて ({summary.total_feedbacks})
        </button>
        {[1, 2, 3, 4, 5].map((star) => (
          <button
            key={star}
            onClick={() => setFilterRating(star)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
              filterRating === star
                ? "bg-orange-500 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            {"★".repeat(star)} ({summary.rating_distribution[star] || 0})
          </button>
        ))}
      </div>

      {/* Feedback List */}
      <div className="space-y-3">
        {filtered.length === 0 ? (
          <div className="text-center py-12 text-gray-400">
            該当するフィードバックはありません
          </div>
        ) : (
          filtered.map((fb, idx) => (
            <FeedbackCard key={`${fb.video_id}-${fb.phase_index}-${idx}`} fb={fb} />
          ))
        )}
      </div>
    </div>
  );
}

// ── Feedback Card ──
function FeedbackCard({ fb }) {
  const stars = "★".repeat(fb.user_rating) + "☆".repeat(5 - fb.user_rating);
  const ratingColor =
    fb.user_rating >= 4
      ? "text-green-600"
      : fb.user_rating >= 3
      ? "text-yellow-600"
      : "text-red-500";

  const timeRange = formatSeconds(fb.time_start) + " – " + formatSeconds(fb.time_end);

  const handleClick = () => {
    // Navigate to clip editor with phase_index and time range as query params
    const params = new URLSearchParams({
      phase: fb.phase_index,
      t_start: fb.time_start,
      t_end: fb.time_end,
      open_editor: '1',
    });
    window.open(`/video/${fb.video_id}?${params.toString()}`, '_blank');
  };

  return (
    <div
      className="bg-white rounded-xl border border-gray-200 p-4 hover:shadow-md hover:border-orange-300 transition-all cursor-pointer group"
      onClick={handleClick}
      title="クリックしてクリップエディタを開く"
    >
      <div className="flex items-start justify-between gap-4">
        {/* Left: Rating + Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-2">
            <span className={`text-lg font-bold ${ratingColor}`}>{stars}</span>
            <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded">
              {timeRange}
            </span>
            {fb.download_count > 0 && (
              <span className="text-xs font-semibold text-blue-600 bg-blue-50 border border-blue-200 px-2 py-0.5 rounded-full">
                ⬇️ {fb.download_count}回
              </span>
            )}
          </div>

          {fb.user_comment && (
            <div className="bg-orange-50 border-l-3 border-orange-400 pl-3 py-2 mb-2 rounded-r">
              <p className="text-sm text-gray-700">{fb.user_comment}</p>
            </div>
          )}

          {fb.summary && (
            <p className="text-xs text-gray-500 line-clamp-2">{fb.summary}</p>
          )}
        </div>

        {/* Right: Meta */}
        <div className="text-right shrink-0">
          <p className="text-xs text-gray-500 font-medium truncate max-w-[180px]" title={fb.video_name}>
            {fb.video_name}
          </p>
          <p className="text-[10px] text-gray-400 mt-1">
            {fb.user_email || fb.user_id}
          </p>
          {fb.rated_at && (
            <p className="text-[10px] text-gray-400 mt-0.5">
              {formatDate(fb.rated_at)}
            </p>
          )}
          <p className="text-[10px] text-orange-400 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
            → エディタを開く
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Stat Card ──
function StatCard({ label, value, unit, color = "gray", small = false }) {
  const colorMap = {
    orange: "border-orange-300 bg-orange-50",
    green: "border-green-300 bg-green-50",
    yellow: "border-yellow-300 bg-yellow-50",
    blue: "border-blue-300 bg-blue-50",
    purple: "border-purple-300 bg-purple-50",
    indigo: "border-indigo-300 bg-indigo-50",
    red: "border-red-300 bg-red-50",
    teal: "border-teal-300 bg-teal-50",
    gray: "border-gray-300 bg-gray-50",
  };
  const textColorMap = {
    orange: "text-orange-600",
    green: "text-green-600",
    yellow: "text-yellow-600",
    blue: "text-blue-600",
    purple: "text-purple-600",
    indigo: "text-indigo-600",
    red: "text-red-600",
    teal: "text-teal-600",
    gray: "text-gray-600",
  };

  return (
    <div className={`rounded-xl border p-4 ${colorMap[color] || colorMap.gray} transition-all duration-200 hover:shadow-md`}>
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`${small ? "text-lg" : "text-2xl"} font-bold ${textColorMap[color] || textColorMap.gray}`}>
        {value}
        {unit && <span className="text-sm font-normal ml-1">{unit}</span>}
      </p>
    </div>
  );
}

// ─── Clip DB Stats Section ───
function ClipDBStatsSection() {
  const [clipStats, setClipStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const baseURL = import.meta.env.VITE_API_BASE_URL;
        const res = await axios.get(`${baseURL}/api/v1/clip-db/stats`, {
          headers: { "X-Admin-Key": "aither:hub" },
          timeout: 15000,
        });
        setClipStats(res.data);
      } catch (e) {
        console.warn("ClipDB stats fetch failed:", e);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return (
      <section className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <span className="text-lg">🎬</span>
          <h2 className="text-lg font-semibold text-gray-700">クリップDB</h2>
          <span className="text-xs text-gray-400 ml-1">売れる瞬間</span>
        </div>
        <div className="text-sm text-gray-400">読み込み中...</div>
      </section>
    );
  }

  if (!clipStats) return null;

  const TAG_COLORS = {
    '共感': '#92400E', '権威': '#1E40AF', '限定性': '#9D174D',
    '実演': '#065F46', '比較': '#3730A3', 'ストーリー': '#991B1B',
    'テンション': '#9A3412', '緊急性': '#854D0E', '社会的証明': '#166534',
    '価格訴求': '#047857', '問題提起': '#9F1239', '解決提示': '#0C4A6E',
  };

  return (
    <section className="mb-8">
      <div className="flex items-center gap-2 mb-4">
        <span className="text-lg">🎬</span>
        <h2 className="text-lg font-semibold text-gray-700">クリップDB</h2>
        <span className="text-xs text-gray-400 ml-1">売れる瞬間</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        <StatCard label="総クリップ" value={clipStats.total_clips} unit="件" color="purple" />
        <StatCard label="売れた" value={clipStats.sold_clips} unit="件" color="green" />
        <StatCard label="未売" value={clipStats.unsold_clips} unit="件" color="gray" />
        <StatCard label="総GMV" value={clipStats.total_gmv >= 10000 ? `¥${(clipStats.total_gmv / 10000).toFixed(1)}万` : `¥${Math.round(clipStats.total_gmv || 0).toLocaleString()}`} color="blue" />
      </div>

      {/* Top tags */}
      {clipStats.top_tags && clipStats.top_tags.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4 mb-4">
          <h3 className="text-sm font-semibold text-gray-600 mb-3">トップタグ（売れた理由）</h3>
          <div className="flex flex-wrap gap-2">
            {clipStats.top_tags.slice(0, 12).map((t, i) => {
              const color = TAG_COLORS[t.tag] || '#374151';
              return (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border"
                  style={{ color, backgroundColor: color + '12', borderColor: color + '30' }}
                >
                  {t.tag}
                  <span className="text-[10px] opacity-60">{t.count}</span>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Top products */}
      {clipStats.top_products && clipStats.top_products.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="text-sm font-semibold text-gray-600 mb-3">トップ商品</h3>
          <div className="space-y-1.5">
            {clipStats.top_products.slice(0, 6).map((p, i) => (
              <div key={i} className="flex items-center justify-between text-xs py-1 border-b border-gray-50 last:border-0">
                <span className="text-gray-700 truncate flex-1">{p.product}</span>
                <span className="text-gray-400 mx-2">{p.count}件</span>
                <span className="font-semibold text-green-600">
                  {p.gmv >= 10000 ? `¥${(p.gmv / 10000).toFixed(1)}万` : `¥${Math.round(p.gmv || 0).toLocaleString()}`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function formatDate(dateStr) {
  if (!dateStr) return "—";
  try {
    const d = new Date(dateStr);
    return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}`;
  } catch {
    return dateStr;
  }
}

function formatSeconds(sec) {
  if (sec == null) return "--:--";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
