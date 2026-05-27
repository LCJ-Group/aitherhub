import { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import AIVideoEditorDemo from './AIVideoEditorDemo';
import VideoUploadCTA from './VideoUploadCTA';

/* ─────────────────────────────────────────────
   AitherHub Landing Page — Ultra Technology Edition
   3D transforms, neural network animation, glassmorphism,
   video editing timeline UI, holographic borders
   ───────────────────────────────────────────── */

// ═══════════════════════════════════════════════
// Neural Network Canvas Background
// ═══════════════════════════════════════════════
function NeuralNetworkBg() {
  const canvasRef = useRef(null);
  const animRef = useRef(null);
  const nodesRef = useRef([]);
  
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let w, h;
    
    const resize = () => {
      w = canvas.width = window.innerWidth;
      h = canvas.height = window.innerHeight;
    };
    resize();
    window.addEventListener('resize', resize);
    
    // Create nodes
    const nodeCount = 80;
    const nodes = [];
    for (let i = 0; i < nodeCount; i++) {
      nodes.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
        radius: Math.random() * 2 + 1,
        pulse: Math.random() * Math.PI * 2,
      });
    }
    nodesRef.current = nodes;
    
    const maxDist = 180;
    
    const animate = () => {
      ctx.clearRect(0, 0, w, h);
      
      // Update & draw nodes
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        n.x += n.vx;
        n.y += n.vy;
        n.pulse += 0.02;
        
        if (n.x < 0 || n.x > w) n.vx *= -1;
        if (n.y < 0 || n.y > h) n.vy *= -1;
        
        const pulseSize = n.radius + Math.sin(n.pulse) * 0.5;
        
        // Glow
        ctx.beginPath();
        ctx.arc(n.x, n.y, pulseSize * 3, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(99, 102, 241, ${0.05 + Math.sin(n.pulse) * 0.03})`;
        ctx.fill();
        
        // Core
        ctx.beginPath();
        ctx.arc(n.x, n.y, pulseSize, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(139, 92, 246, ${0.6 + Math.sin(n.pulse) * 0.3})`;
        ctx.fill();
      }
      
      // Draw connections
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < maxDist) {
            const alpha = (1 - dist / maxDist) * 0.15;
            ctx.beginPath();
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(nodes[j].x, nodes[j].y);
            ctx.strokeStyle = `rgba(99, 102, 241, ${alpha})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }
      
      // Data flow particles along connections
      const time = Date.now() * 0.001;
      for (let i = 0; i < Math.min(nodes.length, 20); i++) {
        const j = (i + 7) % nodes.length;
        const dx = nodes[j].x - nodes[i].x;
        const dy = nodes[j].y - nodes[i].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < maxDist) {
          const t = ((time * 0.5 + i * 0.1) % 1);
          const px = nodes[i].x + dx * t;
          const py = nodes[i].y + dy * t;
          ctx.beginPath();
          ctx.arc(px, py, 1.5, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(34, 211, 238, ${0.8 - t * 0.6})`;
          ctx.fill();
        }
      }
      
      animRef.current = requestAnimationFrame(animate);
    };
    
    animate();
    
    return () => {
      window.removeEventListener('resize', resize);
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, []);
  
  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        zIndex: 0,
        pointerEvents: 'none',
      }}
    />
  );
}

// ═══════════════════════════════════════════════
// Animated Counter
// ═══════════════════════════════════════════════
function AnimatedCounter({ end, suffix = '', duration = 2000 }) {
  const [count, setCount] = useState(0);
  const ref = useRef(null);
  const started = useRef(false);
  
  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !started.current) {
          started.current = true;
          const startTime = Date.now();
          const tick = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            setCount(Math.floor(eased * end));
            if (progress < 1) requestAnimationFrame(tick);
          };
          tick();
        }
      },
      { threshold: 0.3 }
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, [end, duration]);
  
  return <span ref={ref}>{count.toLocaleString()}{suffix}</span>;
}

