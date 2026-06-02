/**
 * AIEditorMonitor – CapCut/Premiere Pro風 AI動画編集モニター
 *
 * まるで人間がCapCut/Premiere Proで編集しているかのような
 * 「仮想編集画面」をリアルタイムで表示する。
 *
 * Features:
 *   - 大きなプレビューウィンドウ（動画再生 + 編集オーバーレイ）
 *   - リアルなマウスカーソルアニメーション（人間が操作している感）
 *   - タイムライン（カット位置マーカー、再生ヘッド、ハサミアニメーション）
 *   - 波形表示（無音除去ステップ）
 *   - 字幕タイピングアニメーション
 *   - ターミナル風AIログ（文字が流れる）
 *   - 人物検出バウンディングボックス
 *   - 元動画のリアルタイム再生（sourceVideoUrl指定時）
 *
 * Props:
 *   logs            – Array of { ts, pct, step, msg, preview_url? }
 *   progressPct     – Current progress percentage (0-100)
 *   progressStep    – Current step name
 *   status          – Clip status (processing, completed, failed, etc.)
 *   compact         – If true, show a smaller version (for MomentClips)
 *   clipUrl         – Final clip URL (when completed)
 *   sourceVideoUrl  – Source video URL (Azure Blob SAS URL) for live preview
 *   clipTimeStart   – Clip start time in seconds (for seeking source video)
 *   clipTimeEnd     – Clip end time in seconds
 */
import { useEffect, useRef, useState, useMemo, useCallback } from 'react';

// ─── Step Configuration ───
const STEP_CONFIG = {
  initializing:       { icon: '📋', color: '#60a5fa', label: 'Initializing',       phase: 'prep' },
  downloading:        { icon: '⬇️', color: '#22d3ee', label: 'Downloading',        phase: 'prep' },
  speech_boundary:    { icon: '🔊', color: '#facc15', label: 'Speech Detection',   phase: 'audio' },
  cutting:            { icon: '✂️', color: '#fb923c', label: 'Scene Cut',           phase: 'edit' },
  person_detection:   { icon: '🧑', color: '#f472b6', label: 'Person Detection',   phase: 'detect' },
  silence_removal:    { icon: '🔇', color: '#94a3b8', label: 'Silence Removal',    phase: 'audio' },
  transcribing:       { icon: '🎤', color: '#4ade80', label: 'Transcribing',       phase: 'subtitle' },
  refining_subtitles: { icon: '✨', color: '#c084fc', label: 'Refining Subtitles', phase: 'subtitle' },
  subtitle_preview:   { icon: '💬', color: '#818cf8', label: 'Subtitle Preview',   phase: 'subtitle' },
  creating_clip:      { icon: '🎬', color: '#f87171', label: 'Creating Clip',      phase: 'render' },
  hook_detection:     { icon: '🎯', color: '#fbbf24', label: 'Hook Detection',     phase: 'edit' },
  hook_insertion:     { icon: '🔥', color: '#fb923c', label: 'Hook Insertion',     phase: 'edit' },
  sound_effects:      { icon: '🔊', color: '#2dd4bf', label: 'Sound Effects',     phase: 'audio' },
  uploading:          { icon: '☁️', color: '#38bdf8', label: 'Uploading',          phase: 'export' },
  completed:          { icon: '✅', color: '#4ade80', label: 'Completed',          phase: 'done' },
};

const STEP_ORDER = [
  'initializing', 'downloading', 'speech_boundary', 'cutting',
  'person_detection', 'silence_removal', 'transcribing', 'refining_subtitles',
  'subtitle_preview', 'creating_clip', 'hook_detection', 'hook_insertion',
  'sound_effects', 'uploading', 'completed',
];

function getStepConfig(step) {
  return STEP_CONFIG[step] || { icon: '⚙️', color: '#94a3b8', label: step || 'Processing', phase: 'prep' };
}

function getStepIndex(step) {
  const idx = STEP_ORDER.indexOf(step);
  return idx >= 0 ? idx : 0;
}

