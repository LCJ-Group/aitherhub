/**
 * AIEditorMonitor – CapCut/Premiere Pro風 AI動画編集モニター
 *
 * まるで人間がCapCut/Premiere Proで編集しているかのような
 * 「仮想編集画面」をリアルタイムで表示する。
 *
 * Features:
 *   - プレビューウィンドウ（動画再生 + 編集オーバーレイ）
 *   - タイムライン（カット位置マーカー、再生ヘッド、ハサミアニメーション）
 *   - 波形表示（無音除去ステップ）
 *   - 字幕タイピングアニメーション
 *   - ターミナル風AIログ（文字が流れる）
 *   - 人物検出バウンディングボックス
 *
 * Props:
 *   logs            – Array of { ts, pct, step, msg, preview_url? }
 *   progressPct     – Current progress percentage (0-100)
 *   progressStep    – Current step name
 *   status          – Clip status (processing, completed, failed, etc.)
 *   compact         – If true, show a smaller version (for MomentClips)
 *   clipUrl         – Final clip URL (when completed)
 */
import { useEffect, useRef, useState, useMemo } from 'react';

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

// ─── Animated Waveform (for audio steps) ───
function AudioWaveform({ isActive, isSilenceRemoval }) {
  const bars = 32;
  return (
    <div className="flex items-end gap-[1px] h-8 px-1">
      {Array.from({ length: bars }).map((_, i) => {
        const isSilent = isSilenceRemoval && (i >= 8 && i <= 12 || i >= 20 && i <= 24);
        return (
          <div
            key={i}
            className={`w-[3px] rounded-full transition-all duration-300 ${
              isSilent
                ? 'bg-red-500/40 opacity-30'
                : isActive
                  ? 'bg-emerald-400/80'
                  : 'bg-gray-600/40'
            }`}
            style={{
              height: isSilent ? '2px' : `${Math.random() * 70 + 30}%`,
              animation: isActive && !isSilent ? `waveform ${0.4 + Math.random() * 0.6}s ease-in-out infinite alternate` : 'none',
              animationDelay: `${i * 30}ms`,
            }}
          />
        );
      })}
      {isSilenceRemoval && (
        <>
          <div className="absolute left-[25%] top-0 bottom-0 w-[2px] bg-red-500/60 animate-pulse" />
          <div className="absolute left-[62%] top-0 bottom-0 w-[2px] bg-red-500/60 animate-pulse" />
        </>
      )}
    </div>
  );
}

