import { useState, useEffect } from "react";
import axios from "axios";
import AdminVideoList from "./admin/AdminVideoList";
import AdminVideoDetail from "./admin/AdminVideoDetail";

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

  // Check session on mount
  useEffect(() => {
    if (sessionStorage.getItem(SESSION_KEY) === "true") {
      setAuthenticated(true);
    }
  }, []);

  // Fetch dashboard data after authentication
  useEffect(() => {
    if (!authenticated) return;
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        const baseURL = import.meta.env.VITE_API_BASE_URL;
        const res = await axios.get(`${baseURL}/api/v1/admin/dashboard-public`, {
          headers: { "X-Admin-Key": `${ADMIN_ID}:${ADMIN_PASS}` },
        });
        if (!cancelled) setStats(res.data);
      } catch (err) {
        if (!cancelled) setError("データの取得に失敗しました");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [authenticated]);

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
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-orange-500"></div>
      </div>
    );
  }

  // ── Error ──
  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <p className="text-red-500 text-lg">{error}</p>
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
      </div>
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
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="総採点数" value={summary.total_feedbacks} unit="件" color="orange" />
          <StatCard label="平均スコア" value={summary.average_rating} unit="/ 5" color="blue" />
          <StatCard label="コメント付き" value={summary.with_comments} unit="件" color="green" />
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

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 hover:shadow-md transition-all">
      <div className="flex items-start justify-between gap-4">
        {/* Left: Rating + Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-2">
            <span className={`text-lg font-bold ${ratingColor}`}>{stars}</span>
            <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded">
              {timeRange}
            </span>
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
