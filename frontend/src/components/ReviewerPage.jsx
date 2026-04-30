import React, { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

/**
 * ReviewerPage — 採点者専用ページ
 * /reviewer でアクセス
 * 
 * 機能:
 * 1. ログイン画面（メール+パスワード）
 * 2. 採点UI（クリップ再生 + 星評価 + コメント）
 * 3. 自動セッション管理（ログイン時間、採点数の自動トラッキング）
 * 4. 採点者ダッシュボード（今日の統計、累計統計）
 */

// ── API Helper ──
const reviewerApi = {
  async login(email, password) {
    const res = await axios.post(`${API_BASE}/api/v1/reviewer/login`, { email, password }, { timeout: 15000 });
    return res.data;
  },
  async logout(token) {
    const res = await axios.post(`${API_BASE}/api/v1/reviewer/logout`, {}, {
      headers: { Authorization: `Bearer ${token}` }, timeout: 10000,
    });
    return res.data;
  },
  async getMe(token) {
    const res = await axios.get(`${API_BASE}/api/v1/reviewer/me`, {
      headers: { Authorization: `Bearer ${token}` }, timeout: 10000,
    });
    return res.data;
  },
  async heartbeat(token) {
    const res = await axios.post(`${API_BASE}/api/v1/reviewer/heartbeat`, {}, {
      headers: { Authorization: `Bearer ${token}` }, timeout: 5000,
    });
    return res.data;
  },
  async rate(token, videoId, phaseIndex, rating, comment) {
    const res = await axios.put(
      `${API_BASE}/api/v1/reviewer/rate/${videoId}/${phaseIndex}`,
      { rating, comment },
      { headers: { Authorization: `Bearer ${token}` }, timeout: 10000 },
    );
    return res.data;
  },
  async getFeedbacks(token, page, perPage, filterRating, clipFilter) {
    const params = new URLSearchParams({ page, per_page: perPage });
    if (filterRating) params.set('filter_rating', filterRating);
    if (clipFilter) params.set('clip_filter', clipFilter);
    const res = await axios.get(`${API_BASE}/api/v1/reviewer/feedbacks?${params}`, {
      headers: { Authorization: `Bearer ${token}` }, timeout: 30000,
    });
    return res.data;
  },
};

// ── Login Screen ──
function LoginScreen({ onLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const data = await reviewerApi.login(email, password);
      onLogin(data);
    } catch (err) {
      setError(err.response?.data?.detail || 'ログインに失敗しました');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-orange-500 to-amber-500 mb-4 shadow-lg shadow-orange-500/20">
            <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-white">AitherHub 採点</h1>
          <p className="text-slate-400 mt-2">採点者アカウントでログインしてください</p>
        </div>

        <form onSubmit={handleSubmit} className="bg-slate-800/50 backdrop-blur rounded-2xl p-6 border border-slate-700/50 shadow-xl">
          {error && (
            <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {error}
            </div>
          )}
          <div className="mb-4">
            <label className="block text-sm font-medium text-slate-300 mb-1.5">メールアドレス</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg bg-slate-700/50 border border-slate-600 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              placeholder="reviewer@example.com"
              required
            />
          </div>
          <div className="mb-6">
            <label className="block text-sm font-medium text-slate-300 mb-1.5">パスワード</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg bg-slate-700/50 border border-slate-600 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              placeholder="••••••••"
              required
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-gradient-to-r from-orange-500 to-amber-500 text-white font-semibold hover:from-orange-600 hover:to-amber-600 transition-all disabled:opacity-50 shadow-lg shadow-orange-500/20"
          >
            {loading ? '認証中...' : 'ログイン'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ── Reviewer Stats Bar ──
function StatsBar({ me, sessionStart }) {
  const [elapsed, setElapsed] = useState('0:00');

  useEffect(() => {
    if (!sessionStart) return;
    const interval = setInterval(() => {
      const diff = Math.floor((Date.now() - new Date(sessionStart).getTime()) / 1000);
      const h = Math.floor(diff / 3600);
      const m = Math.floor((diff % 3600) / 60);
      const s = diff % 60;
      setElapsed(h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`);
    }, 1000);
    return () => clearInterval(interval);
  }, [sessionStart]);

  const today = me?.today || {};
  const allTime = me?.all_time || {};

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700/50">
        <div className="text-xs text-slate-400 mb-1">セッション時間</div>
        <div className="text-lg font-bold text-white font-mono">{elapsed}</div>
      </div>
      <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700/50">
        <div className="text-xs text-slate-400 mb-1">今日の採点数</div>
        <div className="text-lg font-bold text-orange-400">{today.rated_count || 0}</div>
      </div>
      <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700/50">
        <div className="text-xs text-slate-400 mb-1">今日の平均</div>
        <div className="text-lg font-bold text-amber-400">{today.avg_rating || '—'}</div>
      </div>
      <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-700/50">
        <div className="text-xs text-slate-400 mb-1">累計採点数</div>
        <div className="text-lg font-bold text-emerald-400">{allTime.total_rated || 0}</div>
      </div>
    </div>
  );
}

// ── Single Feedback Card ──
function FeedbackCard({ fb, token, onRated }) {
  const [localRating, setLocalRating] = useState(fb.user_rating);
  const [comment, setComment] = useState(fb.user_comment || '');
  const [hoverStar, setHoverStar] = useState(0);
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const videoRef = useRef(null);

  const displayRating = localRating || 0;
  const ratingColors = ['', 'text-red-500', 'text-orange-500', 'text-yellow-500', 'text-lime-500', 'text-emerald-500'];
  const ratingColor = ratingColors[displayRating] || 'text-gray-400';

  const handleRate = async (star) => {
    if (saving) return;
    setSaving(true);
    try {
      await reviewerApi.rate(token, fb.video_id, fb.phase_index, star, comment);
      setLocalRating(star);
      if (onRated) onRated(fb.video_id, fb.phase_index, star);
    } catch (err) {
      alert('採点に失敗しました: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSaving(false);
    }
  };

  const handleCommentSave = async () => {
    if (!localRating) return;
    setSaving(true);
    try {
      await reviewerApi.rate(token, fb.video_id, fb.phase_index, localRating, comment);
    } catch (err) {
      alert('コメント保存に失敗しました');
    } finally {
      setSaving(false);
    }
  };

  const clipUrl = fb.clip_url;
  const hasClip = !!clipUrl;

  return (
    <div className={`rounded-xl border transition-all ${
      expanded
        ? 'bg-slate-800/80 border-orange-500/50 shadow-lg shadow-orange-500/10'
        : localRating
          ? 'bg-slate-800/40 border-slate-700/50 hover:border-slate-600'
          : 'bg-slate-800/60 border-amber-500/30 hover:border-amber-500/50 hover:shadow-md'
    }`}>
      {/* Header */}
      <div
        className="p-4 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1.5">
              {/* Star rating */}
              <div className="flex items-center gap-0.5" onMouseLeave={() => setHoverStar(0)}>
                {[1, 2, 3, 4, 5].map((star) => {
                  const filled = hoverStar > 0 ? star <= hoverStar : star <= displayRating;
                  return (
                    <button
                      key={star}
                      disabled={saving}
                      className={`text-lg transition-all cursor-pointer hover:scale-125 ${
                        filled ? (hoverStar > 0 ? 'text-orange-400' : ratingColor) : 'text-slate-600'
                      } ${saving ? 'opacity-50' : ''}`}
                      onMouseEnter={() => setHoverStar(star)}
                      onClick={(e) => { e.stopPropagation(); handleRate(star); }}
                      title={`${star}点`}
                    >
                      ★
                    </button>
                  );
                })}
              </div>
              {localRating && (
                <span className={`text-sm font-bold ${ratingColor}`}>{localRating}/5</span>
              )}
              {!localRating && (
                <span className="text-xs text-amber-400/70 font-medium">未採点</span>
              )}
            </div>
            <p className="text-sm text-slate-300 line-clamp-2">{fb.phase_description || '説明なし'}</p>
            <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-500">
              <span>{fb.original_filename || 'Unknown'}</span>
              {fb.time_start != null && (
                <span>
                  {Math.floor(fb.time_start / 60)}:{String(Math.floor(fb.time_start % 60)).padStart(2, '0')}
                  {' → '}
                  {Math.floor(fb.time_end / 60)}:{String(Math.floor(fb.time_end % 60)).padStart(2, '0')}
                </span>
              )}
              {hasClip && <span className="text-emerald-400">🎬 クリップあり</span>}
              {fb.rated_by_reviewer_id && (
                <span className="text-blue-400">👤 採点済</span>
              )}
            </div>
          </div>
          <div className="text-slate-500">
            <svg className={`w-5 h-5 transition-transform ${expanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-slate-700/50">
          {/* Video player */}
          {hasClip && (
            <div className="mt-3 rounded-lg overflow-hidden bg-black">
              <video
                ref={videoRef}
                src={clipUrl}
                controls
                preload="metadata"
                className="w-full max-h-[400px]"
                style={{ objectFit: 'contain' }}
              />
            </div>
          )}
          {!hasClip && fb.compressed_blob_url && (
            <div className="mt-3 p-3 rounded-lg bg-slate-700/30 text-slate-400 text-sm text-center">
              クリップ未生成 — ソース動画の {Math.floor((fb.time_start || 0) / 60)}:{String(Math.floor((fb.time_start || 0) % 60)).padStart(2, '0')} 〜 {Math.floor((fb.time_end || 0) / 60)}:{String(Math.floor((fb.time_end || 0) % 60)).padStart(2, '0')} を確認してください
            </div>
          )}

          {/* Full description */}
          {fb.phase_description && (
            <div className="mt-3 p-3 rounded-lg bg-slate-700/30">
              <p className="text-sm text-slate-300 whitespace-pre-wrap">{fb.phase_description}</p>
            </div>
          )}

          {/* Comment input */}
          <div className="mt-3">
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="コメント（任意）"
              rows={2}
              className="w-full px-3 py-2 rounded-lg bg-slate-700/50 border border-slate-600 text-white text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-orange-500 resize-none"
            />
            {comment !== (fb.user_comment || '') && localRating && (
              <button
                onClick={handleCommentSave}
                disabled={saving}
                className="mt-1.5 px-3 py-1 text-xs rounded-lg bg-orange-500/20 text-orange-400 hover:bg-orange-500/30 transition-colors"
              >
                コメント保存
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Reviewer Dashboard ──
function ReviewerDashboard({ token, reviewer, sessionId, onLogout }) {
  const [me, setMe] = useState(null);
  const [feedbacks, setFeedbacks] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [perPage] = useState(20);
  const [totalPages, setTotalPages] = useState(0);
  const [loading, setLoading] = useState(false);
  const [filterRating, setFilterRating] = useState('');  // '' = unrated
  const [clipFilter, setClipFilter] = useState('yes');  // default: show only with clips
  const heartbeatRef = useRef(null);

  // Fetch reviewer info
  const fetchMe = useCallback(async () => {
    try {
      const data = await reviewerApi.getMe(token);
      setMe(data);
    } catch (err) {
      if (err.response?.status === 401) onLogout();
    }
  }, [token, onLogout]);

  // Fetch feedbacks
  const fetchFeedbacks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await reviewerApi.getFeedbacks(token, page, perPage, filterRating, clipFilter);
      setFeedbacks(data.feedbacks);
      setTotal(data.total);
      setTotalPages(data.total_pages);
    } catch (err) {
      if (err.response?.status === 401) onLogout();
    } finally {
      setLoading(false);
    }
  }, [token, page, perPage, filterRating, clipFilter, onLogout]);

  useEffect(() => { fetchMe(); }, [fetchMe]);
  useEffect(() => { fetchFeedbacks(); }, [fetchFeedbacks]);

  // Heartbeat every 2 minutes
  useEffect(() => {
    heartbeatRef.current = setInterval(() => {
      reviewerApi.heartbeat(token).catch(() => {});
    }, 120000);
    return () => clearInterval(heartbeatRef.current);
  }, [token]);

  // Refresh me stats after rating
  const handleRated = useCallback(() => {
    fetchMe();
  }, [fetchMe]);

  const handleLogout = async () => {
    try {
      await reviewerApi.logout(token);
    } catch {}
    onLogout();
  };

  const sessionStart = me?.active_session?.started_at;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
      {/* Top bar */}
      <header className="sticky top-0 z-50 bg-slate-900/80 backdrop-blur border-b border-slate-700/50">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-orange-500 to-amber-500 flex items-center justify-center">
              <span className="text-white text-sm font-bold">A</span>
            </div>
            <div>
              <h1 className="text-sm font-bold text-white">AitherHub 採点</h1>
              <p className="text-xs text-slate-400">{reviewer.display_name} ({reviewer.email})</p>
            </div>
          </div>
          <button
            onClick={handleLogout}
            className="px-3 py-1.5 text-xs rounded-lg bg-slate-700/50 text-slate-300 hover:bg-slate-700 hover:text-white transition-colors border border-slate-600/50"
          >
            ログアウト
          </button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6 space-y-6">
        {/* Stats */}
        <StatsBar me={me} sessionStart={sessionStart} />

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-400">フィルタ:</label>
            <select
              value={filterRating}
              onChange={(e) => { setFilterRating(e.target.value); setPage(1); }}
              className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-slate-700 text-white focus:outline-none focus:ring-2 focus:ring-orange-500"
            >
              <option value="">未採点のみ</option>
              <option value="all">すべて</option>
              <option value="rated">採点済みのみ</option>
              <option value="mine">自分の採点のみ</option>
              <option value="1">★1</option>
              <option value="2">★2</option>
              <option value="3">★3</option>
              <option value="4">★4</option>
              <option value="5">★5</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-400">クリップ:</label>
            <select
              value={clipFilter}
              onChange={(e) => { setClipFilter(e.target.value); setPage(1); }}
              className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-slate-700 text-white focus:outline-none focus:ring-2 focus:ring-orange-500"
            >
              <option value="yes">クリップありのみ</option>
              <option value="">すべて</option>
              <option value="no">クリップなしのみ</option>
            </select>
          </div>
          <div className="ml-auto text-xs text-slate-500">
            {total.toLocaleString()} 件中 {((page - 1) * perPage) + 1}〜{Math.min(page * perPage, total)} 件
          </div>
        </div>

        {/* Feedback list */}
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="animate-spin rounded-full h-8 w-8 border-2 border-orange-500 border-t-transparent" />
          </div>
        ) : feedbacks.length === 0 ? (
          <div className="text-center py-16 text-slate-500">
            <p className="text-lg mb-2">該当するフェーズがありません</p>
            <p className="text-sm">フィルタを変更してみてください</p>
          </div>
        ) : (
          <div className="space-y-3">
            {feedbacks.map((fb, i) => (
              <FeedbackCard
                key={`${fb.video_id}-${fb.phase_index}`}
                fb={fb}
                token={token}
                onRated={handleRated}
              />
            ))}
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 pt-4">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-slate-700 text-white hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              ← 前へ
            </button>
            <span className="text-sm text-slate-400 px-3">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 border border-slate-700 text-white hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              次へ →
            </button>
          </div>
        )}
      </main>
    </div>
  );
}

// ── Main Component ──
export default function ReviewerPage() {
  const [token, setToken] = useState(() => localStorage.getItem('reviewer_token'));
  const [reviewer, setReviewer] = useState(() => {
    try { return JSON.parse(localStorage.getItem('reviewer_info')); } catch { return null; }
  });
  const [sessionId, setSessionId] = useState(() => localStorage.getItem('reviewer_session_id'));

  const handleLogin = (data) => {
    setToken(data.access_token);
    setReviewer(data.reviewer);
    setSessionId(data.session_id);
    localStorage.setItem('reviewer_token', data.access_token);
    localStorage.setItem('reviewer_info', JSON.stringify(data.reviewer));
    localStorage.setItem('reviewer_session_id', data.session_id);
  };

  const handleLogout = () => {
    setToken(null);
    setReviewer(null);
    setSessionId(null);
    localStorage.removeItem('reviewer_token');
    localStorage.removeItem('reviewer_info');
    localStorage.removeItem('reviewer_session_id');
  };

  if (!token || !reviewer) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return (
    <ReviewerDashboard
      token={token}
      reviewer={reviewer}
      sessionId={sessionId}
      onLogout={handleLogout}
    />
  );
}