// ═══════════════════════════════════════════════
// Timeline Animation (Video Editing UI)
// ═══════════════════════════════════════════════
function TimelineUI() {
  const [progress, setProgress] = useState(0);
  
  useEffect(() => {
    const interval = setInterval(() => {
      setProgress(p => (p + 0.3) % 100);
    }, 50);
    return () => clearInterval(interval);
  }, []);
  
  const clips = useMemo(() => [
    { start: 5, width: 15, color: '#8b5cf6', label: 'Hook' },
    { start: 22, width: 20, color: '#06b6d4', label: '商品紹介' },
    { start: 45, width: 12, color: '#10b981', label: 'CTA' },
    { start: 60, width: 18, color: '#f59e0b', label: '実演' },
    { start: 82, width: 14, color: '#ec4899', label: 'クロージング' },
  ], []);
  
  const waveform = useMemo(() => {
    const bars = [];
    for (let i = 0; i < 120; i++) {
      bars.push(Math.random() * 0.8 + 0.2);
    }
    return bars;
  }, []);
  
  return (
    <div style={{
      background: 'rgba(15, 15, 35, 0.9)',
      border: '1px solid rgba(99, 102, 241, 0.3)',
      borderRadius: '12px',
      padding: '20px',
      backdropFilter: 'blur(10px)',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Scanline effect */}
      <div style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: 'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(99, 102, 241, 0.02) 2px, rgba(99, 102, 241, 0.02) 4px)',
        pointerEvents: 'none',
      }} />
      
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
        <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#10b981', boxShadow: '0 0 8px #10b981' }} />
        <span style={{ color: '#94a3b8', fontSize: '12px', fontFamily: 'monospace', letterSpacing: '1px' }}>AI CLIP GENERATOR — PROCESSING</span>
        <span style={{ marginLeft: 'auto', color: '#22d3ee', fontSize: '12px', fontFamily: 'monospace' }}>
          {Math.floor(progress)}% ANALYZED
        </span>
      </div>
      
      {/* Waveform */}
      <div style={{ display: 'flex', alignItems: 'center', height: '40px', gap: '1px', marginBottom: '12px' }}>
        {waveform.map((h, i) => (
          <div key={i} style={{
            flex: 1,
            height: `${h * 100}%`,
            background: i / waveform.length * 100 < progress
              ? 'linear-gradient(to top, #6366f1, #8b5cf6)'
              : 'rgba(99, 102, 241, 0.2)',
            borderRadius: '1px',
            transition: 'background 0.3s',
          }} />
        ))}
      </div>
      
      {/* Timeline tracks */}
      <div style={{ position: 'relative', height: '50px', background: 'rgba(0,0,0,0.3)', borderRadius: '6px', overflow: 'hidden' }}>
        {clips.map((clip, i) => (
          <div key={i} style={{
            position: 'absolute',
            left: `${clip.start}%`,
            width: `${clip.width}%`,
            top: '8px',
            height: '34px',
            background: `${clip.color}33`,
            border: `1px solid ${clip.color}`,
            borderRadius: '4px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '10px',
            color: clip.color,
            fontWeight: '600',
            letterSpacing: '0.5px',
          }}>
            {clip.label}
          </div>
        ))}
        {/* Playhead */}
        <div style={{
          position: 'absolute',
          left: `${progress}%`,
          top: 0,
          bottom: 0,
          width: '2px',
          background: '#22d3ee',
          boxShadow: '0 0 8px #22d3ee',
          transition: 'left 0.05s linear',
        }} />
      </div>
      
      {/* AI Labels */}
      <div style={{ display: 'flex', gap: '8px', marginTop: '12px', flexWrap: 'wrap' }}>
        {['売上ピーク検出', 'フック最適化', '離脱ポイント除去', 'CTA挿入'].map((label, i) => (
          <span key={i} style={{
            padding: '3px 8px',
            fontSize: '10px',
            background: 'rgba(34, 211, 238, 0.1)',
            border: '1px solid rgba(34, 211, 238, 0.3)',
            borderRadius: '4px',
            color: '#22d3ee',
            fontFamily: 'monospace',
          }}>
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════
// 3D Feature Card with holographic border
// ═══════════════════════════════════════════════
function FeatureCard3D({ icon, title, description, color, delay }) {
  const [hovered, setHovered] = useState(false);
  const [visible, setVisible] = useState(false);
  const ref = useRef(null);
  
  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) setVisible(true); },
      { threshold: 0.2 }
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  
  return (
    <div
      ref={ref}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position: 'relative',
        padding: '2px',
        borderRadius: '16px',
        background: hovered
          ? `conic-gradient(from ${Date.now() % 360}deg, ${color}, #6366f1, #22d3ee, ${color})`
          : `linear-gradient(135deg, ${color}44, rgba(99, 102, 241, 0.2))`,
        transform: visible
          ? `perspective(1000px) rotateY(${hovered ? '2deg' : '0deg'}) translateY(0px)`
          : 'perspective(1000px) rotateY(-5deg) translateY(40px)',
        opacity: visible ? 1 : 0,
        transition: `all 0.8s cubic-bezier(0.16, 1, 0.3, 1) ${delay}ms`,
        cursor: 'pointer',
      }}
    >
      <div style={{
        background: 'rgba(10, 10, 30, 0.95)',
        borderRadius: '14px',
        padding: '32px 24px',
        backdropFilter: 'blur(20px)',
        height: '100%',
        position: 'relative',
        overflow: 'hidden',
      }}>
        {/* Glow orb */}
        <div style={{
          position: 'absolute',
          top: '-20px',
          right: '-20px',
          width: '100px',
          height: '100px',
          borderRadius: '50%',
          background: `radial-gradient(circle, ${color}22, transparent)`,
          filter: 'blur(20px)',
        }} />
        
        <div style={{
          fontSize: '40px',
          marginBottom: '16px',
          filter: `drop-shadow(0 0 8px ${color})`,
        }}>
          {icon}
        </div>
        <h3 style={{
          fontSize: '18px',
          fontWeight: '700',
          color: '#fff',
          marginBottom: '12px',
          letterSpacing: '-0.02em',
        }}>
          {title}
        </h3>
        <p style={{
          fontSize: '14px',
          color: '#94a3b8',
          lineHeight: '1.7',
          margin: 0,
        }}>
          {description}
        </p>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════
// Scroll Reveal Section
// ═══════════════════════════════════════════════
function RevealSection({ children, delay = 0 }) {
  const [visible, setVisible] = useState(false);
  const ref = useRef(null);
  
  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) setVisible(true); },
      { threshold: 0.15 }
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  
  return (
    <div
      ref={ref}
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateY(0)' : 'translateY(60px)',
        transition: `all 1s cubic-bezier(0.16, 1, 0.3, 1) ${delay}ms`,
      }}
    >
      {children}
    </div>
  );
}

