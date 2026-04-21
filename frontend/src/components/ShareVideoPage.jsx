import { useEffect, useState, useRef } from 'react';
import { useParams } from 'react-router-dom';

const API = import.meta.env.VITE_API_URL || 'https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net';

export default function ShareVideoPage() {
  const { clipId } = useParams();
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [showProductHighlight, setShowProductHighlight] = useState(false);
  const videoRef = useRef(null);

  useEffect(() => {
    if (!clipId) return;
    fetch(`${API}/api/v1/widget/share/${clipId}`)
      .then(r => { if (!r.ok) throw new Error('Clip not found'); return r.json(); })
      .then(data => { setMeta(data); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [clipId]);

  // Update OGP meta tags dynamically (for SPA)
  useEffect(() => {
    if (!meta?.og) return;
    const og = meta.og;
    document.title = og.title || 'AitherHub Video';
    const setMetaTag = (property, content) => {
      if (!content) return;
      let el = document.querySelector(`meta[property="${property}"]`);
      if (!el) { el = document.createElement('meta'); el.setAttribute('property', property); document.head.appendChild(el); }
      el.setAttribute('content', content);
    };
    setMetaTag('og:title', og.title);
    setMetaTag('og:description', og.description);
    setMetaTag('og:image', og.image);
    setMetaTag('og:url', og.url);
    setMetaTag('og:type', og.type);
    if (og.video) setMetaTag('og:video', og.video);
    setMetaTag('twitter:card', og.image ? 'summary_large_image' : 'summary');
    setMetaTag('twitter:title', og.title);
    setMetaTag('twitter:description', og.description);
    setMetaTag('twitter:image', og.image);
  }, [meta]);

  const handlePlay = () => {
    if (videoRef.current) {
      videoRef.current.play();
      setIsPlaying(true);
    }
  };

  const handleTimeUpdate = () => {
    if (videoRef.current && videoRef.current.duration) {
      const pct = (videoRef.current.currentTime / videoRef.current.duration) * 100;
      setProgress(pct);
      // Show product highlight at 50% progress
      if (pct > 50 && !showProductHighlight) {
        setShowProductHighlight(true);
      }
    }
  };

  const handleVideoEnd = () => {
    setShowProductHighlight(true);
  };

  const buildUtmUrl = (baseUrl, medium = 'share') => {
    try {
      const url = new URL(baseUrl, window.location.origin);
      url.searchParams.set('utm_source', 'aitherhub');
      url.searchParams.set('utm_medium', medium);
      url.searchParams.set('utm_campaign', clipId);
      return url.toString();
    } catch {
      return baseUrl;
    }
  };

  const handleProductClick = () => {
    if (meta?.product_url) {
      window.open(buildUtmUrl(meta.product_url), '_blank');
    }
  };

  const handleShare = async () => {
    const shareUrl = `${API}/v/${clipId}`;
    const shareTitle = meta?.title || 'AitherHub Video';
    if (navigator.share) {
      try {
        await navigator.share({ title: shareTitle, text: shareTitle, url: shareUrl });
      } catch (e) { /* cancelled */ }
    } else {
      await navigator.clipboard.writeText(shareUrl);
      alert('リンクをコピーしました');
    }
  };

  const themeColor = meta?.theme_color || '#FF2D55';

  // --- Loading State ---
  if (loading) return (
    <div style={styles.pageWrap}>
      <div style={styles.loaderContainer}>
        <div style={{...styles.spinner, borderTopColor: themeColor}} />
        <p style={styles.loadingText}>読み込み中...</p>
      </div>
    </div>
  );

  // --- Error State ---
  if (error) return (
    <div style={styles.pageWrap}>
      <div style={styles.errorContainer}>
        <div style={styles.errorIcon}>
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.5)" strokeWidth="1.5">
            <circle cx="12" cy="12" r="10" />
            <path d="M15 9l-6 6M9 9l6 6" />
          </svg>
        </div>
        <p style={styles.errorText}>動画が見つかりませんでした</p>
        <a href="/" style={{...styles.errorLink, color: themeColor}}>AitherHub トップへ</a>
      </div>
    </div>
  );

  const hasProduct = meta.product_name || meta.product_price;
  const hasProductImage = meta.product_image_url;

  return (
    <div style={styles.pageWrap}>
      <div style={styles.card}>

        {/* ── Brand Header ── */}
        <div style={styles.brandBar}>
          <div style={styles.brandLeft}>
            {meta.brand_logo_url ? (
              <img src={meta.brand_logo_url} alt={meta.brand_name} style={styles.brandLogo} />
            ) : meta.brand_name ? (
              <div style={{...styles.brandInitial, background: themeColor}}>
                {meta.brand_name.charAt(0).toUpperCase()}
              </div>
            ) : null}
            <div>
              <span style={styles.brandName}>{meta.brand_name || 'AitherHub'}</span>
              {meta.liver_name && <span style={styles.liverTag}>by {meta.liver_name}</span>}
            </div>
          </div>
          <button style={styles.shareIconBtn} onClick={handleShare} title="シェア">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8M16 6l-4-4-4 4M12 2v13" />
            </svg>
          </button>
        </div>

        {/* ── Video Player ── */}
        <div style={styles.videoContainer}>
          <video
            ref={videoRef}
            src={meta.video_url}
            poster={meta.thumbnail_url || undefined}
            style={styles.video}
            playsInline
            loop
            preload="metadata"
            onEnded={handleVideoEnd}
            onTimeUpdate={handleTimeUpdate}
            onPlay={() => setIsPlaying(true)}
            onPause={() => setIsPlaying(false)}
            onClick={() => {
              if (videoRef.current) {
                if (videoRef.current.paused) videoRef.current.play();
                else videoRef.current.pause();
              }
            }}
            controls={isPlaying}
          />
          {!isPlaying && (
            <div style={styles.playOverlay} onClick={handlePlay}>
              <div style={{...styles.playCircle, background: themeColor}}>
                <svg width="28" height="28" viewBox="0 0 24 24" fill="white">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </div>
              {meta.duration_sec && (
                <span style={styles.durationBadge}>
                  {Math.floor(meta.duration_sec / 60)}:{String(Math.floor(meta.duration_sec % 60)).padStart(2, '0')}
                </span>
              )}
            </div>
          )}
          {/* Progress bar */}
          {isPlaying && (
            <div style={styles.progressTrack}>
              <div style={{...styles.progressFill, width: `${progress}%`, background: themeColor}} />
            </div>
          )}
        </div>

        {/* ── Product Section ── */}
        {hasProduct && (
          <div style={{
            ...styles.productSection,
            ...(showProductHighlight ? { background: `${themeColor}10`, borderLeft: `3px solid ${themeColor}` } : {}),
          }}>
            <div style={styles.productRow}>
              {hasProductImage && (
                <img src={meta.product_image_url} alt={meta.product_name} style={styles.productImg} />
              )}
              <div style={styles.productDetails}>
                <h2 style={styles.productTitle}>{meta.product_name}</h2>
                {meta.product_price && (
                  <span style={{...styles.priceTag, color: themeColor}}>{meta.product_price}</span>
                )}
                {meta.product_description && (
                  <p style={styles.productDesc}>
                    {meta.product_description.length > 80
                      ? meta.product_description.slice(0, 80) + '...'
                      : meta.product_description}
                  </p>
                )}
              </div>
            </div>

            {meta.product_url && (
              <button
                style={{...styles.ctaBtn, background: `linear-gradient(135deg, ${themeColor}, ${themeColor}cc)`}}
                onClick={handleProductClick}
              >
                この商品を見る
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{marginLeft: 6}}>
                  <path d="M5 12h14M12 5l7 7-7 7" />
                </svg>
              </button>
            )}
            {meta.product_cart_url && (
              <button
                style={{...styles.cartBtn, color: themeColor, borderColor: themeColor}}
                onClick={() => window.open(buildUtmUrl(meta.product_cart_url, 'share_cart'), '_blank')}
              >
                カートに追加
              </button>
            )}
          </div>
        )}

        {/* ── If no product info, still show brand CTA ── */}
        {!hasProduct && meta.product_url && (
          <div style={styles.productSection}>
            <button
              style={{...styles.ctaBtn, background: `linear-gradient(135deg, ${themeColor}, ${themeColor}cc)`}}
              onClick={handleProductClick}
            >
              詳しく見る
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{marginLeft: 6}}>
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </button>
          </div>
        )}

        {/* ── Action Bar ── */}
        <div style={styles.actionBar}>
          <button style={styles.actionBtn} onClick={handleShare}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8M16 6l-4-4-4 4M12 2v13" />
            </svg>
            <span>シェア</span>
          </button>
          <a href="/" style={styles.actionBtn}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="3" width="7" height="7" rx="1" />
              <rect x="14" y="3" width="7" height="7" rx="1" />
              <rect x="3" y="14" width="7" height="7" rx="1" />
              <rect x="14" y="14" width="7" height="7" rx="1" />
            </svg>
            <span>他の動画</span>
          </a>
        </div>

        {/* ── Footer ── */}
        <div style={styles.footer}>
          <span style={styles.footerText}>Powered by </span>
          <a href="https://www.aitherhub.com" style={{...styles.footerLink, color: `${themeColor}88`}}>AitherHub</a>
        </div>
      </div>
    </div>
  );
}

