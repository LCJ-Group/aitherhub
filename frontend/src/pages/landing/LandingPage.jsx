/**
 * AitherHub Landing Page
 * Design: Cyberpunk × Neural Network × Future Tech
 * Dark base + Neon glow (cyan/purple) + Canvas 2D particles + scroll animations
 * No Three.js dependency - uses Canvas 2D for React 19 compatibility
 */
import { useRef, useEffect, useState, useCallback } from 'react';
import { motion, useScroll, useTransform, useInView } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

// ─── Canvas 2D Neural Particle Field ───
function NeuralCanvas() {
  const canvasRef = useRef(null);
  const animRef = useRef(null);
  const particlesRef = useRef([]);
  const mouseRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let width = canvas.parentElement.offsetWidth;
    let height = canvas.parentElement.offsetHeight;
    canvas.width = width;
    canvas.height = height;

    // Create particles
    const PARTICLE_COUNT = 150;
    const particles = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        radius: Math.random() * 2 + 0.5,
        opacity: Math.random() * 0.5 + 0.2,
        color: Math.random() > 0.5 ? '#00f5ff' : '#a855f7',
      });
    }
    particlesRef.current = particles;

    const handleResize = () => {
      width = canvas.parentElement.offsetWidth;
      height = canvas.parentElement.offsetHeight;
      canvas.width = width;
      canvas.height = height;
    };

    const handleMouseMove = (e) => {
      const rect = canvas.getBoundingClientRect();
      mouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };

    window.addEventListener('resize', handleResize);
    canvas.addEventListener('mousemove', handleMouseMove);

    const animate = () => {
      ctx.clearRect(0, 0, width, height);

      // Update and draw particles
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        p.x += p.vx;
        p.y += p.vy;

        // Bounce off edges
        if (p.x < 0 || p.x > width) p.vx *= -1;
        if (p.y < 0 || p.y > height) p.vy *= -1;

        // Draw particle
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.globalAlpha = p.opacity;
        ctx.fill();

        // Draw connections
        for (let j = i + 1; j < particles.length; j++) {
          const p2 = particles[j];
          const dx = p.x - p2.x;
          const dy = p.y - p2.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 120) {
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(p2.x, p2.y);
            ctx.strokeStyle = p.color;
            ctx.globalAlpha = (1 - dist / 120) * 0.15;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }

        // Mouse interaction
        const mx = mouseRef.current.x - p.x;
        const my = mouseRef.current.y - p.y;
        const mDist = Math.sqrt(mx * mx + my * my);
        if (mDist < 150) {
          p.vx -= mx * 0.00005;
          p.vy -= my * 0.00005;
        }
      }
      ctx.globalAlpha = 1;
      animRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener('resize', handleResize);
      canvas.removeEventListener('mousemove', handleMouseMove);
    };
  }, []);

  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />;
}

// ─── Animated Counter ───
function AnimatedCounter({ end, duration = 2, suffix = '' }) {
  const [count, setCount] = useState(0);
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true });

  useEffect(() => {
    if (!isInView) return;
    let start = 0;
    const increment = end / (duration * 60);
    const timer = setInterval(() => {
      start += increment;
      if (start >= end) {
        setCount(end);
        clearInterval(timer);
      } else {
        setCount(Math.floor(start));
      }
    }, 1000 / 60);
    return () => clearInterval(timer);
  }, [isInView, end, duration]);

  return <span ref={ref}>{count.toLocaleString()}{suffix}</span>;
}

// ─── Section Wrapper with Fade-in ───
function FadeInSection({ children, className = '', delay = 0 }) {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: '-100px' });
  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 60 }}
      animate={isInView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.8, delay, ease: [0.25, 0.46, 0.45, 0.94] }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

