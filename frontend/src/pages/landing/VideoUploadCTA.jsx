import { useState, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import UploadService from '../../base/services/uploadService';
import ChatRegisterModal from './ChatRegisterModal';

/* ─────────────────────────────────────────────
   Video Upload CTA — Real Upload + Registration Flow
   Flow: Upload (real Azure Blob) → Progress → Registration Popup
   AitherHub positioning: 商品紹介・ライブコマース特化
   
   Upload flow:
   1. generateUploadUrl (no auth) → get video_id, upload_url
   2. uploadToAzure (direct to Azure Blob, no auth)
   3. Save pending video to localStorage
   4. After registration/login → upload-complete (auth required)
   ───────────────────────────────────────────── */

const GUEST_EMAIL_PREFIX = 'guest_';

export default function VideoUploadCTA() {
  const navigate = useNavigate();
  const [step, setStep] = useState('idle'); // idle | uploading | confirm | error
  const [progress, setProgress] = useState(0);
  const [videoLink, setVideoLink] = useState('');
  const [fileName, setFileName] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const fileInputRef = useRef(null);

  // Real upload: generate SAS URL → upload to Azure Blob → save video_id for post-login
  const startRealUpload = useCallback(async (file) => {
    setFileName(file.name);
    setStep('uploading');
    setProgress(0);
    setErrorMsg('');

    try {
      // Generate a temporary guest email for the upload
      const guestEmail = `${GUEST_EMAIL_PREFIX}${Date.now()}@aitherhub.temp`;

      // Step 1: Get upload URL (no auth required)
      const { video_id, upload_id, upload_url } = await UploadService.generateUploadUrl(guestEmail, file.name);

      // Step 2: Upload to Azure Blob (no auth required, direct to Azure)
      await UploadService.uploadToAzure(file, upload_url, upload_id, (pct) => {
        setProgress(Math.min(pct, 99));
      });

      setProgress(100);

      // Step 3: Save pending video info to localStorage for post-login completion
      const pendingVideo = {
        video_id,
        upload_id,
        filename: file.name,
        fileSize: file.size,
        guestEmail,
        uploadedAt: new Date().toISOString(),
      };
      localStorage.setItem('aitherhub_pending_video', JSON.stringify(pendingVideo));

      // Show confirm state after a brief delay
      setTimeout(() => setStep('confirm'), 500);
    } catch (error) {
      console.error('[VideoUploadCTA] Upload failed:', error);
      const msg = error?.userMessage || error?.message || 'アップロードに失敗しました。もう一度お試しください。';
      setErrorMsg(msg);
      setStep('error');
    }
  }, []);

  // Handle file drop
  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer?.files?.[0];
    if (file && file.type.startsWith('video/')) {
      startRealUpload(file);
    }
  }, [startRealUpload]);

  // Handle file input
  const handleFileSelect = useCallback((e) => {
    const file = e.target.files?.[0];
    if (file) {
      startRealUpload(file);
    }
  }, [startRealUpload]);

  // Handle link submit — for URL-based, redirect to register directly
  const handleLinkSubmit = useCallback(() => {
    if (videoLink.trim()) {
      localStorage.setItem('aitherhub_pending_video_url', videoLink.trim());
      navigate('/register');
    }
  }, [videoLink, navigate]);

  // Chat register modal state
  const [showChatRegister, setShowChatRegister] = useState(false);

  // Open chat register modal instead of navigating away
  const goRegister = () => {
    setShowChatRegister(true);
  };

  // Handle successful registration from chat modal
  const handleChatRegisterSuccess = () => {
    setShowChatRegister(false);
    navigate('/');
  };

  // Get pending video data from localStorage
  const getPendingVideo = () => {
    try {
      const raw = localStorage.getItem('aitherhub_pending_video');
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  };

  // Retry upload
  const handleRetry = () => {
    setStep('idle');
    setProgress(0);
    setErrorMsg('');
    setFileName('');
  };

  return (
    <div id="video-upload-cta" style={{ position: 'relative' }}>
      {/* Main Card */}
      <div style={{
        maxWidth: '800px',
        margin: '0 auto',
        borderRadius: '24px',
        background: 'linear-gradient(160deg, rgba(30, 20, 50, 0.95) 0%, rgba(20, 15, 40, 0.98) 100%)',
        border: '1px solid rgba(139, 92, 246, 0.2)',
        padding: '48px 40px',
        position: 'relative',
        overflow: 'hidden',
        boxShadow: '0 20px 80px rgba(99, 102, 241, 0.15), inset 0 1px 0 rgba(255,255,255,0.05)',
      }}>
        {/* Background glow */}
        <div style={{
          position: 'absolute',
          top: '-50%',
          left: '-20%',
          width: '140%',
          height: '200%',
          background: 'radial-gradient(ellipse at center, rgba(139, 92, 246, 0.06) 0%, transparent 70%)',
          pointerEvents: 'none',
        }} />

        {/* Content */}
        <div style={{ position: 'relative', zIndex: 1 }}>
          {/* Title */}
          <h2 style={{
            textAlign: 'center',
            fontSize: 'clamp(22px, 3.5vw, 32px)',
            fontWeight: '700',
            color: '#fff',
            marginBottom: '8px',
            letterSpacing: '-0.02em',
          }}>
            あなたの配信動画を、AIで解析してみよう
          </h2>
          <p style={{
            textAlign: 'center',
            color: '#94a3b8',
            fontSize: '14px',
            marginBottom: '32px',
          }}>
            動画をアップロードするだけ。AIが売上ポイントを自動で見つけます。
          </p>

          {/* ─── IDLE STATE ─── */}
          {step === 'idle' && (
            <>
              {/* Drop zone */}
              <div
                onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                style={{
                  border: `2px dashed ${isDragOver ? '#8b5cf6' : 'rgba(139, 92, 246, 0.3)'}`,
                  borderRadius: '16px',
                  padding: '40px 24px',
                  textAlign: 'center',
                  cursor: 'pointer',
                  background: isDragOver ? 'rgba(139, 92, 246, 0.08)' : 'rgba(0,0,0,0.2)',
                  transition: 'all 0.3s ease',
                  marginBottom: '20px',
                }}
              >
                <div style={{ fontSize: '40px', marginBottom: '12px' }}>📹</div>
                <p style={{ color: '#e2e8f0', fontSize: '15px', fontWeight: '600', marginBottom: '6px' }}>
                  動画ファイルをドラッグ＆ドロップ
                </p>
                <p style={{ color: '#64748b', fontSize: '13px' }}>
                  または<span style={{ color: '#a78bfa', textDecoration: 'underline', marginLeft: '4px' }}>クリックしてファイルを選択</span>
                </p>
                <p style={{ color: '#475569', fontSize: '11px', marginTop: '12px' }}>
                  MP4, MOV, WebM対応 ・ 最大2GB
                </p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="video/*"
                  onChange={handleFileSelect}
                  style={{ display: 'none' }}
                />
              </div>

              {/* OR divider */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '16px',
                margin: '20px 0',
              }}>
                <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.08)' }} />
                <span style={{ color: '#64748b', fontSize: '12px' }}>または</span>
                <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.08)' }} />
              </div>

              {/* Link input (OpusClip style) */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0',
                background: 'rgba(255,255,255,0.06)',
                borderRadius: '50px',
                border: '1px solid rgba(255,255,255,0.1)',
                padding: '4px 4px 4px 20px',
                maxWidth: '560px',
                margin: '0 auto',
              }}>
                <span style={{ fontSize: '16px', marginRight: '8px', opacity: 0.6 }}>🔗</span>
                <input
                  type="text"
                  placeholder="TikTok / YouTube / 動画URLを貼り付け"
                  value={videoLink}
                  onChange={(e) => setVideoLink(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleLinkSubmit()}
                  style={{
                    flex: 1,
                    background: 'transparent',
                    border: 'none',
                    outline: 'none',
                    color: '#e2e8f0',
                    fontSize: '14px',
                    padding: '12px 0',
                  }}
                />
                <button
                  onClick={handleLinkSubmit}
                  disabled={!videoLink.trim()}
                  style={{
                    background: videoLink.trim() ? 'linear-gradient(135deg, #6366f1, #8b5cf6)' : 'rgba(255,255,255,0.1)',
                    border: 'none',
                    color: '#fff',
                    padding: '12px 24px',
                    borderRadius: '50px',
                    fontSize: '14px',
                    fontWeight: '600',
                    cursor: videoLink.trim() ? 'pointer' : 'default',
                    transition: 'all 0.3s',
                    whiteSpace: 'nowrap',
                  }}
                >
                  AI解析を開始
                </button>
              </div>
            </>
          )}

          {/* ─── UPLOADING STATE (REAL) ─── */}
          {step === 'uploading' && (
            <div style={{ textAlign: 'center', padding: '20px 0' }}>
              <div style={{
                width: '64px',
                height: '64px',
                margin: '0 auto 20px',
                borderRadius: '50%',
                border: '3px solid rgba(139, 92, 246, 0.2)',
                borderTopColor: '#8b5cf6',
                animation: 'spin 1s linear infinite',
              }} />
              <p style={{ color: '#e2e8f0', fontSize: '15px', fontWeight: '600', marginBottom: '8px' }}>
                アップロード中...
              </p>
              <p style={{ color: '#64748b', fontSize: '13px', marginBottom: '20px' }}>
                {fileName.length > 40 ? fileName.slice(0, 40) + '...' : fileName}
              </p>
              {/* Progress bar */}
              <div style={{
                maxWidth: '400px',
                margin: '0 auto',
                height: '6px',
                background: 'rgba(255,255,255,0.08)',
                borderRadius: '3px',
                overflow: 'hidden',
              }}>
                <div style={{
                  width: `${progress}%`,
                  height: '100%',
                  background: 'linear-gradient(90deg, #6366f1, #8b5cf6, #a78bfa)',
                  borderRadius: '3px',
                  transition: 'width 0.3s ease',
                }} />
              </div>
              <p style={{ color: '#8b5cf6', fontSize: '12px', marginTop: '8px', fontFamily: 'monospace' }}>
                {Math.round(progress)}%
              </p>
            </div>
          )}

          {/* ─── ERROR STATE ─── */}
          {step === 'error' && (
            <div style={{ textAlign: 'center', padding: '20px 0' }}>
              <div style={{
                width: '56px',
                height: '56px',
                margin: '0 auto 20px',
                borderRadius: '50%',
                background: 'rgba(239, 68, 68, 0.15)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '24px',
                color: '#ef4444',
                fontWeight: '700',
              }}>
                ✕
              </div>
              <p style={{ color: '#ef4444', fontSize: '14px', fontWeight: '600', marginBottom: '8px' }}>
                アップロードに失敗しました
              </p>
              <p style={{ color: '#94a3b8', fontSize: '13px', marginBottom: '20px', maxWidth: '400px', margin: '0 auto 20px' }}>
                {errorMsg}
              </p>
              <button
                onClick={handleRetry}
                style={{
                  background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                  border: 'none',
                  color: '#fff',
                  padding: '12px 32px',
                  borderRadius: '10px',
                  fontSize: '14px',
                  fontWeight: '600',
                  cursor: 'pointer',
                }}
              >
                もう一度試す
              </button>
            </div>
          )}

          {/* ─── CONFIRM STATE (商品特化ポジショニング) ─── */}
          {step === 'confirm' && (
            <div style={{ textAlign: 'center', padding: '20px 0' }}>
              {/* Success check */}
              <div style={{
                width: '56px',
                height: '56px',
                margin: '0 auto 20px',
                borderRadius: '50%',
                background: 'linear-gradient(135deg, #10b981, #059669)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '24px',
                color: '#fff',
                animation: 'popIn 0.4s ease',
              }}>
                ✓
              </div>
              <p style={{ color: '#10b981', fontSize: '14px', fontWeight: '600', marginBottom: '4px' }}>
                アップロード完了！
              </p>
              <p style={{ color: '#e2e8f0', fontSize: '18px', fontWeight: '700', marginBottom: '16px' }}>
                AI解析を開始する準備ができました
              </p>

              {/* 商品特化メッセージ */}
              <div style={{
                background: 'rgba(99, 102, 241, 0.08)',
                border: '1px solid rgba(99, 102, 241, 0.2)',
                borderRadius: '12px',
                padding: '16px 20px',
                maxWidth: '480px',
                margin: '0 auto 24px',
                textAlign: 'left',
              }}>
                <p style={{ color: '#a78bfa', fontSize: '12px', fontWeight: '600', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span>💡</span> AitherHubの特徴
                </p>
                <p style={{ color: '#cbd5e1', fontSize: '13px', lineHeight: '1.7' }}>
                  AitherHubは<strong style={{ color: '#e2e8f0' }}>商品紹介・ライブコマース動画に特化</strong>したAI解析ツールです。
                  売れた瞬間、商品の見せ方、購買を促すトーク——販売に直結するポイントをAIが自動検出します。
                </p>
              </div>

              {/* Registration CTA */}
              <p style={{ color: '#94a3b8', fontSize: '13px', marginBottom: '16px' }}>
                AI解析を開始するには、無料アカウントの作成が必要です（30秒で完了）
              </p>
              <button
                onClick={goRegister}
                style={{
                  background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                  border: 'none',
                  color: '#fff',
                  padding: '14px 40px',
                  borderRadius: '12px',
                  fontSize: '16px',
                  fontWeight: '700',
                  cursor: 'pointer',
                  boxShadow: '0 8px 40px rgba(99, 102, 241, 0.4)',
                  transition: 'all 0.3s',
                }}
                onMouseEnter={e => { e.target.style.transform = 'translateY(-2px)'; e.target.style.boxShadow = '0 12px 50px rgba(99, 102, 241, 0.5)'; }}
                onMouseLeave={e => { e.target.style.transform = 'translateY(0)'; e.target.style.boxShadow = '0 8px 40px rgba(99, 102, 241, 0.4)'; }}
              >
                無料アカウントを作成してAI解析を開始
              </button>
              <p style={{ color: '#475569', fontSize: '11px', marginTop: '12px' }}>
                クレジットカード不要 ・ Google / TikTokアカウントでも登録可能
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Chat Register Modal */}
      <ChatRegisterModal
        isOpen={showChatRegister}
        onClose={() => setShowChatRegister(false)}
        onSuccess={handleChatRegisterSuccess}
        pendingVideo={getPendingVideo()}
      />

      {/* CSS */}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        @keyframes popIn {
          from { transform: scale(0.5); opacity: 0; }
          to { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
