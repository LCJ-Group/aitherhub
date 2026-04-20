import React, { useState, useEffect, useCallback, useRef } from 'react';
import Highcharts from 'highcharts';
import HighchartsReact from 'highcharts-react-official';

const API_BASE = import.meta.env.VITE_API_URL || 'https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1';

// ─── Auth helpers ───
function getToken() { return localStorage.getItem('brand_token'); }
function setToken(t) { localStorage.setItem('brand_token', t); }
function clearToken() { localStorage.removeItem('brand_token'); localStorage.removeItem('brand_info'); }
function getBrandInfo() { try { return JSON.parse(localStorage.getItem('brand_info') || '{}'); } catch { return {}; } }
function setBrandInfo(info) { localStorage.setItem('brand_info', JSON.stringify(info)); }

async function brandFetch(path, opts = {}) {
  const token = getToken();
  const headers = { ...(opts.headers || {}), 'Authorization': `Bearer ${token}` };
  if (!(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers });
  if (res.status === 401) { clearToken(); window.location.reload(); return null; }
  return res;
}

// ─── Styles ───
const colors = {
  bg: '#0a0a0a', card: '#141414', cardHover: '#1a1a1a', border: '#2a2a2a',
  accent: '#a855f7', accentHover: '#9333ea', accentLight: 'rgba(168,85,247,0.1)',
  text: '#ffffff', textSecondary: '#a1a1aa', textMuted: '#71717a',
  success: '#22c55e', danger: '#ef4444', warning: '#f59e0b',
  info: '#3b82f6',
};

const baseCard = {
  background: colors.card, borderRadius: 16, border: `1px solid ${colors.border}`,
  padding: 24, transition: 'all 0.2s',
};

