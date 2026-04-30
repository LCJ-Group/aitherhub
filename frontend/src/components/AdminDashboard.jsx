import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import axios from "axios";
import AdminVideoList from "./admin/AdminVideoList";
import AdminVideoDetail from "./admin/AdminVideoDetail";
import AdminDiagnostics from "./admin/AdminDiagnostics";
import AdminSystemErrors from "./admin/AdminSystemErrors";
import AdminBugReports from "./admin/AdminBugReports";
import AdminWorkLogs from "./admin/AdminWorkLogs";
import AdminLessons from "./admin/AdminLessons";
import AdminScriptGenerations from "./admin/AdminScriptGenerations";
import AdminClipDB from "./admin/AdminClipDB";
import AdminWidgetManager from "./admin/AdminWidgetManager";
import SubtitleDictionary from "./SubtitleDictionary";
import AdminReviewerManager from "./AdminReviewerManager";
import { useTranslation } from 'react-i18next';
import { changeLanguage } from '../i18n';
import ClipFeedbackPanel from './ClipFeedbackPanel';
import VideoService from '../base/services/videoService';

const ADMIN_ID = "aither";
const ADMIN_PASS = "hub";
const SESSION_KEY = "aitherhub_admin_auth";
const REVIEWER_KEY = "aitherhub_reviewer";