// ─── Fake Mouse Cursor (human-like editing feel) ───
function FakeMouseCursor({ isActive, currentStep }) {
  const [pos, setPos] = useState({ x: 50, y: 50 });
  const [isClicking, setIsClicking] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const animRef = useRef(null);

  useEffect(() => {
    if (!isActive) return;

    // Define target positions based on current step
    const getTargetForStep = () => {
      switch (currentStep) {
        case 'cutting':
        case 'hook_detection':
        case 'hook_insertion':
          // Move along timeline area
          return { x: 20 + Math.random() * 60, y: 85 + Math.random() * 8 };
        case 'person_detection':
          // Move around the video preview
          return { x: 30 + Math.random() * 40, y: 30 + Math.random() * 40 };
        case 'transcribing':
        case 'refining_subtitles':
        case 'subtitle_preview':
          // Move to subtitle area
          return { x: 20 + Math.random() * 60, y: 70 + Math.random() * 15 };
        case 'silence_removal':
        case 'speech_boundary':
        case 'sound_effects':
          // Move to audio waveform area
          return { x: 15 + Math.random() * 70, y: 75 + Math.random() * 10 };
        default:
          // General movement
          return { x: 20 + Math.random() * 60, y: 20 + Math.random() * 60 };
      }
    };

    const moveInterval = setInterval(() => {
      const target = getTargetForStep();
      // Smooth easing toward target
      setPos(prev => ({
        x: prev.x + (target.x - prev.x) * 0.15,
        y: prev.y + (target.y - prev.y) * 0.15,
      }));

      // Simulate clicks
      if (Math.random() < 0.08) {
        setIsClicking(true);
        setTimeout(() => setIsClicking(false), 150);
      }

      // Simulate dragging on timeline
      if (currentStep === 'cutting' && Math.random() < 0.05) {
        setIsDragging(true);
        setTimeout(() => setIsDragging(false), 800);
      }
    }, 80);

    return () => clearInterval(moveInterval);
  }, [isActive, currentStep]);

  if (!isActive) return null;

  return (
    <div
      className="absolute z-30 pointer-events-none transition-all"
      style={{
        left: `${pos.x}%`,
        top: `${pos.y}%`,
        transform: 'translate(-2px, -2px)',
        transition: 'left 0.12s cubic-bezier(0.25, 0.1, 0.25, 1), top 0.12s cubic-bezier(0.25, 0.1, 0.25, 1)',
      }}
    >
      {/* Cursor SVG */}
      <svg
        width="16" height="20" viewBox="0 0 16 20" fill="none"
        className={`drop-shadow-lg ${isClicking ? 'scale-90' : ''}`}
        style={{ transition: 'transform 0.1s ease' }}
      >
        <path d="M1 1L1 15L5 11L9 19L11 18L7 10L13 10L1 1Z" fill="white" stroke="#333" strokeWidth="1" />
      </svg>
      {/* Click ripple */}
      {isClicking && (
        <div className="absolute top-1 left-1 w-4 h-4 rounded-full border border-blue-400/60 animate-ping" />
      )}
      {/* Drag indicator */}
      {isDragging && (
        <div className="absolute top-5 left-0 text-[7px] font-mono text-blue-300 bg-black/60 px-1 rounded whitespace-nowrap">
          drag
        </div>
      )}
    </div>
  );
}

// ─── Animated Waveform (for audio steps) ───
function AudioWaveform({ isActive, isSilenceRemoval }) {
  const bars = 40;
  return (
    <div className="flex items-end gap-[1px] h-10 px-1">
      {Array.from({ length: bars }).map((_, i) => {
        const isSilent = isSilenceRemoval && (i >= 10 && i <= 15 || i >= 25 && i <= 30);
        return (
          <div
            key={i}
            className={`w-[3px] rounded-full transition-all duration-200 ${
              isSilent
                ? 'bg-red-500/50 opacity-30'
                : isActive
                  ? 'bg-emerald-400/80'
                  : 'bg-gray-600/40'
            }`}
            style={{
              height: isSilent ? '2px' : `${Math.random() * 70 + 30}%`,
              animation: isActive && !isSilent ? `waveform ${0.3 + Math.random() * 0.4}s ease-in-out infinite alternate` : 'none',
              animationDelay: `${i * 20}ms`,
            }}
          />
        );
      })}
      {isSilenceRemoval && (
        <>
          <div className="absolute left-[25%] top-0 bottom-0 w-[2px] bg-red-500/70 animate-pulse" />
          <div className="absolute left-[62%] top-0 bottom-0 w-[2px] bg-red-500/70 animate-pulse" />
          {/* Strikethrough effect on silent regions */}
          <div className="absolute left-[25%] top-1/2 w-[12%] h-[2px] bg-red-400/80" style={{ transform: 'rotate(-3deg)' }} />
          <div className="absolute left-[62%] top-1/2 w-[12%] h-[2px] bg-red-400/80" style={{ transform: 'rotate(-3deg)' }} />
        </>
      )}
    </div>
  );
}

