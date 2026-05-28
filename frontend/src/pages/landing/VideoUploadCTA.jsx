import { useState, useRef, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import UploadService from '../../base/services/uploadService';
import { API_CONFIG } from '../../base/api/config';
import ChatRegisterModal from './ChatRegisterModal';

/* ─────────────────────────────────────────────
   Video Upload CTA — Real Upload + Instant Preview + Real-time Polling
   
   P0: Instant metadata from browser (duration, resolution, thumbnails, bitrate, FPS)
   P1: Real-time polling of backend analysis status (step-by-step progress)
   P2: Early product detection preview from processing_logs
   P3: Completion celebration (confetti + highlight diffs)
   
   Upload flow:
   1. generateUploadUrl (no auth) → get video_id, upload_url
   2. uploadToAzure (direct to Azure Blob, no auth)
   3. Instant metadata extraction from browser <video> element
   4. Start polling GET /api/v1/videos/{video_id}/status/public
   5. Show real-time progress with step-by-step updates
   6. Save pending video to localStorage
   7. After registration/login → upload-complete (auth required)
   ───────────────────────────────────────────── */

const GUEST_EMAIL_PREFIX = 'guest_';
const POLL_INTERVAL_MS = 3000; // Poll every 3 seconds

// ─── P0: Extract full metadata from browser <video> element ───
function extractVideoMetadata(file) {
  return new Promise((resolve) => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.muted = true;
    video.playsInline = true;
    const url = URL.createObjectURL(file);
    video.src = url;

    let resolved = false;
    const finish = (data) => {
      if (resolved) return;
      resolved = true;
      URL.revokeObjectURL(url);
      resolve(data);
    };

    video.onloadedmetadata = () => {
      const duration = video.duration || 0;
      const width = video.videoWidth || 0;
      const height = video.videoHeight || 0;
      const fileSizeMB = file.size / (1024 * 1024);
      const bitrateKbps = duration > 0 ? Math.round((file.size * 8) / (duration * 1000)) : 0;

      // Generate 4 thumbnails at 0%, 25%, 50%, 75%
      const thumbnailTimes = [0.01, 0.25, 0.5, 0.75].map(pct => pct * duration);
      const thumbnails = [];
      let thumbIdx = 0;

      const captureNext = () => {
        if (thumbIdx >= thumbnailTimes.length) {
          // Estimate FPS using requestVideoFrameCallback if available
          let estimatedFps = 30; // default
          if (bitrateKbps > 0 && width > 0) {
            // Rough estimate: higher bitrate per pixel = higher fps
            const bitsPerPixelPerSec = (bitrateKbps * 1000) / (width * height);
            if (bitsPerPixelPerSec > 0.15) estimatedFps = 60;
            else if (bitsPerPixelPerSec > 0.05) estimatedFps = 30;
            else estimatedFps = 24;
          }

          finish({
            duration,
            width,
            height,
            fileSizeMB: fileSizeMB.toFixed(1),
            bitrateKbps,
            bitrateMbps: (bitrateKbps / 1000).toFixed(1),
            estimatedFps,
            thumbnails,
            // Estimated analysis metrics
            estimatedFrames: Math.round(duration * 0.5),
            estimatedScenes: Math.max(1, Math.round((duration / 60) * 1.5)),
            estimatedProducts: Math.max(1, Math.round(Math.max(1, Math.round((duration / 60) * 1.5)) * 0.4)),
            estimatedProcessingMin: Math.max(1, Math.round(duration / 120)), // ~2min real per 1min video
          });
          return;
        }

        video.currentTime = thumbnailTimes[thumbIdx];
      };

      video.onseeked = () => {
        try {
          const canvas = document.createElement('canvas');
          const scale = Math.min(1, 400 / Math.max(width, 1));
          canvas.width = Math.round(width * scale);
          canvas.height = Math.round(height * scale);
          const ctx = canvas.getContext('2d');
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          thumbnails.push(canvas.toDataURL('image/jpeg', 0.7));
        } catch {
          thumbnails.push(null);
        }
        thumbIdx++;
        captureNext();
      };

      captureNext();
    };

    video.onerror = () => {
      finish({
        duration: 0, width: 0, height: 0,
        fileSizeMB: (file.size / (1024 * 1024)).toFixed(1),
        bitrateKbps: 0, bitrateMbps: '0', estimatedFps: 30,
        thumbnails: [],
        estimatedFrames: 0, estimatedScenes: 0, estimatedProducts: 0,
        estimatedProcessingMin: 0,
      });
    };

    // Timeout fallback
    setTimeout(() => finish({
      duration: 0, width: 0, height: 0,
      fileSizeMB: (file.size / (1024 * 1024)).toFixed(1),
      bitrateKbps: 0, bitrateMbps: '0', estimatedFps: 30,
      thumbnails: [],
      estimatedFrames: 0, estimatedScenes: 0, estimatedProducts: 0,
      estimatedProcessingMin: 0,
    }), 10000);
  });
}

