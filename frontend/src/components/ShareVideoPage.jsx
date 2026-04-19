import { useEffect, useState, useRef } from 'react';
import { useParams } from 'react-router-dom';

const API = import.meta.env.VITE_API_URL || 'https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net';

export default function ShareVideoPage() {
  const { clipId } = useParams();
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [showProduct, setShowProduct] = useState(false);
  const videoRef = useRef(null);

  useEffect(() => {
    if (!clipId) return;
    fetch(`${API}/widget/share/${clipId}`)
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
    // Twitter card
    setMetaTag('twitter:card', 'summary_large_image');
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

  const handleVideoEnd = () => {
    setShowProduct(true);
  };

  const handleProductClick = () => {
    if (meta?.product_url) {
      const url = new URL(meta.product_url, window.location.origin);
      url.searchParams.set('utm_source', 'aitherhub');
      url.searchParams.set('utm_medium', 'share');
      url.searchParams.set('utm_campaign', clipId);
      window.open(url.toString(), '_blank');
    }
  };

  const handleShare = async () => {
    const shareUrl = `https://www.aitherhub.com/v/${clipId}`;
    const shareText = meta?.title ? `${meta.title}\n${shareUrl}` : shareUrl;
    if (navigator.share) {
      try {
        await navigator.share({ title: meta?.title || 'AitherHub Video', text: shareText, url: shareUrl });
      } catch (e) { /* cancelled */ }
    } else {
      await navigator.clipboard.writeText(shareUrl);
      alert('リンクをコピーしました');
    }
  };

  if (loading) return (
    <div style={styles.container}>
      <div style={styles.loader}>
        <div style={styles.spinner} />
        <p style={styles.loadingText}>読み込み中...</p>
      </div>
    </div>
  );

  if (error) return (
    <div style={styles.container}>
      <div style={styles.errorBox}>
        <p style={styles.errorText}>動画が見つかりませんでした</p>
        <a href="/" style={styles.homeLink}>AitherHub トップへ</a>
      </div>
    </div>
  );

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        {/* Brand Header */}
        {meta.brand_name && (
          <div style={styles.brandHeader}>
            {meta.brand_logo_url && (
              <img src={meta.brand_logo_url} alt={meta.brand_name} style={styles.brandLogo} />
            )}
            <span style={styles.brandName}>{meta.brand_name}</span>
          </div>
        )}

        {/* Video Player */}
        <div style={styles.videoWrapper}>
          <video
            ref={videoRef}
            src={meta.video_url}
            poster={meta.thumbnail_url}
            style={styles.video}
            playsInline
            loop
            onEnded={handleVideoEnd}
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
            <button style={styles.playButton} onClick={handlePlay}>
              <svg width="64" height="64" viewBox="0 0 64 64" fill="none">
                <circle cx="32" cy="32" r="32" fill="rgba(0,0,0,0.5)" />
                <path d="M26 20L46 32L26 44V20Z" fill="white" />
              </svg>
            </button>
          )}
          {meta.duration_sec && (
            <span style={styles.duration}>
              {Math.floor(meta.duration_sec / 60)}:{String(Math.floor(meta.duration_sec % 60)).padStart(2, '0')}
            </span>
          )}
        </div>

        {/* Product Info */}
        {meta.product_name && (
          <div style={{...styles.productSection, ...(showProduct ? styles.productHighlight : {})}}>
            <div style={styles.productInfo}>
              {meta.product_image_url && (
                <img src={meta.product_image_url} alt={meta.product_name} style={styles.productImage} />
              )}
              <div style={styles.productText}>
                <h2 style={styles.productName}>{meta.product_name}</h2>
                {meta.product_price && (
                  <p style={styles.productPrice}>{meta.product_price}</p>
                )}
                {meta.liver_name && (
                  <p style={styles.liverName}>{meta.liver_name}</p>
                )}
              </div>
            </div>
            {meta.product_url && (
              <button style={styles.ctaButton} onClick={handleProductClick}>
                この商品を見る
              </button>
            )}
            {meta.product_cart_url && (
              <button style={styles.cartButton} onClick={() => {
                const url = new URL(meta.product_cart_url, window.location.origin);
                url.searchParams.set('utm_source', 'aitherhub');
                url.searchParams.set('utm_medium', 'share');
                url.searchParams.set('utm_campaign', clipId);
                window.open(url.toString(), '_blank');
              }}>
                カートに追加
              </button>
            )}
          </div>
        )}

        {/* Action Buttons */}
        <div style={styles.actions}>
          <button style={styles.shareButton} onClick={handleShare}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8M16 6l-4-4-4 4M12 2v13" />
            </svg>
            シェア
          </button>
          <a href="/" style={styles.moreButton}>
            他の動画を見る
          </a>
        </div>

        {/* Footer */}
        <div style={styles.footer}>
          <span style={styles.footerText}>Powered by </span>
          <a href="https://www.aitherhub.com" style={styles.footerLink}>AitherHub</a>
        </div>
      </div>
    </div>
  );
}

