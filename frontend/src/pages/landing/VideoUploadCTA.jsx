import { useState, useRef, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import UploadService from '../../base/services/uploadService';
import ChatRegisterModal from './ChatRegisterModal';

/* ─────────────────────────────────────────────
   Video Upload CTA — Real Upload + Instant Preview + Registration Flow
   Flow: Upload (real Azure Blob) → Instant Preview (thumbnail + estimated data) → Registration Popup
   AitherHub positioning: 商品紹介・ライブコマース特化
   
   Upload flow:
   1. generateUploadUrl (no auth) → get video_id, upload_url
   2. uploadToAzure (direct to Azure Blob, no auth)
   3. Generate instant preview (thumbnail from video + estimated metrics)
   4. Save pending video to localStorage
   5. After registration/login → upload-complete (auth required)
   ───────────────────────────────────────────── */

const GUEST_EMAIL_PREFIX = 'guest_';

// Estimate analysis metrics from file metadata
function estimateMetrics(file, videoDurationSec) {
  const fileSizeMB = file.size / (1024 * 1024);
  // videoDurationSec is actual duration in seconds from video metadata
  // Fallback: estimate from file size only if no duration available
  const durationSec = videoDurationSec || Math.round(fileSizeMB * 0.8 * 60); // fallback: ~0.8 min per MB
  const durationMin = durationSec / 60;
  const estimatedFrames = Math.round(durationSec * 0.5); // 0.5 fps analysis rate
  const estimatedScenes = Math.max(1, Math.round(durationMin * 1.5)); // ~1.5 scenes per minute
  const estimatedProducts = Math.max(1, Math.round(estimatedScenes * 0.4)); // ~40% of scenes have products
  return {
    durationSec: Math.round(durationSec), // actual seconds
    durationMin: durationMin,
    frames: estimatedFrames,
    scenes: estimatedScenes,
    products: estimatedProducts,
    fileSizeMB: fileSizeMB.toFixed(1),
  };
}

export default function VideoUploadCTA() {
  const navigate = useNavigate();
  const [step, setStep] = useState('idle'); // idle | uploading | preview | error
  const [progress, setProgress] = useState(0);
  const [videoLink, setVideoLink] = useState('');
  const [fileName, setFileName] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [thumbnailUrl, setThumbnailUrl] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [analysisProgress, setAnalysisProgress] = useState(0);
  const fileInputRef = useRef(null);
  const videoRef = useRef(null);
  const analysisTimerRef = useRef(null);

  // Generate thumbnail from video file
  const generateThumbnail = useCallback((file) => {
    return new Promise((resolve) => {
      const video = document.createElement('video');
      video.preload = 'metadata';
      video.muted = true;
      video.playsInline = true;
      
      const url = URL.createObjectURL(file);
      video.src = url;
      
      video.onloadeddata = () => {
        // Seek to 2 seconds or 10% of duration
        const seekTime = Math.min(2, video.duration * 0.1);
        video.currentTime = seekTime;
      };
      
      video.onseeked = () => {
        const canvas = document.createElement('canvas');
        canvas.width = Math.min(video.videoWidth, 640);
        canvas.height = Math.round(canvas.width * (video.videoHeight / video.videoWidth));
        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.8);
        URL.revokeObjectURL(url);
        resolve({ thumbnail: dataUrl, duration: video.duration });
      };
      
      video.onerror = () => {
        URL.revokeObjectURL(url);
        resolve({ thumbnail: null, duration: null });
      };

      // Timeout fallback
      setTimeout(() => {
        URL.revokeObjectURL(url);
        resolve({ thumbnail: null, duration: null });
      }, 5000);
    });
  }, []);

  // Simulate analysis progress animation
  useEffect(() => {
    if (step === 'preview') {
      setAnalysisProgress(0);
      let progress = 0;
      analysisTimerRef.current = setInterval(() => {
        progress += Math.random() * 3 + 0.5;
        if (progress > 85) progress = 85; // Cap at 85% - needs registration to complete
        setAnalysisProgress(Math.round(progress));
      }, 800);
    } else {
      if (analysisTimerRef.current) {
        clearInterval(analysisTimerRef.current);
        analysisTimerRef.current = null;
      }
    }
    return () => {
      if (analysisTimerRef.current) {
        clearInterval(analysisTimerRef.current);
      }
    };
  }, [step]);

  // Real upload: generate SAS URL → upload to Azure Blob → show instant preview
  const startRealUpload = useCallback(async (file) => {
    setFileName(file.name);
    setStep('uploading');
    setProgress(0);
    setErrorMsg('');
    setThumbnailUrl(null);
    setMetrics(null);

    // Start thumbnail generation in parallel with upload
    const thumbnailPromise = generateThumbnail(file);

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

      // Step 3: Get thumbnail and duration
      const { thumbnail, duration } = await thumbnailPromise;
      setThumbnailUrl(thumbnail);

      // Step 4: Calculate estimated metrics (duration is in seconds from video element)
      const estimatedMetrics = estimateMetrics(file, duration ? Math.round(duration) : null);
      setMetrics(estimatedMetrics);

      // Step 5: Save pending video info to localStorage for post-login completion
      const pendingVideo = {
        video_id,
        upload_id,
        filename: file.name,
        fileSize: file.size,
        guestEmail,
        uploadedAt: new Date().toISOString(),
      };
      localStorage.setItem('aitherhub_pending_video', JSON.stringify(pendingVideo));

      // Show instant preview state
      setTimeout(() => setStep('preview'), 300);
    } catch (error) {
      console.error('[VideoUploadCTA] Upload failed:', error);
      const msg = error?.userMessage || error?.message || 'アップロードに失敗しました。もう一度お試しください。';
      setErrorMsg(msg);
      setStep('error');
    }
  }, [generateThumbnail]);

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
    setThumbnailUrl(null);
    setMetrics(null);
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

          {/* ─── INSTANT PREVIEW STATE (NEW: 即時プレビュー) ─── */}
          {step === 'preview' && (
            <div style={{ padding: '10px 0' }}>
              {/* Two-column layout: thumbnail + metrics */}
              <div style={{
                display: 'flex',
                gap: '24px',
                alignItems: 'flex-start',
                marginBottom: '24px',
                flexWrap: 'wrap',
              }}>
                {/* Thumbnail */}
                <div style={{
                  flex: '0 0 auto',
                  width: '240px',
                  minWidth: '200px',
                  position: 'relative',
                  borderRadius: '12px',
                  overflow: 'hidden',
                  border: '1px solid rgba(139, 92, 246, 0.3)',
                  boxShadow: '0 4px 20px rgba(0,0,0,0.3)',
                }}>
                  {thumbnailUrl ? (
                    <img
                      src={thumbnailUrl}
                      alt="Video thumbnail"
                      style={{ width: '100%', height: 'auto', display: 'block' }}
                    />
                  ) : (
                    <div style={{
                      width: '100%',
                      height: '135px',
                      background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.2), rgba(139, 92, 246, 0.2))',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '32px',
                    }}>
                      🎬
                    </div>
                  )}
                  {/* Play icon overlay */}
                  <div style={{
                    position: 'absolute',
                    top: '50%',
                    left: '50%',
                    transform: 'translate(-50%, -50%)',
                    width: '40px',
                    height: '40px',
                    borderRadius: '50%',
                    background: 'rgba(0,0,0,0.6)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}>
                    <div style={{
                      width: 0,
                      height: 0,
                      borderStyle: 'solid',
                      borderWidth: '8px 0 8px 14px',
                      borderColor: 'transparent transparent transparent #fff',
                      marginLeft: '3px',
                    }} />
                  </div>
                  {/* File name badge */}
                  <div style={{
                    position: 'absolute',
                    bottom: '8px',
                    left: '8px',
                    right: '8px',
                    background: 'rgba(0,0,0,0.75)',
                    borderRadius: '6px',
                    padding: '4px 8px',
                  }}>
                    <p style={{ color: '#e2e8f0', fontSize: '10px', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {fileName}
                    </p>
                  </div>
                </div>

                {/* Estimated Metrics */}
                <div style={{ flex: 1, minWidth: '200px' }}>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    marginBottom: '12px',
                  }}>
                    <div style={{
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      background: '#10b981',
                      animation: 'pulse 2s infinite',
                    }} />
                    <span style={{ color: '#10b981', fontSize: '12px', fontWeight: '600' }}>
                      アップロード完了 ・ AI解析中...
                    </span>
                  </div>

                  {/* Metrics grid */}
                  {metrics && (
                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(2, 1fr)',
                      gap: '10px',
                      marginBottom: '16px',
                    }}>
                      <div style={{
                        background: 'rgba(99, 102, 241, 0.08)',
                        border: '1px solid rgba(99, 102, 241, 0.15)',
                        borderRadius: '10px',
                        padding: '12px',
                      }}>
                        <p style={{ color: '#8b5cf6', fontSize: '10px', fontWeight: '600', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>推定配信時間</p>
                        <p style={{ color: '#e2e8f0', fontSize: '18px', fontWeight: '700', margin: 0 }}>
                          {metrics.durationSec >= 60
                            ? <>{Math.round(metrics.durationMin)}<span style={{ fontSize: '12px', color: '#94a3b8', marginLeft: '2px' }}>分</span></>
                            : <>{metrics.durationSec}<span style={{ fontSize: '12px', color: '#94a3b8', marginLeft: '2px' }}>秒</span></>
                          }
                        </p>
                      </div>
                      <div style={{
                        background: 'rgba(16, 185, 129, 0.08)',
                        border: '1px solid rgba(16, 185, 129, 0.15)',
                        borderRadius: '10px',
                        padding: '12px',
                      }}>
                        <p style={{ color: '#10b981', fontSize: '10px', fontWeight: '600', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>解析フレーム数</p>
                        <p style={{ color: '#e2e8f0', fontSize: '18px', fontWeight: '700', margin: 0 }}>
                          {metrics.frames.toLocaleString()}<span style={{ fontSize: '12px', color: '#94a3b8', marginLeft: '2px' }}>枚</span>
                        </p>
                      </div>
                      <div style={{
                        background: 'rgba(245, 158, 11, 0.08)',
                        border: '1px solid rgba(245, 158, 11, 0.15)',
                        borderRadius: '10px',
                        padding: '12px',
                      }}>
                        <p style={{ color: '#f59e0b', fontSize: '10px', fontWeight: '600', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>推定シーン数</p>
                        <p style={{ color: '#e2e8f0', fontSize: '18px', fontWeight: '700', margin: 0 }}>
                          {metrics.scenes}<span style={{ fontSize: '12px', color: '#94a3b8', marginLeft: '2px' }}>シーン</span>
                        </p>
                      </div>
                      <div style={{
                        background: 'rgba(236, 72, 153, 0.08)',
                        border: '1px solid rgba(236, 72, 153, 0.15)',
                        borderRadius: '10px',
                        padding: '12px',
                      }}>
                        <p style={{ color: '#ec4899', fontSize: '10px', fontWeight: '600', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>商品検出（予測）</p>
                        <p style={{ color: '#e2e8f0', fontSize: '18px', fontWeight: '700', margin: 0 }}>
                          {metrics.products}<span style={{ fontSize: '12px', color: '#94a3b8', marginLeft: '2px' }}>商品</span>
                        </p>
                      </div>
                    </div>
                  )}

                  {/* Analysis progress bar */}
                  <div style={{ marginBottom: '8px' }}>
                    <div style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      marginBottom: '6px',
                    }}>
                      <span style={{ color: '#94a3b8', fontSize: '11px' }}>AI解析進捗</span>
                      <span style={{ color: '#a78bfa', fontSize: '11px', fontFamily: 'monospace' }}>{analysisProgress}%</span>
                    </div>
                    <div style={{
                      height: '4px',
                      background: 'rgba(255,255,255,0.08)',
                      borderRadius: '2px',
                      overflow: 'hidden',
                    }}>
                      <div style={{
                        width: `${analysisProgress}%`,
                        height: '100%',
                        background: 'linear-gradient(90deg, #6366f1, #8b5cf6, #a78bfa)',
                        borderRadius: '2px',
                        transition: 'width 0.8s ease',
                        position: 'relative',
                      }}>
                        <div style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          right: 0,
                          bottom: 0,
                          background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent)',
                          animation: 'shimmer 2s infinite',
                        }} />
                      </div>
                    </div>
                  </div>
                  <p style={{ color: '#64748b', fontSize: '10px', margin: 0 }}>
                    ファイルサイズ: {metrics?.fileSizeMB || '—'} MB
                  </p>
                </div>
              </div>

              {/* Analysis steps animation */}
              <div style={{
                background: 'rgba(0,0,0,0.25)',
                borderRadius: '12px',
                padding: '16px 20px',
                marginBottom: '24px',
                border: '1px solid rgba(255,255,255,0.05)',
              }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {[
                    { label: 'フレーム抽出', done: analysisProgress > 15 },
                    { label: '商品検出 AI', done: analysisProgress > 35 },
                    { label: 'シーン分割', done: analysisProgress > 55 },
                    { label: '売上ポイント解析', done: analysisProgress > 75 },
                    { label: 'レポート生成', done: false },
                  ].map((item, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <div style={{
                        width: '18px',
                        height: '18px',
                        borderRadius: '50%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '10px',
                        ...(item.done
                          ? { background: 'rgba(16, 185, 129, 0.2)', color: '#10b981' }
                          : analysisProgress > (i * 20)
                            ? { border: '2px solid #8b5cf6', borderTopColor: 'transparent', animation: 'spin 1s linear infinite' }
                            : { background: 'rgba(255,255,255,0.05)', color: '#475569' }
                        ),
                      }}>
                        {item.done ? '✓' : ''}
                      </div>
                      <span style={{
                        color: item.done ? '#10b981' : (analysisProgress > (i * 20) ? '#e2e8f0' : '#475569'),
                        fontSize: '13px',
                        fontWeight: item.done ? '500' : '400',
                        transition: 'color 0.3s',
                      }}>
                        {item.label}
                      </span>
                      {!item.done && analysisProgress > (i * 20) && analysisProgress <= ((i + 1) * 20) && (
                        <span style={{ color: '#8b5cf6', fontSize: '11px', marginLeft: 'auto' }}>処理中...</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* CTA: Register to see full results */}
              <div style={{
                background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.1), rgba(139, 92, 246, 0.1))',
                border: '1px solid rgba(139, 92, 246, 0.25)',
                borderRadius: '16px',
                padding: '20px 24px',
                textAlign: 'center',
              }}>
                <p style={{ color: '#e2e8f0', fontSize: '15px', fontWeight: '600', marginBottom: '8px' }}>
                  🔒 完全な解析レポートを見るには
                </p>
                <p style={{ color: '#94a3b8', fontSize: '13px', marginBottom: '16px' }}>
                  無料アカウントを作成すると、商品検出・売上ポイント・改善提案の全データにアクセスできます
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
                  無料登録して完全な解析を見る
                </button>
                <p style={{ color: '#475569', fontSize: '11px', marginTop: '12px' }}>
                  クレジットカード不要 ・ 30秒で完了 ・ Google / TikTokアカウントでも登録可能
                </p>
              </div>
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
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        @keyframes shimmer {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(100%); }
        }
      `}</style>
    </div>
  );
}