// Format duration nicely
function formatDuration(sec) {
  if (!sec || sec <= 0) return '—';
  if (sec < 60) return `${Math.round(sec)}秒`;
  if (sec < 3600) {
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    return s > 0 ? `${m}分${s}秒` : `${m}分`;
  }
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return m > 0 ? `${h}時間${m}分` : `${h}時間`;
}

// ─── Analysis step definitions ───
const ANALYSIS_STEPS = [
  { key: 'frames', label: 'フレーム抽出', statusMatch: ['STEP_0_EXTRACT_FRAMES'], progressThreshold: 10 },
  { key: 'product', label: '商品検出 AI', statusMatch: ['STEP_12_5_PRODUCT_DETECTION'], progressThreshold: 30 },
  { key: 'scenes', label: 'シーン分割', statusMatch: ['STEP_1_PHASE_SPLIT', 'STEP_2_PHASE_REFINE'], progressThreshold: 50 },
  { key: 'sales', label: '売上ポイント解析', statusMatch: ['STEP_3_INSIGHT', 'STEP_4_SALES_ANALYSIS'], progressThreshold: 70 },
  { key: 'report', label: 'レポート生成', statusMatch: ['STEP_5_REPORT', 'DONE'], progressThreshold: 90 },
];

export default function VideoUploadCTA() {
  const navigate = useNavigate();
  const [step, setStep] = useState('idle'); // idle | uploading | preview | done | error
  const [progress, setProgress] = useState(0);
  const [videoLink, setVideoLink] = useState('');
  const [fileName, setFileName] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  // P0: Instant metadata
  const [metadata, setMetadata] = useState(null);
  const [activeThumbnail, setActiveThumbnail] = useState(0);

  // P1: Real-time polling
  const [videoId, setVideoId] = useState(null);
  const [analysisStatus, setAnalysisStatus] = useState(null);
  const [analysisProgress, setAnalysisProgress] = useState(0);
  const pollRef = useRef(null);

  // P3: Confetti
  const [showConfetti, setShowConfetti] = useState(false);
  const [prevMetrics, setPrevMetrics] = useState(null);

  const fileInputRef = useRef(null);

  // Chat register modal
  const [showChatRegister, setShowChatRegister] = useState(false);

  // ─── P1: Polling logic ───
  useEffect(() => {
    if (!videoId || step === 'idle' || step === 'uploading' || step === 'error') {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }

    const pollStatus = async () => {
      try {
        const baseUrl = API_CONFIG.BASE_URL || '';
        const res = await fetch(`${baseUrl}/api/v1/videos/${videoId}/status/public`);
        if (!res.ok) return;
        const data = await res.json();
        setAnalysisStatus(data);

        // Update progress from backend
        const backendProgress = data.progress || 0;
        setAnalysisProgress(prev => Math.max(prev, backendProgress));

        // P3: Check if done
        if (data.is_done) {
          setAnalysisProgress(100);
          setStep('done');
          // Save real metrics for diff highlight
          setPrevMetrics(metadata ? {
            scenes: metadata.estimatedScenes,
            products: metadata.estimatedProducts,
          } : null);
          // Confetti!
          setShowConfetti(true);
          setTimeout(() => setShowConfetti(false), 4000);
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        }

        if (data.is_error) {
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        }
      } catch (err) {
        console.warn('[VideoUploadCTA] Poll error:', err);
      }
    };

    // Initial poll
    pollStatus();
    pollRef.current = setInterval(pollStatus, POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [videoId, step, metadata]);

  // Thumbnail auto-rotate
  useEffect(() => {
    if (!metadata?.thumbnails?.length || metadata.thumbnails.length <= 1) return;
    const timer = setInterval(() => {
      setActiveThumbnail(prev => (prev + 1) % metadata.thumbnails.length);
    }, 3000);
    return () => clearInterval(timer);
  }, [metadata]);

  // ─── Upload handler ───
  const startRealUpload = useCallback(async (file) => {
    setFileName(file.name);
    setStep('uploading');
    setProgress(0);
    setErrorMsg('');
    setMetadata(null);
    setAnalysisStatus(null);
    setAnalysisProgress(0);
    setVideoId(null);
    setShowConfetti(false);
    setPrevMetrics(null);

    // P0: Start metadata extraction in parallel
    const metadataPromise = extractVideoMetadata(file);

    try {
      const guestEmail = `${GUEST_EMAIL_PREFIX}${Date.now()}@aitherhub.temp`;

      // Step 1: Get upload URL
      const { video_id, upload_id, upload_url } = await UploadService.generateUploadUrl(guestEmail, file.name);
      setVideoId(video_id);

      // Step 2: Upload to Azure Blob
      await UploadService.uploadToAzure(file, upload_url, upload_id, (pct) => {
        setProgress(Math.min(pct, 99));
      });
      setProgress(100);

      // P0: Get instant metadata
      const meta = await metadataPromise;
      setMetadata(meta);

      // Save pending video
      const pendingVideo = {
        video_id,
        upload_id,
        filename: file.name,
        fileSize: file.size,
        guestEmail,
        uploadedAt: new Date().toISOString(),
      };
      localStorage.setItem('aitherhub_pending_video', JSON.stringify(pendingVideo));

      // Transition to preview (polling starts automatically via useEffect)
      setTimeout(() => setStep('preview'), 300);
    } catch (error) {
      console.error('[VideoUploadCTA] Upload failed:', error);
      const msg = error?.userMessage || error?.message || 'アップロードに失敗しました。もう一度お試しください。';
      setErrorMsg(msg);
      setStep('error');
    }
  }, []);

  // ─── Helpers ───
  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer?.files?.[0];
    if (file && file.type.startsWith('video/')) startRealUpload(file);
  }, [startRealUpload]);

  const handleFileSelect = useCallback((e) => {
    const file = e.target.files?.[0];
    if (file) startRealUpload(file);
  }, [startRealUpload]);

  const handleLinkSubmit = useCallback(() => {
    if (videoLink.trim()) {
      localStorage.setItem('aitherhub_pending_video_url', videoLink.trim());
      navigate('/register');
    }
  }, [videoLink, navigate]);

  const goRegister = () => setShowChatRegister(true);
  const handleChatRegisterSuccess = () => { setShowChatRegister(false); navigate('/'); };
  const getPendingVideo = () => {
    try { const raw = localStorage.getItem('aitherhub_pending_video'); return raw ? JSON.parse(raw) : null; }
    catch { return null; }
  };
  const handleRetry = () => {
    setStep('idle'); setProgress(0); setErrorMsg(''); setFileName('');
    setMetadata(null); setAnalysisStatus(null); setAnalysisProgress(0);
    setVideoId(null); setShowConfetti(false);
  };

  // ─── Determine step states for analysis steps ───
  const getStepState = (stepDef, idx) => {
    if (!analysisStatus) {
      // Fake progress based on analysisProgress
      if (analysisProgress > stepDef.progressThreshold + 15) return 'done';
      if (analysisProgress > stepDef.progressThreshold) return 'active';
      return 'pending';
    }
    const currentStatus = analysisStatus.status || '';
    const allStatuses = ANALYSIS_STEPS.flatMap(s => s.statusMatch);
    const currentIdx = allStatuses.findIndex(s => currentStatus.startsWith(s));
    const stepStartIdx = ANALYSIS_STEPS.slice(0, idx).flatMap(s => s.statusMatch).length;
    const stepEndIdx = stepStartIdx + stepDef.statusMatch.length;

    if (currentStatus === 'DONE') return 'done';
    if (currentIdx >= stepEndIdx) return 'done';
    if (currentIdx >= stepStartIdx) return 'active';
    return 'pending';
  };

  // Get real metrics (from backend) or estimated (from P0)
  const getRealMetric = (key) => {
    if (analysisStatus) {
      if (key === 'scenes' && analysisStatus.phase_count > 0) return analysisStatus.phase_count;
      if (key === 'products' && analysisStatus.top_products?.length > 0) return analysisStatus.top_products.length;
      if (key === 'duration' && analysisStatus.duration > 0) return analysisStatus.duration;
    }
    return null;
  };

  // Check if a metric was updated from estimate
  const isMetricUpdated = (key) => {
    if (!prevMetrics || !analysisStatus) return false;
    const real = getRealMetric(key);
    if (real === null) return false;
    if (key === 'scenes') return real !== prevMetrics.scenes;
    if (key === 'products') return real !== prevMetrics.products;
    return false;
  };

  return (
    <div id="video-upload-cta" style={{ position: 'relative' }}>
      {/* P3: Confetti overlay */}
      {showConfetti && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          pointerEvents: 'none', zIndex: 9999, overflow: 'hidden',
        }}>
          {Array.from({ length: 50 }).map((_, i) => (
            <div key={i} style={{
              position: 'absolute',
              left: `${Math.random() * 100}%`,
              top: '-10px',
              width: `${6 + Math.random() * 8}px`,
              height: `${6 + Math.random() * 8}px`,
              background: ['#6366f1', '#8b5cf6', '#10b981', '#f59e0b', '#ec4899', '#06b6d4'][i % 6],
              borderRadius: Math.random() > 0.5 ? '50%' : '2px',
              animation: `confettiFall ${2 + Math.random() * 2}s ease-in forwards`,
              animationDelay: `${Math.random() * 0.5}s`,
              transform: `rotate(${Math.random() * 360}deg)`,
            }} />
          ))}
        </div>
      )}

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
          position: 'absolute', top: '-50%', left: '-20%', width: '140%', height: '200%',
          background: 'radial-gradient(ellipse at center, rgba(139, 92, 246, 0.06) 0%, transparent 70%)',
          pointerEvents: 'none',
        }} />

        <div style={{ position: 'relative', zIndex: 1 }}>
          {/* Title */}
          <h2 style={{
            textAlign: 'center', fontSize: 'clamp(22px, 3.5vw, 32px)', fontWeight: '700',
            color: '#fff', marginBottom: '8px', letterSpacing: '-0.02em',
          }}>
            あなたの配信動画を、AIで解析してみよう
          </h2>
          <p style={{ textAlign: 'center', color: '#94a3b8', fontSize: '14px', marginBottom: '32px' }}>
            動画をアップロードするだけ。AIが売上ポイントを自動で見つけます。
          </p>

          {/* ─── IDLE STATE ─── */}
          {step === 'idle' && (
            <>
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
                  transition: 'all 0.3s',
                  background: isDragOver ? 'rgba(139, 92, 246, 0.08)' : 'rgba(0,0,0,0.2)',
                  marginBottom: '20px',
                }}
              >
                <div style={{ fontSize: '40px', marginBottom: '12px' }}>📹</div>
                <p style={{ color: '#e2e8f0', fontSize: '15px', fontWeight: '600', marginBottom: '4px' }}>
                  動画ファイルをドラッグ＆ドロップ
                </p>
                <p style={{ color: '#64748b', fontSize: '12px', margin: 0 }}>
                  または クリックしてファイルを選択（MP4, MOV, AVI）
                </p>
              </div>
              <input ref={fileInputRef} type="file" accept="video/*" onChange={handleFileSelect} style={{ display: 'none' }} />

              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
                <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.1)' }} />
                <span style={{ color: '#475569', fontSize: '12px' }}>または</span>
                <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.1)' }} />
              </div>

              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  placeholder="TikTok / YouTube Live URLを貼り付け"
                  value={videoLink}
                  onChange={(e) => setVideoLink(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleLinkSubmit()}
                  style={{
                    flex: 1, background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(139, 92, 246, 0.2)',
                    borderRadius: '12px', padding: '12px 16px', color: '#e2e8f0', fontSize: '13px', outline: 'none',
                  }}
                />
                <button
                  onClick={handleLinkSubmit}
                  disabled={!videoLink.trim()}
                  style={{
                    background: videoLink.trim() ? 'linear-gradient(135deg, #6366f1, #8b5cf6)' : 'rgba(99, 102, 241, 0.2)',
                    border: 'none', color: '#fff', padding: '12px 20px', borderRadius: '12px',
                    fontSize: '13px', fontWeight: '600', cursor: videoLink.trim() ? 'pointer' : 'default',
                    opacity: videoLink.trim() ? 1 : 0.5,
                  }}
                >
                  解析開始
                </button>
              </div>
            </>
          )}

          {/* ─── UPLOADING STATE ─── */}
          {step === 'uploading' && (
            <div style={{ textAlign: 'center', padding: '20px 0' }}>
              <div style={{
                width: '64px', height: '64px', margin: '0 auto 16px',
                borderRadius: '50%', border: '3px solid rgba(139, 92, 246, 0.2)',
                borderTopColor: '#8b5cf6', animation: 'spin 1s linear infinite',
              }} />
              <p style={{ color: '#e2e8f0', fontSize: '16px', fontWeight: '600', marginBottom: '4px' }}>
                アップロード中...
              </p>
              <p style={{ color: '#94a3b8', fontSize: '13px', marginBottom: '16px' }}>{fileName}</p>
              <div style={{ maxWidth: '300px', margin: '0 auto' }}>
                <div style={{ height: '6px', background: 'rgba(255,255,255,0.08)', borderRadius: '3px', overflow: 'hidden' }}>
                  <div style={{
                    width: `${progress}%`, height: '100%',
                    background: 'linear-gradient(90deg, #6366f1, #8b5cf6)',
                    borderRadius: '3px', transition: 'width 0.3s',
                  }} />
                </div>
                <p style={{ color: '#a78bfa', fontSize: '12px', marginTop: '8px', fontFamily: 'monospace' }}>{progress}%</p>
              </div>
            </div>
          )}

          {/* ─── ERROR STATE ─── */}
          {step === 'error' && (
            <div style={{ textAlign: 'center', padding: '20px 0' }}>
              <div style={{ fontSize: '40px', marginBottom: '12px' }}>⚠️</div>
              <p style={{ color: '#f87171', fontSize: '15px', fontWeight: '600', marginBottom: '8px' }}>
                アップロードに失敗しました
              </p>
              <p style={{ color: '#94a3b8', fontSize: '13px', marginBottom: '20px' }}>{errorMsg}</p>
              <button onClick={handleRetry} style={{
                background: 'rgba(139, 92, 246, 0.15)', border: '1px solid rgba(139, 92, 246, 0.3)',
                color: '#a78bfa', padding: '10px 24px', borderRadius: '10px',
                fontSize: '14px', fontWeight: '600', cursor: 'pointer',
              }}>
                もう一度試す
              </button>
            </div>
          )}

          {/* ─── PREVIEW / DONE STATE ─── */}
          {(step === 'preview' || step === 'done') && (
            <div>
              {/* Two-column layout: Thumbnail + Metrics */}
              <div style={{ display: 'flex', gap: '24px', marginBottom: '20px', flexWrap: 'wrap' }}>
                {/* Thumbnail area */}
                <div style={{
                  width: '260px', flexShrink: 0,
                  borderRadius: '12px', overflow: 'hidden',
                  background: '#000', position: 'relative',
                  aspectRatio: metadata?.width && metadata?.height ? `${metadata.width}/${metadata.height}` : '9/16',
                  maxHeight: '400px',
                }}>
                  {metadata?.thumbnails?.[activeThumbnail] ? (
                    <img
                      src={metadata.thumbnails[activeThumbnail]}
                      alt="Video preview"
                      style={{ width: '100%', height: '100%', objectFit: 'cover', transition: 'opacity 0.5s' }}
                    />
                  ) : (
                    <div style={{
                      width: '100%', height: '100%', display: 'flex',
                      alignItems: 'center', justifyContent: 'center', fontSize: '32px',
                    }}>🎬</div>
                  )}
                  {/* Play icon */}
                  <div style={{
                    position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
                    width: '40px', height: '40px', borderRadius: '50%', background: 'rgba(0,0,0,0.6)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    <div style={{
                      width: 0, height: 0, borderStyle: 'solid',
                      borderWidth: '8px 0 8px 14px', borderColor: 'transparent transparent transparent #fff',
                      marginLeft: '3px',
                    }} />
                  </div>
                  {/* Thumbnail dots */}
                  {metadata?.thumbnails?.length > 1 && (
                    <div style={{
                      position: 'absolute', bottom: '30px', left: '50%', transform: 'translateX(-50%)',
                      display: 'flex', gap: '4px',
                    }}>
                      {metadata.thumbnails.map((_, i) => (
                        <div key={i} onClick={(e) => { e.stopPropagation(); setActiveThumbnail(i); }} style={{
                          width: '6px', height: '6px', borderRadius: '50%', cursor: 'pointer',
                          background: i === activeThumbnail ? '#8b5cf6' : 'rgba(255,255,255,0.4)',
                          transition: 'background 0.3s',
                        }} />
                      ))}
                    </div>
                  )}
                  {/* File name badge */}
                  <div style={{
                    position: 'absolute', bottom: '8px', left: '8px', right: '8px',
                    background: 'rgba(0,0,0,0.75)', borderRadius: '6px', padding: '4px 8px',
                  }}>
                    <p style={{ color: '#e2e8f0', fontSize: '10px', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {fileName}
                    </p>
                  </div>
                </div>

                {/* Metrics area */}
                <div style={{ flex: 1, minWidth: '200px' }}>
                  {/* Status badge */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                    <div style={{
                      width: '8px', height: '8px', borderRadius: '50%',
                      background: step === 'done' ? '#10b981' : '#f59e0b',
                      animation: step === 'done' ? 'none' : 'pulse 2s infinite',
                    }} />
                    <span style={{
                      color: step === 'done' ? '#10b981' : '#f59e0b',
                      fontSize: '12px', fontWeight: '600',
                    }}>
                      {step === 'done' ? '✨ 解析完了！' : analysisStatus?.message || 'AI解析中...'}
                    </span>
                  </div>

                  {/* P0: Instant metadata cards */}
                  {metadata && (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '8px', marginBottom: '12px' }}>
                      {/* Duration - REAL from browser */}
                      <MetricCard
                        label="配信時間"
                        value={formatDuration(getRealMetric('duration') || metadata.duration)}
                        color="#8b5cf6"
                        isReal={true}
                        highlight={false}
                      />
                      {/* Resolution */}
                      <MetricCard
                        label="解像度"
                        value={metadata.width > 0 ? `${metadata.width}×${metadata.height}` : '—'}
                        color="#06b6d4"
                        isReal={true}
                        highlight={false}
                      />
                      {/* Scenes */}
                      <MetricCard
                        label={getRealMetric('scenes') ? 'シーン数' : '推定シーン数'}
                        value={`${getRealMetric('scenes') || metadata.estimatedScenes}`}
                        unit="シーン"
                        color="#f59e0b"
                        isReal={!!getRealMetric('scenes')}
                        highlight={isMetricUpdated('scenes')}
                      />
                      {/* Products */}
                      <MetricCard
                        label={getRealMetric('products') ? '商品検出' : '商品検出（予測）'}
                        value={`${getRealMetric('products') || metadata.estimatedProducts}`}
                        unit="商品"
                        color="#ec4899"
                        isReal={!!getRealMetric('products')}
                        highlight={isMetricUpdated('products')}
                      />
                    </div>
                  )}

                  {/* Extra metadata row */}
                  {metadata && (
                    <div style={{
                      display: 'flex', gap: '12px', marginBottom: '12px',
                      flexWrap: 'wrap',
                    }}>
                      <MiniStat label="ビットレート" value={`${metadata.bitrateMbps} Mbps`} />
                      <MiniStat label="FPS" value={`${metadata.estimatedFps}fps`} />
                      <MiniStat label="サイズ" value={`${metadata.fileSizeMB} MB`} />
                      {metadata.estimatedProcessingMin > 0 && (
                        <MiniStat label="推定処理時間" value={`約${metadata.estimatedProcessingMin}分`} />
                      )}
                    </div>
                  )}

                  {/* P2: Product detection preview from processing_logs */}
                  {analysisStatus?.top_products?.length > 0 && (
                    <div style={{
                      background: 'rgba(236, 72, 153, 0.08)', border: '1px solid rgba(236, 72, 153, 0.2)',
                      borderRadius: '8px', padding: '8px 12px', marginBottom: '12px',
                      animation: 'fadeIn 0.5s ease',
                    }}>
                      <p style={{ color: '#ec4899', fontSize: '10px', fontWeight: '600', margin: '0 0 4px' }}>🔍 検出された商品</p>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                        {analysisStatus.top_products.map((p, i) => (
                          <span key={i} style={{
                            background: 'rgba(236, 72, 153, 0.15)', color: '#f9a8d4',
                            fontSize: '11px', padding: '2px 8px', borderRadius: '4px',
                          }}>
                            {typeof p === 'string' ? p : p.name || p.product_name || JSON.stringify(p)}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Progress bar */}
                  <div style={{ marginBottom: '6px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                      <span style={{ color: '#94a3b8', fontSize: '11px' }}>
                        {step === 'done' ? '解析完了' : 'AI解析進捗'}
                      </span>
                      <span style={{ color: '#a78bfa', fontSize: '11px', fontFamily: 'monospace' }}>
                        {analysisProgress}%
                      </span>
                    </div>
                    <div style={{ height: '4px', background: 'rgba(255,255,255,0.08)', borderRadius: '2px', overflow: 'hidden' }}>
                      <div style={{
                        width: `${analysisProgress}%`, height: '100%',
                        background: step === 'done'
                          ? 'linear-gradient(90deg, #10b981, #34d399)'
                          : 'linear-gradient(90deg, #6366f1, #8b5cf6, #a78bfa)',
                        borderRadius: '2px', transition: 'width 0.8s ease',
                        position: 'relative',
                      }}>
                        {step !== 'done' && (
                          <div style={{
                            position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                            background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent)',
                            animation: 'shimmer 2s infinite',
                          }} />
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Analysis steps */}
              <div style={{
                background: 'rgba(0,0,0,0.25)', borderRadius: '12px',
                padding: '14px 18px', marginBottom: '20px',
                border: '1px solid rgba(255,255,255,0.05)',
              }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {ANALYSIS_STEPS.map((s, i) => {
                    const state = getStepState(s, i);
                    return (
                      <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <div style={{
                          width: '18px', height: '18px', borderRadius: '50%',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px',
                          flexShrink: 0,
                          ...(state === 'done'
                            ? { background: 'rgba(16, 185, 129, 0.2)', color: '#10b981' }
                            : state === 'active'
                              ? { border: '2px solid #8b5cf6', borderTopColor: 'transparent', animation: 'spin 1s linear infinite' }
                              : { background: 'rgba(255,255,255,0.05)', color: '#475569' }
                          ),
                        }}>
                          {state === 'done' ? '✓' : ''}
                        </div>
                        <span style={{
                          color: state === 'done' ? '#10b981' : state === 'active' ? '#e2e8f0' : '#475569',
                          fontSize: '13px', fontWeight: state === 'done' ? '500' : '400',
                          transition: 'color 0.3s',
                        }}>
                          {s.label}
                        </span>
                        {state === 'active' && (
                          <span style={{ color: '#8b5cf6', fontSize: '11px', marginLeft: 'auto' }}>
                            {analysisStatus?.step_progress > 0 ? `${analysisStatus.step_progress}%` : '処理中...'}
                          </span>
                        )}
                        {state === 'done' && i === ANALYSIS_STEPS.length - 1 && step === 'done' && (
                          <span style={{ color: '#10b981', fontSize: '11px', marginLeft: 'auto' }}>完了!</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* P2: Processing logs preview */}
              {analysisStatus?.processing_logs?.length > 0 && (
                <div style={{
                  background: 'rgba(0,0,0,0.2)', borderRadius: '8px',
                  padding: '10px 14px', marginBottom: '20px',
                  border: '1px solid rgba(255,255,255,0.05)',
                  maxHeight: '80px', overflow: 'auto',
                }}>
                  <p style={{ color: '#64748b', fontSize: '10px', margin: '0 0 4px', fontWeight: '600' }}>📋 処理ログ</p>
                  {analysisStatus.processing_logs.slice(-3).map((log, i) => (
                    <p key={i} style={{ color: '#94a3b8', fontSize: '10px', margin: '2px 0', fontFamily: 'monospace' }}>
                      {typeof log === 'string' ? log : log.message || JSON.stringify(log)}
                    </p>
                  ))}
                </div>
              )}

              {/* CTA */}
              <div style={{
                background: step === 'done'
                  ? 'linear-gradient(135deg, rgba(16, 185, 129, 0.1), rgba(6, 182, 212, 0.1))'
                  : 'linear-gradient(135deg, rgba(99, 102, 241, 0.1), rgba(139, 92, 246, 0.1))',
                border: `1px solid ${step === 'done' ? 'rgba(16, 185, 129, 0.25)' : 'rgba(139, 92, 246, 0.25)'}`,
                borderRadius: '16px', padding: '20px 24px', textAlign: 'center',
              }}>
                <p style={{ color: '#e2e8f0', fontSize: '15px', fontWeight: '600', marginBottom: '8px' }}>
                  {step === 'done' ? '🎉 解析が完了しました！' : '🔒 完全な解析レポートを見るには'}
                </p>
                <p style={{ color: '#94a3b8', fontSize: '13px', marginBottom: '16px' }}>
                  {step === 'done'
                    ? '無料アカウントを作成して、詳細な解析レポート・商品検出・改善提案を確認しましょう'
                    : '無料アカウントを作成すると、商品検出・売上ポイント・改善提案の全データにアクセスできます'}
                </p>
                <button
                  onClick={goRegister}
                  style={{
                    background: step === 'done'
                      ? 'linear-gradient(135deg, #10b981, #06b6d4)'
                      : 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                    border: 'none', color: '#fff', padding: '14px 40px', borderRadius: '12px',
                    fontSize: '16px', fontWeight: '700', cursor: 'pointer',
                    boxShadow: step === 'done'
                      ? '0 8px 40px rgba(16, 185, 129, 0.4)'
                      : '0 8px 40px rgba(99, 102, 241, 0.4)',
                    transition: 'all 0.3s',
                    animation: step === 'done' ? 'pulseGlow 2s infinite' : 'none',
                  }}
                  onMouseEnter={e => { e.target.style.transform = 'translateY(-2px)'; }}
                  onMouseLeave={e => { e.target.style.transform = 'translateY(0)'; }}
                >
                  {step === 'done' ? '無料登録して完全レポートを見る 🚀' : '無料登録して完全な解析を見る'}
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

      {/* CSS Animations */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        @keyframes shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes popIn { from { transform: scale(0.8); opacity: 0; } to { transform: scale(1); opacity: 1; } }
        @keyframes confettiFall {
          0% { transform: translateY(0) rotate(0deg); opacity: 1; }
          100% { transform: translateY(100vh) rotate(720deg); opacity: 0; }
        }
        @keyframes pulseGlow {
          0%, 100% { box-shadow: 0 8px 40px rgba(16, 185, 129, 0.4); }
          50% { box-shadow: 0 8px 60px rgba(16, 185, 129, 0.6); }
        }
        @keyframes highlightFlash {
          0% { background: rgba(16, 185, 129, 0.3); }
          100% { background: transparent; }
        }
      `}</style>
    </div>
  );
}

// ─── Sub-components ───

function MetricCard({ label, value, unit, color, isReal, highlight }) {
  return (
    <div style={{
      background: `${color}11`,
      border: `1px solid ${color}25`,
      borderRadius: '10px',
      padding: '10px 12px',
      position: 'relative',
      animation: highlight ? 'highlightFlash 1s ease' : 'none',
    }}>
      {isReal && (
        <div style={{
          position: 'absolute', top: '6px', right: '8px',
          width: '6px', height: '6px', borderRadius: '50%',
          background: '#10b981',
        }} />
      )}
      <p style={{
        color, fontSize: '10px', fontWeight: '600', margin: '0 0 2px',
        textTransform: 'uppercase', letterSpacing: '0.05em',
      }}>
        {label}
      </p>
      <p style={{ color: '#e2e8f0', fontSize: '18px', fontWeight: '700', margin: 0 }}>
        {value}
        {unit && <span style={{ fontSize: '11px', color: '#94a3b8', marginLeft: '2px' }}>{unit}</span>}
      </p>
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      borderRadius: '6px', padding: '4px 10px',
      border: '1px solid rgba(255,255,255,0.06)',
    }}>
      <span style={{ color: '#64748b', fontSize: '9px' }}>{label}: </span>
      <span style={{ color: '#94a3b8', fontSize: '10px', fontWeight: '600' }}>{value}</span>
    </div>
  );
}