// ─── Glowing Card ───
function GlowCard({ icon, title, description, color = 'cyan' }) {
  const colorMap = {
    cyan: 'from-cyan-500/20 to-transparent border-cyan-500/30 hover:border-cyan-400/60',
    purple: 'from-purple-500/20 to-transparent border-purple-500/30 hover:border-purple-400/60',
    green: 'from-emerald-500/20 to-transparent border-emerald-500/30 hover:border-emerald-400/60',
    gold: 'from-amber-500/20 to-transparent border-amber-500/30 hover:border-amber-400/60',
  };
  const glowMap = {
    cyan: 'shadow-cyan-500/20 hover:shadow-cyan-500/40',
    purple: 'shadow-purple-500/20 hover:shadow-purple-500/40',
    green: 'shadow-emerald-500/20 hover:shadow-emerald-500/40',
    gold: 'shadow-amber-500/20 hover:shadow-amber-500/40',
  };
  return (
    <div className={`relative group p-6 rounded-2xl border bg-gradient-to-b ${colorMap[color]} backdrop-blur-sm transition-all duration-500 shadow-lg ${glowMap[color]} hover:scale-[1.02]`}>
      <div className="text-4xl mb-4">{icon}</div>
      <h3 className="text-xl font-bold text-white mb-2">{title}</h3>
      <p className="text-gray-400 text-sm leading-relaxed">{description}</p>
    </div>
  );
}

// ─── Pricing Card ───
function PricingCard({ name, price, period, features, highlighted, cta }) {
  const navigate = useNavigate();
  return (
    <div className={`relative p-6 rounded-2xl border transition-all duration-300 ${
      highlighted
        ? 'border-cyan-500/60 bg-gradient-to-b from-cyan-950/40 to-gray-950/80 shadow-lg shadow-cyan-500/20 scale-105'
        : 'border-gray-700/50 bg-gray-900/50 hover:border-gray-600/60'
    }`}>
      {highlighted && (
        <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-4 py-1 bg-gradient-to-r from-cyan-500 to-purple-500 rounded-full text-xs font-bold text-white">
          MOST POPULAR
        </div>
      )}
      <h3 className="text-lg font-bold text-white mb-1">{name}</h3>
      <div className="flex items-baseline gap-1 mb-4">
        <span className="text-3xl font-black text-white">{price}</span>
        {period && <span className="text-gray-500 text-sm">{period}</span>}
      </div>
      <ul className="space-y-2 mb-6">
        {features.map((f, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-gray-300">
            <span className="text-cyan-400 mt-0.5">&#10003;</span>
            <span>{f}</span>
          </li>
        ))}
      </ul>
      <button
        onClick={() => navigate('/register')}
        className={`w-full py-2.5 rounded-lg font-semibold text-sm transition-all duration-300 ${
          highlighted
            ? 'bg-gradient-to-r from-cyan-500 to-purple-600 text-white hover:opacity-90 shadow-lg shadow-cyan-500/30'
            : 'bg-gray-800 text-gray-300 border border-gray-700 hover:bg-gray-700 hover:text-white'
        }`}
      >
        {cta}
      </button>
    </div>
  );
}

// ─── Floating Orb Animation (CSS-based) ───
function FloatingOrbs() {
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-cyan-500/5 rounded-full blur-[100px] animate-pulse" style={{ animationDuration: '4s' }} />
      <div className="absolute top-1/3 right-1/4 w-80 h-80 bg-purple-600/8 rounded-full blur-[80px] animate-pulse" style={{ animationDuration: '6s', animationDelay: '1s' }} />
      <div className="absolute bottom-1/4 left-1/3 w-72 h-72 bg-cyan-400/5 rounded-full blur-[90px] animate-pulse" style={{ animationDuration: '5s', animationDelay: '2s' }} />
    </div>
  );
}