// ─── Timeline with Cut Markers ───
function EditTimeline({ progressPct, currentStep, logs }) {
  const cutPositions = useMemo(() => {
    const cuts = [];
    logs.forEach(log => {
      if (log.step === 'cutting' || log.step === 'silence_removal') {
        const pos = (log.pct || 0) / 100;
        if (pos > 0 && pos < 1) cuts.push(pos);
      }
    });
    if (cuts.length === 0 && getStepIndex(currentStep) >= getStepIndex('cutting')) {
      return [0.12, 0.28, 0.43, 0.58, 0.72, 0.88];
    }
    return cuts.length > 0 ? cuts : [];
  }, [logs, currentStep]);

  const playheadPos = progressPct / 100;
  const isCutting = currentStep === 'cutting' || currentStep === 'silence_removal';

  return (
    <div className="relative h-12 bg-gray-900/80 rounded-md overflow-hidden border border-gray-700/50">
      {/* Track background */}
      <div className="absolute inset-0 flex">
        <div className="flex-1 bg-gradient-to-r from-blue-900/20 via-purple-900/15 to-blue-900/20" />
      </div>

      {/* Video track visualization */}
      <div className="absolute top-1.5 bottom-1.5 left-0 right-0 mx-2">
        {/* Clip segments with thumbnails feel */}
        <div className="absolute inset-0 flex gap-[2px]">
          {Array.from({ length: 10 }).map((_, i) => (
            <div
              key={i}
              className={`flex-1 rounded-sm relative overflow-hidden ${
                i % 3 === 0 ? 'bg-indigo-600/50' : i % 3 === 1 ? 'bg-purple-600/40' : 'bg-blue-600/35'
              } ${playheadPos * 10 > i ? 'opacity-100' : 'opacity-40'}`}
              style={{ transition: 'opacity 0.3s ease' }}
            >
              {/* Mini waveform inside each segment */}
              <div className="absolute bottom-0 left-0 right-0 h-[40%] flex items-end gap-[1px] px-[1px]">
                {Array.from({ length: 6 }).map((_, j) => (
                  <div
                    key={j}
                    className="flex-1 bg-white/20 rounded-t-sm"
                    style={{ height: `${20 + Math.random() * 80}%` }}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Cut markers (scissors) */}
        {cutPositions.map((pos, i) => (
          <div
            key={i}
            className="absolute top-0 bottom-0 flex flex-col items-center justify-center z-10"
            style={{ left: `${pos * 100}%`, transform: 'translateX(-50%)' }}
          >
            <div className={`w-[2px] h-full ${isCutting ? 'bg-yellow-400/90' : 'bg-orange-400/60'}`} />
            <span
              className={`absolute text-[9px] -top-0.5 ${isCutting ? 'animate-bounce' : ''}`}
            >
              ✂️
            </span>
          </div>
        ))}

        {/* Playhead */}
        <div
          className="absolute top-0 bottom-0 z-20 transition-all duration-500 ease-out"
          style={{ left: `${playheadPos * 100}%` }}
        >
          <div className="w-[2px] h-full bg-white shadow-[0_0_8px_rgba(255,255,255,0.9)]" />
          <div className="absolute -top-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-white rounded-sm shadow-lg rotate-45" style={{ clipPath: 'polygon(0 0, 100% 0, 50% 100%)' }} />
        </div>
      </div>

      {/* Time markers */}
      <div className="absolute bottom-0.5 left-2 right-2 flex justify-between">
        {['0:00', '', '', '', '', `${Math.floor(progressPct / 100 * 60)}s`].map((t, i) => (
          <span key={i} className="text-[7px] font-mono text-gray-500">{t}</span>
        ))}
      </div>
    </div>
  );
}

// ─── Person Detection Overlay ───
function PersonDetectionOverlay({ isActive }) {
  if (!isActive) return null;

  return (
    <div className="absolute inset-0 pointer-events-none z-10">
      {/* Scanning line */}
      <div
        className="absolute left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-cyan-400 to-transparent opacity-80"
        style={{ animation: 'scanLine 1.8s ease-in-out infinite' }}
      />
      {/* Primary detection box */}
      <div
        className="absolute border-2 border-cyan-400 rounded-sm"
        style={{
          top: '10%', left: '20%', width: '35%', height: '75%',
          animation: 'fadeInBox 0.5s ease-out forwards',
          boxShadow: '0 0 12px rgba(34, 211, 238, 0.5)',
        }}
      >
        <div className="absolute -top-5 left-0 bg-cyan-500/90 px-1.5 py-0.5 rounded text-[9px] text-white font-mono font-bold">
          Person 1 • 98%
        </div>
        {/* Corner markers */}
        <div className="absolute top-0 left-0 w-3 h-3 border-t-2 border-l-2 border-cyan-400" />
        <div className="absolute top-0 right-0 w-3 h-3 border-t-2 border-r-2 border-cyan-400" />
        <div className="absolute bottom-0 left-0 w-3 h-3 border-b-2 border-l-2 border-cyan-400" />
        <div className="absolute bottom-0 right-0 w-3 h-3 border-b-2 border-r-2 border-cyan-400" />
      </div>
      {/* Secondary detection (smaller person in background) */}
      <div
        className="absolute border border-cyan-300/60 rounded-sm"
        style={{
          top: '25%', left: '60%', width: '18%', height: '50%',
          animation: 'fadeInBox 0.8s ease-out 0.3s forwards',
          opacity: 0,
        }}
      >
        <div className="absolute -top-4 left-0 bg-cyan-500/70 px-1 rounded text-[7px] text-white font-mono">
          Person 2 • 82%
        </div>
      </div>
    </div>
  );
}

// ─── Cut Line Animation Overlay ───
function CutLineOverlay({ isActive, progressPct }) {
  if (!isActive) return null;

  return (
    <div className="absolute inset-0 pointer-events-none z-10">
      {/* Vertical cut line sweeping across */}
      <div
        className="absolute top-0 bottom-0 w-[2px] bg-gradient-to-b from-yellow-400 via-orange-400 to-yellow-400"
        style={{
          left: `${(progressPct % 50) * 2}%`,
          boxShadow: '0 0 10px rgba(251, 146, 60, 0.7), 0 0 20px rgba(251, 146, 60, 0.3)',
          animation: 'cutSweep 2.5s ease-in-out infinite',
        }}
      />
      {/* Scissors icon at cut position */}
      <div
        className="absolute text-lg"
        style={{
          left: `${(progressPct % 50) * 2}%`,
          top: '45%',
          transform: 'translate(-50%, -50%)',
          animation: 'cutSweep 2.5s ease-in-out infinite',
        }}
      >
        ✂️
      </div>
      {/* Selection highlight */}
      <div
        className="absolute top-2 bottom-2 bg-blue-400/10 border border-blue-400/30 rounded"
        style={{
          left: '15%', width: '30%',
          animation: 'selectionPulse 3s ease-in-out infinite',
        }}
      />
    </div>
  );
}

// ─── Subtitle Typing Animation ───
function SubtitleTyping({ isActive, message }) {
  const [displayText, setDisplayText] = useState('');
  const [cursorVisible, setCursorVisible] = useState(true);

  useEffect(() => {
    if (!isActive) {
      setDisplayText('');
      return;
    }
    const text = message || 'AIが字幕を生成しています...';
    let idx = 0;
    setDisplayText('');
    const interval = setInterval(() => {
      if (idx < text.length) {
        setDisplayText(text.slice(0, idx + 1));
        idx++;
      } else {
        clearInterval(interval);
      }
    }, 45); // Faster typing
    return () => clearInterval(interval);
  }, [isActive, message]);

  useEffect(() => {
    const blink = setInterval(() => setCursorVisible(v => !v), 500);
    return () => clearInterval(blink);
  }, []);

  if (!isActive && !displayText) return null;

  return (
    <div className="absolute bottom-6 left-[10%] right-[10%] z-10">
      <div className="bg-black/85 backdrop-blur-sm rounded-lg px-4 py-2.5 border border-gray-500/40 shadow-lg">
        <span className="text-white text-sm font-medium leading-relaxed">
          {displayText}
          <span className={`inline-block w-[2px] h-4 bg-white ml-0.5 align-middle ${cursorVisible ? 'opacity-100' : 'opacity-0'}`} />
        </span>
      </div>
    </div>
  );
}

// ─── Terminal-style AI Log ───
function TerminalLog({ logs, isActive }) {
  const scrollRef = useRef(null);
  const [visibleLogs, setVisibleLogs] = useState([]);

  useEffect(() => {
    setVisibleLogs(logs.slice(-6));
  }, [logs]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [visibleLogs]);

  return (
    <div className="bg-gray-950 rounded-md border border-gray-700/50 overflow-hidden">
      {/* Terminal header */}
      <div className="flex items-center gap-1.5 px-2 py-1 bg-gray-800/80 border-b border-gray-700/50">
        <div className="flex gap-1">
          <div className="w-2 h-2 rounded-full bg-red-500/80" />
          <div className="w-2 h-2 rounded-full bg-yellow-500/80" />
          <div className="w-2 h-2 rounded-full bg-green-500/80" />
        </div>
        <span className="text-[9px] font-mono text-gray-400 ml-1">AI Editor — Processing Log</span>
        {isActive && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />}
      </div>
      {/* Log content */}
      <div
        ref={scrollRef}
        className="px-2 py-1.5 max-h-20 overflow-y-auto font-mono text-[10px] leading-relaxed"
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#374151 #111827' }}
      >
        {visibleLogs.map((log, idx) => {
          const config = getStepConfig(log.step);
          const isLatest = idx === visibleLogs.length - 1 && isActive;
          return (
            <div
              key={idx}
              className={`flex items-start gap-1.5 py-0.5 ${isLatest ? 'animate-fadeIn' : ''}`}
            >
              <span className="text-gray-600 flex-shrink-0">{log.ts || '00:00'}</span>
              <span className="flex-shrink-0" style={{ color: config.color }}>{config.icon}</span>
              <span className={`flex-1 ${isLatest ? 'text-gray-200' : 'text-gray-400'}`}>
                {log.msg || config.label}
              </span>
              {log.preview_url && <span className="text-blue-400 flex-shrink-0">▶</span>}
            </div>
          );
        })}
        {isActive && visibleLogs.length === 0 && (
          <div className="text-gray-500 py-1">
            <span className="text-green-400">$</span> Initializing AI editor...
          </div>
        )}
        {isActive && (
          <div className="flex items-center gap-1 text-green-400 py-0.5">
            <span>$</span>
            <span className="animate-pulse">▊</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Video Preview Player (for log preview_url) ───
function PreviewPlayer({ url, stepLabel, isLatest }) {
  const videoRef = useRef(null);

  useEffect(() => {
    if (videoRef.current && url) {
      videoRef.current.load();
      videoRef.current.play().catch(() => {});
    }
  }, [url]);

  if (!url) return null;

  return (
    <video
      ref={videoRef}
      src={url}
      className="w-full h-full object-contain"
      muted
      loop
      playsInline
      preload="auto"
    />
  );
}

// ─── Source Video Player (plays source video from clipTimeStart) ───
function SourceVideoPlayer({ url, timeStart = 0, timeEnd = null, isActive }) {
  const videoRef = useRef(null);
  const [isLoaded, setIsLoaded] = useState(false);
  const [hasError, setHasError] = useState(false);

  const handleLoadedMetadata = useCallback(() => {
    if (videoRef.current) {
      videoRef.current.currentTime = timeStart || 0;
      setIsLoaded(true);
      if (isActive) {
        videoRef.current.play().catch(() => {});
      }
    }
  }, [timeStart, isActive]);

  const handleTimeUpdate = useCallback(() => {
    if (videoRef.current && timeEnd && videoRef.current.currentTime >= timeEnd) {
      videoRef.current.currentTime = timeStart || 0;
    }
  }, [timeStart, timeEnd]);

  const handleError = useCallback(() => {
    setHasError(true);
  }, []);

  useEffect(() => {
    if (videoRef.current && isLoaded && isActive) {
      videoRef.current.play().catch(() => {});
    } else if (videoRef.current && !isActive) {
      videoRef.current.pause();
    }
  }, [isActive, isLoaded]);

  if (!url || hasError) return null;

  return (
    <>
      <video
        ref={videoRef}
        src={url}
        className="w-full h-full object-contain"
        muted
        playsInline
        preload="metadata"
        onLoadedMetadata={handleLoadedMetadata}
        onTimeUpdate={handleTimeUpdate}
        onError={handleError}
      />
      {!isLoaded && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/50">
          <div className="w-8 h-8 rounded-full border-2 border-gray-600 border-t-blue-400 animate-spin" />
        </div>
      )}
    </>
  );
}

// ─── AI Preparing Overlay (replaces queue waiting display) ───
function AIPreparingOverlay({ queuePosition, queueEstimatedSeconds }) {
  const [currentMsgIdx, setCurrentMsgIdx] = useState(0);
  const [dots, setDots] = useState('');
  const [fakeProgress, setFakeProgress] = useState(0);

  // Rotating preparation messages that give the impression AI is actively working
  const prepMessages = useMemo(() => [
    { icon: '🔍', text: 'Analyzing video structure...' },
    { icon: '🧠', text: 'Loading AI editing model...' },
    { icon: '🎯', text: 'Detecting optimal cut points...' },
    { icon: '⚡', text: 'Reserving render pipeline...' },
    { icon: '🎬', text: 'Preparing editing workspace...' },
    { icon: '📊', text: 'Calculating scene transitions...' },
    { icon: '🤖', text: 'AI Editor Pro initializing...' },
    { icon: '🔧', text: 'Configuring export settings...' },
  ], []);

  // Rotate messages every 3 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      setCurrentMsgIdx(prev => (prev + 1) % prepMessages.length);
    }, 3000);
    return () => clearInterval(interval);
  }, [prepMessages.length]);

  // Animate dots
  useEffect(() => {
    const interval = setInterval(() => {
      setDots(prev => prev.length >= 3 ? '' : prev + '.');
    }, 500);
    return () => clearInterval(interval);
  }, []);

  // Fake progress that slowly increases (never reaches 100)
  useEffect(() => {
    const interval = setInterval(() => {
      setFakeProgress(prev => {
        if (prev >= 85) return 85 + Math.random() * 2;
        return prev + Math.random() * 3 + 0.5;
      });
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const currentMsg = prepMessages[currentMsgIdx];

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[#0d0d0d]/80">
      {/* Animated AI brain icon */}
      <div className="relative">
        <div className="w-16 h-16 rounded-full border-2 border-gray-700 border-t-cyan-400 border-r-purple-400 animate-spin" style={{ animationDuration: '2.5s' }} />
        <span className="absolute inset-0 flex items-center justify-center text-2xl">🤖</span>
        {/* Pulse ring */}
        <div className="absolute inset-0 w-16 h-16 rounded-full border border-cyan-400/30 animate-ping" style={{ animationDuration: '2s' }} />
      </div>

      {/* Current preparation step */}
      <div className="flex flex-col items-center gap-1.5">
        <span className="text-xs font-mono text-cyan-400 transition-all duration-500">
          {currentMsg.icon} {currentMsg.text}{dots}
        </span>
        <span className="text-[10px] font-mono text-gray-500">
          AI Editor Pro — Preparing
        </span>
      </div>

      {/* Fake progress bar */}
      <div className="w-32 h-1 bg-gray-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-cyan-500 via-purple-500 to-pink-500 rounded-full transition-all duration-1000 ease-out"
          style={{ width: `${Math.min(fakeProgress, 90)}%` }}
        />
      </div>

      {/* Terminal-style fake log lines */}
      <div className="w-48 mt-1">
        <div className="text-[8px] font-mono text-gray-600 space-y-0.5">
          <div className="flex items-center gap-1 opacity-60">
            <span className="text-green-500">$</span>
            <span>model.load("editor-pro-v3")</span>
          </div>
          <div className="flex items-center gap-1 opacity-40">
            <span className="text-green-500">$</span>
            <span>gpu.allocate(render_slot)</span>
          </div>
          <div className="flex items-center gap-1 animate-pulse">
            <span className="text-green-500">$</span>
            <span className="text-cyan-400">▊</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main Component ───
export default function AIEditorMonitor({
  logs = [],
  progressPct = 0,
  progressStep = '',
  status = '',
  compact = false,
  clipUrl = '',
  queuePosition = null,
  queueEstimatedSeconds = null,
  sourceVideoUrl = null,
  clipTimeStart = 0,
  clipTimeEnd = null,
}) {
  const [isExpanded, setIsExpanded] = useState(true);
  const [selectedPreviewIdx, setSelectedPreviewIdx] = useState(-1);

  // V14.3: Treat pending/requesting with no progress as "queued" (AI preparing)
  const isQueued = status === 'queued' || (
    ['pending', 'requesting'].includes(status) && progressPct === 0 && logs.length === 0
  );
  const isActive = ['processing', 'pending', 'requesting'].includes(status) || isQueued;
  const isCompleted = status === 'completed';
  const isFailed = status === 'failed' || status === 'dead';

  const previewEntries = useMemo(() => {
    return logs.filter(log => log.preview_url);
  }, [logs]);

  const currentPreview = useMemo(() => {
    if (previewEntries.length === 0) return null;
    if (selectedPreviewIdx >= 0 && selectedPreviewIdx < previewEntries.length) {
      return previewEntries[selectedPreviewIdx];
    }
    return previewEntries[previewEntries.length - 1];
  }, [previewEntries, selectedPreviewIdx]);

  const stepConfig = getStepConfig(progressStep);
  const isPersonDetection = progressStep === 'person_detection';
  const isSubtitleStep = ['transcribing', 'refining_subtitles', 'subtitle_preview'].includes(progressStep);
  const isAudioStep = ['speech_boundary', 'silence_removal', 'sound_effects'].includes(progressStep);
  const isCutStep = ['cutting', 'hook_detection', 'hook_insertion'].includes(progressStep);

  const showSourceVideo = !currentPreview && sourceVideoUrl && isActive;

  const subtitleMessage = useMemo(() => {
    const subtitleLogs = logs.filter(l =>
      ['transcribing', 'refining_subtitles', 'subtitle_preview'].includes(l.step)
    );
    return subtitleLogs.length > 0 ? subtitleLogs[subtitleLogs.length - 1].msg : '';
  }, [logs]);

  // Compact collapsed state
  if (compact && !isExpanded) {
    if (!isActive && logs.length === 0) return null;
    return (
      <button
        type="button"
        onClick={() => setIsExpanded(true)}
        className="w-full mt-1 flex items-center gap-2 px-2 py-1.5 rounded-lg bg-gray-900/90 border border-gray-700/50 hover:border-green-600/50 transition-all group"
      >
        <span className={`w-2 h-2 rounded-full ${isActive ? 'bg-green-400 animate-pulse' : 'bg-gray-500'}`} />
        <span className="text-[10px] font-mono text-green-400 font-medium">🖥️ AI Editor</span>
        <span className="text-[10px] font-mono text-gray-500 ml-auto">
          {progressPct > 0 ? `${progressPct}%` : ''} {stepConfig.icon} {stepConfig.label}
        </span>
        <svg className="w-3 h-3 text-gray-500 group-hover:text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
    );
  }

  if (!isActive && logs.length === 0 && !isCompleted && !isFailed) return null;

  return (
    <div className={`rounded-xl overflow-hidden transition-all duration-300 ${
      isFailed ? 'bg-[#0d0d0d] border border-red-700/40' :
      isCompleted ? 'bg-[#0d0d0d] border border-green-700/40' :
      'bg-[#0d0d0d] border border-gray-700/40'
    }`}>
      {/* ─── Title Bar (macOS style) ─── */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-[#1a1a1a] border-b border-gray-800/80">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
        </div>
        <div className="flex-1 flex items-center justify-center gap-2">
          <span className="text-[11px] font-medium text-gray-300 tracking-wide">
            AI Editor Pro
          </span>
          {isActive && (
            <span className="flex items-center gap-1 text-[9px] text-green-400 font-mono">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
              EDITING
            </span>
          )}
          {isCompleted && (
            <span className="text-[9px] text-green-400 font-mono">✓ DONE</span>
          )}
        </div>
        {compact && (
          <button
            type="button"
            onClick={() => setIsExpanded(false)}
            className="text-gray-500 hover:text-gray-300"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
            </svg>
          </button>
        )}
      </div>

      {/* ─── Main Content ─── */}
      <div className="flex flex-col">
        {/* ─── Preview Window (LARGER - 16:9 aspect ratio, no max-height restriction) ─── */}
        <div className="relative bg-black aspect-[16/9] overflow-hidden">
          {/* Fake mouse cursor for human-like feel */}
          <FakeMouseCursor isActive={isActive && !isQueued} currentStep={progressStep} />

          {currentPreview ? (
            <>
              <PreviewPlayer
                url={currentPreview.preview_url}
                stepLabel={getStepConfig(currentPreview.step).label}
                isLatest={true}
              />
              {/* Overlays */}
              <PersonDetectionOverlay isActive={isPersonDetection} />
              <SubtitleTyping isActive={isSubtitleStep} message={subtitleMessage} />
              {/* Step badge */}
              <div className="absolute top-2 left-2 z-20 flex items-center gap-1.5 px-2 py-0.5 rounded bg-black/70 backdrop-blur-sm">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                <span className="text-[9px] font-mono text-green-400">{getStepConfig(currentPreview.step).label}</span>
              </div>
              {/* LIVE badge */}
              {isActive && (
                <div className="absolute top-2 right-2 z-20 flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-600/90">
                  <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                  <span className="text-[8px] font-bold text-white tracking-wider">LIVE</span>
                </div>
              )}
            </>
          ) : showSourceVideo ? (
            <>
              {/* Source video playing from clip start time */}
              <SourceVideoPlayer
                url={sourceVideoUrl}
                timeStart={clipTimeStart}
                timeEnd={clipTimeEnd}
                isActive={isActive}
              />
              {/* AI Edit Overlays on top of source video */}
              <PersonDetectionOverlay isActive={isPersonDetection} />
              <CutLineOverlay isActive={isCutStep} progressPct={progressPct} />
              <SubtitleTyping isActive={isSubtitleStep} message={subtitleMessage} />
              {/* Step badge */}
              <div className="absolute top-2 left-2 z-20 flex items-center gap-1.5 px-2 py-0.5 rounded bg-black/70 backdrop-blur-sm">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                <span className="text-[9px] font-mono text-blue-400">{stepConfig.label}</span>
              </div>
              {/* LIVE badge */}
              <div className="absolute top-2 right-2 z-20 flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-600/90">
                <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                <span className="text-[8px] font-bold text-white tracking-wider">LIVE</span>
              </div>
              {/* Source indicator */}
              <div className="absolute bottom-2 left-2 z-20 flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/60 backdrop-blur-sm">
                <span className="text-[8px] font-mono text-gray-300">
                  📹 Source • {Math.floor(clipTimeStart / 60)}:{String(Math.floor(clipTimeStart % 60)).padStart(2, '0')}
                  {clipTimeEnd ? ` → ${Math.floor(clipTimeEnd / 60)}:${String(Math.floor(clipTimeEnd % 60)).padStart(2, '0')}` : ''}
                </span>
              </div>
              {/* Progress overlay bar at bottom */}
              <div className="absolute bottom-0 left-0 right-0 h-1 z-20 bg-gray-900/50">
                <div
                  className="h-full bg-gradient-to-r from-blue-500 to-purple-500 transition-all duration-500"
                  style={{ width: `${Math.max(progressPct, 2)}%` }}
                />
              </div>
            </>
          ) : isQueued ? (
            <AIPreparingOverlay queuePosition={queuePosition} queueEstimatedSeconds={queueEstimatedSeconds} />
          ) : isActive ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
              {/* Animated editor workspace placeholder */}
              <div className="relative">
                <div className="w-16 h-16 rounded-full border-2 border-gray-700 border-t-blue-400 animate-spin" />
                <span className="absolute inset-0 flex items-center justify-center text-2xl">🎬</span>
              </div>
              <span className="text-xs font-mono text-blue-400">
                Preparing workspace...
              </span>
            </div>
          ) : null}
        </div>

        {/* ─── Audio Waveform (for audio steps) ─── */}
        {(isAudioStep || getStepIndex(progressStep) >= getStepIndex('speech_boundary')) && isActive && (
          <div className="relative px-3 py-2 bg-[#111] border-t border-gray-800/50">
            <div className="flex items-center gap-2">
              <span className="text-[9px] font-mono text-gray-500 w-10">🎵 Audio</span>
              <div className="flex-1 relative">
                <AudioWaveform
                  isActive={isAudioStep}
                  isSilenceRemoval={progressStep === 'silence_removal'}
                />
              </div>
            </div>
          </div>
        )}

        {/* ─── Timeline ─── */}
        <div className="px-3 py-2 bg-[#111] border-t border-gray-800/50">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[9px] font-mono text-gray-500">Timeline</span>
            <div className="flex-1 h-px bg-gray-800" />
            <span className="text-[9px] font-mono text-gray-400 font-bold">{progressPct}%</span>
          </div>
          <EditTimeline progressPct={progressPct} currentStep={progressStep} logs={logs} />
        </div>

        {/* ─── Step Progress Indicator ─── */}
        <div className="px-3 py-2 bg-[#0f0f0f] border-t border-gray-800/50">
          <div className="flex items-center gap-1.5 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
            {['cutting', 'person_detection', 'silence_removal', 'transcribing', 'creating_clip', 'uploading'].map((step, i) => {
              const cfg = getStepConfig(step);
              const stepIdx = getStepIndex(step);
              const currentIdx = getStepIndex(progressStep);
              const isDone = currentIdx > stepIdx;
              const isCurrent = progressStep === step;
              return (
                <div key={step} className="flex items-center gap-1">
                  <div
                    className={`flex items-center gap-0.5 px-2 py-1 rounded text-[9px] font-mono transition-all ${
                      isDone ? 'bg-green-900/30 text-green-400' :
                      isCurrent ? 'bg-gray-700/50 text-white ring-1 ring-gray-500 animate-pulse' :
                      'bg-gray-800/30 text-gray-600'
                    }`}
                  >
                    <span className="text-[10px]">{isDone ? '✓' : cfg.icon}</span>
                    <span className="hidden sm:inline">{cfg.label.split(' ')[0]}</span>
                  </div>
                  {i < 5 && <span className="text-gray-700 text-[9px]">→</span>}
                </div>
              );
            })}
          </div>
        </div>

        {/* ─── Terminal Log ─── */}
        <div className="px-3 py-2 border-t border-gray-800/50">
          <TerminalLog logs={logs} isActive={isActive} />
        </div>

        {/* ─── Footer Status Bar ─── */}
        <div className="flex items-center justify-between px-3 py-1.5 bg-[#1a1a1a] border-t border-gray-800/80">
          <div className="flex items-center gap-2">
            <span className="text-[9px] font-mono text-gray-500">
              {isQueued ? '🤖 AI Preparing...' : isActive ? `⚡ ${stepConfig.label}` : isCompleted ? '✅ Done' : isFailed ? '❌ Failed' : ''}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[8px] font-mono text-gray-500">
              {logs.length} steps
            </span>
            <span className="text-[8px] font-mono text-gray-500">
              {previewEntries.length} previews
            </span>
            {/* Progress bar mini */}
            <div className="flex items-center gap-1">
              <div className="w-20 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    isFailed ? 'bg-red-500' :
                    isCompleted ? 'bg-green-500' :
                    'bg-gradient-to-r from-blue-500 to-purple-500'
                  }`}
                  style={{ width: `${Math.max(progressPct, 2)}%` }}
                />
              </div>
              <span className="text-[9px] font-mono text-gray-400 font-bold">{progressPct}%</span>
            </div>
          </div>
        </div>
      </div>

      {/* ─── CSS Animations ─── */}
      <style>{`
        @keyframes waveform {
          0% { height: 15%; }
          100% { height: 85%; }
        }
        @keyframes scanLine {
          0% { top: 0%; }
          50% { top: 100%; }
          100% { top: 0%; }
        }
        @keyframes fadeInBox {
          from { opacity: 0; transform: scale(0.9); }
          to { opacity: 1; transform: scale(1); }
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes cutSweep {
          0% { left: 10%; }
          50% { left: 85%; }
          100% { left: 10%; }
        }
        @keyframes cutFlash {
          0%, 90%, 100% { opacity: 0; }
          95% { opacity: 1; }
        }
        @keyframes selectionPulse {
          0%, 100% { opacity: 0.3; }
          50% { opacity: 0.6; }
        }
        .animate-fadeIn {
          animation: fadeIn 0.3s ease-out forwards;
        }
      `}</style>
    </div>
  );
}