// ─── Login Component ───
function LoginPage({ onLogin }) {
  const [clientId, setClientId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('id')) setClientId(params.get('id'));
  }, []);

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoading(true); setError('');
    try {
      const res = await fetch(`${API_BASE}/brand/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId, password }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'ログインに失敗しました'); return; }
      setToken(data.token);
      setBrandInfo({ client_id: data.client_id, name: data.name, domain: data.domain });
      onLogin(data);
    } catch (err) {
      setError('通信エラーが発生しました');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', background: colors.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ ...baseCard, width: 400, maxWidth: '90vw' }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <h1 style={{ color: colors.text, fontSize: 28, fontWeight: 700, margin: 0 }}>
            <span style={{ color: colors.accent }}>Aither</span>Hub
          </h1>
          <p style={{ color: colors.textSecondary, marginTop: 8, fontSize: 14 }}>ブランドポータル</p>
        </div>
        <form onSubmit={handleLogin}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ color: colors.textSecondary, fontSize: 13, display: 'block', marginBottom: 6 }}>クライアントID</label>
            <input value={clientId} onChange={e => setClientId(e.target.value)}
              style={{ width: '100%', padding: '10px 14px', background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: 8, color: colors.text, fontSize: 15, outline: 'none', boxSizing: 'border-box' }}
              placeholder="例: cab30223" />
          </div>
          <div style={{ marginBottom: 24 }}>
            <label style={{ color: colors.textSecondary, fontSize: 13, display: 'block', marginBottom: 6 }}>パスワード</label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)}
              style={{ width: '100%', padding: '10px 14px', background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: 8, color: colors.text, fontSize: 15, outline: 'none', boxSizing: 'border-box' }} />
          </div>
          {error && <p style={{ color: colors.danger, fontSize: 13, marginBottom: 16 }}>{error}</p>}
          <button type="submit" disabled={loading}
            style={{ width: '100%', padding: '12px 0', background: colors.accent, color: '#fff', border: 'none', borderRadius: 10, fontSize: 15, fontWeight: 600, cursor: 'pointer', opacity: loading ? 0.6 : 1 }}>
            {loading ? 'ログイン中...' : 'ログイン'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ─── Video Upload Component ───
function VideoUploader({ onUploaded }) {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);

  const handleUpload = async (file) => {
    if (!file || !file.type.startsWith('video/')) { alert('動画ファイルを選択してください'); return; }
    setUploading(true); setProgress(0);
    try {
      const sasRes = await brandFetch('/brand/upload/sas', {
        method: 'POST', body: JSON.stringify({ filename: file.name }),
      });
      const sasData = await sasRes.json();
      const xhr = new XMLHttpRequest();
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) setProgress(Math.round(e.loaded / e.total * 100)); };
      await new Promise((resolve, reject) => {
        xhr.onload = () => xhr.status < 400 ? resolve() : reject(new Error(`Upload failed: ${xhr.status}`));
        xhr.onerror = () => reject(new Error('Upload failed'));
        xhr.open('PUT', sasData.upload_url);
        xhr.setRequestHeader('x-ms-blob-type', 'BlockBlob');
        xhr.setRequestHeader('Content-Type', file.type);
        xhr.send(file);
      });
      const regRes = await brandFetch('/brand/clips', {
        method: 'POST', body: JSON.stringify({ blob_url: sasData.blob_url, title: file.name }),
      });
      const regData = await regRes.json();
      setProgress(100);
      if (onUploaded) onUploaded(regData);
    } catch (err) {
      alert('アップロードに失敗しました: ' + err.message);
    } finally {
      setUploading(false); setProgress(0);
    }
  };

  const handleDrop = (e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files[0]) handleUpload(e.dataTransfer.files[0]); };

  return (
    <div
      onDragOver={e => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      onClick={() => !uploading && fileRef.current?.click()}
      style={{
        ...baseCard, textAlign: 'center', cursor: uploading ? 'default' : 'pointer',
        border: `2px dashed ${dragOver ? colors.accent : colors.border}`,
        background: dragOver ? colors.accentLight : colors.card,
      }}>
      <input ref={fileRef} type="file" accept="video/*" style={{ display: 'none' }}
        onChange={e => e.target.files[0] && handleUpload(e.target.files[0])} />
      {uploading ? (
        <div>
          <p style={{ color: colors.text, fontSize: 16, margin: '0 0 12px' }}>アップロード中... {progress}%</p>
          <div style={{ width: '100%', height: 6, background: colors.bg, borderRadius: 3 }}>
            <div style={{ width: `${progress}%`, height: '100%', background: colors.accent, borderRadius: 3, transition: 'width 0.3s' }} />
          </div>
        </div>
      ) : (
        <div>
          <div style={{ fontSize: 40, marginBottom: 8 }}>📹</div>
          <p style={{ color: colors.text, fontSize: 16, margin: '0 0 4px' }}>動画をドラッグ＆ドロップ</p>
          <p style={{ color: colors.textMuted, fontSize: 13, margin: 0 }}>またはクリックして選択（MP4推奨）</p>
        </div>
      )}
    </div>
  );
}

// ─── Clip Card Component ───
function ClipCard({ clip, onAssign, onRemove, onEdit, onDownload, onPin, isWidget, isRecommended }) {
  const [playing, setPlaying] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const videoRef = useRef(null);

  const handleDownload = async (e) => {
    e.stopPropagation();
    if (downloading) return;
    setDownloading(true);
    try {
      if (onDownload) {
        await onDownload(clip.clip_id);
      }
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div style={{ ...baseCard, padding: 0, overflow: 'hidden', position: 'relative' }}>
      {/* Video / Thumbnail */}
      <div style={{ position: 'relative', aspectRatio: '9/16', background: '#000', cursor: 'pointer' }}
        onClick={() => { setPlaying(!playing); if (videoRef.current) playing ? videoRef.current.pause() : videoRef.current.play(); }}>
        {playing ? (
          <video ref={videoRef} src={clip.clip_url} autoPlay playsInline
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            onEnded={() => setPlaying(false)} />
        ) : (
          <div style={{ width: '100%', height: '100%', position: 'relative', background: '#111' }}>
            {clip.thumbnail_url ? (
              <img src={clip.thumbnail_url} style={{ width: '100%', height: '100%', objectFit: 'cover' }} alt="" />
            ) : clip.clip_url ? (
              <video src={clip.clip_url} preload="metadata" muted playsInline
                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                onLoadedData={(e) => { e.target.currentTime = 0.5; }} />
            ) : (
              <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <div style={{ fontSize: 48, opacity: 0.5 }}>▶</div>
              </div>
            )}
            {!playing && (
              <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', fontSize: 36, color: 'rgba(255,255,255,0.8)', textShadow: '0 2px 8px rgba(0,0,0,0.5)', pointerEvents: 'none' }}>▶</div>
            )}
          </div>
        )}
        {clip.duration_sec && (
          <span style={{ position: 'absolute', bottom: 8, right: 8, background: 'rgba(0,0,0,0.7)', color: '#fff', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>
            {Math.floor(clip.duration_sec / 60)}:{String(Math.floor(clip.duration_sec % 60)).padStart(2, '0')}
          </span>
        )}
        {isWidget && (
          <span style={{ position: 'absolute', top: 8, left: 8, background: colors.success, color: '#fff', padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>
            配信中
          </span>
        )}
        {isWidget && clip.is_pinned && (
          <span style={{ position: 'absolute', top: 8, right: 8, background: '#f59e0b', color: '#fff', padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>
            ★ 優先
          </span>
        )}
        {isRecommended && clip.is_assigned && (
          <span style={{ position: 'absolute', top: 8, left: 8, background: colors.info, color: '#fff', padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>
            追加済み
          </span>
        )}
      </div>
      {/* Info */}
      <div style={{ padding: 12 }}>
        <p style={{ color: colors.text, fontSize: 13, margin: '0 0 4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {clip.product_name || clip.widget_product_name || clip.liver_name || 'クリップ'}
        </p>
        {(clip.widget_product_price || clip.product_price) && (
          <p style={{ color: colors.accent, fontSize: 13, fontWeight: 600, margin: '0 0 4px' }}>
            {clip.widget_product_price || clip.product_price}
          </p>
        )}
        <p style={{ color: colors.textMuted, fontSize: 11, margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {clip.transcript_text?.slice(0, 50) || clip.clip_id?.slice(0, 8) || ''}
        </p>
        {/* Actions */}
        <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
          {isWidget ? (
            <>
              <button onClick={() => onPin && onPin(clip.clip_id)}
                style={{ flex: 0, minWidth: 36, padding: '6px 8px', background: clip.is_pinned ? 'rgba(245,158,11,0.2)' : 'rgba(245,158,11,0.05)', color: '#f59e0b', border: `1px solid ${clip.is_pinned ? '#f59e0b' : 'rgba(245,158,11,0.3)'}`, borderRadius: 6, fontSize: 14, cursor: 'pointer', lineHeight: 1 }}
                title={clip.is_pinned ? '優先解除' : '優先表示'}>
                {clip.is_pinned ? '★' : '☆'}
              </button>
              <button onClick={() => onEdit && onEdit(clip)}
                style={{ flex: 1, padding: '6px 0', background: colors.accentLight, color: colors.accent, border: `1px solid ${colors.accent}`, borderRadius: 6, fontSize: 12, cursor: 'pointer' }}>
                編集
              </button>
              <button onClick={() => onRemove && onRemove(clip.clip_id)}
                style={{ flex: 1, padding: '6px 0', background: 'rgba(239,68,68,0.1)', color: colors.danger, border: `1px solid ${colors.danger}`, borderRadius: 6, fontSize: 12, cursor: 'pointer' }}>
                削除
              </button>
            </>
          ) : isRecommended ? (
            <>
              {!clip.is_assigned && (
                <button onClick={() => onAssign && onAssign(clip.clip_id)}
                  style={{ flex: 1, padding: '6px 0', background: colors.accent, color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
                  ＋ 追加
                </button>
              )}
              <button onClick={handleDownload} disabled={downloading}
                style={{ flex: 1, padding: '6px 0', background: 'rgba(59,130,246,0.1)', color: colors.info, border: `1px solid ${colors.info}`, borderRadius: 6, fontSize: 12, cursor: 'pointer', opacity: downloading ? 0.6 : 1 }}>
                {downloading ? '...' : '↓ DL'}
              </button>
            </>
          ) : (
            <>
              <button onClick={() => onAssign && onAssign(clip.clip_id)}
                style={{ flex: 1, padding: '6px 0', background: colors.accent, color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
                ＋ 追加
              </button>
              <button onClick={handleDownload} disabled={downloading}
                style={{ flex: 0, minWidth: 40, padding: '6px 8px', background: 'rgba(59,130,246,0.1)', color: colors.info, border: `1px solid ${colors.info}`, borderRadius: 6, fontSize: 12, cursor: 'pointer', opacity: downloading ? 0.6 : 1 }}>
                {downloading ? '...' : '↓'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Product Edit Modal ───
function ProductEditModal({ clip, onSave, onClose }) {
  const [form, setForm] = useState({
    product_name: clip.widget_product_name || clip.product_name || '',
    product_price: clip.widget_product_price || clip.product_price || '',
    product_image_url: clip.widget_product_image_url || '',
    product_url: clip.widget_product_url || '',
    product_cart_url: clip.widget_product_cart_url || '',
    page_url_pattern: clip.page_url_pattern || '',
  });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await brandFetch(`/brand/clips/${clip.clip_id}`, {
        method: 'PUT', body: JSON.stringify(form),
      });
      onSave();
    } catch (err) {
      alert('保存に失敗しました');
    } finally {
      setSaving(false);
    }
  };

  const fieldStyle = { width: '100%', padding: '10px 14px', background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: 8, color: colors.text, fontSize: 14, outline: 'none', boxSizing: 'border-box' };
  const labelStyle = { color: colors.textSecondary, fontSize: 12, display: 'block', marginBottom: 4 };

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999 }}
      onClick={onClose}>
      <div style={{ ...baseCard, width: 480, maxWidth: '90vw', maxHeight: '90vh', overflow: 'auto' }} onClick={e => e.stopPropagation()}>
        <h3 style={{ color: colors.text, margin: '0 0 20px', fontSize: 18 }}>商品情報を編集</h3>
        {[
          ['product_name', '商品名', '例: KYOGOKUカラーシャンプー'],
          ['product_price', '価格', '例: ¥3,980'],
          ['product_url', '商品ページURL', 'https://...'],
          ['product_cart_url', 'カート追加URL（任意）', 'https://...'],
          ['product_image_url', '商品画像URL（任意）', 'https://...'],
          ['page_url_pattern', '表示ページパターン（任意）', '例: /products/*'],
        ].map(([key, label, placeholder]) => (
          <div key={key} style={{ marginBottom: 14 }}>
            <label style={labelStyle}>{label}</label>
            <input value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })}
              placeholder={placeholder} style={fieldStyle} />
          </div>
        ))}
        <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
          <button onClick={onClose} style={{ flex: 1, padding: '10px 0', background: 'transparent', color: colors.textSecondary, border: `1px solid ${colors.border}`, borderRadius: 8, fontSize: 14, cursor: 'pointer' }}>
            キャンセル
          </button>
          <button onClick={handleSave} disabled={saving}
            style={{ flex: 1, padding: '10px 0', background: colors.accent, color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', opacity: saving ? 0.6 : 1 }}>
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Analytics Component ───
// ─── Enhanced Analytics Panel (v2) ───
function KpiCard({ label, value, sub, growth, color }) {
  const growthColor = growth > 0 ? colors.success : growth < 0 ? colors.danger : colors.textMuted;
  return (
    <div style={{ ...baseCard, textAlign: 'center', minWidth: 130 }}>
      <div style={{ fontSize: 26, fontWeight: 700, color: color || colors.text }}>{value}</div>
      <div style={{ fontSize: 12, color: colors.textSecondary, marginTop: 4 }}>{label}</div>
      {sub && <div style={{ fontSize: 11, color: colors.textMuted, marginTop: 2 }}>{sub}</div>}
      {growth !== undefined && growth !== null && (
        <div style={{ fontSize: 11, color: growthColor, marginTop: 4, fontWeight: 600 }}>
          {growth > 0 ? '+' : ''}{growth}% vs 前期
        </div>
      )}
    </div>
  );
}

function FunnelBar({ stages }) {
  if (!stages || !stages.length) return null;
  const maxCount = Math.max(...stages.map(s => s.count), 1);
  return (
    <div style={{ ...baseCard, marginTop: 20 }}>
      <h3 style={{ color: colors.text, fontSize: 16, margin: '0 0 16px', fontWeight: 600 }}>ファネル分析</h3>
      {stages.map((s, i) => (
        <div key={s.stage_key} style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ color: colors.textSecondary, fontSize: 13 }}>{s.stage}</span>
            <span style={{ color: colors.text, fontSize: 13, fontWeight: 600 }}>{s.count.toLocaleString()} ({s.rate}%)</span>
          </div>
          <div style={{ background: colors.bg, borderRadius: 6, height: 8, overflow: 'hidden' }}>
            <div style={{
              width: `${(s.count / maxCount) * 100}%`,
              height: '100%',
              background: `linear-gradient(90deg, ${colors.accent}, ${i > 3 ? colors.success : colors.info})`,
              borderRadius: 6,
              transition: 'width 0.6s ease',
            }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function ClipPerformanceTable({ clips, onFeedback }) {
  if (!clips || !clips.length) return (
    <div style={{ ...baseCard, marginTop: 20, textAlign: 'center', padding: 40 }}>
      <p style={{ color: colors.textMuted }}>クリップパフォーマンスデータがまだありません</p>
    </div>
  );
  return (
    <div style={{ ...baseCard, marginTop: 20, overflowX: 'auto' }}>
      <h3 style={{ color: colors.text, fontSize: 16, margin: '0 0 16px', fontWeight: 600 }}>クリップ別パフォーマンス</h3>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${colors.border}` }}>
            {['クリップ', '再生', '完了率', 'CTR', 'CVR', 'リプレイ', 'エンゲージ', 'CV', '評価'].map(h => (
              <th key={h} style={{ color: colors.textMuted, fontWeight: 500, padding: '8px 6px', textAlign: h === 'クリップ' ? 'left' : 'center', whiteSpace: 'nowrap' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {clips.map((c, i) => (
            <tr key={c.clip_id} style={{ borderBottom: `1px solid ${colors.border}` }}>
              <td style={{ padding: '10px 6px', display: 'flex', alignItems: 'center', gap: 8 }}>
                {c.thumbnail_url && <img src={c.thumbnail_url} alt="" style={{ width: 40, height: 40, borderRadius: 6, objectFit: 'cover' }} />}
                <div>
                  <div style={{ color: colors.text, fontWeight: 500, fontSize: 12 }}>{c.product_name || `Clip ${c.clip_id.slice(0, 6)}`}</div>
                  <div style={{ color: colors.textMuted, fontSize: 11 }}>{c.liver_name || ''}</div>
                </div>
              </td>
              <td style={{ textAlign: 'center', color: colors.text, fontWeight: 600 }}>{c.plays.toLocaleString()}</td>
              <td style={{ textAlign: 'center' }}>
                <span style={{ color: c.completion_rate >= 50 ? colors.success : c.completion_rate >= 25 ? colors.warning : colors.danger, fontWeight: 600 }}>
                  {c.completion_rate}%
                </span>
              </td>
              <td style={{ textAlign: 'center', color: colors.text }}>{c.ctr}%</td>
              <td style={{ textAlign: 'center', color: c.cvr > 0 ? colors.success : colors.textMuted, fontWeight: 600 }}>{c.cvr}%</td>
              <td style={{ textAlign: 'center', color: colors.textSecondary }}>{c.replays}</td>
              <td style={{ textAlign: 'center' }}>
                <div style={{
                  display: 'inline-block', padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 600,
                  background: c.engagement_score >= 60 ? 'rgba(34,197,94,0.15)' : c.engagement_score >= 30 ? 'rgba(245,158,11,0.15)' : 'rgba(239,68,68,0.15)',
                  color: c.engagement_score >= 60 ? colors.success : c.engagement_score >= 30 ? colors.warning : colors.danger,
                }}>{c.engagement_score}</div>
              </td>
              <td style={{ textAlign: 'center', color: colors.text, fontWeight: 600 }}>{c.purchases}</td>
              <td style={{ textAlign: 'center' }}>
                <FeedbackStars clipId={c.clip_id} onFeedback={onFeedback} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FeedbackStars({ clipId, onFeedback }) {
  const [rating, setRating] = useState(0);
  const [hover, setHover] = useState(0);
  const [saved, setSaved] = useState(false);

  const handleClick = async (star) => {
    setRating(star);
    try {
      await brandFetch(`/brand/clips/${clipId}/feedback`, {
        method: 'POST',
        body: JSON.stringify({ rating: star, tags: [], comment: '' }),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      if (onFeedback) onFeedback();
    } catch (e) { /* ignore */ }
  };

  return (
    <div style={{ display: 'flex', gap: 2, alignItems: 'center' }}>
      {[1, 2, 3, 4, 5].map(s => (
        <span key={s}
          onClick={() => handleClick(s)}
          onMouseEnter={() => setHover(s)}
          onMouseLeave={() => setHover(0)}
          style={{
            cursor: 'pointer', fontSize: 14,
            color: s <= (hover || rating) ? colors.warning : colors.textMuted,
            transition: 'color 0.15s',
          }}>
          {s <= (hover || rating) ? '\u2605' : '\u2606'}
        </span>
      ))}
      {saved && <span style={{ fontSize: 10, color: colors.success, marginLeft: 4 }}>OK</span>}
    </div>
  );
}

function AnalyticsPanel({ analytics }) {
  if (!analytics) return null;
  const stats = [
    { label: '動画再生', value: analytics.total_views || 0, icon: '\u25B6' },
    { label: 'CTAクリック', value: analytics.total_clicks || 0, icon: '\uD83D\uDD17' },
    { label: 'コンバージョン', value: analytics.total_conversions || 0, icon: '\u2713' },
  ];
  const cvr = analytics.total_views > 0
    ? ((analytics.total_conversions / analytics.total_views) * 100).toFixed(2) + '%'
    : '\u2014';

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
      {stats.map(s => (
        <div key={s.label} style={{ ...baseCard, textAlign: 'center' }}>
          <div style={{ fontSize: 28, fontWeight: 700, color: colors.text }}>{s.value.toLocaleString()}</div>
          <div style={{ fontSize: 13, color: colors.textSecondary, marginTop: 4 }}>{s.icon} {s.label}</div>
        </div>
      ))}
      <div style={{ ...baseCard, textAlign: 'center' }}>
        <div style={{ fontSize: 28, fontWeight: 700, color: colors.accent }}>{cvr}</div>
        <div style={{ fontSize: 13, color: colors.textSecondary, marginTop: 4 }}>CVR</div>
      </div>
    </div>
  );
}

// ─── GTM Tag Component ───
function GtmTagPanel({ tagData }) {
  const [copied, setCopied] = useState(false);
  if (!tagData) return null;

  const handleCopy = () => {
    navigator.clipboard.writeText(tagData.tag_html);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={baseCard}>
      <h3 style={{ color: colors.text, margin: '0 0 12px', fontSize: 16 }}>GTMタグ</h3>
      <p style={{ color: colors.textSecondary, fontSize: 13, margin: '0 0 12px' }}>{tagData.instructions}</p>
      <div style={{ position: 'relative' }}>
        <pre style={{ background: colors.bg, padding: 16, borderRadius: 8, color: colors.accent, fontSize: 12, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
          {tagData.tag_html}
        </pre>
        <button onClick={handleCopy}
          style={{ position: 'absolute', top: 8, right: 8, padding: '4px 12px', background: copied ? colors.success : colors.accent, color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, cursor: 'pointer' }}>
          {copied ? 'コピー済み' : 'コピー'}
        </button>
      </div>
    </div>
  );
}

// ─── Keywords Settings Component ───
function KeywordsSettings({ initialKeywords, onSaved }) {
  const [keywords, setKeywords] = useState(initialKeywords || '');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await brandFetch('/brand/keywords', {
        method: 'PUT', body: JSON.stringify({ keywords }),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      if (onSaved) onSaved(keywords);
    } catch (err) {
      alert('保存に失敗しました');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={baseCard}>
      <h3 style={{ color: colors.text, margin: '0 0 8px', fontSize: 16 }}>ブランドキーワード</h3>
      <p style={{ color: colors.textSecondary, fontSize: 13, margin: '0 0 16px' }}>
        おすすめクリップの自動マッチングに使用されます。カンマ区切りで複数入力できます。
      </p>
      <div style={{ display: 'flex', gap: 10 }}>
        <input value={keywords} onChange={e => setKeywords(e.target.value)}
          placeholder="例: KYOGOKU, 京極, ケラチン, シャンプー"
          style={{ flex: 1, padding: '10px 14px', background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: 8, color: colors.text, fontSize: 14, outline: 'none', boxSizing: 'border-box' }} />
        <button onClick={handleSave} disabled={saving}
          style={{ padding: '10px 20px', background: saved ? colors.success : colors.accent, color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', opacity: saving ? 0.6 : 1, whiteSpace: 'nowrap' }}>
          {saving ? '保存中...' : saved ? '保存済み' : '保存'}
        </button>
      </div>
    </div>
  );
}

// ─── Main Dashboard ───
function BrandDashboard({ brandInfo, onLogout }) {
  const [tab, setTab] = useState('widget');
  const [clips, setClips] = useState([]);
  const [widgetClips, setWidgetClips] = useState([]);
  const [recommendedClips, setRecommendedClips] = useState([]);
  const [recommendedTotal, setRecommendedTotal] = useState(0);
  const [recommendedKeywords, setRecommendedKeywords] = useState([]);
  const [recommendedSearch, setRecommendedSearch] = useState('');
  const [recommendedLoading, setRecommendedLoading] = useState(false);
  const [analytics, setAnalytics] = useState(null);
  const [tagData, setTagData] = useState(null);
  const [editClip, setEditClip] = useState(null);
  const [loading, setLoading] = useState(true);
  const [brandKeywords, setBrandKeywords] = useState('');
  // Enhanced analytics state
  const [analyticsDays, setAnalyticsDays] = useState(30);
  const [overview, setOverview] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [clipPerf, setClipPerf] = useState(null);
  const [dailyData, setDailyData] = useState(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [clipsRes, widgetRes, analyticsRes, tagRes] = await Promise.all([
        brandFetch('/brand/clips'),
        brandFetch('/brand/widget/clips'),
        brandFetch('/brand/analytics?days=30'),
        brandFetch('/brand/gtm-tag'),
      ]);
      if (clipsRes) setClips((await clipsRes.json()).clips || []);
      if (widgetRes) setWidgetClips((await widgetRes.json()).clips || []);
      if (analyticsRes) setAnalytics(await analyticsRes.json());
      if (tagRes) setTagData(await tagRes.json());
    } catch (err) {
      console.error('Failed to load data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRecommended = useCallback(async (searchQuery) => {
    setRecommendedLoading(true);
    try {
      const params = new URLSearchParams({ limit: '50', offset: '0' });
      if (searchQuery) params.set('q', searchQuery);
      const res = await brandFetch(`/brand/recommended-clips?${params}`);
      if (res) {
        const data = await res.json();
        setRecommendedClips(data.clips || []);
        setRecommendedTotal(data.total || 0);
        setRecommendedKeywords(data.keywords || []);
        if (data.message && !data.clips?.length) {
          // No keywords set
          setBrandKeywords('');
        }
      }
    } catch (err) {
      console.error('Failed to load recommended clips:', err);
    } finally {
      setRecommendedLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);
  useEffect(() => { if (tab === 'recommended') loadRecommended(recommendedSearch); }, [tab]);

  // Load enhanced analytics when tab switches or period changes
  const loadAnalytics = useCallback(async (days) => {
    setAnalyticsLoading(true);
    try {
      const [ovRes, fnRes, cpRes, dlRes] = await Promise.all([
        brandFetch(`/brand/analytics/overview?days=${days}`),
        brandFetch(`/brand/analytics/funnel?days=${days}`),
        brandFetch(`/brand/analytics/clip-performance?days=${days}`),
        brandFetch(`/brand/analytics/daily?days=${days}`),
      ]);
      if (ovRes) setOverview(await ovRes.json());
      if (fnRes) setFunnel(await fnRes.json());
      if (cpRes) setClipPerf(await cpRes.json());
      if (dlRes) setDailyData(await dlRes.json());
    } catch (err) {
      console.error('Failed to load enhanced analytics:', err);
    } finally {
      setAnalyticsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tab === 'analytics') loadAnalytics(analyticsDays);
  }, [tab, analyticsDays, loadAnalytics]);

  const handleAssign = async (clipId) => {
    await brandFetch('/brand/widget/clips', { method: 'POST', body: JSON.stringify({ clip_id: clipId }) });
    loadData();
    if (tab === 'recommended') loadRecommended(recommendedSearch);
  };

  const handleRemove = async (clipId) => {
    if (!confirm('このクリップをウィジェットから削除しますか？')) return;
    try {
      const res = await brandFetch(`/brand/widget/clips/${clipId}`, { method: 'DELETE' });
      if (res && !res.ok) {
        const err = await res.text();
        alert(`削除に失敗しました: ${err}`);
        return;
      }
      loadData();
    } catch (err) {
      alert(`削除に失敗しました: ${err.message}`);
    }
  };

  const handlePin = async (clipId) => {
    try {
      const res = await brandFetch(`/brand/widget/clips/${clipId}/pin`, { method: 'PUT' });
      if (res && !res.ok) {
        const err = await res.text();
        alert(`優先設定に失敗しました: ${err}`);
        return;
      }
      loadData();
    } catch (err) {
      alert(`優先設定に失敗しました: ${err.message}`);
    }
  };

  const handleDownload = async (clipId) => {
    try {
      const res = await brandFetch(`/brand/clips/${clipId}/download`);
      if (res) {
        const data = await res.json();
        if (data.download_url) {
          // Open download URL in new tab
          const a = document.createElement('a');
          a.href = data.download_url;
          a.download = data.filename || 'clip.mp4';
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        }
      }
    } catch (err) {
      alert('ダウンロードに失敗しました');
    }
  };

  const handleRecommendedSearch = (e) => {
    e.preventDefault();
    loadRecommended(recommendedSearch);
  };

  const tabs = [
    { id: 'widget', label: 'ウィジェット', icon: '📺' },
    { id: 'recommended', label: 'おすすめ', icon: '✨' },
    { id: 'upload', label: '動画管理', icon: '📹' },
    { id: 'analytics', label: 'アナリティクス', icon: '📊' },
    { id: 'settings', label: '設定', icon: '⚙' },
  ];

  return (
    <div style={{ minHeight: '100vh', background: colors.bg }}>
      {/* Header */}
      <header style={{ background: colors.card, borderBottom: `1px solid ${colors.border}`, padding: '12px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'sticky', top: 0, zIndex: 100 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <h1 style={{ color: colors.text, fontSize: 20, fontWeight: 700, margin: 0 }}>
            <span style={{ color: colors.accent }}>Aither</span>Hub
          </h1>
          <span style={{ color: colors.textMuted, fontSize: 13 }}>|</span>
          <span style={{ color: colors.textSecondary, fontSize: 14 }}>{brandInfo.name || getBrandInfo().name}</span>
        </div>
        <button onClick={onLogout}
          style={{ padding: '6px 16px', background: 'transparent', color: colors.textMuted, border: `1px solid ${colors.border}`, borderRadius: 6, fontSize: 13, cursor: 'pointer' }}>
          ログアウト
        </button>
      </header>

      {/* Tab Navigation */}
      <nav style={{ background: colors.card, borderBottom: `1px solid ${colors.border}`, padding: '0 24px', display: 'flex', gap: 0, overflowX: 'auto' }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{
              padding: '14px 16px', background: 'transparent', color: tab === t.id ? colors.accent : colors.textMuted,
              border: 'none', borderBottom: tab === t.id ? `2px solid ${colors.accent}` : '2px solid transparent',
              fontSize: 13, cursor: 'pointer', fontWeight: tab === t.id ? 600 : 400, whiteSpace: 'nowrap',
            }}>
            {t.icon} {t.label}
          </button>
        ))}
      </nav>

      {/* Content */}
      <main style={{ maxWidth: 1200, margin: '0 auto', padding: 24 }}>
        {loading && <p style={{ color: colors.textMuted, textAlign: 'center', padding: 40 }}>読み込み中...</p>}

        {/* Widget Tab */}
        {tab === 'widget' && !loading && (
          <div>
            <h2 style={{ color: colors.text, fontSize: 20, fontWeight: 600, margin: '0 0 20px' }}>
              ウィジェットに配信中のクリップ ({widgetClips.length})
            </h2>
            {widgetClips.length === 0 ? (
              <div style={{ ...baseCard, textAlign: 'center', padding: 40 }}>
                <p style={{ color: colors.textMuted, fontSize: 15 }}>まだクリップが割り当てられていません</p>
                <p style={{ color: colors.textMuted, fontSize: 13 }}>「おすすめ」タブからクリップを追加してください</p>
                <button onClick={() => setTab('recommended')}
                  style={{ marginTop: 16, padding: '10px 24px', background: colors.accent, color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
                  おすすめクリップを見る
                </button>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 16 }}>
                {widgetClips.map(c => (
                  <ClipCard key={c.clip_id} clip={c} isWidget onRemove={handleRemove} onEdit={setEditClip} onDownload={handleDownload} onPin={handlePin} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Recommended Tab */}
        {tab === 'recommended' && (
          <div>
            <h2 style={{ color: colors.text, fontSize: 20, fontWeight: 600, margin: '0 0 8px' }}>
              おすすめクリップ
            </h2>
            <p style={{ color: colors.textSecondary, fontSize: 13, margin: '0 0 20px' }}>
              あなたのブランドに関連するライブ配信の切り抜き動画です。ウィジェットに追加したり、ダウンロードしてSNSで使えます。
            </p>

            {/* Search */}
            <form onSubmit={handleRecommendedSearch} style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
              <input value={recommendedSearch} onChange={e => setRecommendedSearch(e.target.value)}
                placeholder="追加キーワードで絞り込み..."
                style={{ flex: 1, padding: '10px 14px', background: colors.card, border: `1px solid ${colors.border}`, borderRadius: 8, color: colors.text, fontSize: 14, outline: 'none', boxSizing: 'border-box' }} />
              <button type="submit"
                style={{ padding: '10px 20px', background: colors.accent, color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
                検索
              </button>
            </form>

            {/* Keywords info */}
            {recommendedKeywords.length > 0 && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 }}>
                <span style={{ color: colors.textMuted, fontSize: 12, lineHeight: '24px' }}>マッチキーワード:</span>
                {recommendedKeywords.map((kw, i) => (
                  <span key={i} style={{ padding: '2px 10px', background: colors.accentLight, color: colors.accent, borderRadius: 12, fontSize: 12, lineHeight: '20px' }}>
                    {kw}
                  </span>
                ))}
              </div>
            )}

            {recommendedLoading ? (
              <p style={{ color: colors.textMuted, textAlign: 'center', padding: 40 }}>検索中...</p>
            ) : recommendedClips.length === 0 ? (
              <div style={{ ...baseCard, textAlign: 'center', padding: 40 }}>
                <p style={{ color: colors.textMuted, fontSize: 15 }}>おすすめクリップが見つかりません</p>
                <p style={{ color: colors.textMuted, fontSize: 13 }}>「設定」タブでブランドキーワードを設定してください</p>
                <button onClick={() => setTab('settings')}
                  style={{ marginTop: 16, padding: '10px 24px', background: colors.accent, color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
                  キーワードを設定
                </button>
              </div>
            ) : (
              <>
                <p style={{ color: colors.textMuted, fontSize: 13, marginBottom: 16 }}>
                  {recommendedTotal}件のクリップが見つかりました
                </p>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 16 }}>
                  {recommendedClips.map(c => (
                    <ClipCard key={c.clip_id} clip={c} isRecommended onAssign={handleAssign} onDownload={handleDownload} />
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* Upload Tab */}
        {tab === 'upload' && !loading && (
          <div>
            <h2 style={{ color: colors.text, fontSize: 20, fontWeight: 600, margin: '0 0 20px' }}>動画管理</h2>
            <VideoUploader onUploaded={() => loadData()} />
            <div style={{ marginTop: 24 }}>
              <h3 style={{ color: colors.textSecondary, fontSize: 15, margin: '0 0 16px' }}>
                アップロード済み・利用可能なクリップ ({clips.length})
              </h3>
              {clips.length === 0 ? (
                <p style={{ color: colors.textMuted, fontSize: 14, textAlign: 'center', padding: 20 }}>
                  まだクリップがありません。上のエリアから動画をアップロードしてください。
                </p>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 16 }}>
                  {clips.map(c => (
                    <ClipCard key={c.clip_id} clip={c}
                      isWidget={c.widget_active}
                      onAssign={handleAssign}
                      onRemove={handleRemove}
                      onEdit={setEditClip}
                      onDownload={handleDownload}
                      onPin={handlePin} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Analytics Tab — Enhanced v2 */}
        {tab === 'analytics' && !loading && (
          <div>
            {/* Period Selector */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
              <h2 style={{ color: colors.text, fontSize: 20, fontWeight: 600, margin: 0 }}>アナリティクス</h2>
              <div style={{ display: 'flex', gap: 6 }}>
                {[7, 14, 30, 90].map(d => (
                  <button key={d} onClick={() => setAnalyticsDays(d)}
                    style={{
                      padding: '6px 14px', borderRadius: 8, fontSize: 12, fontWeight: 500, cursor: 'pointer',
                      background: analyticsDays === d ? colors.accent : colors.card,
                      color: analyticsDays === d ? '#fff' : colors.textSecondary,
                      border: `1px solid ${analyticsDays === d ? colors.accent : colors.border}`,
                    }}>
                    {d}日
                  </button>
                ))}
              </div>
            </div>

            {analyticsLoading && <p style={{ color: colors.textMuted, textAlign: 'center', padding: 40 }}>読み込み中...</p>}

            {!analyticsLoading && (
              <>
                {/* KPI Overview Cards */}
                {overview?.kpi && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
                    <KpiCard label="動画再生" value={(overview.kpi.plays || 0).toLocaleString()} growth={overview.kpi.plays_growth} />
                    <KpiCard label="ユニーク視聴者" value={(overview.kpi.unique_viewers || 0).toLocaleString()} />
                    <KpiCard label="完了率" value={`${overview.kpi.completion_rate || 0}%`}
                      sub={`平均${overview.kpi.avg_watch_sec || 0}秒`}
                      color={overview.kpi.completion_rate >= 50 ? colors.success : colors.warning} />
                    <KpiCard label="CTR" value={`${overview.kpi.ctr || 0}%`}
                      sub={`${(overview.kpi.clicks || 0).toLocaleString()}クリック`}
                      growth={overview.kpi.clicks_growth} />
                    <KpiCard label="購入" value={(overview.kpi.purchases || 0).toLocaleString()}
                      growth={overview.kpi.purchases_growth}
                      color={overview.kpi.purchases > 0 ? colors.success : colors.text} />
                    <KpiCard label="CVR" value={`${overview.kpi.cvr || 0}%`}
                      sub={`${(overview.kpi.conversions || 0).toLocaleString()}CV`}
                      growth={overview.kpi.conversions_growth}
                      color={colors.accent} />
                  </div>
                )}

                {/* Daily Chart (Highcharts) */}
                {dailyData?.daily?.length > 0 && (
                  <div style={{ ...baseCard, marginTop: 20 }}>
                    <h3 style={{ color: colors.text, fontSize: 16, margin: '0 0 8px', fontWeight: 600 }}>日別トレンド</h3>
                    <HighchartsReact highcharts={Highcharts} options={{
                      chart: { type: 'area', backgroundColor: 'transparent', height: 260, style: { fontFamily: 'inherit' } },
                      title: { text: null },
                      xAxis: {
                        categories: dailyData.daily.map(d => d.day.slice(5)),
                        labels: { style: { color: colors.textMuted, fontSize: '10px' } },
                        lineColor: colors.border, tickColor: colors.border,
                      },
                      yAxis: [
                        { title: { text: null }, labels: { style: { color: colors.textMuted, fontSize: '10px' } }, gridLineColor: colors.border },
                      ],
                      legend: { itemStyle: { color: colors.textSecondary, fontSize: '11px' } },
                      tooltip: { shared: true, backgroundColor: colors.card, borderColor: colors.border, style: { color: colors.text } },
                      plotOptions: { area: { fillOpacity: 0.15, marker: { radius: 2 } } },
                      series: [
                        { name: '再生', data: dailyData.daily.map(d => d.plays), color: colors.accent },
                        { name: 'クリック', data: dailyData.daily.map(d => d.clicks), color: colors.info },
                        { name: 'CV', data: dailyData.daily.map(d => d.conversions), color: colors.success },
                      ],
                      credits: { enabled: false },
                    }} />
                  </div>
                )}

                {/* Funnel */}
                <FunnelBar stages={funnel?.funnel} />

                {/* Clip Performance Table */}
                <ClipPerformanceTable clips={clipPerf?.clips} onFeedback={() => loadAnalytics(analyticsDays)} />

                {/* Fallback: basic analytics if enhanced not yet available */}
                {!overview && analytics && <AnalyticsPanel analytics={analytics} />}
              </>
            )}
          </div>
        )}

        {/* Settings Tab */}
        {tab === 'settings' && !loading && (
          <div>
            <h2 style={{ color: colors.text, fontSize: 20, fontWeight: 600, margin: '0 0 20px' }}>設定</h2>
            <KeywordsSettings
              initialKeywords={brandKeywords}
              onSaved={(kw) => { setBrandKeywords(kw); }}
            />
            <div style={{ marginTop: 20 }}>
              <GtmTagPanel tagData={tagData} />
            </div>
            <div style={{ ...baseCard, marginTop: 20 }}>
              <h3 style={{ color: colors.text, fontSize: 16, margin: '0 0 12px' }}>アカウント情報</h3>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  {[
                    ['クライアントID', getBrandInfo().client_id],
                    ['ブランド名', getBrandInfo().name],
                    ['ドメイン', getBrandInfo().domain],
                  ].map(([k, v]) => (
                    <tr key={k}>
                      <td style={{ color: colors.textSecondary, fontSize: 13, padding: '8px 0', width: 140 }}>{k}</td>
                      <td style={{ color: colors.text, fontSize: 13, padding: '8px 0' }}>{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>

      {/* Product Edit Modal */}
      {editClip && (
        <ProductEditModal clip={editClip} onClose={() => setEditClip(null)} onSave={() => { setEditClip(null); loadData(); }} />
      )}
    </div>
  );
}

// ─── Main Export ───
export default function BrandPortal() {
  const [loggedIn, setLoggedIn] = useState(!!getToken());
  const [brandInfo, setBrandInfoState] = useState(getBrandInfo());

  const handleLogin = (data) => {
    setBrandInfoState(data);
    setLoggedIn(true);
  };

  const handleLogout = () => {
    clearToken();
    setLoggedIn(false);
    setBrandInfoState({});
  };

  if (!loggedIn) return <LoginPage onLogin={handleLogin} />;
  return <BrandDashboard brandInfo={brandInfo} onLogout={handleLogout} />;
}
