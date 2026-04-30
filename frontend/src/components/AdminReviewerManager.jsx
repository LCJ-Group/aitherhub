import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

/**
 * AdminReviewerManager — 管理者ダッシュボード内の採点者管理タブ
 * 
 * 機能:
 * 1. 採点者一覧（統計付き）
 * 2. 新規採点者アカウント作成
 * 3. 採点者の有効/無効化
 * 4. セッション履歴
 * 5. 採点者ごとの評価分布グラフ
 */

export default function AdminReviewerManager({ adminKey }) {
  const [reviewers, setReviewers] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [selectedReviewer, setSelectedReviewer] = useState(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [createForm, setCreateForm] = useState({ email: '', password: '', display_name: '' });
  const [createError, setCreateError] = useState('');
  const [createSuccess, setCreateSuccess] = useState('');

  const headers = { 'X-Admin-Key': adminKey, 'Content-Type': 'application/json' };

  const fetchReviewers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/api/v1/admin/reviewers`, { headers, timeout: 15000 });
      setReviewers(res.data.reviewers || []);
    } catch (err) {
      console.error('Failed to fetch reviewers:', err);
    } finally {
      setLoading(false);
    }
  }, [adminKey]);

  const fetchSessions = useCallback(async (reviewerId) => {
    setSessionsLoading(true);
    try {
      const params = reviewerId ? `?reviewer_id=${reviewerId}` : '';
      const res = await axios.get(`${API_BASE}/api/v1/admin/review-sessions${params}`, { headers, timeout: 15000 });
      setSessions(res.data.sessions || []);
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
    } finally {
      setSessionsLoading(false);
    }
  }, [adminKey]);

  useEffect(() => { fetchReviewers(); }, [fetchReviewers]);

  const handleCreate = async (e) => {
    e.preventDefault();
    setCreateError('');
    setCreateSuccess('');
    try {
      const res = await axios.post(`${API_BASE}/api/v1/admin/reviewers`, createForm, { headers, timeout: 10000 });
      setCreateSuccess(`採点者「${createForm.display_name}」を作成しました (ID: ${res.data.reviewer_id})`);
      setCreateForm({ email: '', password: '', display_name: '' });
      fetchReviewers();
    } catch (err) {
      setCreateError(err.response?.data?.detail || '作成に失敗しました');
    }
  };

  const handleToggleActive = async (reviewer) => {
    try {
      await axios.put(`${API_BASE}/api/v1/admin/reviewers/${reviewer.id}`, {
        is_active: !reviewer.is_active,
      }, { headers, timeout: 10000 });
      fetchReviewers();
    } catch (err) {
      alert('更新に失敗しました: ' + (err.response?.data?.detail || err.message));
    }
  };

  const handleResetPassword = async (reviewer) => {
    const newPassword = prompt(`${reviewer.display_name} の新しいパスワードを入力:`);
    if (!newPassword) return;
    try {
      await axios.put(`${API_BASE}/api/v1/admin/reviewers/${reviewer.id}`, {
        password: newPassword,
      }, { headers, timeout: 10000 });
      alert('パスワードをリセットしました');
    } catch (err) {
      alert('パスワードリセットに失敗しました');
    }
  };

  const handleViewSessions = (reviewer) => {
    setSelectedReviewer(reviewer);
    fetchSessions(reviewer.id);
  };

  // Distribution bar chart
  const DistributionBar = ({ distribution }) => {
    const total = Object.values(distribution).reduce((a, b) => a + b, 0);
    if (total === 0) return <span className="text-gray-400 text-xs">データなし</span>;
    const colors = ['bg-red-500', 'bg-orange-500', 'bg-yellow-500', 'bg-lime-500', 'bg-emerald-500'];
    return (
      <div className="flex h-4 rounded-full overflow-hidden bg-gray-100 w-full">
        {[1, 2, 3, 4, 5].map((star) => {
          const pct = (distribution[String(star)] / total) * 100;
          if (pct === 0) return null;
          return (
            <div
              key={star}
              className={`${colors[star - 1]} transition-all`}
              style={{ width: `${pct}%` }}
              title={`★${star}: ${distribution[String(star)]}件 (${pct.toFixed(1)}%)`}
            />
          );
        })}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-900">採点者管理</h2>
          <p className="text-sm text-gray-500 mt-1">
            採点者アカウントの作成・管理、セッション履歴の確認
          </p>
        </div>
        <button
          onClick={() => setShowCreateForm(!showCreateForm)}
          className="px-4 py-2 text-sm rounded-lg bg-orange-500 text-white hover:bg-orange-600 transition-colors font-medium"
        >
          {showCreateForm ? '閉じる' : '＋ 新規採点者'}
        </button>
      </div>

      {/* Create form */}
      {showCreateForm && (
        <form onSubmit={handleCreate} className="bg-orange-50 rounded-xl p-4 border border-orange-200 space-y-3">
          <h3 className="font-semibold text-orange-800">新規採点者アカウント作成</h3>
          {createError && <div className="text-sm text-red-600 bg-red-50 p-2 rounded">{createError}</div>}
          {createSuccess && <div className="text-sm text-emerald-600 bg-emerald-50 p-2 rounded">{createSuccess}</div>}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <input
              type="text"
              placeholder="表示名（例: 田中太郎）"
              value={createForm.display_name}
              onChange={(e) => setCreateForm({ ...createForm, display_name: e.target.value })}
              className="px-3 py-2 rounded-lg border border-orange-300 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
              required
            />
            <input
              type="email"
              placeholder="メールアドレス"
              value={createForm.email}
              onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
              className="px-3 py-2 rounded-lg border border-orange-300 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
              required
            />
            <input
              type="text"
              placeholder="パスワード"
              value={createForm.password}
              onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
              className="px-3 py-2 rounded-lg border border-orange-300 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
              required
            />
          </div>
          <button type="submit" className="px-4 py-2 text-sm rounded-lg bg-orange-500 text-white hover:bg-orange-600 font-medium">
            作成
          </button>
        </form>
      )}

      {/* Reviewer list */}
      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-orange-500 border-t-transparent" />
        </div>
      ) : reviewers.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          <p className="text-lg">採点者がまだいません</p>
          <p className="text-sm mt-1">「＋ 新規採点者」から作成してください</p>
        </div>
      ) : (
        <div className="space-y-3">
          {reviewers.map((r) => (
            <div
              key={r.id}
              className={`rounded-xl border p-4 transition-all ${
                r.is_active ? 'bg-white border-gray-200 hover:border-orange-300 hover:shadow-md' : 'bg-gray-50 border-gray-200 opacity-60'
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-bold text-gray-900">{r.display_name}</span>
                    <span className="text-xs text-gray-500">({r.email})</span>
                    {!r.is_active && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-red-100 text-red-600 font-medium">無効</span>
                    )}
                  </div>
                  {/* Stats row */}
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mt-3">
                    <div>
                      <div className="text-xs text-gray-400">累計採点</div>
                      <div className="text-lg font-bold text-gray-900">{r.total_rated.toLocaleString()}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">今日の採点</div>
                      <div className="text-lg font-bold text-orange-500">{r.today_rated}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">平均スコア</div>
                      <div className="text-lg font-bold text-amber-500">{r.avg_rating}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">セッション数</div>
                      <div className="text-lg font-bold text-blue-500">{r.total_sessions}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-400">合計作業時間</div>
                      <div className="text-lg font-bold text-emerald-500">
                        {r.total_minutes >= 60
                          ? `${Math.floor(r.total_minutes / 60)}h ${Math.round(r.total_minutes % 60)}m`
                          : `${Math.round(r.total_minutes)}m`}
                      </div>
                    </div>
                  </div>
                  {/* Distribution */}
                  <div className="mt-3">
                    <div className="text-xs text-gray-400 mb-1">評価分布</div>
                    <DistributionBar distribution={r.distribution} />
                    <div className="flex justify-between mt-0.5 text-[10px] text-gray-400">
                      <span>★1: {r.distribution['1']}</span>
                      <span>★2: {r.distribution['2']}</span>
                      <span>★3: {r.distribution['3']}</span>
                      <span>★4: {r.distribution['4']}</span>
                      <span>★5: {r.distribution['5']}</span>
                    </div>
                  </div>
                  {r.last_session_at && (
                    <div className="text-xs text-gray-400 mt-2">
                      最終セッション: {new Date(r.last_session_at).toLocaleString('ja-JP')}
                    </div>
                  )}
                </div>
                {/* Actions */}
                <div className="flex flex-col gap-1.5">
                  <button
                    onClick={() => handleViewSessions(r)}
                    className="px-3 py-1 text-xs rounded-lg bg-blue-50 text-blue-600 hover:bg-blue-100 transition-colors"
                  >
                    セッション
                  </button>
                  <button
                    onClick={() => handleResetPassword(r)}
                    className="px-3 py-1 text-xs rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors"
                  >
                    PW変更
                  </button>
                  <button
                    onClick={() => handleToggleActive(r)}
                    className={`px-3 py-1 text-xs rounded-lg transition-colors ${
                      r.is_active
                        ? 'bg-red-50 text-red-600 hover:bg-red-100'
                        : 'bg-emerald-50 text-emerald-600 hover:bg-emerald-100'
                    }`}
                  >
                    {r.is_active ? '無効化' : '有効化'}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Session history modal */}
      {selectedReviewer && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setSelectedReviewer(null)}>
          <div className="bg-white rounded-2xl shadow-xl max-w-2xl w-full max-h-[80vh] overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="p-4 border-b border-gray-200 flex items-center justify-between">
              <div>
                <h3 className="font-bold text-gray-900">{selectedReviewer.display_name} のセッション履歴</h3>
                <p className="text-xs text-gray-500">{selectedReviewer.email}</p>
              </div>
              <button onClick={() => setSelectedReviewer(null)} className="text-gray-400 hover:text-gray-600 text-xl">✕</button>
            </div>
            <div className="p-4 overflow-y-auto max-h-[60vh]">
              {sessionsLoading ? (
                <div className="flex justify-center py-8">
                  <div className="animate-spin rounded-full h-6 w-6 border-2 border-orange-500 border-t-transparent" />
                </div>
              ) : sessions.length === 0 ? (
                <p className="text-center text-gray-400 py-8">セッション履歴がありません</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-500 border-b">
                      <th className="pb-2">開始</th>
                      <th className="pb-2">終了</th>
                      <th className="pb-2 text-right">時間</th>
                      <th className="pb-2 text-right">採点数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((s) => (
                      <tr key={s.id} className="border-b border-gray-100">
                        <td className="py-2 text-gray-700">
                          {new Date(s.started_at).toLocaleString('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </td>
                        <td className="py-2 text-gray-700">
                          {s.ended_at
                            ? new Date(s.ended_at).toLocaleString('ja-JP', { hour: '2-digit', minute: '2-digit' })
                            : <span className="text-emerald-500 font-medium">アクティブ</span>}
                        </td>
                        <td className="py-2 text-right text-gray-700">
                          {s.duration_minutes != null
                            ? s.duration_minutes >= 60
                              ? `${Math.floor(s.duration_minutes / 60)}h ${Math.round(s.duration_minutes % 60)}m`
                              : `${Math.round(s.duration_minutes)}m`
                            : '—'}
                        </td>
                        <td className="py-2 text-right font-medium text-orange-500">{s.clips_reviewed}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
