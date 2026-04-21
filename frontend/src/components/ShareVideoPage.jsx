import { useEffect, useState, useRef, useCallback } from 'react';
import { useParams } from 'react-router-dom';

const API = import.meta.env.VITE_API_URL || 'https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net';

export default function ShareVideoPage() {
  const { clipId } = useParams();
  const [clips, setClips] = useState([]);
  const [brandName, setBrandName] = useState('');
  const [themeColor, setThemeColor] = useState('#FF2D55');
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isMuted, setIsMuted] = useState(true);
  const [showSoundHint, setShowSoundHint] = useState(true);
  const [liked, setLiked] = useState({});

  const containerRef = useRef(null);
  const videoRefs = useRef({});
  const touchStartY = useRef(0);
  const touchStartTime = useRef(0);
  const isTransitioning = useRef(false);

  // ── Fetch data ──
  useEffect(() => {
    if (!clipId) return;
    fetch(`${API}/api/v1/widget/share/${clipId}`)
      .then(r => { if (!r.ok) throw new Error('Clip not found'); return r.json(); })
      .then(meta => {
        const clientId = meta.client_id;
        setBrandName(meta.brand_name || '');
        setThemeColor(meta.theme_color || '#FF2D55');

        if (meta.og) {
          document.title = meta.og.title || 'AitherHub Video';
          const setMeta = (prop, val) => {
            if (!val) return;
            let el = document.querySelector(`meta[property="${prop}"]`);
            if (!el) { el = document.createElement('meta'); el.setAttribute('property', prop); document.head.appendChild(el); }
            el.setAttribute('content', val);
          };
          setMeta('og:title', meta.og.title);
          setMeta('og:description', meta.og.description);
          setMeta('og:image', meta.og.image);
          setMeta('og:url', meta.og.url);
        }

        if (!clientId) {
          setClips([meta]);
          setCurrentIndex(0);
          setLoading(false);
          return;
        }

        return fetch(`${API}/api/v1/widget/config/${clientId}`)
          .then(r => r.json())
          .then(config => {
            const allClips = (config.clips || []).map(c => ({
              ...c,
              video_url: c.widget_url || c.exported_url || c.clip_url,
            }));
            if (!allClips.length) {
              setClips([meta]);
              setCurrentIndex(0);
              setLoading(false);
              return;
            }
            let targetIdx = allClips.findIndex(c => c.clip_id === clipId);
            if (targetIdx === -1) targetIdx = 0;
            setClips(allClips);
            setCurrentIndex(targetIdx);
            setLoading(false);
          });
      })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [clipId]);

  // ── Play current video ──
  useEffect(() => {
    if (!clips.length) return;
    Object.entries(videoRefs.current).forEach(([idx, vid]) => {
      if (!vid) return;
      if (parseInt(idx) === currentIndex) {
        vid.muted = isMuted;
        const playPromise = vid.play();
        if (playPromise) playPromise.catch(() => {});
      } else {
        vid.pause();
        vid.currentTime = 0;
      }
    });
  }, [currentIndex, clips.length]);

  // ── Mute toggle ──
  useEffect(() => {
    const vid = videoRefs.current[currentIndex];
    if (vid) vid.muted = isMuted;
  }, [isMuted, currentIndex]);

  // ── Touch/Swipe handling ──
  const goTo = useCallback((newIndex) => {
    if (isTransitioning.current) return;
    if (newIndex < 0 || newIndex >= clips.length) return;
    isTransitioning.current = true;
    setCurrentIndex(newIndex);
    setTimeout(() => { isTransitioning.current = false; }, 400);
  }, [clips.length]);

  const handleTouchStart = (e) => {
    touchStartY.current = e.touches[0].clientY;
    touchStartTime.current = Date.now();
  };

  const handleTouchEnd = (e) => {
    const dy = touchStartY.current - e.changedTouches[0].clientY;
    const dt = Date.now() - touchStartTime.current;
    const threshold = 50;
    const velocity = Math.abs(dy) / dt;

    if (Math.abs(dy) > threshold || velocity > 0.3) {
      if (dy > 0) goTo(currentIndex + 1);
      else goTo(currentIndex - 1);
    }
  };

  // ── Wheel handling (desktop) ──
  const wheelTimeout = useRef(null);
  const handleWheel = (e) => {
    e.preventDefault();
    if (wheelTimeout.current) return;
    wheelTimeout.current = setTimeout(() => { wheelTimeout.current = null; }, 600);
    if (e.deltaY > 30) goTo(currentIndex + 1);
    else if (e.deltaY < -30) goTo(currentIndex - 1);
  };

  // ── Dismiss sound hint ──
  const dismissSoundHint = () => {
    setShowSoundHint(false);
    setIsMuted(false);
  };

  // ── Share ──
  const handleShare = async () => {
    const shareUrl = `${API}/v/${clips[currentIndex]?.clip_id || clipId}`;
    const shareTitle = brandName || 'AitherHub Video';
    if (navigator.share) {
      try { await navigator.share({ title: shareTitle, url: shareUrl }); } catch {}
    } else {
      await navigator.clipboard?.writeText(shareUrl);
      alert('リンクをコピーしました');
    }
  };

  // ── Close ──
  const handleClose = () => {
    const clip = clips[currentIndex];
    if (clip?.product_url) {
      window.location.href = clip.product_url;
    } else {
      window.location.href = '/';
    }
  };

  // ── Loading ──
  if (loading) return (
    <div style={S.fullscreen}>
      <div style={S.center}>
        <div style={{...S.spinner, borderTopColor: themeColor}} />
      </div>
      <style>{`@keyframes ath-spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );

  // ── Error ──
  if (error || !clips.length) return (
    <div style={S.fullscreen}>
      <div style={S.center}>
        <p style={S.errorText}>動画が見つかりませんでした</p>
        <a href="/" style={{color: themeColor, textDecoration: 'none'}}>トップへ</a>
      </div>
    </div>
  );

  const currentClip = clips[currentIndex] || {};

  return (
    <div
      ref={containerRef}
      style={S.fullscreen}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onWheel={handleWheel}
    >
      {/* ── Video Slides ── */}
      <div style={S.slidesContainer}>
        {clips.map((clip, idx) => {
          const offset = idx - currentIndex;
          if (Math.abs(offset) > 1) return null;
          return (
            <div
              key={clip.clip_id || idx}
              style={{
                ...S.slide,
                transform: `translateY(${offset * 100}%)`,
                transition: 'transform 0.4s cubic-bezier(0.25, 0.1, 0.25, 1)',
                WebkitTransition: '-webkit-transform 0.4s cubic-bezier(0.25, 0.1, 0.25, 1)',
                zIndex: offset === 0 ? 2 : 1,
              }}
            >
              <video
                ref={el => { videoRefs.current[idx] = el; }}
                src={clip.video_url || clip.widget_url || clip.exported_url || clip.clip_url}
                poster={clip.thumbnail_url || undefined}
                style={S.video}
                playsInline
                webkit-playsinline=""
                x5-playsinline=""
                loop
                muted={isMuted}
                preload={Math.abs(offset) <= 1 ? 'auto' : 'none'}
                onClick={() => {
                  const vid = videoRefs.current[idx];
                  if (vid) vid.paused ? vid.play().catch(() => {}) : vid.pause();
                }}
              />
            </div>
          );
        })}
      </div>

      {/* ── Header ── */}
      <div style={S.header}>
        <div style={S.headerBrand}>
          {brandName || 'KYOGOKU Professional'}
        </div>
        <button style={S.closeBtn} onClick={handleClose}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* ── Clip counter ── */}
      <div style={S.counter}>
        {currentIndex + 1}
      </div>

      {/* ── Right side actions ── */}
      <div style={S.actions}>
        <button
          style={S.actionBtn}
          onClick={() => setLiked(prev => ({ ...prev, [currentIndex]: !prev[currentIndex] }))}
        >
          <svg width="28" height="28" viewBox="0 0 24 24"
            fill={liked[currentIndex] ? themeColor : 'none'}
            stroke={liked[currentIndex] ? themeColor : 'white'}
            strokeWidth="2"
          >
            <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
          </svg>
          <span style={S.actionLabel}>いいね</span>
        </button>

        <button style={S.actionBtn} onClick={handleShare}>
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
            <path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8M16 6l-4-4-4 4M12 2v13" />
          </svg>
          <span style={S.actionLabel}>シェア</span>
        </button>

        <button style={S.actionBtn} onClick={() => { setIsMuted(!isMuted); setShowSoundHint(false); }}>
          {isMuted ? (
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
              <path d="M11 5L6 9H2v6h4l5 4V5z" />
              <line x1="23" y1="9" x2="17" y2="15" />
              <line x1="17" y1="9" x2="23" y2="15" />
            </svg>
          ) : (
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
              <path d="M11 5L6 9H2v6h4l5 4V5z" />
              <path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07" />
            </svg>
          )}
          <span style={S.actionLabel}>音声</span>
        </button>
      </div>

      {/* ── Sound hint overlay ── */}
      {showSoundHint && (
        <div style={S.soundHint} onClick={dismissSoundHint}>
          <div style={S.soundHintBox}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
              <path d="M11 5L6 9H2v6h4l5 4V5z" />
              <path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07" />
            </svg>
            <span style={S.soundHintText}>タップして音声ON</span>
          </div>
        </div>
      )}

      {/* ── CTA Button ── */}
      {currentClip.product_url && (
        <div style={S.ctaArea}>
          <button
            style={{...S.ctaBtn, background: themeColor}}
            onClick={() => { window.open(currentClip.product_url, '_blank'); }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" style={{marginRight: 8}}>
              <path d="M6 2L3 6v14a2 2 0 002 2h14a2 2 0 002-2V6l-3-4zM3 6h18M16 10a4 4 0 01-8 0" />
            </svg>
            商品を見る
          </button>
        </div>
      )}

      {/* ── Footer ── */}
      <div style={S.footer}>
        <div style={S.footerBrand}>{brandName || 'KYOGOKU Professional'}</div>
        <div style={S.footerPowered}>Powered by AitherHub</div>
      </div>

      <style>{`
        @keyframes ath-spin { to { transform: rotate(360deg); } }
        * { -webkit-tap-highlight-color: transparent; }
        html, body { margin: 0; padding: 0; overflow: hidden; height: 100%; width: 100%; background: #000; }
        #root { height: 100%; width: 100%; }
        video::-webkit-media-controls { display: none !important; }
        video::-webkit-media-controls-enclosure { display: none !important; }
      `}</style>
    </div>
  );
}

// ─── Styles (Safari/iOS compatible - no 'inset' shorthand) ───
const S = {
  fullscreen: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    width: '100%',
    height: '100%',
    background: '#000',
    overflow: 'hidden',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Hiragino Sans", "Noto Sans JP", sans-serif',
    WebkitFontSmoothing: 'antialiased',
    touchAction: 'none',
    userSelect: 'none',
    WebkitUserSelect: 'none',
  },
  center: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    height: '100%',
  },
  spinner: {
    width: 36, height: 36,
    border: '3px solid rgba(255,255,255,0.1)',
    borderTopColor: '#FF2D55',
    borderRadius: '50%',
    animation: 'ath-spin 0.7s linear infinite',
  },
  errorText: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 15,
    marginBottom: 16,
  },

  // Slides
  slidesContainer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    overflow: 'hidden',
  },
  slide: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    width: '100%',
    height: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  video: {
    width: '100%',
    height: '100%',
    objectFit: 'cover',
    WebkitTransform: 'translateZ(0)',
  },

  // Header
  header: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '48px 16px 12px',
    background: 'linear-gradient(180deg, rgba(0,0,0,0.6) 0%, transparent 100%)',
    zIndex: 10,
  },
  headerBrand: {
    color: '#fff',
    fontSize: 16,
    fontWeight: 700,
    textShadow: '0 1px 4px rgba(0,0,0,0.5)',
  },
  closeBtn: {
    background: 'rgba(0,0,0,0.3)',
    border: 'none',
    borderRadius: '50%',
    width: 36,
    height: 36,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    WebkitBackdropFilter: 'blur(8px)',
    backdropFilter: 'blur(8px)',
  },

  // Counter
  counter: {
    position: 'absolute',
    top: 56,
    right: 16,
    color: 'rgba(255,255,255,0.7)',
    fontSize: 13,
    fontWeight: 600,
    zIndex: 10,
    marginTop: 40,
  },

  // Right actions
  actions: {
    position: 'absolute',
    right: 12,
    bottom: 180,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 20,
    zIndex: 10,
  },
  actionBtn: {
    background: 'none',
    border: 'none',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 4,
    cursor: 'pointer',
    padding: 0,
    filter: 'drop-shadow(0 1px 3px rgba(0,0,0,0.5))',
    WebkitFilter: 'drop-shadow(0 1px 3px rgba(0,0,0,0.5))',
  },
  actionLabel: {
    color: '#fff',
    fontSize: 11,
    fontWeight: 500,
    textShadow: '0 1px 3px rgba(0,0,0,0.5)',
  },

  // Sound hint
  soundHint: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 20,
    cursor: 'pointer',
  },
  soundHintBox: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 8,
    background: 'rgba(0,0,0,0.6)',
    padding: '20px 32px',
    borderRadius: 16,
    WebkitBackdropFilter: 'blur(12px)',
    backdropFilter: 'blur(12px)',
  },
  soundHintText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: 600,
  },

  // CTA
  ctaArea: {
    position: 'absolute',
    bottom: 80,
    left: 16,
    right: 16,
    zIndex: 10,
  },
  ctaBtn: {
    width: '100%',
    padding: '14px 20px',
    color: '#fff',
    border: 'none',
    borderRadius: 28,
    fontSize: 15,
    fontWeight: 700,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    boxShadow: '0 4px 20px rgba(0,0,0,0.4)',
    WebkitBackdropFilter: 'blur(8px)',
    backdropFilter: 'blur(8px)',
  },

  // Footer
  footer: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    padding: '12px 16px 32px',
    background: 'linear-gradient(0deg, rgba(0,0,0,0.6) 0%, transparent 100%)',
    zIndex: 10,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
  },
  footerBrand: {
    color: '#fff',
    fontSize: 14,
    fontWeight: 600,
    textShadow: '0 1px 4px rgba(0,0,0,0.5)',
  },
  footerPowered: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: 11,
  },
};