const styles = {
  container: {
    minHeight: '100vh',
    background: 'linear-gradient(135deg, #0a0a0a 0%, #1a1a2e 50%, #16213e 100%)',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'flex-start',
    padding: '20px 16px',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  },
  card: {
    width: '100%',
    maxWidth: '480px',
    background: '#1e1e2e',
    borderRadius: '16px',
    overflow: 'hidden',
    boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
  },
  brandHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '16px 20px',
    borderBottom: '1px solid rgba(255,255,255,0.08)',
  },
  brandLogo: {
    width: '32px',
    height: '32px',
    borderRadius: '50%',
    objectFit: 'cover',
  },
  brandName: {
    color: '#fff',
    fontSize: '15px',
    fontWeight: '600',
  },
  videoWrapper: {
    position: 'relative',
    width: '100%',
    aspectRatio: '9/16',
    background: '#000',
    cursor: 'pointer',
  },
  video: {
    width: '100%',
    height: '100%',
    objectFit: 'cover',
  },
  playButton: {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    zIndex: 2,
  },
  duration: {
    position: 'absolute',
    bottom: '12px',
    right: '12px',
    background: 'rgba(0,0,0,0.7)',
    color: '#fff',
    fontSize: '12px',
    padding: '2px 8px',
    borderRadius: '4px',
  },
  productSection: {
    padding: '20px',
    borderTop: '1px solid rgba(255,255,255,0.08)',
    transition: 'background 0.3s',
  },
  productHighlight: {
    background: 'rgba(255,45,85,0.08)',
  },
  productInfo: {
    display: 'flex',
    gap: '14px',
    marginBottom: '14px',
  },
  productImage: {
    width: '72px',
    height: '72px',
    borderRadius: '10px',
    objectFit: 'cover',
    flexShrink: 0,
  },
  productText: {
    flex: 1,
    minWidth: 0,
  },
  productName: {
    color: '#fff',
    fontSize: '15px',
    fontWeight: '600',
    margin: '0 0 6px 0',
    lineHeight: '1.4',
  },
  productPrice: {
    color: '#FF2D55',
    fontSize: '18px',
    fontWeight: '700',
    margin: '0 0 4px 0',
  },
  liverName: {
    color: 'rgba(255,255,255,0.5)',
    fontSize: '13px',
    margin: 0,
  },
  ctaButton: {
    width: '100%',
    padding: '14px',
    background: 'linear-gradient(135deg, #FF2D55, #FF6B6B)',
    color: '#fff',
    border: 'none',
    borderRadius: '12px',
    fontSize: '16px',
    fontWeight: '700',
    cursor: 'pointer',
    marginBottom: '8px',
    transition: 'transform 0.2s',
  },
  cartButton: {
    width: '100%',
    padding: '12px',
    background: 'transparent',
    color: '#FF2D55',
    border: '2px solid #FF2D55',
    borderRadius: '12px',
    fontSize: '14px',
    fontWeight: '600',
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  actions: {
    display: 'flex',
    gap: '10px',
    padding: '16px 20px',
    borderTop: '1px solid rgba(255,255,255,0.08)',
  },
  shareButton: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '8px',
    padding: '12px',
    background: 'rgba(255,255,255,0.08)',
    color: '#fff',
    border: 'none',
    borderRadius: '10px',
    fontSize: '14px',
    fontWeight: '500',
    cursor: 'pointer',
  },
  moreButton: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '12px',
    background: 'rgba(255,255,255,0.08)',
    color: '#fff',
    border: 'none',
    borderRadius: '10px',
    fontSize: '14px',
    fontWeight: '500',
    cursor: 'pointer',
    textDecoration: 'none',
  },
  footer: {
    padding: '12px 20px',
    textAlign: 'center',
    borderTop: '1px solid rgba(255,255,255,0.05)',
  },
  footerText: {
    color: 'rgba(255,255,255,0.3)',
    fontSize: '12px',
  },
  footerLink: {
    color: 'rgba(255,255,255,0.5)',
    fontSize: '12px',
    textDecoration: 'none',
  },
  loader: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '60vh',
  },
  spinner: {
    width: '40px',
    height: '40px',
    border: '3px solid rgba(255,255,255,0.1)',
    borderTopColor: '#FF2D55',
    borderRadius: '50%',
    animation: 'spin 0.8s linear infinite',
  },
  loadingText: {
    color: 'rgba(255,255,255,0.5)',
    marginTop: '16px',
    fontSize: '14px',
  },
  errorBox: {
    textAlign: 'center',
    padding: '60px 20px',
  },
  errorText: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: '16px',
    marginBottom: '16px',
  },
  homeLink: {
    color: '#FF2D55',
    textDecoration: 'none',
    fontSize: '14px',
  },
};