// ─── Styles ───
const styles = {
  pageWrap: {
    minHeight: '100vh',
    background: 'linear-gradient(160deg, #0a0a12 0%, #12122a 40%, #1a1a3e 100%)',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'flex-start',
    padding: '24px 16px',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Hiragino Sans", "Noto Sans JP", sans-serif',
    WebkitFontSmoothing: 'antialiased',
  },
  card: {
    width: '100%',
    maxWidth: '440px',
    background: 'rgba(25, 25, 40, 0.95)',
    borderRadius: '20px',
    overflow: 'hidden',
    boxShadow: '0 24px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.06)',
    backdropFilter: 'blur(20px)',
  },

  // Brand Bar
  brandBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '14px 18px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  brandLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
  },
  brandLogo: {
    width: '34px',
    height: '34px',
    borderRadius: '50%',
    objectFit: 'cover',
    border: '1px solid rgba(255,255,255,0.1)',
  },
  brandInitial: {
    width: '34px',
    height: '34px',
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#fff',
    fontSize: '15px',
    fontWeight: '700',
    flexShrink: 0,
  },
  brandName: {
    color: '#fff',
    fontSize: '14px',
    fontWeight: '600',
    display: 'block',
    lineHeight: '1.3',
  },
  liverTag: {
    color: 'rgba(255,255,255,0.4)',
    fontSize: '11px',
    display: 'block',
  },
  shareIconBtn: {
    background: 'rgba(255,255,255,0.08)',
    border: 'none',
    borderRadius: '50%',
    width: '36px',
    height: '36px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: 'rgba(255,255,255,0.7)',
    cursor: 'pointer',
    transition: 'background 0.2s',
  },

  // Video
  videoContainer: {
    position: 'relative',
    width: '100%',
    aspectRatio: '9/16',
    background: '#000',
    cursor: 'pointer',
    overflow: 'hidden',
  },
  video: {
    width: '100%',
    height: '100%',
    objectFit: 'cover',
  },
  playOverlay: {
    position: 'absolute',
    inset: 0,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'linear-gradient(180deg, rgba(0,0,0,0.1) 0%, rgba(0,0,0,0.4) 100%)',
    cursor: 'pointer',
  },
  playCircle: {
    width: '64px',
    height: '64px',
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
    transition: 'transform 0.2s',
  },
  durationBadge: {
    marginTop: '12px',
    background: 'rgba(0,0,0,0.6)',
    color: '#fff',
    fontSize: '12px',
    fontWeight: '500',
    padding: '4px 12px',
    borderRadius: '20px',
    backdropFilter: 'blur(8px)',
  },
  progressTrack: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    height: '3px',
    background: 'rgba(255,255,255,0.15)',
  },
  progressFill: {
    height: '100%',
    borderRadius: '0 2px 2px 0',
    transition: 'width 0.3s linear',
  },

  // Product
  productSection: {
    padding: '18px',
    borderTop: '1px solid rgba(255,255,255,0.06)',
    transition: 'all 0.4s ease',
  },
  productRow: {
    display: 'flex',
    gap: '14px',
    marginBottom: '14px',
  },
  productImg: {
    width: '72px',
    height: '72px',
    borderRadius: '12px',
    objectFit: 'cover',
    flexShrink: 0,
    border: '1px solid rgba(255,255,255,0.08)',
  },
  productDetails: {
    flex: 1,
    minWidth: 0,
  },
  productTitle: {
    color: '#fff',
    fontSize: '15px',
    fontWeight: '600',
    margin: '0 0 6px 0',
    lineHeight: '1.4',
    display: '-webkit-box',
    WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical',
    overflow: 'hidden',
  },
  priceTag: {
    fontSize: '18px',
    fontWeight: '700',
    display: 'inline-block',
    marginBottom: '4px',
  },
  productDesc: {
    color: 'rgba(255,255,255,0.45)',
    fontSize: '12px',
    lineHeight: '1.5',
    margin: '4px 0 0 0',
  },
  ctaBtn: {
    width: '100%',
    padding: '14px',
    color: '#fff',
    border: 'none',
    borderRadius: '12px',
    fontSize: '15px',
    fontWeight: '700',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'transform 0.15s, box-shadow 0.15s',
    boxShadow: '0 4px 16px rgba(0,0,0,0.3)',
    marginBottom: '8px',
  },
  cartBtn: {
    width: '100%',
    padding: '12px',
    background: 'transparent',
    border: '1.5px solid',
    borderRadius: '12px',
    fontSize: '14px',
    fontWeight: '600',
    cursor: 'pointer',
    transition: 'all 0.2s',
  },

  // Actions
  actionBar: {
    display: 'flex',
    gap: '8px',
    padding: '12px 18px',
    borderTop: '1px solid rgba(255,255,255,0.06)',
  },
  actionBtn: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '6px',
    padding: '10px',
    background: 'rgba(255,255,255,0.06)',
    color: 'rgba(255,255,255,0.7)',
    border: 'none',
    borderRadius: '10px',
    fontSize: '13px',
    fontWeight: '500',
    cursor: 'pointer',
    textDecoration: 'none',
    transition: 'background 0.2s',
  },

  // Footer
  footer: {
    padding: '10px 18px',
    textAlign: 'center',
    borderTop: '1px solid rgba(255,255,255,0.04)',
  },
  footerText: {
    color: 'rgba(255,255,255,0.2)',
    fontSize: '11px',
  },
  footerLink: {
    fontSize: '11px',
    textDecoration: 'none',
    fontWeight: '500',
  },

  // Loading
  loaderContainer: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '60vh',
  },
  spinner: {
    width: '36px',
    height: '36px',
    border: '3px solid rgba(255,255,255,0.08)',
    borderTopColor: '#FF2D55',
    borderRadius: '50%',
    animation: 'spin 0.7s linear infinite',
  },
  loadingText: {
    color: 'rgba(255,255,255,0.4)',
    marginTop: '14px',
    fontSize: '13px',
  },

  // Error
  errorContainer: {
    textAlign: 'center',
    padding: '80px 20px',
  },
  errorIcon: {
    marginBottom: '16px',
  },
  errorText: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: '15px',
    marginBottom: '16px',
  },
  errorLink: {
    textDecoration: 'none',
    fontSize: '14px',
    fontWeight: '500',
  },
};
