import React, { useState, useEffect, useCallback, useRef } from 'react';

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

  // Pre-fill from URL param
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
      // 1. Get SAS URL
      const sasRes = await brandFetch('/brand/upload/sas', {
        method: 'POST', body: JSON.stringify({ filename: file.name }),
      });
      const sasData = await sasRes.json();

      // 2. Upload directly to Azure Blob
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

      // 3. Register clip in DB
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
function ClipCard({ clip, onAssign, onRemove, onEdit, isWidget }) {
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef(null);

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
      </div>
      {/* Info */}
      <div style={{ padding: 12 }}>
        <p style={{ color: colors.text, fontSize: 13, margin: '0 0 4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {clip.product_name || clip.widget_product_name || clip.liver_name || 'クリップ'}
        </p>
        {(clip.product_price || clip.widget_product_price) && (
          <p style={{ color: colors.accent, fontSize: 13, fontWeight: 600, margin: '0 0 4px' }}>
            {clip.widget_product_price || clip.product_price}
          </p>
        )}
        <p style={{ color: colors.textMuted, fontSize: 11, margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {clip.transcript_text?.slice(0, 50) || clip.clip_id?.slice(0, 8) || ''}
        </p>
        {/* Actions */}
        <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          {isWidget ? (
            <>
              <button onClick={() => onEdit && onEdit(clip)}
                style={{ flex: 1, padding: '6px 0', background: colors.accentLight, color: colors.accent, border: `1px solid ${colors.accent}`, borderRadius: 6, fontSize: 12, cursor: 'pointer' }}>
                編集
              </button>
              <button onClick={() => onRemove && onRemove(clip.clip_id)}
                style={{ flex: 1, padding: '6px 0', background: 'rgba(239,68,68,0.1)', color: colors.danger, border: `1px solid ${colors.danger}`, borderRadius: 6, fontSize: 12, cursor: 'pointer' }}>
                削除
              </button>
            </>
          ) : (
            <button onClick={() => onAssign && onAssign(clip.clip_id)}
              style={{ flex: 1, padding: '8px 0', background: colors.accent, color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
              ＋ ウィジェットに追加
            </button>
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
function AnalyticsPanel({ analytics }) {
  if (!analytics) return null;
  const stats = [
    { label: '動画再生', value: analytics.total_views || 0, icon: '▶' },
    { label: 'CTAクリック', value: analytics.total_clicks || 0, icon: '🔗' },
    { label: 'コンバージョン', value: analytics.total_conversions || 0, icon: '✓' },
  ];
  const cvr = analytics.total_views > 0
    ? ((analytics.total_conversions / analytics.total_views) * 100).toFixed(2) + '%'
    : '—';

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

// ─── Main Dashboard ───
function BrandDashboard({ brandInfo, onLogout }) {
  const [tab, setTab] = useState('widget'); // widget, upload, analytics, settings
  const [clips, setClips] = useState([]);
  const [widgetClips, setWidgetClips] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [tagData, setTagData] = useState(null);
  const [editClip, setEditClip] = useState(null);
  const [loading, setLoading] = useState(true);

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

  useEffect(() => { loadData(); }, [loadData]);

  const handleAssign = async (clipId) => {
    await brandFetch('/brand/widget/clips', { method: 'POST', body: JSON.stringify({ clip_id: clipId }) });
    loadData();
  };

  const handleRemove = async (clipId) => {
    if (!confirm('このクリップをウィジェットから削除しますか？')) return;
    await brandFetch(`/brand/widget/clips/${clipId}`, { method: 'DELETE' });
    loadData();
  };

  const tabs = [
    { id: 'widget', label: 'ウィジェット', icon: '📺' },
    { id: 'upload', label: '動画管理', icon: '📹' },
    { id: 'analytics', label: 'アナリティクス', icon: '📊' },
    { id: 'settings', label: '設定', icon: '⚙' },
  ];

  // Clips not yet assigned to widget
  const unassignedClips = clips.filter(c => !c.widget_active);

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
      <nav style={{ background: colors.card, borderBottom: `1px solid ${colors.border}`, padding: '0 24px', display: 'flex', gap: 0 }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{
              padding: '14px 20px', background: 'transparent', color: tab === t.id ? colors.accent : colors.textMuted,
              border: 'none', borderBottom: tab === t.id ? `2px solid ${colors.accent}` : '2px solid transparent',
              fontSize: 14, cursor: 'pointer', fontWeight: tab === t.id ? 600 : 400,
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
                <p style={{ color: colors.textMuted, fontSize: 13 }}>「動画管理」タブから動画をアップロードして追加してください</p>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 16 }}>
                {widgetClips.map(c => (
                  <ClipCard key={c.clip_id} clip={c} isWidget onRemove={handleRemove} onEdit={setEditClip} />
                ))}
              </div>
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
                      onEdit={setEditClip} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Analytics Tab */}
        {tab === 'analytics' && !loading && (
          <div>
            <h2 style={{ color: colors.text, fontSize: 20, fontWeight: 600, margin: '0 0 20px' }}>アナリティクス（過去30日）</h2>
            <AnalyticsPanel analytics={analytics} />
            {analytics?.daily_views?.length > 0 && (
              <div style={{ ...baseCard, marginTop: 20 }}>
                <h3 style={{ color: colors.text, fontSize: 16, margin: '0 0 16px' }}>日別再生数</h3>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 120 }}>
                  {analytics.daily_views.slice().reverse().map((d, i) => {
                    const maxViews = Math.max(...analytics.daily_views.map(x => x.views));
                    const h = maxViews > 0 ? (d.views / maxViews) * 100 : 0;
                    return (
                      <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                        <span style={{ color: colors.textMuted, fontSize: 10, marginBottom: 4 }}>{d.views}</span>
                        <div style={{ width: '100%', height: `${h}%`, minHeight: 2, background: colors.accent, borderRadius: '4px 4px 0 0' }} />
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {analytics?.top_clips?.length > 0 && (
              <div style={{ ...baseCard, marginTop: 20 }}>
                <h3 style={{ color: colors.text, fontSize: 16, margin: '0 0 12px' }}>人気クリップ TOP 10</h3>
                {analytics.top_clips.map((c, i) => (
                  <div key={c.clip_id} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: i < analytics.top_clips.length - 1 ? `1px solid ${colors.border}` : 'none' }}>
                    <span style={{ color: colors.textSecondary, fontSize: 13 }}>#{i + 1} {c.clip_id.slice(0, 8)}...</span>
                    <span style={{ color: colors.text, fontSize: 13, fontWeight: 600 }}>{c.plays} 再生</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Settings Tab */}
        {tab === 'settings' && !loading && (
          <div>
            <h2 style={{ color: colors.text, fontSize: 20, fontWeight: 600, margin: '0 0 20px' }}>設定</h2>
            <GtmTagPanel tagData={tagData} />
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