export default function AdminDashboard() {
  useTranslation(); // triggers re-render on language change
  const [authenticated, setAuthenticated] = useState(false);
  const [loginId, setLoginId] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginMode, setLoginMode] = useState("admin"); // "admin" or "reviewer"
  const [reviewerInfo, setReviewerInfo] = useState(null); // { id, name, email, token, session_id }
  const [reviewerSessionTimer, setReviewerSessionTimer] = useState(0);

  const [stats, setStats] = useState(null);
  const [feedbackData, setFeedbackData] = useState(null);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [feedbackError, setFeedbackError] = useState(null);
  const [feedbackIncludeUnrated, setFeedbackIncludeUnrated] = useState(true);
  const [feedbackRefreshKey, setFeedbackRefreshKey] = useState(0);
  const [feedbackPage, setFeedbackPage] = useState(1);
  const [feedbackFilterRating, setFeedbackFilterRating] = useState(0);
  const [feedbackClipFilter, setFeedbackClipFilter] = useState(null); // null=all, 'yes'=clip only, 'no'=no clip
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Support ?tab= URL parameter for shareable links
  const urlParams = new URLSearchParams(window.location.search);
  const initialTab = urlParams.get("tab") || "dashboard";
  const [activeTab, setActiveTabRaw] = useState(initialTab);

  // Wrap setActiveTab to also update URL query parameter
  const setActiveTab = useCallback((tab) => {
    setActiveTabRaw(tab);
    const url = new URL(window.location);
    if (tab === "dashboard") {
      url.searchParams.delete("tab");
    } else {
      url.searchParams.set("tab", tab);
    }
    window.history.replaceState({}, "", url);
  }, []);
  const [selectedVideoId, setSelectedVideoId] = useState(null);
  const [uploadHealth, setUploadHealth] = useState(null);
  const [uploadHealthLoading, setUploadHealthLoading] = useState(false);

  // Check session on mount
  useEffect(() => {
    if (sessionStorage.getItem(SESSION_KEY) === "true") {
      setAuthenticated(true);
    }
    // Restore reviewer session if exists
    const savedReviewer = sessionStorage.getItem(REVIEWER_KEY);
    if (savedReviewer) {
      try {
        const info = JSON.parse(savedReviewer);
        setReviewerInfo(info);
        setAuthenticated(true);
        // Default to feedbacks tab for reviewer
        if (!urlParams.get("tab") || !['feedbacks', 'clip-db'].includes(urlParams.get("tab"))) {
          setActiveTabRaw("feedbacks");
        }
      } catch (e) { sessionStorage.removeItem(REVIEWER_KEY); }
    }
  }, []);

  // Reviewer session timer
  useEffect(() => {
    if (!reviewerInfo) return;
    const interval = setInterval(() => setReviewerSessionTimer(t => t + 1), 1000);
    return () => clearInterval(interval);
  }, [reviewerInfo]);

  // Reviewer heartbeat every 60s
  useEffect(() => {
    if (!reviewerInfo?.token) return;
    const baseURL = import.meta.env.VITE_API_BASE_URL;
    const hb = () => axios.post(`${baseURL}/api/v1/reviewer/heartbeat`, {}, {
      headers: { Authorization: `Bearer ${reviewerInfo.token}` }, timeout: 5000
    }).catch(() => {});
    const interval = setInterval(hb, 60000);
    return () => clearInterval(interval);
  }, [reviewerInfo?.token]);

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
        ? [window.__t('adminDashboard_b2d586', 'サーバー接続タイムアウト。リトライしてください。')]
        : err.response?.status === 401 || err.response?.status === 403
          ? [window.__t('adminDashboard_bdf22f', '認証エラー。再ログインしてください。')]
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
        setError(window.__t('adminDashboard_41abbf', '読み込みがタイムアウトしました。リトライしてください。'));
      }
    }, 45000);
    return () => clearTimeout(timer);
  }, [loading, stats]);

  // Fetch feedbacks when tab switches
  useEffect(() => {
    if (!authenticated || activeTab !== "feedbacks") return;
    let cancelled = false;
    (async () => {
      try {
        setFeedbackLoading(true);
        setFeedbackError(null);
        const baseURL = import.meta.env.VITE_API_BASE_URL;
        const params = {
          include_unrated: feedbackIncludeUnrated,
          page: feedbackPage,
          per_page: 50,
          filter_rating: feedbackFilterRating,
          ...(feedbackClipFilter ? { has_clip: feedbackClipFilter } : {}),
        };
        // Retry up to 2 times on failure (cold start / timeout)
        let lastErr = null;
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            const res = await axios.get(`${baseURL}/api/v1/admin/feedbacks`, {
              headers: { "X-Admin-Key": `${ADMIN_ID}:${ADMIN_PASS}` },
              params,
              timeout: 45000,
            });
            if (!cancelled) setFeedbackData(res.data);
            lastErr = null;
            break;
          } catch (err) {
            lastErr = err;
            if (attempt < 2) {
              // Wait before retry (exponential backoff: 2s, 4s)
              await new Promise(r => setTimeout(r, (attempt + 1) * 2000));
            }
          }
        }
        if (lastErr && !cancelled) {
          console.error("Failed to fetch feedbacks after retries:", lastErr);
          setFeedbackError(lastErr.message || "Unknown error");
        }
      } catch (err) {
        if (!cancelled) setFeedbackError(err.message || "Unknown error");
        console.error("Failed to fetch feedbacks:", err);
      } finally {
        if (!cancelled) setFeedbackLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [authenticated, activeTab, feedbackIncludeUnrated, feedbackRefreshKey, feedbackPage, feedbackFilterRating, feedbackClipFilter]);

  // Auto-refresh feedbacks when returning from editor tab (visibilitychange)
  useEffect(() => {
    if (!authenticated || activeTab !== "feedbacks") return;
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        setFeedbackRefreshKey(k => k + 1);
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, [authenticated, activeTab]);

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

  const handleLogin = async (e) => {
    e.preventDefault();
    if (loginMode === "admin") {
      if (loginId === ADMIN_ID && loginPass === ADMIN_PASS) {
        sessionStorage.setItem(SESSION_KEY, "true");
        setAuthenticated(true);
        setLoginError("");
      } else {
        setLoginError(window.__t('adminDashboard_bcc470', 'IDまたはパスワードが正しくありません'));
      }
    } else {
      // Reviewer login via API
      try {
        setLoginError("");
        const baseURL = import.meta.env.VITE_API_BASE_URL;
        const res = await axios.post(`${baseURL}/api/v1/reviewer/login`, {
          email: loginId, password: loginPass
        }, { timeout: 15000 });
        const info = {
          id: res.data.reviewer_id,
          name: res.data.name,
          email: res.data.email,
          token: res.data.access_token,
          session_id: res.data.session_id,
        };
        sessionStorage.setItem(REVIEWER_KEY, JSON.stringify(info));
        setReviewerInfo(info);
        setReviewerSessionTimer(0);
        setActiveTabRaw("feedbacks");
        setAuthenticated(true);
      } catch (err) {
        const detail = err.response?.data?.detail || err.message;
        setLoginError(`ログイン失敗: ${detail}`);
      }
    }
  };

  const handleLogout = async () => {
    if (reviewerInfo?.token) {
      const baseURL = import.meta.env.VITE_API_BASE_URL;
      await axios.post(`${baseURL}/api/v1/reviewer/logout`, {}, {
        headers: { Authorization: `Bearer ${reviewerInfo.token}` }, timeout: 5000
      }).catch(() => {});
    }
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(REVIEWER_KEY);
    setAuthenticated(false);
    setReviewerInfo(null);
    setStats(null);
    setFeedbackData(null);
    setReviewerSessionTimer(0);
  };

  const isReviewer = !!reviewerInfo;
  const formatSessionTime = (seconds) => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return h > 0 ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${m}:${String(s).padStart(2,'0')}`;
  };

  // Reviewer-allowed tabs
  const REVIEWER_TABS = ["feedbacks", "clip-db"];

  // ── Login Screen ──
  if (!authenticated) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-sm">
          <div className="text-center mb-6">
            <h1 className="text-xl font-bold text-gray-800">AitherHub</h1>
            <p className="text-sm text-gray-400 mt-1">
              {loginMode === "admin" ? window.__t('adminDashboard_bdb529', '管理者ログイン') : '採点者ログイン'}
            </p>
          </div>
          {/* Login mode toggle */}
          <div className="flex gap-1 mb-5 bg-gray-100 rounded-lg p-1">
            <button
              type="button"
              onClick={() => { setLoginMode("admin"); setLoginError(""); setLoginId(""); setLoginPass(""); }}
              className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-all ${
                loginMode === "admin" ? "bg-white text-gray-800 shadow-sm" : "text-gray-500"
              }`}
            >管理者</button>
            <button
              type="button"
              onClick={() => { setLoginMode("reviewer"); setLoginError(""); setLoginId(""); setLoginPass(""); }}
              className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-all ${
                loginMode === "reviewer" ? "bg-white text-blue-600 shadow-sm" : "text-gray-500"
              }`}
            >採点者</button>
          </div>
          <form onSubmit={handleLogin}>
            <div className="mb-4">
              <label className="block text-sm text-gray-600 mb-1">{loginMode === "admin" ? "ID" : "メールアドレス"}</label>
              <input
                type={loginMode === "reviewer" ? "email" : "text"}
                value={loginId}
                onChange={(e) => setLoginId(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
                autoFocus
              />
            </div>
            <div className="mb-4">
              <label className="block text-sm text-gray-600 mb-1">{window.__t('password', 'パスワード')}</label>
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
              className={`w-full font-medium py-2 rounded-lg transition-colors text-white ${
                loginMode === "admin" ? "bg-orange-500 hover:bg-orange-600" : "bg-blue-500 hover:bg-blue-600"
              }`}
            >
              ログイン
            </button>
          </form>
        </div>
      </div>
    );
  }

  // ── Loading ── (skip for reviewer — they don't need dashboard stats)
  if (loading && !isReviewer) {
    return (
      <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center gap-4">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-orange-500"></div>
        <p className="text-gray-400 text-sm">{window.__t('adminDashboard_4e4ae3', 'ダッシュボードを読み込み中...')}</p>
      </div>
    );
  }

  // ── Error ── (skip for reviewer)
  if (error && !isReviewer) {
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
          onClick={handleLogout}
          className="text-sm text-gray-400 hover:text-gray-600 transition-colors"
        >
          ログイン画面に戻る
        </button>
      </div>
    );
  }

  if (!stats && !isReviewer) return null;

  const { data_volume, video_types, user_scale } = stats || {};

  // ── Dashboard ──
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="w-full max-w-5xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-800">
              {isReviewer ? 'AitherHub 採点' : 'Aitherhub マスターダッシュボード'}
            </h1>
            {isReviewer && (
              <p className="text-sm text-gray-500 mt-1">
                {reviewerInfo.name} ({reviewerInfo.email})
              </p>
            )}
          </div>
          <div className="flex items-center gap-4">
            {isReviewer && (
              <div className="text-sm text-gray-500 bg-gray-100 px-3 py-1.5 rounded-lg">
                セッション: <span className="font-mono font-medium text-gray-700">{formatSessionTime(reviewerSessionTimer)}</span>
              </div>
            )}
            <button
              onClick={handleLogout}
              className="text-sm text-gray-400 hover:text-gray-600 transition-colors"
            >
              ログアウト
            </button>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex gap-1 mb-8 bg-gray-100 rounded-lg p-1 w-fit flex-wrap">
          {!isReviewer && (
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
          )}
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
          {!isReviewer && (
            <>
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
            </>
          )}
          <button
            onClick={() => setActiveTab("clip-db")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === "clip-db"
                ? "bg-white text-purple-600 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            🎬 クリップDB
          </button>
          {!isReviewer && (
            <>
              <button
                onClick={() => setActiveTab("widget")}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                  activeTab === "widget"
                    ? "bg-white text-pink-600 shadow-sm"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                🎯 ウィジェット
              </button>
              <button
                onClick={() => setActiveTab("subtitle-dict")}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                  activeTab === "subtitle-dict"
                    ? "bg-white text-pink-600 shadow-sm"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                📖 字幕辞書
              </button>
              <button
                onClick={() => setActiveTab("reviewers")}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                  activeTab === "reviewers"
                    ? "bg-white text-pink-600 shadow-sm"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                👥 採点者
              </button>
            </>
          )}
        </div>

        {activeTab === "dashboard" && (
          <>
            {/* データ量 (AI資産量) */}
            <section className="mb-8">
              <div className="flex items-center gap-2 mb-4">
                <span className="text-lg">📊</span>
                <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_c95496', 'データ量')}</h2>
                <span className="text-xs text-gray-400 ml-1">{window.__t('adminDashboard_18646f', 'AI資産量')}</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard label={window.__t('adminDashboard_2a3a61', '総動画数')} value={data_volume.total_videos} unit={window.__t('adminDashboard_a4fb84', '本')} color="orange" />
                <StatCard label={window.__t('adminDashboard_2f8bbb', '解析済')} value={data_volume.analyzed_videos} unit={window.__t('adminDashboard_a4fb84', '本')} color="green" />
                <StatCard label={window.__t('adminDashboard_fbce43', '解析待ち')} value={data_volume.pending_videos} unit={window.__t('adminDashboard_a4fb84', '本')} color="yellow" />
                <StatCard label={window.__t('adminDashboard_6eea5d', '総動画時間')} value={data_volume.total_duration_display} color="blue" />
              </div>
            </section>

            {/* 動画タイプ (データ構造) */}
            <section className="mb-8">
              <div className="flex items-center gap-2 mb-4">
                <span className="text-lg">🎬</span>
                <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_968485', '動画タイプ')}</h2>
                <span className="text-xs text-gray-400 ml-1">{window.__t('adminDashboard_c1c727', 'データ構造')}</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <StatCard label={window.__t('adminDashboard_c5ee71', '画面収録数')} value={video_types.screen_recording_count} unit={window.__t('adminDashboard_a4fb84', '本')} color="purple" />
                <StatCard label={window.__t('adminDashboard_dfe0df', 'クリーン動画数')} value={video_types.clean_video_count} unit={window.__t('adminDashboard_a4fb84', '本')} color="indigo" />
                <StatCard label={window.__t('adminDashboard_4e2a37', '最新アップ日')} value={formatDate(video_types.latest_upload)} color="gray" small />
              </div>
            </section>

            {/* 会員規模 (母数) */}
            <section className="mb-8">
              <div className="flex items-center gap-2 mb-4">
                <span className="text-lg">👥</span>
                <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_805412', '会員規模')}</h2>
                <span className="text-xs text-gray-400 ml-1">{window.__t('adminDashboard_7c37c3', '母数')}</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <StatCard label={window.__t('adminDashboard_0acdd7', '総ユーザー')} value={user_scale.total_users} unit={window.__t('analytics_personUnit', '人')} color="orange" />
                <StatCard label={window.__t('adminDashboard_114cea', '配信者数')} value={user_scale.total_streamers} unit={window.__t('analytics_personUnit', '人')} color="red" />
                <StatCard label={window.__t('adminDashboard_ed0dea', '今月アップ人数')} value={user_scale.this_month_uploaders} unit={window.__t('analytics_personUnit', '人')} color="teal" />
              </div>
            </section>

            {/* クリップDB (売れる瞬間) */}
            <ClipDBStatsSection />
          </>
        )}

        {activeTab === "feedbacks" && (
          <FeedbackSection
            data={feedbackData}
            loading={feedbackLoading}
            error={feedbackError}
            includeUnrated={feedbackIncludeUnrated}
            setIncludeUnrated={(v) => { setFeedbackIncludeUnrated(v); setFeedbackPage(1); setFeedbackData(null); }}
            onRefresh={() => { setFeedbackData(null); setFeedbackError(null); setFeedbackRefreshKey(k => k + 1); }}
            filterRating={feedbackFilterRating}
            setFilterRating={(v) => { setFeedbackFilterRating(v); setFeedbackPage(1); setFeedbackData(null); }}
            clipFilter={feedbackClipFilter}
            setClipFilter={(v) => { setFeedbackClipFilter(v); setFeedbackPage(1); setFeedbackData(null); }}
            page={feedbackPage}
            setPage={(p) => { setFeedbackPage(p); setFeedbackData(null); }}
            reviewerInfo={reviewerInfo}
          />
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
        {activeTab === "clip-db" && (
          <AdminClipDB adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
        )}
        {activeTab === "widget" && (
          <AdminWidgetManager adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
        )}
        {activeTab === "subtitle-dict" && (
          <SubtitleDictionary adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
        )}
        {activeTab === "reviewers" && (
          <AdminReviewerManager adminKey={`${ADMIN_ID}:${ADMIN_PASS}`} />
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

  // Batch retry all stuck videos
  const [batchRetrying, setBatchRetrying] = useState(false);
  const [batchResult, setBatchResult] = useState(null);
  const [monitorHealth, setMonitorHealth] = useState(null);

  const handleBatchRetry = async () => {
    if (!window.confirm(`スタック中の全動画(${stuck_videos}${window.__t('errorLogCount', '件')})を一括リトライしますか？`)) return;
    setBatchRetrying(true);
    setBatchResult(null);
    try {
      const baseURL = import.meta.env.VITE_API_BASE_URL;
      const res = await axios.post(`${baseURL}/api/v1/admin/batch-retry-stuck`, {}, {
        headers: { "X-Admin-Key": "aither:hub" },
      });
      setBatchResult(res.data);
    } catch (e) {
      setBatchResult({ error: e.message });
    } finally {
      setBatchRetrying(false);
    }
  };

  // Fetch monitor health on mount
  useEffect(() => {
    const fetchMonitorHealth = async () => {
      try {
        const baseURL = import.meta.env.VITE_API_BASE_URL;
        const res = await axios.get(`${baseURL}/api/v1/admin/monitor-health`, {
          headers: { "X-Admin-Key": "aither:hub" },
        });
        setMonitorHealth(res.data);
      } catch (e) {
        console.warn("Monitor health fetch failed:", e);
      }
    };
    fetchMonitorHealth();
  }, []);

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
          <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_19be33', 'Upload Health 概要')}</h2>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label={window.__t('adminDashboard_5c75e6', '総アップロード')} value={overall.total_uploads} unit={window.__t('errorLogCount', '件')} color="orange" />
          <StatCard label={window.__t('common_success', '成功')} value={overall.done} unit={window.__t('errorLogCount', '件')} color="green" />
          <StatCard label={window.__t('sidebar_error', 'エラー')} value={overall.error} unit={window.__t('errorLogCount', '件')} color="red" />
          <StatCard label={window.__t('clip_processing', '処理中')} value={overall.processing} unit={window.__t('errorLogCount', '件')} color="yellow" />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mt-4">
          <StatCard label={window.__t('adminDashboard_b66435', '成功率')} value={`${overall.success_rate_pct}%`} color="green" />
          <StatCard label={window.__t('adminDashboard_148c3a', 'エラー率')} value={`${overall.error_rate_pct}%`} color="red" />
          <StatCard label={window.__t('adminDashboard_56ed9a', 'スタック')} value={stuck_videos} unit={window.__t('errorLogCount', '件')} color={stuck_videos > 0 ? "red" : "gray"} />
        </div>

        {/* Batch Retry + Monitor Health */}
        {stuck_videos > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              onClick={handleBatchRetry}
              disabled={batchRetrying}
              className="px-4 py-2 bg-orange-500 hover:bg-orange-600 text-white text-sm font-medium rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {batchRetrying ? [window.__t('adminDashboard_3653a4', 'リトライ中...')] : `全${stuck_videos}${window.__t('adminDashboard_4b1fa3', '件を一括リトライ')}`}
            </button>
            {batchResult && !batchResult.error && (
              <span className="text-sm text-green-600">
                成功: {batchResult.success}/{batchResult.total} 件 | 失敗: {batchResult.failed} | スキップ: {batchResult.skipped}
              </span>
            )}
            {batchResult?.error && (
              <span className="text-sm text-red-600">エラー: {batchResult.error}</span>
            )}
          </div>
        )}

        {/* Monitor Health */}
        {monitorHealth && (
          <div className="mt-3 p-3 rounded-lg border border-gray-200 bg-gray-50">
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-2 h-2 rounded-full ${monitorHealth.status === 'healthy' ? 'bg-green-500' : monitorHealth.status === 'warning' ? 'bg-yellow-500' : 'bg-red-500'}`}></span>
              <span className="text-xs font-medium text-gray-600">Monitor: {monitorHealth.status}</span>
              {monitorHealth.last_run_at && (
                <span className="text-xs text-gray-400">最終実行: {new Date(monitorHealth.last_run_at).toLocaleString('ja-JP')}</span>
              )}
            </div>
            {monitorHealth.last_result && (
              <div className="text-xs text-gray-500">
                検出: {monitorHealth.last_result.found || 0}件 | リキュー: {monitorHealth.last_result.requeued || 0}件 | 失敗: {monitorHealth.last_result.failed || 0}件
              </div>
            )}
          </div>
        )}
      </section>

      {/* Time-based */}
      <section className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <span className="text-lg">&#x23F0;</span>
          <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_9b146e', '期間別')}</h2>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-xl border border-blue-200 bg-blue-50 p-4">
            <p className="text-xs text-gray-500 mb-2">{window.__t('adminDashboard_95dde1', '過去24時間')}</p>
            <div className="flex gap-4">
              <div><span className="text-sm text-gray-500">UP</span> <span className="text-lg font-bold text-blue-600">{last_24h.uploads}</span></div>
              <div><span className="text-sm text-gray-500">OK</span> <span className="text-lg font-bold text-green-600">{last_24h.done}</span></div>
              <div><span className="text-sm text-gray-500">NG</span> <span className="text-lg font-bold text-red-600">{last_24h.error}</span></div>
            </div>
          </div>
          <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-4">
            <p className="text-xs text-gray-500 mb-2">{window.__t('adminDashboard_2ebda4', '過去7日間')}</p>
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
          <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_e05b15', 'ステータス分布')}</h2>
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
          <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_f3eea3', '最近のアップロード')}</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_620172', 'ファイル名')}</th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('live_status', 'ステータス')}</th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_888167', 'タイプ')}</th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_597622', 'ユーザー')}</th>
                <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_46db7b', '日時')}</th>
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
            <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_f0415a', 'Enqueue 統計')}</h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="Enqueue OK" value={enqueue_stats.total_ok} unit={window.__t('errorLogCount', '件')} color="green" />
            <StatCard label="Enqueue FAILED" value={enqueue_stats.total_failed} unit={window.__t('errorLogCount', '件')} color="red" />
            <StatCard label="OK (24h)" value={enqueue_stats.ok_last_24h} unit={window.__t('errorLogCount', '件')} color="green" />
            <StatCard label="FAILED (24h)" value={enqueue_stats.failed_last_24h} unit={window.__t('errorLogCount', '件')} color="red" />
          </div>
          {enqueue_stats.enqueue_success_rate_pct != null && (
            <div className="mt-3">
              <StatCard label={window.__t('adminDashboard_309a5a', 'Enqueue 成功率')} value={`${enqueue_stats.enqueue_success_rate_pct}%`} color={enqueue_stats.enqueue_success_rate_pct >= 95 ? "green" : "red"} />
            </div>
          )}
        </section>
      )}

      {/* Pipeline Stages */}
      {pipeline_stages && (
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">&#x2699;&#xFE0F;</span>
            <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_05a00d', 'パイプラインステージ')}</h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <StatCard label={window.__t('statusNew', 'アップロード待ち')} value={pipeline_stages.uploaded_waiting} unit={window.__t('errorLogCount', '件')} color="blue" />
            <StatCard label={window.__t('clip_processing', '処理中')} value={pipeline_stages.processing} unit={window.__t('errorLogCount', '件')} color="yellow" />
            <StatCard label={window.__t('clip_completed', '完了')} value={pipeline_stages.done} unit={window.__t('errorLogCount', '件')} color="green" />
            <StatCard label={window.__t('sidebar_error', 'エラー')} value={pipeline_stages.error} unit={window.__t('errorLogCount', '件')} color="red" />
            <StatCard label={window.__t('adminDashboard_3f6b4e', 'Enqueue失敗')} value={pipeline_stages.enqueue_failed} unit={window.__t('errorLogCount', '件')} color="red" />
            <StatCard label={window.__t('adminDashboard_3b3c0c', 'スタック(>2h)')} value={pipeline_stages.stuck_gt_2h} unit={window.__t('errorLogCount', '件')} color={pipeline_stages.stuck_gt_2h > 0 ? "red" : "gray"} />
          </div>
        </section>
      )}

      {/* Retry Candidates */}
      {retry_candidates && retry_candidates.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">&#x1F504;</span>
            <h2 className="text-lg font-semibold text-orange-600">{window.__t('adminDashboard_bf9f4e', 'リトライ候補 (Enqueue失敗)')}</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_620172', 'ファイル名')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('live_status', 'ステータス')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_a576c3', 'エラー内容')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_597622', 'ユーザー')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_46db7b', '日時')}</th>
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
            <h2 className="text-lg font-semibold text-red-600">{window.__t('adminDashboard_d6ac36', 'パイプラインステージエラー')}</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_620172', 'ファイル名')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_a74623', 'エラーステージ')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_585f2c', '最終ステージ')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_a576c3', 'エラー内容')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_597622', 'ユーザー')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_46db7b', '日時')}</th>
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
            <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_716ea8', '最近のステージエラーログ')}</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_86a507', 'ステージ')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_6fc9eb', 'エラータイプ')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_a576c3', 'エラー内容')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_4f21fa', '所要時間')}</th>
                  <th className="text-left py-2 px-3 text-gray-500 font-medium">{window.__t('adminDashboard_46db7b', '日時')}</th>
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
            <h2 className="text-lg font-semibold text-red-600">{window.__t('adminDashboard_14cd2b', '最近のエラー (7日間)')}</h2>
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
function FeedbackSection({ data, loading, error, includeUnrated, setIncludeUnrated, onRefresh, filterRating, setFilterRating, clipFilter, setClipFilter, page, setPage, reviewerInfo }) {
  const { i18n } = useTranslation();
  const [expandedIdx, setExpandedIdx] = useState(-1);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-orange-500"></div>
        <span className="ml-3 text-sm text-gray-500">読み込み中...</span>
      </div>
    );
  }

  if (!data || error) {
    return (
      <div className="text-center py-16">
        <p className="text-gray-400 mb-4">
          {error ? `フィードバックデータの取得に失敗しました (${error})` : 'フィードバックデータの取得に失敗しました'}
        </p>
        <button
          onClick={onRefresh}
          className="px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 transition-colors"
        >
          再読み込み
        </button>
      </div>
    );
  }

  const { summary, feedbacks, pagination } = data;
  const unratedCount = summary.total_unrated || 0;
  const ratedCount = summary.total_feedbacks || 0;
  const totalCount = summary.total_phases || 0;
  const totalFiltered = pagination?.total_filtered || feedbacks.length;
  const totalPages = pagination?.total_pages || 1;
  const currentPage = pagination?.page || page;

  return (
    <div>
      {/* Summary Cards */}
      <section className="mb-8">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span className="text-lg">⭐</span>
            <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_446612', 'フィードバック概要')}</h2>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5">
              {[{ code: 'ja', label: '🇯🇵 日本語' }, { code: 'zh-TW', label: '🇹🇼 中文' }, { code: 'en', label: '🇺🇸 EN' }].map(lang => (
                <button
                  key={lang.code}
                  onClick={() => changeLanguage(lang.code)}
                  className={`px-2 py-1 text-[10px] font-medium rounded-md transition-all ${
                    i18n.language === lang.code
                      ? 'bg-white text-orange-600 shadow-sm'
                      : 'text-gray-500 hover:text-gray-700'
                  }`}
                >
                  {lang.label}
                </button>
              ))}
            </div>
            <button
              onClick={onRefresh}
              className="px-3 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-lg transition-all"
            >
              🔄 更新
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-7 gap-4">
          <StatCard label="総フェーズ数" value={totalCount.toLocaleString()} unit={window.__t('errorLogCount', '件')} color="gray" />
          <StatCard label={window.__t('adminDashboard_18ecb6', '総採点数')} value={ratedCount.toLocaleString()} unit={window.__t('errorLogCount', '件')} color="orange" />
          <StatCard label="未採点" value={unratedCount.toLocaleString()} unit={window.__t('errorLogCount', '件')} color="red" />
          <StatCard label={window.__t('adminDashboard_15e370', '平均スコア')} value={summary.average_rating} unit="/ 5" color="blue" />
          <StatCard label="クリップあり" value={(summary.with_clip_count || 0).toLocaleString()} unit={window.__t('errorLogCount', '件')} color="teal" />
          <StatCard label={window.__t('adminDashboard_424176', 'コメント付き')} value={summary.with_comments} unit={window.__t('errorLogCount', '件')} color="green" />
          <div className="rounded-xl border p-4 border-purple-300 bg-purple-50 transition-all duration-200 hover:shadow-md">
            <p className="text-xs text-gray-500 mb-2">{window.__t('adminDashboard_804dd5', 'スコア分布')}</p>
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
                      title={`${star}${window.__t('pointSuffix', '点')}: ${count}${window.__t('errorLogCount', '件')}`}
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
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <span className="text-sm text-gray-500">{window.__t('adminDashboard_12bfab', 'フィルタ:')}</span>
        <button
          onClick={() => setFilterRating(0)}
          className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
            filterRating === 0
              ? "bg-orange-500 text-white"
              : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          すべて ({totalCount.toLocaleString()})
        </button>
        <button
          onClick={() => setFilterRating(-1)}
          className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
            filterRating === -1
              ? "bg-red-500 text-white"
              : "bg-red-50 text-red-600 hover:bg-red-100 border border-red-200"
          }`}
        >
          未採点 ({unratedCount.toLocaleString()})
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

        {/* Clip Filter separator */}
        <span className="text-gray-300 mx-1">|</span>
        <button
          onClick={() => setClipFilter(null)}
          className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
            clipFilter === null
              ? "bg-teal-500 text-white"
              : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          全クリップ
        </button>
        <button
          onClick={() => setClipFilter('yes')}
          className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
            clipFilter === 'yes'
              ? "bg-teal-500 text-white"
              : "bg-teal-50 text-teal-600 hover:bg-teal-100 border border-teal-200"
          }`}
        >
          クリップあり ({(summary.with_clip_count || 0).toLocaleString()})
        </button>
        <button
          onClick={() => setClipFilter('no')}
          className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
            clipFilter === 'no'
              ? "bg-teal-500 text-white"
              : "bg-gray-50 text-gray-600 hover:bg-gray-100 border border-gray-200"
          }`}
        >
          クリップなし ({(summary.without_clip_count || 0).toLocaleString()})
        </button>
      </div>

      {/* Pagination Info */}
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs text-gray-400">
          {totalFiltered.toLocaleString()} 件中 {((currentPage - 1) * 50 + 1).toLocaleString()} - {Math.min(currentPage * 50, totalFiltered).toLocaleString()} 件を表示
        </p>
        {totalPages > 1 && (
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(1)}
              disabled={currentPage <= 1}
              className="px-2 py-1 text-xs rounded bg-gray-100 hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              «
            </button>
            <button
              onClick={() => setPage(currentPage - 1)}
              disabled={currentPage <= 1}
              className="px-2 py-1 text-xs rounded bg-gray-100 hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              ‹ 前へ
            </button>
            <span className="px-3 py-1 text-xs font-medium text-gray-700">
              {currentPage} / {totalPages}
            </span>
            <button
              onClick={() => setPage(currentPage + 1)}
              disabled={currentPage >= totalPages}
              className="px-2 py-1 text-xs rounded bg-gray-100 hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              次へ ›
            </button>
            <button
              onClick={() => setPage(totalPages)}
              disabled={currentPage >= totalPages}
              className="px-2 py-1 text-xs rounded bg-gray-100 hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              »
            </button>
          </div>
        )}
      </div>

      {/* Feedback List */}
      <div className="space-y-3">
        {feedbacks.length === 0 ? (
          <div className="text-center py-12 text-gray-400">
            該当するフィードバックはありません
          </div>
        ) : (
          feedbacks.map((fb, idx) => (
            <FeedbackCard
              key={`${fb.video_id}-${fb.phase_index}-${idx}`}
              fb={fb}
              onRated={onRefresh}
              feedbacks={feedbacks}
              currentIdx={idx}
              reviewerInfo={reviewerInfo}
              expanded={expandedIdx === idx}
              onToggle={() => setExpandedIdx(expandedIdx === idx ? -1 : idx)}
              onNext={() => {
                if (idx < feedbacks.length - 1) {
                  setExpandedIdx(idx + 1);
                  // Scroll to next card after state update
                  setTimeout(() => {
                    const cards = document.querySelectorAll('[data-feedback-card]');
                    if (cards[idx + 1]) cards[idx + 1].scrollIntoView({ behavior: 'smooth', block: 'center' });
                  }, 100);
                }
              }}
              onPrev={() => {
                if (idx > 0) {
                  setExpandedIdx(idx - 1);
                  setTimeout(() => {
                    const cards = document.querySelectorAll('[data-feedback-card]');
                    if (cards[idx - 1]) cards[idx - 1].scrollIntoView({ behavior: 'smooth', block: 'center' });
                  }, 100);
                }
              }}
            />
          ))
        )}
      </div>

      {/* Bottom Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-1 mt-6 mb-4">
          <button
            onClick={() => { setPage(currentPage - 1); window.scrollTo(0, 0); }}
            disabled={currentPage <= 1}
            className="px-3 py-1.5 text-xs rounded-lg bg-gray-100 hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            ‹ 前のページ
          </button>
          {/* Page number buttons */}
          {(() => {
            const pages = [];
            const start = Math.max(1, currentPage - 3);
            const end = Math.min(totalPages, currentPage + 3);
            if (start > 1) pages.push(<span key="s" className="px-1 text-xs text-gray-400">...</span>);
            for (let i = start; i <= end; i++) {
              pages.push(
                <button
                  key={i}
                  onClick={() => { setPage(i); window.scrollTo(0, 0); }}
                  className={`px-2.5 py-1.5 text-xs rounded-lg transition-all ${
                    i === currentPage
                      ? "bg-orange-500 text-white font-bold"
                      : "bg-gray-100 hover:bg-gray-200 text-gray-600"
                  }`}
                >
                  {i}
                </button>
              );
            }
            if (end < totalPages) pages.push(<span key="e" className="px-1 text-xs text-gray-400">...</span>);
            return pages;
          })()}
          <button
            onClick={() => { setPage(currentPage + 1); window.scrollTo(0, 0); }}
            disabled={currentPage >= totalPages}
            className="px-3 py-1.5 text-xs rounded-lg bg-gray-100 hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            次のページ ›
          </button>
        </div>
      )}
    </div>
  );
}

// ── Feedback Card (Expandable with Video Preview + Full Rating Panel) ──
function FeedbackCard({ fb, onRated, feedbacks, currentIdx, expanded, onToggle, onNext, onPrev, reviewerInfo }) {
  const isUnrated = fb.user_rating == null;
  const [hoverStar, setHoverStar] = useState(0);
  const [saving, setSaving] = useState(false);
  const [localRating, setLocalRating] = useState(fb.user_rating);
  const [nextBlockedMsg, setNextBlockedMsg] = useState(null);
  const videoRef = useRef(null);
  const isSourceVideo = !fb.clip_url && !!fb.source_url;
  const [videoTime, setVideoTime] = useState({ current: 0, total: 0 });
  const [playbackSpeed, setPlaybackSpeed] = useState(2);
  const speedOptions = [1, 1.5, 2, 3];
  const [guideOpen, setGuideOpen] = useState(false);

  // Scoring guideline: "売上貢献度" axis
  const STAR_GUIDELINES = {
    1: { short: '買いたくならない', detail: '商品と無関係 / 雑談 / 技術トラブル / 無言' },
    2: { short: 'ほぼ買いたくならない', detail: '商品名は出るが具体的な説明なし' },
    3: { short: '少し興味は持つかも', detail: '商品の説明あり、ただしデモなし' },
    4: { short: '買いたくなる', detail: '商品デモあり / 使用感共有 / ビフォーアフター / 視聴者との対話' },
    5: { short: '今すぐ買いたい', detail: '★4の要素 + 限定感・緊急性 / 実際に注文が入っている瞬間' },
  };

  // Build optimized video URL: clip_url directly, source_url with Media Fragment #t=start,end
  const videoSrc = useMemo(() => {
    if (fb.clip_url) return fb.clip_url;
    if (fb.source_url && fb.time_start != null && fb.time_end != null) {
      return `${fb.source_url}#t=${fb.time_start},${fb.time_end}`;
    }
    return fb.source_url || null;
  }, [fb.clip_url, fb.source_url, fb.time_start, fb.time_end]);

  const displayRating = localRating || 0;
  const ratingColor = isUnrated && localRating == null
    ? "text-gray-300"
    : displayRating >= 4
    ? "text-green-600"
    : displayRating >= 3
    ? "text-yellow-600"
    : "text-red-500";

  const timeRange = formatSeconds(fb.time_start) + " – " + formatSeconds(fb.time_end);

  const handleClick = () => {
    const params = new URLSearchParams({
      phase: fb.phase_index,
      t_start: fb.time_start,
      t_end: fb.time_end,
      open_editor: '1',
    });
    window.open(`/video/${fb.video_id}?${params.toString()}`, '_blank');
  };

  const handleRate = async (e, star) => {
    e.stopPropagation();
    if (saving) return;
    setSaving(true);
    try {
      const baseURL = import.meta.env.VITE_API_BASE_URL;
      await axios.put(
        `${baseURL}/api/v1/admin/feedbacks/${fb.video_id}/phases/${fb.phase_index}/rating`,
        { rating: star, ...(reviewerInfo?.id ? { reviewer_id: reviewerInfo.id } : {}) },
        { headers: { "X-Admin-Key": "aither:hub" }, timeout: 10000 }
      );
      setLocalRating(star);
      // Do NOT call onRated() here — it triggers a full list refresh
      // which removes the rated card from the "unrated" filter, making it look
      // like the page auto-advanced. Refresh happens on page change or manual refresh.
    } catch (err) {
      console.error("Failed to rate:", err);
      alert("採点に失敗しました: " + (err.response?.data?.detail || err.message));
    } finally {
      setSaving(false);
    }
  };

  const handleToggleExpand = (e) => {
    // Don't expand if clicking on star buttons or links
    if (e.target.closest('button') && !e.target.closest('[data-expand-toggle]')) return;
    if (e.target.closest('a')) return;
    if (onToggle) onToggle();
  };

  return (
    <div
      data-feedback-card
      className={`rounded-xl border transition-all ${
        expanded
          ? "bg-white border-orange-400 shadow-lg p-0"
          : isUnrated && localRating == null
          ? "bg-yellow-50 border-yellow-200 hover:border-yellow-400 hover:shadow-md p-4 cursor-pointer"
          : "bg-white border-gray-200 hover:border-orange-300 hover:shadow-md p-4 cursor-pointer"
      }`}
    >
      {/* Collapsed header (always visible) */}
      <div
        className={`flex items-start justify-between gap-4 ${expanded ? 'p-4 pb-2' : ''}`}
        onClick={handleToggleExpand}
        data-expand-toggle
      >
        {/* Left: Rating + Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-2">
            {/* Interactive star rating */}
            <div className="flex items-center gap-0.5" onMouseLeave={() => setHoverStar(0)}>
              {[1, 2, 3, 4, 5].map((star) => {
                const filled = hoverStar > 0 ? star <= hoverStar : star <= displayRating;
                return (
                  <button
                    key={star}
                    type="button"
                    disabled={saving}
                    className={`text-lg font-bold transition-all cursor-pointer hover:scale-125 ${
                      filled
                        ? (hoverStar > 0 ? "text-orange-400" : ratingColor)
                        : "text-gray-300"
                    } ${saving ? "opacity-50" : ""}`}
                    onMouseEnter={() => setHoverStar(star)}
                    onClick={(e) => handleRate(e, star)}
                    title={`${star}点をつける`}
                  >
                    ★
                  </button>
                );
              })}
            </div>
            {isUnrated && localRating == null && (
              <span className="text-[10px] font-medium text-yellow-700 bg-yellow-200 px-2 py-0.5 rounded-full">
                未採点
              </span>
            )}
            {localRating != null && localRating !== fb.user_rating && (
              <span className="text-[10px] font-medium text-green-700 bg-green-100 px-2 py-0.5 rounded-full">
                保存済
              </span>
            )}
            <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded">
              {timeRange}
            </span>
            {(fb.clip_url || fb.source_url) && (
              <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${fb.clip_url ? 'text-blue-600 bg-blue-50' : 'text-purple-600 bg-purple-50'}`}>
                {fb.clip_url ? '▶ クリップあり' : '▶ 元動画'}
              </span>
            )}
            {fb.generation_source && fb.generation_source !== 'pipeline' && (
              <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${
                fb.generation_source === 'batch_raw_v2' ? 'text-green-700 bg-green-50 border-green-300' :
                fb.generation_source === 'batch_raw' ? 'text-amber-700 bg-amber-50 border-amber-300' :
                fb.generation_source === 'batch_no_subtitle' ? 'text-purple-700 bg-purple-50 border-purple-300' :
                'text-orange-600 bg-orange-50 border-orange-200'
              }`}>
                {fb.generation_source === 'batch_raw_v2' ? '✅ batch v2' :
                 fb.generation_source === 'batch_raw' ? '⚠️ batch v1' :
                 fb.generation_source === 'batch_no_subtitle' ? '🔇 no-sub' :
                 `🔄 ${fb.generation_source}`}
              </span>
            )}
            {fb.clip_duration_sec != null && fb.clip_duration_sec > 0 && (
              <span className="text-[10px] font-medium px-2 py-0.5 rounded-full text-teal-600 bg-teal-50">
                ✂️ {Math.floor(fb.clip_duration_sec / 60)}:{String(Math.floor(fb.clip_duration_sec % 60)).padStart(2, '0')}
              </span>
            )}
            {fb.download_count > 0 && (
              <span className="text-xs font-semibold text-blue-600 bg-blue-50 border border-blue-200 px-2 py-0.5 rounded-full">
                ⬇️ {fb.download_count}回
              </span>
            )}
          </div>

          {!expanded && fb.user_comment && (
            <div className="bg-orange-50 border-l-3 border-orange-400 pl-3 py-2 mb-2 rounded-r">
              <p className="text-sm text-gray-700">{fb.user_comment}</p>
            </div>
          )}

          {!expanded && fb.summary && (
            <p className="text-xs text-gray-500 line-clamp-2">{fb.summary}</p>
          )}
        </div>

        {/* Right: Meta + Open link */}
        <div className="text-right shrink-0">
          <p className="text-xs text-gray-500 font-medium truncate max-w-[180px]" title={fb.video_name}>
            {fb.video_name}
          </p>
          <p className="text-[10px] text-gray-400 mt-1">
            {fb.user_email || fb.user_id}
          </p>
          {fb.rated_at && localRating === fb.user_rating && (
            <p className="text-[10px] text-gray-400 mt-0.5">
              {formatDate(fb.rated_at)}
            </p>
          )}
          {fb.reviewer_name && (
            <p className="text-[10px] text-blue-500 mt-0.5 font-medium">
              👤 {fb.reviewer_name}
            </p>
          )}
          <div className="flex items-center gap-2 mt-1 justify-end">
            <button
              onClick={handleClick}
              className="text-[10px] text-orange-500 hover:text-orange-700 underline"
            >
              → エディタを開く
            </button>
            <span
              data-expand-toggle
              className="text-[10px] text-gray-400 cursor-pointer hover:text-gray-600"
            >
              {expanded ? '▲ 閉じる' : '▼ 詳細'}
            </span>
          </div>
        </div>
      </div>

      {/* Expanded: Video Preview + Full Rating Panel */}
      {expanded && (
        <div className="border-t border-gray-200 p-4 pt-3">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Left: Video Preview */}
            <div>
              {videoSrc ? (
                <div className="rounded-lg overflow-hidden bg-black aspect-[9/16] max-h-[400px] mx-auto relative" style={{ maxWidth: '225px' }}>
                  <video
                    ref={videoRef}
                    src={videoSrc}
                    controls
                    loop
                    playsInline
                    autoPlay
                    muted
                    className="w-full h-full object-contain"
                    preload={isSourceVideo ? 'none' : 'auto'}
                    onLoadedMetadata={(e) => {
                      const vid = e.target;
                      vid.playbackRate = playbackSpeed;
                    }}
                    onTimeUpdate={(e) => {
                      const vid = e.target;
                      setVideoTime({ current: vid.currentTime, total: vid.duration || 0 });
                      // For source video with Media Fragment, enforce time bounds
                      if (isSourceVideo && fb.time_end != null) {
                        if (vid.currentTime >= fb.time_end + 0.5) {
                          vid.currentTime = fb.time_start || 0;
                        }
                      }
                    }}
                  />
                  <div className="absolute bottom-1 left-1 right-1 flex items-center justify-between">
                    <div className="bg-black/60 text-white text-[10px] px-1.5 py-0.5 rounded flex gap-2">
                      {isSourceVideo ? (
                        <span>元動画 ({formatSeconds(fb.time_start)}〜{formatSeconds(fb.time_end)})</span>
                      ) : videoTime.total > 0 ? (
                        <span>{formatSeconds(videoTime.current)} / {formatSeconds(videoTime.total)}</span>
                      ) : null}
                    </div>
                    <div className="flex gap-0.5">
                      {speedOptions.map(s => (
                        <button
                          key={s}
                          onClick={(e) => {
                            e.stopPropagation();
                            setPlaybackSpeed(s);
                            if (videoRef.current) videoRef.current.playbackRate = s;
                          }}
                          className={`px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors ${
                            playbackSpeed === s
                              ? 'bg-orange-500 text-white'
                              : 'bg-black/60 text-white/80 hover:bg-black/80'
                          }`}
                        >
                          {s}x
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="rounded-lg bg-gray-100 aspect-[9/16] max-h-[400px] mx-auto flex items-center justify-center" style={{ maxWidth: '225px' }}>
                  <div className="text-center text-gray-400">
                    <p className="text-2xl mb-2">🎬</p>
                    <p className="text-xs">動画なし</p>
                    <button
                      onClick={handleClick}
                      className="mt-2 text-xs text-orange-500 hover:text-orange-700 underline"
                    >
                      エディタで確認
                    </button>
                  </div>
                </div>
              )}
              {/* AI Summary */}
              {fb.summary && (
                <div className="mt-3 p-3 bg-gray-50 rounded-lg">
                  <p className="text-[10px] font-semibold text-gray-500 mb-1">AI分析</p>
                  <p className="text-xs text-gray-600 leading-relaxed">{fb.summary}</p>
                </div>
              )}
            </div>

            {/* Right: Full Rating Panel */}
            <div>
              {/* Star Rating (Admin Score) */}
              <div className="mb-4 p-3 bg-orange-50 rounded-lg border border-orange-200">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-bold text-gray-700">⭐ 管理者採点（1〜5）— 売上貢献度</p>
                  <button
                    type="button"
                    onClick={() => setGuideOpen(!guideOpen)}
                    className="text-[10px] font-semibold text-orange-600 hover:text-orange-800 bg-orange-100 hover:bg-orange-200 px-2 py-0.5 rounded-full transition-all"
                  >
                    {guideOpen ? '採点ガイド ▲' : '採点ガイド ▼'}
                  </button>
                </div>

                {/* Collapsible Scoring Guideline Panel */}
                {guideOpen && (
                  <div className="mb-3 p-2 bg-white rounded-md border border-orange-100 text-[10px] leading-relaxed">
                    <table className="w-full">
                      <tbody>
                        {[1, 2, 3, 4, 5].map((s) => (
                          <tr key={s} className={s < 5 ? 'border-b border-orange-50' : ''}>
                            <td className="py-1 pr-2 font-bold text-orange-600 whitespace-nowrap align-top">★{s}</td>
                            <td className="py-1 pr-1 font-semibold text-gray-700 whitespace-nowrap align-top">{STAR_GUIDELINES[s].short}</td>
                            <td className="py-1 text-gray-500">{STAR_GUIDELINES[s].detail}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <div className="flex items-center gap-1" onMouseLeave={() => setHoverStar(0)}>
                  {[1, 2, 3, 4, 5].map((star) => {
                    const filled = hoverStar > 0 ? star <= hoverStar : star <= displayRating;
                    return (
                      <button
                        key={star}
                        type="button"
                        disabled={saving}
                        className={`text-2xl font-bold transition-all cursor-pointer hover:scale-110 ${
                          filled
                            ? (hoverStar > 0 ? "text-orange-400" : ratingColor)
                            : "text-gray-300"
                        } ${saving ? "opacity-50" : ""}`}
                        onMouseEnter={() => setHoverStar(star)}
                        onClick={(e) => handleRate(e, star)}
                        title={`${star}点 — ${STAR_GUIDELINES[star].short}（${STAR_GUIDELINES[star].detail}）`}
                      >
                        ★
                      </button>
                    );
                  })}
                  {localRating != null && (
                    <span className="ml-2 text-sm font-bold text-orange-600">{localRating}点</span>
                  )}
                </div>

                {/* Hover label: show guideline text when hovering a star */}
                {hoverStar > 0 && (
                  <div className="mt-1.5 text-[11px] font-semibold text-orange-700 bg-orange-100 rounded px-2 py-1 transition-all">
                    ★{hoverStar} {STAR_GUIDELINES[hoverStar].short} — <span className="font-normal text-gray-600">{STAR_GUIDELINES[hoverStar].detail}</span>
                  </div>
                )}
              </div>

              {/* ClipFeedbackPanel (Good/Bad, Reason Tags, Sales DNA, Comment) */}
              <ClipFeedbackPanel
                videoId={fb.video_id}
                phaseIndex={fb.phase_index}
                timeStart={fb.time_start}
                timeEnd={fb.time_end}
                clipId={fb.clip_id}
                adminMode={true}
                onFeedbackSubmitted={() => {
                  // Do NOT call onRated() here — it triggers a full list refresh
                  // which resets expanded state. Just auto-advance to next card.
                  if (onNext) setTimeout(() => onNext(), 500);
                }}
              />

              {/* Navigation: Prev / Next */}
              <div className="flex items-center justify-between mt-4 pt-3 border-t border-gray-200">
                <button
                  onClick={onPrev}
                  disabled={!feedbacks || currentIdx <= 0}
                  className="px-4 py-2 text-sm font-semibold rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-600 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                >
                  ‹ 前へ
                </button>
                <span className="text-xs text-gray-400">
                  {currentIdx + 1} / {feedbacks?.length || 0}
                </span>
                <button
                  onClick={() => {
                    if (localRating == null) {
                      setNextBlockedMsg('⚠️ 星の採点をしてから次へ進んでください');
                      setTimeout(() => setNextBlockedMsg(null), 3000);
                      return;
                    }
                    setNextBlockedMsg(null);
                    onNext();
                  }}
                  disabled={!feedbacks || currentIdx >= feedbacks.length - 1}
                  className={`px-4 py-2 text-sm font-semibold rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed ${
                    localRating == null
                      ? 'bg-gray-300 hover:bg-gray-400 text-gray-600'
                      : 'bg-orange-500 hover:bg-orange-600 text-white'
                  }`}
                >
                  次へ ›
                </button>
              </div>
              {nextBlockedMsg && (
                <div className="mt-2 text-center text-sm font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded-lg py-2 px-3 animate-pulse">
                  {nextBlockedMsg}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
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
          <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_f4f096', 'クリップDB')}</h2>
          <span className="text-xs text-gray-400 ml-1">{window.__t('adminDashboard_d744f0', '売れる瞬間')}</span>
        </div>
        <div className="text-sm text-gray-400">{window.__t('common_loading', '読み込み中...')}</div>
      </section>
    );
  }

  if (!clipStats) return null;

  const TAG_LABEL_MAP = {
    HOOK: window.__t('adminDashboard_022430', 'フック'), EMPATHY: window.__t('empathy', '共感'), PROBLEM: window.__t('adminDashboard_3b0c9b', '問題提起'),
    EDUCATION: window.__t('education', '教育'), SOLUTION: window.__t('adminDashboard_e88d65', '解決策'), DEMONSTRATION: window.__t('adminDashboard_f5b486', '実演'),
    COMPARISON: window.__t('comparison', '比較'), PROOF: window.__t('proof', '証拠'), TRUST: window.__t('trust', '信頼'),
    SOCIAL_PROOF: window.__t('adminDashboard_fe9111', '社会的証明'), OBJECTION_HANDLING: window.__t('objectionHandling', '反論処理'),
    URGENCY: window.__t('adminDashboard_2ae709', '緊急性'), LIMITED_OFFER: window.__t('adminDashboard_6b429c', '限定オファー'), BONUS: window.__t('bonus', '特典'),
    CTA: window.__t('adminDashboard_5670e9', '行動喚起'), PRICE: window.__t('adminDashboard_6049bc', '価格訴求'), STORY: window.__t('adminDashboard_cffaf2', 'ストーリー'),
  };
  const TAG_COLORS = {
    [window.__t('empathy', '共感')]: '#92400E', [window.__t('adminDashboard_f983c2', '権威')]: '#1E40AF', [window.__t('adminDashboard_f8cd5e', '限定性')]: '#9D174D',
    [window.__t('adminDashboard_f5b486', '実演')]: '#065F46', [window.__t('comparison', '比較')]: '#3730A3', [window.__t('adminDashboard_cffaf2', 'ストーリー')]: '#991B1B',
    [window.__t('adminDashboard_1d9246', 'テンション')]: '#9A3412', [window.__t('adminDashboard_2ae709', '緊急性')]: '#854D0E', [window.__t('adminDashboard_fe9111', '社会的証明')]: '#166534',
    [window.__t('adminDashboard_6049bc', '価格訴求')]: '#047857', [window.__t('adminDashboard_3b0c9b', '問題提起')]: '#9F1239', [window.__t('adminDashboard_7c11e2', '解決提示')]: '#0C4A6E',
    // English key aliases
    HOOK: '#6D28D9', EMPATHY: '#92400E', PROBLEM: '#9F1239',
    EDUCATION: '#1E40AF', SOLUTION: '#065F46', DEMONSTRATION: '#0F766E',
    COMPARISON: '#3730A3', PROOF: '#155E75', TRUST: '#065F46',
    SOCIAL_PROOF: '#166534', OBJECTION_HANDLING: '#92400E',
    URGENCY: '#9A3412', LIMITED_OFFER: '#9D174D', BONUS: '#3F6212',
    CTA: '#991B1B', PRICE: '#047857', STORY: '#991B1B',
  };

  return (
    <section className="mb-8">
      <div className="flex items-center gap-2 mb-4">
        <span className="text-lg">🎬</span>
        <h2 className="text-lg font-semibold text-gray-700">{window.__t('adminDashboard_f4f096', 'クリップDB')}</h2>
        <span className="text-xs text-gray-400 ml-1">{window.__t('adminDashboard_d744f0', '売れる瞬間')}</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        <StatCard label={window.__t('adminDashboard_03071d', '総クリップ')} value={clipStats.total_clips} unit={window.__t('errorLogCount', '件')} color="purple" />
        <StatCard label={window.__t('adminDashboard_e6443c', '売れた')} value={clipStats.sold_clips} unit={window.__t('errorLogCount', '件')} color="green" />
        <StatCard label={window.__t('adminDashboard_2989b4', '未売')} value={clipStats.unsold_clips} unit={window.__t('errorLogCount', '件')} color="gray" />
        <StatCard label={window.__t('adminDashboard_076fe3', '総GMV')} value={clipStats.total_gmv >= 10000 ? `¥${(clipStats.total_gmv / 10000).toFixed(1)}${window.__t('tenThousand', '万')}` : `¥${Math.round(clipStats.total_gmv || 0).toLocaleString()}`} color="blue" />
      </div>

      {/* Top tags */}
      {clipStats.top_tags && clipStats.top_tags.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4 mb-4">
          <h3 className="text-sm font-semibold text-gray-600 mb-3">{window.__t('adminDashboard_7a08d5', 'トップタグ（売れた理由）')}</h3>
          <div className="flex flex-wrap gap-2">
            {clipStats.top_tags.slice(0, 12).map((t, i) => {
              const color = TAG_COLORS[t.tag] || '#374151';
              return (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border"
                  style={{ color, backgroundColor: color + '12', borderColor: color + '30' }}
                >
                  {TAG_LABEL_MAP[t.tag] || t.tag}
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
          <h3 className="text-sm font-semibold text-gray-600 mb-3">{window.__t('adminDashboard_f9cbbc', 'トップ商品')}</h3>
          <div className="space-y-1.5">
            {clipStats.top_products.slice(0, 6).map((p, i) => (
              <div key={i} className="flex items-center justify-between text-xs py-1 border-b border-gray-50 last:border-0">
                <span className="text-gray-700 truncate flex-1">{p.product}</span>
                <span className="text-gray-400 mx-2">{p.count}件</span>
                <span className="font-semibold text-green-600">
                  {p.gmv >= 10000 ? `¥${(p.gmv / 10000).toFixed(1)}${window.__t('tenThousand', '万')}` : `¥${Math.round(p.gmv || 0).toLocaleString()}`}
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