// ═══════════════════════════════════════════════
// Main Landing Page
// ═══════════════════════════════════════════════
export default function LandingPage() {
  const navigate = useNavigate();
  const [scrollY, setScrollY] = useState(0);
  
  useEffect(() => {
    const handleScroll = () => setScrollY(window.scrollY);
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);
  
  const goRegister = () => navigate('/register');
  const scrollToUpload = () => document.getElementById('video-upload-cta')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const goLogin = () => navigate('/login');
  
  return (
    <div className="lp-root" style={{
      position: 'fixed',
      top: 0,
      left: 0,
      width: '100vw',
      height: '100vh',
      background: '#050510',
      color: '#fff',
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
      overflowX: 'hidden',
      overflowY: 'auto',
      zIndex: 9999,
    }}>
      <NeuralNetworkBg />
      
      {/* Grid overlay */}
      <div style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundImage: `
          linear-gradient(rgba(99, 102, 241, 0.03) 1px, transparent 1px),
          linear-gradient(90deg, rgba(99, 102, 241, 0.03) 1px, transparent 1px)
        `,
        backgroundSize: '60px 60px',
        zIndex: 1,
        pointerEvents: 'none',
      }} />
      
      {/* Radial gradient overlays */}
      <div style={{
        position: 'fixed',
        top: '-30%',
        left: '-10%',
        width: '60%',
        height: '60%',
        borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(99, 102, 241, 0.08), transparent 70%)',
        zIndex: 1,
        pointerEvents: 'none',
        transform: `translateY(${scrollY * 0.1}px)`,
      }} />
      <div style={{
        position: 'fixed',
        bottom: '-20%',
        right: '-10%',
        width: '50%',
        height: '50%',
        borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(34, 211, 238, 0.06), transparent 70%)',
        zIndex: 1,
        pointerEvents: 'none',
        transform: `translateY(${-scrollY * 0.05}px)`,
      }} />
      
      {/* Content */}
      <div style={{ position: 'relative', zIndex: 2 }}>
        
        {/* ═══ HEADER ═══ */}
        <header style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          zIndex: 100,
          padding: '16px 40px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: scrollY > 50 ? 'rgba(5, 5, 16, 0.85)' : 'transparent',
          backdropFilter: scrollY > 50 ? 'blur(20px)' : 'none',
          borderBottom: scrollY > 50 ? '1px solid rgba(99, 102, 241, 0.1)' : 'none',
          transition: 'all 0.3s ease',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{
              width: '36px',
              height: '36px',
              borderRadius: '10px',
              background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: '800',
              fontSize: '16px',
              boxShadow: '0 0 20px rgba(99, 102, 241, 0.4)',
            }}>A</div>
            <span style={{ fontWeight: '700', fontSize: '18px', letterSpacing: '-0.02em' }}>AitherHub</span>
            <span style={{ fontSize: '11px', color: '#64748b', marginLeft: '4px' }}>アイザーハブ</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <button
              onClick={goLogin}
              style={{
                background: 'none',
                border: '1px solid rgba(255,255,255,0.2)',
                color: '#fff',
                padding: '8px 20px',
                borderRadius: '8px',
                fontSize: '14px',
                cursor: 'pointer',
                transition: 'all 0.2s',
              }}
              onMouseEnter={e => { e.target.style.borderColor = '#6366f1'; e.target.style.color = '#a5b4fc'; }}
              onMouseLeave={e => { e.target.style.borderColor = 'rgba(255,255,255,0.2)'; e.target.style.color = '#fff'; }}
            >
              ログイン
            </button>
            <button
              onClick={scrollToUpload}
              style={{
                background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                border: 'none',
                color: '#fff',
                padding: '8px 24px',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: '600',
                cursor: 'pointer',
                boxShadow: '0 4px 20px rgba(99, 102, 241, 0.4)',
                transition: 'all 0.2s',
              }}
              onMouseEnter={e => { e.target.style.transform = 'translateY(-1px)'; e.target.style.boxShadow = '0 6px 30px rgba(99, 102, 241, 0.6)'; }}
              onMouseLeave={e => { e.target.style.transform = 'translateY(0)'; e.target.style.boxShadow = '0 4px 20px rgba(99, 102, 241, 0.4)'; }}
            >
              無料で始める
            </button>
          </div>
        </header>
        
        {/* ═══ HERO ═══ */}
        <section style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '120px 24px 80px',
          textAlign: 'center',
        }}>
          {/* Badge */}
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '6px 16px',
            borderRadius: '100px',
            background: 'rgba(99, 102, 241, 0.1)',
            border: '1px solid rgba(99, 102, 241, 0.3)',
            marginBottom: '32px',
            animation: 'pulse-border 3s infinite',
          }}>
            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#22d3ee', boxShadow: '0 0 8px #22d3ee', animation: 'blink 2s infinite' }} />
            <span style={{ fontSize: '13px', color: '#a5b4fc', letterSpacing: '0.5px' }}>世界初 — ライブコマース全工程AI化プラットフォーム</span>
          </div>
          
          {/* Main heading */}
          <h1 style={{
            fontSize: 'clamp(36px, 6vw, 72px)',
            fontWeight: '800',
            lineHeight: '1.1',
            marginBottom: '24px',
            letterSpacing: '-0.03em',
          }}>
            <span style={{ display: 'block', color: '#fff' }}>映像で売るなら、</span>
            <span style={{
              display: 'block',
              background: 'linear-gradient(135deg, #a78bfa, #6366f1, #22d3ee)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              filter: 'drop-shadow(0 0 30px rgba(99, 102, 241, 0.3))',
            }}>
              AitherHub.
            </span>
          </h1>
          
          {/* Subheading */}
          <p style={{
            fontSize: 'clamp(15px, 2vw, 18px)',
            color: '#94a3b8',
            maxWidth: '600px',
            lineHeight: '1.8',
            marginBottom: '12px',
          }}>
            日本No.1ライブコマース企業の実データから生まれた、
          </p>
          <p style={{
            fontSize: 'clamp(16px, 2.2vw, 20px)',
            color: '#e2e8f0',
            fontWeight: '600',
            marginBottom: '8px',
          }}>
            世界で最も賢い「販売AI頭脳」。
          </p>
          <p style={{
            fontSize: 'clamp(14px, 1.8vw, 16px)',
            color: '#64748b',
            marginBottom: '48px',
          }}>
            「なぜ売れたか」を解析し、「売れる映像」を自動で量産する。
          </p>
          
          {/* CTA buttons */}
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', justifyContent: 'center' }}>
            <button
              onClick={scrollToUpload}
              style={{
                background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                border: 'none',
                color: '#fff',
                padding: '14px 36px',
                borderRadius: '12px',
                fontSize: '16px',
                fontWeight: '700',
                cursor: 'pointer',
                boxShadow: '0 8px 40px rgba(99, 102, 241, 0.4), inset 0 1px 0 rgba(255,255,255,0.1)',
                transition: 'all 0.3s',
                letterSpacing: '0.02em',
              }}
              onMouseEnter={e => { e.target.style.transform = 'translateY(-2px) scale(1.02)'; e.target.style.boxShadow = '0 12px 50px rgba(99, 102, 241, 0.6)'; }}
              onMouseLeave={e => { e.target.style.transform = 'translateY(0) scale(1)'; e.target.style.boxShadow = '0 8px 40px rgba(99, 102, 241, 0.4)'; }}
            >
              無料で始める
            </button>
            <button
              onClick={() => document.getElementById('features')?.scrollIntoView({ behavior: 'smooth' })}
              style={{
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.15)',
                color: '#e2e8f0',
                padding: '14px 36px',
                borderRadius: '12px',
                fontSize: '16px',
                fontWeight: '500',
                cursor: 'pointer',
                backdropFilter: 'blur(10px)',
                transition: 'all 0.3s',
              }}
              onMouseEnter={e => { e.target.style.borderColor = '#6366f1'; e.target.style.background = 'rgba(99, 102, 241, 0.1)'; }}
              onMouseLeave={e => { e.target.style.borderColor = 'rgba(255,255,255,0.15)'; e.target.style.background = 'rgba(255,255,255,0.05)'; }}
            >
              機能を見る ↓
            </button>
          </div>
          
          {/* Scroll indicator */}
          <div style={{
            position: 'absolute',
            bottom: '40px',
            left: '50%',
            transform: 'translateX(-50%)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '8px',
            opacity: 0.5,
            animation: 'float 3s ease-in-out infinite',
          }}>
            <span style={{ fontSize: '11px', color: '#64748b', letterSpacing: '2px' }}>SCROLL</span>
            <div style={{ width: '1px', height: '30px', background: 'linear-gradient(to bottom, #6366f1, transparent)' }} />
          </div>
        </section>
        
        {/* ═══ AI VIDEO EDITOR DEMO ═══ */}
        <section style={{ padding: '80px 24px', maxWidth: '1000px', margin: '0 auto' }}>
          <RevealSection>
            <div style={{ textAlign: 'center', marginBottom: '40px' }}>
              <span style={{ fontSize: '12px', color: '#22d3ee', letterSpacing: '2px', fontFamily: 'monospace' }}>REAL-TIME AI PROCESSING</span>
              <h2 style={{ fontSize: 'clamp(24px, 4vw, 36px)', fontWeight: '700', marginTop: '12px', letterSpacing: '-0.02em' }}>
                AIが動画を自動編集する、その瞬間。
              </h2>
              <p style={{ color: '#64748b', fontSize: '14px', marginTop: '12px', maxWidth: '600px', margin: '12px auto 0' }}>
                アップロードするだけ。無音カット、字幕生成、フック最適化——すべてAIが6倍速で処理。
              </p>
            </div>
            <AIVideoEditorDemo />
          </RevealSection>
        </section>
        
        {/* ═══ VIDEO UPLOAD CTA ═══ */}
        <section style={{ padding: '80px 24px' }}>
          <RevealSection>
            <VideoUploadCTA />
          </RevealSection>
        </section>
        
        {/* ═══ AUTHORITY ═══ */}
        <section style={{ padding: '80px 24px' }}>
          <RevealSection>
            <div style={{
              maxWidth: '900px',
              margin: '0 auto',
              textAlign: 'center',
              padding: '48px 32px',
              borderRadius: '20px',
              background: 'rgba(99, 102, 241, 0.05)',
              border: '1px solid rgba(99, 102, 241, 0.15)',
              backdropFilter: 'blur(10px)',
              position: 'relative',
              overflow: 'hidden',
            }}>
              {/* Corner accents */}
              <div style={{ position: 'absolute', top: '0', left: '0', width: '40px', height: '40px', borderTop: '2px solid #6366f1', borderLeft: '2px solid #6366f1', borderRadius: '4px 0 0 0' }} />
              <div style={{ position: 'absolute', top: '0', right: '0', width: '40px', height: '40px', borderTop: '2px solid #6366f1', borderRight: '2px solid #6366f1', borderRadius: '0 4px 0 0' }} />
              <div style={{ position: 'absolute', bottom: '0', left: '0', width: '40px', height: '40px', borderBottom: '2px solid #6366f1', borderLeft: '2px solid #6366f1', borderRadius: '0 0 0 4px' }} />
              <div style={{ position: 'absolute', bottom: '0', right: '0', width: '40px', height: '40px', borderBottom: '2px solid #6366f1', borderRight: '2px solid #6366f1', borderRadius: '0 0 4px 0' }} />
              
              <div style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                padding: '6px 16px',
                borderRadius: '100px',
                background: 'rgba(245, 158, 11, 0.1)',
                border: '1px solid rgba(245, 158, 11, 0.3)',
                marginBottom: '24px',
              }}>
                <span style={{ fontSize: '14px' }}>★</span>
                <span style={{ fontSize: '13px', color: '#fbbf24' }}>日本No.1 ライブコマース企業「ライブコマースジャパン」監修</span>
              </div>
              
              <h2 style={{ fontSize: 'clamp(22px, 3.5vw, 34px)', fontWeight: '700', lineHeight: '1.4', marginBottom: '16px', letterSpacing: '-0.02em' }}>
                数千時間の実配信データで鍛えた、<br />
                <span style={{ color: '#a78bfa' }}>世界で最も賢い販売AI。</span>
              </h2>
              <p style={{ color: '#94a3b8', fontSize: '15px', lineHeight: '1.8', maxWidth: '700px', margin: '0 auto' }}>
                汎用AIツールとは違う。実際に「売れた瞬間」「客が離れた瞬間」「購買が跳ねたフレーズ」——
                すべてのリアルデータから学習した、ライブコマース専用のAI頭脳。
              </p>
            </div>
          </RevealSection>
          
          {/* Stats */}
          <div style={{
            maxWidth: '900px',
            margin: '48px auto 0',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
            gap: '24px',
          }}>
            {[
              { value: 4000, suffix: '+', label: '生成クリップ数', color: '#a78bfa' },
              { value: 2000, suffix: '+', label: '分析済み配信時間(h)', color: '#22d3ee' },
              { value: 135, suffix: '%', label: '平均売上向上率', color: '#10b981' },
              { value: 12, suffix: '秒', label: 'クリップ生成速度', color: '#f59e0b' },
            ].map((stat, i) => (
              <RevealSection key={i} delay={i * 100}>
                <div style={{
                  textAlign: 'center',
                  padding: '24px 16px',
                  borderRadius: '12px',
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}>
                  <div style={{ fontSize: '36px', fontWeight: '800', color: stat.color, fontFamily: 'monospace' }}>
                    <AnimatedCounter end={stat.value} suffix={stat.suffix} />
                  </div>
                  <div style={{ fontSize: '13px', color: '#64748b', marginTop: '8px' }}>{stat.label}</div>
                </div>
              </RevealSection>
            ))}
          </div>
        </section>
        
        {/* ═══ FEATURES ═══ */}
        <section id="features" style={{ padding: '100px 24px' }}>
          <RevealSection>
            <div style={{ textAlign: 'center', marginBottom: '60px' }}>
              <span style={{ fontSize: '12px', color: '#22d3ee', letterSpacing: '2px', fontFamily: 'monospace' }}>AI MODULES</span>
              <h2 style={{ fontSize: 'clamp(26px, 4vw, 40px)', fontWeight: '700', marginTop: '12px', letterSpacing: '-0.02em' }}>
                4つのAIが、売上を自動で最大化する。
              </h2>
              <p style={{ color: '#64748b', marginTop: '12px', fontSize: '15px' }}>配信 → 分析 → 生成 → 配信。すべてAIが回す。</p>
            </div>
          </RevealSection>
          
          <div style={{
            maxWidth: '1000px',
            margin: '0 auto',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: '24px',
          }}>
            <FeatureCard3D
              icon="📊"
              title="AI分析エンジン"
              description="配信動画をAIが自動解析。売れた瞬間、離脱ポイント、最適なフック——すべてを数値化。なぜ売れたかが、一目でわかる。"
              color="#6366f1"
              delay={0}
            />
            <FeatureCard3D
              icon="⚡"
              title="AIクリップ自動生成"
              description="1本の配信から、売れるショート動画を30本以上自動生成。字幕・ズーム・CTA最適化まで全自動。"
              color="#22d3ee"
              delay={150}
            />
            <FeatureCard3D
              icon="🎬"
              title="AI映像編集"
              description="台本生成、Face Swap、音声変換——プロの映像制作をAIが代替。顔出し不要で、あなたのクローンが売り続ける。"
              color="#10b981"
              delay={300}
            />
            <FeatureCard3D
              icon="🛰"
              title="AI自動配信"
              description="AIライバーが24時間365日、あなたの代わりに配信。デジタルヒューマンが商品を売り続ける、完全自動化の未来。"
              color="#f59e0b"
              delay={450}
            />
          </div>
        </section>
        
        {/* ═══ 3 STEPS ═══ */}
        <section style={{ padding: '100px 24px' }}>
          <RevealSection>
            <div style={{ textAlign: 'center', marginBottom: '60px' }}>
              <span style={{ fontSize: '12px', color: '#22d3ee', letterSpacing: '2px', fontFamily: 'monospace' }}>WORKFLOW</span>
              <h2 style={{ fontSize: 'clamp(26px, 4vw, 40px)', fontWeight: '700', marginTop: '12px', letterSpacing: '-0.02em' }}>
                3ステップで、売上が変わる。
              </h2>
            </div>
          </RevealSection>
          
          <div style={{ maxWidth: '900px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '32px' }}>
            {[
              { num: '01', title: '配信動画を入れる', desc: 'ライブ配信のURLを貼るだけ。または動画ファイルをアップロード。', color: '#6366f1' },
              { num: '02', title: 'AIが全自動で処理', desc: '分析 → 最適区間抽出 → 字幕生成 → エフェクト追加 → クリップ完成。', color: '#22d3ee' },
              { num: '03', title: '売れる動画が量産', desc: 'TikTok、Instagram、YouTube Shorts——各プラットフォームに最適化されたクリップが完成。', color: '#10b981' },
            ].map((step, i) => (
              <RevealSection key={i} delay={i * 150}>
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '24px',
                  padding: '32px',
                  borderRadius: '16px',
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.06)',
                  backdropFilter: 'blur(10px)',
                  position: 'relative',
                  overflow: 'hidden',
                }}>
                  {/* Step number */}
                  <div style={{
                    fontSize: '48px',
                    fontWeight: '900',
                    color: step.color,
                    opacity: 0.3,
                    fontFamily: 'monospace',
                    minWidth: '80px',
                    textAlign: 'center',
                  }}>
                    {step.num}
                  </div>
                  <div>
                    <h3 style={{ fontSize: '20px', fontWeight: '700', marginBottom: '8px' }}>{step.title}</h3>
                    <p style={{ color: '#94a3b8', fontSize: '14px', lineHeight: '1.6', margin: 0 }}>{step.desc}</p>
                  </div>
                  {/* Connecting line */}
                  {i < 2 && (
                    <div style={{
                      position: 'absolute',
                      bottom: '-32px',
                      left: '56px',
                      width: '2px',
                      height: '32px',
                      background: `linear-gradient(to bottom, ${step.color}44, transparent)`,
                      zIndex: 1,
                    }} />
                  )}
                </div>
              </RevealSection>
            ))}
          </div>
        </section>
        
        {/* ═══ COMPARISON ═══ */}
        <section style={{ padding: '100px 24px' }}>
          <RevealSection>
            <div style={{ textAlign: 'center', marginBottom: '60px' }}>
              <span style={{ fontSize: '12px', color: '#22d3ee', letterSpacing: '2px', fontFamily: 'monospace' }}>WHY AITHERHUB</span>
              <h2 style={{ fontSize: 'clamp(24px, 4vw, 38px)', fontWeight: '700', marginTop: '12px', letterSpacing: '-0.02em' }}>
                なぜAitherHubだけが「売れる」のか。
              </h2>
            </div>
          </RevealSection>
          
          <div style={{ maxWidth: '900px', margin: '0 auto', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(350px, 1fr))', gap: '24px' }}>
            <RevealSection delay={0}>
              <div style={{
                padding: '32px',
                borderRadius: '16px',
                background: 'rgba(239, 68, 68, 0.03)',
                border: '1px solid rgba(239, 68, 68, 0.15)',
                height: '100%',
              }}>
                <h3 style={{ color: '#ef4444', fontSize: '16px', fontWeight: '700', marginBottom: '20px' }}>✕ 他のAIツール</h3>
                <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {[
                    '汎用AI。ライブコマースのデータを持っていない',
                    '「切り抜き」はできるが「売れる切り抜き」は分からない',
                    '分析と生成が別ツール。ワークフローが分断',
                    '配信の最適化は人間任せ',
                  ].map((item, i) => (
                    <li key={i} style={{ color: '#94a3b8', fontSize: '14px', lineHeight: '1.6', paddingLeft: '20px', position: 'relative' }}>
                      <span style={{ position: 'absolute', left: 0, color: '#ef4444' }}>—</span>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </RevealSection>
            
            <RevealSection delay={200}>
              <div style={{
                padding: '32px',
                borderRadius: '16px',
                background: 'rgba(99, 102, 241, 0.05)',
                border: '1px solid rgba(99, 102, 241, 0.3)',
                height: '100%',
                position: 'relative',
                overflow: 'hidden',
              }}>
                <div style={{ position: 'absolute', top: '-30px', right: '-30px', width: '100px', height: '100px', borderRadius: '50%', background: 'radial-gradient(circle, rgba(99, 102, 241, 0.15), transparent)', filter: 'blur(20px)' }} />
                <h3 style={{ color: '#a78bfa', fontSize: '16px', fontWeight: '700', marginBottom: '20px' }}>✓ AitherHub</h3>
                <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {[
                    { text: '実売上データで学習。「売れるパターン」を知っている', highlight: '実売上データ' },
                    { text: '「なぜ売れたか」を解析し、その法則で動画を生成', highlight: 'なぜ売れたか' },
                    { text: '分析→生成→配信が一気通貫', highlight: '一気通貫' },
                    { text: 'AIが24時間、売上を最適化し続ける', highlight: '24時間' },
                  ].map((item, i) => (
                    <li key={i} style={{ color: '#e2e8f0', fontSize: '14px', lineHeight: '1.6', paddingLeft: '20px', position: 'relative' }}>
                      <span style={{ position: 'absolute', left: 0, color: '#22d3ee' }}>✓</span>
                      {item.text}
                    </li>
                  ))}
                </ul>
              </div>
            </RevealSection>
          </div>
        </section>
        
        {/* ═══ PRICING ═══ */}
        <section style={{ padding: '100px 24px' }}>
          <RevealSection>
            <div style={{ textAlign: 'center', marginBottom: '60px' }}>
              <span style={{ fontSize: '12px', color: '#22d3ee', letterSpacing: '2px', fontFamily: 'monospace' }}>PRICING</span>
              <h2 style={{ fontSize: 'clamp(26px, 4vw, 40px)', fontWeight: '700', marginTop: '12px', letterSpacing: '-0.02em' }}>料金プラン</h2>
              <p style={{ color: '#64748b', marginTop: '12px', fontSize: '15px' }}>まずは無料で、AIの実力を体感してください。</p>
              <p style={{ color: '#22d3ee', marginTop: '8px', fontSize: '13px', fontWeight: '500' }}>No credit card required</p>
            </div>
          </RevealSection>
          
          <div style={{
            maxWidth: '1100px',
            margin: '0 auto',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
            gap: '20px',
          }}>
            {[
              { name: 'Free', price: '$0', period: '', desc: 'まずは試してみたい方', features: ['動画解析 1本/月', '基本字幕生成', '商品タグ付け（1商品）', 'AI売上レポート（簡易版）'], popular: false, cta: '無料で始める' },
              { name: 'Growth', price: '$29', period: '/mo', desc: '個人ライバー・副業', features: ['動画解析 15本/月', 'AI字幕カスタマイズ', '商品×売上 相関レポート', 'フェーズ自動分割', '売れるクリップ自動生成'], popular: false, cta: 'Start Growth' },
              { name: 'Pro', price: '$79', period: '/mo', desc: 'プロライバー・中小EC', features: ['動画解析 無制限', '全AI機能フルアクセス', 'リアルタイムライブ分析', '売れる台本AI生成', '優先処理（2x速度）', 'チーム2名まで'], popular: true, cta: 'Start Pro' },
              { name: 'Enterprise', price: 'Custom', period: '', desc: 'MCN・大手EC', features: ['全機能 + カスタムAIモデル', 'マルチアカウント管理', '専任サポート & SLA', 'API アクセス', 'オンボーディング支援'], popular: false, cta: 'Contact Us' },
            ].map((plan, i) => (
              <RevealSection key={i} delay={i * 100}>
                <div style={{
                  padding: plan.popular ? '3px' : '1px',
                  borderRadius: '16px',
                  background: plan.popular
                    ? 'linear-gradient(135deg, #6366f1, #22d3ee, #8b5cf6)'
                    : 'rgba(255,255,255,0.08)',
                  height: '100%',
                }}>
                  <div style={{
                    background: '#0a0a1e',
                    borderRadius: plan.popular ? '14px' : '15px',
                    padding: '28px 24px',
                    height: '100%',
                    display: 'flex',
                    flexDirection: 'column',
                    position: 'relative',
                  }}>
                    {plan.popular && (
                      <div style={{
                        position: 'absolute',
                        top: '-12px',
                        left: '50%',
                        transform: 'translateX(-50%)',
                        padding: '4px 12px',
                        borderRadius: '100px',
                        background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                        fontSize: '11px',
                        fontWeight: '700',
                        letterSpacing: '1px',
                      }}>
                        MOST POPULAR
                      </div>
                    )}
                    <h3 style={{ fontSize: '18px', fontWeight: '600', color: '#e2e8f0', marginBottom: '4px' }}>{plan.name}</h3>
                    <p style={{ fontSize: '12px', color: '#64748b', marginBottom: '16px' }}>{plan.desc}</p>
                    <div style={{ marginBottom: '20px' }}>
                      <span style={{ fontSize: '36px', fontWeight: '800', color: '#fff' }}>{plan.price}</span>
                      <span style={{ color: '#64748b', fontSize: '14px' }}>{plan.period}</span>
                    </div>
                    <ul style={{ listStyle: 'none', padding: 0, margin: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: '10px' }}>
                      {plan.features.map((f, j) => (
                        <li key={j} style={{ fontSize: '13px', color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ color: '#22d3ee', fontSize: '12px' }}>✓</span> {f}
                        </li>
                      ))}
                    </ul>
                    <button
                      onClick={goRegister}
                      style={{
                        marginTop: '24px',
                        width: '100%',
                        padding: '12px',
                        borderRadius: '8px',
                        border: plan.popular ? 'none' : '1px solid rgba(255,255,255,0.15)',
                        background: plan.popular ? 'linear-gradient(135deg, #6366f1, #8b5cf6)' : 'transparent',
                        color: '#fff',
                        fontSize: '14px',
                        fontWeight: '600',
                        cursor: 'pointer',
                        transition: 'all 0.2s',
                      }}
                      onMouseEnter={e => { e.target.style.transform = 'translateY(-1px)'; }}
                      onMouseLeave={e => { e.target.style.transform = 'translateY(0)'; }}
                    >
                      {plan.cta}
                    </button>
                  </div>
                </div>
              </RevealSection>
            ))}
          </div>
        </section>
        
        {/* ═══ FINAL CTA ═══ */}
        <section style={{ padding: '120px 24px', textAlign: 'center' }}>
          <RevealSection>
            <h2 style={{
              fontSize: 'clamp(28px, 4.5vw, 44px)',
              fontWeight: '800',
              lineHeight: '1.3',
              marginBottom: '20px',
              letterSpacing: '-0.02em',
            }}>
              売れる理由を、<br />
              <span style={{
                background: 'linear-gradient(135deg, #a78bfa, #22d3ee)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
              }}>
                今すぐ知る。
              </span>
            </h2>
            <p style={{ color: '#94a3b8', fontSize: '16px', marginBottom: '40px', lineHeight: '1.8' }}>
              無料プランで、AIの実力を体感してください。<br />
              クレジットカード不要。30秒で開始。
            </p>
            <button
              onClick={scrollToUpload}
              style={{
                background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                border: 'none',
                color: '#fff',
                padding: '16px 48px',
                borderRadius: '12px',
                fontSize: '18px',
                fontWeight: '700',
                cursor: 'pointer',
                boxShadow: '0 8px 40px rgba(99, 102, 241, 0.5)',
                transition: 'all 0.3s',
              }}
              onMouseEnter={e => { e.target.style.transform = 'translateY(-3px) scale(1.02)'; e.target.style.boxShadow = '0 16px 60px rgba(99, 102, 241, 0.6)'; }}
              onMouseLeave={e => { e.target.style.transform = 'translateY(0) scale(1)'; e.target.style.boxShadow = '0 8px 40px rgba(99, 102, 241, 0.5)'; }}
            >
              無料で動画を解析する
            </button>
          </RevealSection>
        </section>
        
        {/* ═══ FOOTER ═══ */}
        <footer style={{
          padding: '40px 24px',
          borderTop: '1px solid rgba(255,255,255,0.05)',
          textAlign: 'center',
        }}>
          <div style={{ display: 'flex', justifyContent: 'center', gap: '24px', marginBottom: '16px' }}>
            <a href="/privacy-policy" style={{ color: '#64748b', fontSize: '13px', textDecoration: 'none' }}>プライバシーポリシー</a>
            <a href="mailto:support@aitherhub.com" style={{ color: '#64748b', fontSize: '13px', textDecoration: 'none' }}>お問い合わせ</a>
          </div>
          <p style={{ color: '#334155', fontSize: '12px' }}>© 2025 AitherHub. All rights reserved.</p>
        </footer>
      </div>
      
      {/* Global CSS animations */}
      <style>{`
        @keyframes float {
          0%, 100% { transform: translateX(-50%) translateY(0); }
          50% { transform: translateX(-50%) translateY(-8px); }
        }
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
        @keyframes pulse-border {
          0%, 100% { box-shadow: 0 0 0 0 rgba(99, 102, 241, 0.2); }
          50% { box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.1); }
        }
        .lp-root, .lp-root * { box-sizing: border-box; margin: 0; padding: 0; }
        .lp-root { scroll-behavior: smooth; }
        @media (max-width: 768px) {
          .lp-root section { padding-left: 16px !important; padding-right: 16px !important; }
        }
      `}</style>
    </div>
  );
}