// ─── Main Landing Page ───
export default function LandingPage() {
  const navigate = useNavigate();
  const { scrollYProgress } = useScroll();
  const heroOpacity = useTransform(scrollYProgress, [0, 0.15], [1, 0]);
  const heroScale = useTransform(scrollYProgress, [0, 0.15], [1, 0.95]);

  return (
    <div className="min-h-screen bg-[#0a0a1a] text-white overflow-x-hidden">
      {/* ─── Navigation ─── */}
      <nav className="fixed top-0 left-0 right-0 z-50 backdrop-blur-md bg-[#0a0a1a]/70 border-b border-white/5">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-400 to-purple-600 flex items-center justify-center font-black text-sm">A</div>
            <span className="font-bold text-lg tracking-tight">AitherHub</span>
            <span className="text-[10px] text-gray-500 ml-1 hidden sm:inline">アイザーハブ</span>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={() => navigate('/login')} className="text-sm text-gray-400 hover:text-white transition-colors px-3 py-1.5">
              ログイン
            </button>
            <button onClick={() => navigate('/register')} className="text-sm bg-gradient-to-r from-cyan-500 to-purple-600 text-white px-4 py-1.5 rounded-lg font-semibold hover:opacity-90 transition-opacity shadow-lg shadow-cyan-500/20">
              無料で始める
            </button>
          </div>
        </div>
      </nav>

      {/* ─── Hero Section ─── */}
      <motion.section style={{ opacity: heroOpacity, scale: heroScale }} className="relative min-h-screen flex items-center justify-center pt-16">
        {/* Canvas Particle Background */}
        <div className="absolute inset-0 z-0">
          <NeuralCanvas />
        </div>
        {/* Floating Orbs */}
        <FloatingOrbs />
        {/* Gradient overlays */}
        <div className="absolute inset-0 bg-gradient-to-b from-transparent via-[#0a0a1a]/30 to-[#0a0a1a] z-[1]" />

        {/* Content */}
        <div className="relative z-10 text-center px-6 max-w-5xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1, delay: 0.2 }}
          >
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-cyan-500/30 bg-cyan-950/30 text-cyan-300 text-xs font-medium mb-8 backdrop-blur-sm">
              <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
              世界初 — ライブコマース全工程AI化プラットフォーム
            </div>
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1, delay: 0.4 }}
            className="text-5xl sm:text-6xl md:text-7xl font-black leading-tight tracking-tight mb-6"
          >
            <span className="bg-gradient-to-r from-white via-gray-100 to-gray-300 bg-clip-text text-transparent">
              映像で売るなら、
            </span>
            <br />
            <span className="bg-gradient-to-r from-cyan-400 via-purple-400 to-cyan-400 bg-clip-text text-transparent animate-gradient-x">
              AitherHub.
            </span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1, delay: 0.6 }}
            className="text-lg sm:text-xl text-gray-400 max-w-2xl mx-auto mb-10 leading-relaxed"
          >
            日本No.1ライブコマース企業の実データから生まれた、
            <br className="hidden sm:block" />
            <span className="text-white font-semibold">世界で最も賢い「販売AI頭脳」。</span>
            <br />
            「なぜ売れたか」を解析し、「売れる映像」を自動で量産する。
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 1, delay: 0.8 }}
            className="flex flex-col sm:flex-row items-center justify-center gap-4"
          >
            <button
              onClick={() => navigate('/register')}
              className="group relative px-8 py-3.5 bg-gradient-to-r from-cyan-500 to-purple-600 rounded-xl font-bold text-white shadow-2xl shadow-cyan-500/30 hover:shadow-cyan-500/50 transition-all duration-300 hover:scale-105"
            >
              <span className="relative z-10">無料で始める</span>
              <div className="absolute inset-0 rounded-xl bg-gradient-to-r from-cyan-400 to-purple-500 opacity-0 group-hover:opacity-100 transition-opacity duration-300 blur-xl" />
            </button>
            <button
              onClick={() => document.getElementById('features')?.scrollIntoView({ behavior: 'smooth' })}
              className="px-8 py-3.5 border border-gray-700 rounded-xl font-semibold text-gray-300 hover:border-gray-500 hover:text-white transition-all duration-300"
            >
              機能を見る ↓
            </button>
          </motion.div>
        </div>

        {/* Scroll indicator */}
        <motion.div
          animate={{ y: [0, 10, 0] }}
          transition={{ repeat: Infinity, duration: 2 }}
          className="absolute bottom-10 left-1/2 -translate-x-1/2 z-10"
        >
          <div className="w-6 h-10 rounded-full border-2 border-gray-600 flex items-start justify-center p-1.5">
            <div className="w-1.5 h-3 rounded-full bg-cyan-400 animate-pulse" />
          </div>
        </motion.div>
      </motion.section>

      {/* ─── Authority Section ─── */}
      <section className="relative py-24 px-6">
        <div className="max-w-5xl mx-auto text-center">
          <FadeInSection>
            <div className="inline-flex items-center gap-3 px-5 py-2 rounded-full border border-amber-500/30 bg-amber-950/20 mb-8">
              <span className="text-amber-400 text-sm font-bold">&#9733;</span>
              <span className="text-amber-200 text-sm font-medium">日本No.1 ライブコマース企業「ライブコマースジャパン」監修</span>
            </div>
          </FadeInSection>
          <FadeInSection delay={0.2}>
            <h2 className="text-3xl sm:text-4xl font-black mb-6">
              <span className="text-white">数千時間の実配信データで鍛えた、</span>
              <br />
              <span className="bg-gradient-to-r from-cyan-400 to-purple-400 bg-clip-text text-transparent">世界で最も賢い販売AI。</span>
            </h2>
          </FadeInSection>
          <FadeInSection delay={0.4}>
            <p className="text-gray-400 max-w-2xl mx-auto leading-relaxed">
              汎用AIツールとは違う。実際に「売れた瞬間」「客が離れた瞬間」「購買が跳ねたフレーズ」——
              すべてのリアルデータから学習した、ライブコマース専用のAI頭脳。
            </p>
          </FadeInSection>
        </div>
      </section>

      {/* ─── Stats Section ─── */}
      <section className="py-16 px-6 border-y border-white/5">
        <div className="max-w-5xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
          {[
            { value: 10000, suffix: '+', label: '生成クリップ数' },
            { value: 5000, suffix: '+', label: '分析済み配信時間(h)' },
            { value: 340, suffix: '%', label: '平均売上向上率' },
            { value: 30, suffix: '秒', label: 'クリップ生成速度' },
          ].map((stat, i) => (
            <FadeInSection key={i} delay={i * 0.1}>
              <div className="text-3xl sm:text-4xl font-black bg-gradient-to-r from-cyan-400 to-purple-400 bg-clip-text text-transparent">
                <AnimatedCounter end={stat.value} suffix={stat.suffix} />
              </div>
              <div className="text-gray-500 text-sm mt-1">{stat.label}</div>
            </FadeInSection>
          ))}
        </div>
      </section>

      {/* ─── Features Section ─── */}
      <section id="features" className="py-24 px-6">
        <div className="max-w-6xl mx-auto">
          <FadeInSection>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-black text-white mb-4">4つのAIが、売上を自動で最大化する。</h2>
              <p className="text-gray-500">配信 → 分析 → 生成 → 配信。すべてAIが回す。</p>
            </div>
          </FadeInSection>
          <div className="grid md:grid-cols-2 gap-6">
            <FadeInSection delay={0.1}>
              <GlowCard
                icon="&#128202;"
                title="AI分析エンジン"
                description="配信動画をAIが自動解析。売れた瞬間、離脱ポイント、最適なフック——すべてを数値化。なぜ売れたかが、一目でわかる。"
                color="cyan"
              />
            </FadeInSection>
            <FadeInSection delay={0.2}>
              <GlowCard
                icon="&#9889;"
                title="AIクリップ自動生成"
                description="1本の配信から、売れるショート動画を30本以上自動生成。字幕・ズーム・CTA最適化まで全自動。"
                color="purple"
              />
            </FadeInSection>
            <FadeInSection delay={0.3}>
              <GlowCard
                icon="&#127916;"
                title="AI映像編集"
                description="台本生成、Face Swap、音声変換——プロの映像制作をAIが代替。顔出し不要で、あなたのクローンが売り続ける。"
                color="green"
              />
            </FadeInSection>
            <FadeInSection delay={0.4}>
              <GlowCard
                icon="&#128752;"
                title="AI自動配信"
                description="AIライバーが24時間365日、あなたの代わりに配信。デジタルヒューマンが商品を売り続ける、完全自動化の未来。"
                color="gold"
              />
            </FadeInSection>
          </div>
        </div>
      </section>

      {/* ─── How it Works ─── */}
      <section className="py-24 px-6 bg-gradient-to-b from-transparent via-cyan-950/5 to-transparent">
        <div className="max-w-5xl mx-auto">
          <FadeInSection>
            <div className="text-center mb-16">
              <h2 className="text-3xl sm:text-4xl font-black text-white mb-4">3ステップで、売上が変わる。</h2>
            </div>
          </FadeInSection>
          <div className="grid md:grid-cols-3 gap-8">
            {[
              { step: '01', title: '配信動画を入れる', desc: 'ライブ配信のURLを貼るだけ。または動画ファイルをアップロード。', color: 'text-cyan-400' },
              { step: '02', title: 'AIが全自動で処理', desc: '分析 → 最適区間抽出 → 字幕生成 → エフェクト追加 → クリップ完成。', color: 'text-purple-400' },
              { step: '03', title: '売れる動画が量産', desc: 'TikTok、Instagram、YouTube Shorts——各プラットフォームに最適化されたクリップが完成。', color: 'text-emerald-400' },
            ].map((item, i) => (
              <FadeInSection key={i} delay={i * 0.15}>
                <div className="text-center">
                  <div className={`text-6xl font-black ${item.color} opacity-30 mb-4`}>{item.step}</div>
                  <h3 className="text-xl font-bold text-white mb-2">{item.title}</h3>
                  <p className="text-gray-500 text-sm leading-relaxed">{item.desc}</p>
                </div>
              </FadeInSection>
            ))}
          </div>
        </div>
      </section>

      {/* ─── Why AitherHub (Differentiation) ─── */}
      <section className="py-24 px-6">
        <div className="max-w-5xl mx-auto">
          <FadeInSection>
            <div className="text-center mb-12">
              <h2 className="text-3xl sm:text-4xl font-black text-white mb-4">なぜAitherHubだけが「売れる」のか。</h2>
            </div>
          </FadeInSection>
          <FadeInSection delay={0.2}>
            <div className="grid md:grid-cols-2 gap-8">
              <div className="p-6 rounded-2xl border border-red-500/20 bg-red-950/10">
                <h3 className="text-lg font-bold text-red-400 mb-4">&#10005; 他のAIツール</h3>
                <ul className="space-y-3 text-gray-400 text-sm">
                  <li>汎用AI。ライブコマースのデータを持っていない</li>
                  <li>「切り抜き」はできるが「売れる切り抜き」は分からない</li>
                  <li>分析と生成が別ツール。ワークフローが分断</li>
                  <li>配信の最適化は人間任せ</li>
                </ul>
              </div>
              <div className="p-6 rounded-2xl border border-cyan-500/30 bg-cyan-950/10">
                <h3 className="text-lg font-bold text-cyan-400 mb-4">&#10003; AitherHub</h3>
                <ul className="space-y-3 text-gray-300 text-sm">
                  <li><span className="text-cyan-400 font-bold">実売上データ</span>で学習。「売れるパターン」を知っている</li>
                  <li>「なぜ売れたか」を解析し、その法則で動画を生成</li>
                  <li>分析→生成→配信が<span className="text-cyan-400 font-bold">一気通貫</span></li>
                  <li>AIが24時間、売上を最適化し続ける</li>
                </ul>
              </div>
            </div>
          </FadeInSection>
        </div>
      </section>

      {/* ─── Pricing Section ─── */}
      <section className="py-24 px-6 bg-gradient-to-b from-transparent via-purple-950/5 to-transparent">
        <div className="max-w-5xl mx-auto">
          <FadeInSection>
            <div className="text-center mb-12">
              <h2 className="text-3xl sm:text-4xl font-black text-white mb-4">料金プラン</h2>
              <p className="text-gray-500">まずは無料で、AIの実力を体感してください。</p>
            </div>
          </FadeInSection>
          <div className="grid md:grid-cols-4 gap-4">
            <FadeInSection delay={0.1}>
              <PricingCard
                name="Free"
                price="$0"
                period=""
                features={['動画分析 3本/月', 'クリップ生成 5本/月', '台本生成 3回/月', 'AI分析レポート']}
                cta="無料で始める"
              />
            </FadeInSection>
            <FadeInSection delay={0.2}>
              <PricingCard
                name="Starter"
                price="$29"
                period="/月"
                features={['動画分析 15本/月', 'クリップ生成 30本/月', '台本生成 無制限', 'TikTok追跡 10本']}
                cta="Starterを始める"
              />
            </FadeInSection>
            <FadeInSection delay={0.3}>
              <PricingCard
                name="Pro"
                price="$79"
                period="/月"
                highlighted
                features={['動画分析 50本/月', 'クリップ生成 100本/月', 'Face Swap 10本/月', 'TikTok追跡 50本', '優先サポート']}
                cta="Proを始める"
              />
            </FadeInSection>
            <FadeInSection delay={0.4}>
              <PricingCard
                name="Business"
                price="$199"
                period="/月"
                features={['動画分析 無制限', 'クリップ生成 無制限', 'Face Swap 30本/月', 'Auto Video 5本/月', 'ブランドポータル']}
                cta="Businessを始める"
              />
            </FadeInSection>
          </div>
        </div>
      </section>

      {/* ─── Final CTA ─── */}
      <section className="py-32 px-6 relative">
        <div className="absolute inset-0 bg-gradient-to-t from-cyan-950/10 via-transparent to-transparent" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-cyan-500/5 rounded-full blur-[100px]" />
        <FadeInSection>
          <div className="relative max-w-3xl mx-auto text-center">
            <h2 className="text-4xl sm:text-5xl font-black text-white mb-6">
              売れる理由を、
              <br />
              <span className="bg-gradient-to-r from-cyan-400 to-purple-400 bg-clip-text text-transparent">今すぐ知る。</span>
            </h2>
            <p className="text-gray-400 mb-10 text-lg">
              無料プランで、AIの実力を体感してください。
              <br />
              クレジットカード不要。30秒で開始。
            </p>
            <button
              onClick={() => navigate('/register')}
              className="group relative px-10 py-4 bg-gradient-to-r from-cyan-500 to-purple-600 rounded-xl font-bold text-lg text-white shadow-2xl shadow-cyan-500/30 hover:shadow-cyan-500/50 transition-all duration-300 hover:scale-105"
            >
              <span className="relative z-10">無料アカウントを作成</span>
              <div className="absolute inset-0 rounded-xl bg-gradient-to-r from-cyan-400 to-purple-500 opacity-0 group-hover:opacity-100 transition-opacity duration-300 blur-xl" />
            </button>
          </div>
        </FadeInSection>
      </section>

      {/* ─── Footer ─── */}
      <footer className="border-t border-white/5 py-12 px-6">
        <div className="max-w-5xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-gradient-to-br from-cyan-400 to-purple-600 flex items-center justify-center font-black text-[10px]">A</div>
            <span className="font-bold text-sm">AitherHub</span>
            <span className="text-[10px] text-gray-600 ml-1">アイザーハブ</span>
          </div>
          <div className="flex items-center gap-6 text-sm text-gray-500">
            <a href="/privacy-policy" className="hover:text-gray-300 transition-colors">プライバシーポリシー</a>
            <a href="mailto:support@aitherhub.com" className="hover:text-gray-300 transition-colors">お問い合わせ</a>
          </div>
          <div className="text-xs text-gray-600">
            &copy; 2026 AitherHub by Live Commerce Japan Inc.
          </div>
        </div>
      </footer>

      {/* ─── Custom CSS for gradient animation ─── */}
      <style>{`
        @keyframes gradient-x {
          0%, 100% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
        }
        .animate-gradient-x {
          background-size: 200% 200%;
          animation: gradient-x 3s ease infinite;
        }
      `}</style>
    </div>
  );
}