// ─── Timeline with Cut Markers ───
function EditTimeline({ progressPct, currentStep, logs }) {
  const cutPositions = useMemo(() => {
    // Generate cut positions from logs that mention cutting
    const cuts = [];
    logs.forEach(log => {
      if (log.step === 'cutting' || log.step === 'silence_removal') {
        // Simulate cut positions based on progress
        const pos = (log.pct || 0) / 100;
        if (pos > 0 && pos < 1) cuts.push(pos);
      }
    });
    // Add some visual cuts for demo
    if (cuts.length === 0 && getStepIndex(currentStep) >= getStepIndex('cutting')) {
      return [0.15, 0.32, 0.48, 0.67, 0.82];
    }
    return cuts.length > 0 ? cuts : [];
  }, [logs, currentStep]);

  const playheadPos = progressPct / 100;
  const isCutting = currentStep === 'cutting' || currentStep === 'silence_removal';

  return (
    <div className="relative h-10 bg-gray-900/80 rounded-md overflow-hidden border border-gray-700/50">
      {/* Track background with gradient segments */}
      <div className="absolute inset-0 flex">
        <div className="flex-1 bg-gradient-to-r from-blue-900/30 via-purple-900/20 to-blue-900/30" />
      </div>

      {/* Video track visualization */}
      <div className="absolute top-1 bottom-1 left-0 right-0 mx-2">
        {/* Clip segments */}
        <div className="absolute inset-0 flex gap-[2px]">
          {Array.from({ length: 8 }).map((_, i) => (
            <div
              key={i}
              className={`flex-1 rounded-sm ${
                i % 2 === 0 ? 'bg-indigo-600/40' : 'bg-purple-600/30'
              } ${playheadPos * 8 > i ? 'opacity-100' : 'opacity-50'}`}
              style={{
                transition: 'opacity 0.5s ease',
              }}
            />
          ))}
        </div>

        {/* Cut markers (scissors) */}
        {cutPositions.map((pos, i) => (
          <div
            key={i}
            className="absolute top-0 bottom-0 flex flex-col items-center justify-center z-10"
            style={{ left: `${pos * 100}%`, transform: 'translateX(-50%)' }}
          >
            <div className={`w-[2px] h-full ${isCutting ? 'bg-yellow-400/80' : 'bg-orange-400/60'}`} />
            <span
              className={`absolute text-[8px] -top-0.5 ${isCutting ? 'animate-bounce' : ''}`}
              style={{ fontSize: '10px' }}
            >
              ✂️
            </span>
          </div>
        ))}

        {/* Playhead */}
        <div
          className="absolute top-0 bottom-0 z-20 transition-all duration-700 ease-out"
          style={{ left: `${playheadPos * 100}%` }}
        >
          <div className="w-[2px] h-full bg-white shadow-[0_0_6px_rgba(255,255,255,0.8)]" />
          <div className="absolute -top-1 left-1/2 -translate-x-1/2 w-2 h-2 bg-white rounded-full shadow-lg" />
        </div>
      </div>

      {/* Time markers */}
      <div className="absolute bottom-0 left-2 right-2 flex justify-between">
        {['0:00', '', '', '', `${Math.floor(progressPct / 100 * 60)}s`].map((t, i) => (
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
        style={{
          animation: 'scanLine 2s ease-in-out infinite',
        }}
      />
      {/* Detection boxes */}
      <div
        className="absolute border-2 border-cyan-400 rounded-sm"
        style={{
          top: '15%', left: '25%', width: '30%', height: '70%',
          animation: 'fadeInBox 0.5s ease-out forwards',
          boxShadow: '0 0 8px rgba(34, 211, 238, 0.4)',
        }}
      >
        <div className="absolute -top-4 left-0 bg-cyan-500/90 px-1 rounded text-[8px] text-white font-mono">
          Person 1 • 98%
        </div>
        {/* Corner markers */}
        <div className="absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-cyan-400" />
        <div className="absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-cyan-400" />
        <div className="absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-cyan-400" />
        <div className="absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-cyan-400" />
      </div>
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
    }, 60);
    return () => clearInterval(interval);
  }, [isActive, message]);

  useEffect(() => {
    const blink = setInterval(() => setCursorVisible(v => !v), 530);
    return () => clearInterval(blink);
  }, []);

  if (!isActive && !displayText) return null;

  return (
    <div className="absolute bottom-4 left-4 right-4 z-10">
      <div className="bg-black/80 backdrop-blur-sm rounded-md px-3 py-2 border border-gray-600/50">
        <span className="text-white text-xs font-medium">
          {displayText}
          <span className={`inline-block w-[2px] h-3 bg-white ml-0.5 ${cursorVisible ? 'opacity-100' : 'opacity-0'}`} />
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
    setVisibleLogs(logs.slice(-8));
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
        className="px-2 py-1.5 max-h-24 overflow-y-auto font-mono text-[10px] leading-relaxed"
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

// ─── Video Preview Player ───
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

// ─── Main Component ───
export default function AIEditorMonitor({ logs = [], progressPct = 0, progressStep = '', status = '', compact = false, clipUrl = '' }) {
  const [isExpanded, setIsExpanded] = useState(true);
  const [selectedPreviewIdx, setSelectedPreviewIdx] = useState(-1);

  const isQueued = status === 'queued';
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

  // Get latest subtitle message
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
          <span className="text-[10px] font-medium text-gray-300 tracking-wide">
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
        {/* ─── Preview Window ─── */}
        <div className="relative bg-black aspect-video max-h-[200px] overflow-hidden">
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
          ) : isQueued ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
              <div className="relative">
                <div className="w-14 h-14 rounded-full border-2 border-gray-700 border-t-amber-400 animate-spin" style={{ animationDuration: '3s' }} />
                <span className="absolute inset-0 flex items-center justify-center text-xl">⏳</span>
              </div>
              <span className="text-[11px] font-mono text-amber-400 animate-pulse">
                Waiting in queue...
              </span>
              <span className="text-[9px] font-mono text-gray-600">
                AI Editor will start soon
              </span>
            </div>
          ) : isActive ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
              <div className="relative">
                <div className="w-14 h-14 rounded-full border-2 border-gray-700 border-t-blue-400 animate-spin" />
                <span className="absolute inset-0 flex items-center justify-center text-xl">🎬</span>
              </div>
              <span className="text-[11px] font-mono text-blue-400">
                Preparing workspace...
              </span>
            </div>
          ) : null}
        </div>

        {/* ─── Audio Waveform (for audio steps) ─── */}
        {(isAudioStep || getStepIndex(progressStep) >= getStepIndex('speech_boundary')) && isActive && (
          <div className="relative px-3 py-1.5 bg-[#111] border-t border-gray-800/50">
            <div className="flex items-center gap-2">
              <span className="text-[8px] font-mono text-gray-500 w-8">🎵 Audio</span>
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
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[8px] font-mono text-gray-500">Timeline</span>
            <div className="flex-1 h-px bg-gray-800" />
            <span className="text-[8px] font-mono text-gray-500">{progressPct}%</span>
          </div>
          <EditTimeline progressPct={progressPct} currentStep={progressStep} logs={logs} />
        </div>

        {/* ─── Step Progress Indicator ─── */}
        <div className="px-3 py-1.5 bg-[#0f0f0f] border-t border-gray-800/50">
          <div className="flex items-center gap-1 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
            {['cutting', 'person_detection', 'silence_removal', 'transcribing', 'creating_clip', 'uploading'].map((step, i) => {
              const cfg = getStepConfig(step);
              const stepIdx = getStepIndex(step);
              const currentIdx = getStepIndex(progressStep);
              const isDone = currentIdx > stepIdx;
              const isCurrent = progressStep === step;
              return (
                <div key={step} className="flex items-center gap-0.5">
                  <div
                    className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[8px] font-mono transition-all ${
                      isDone ? 'bg-green-900/30 text-green-400' :
                      isCurrent ? 'bg-gray-700/50 text-white ring-1 ring-gray-500' :
                      'bg-gray-800/30 text-gray-600'
                    }`}
                  >
                    <span className="text-[9px]">{isDone ? '✓' : cfg.icon}</span>
                    <span className="hidden sm:inline">{cfg.label.split(' ')[0]}</span>
                  </div>
                  {i < 5 && <span className="text-gray-700 text-[8px]">→</span>}
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
        <div className="flex items-center justify-between px-3 py-1 bg-[#1a1a1a] border-t border-gray-800/80">
          <div className="flex items-center gap-2">
            <span className="text-[8px] font-mono text-gray-500">
              {isQueued ? '⏳ Queued' : isActive ? `⚡ ${stepConfig.label}` : isCompleted ? '✅ Done' : isFailed ? '❌ Failed' : ''}
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
              <div className="w-16 h-1 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-700 ${
                    isFailed ? 'bg-red-500' :
                    isCompleted ? 'bg-green-500' :
                    'bg-gradient-to-r from-blue-500 to-purple-500'
                  }`}
                  style={{ width: `${Math.max(progressPct, 2)}%` }}
                />
              </div>
              <span className="text-[8px] font-mono text-gray-400">{progressPct}%</span>
            </div>
          </div>
        </div>
      </div>

      {/* ─── CSS Animations ─── */}
      <style>{`
        @keyframes waveform {
          0% { height: 20%; }
          100% { height: 80%; }
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
        .animate-fadeIn {
          animation: fadeIn 0.3s ease-out forwards;
        }
      `}</style>
    </div>
  );
}
