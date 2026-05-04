/**
 * AIEditorMonitor – Real-time AI video editing monitor.
 *
 * Shows a "virtual monitor" experience where users can watch AI edit their video
 * in real-time, with intermediate preview videos at each processing step.
 *
 * Features:
 *   - Video player that auto-switches as new intermediate previews arrive
 *   - Step timeline with icons, progress, and elapsed time
 *   - "AI is editing your video" status display
 *   - Smooth transitions between preview stages
 *
 * Props:
 *   logs            – Array of { ts, pct, step, msg, preview_url? } from processing_logs
 *   progressPct     – Current progress percentage (0-100)
 *   progressStep    – Current step name
 *   status          – Clip status (processing, completed, failed, etc.)
 *   compact         – If true, show a smaller version (for MomentClips)
 *   clipUrl         – Final clip URL (when completed)
 */
import { useEffect, useRef, useState, useMemo, useCallback } from 'react';

// Step configuration with icons, colors, and human-readable labels
const STEP_CONFIG = {
  initializing:       { icon: '📋', color: 'text-blue-400',   bg: 'bg-blue-500/20',   label: 'Initializing' },
  downloading:        { icon: '⬇️', color: 'text-cyan-400',   bg: 'bg-cyan-500/20',   label: 'Downloading' },
  speech_boundary:    { icon: '🔊', color: 'text-yellow-400', bg: 'bg-yellow-500/20', label: 'Speech Detection' },
  cutting:            { icon: '✂️', color: 'text-orange-400',  bg: 'bg-orange-500/20', label: 'Cutting' },
  person_detection:   { icon: '🧑', color: 'text-pink-400',   bg: 'bg-pink-500/20',   label: 'Person Detection' },
  silence_removal:    { icon: '🔇', color: 'text-gray-400',   bg: 'bg-gray-500/20',   label: 'Silence Removal' },
  transcribing:       { icon: '🎤', color: 'text-green-400',  bg: 'bg-green-500/20',  label: 'Transcribing' },
  refining_subtitles: { icon: '✨', color: 'text-purple-400', bg: 'bg-purple-500/20', label: 'Refining Subtitles' },
  subtitle_preview:   { icon: '💬', color: 'text-indigo-400', bg: 'bg-indigo-500/20', label: 'Subtitle Preview' },
  creating_clip:      { icon: '🎬', color: 'text-red-400',    bg: 'bg-red-500/20',    label: 'Creating Clip' },
  hook_detection:     { icon: '🎯', color: 'text-amber-400',  bg: 'bg-amber-500/20',  label: 'Hook Detection' },
  hook_insertion:     { icon: '🔥', color: 'text-orange-400', bg: 'bg-orange-500/20', label: 'Hook Insertion' },
  sound_effects:      { icon: '🔊', color: 'text-teal-400',   bg: 'bg-teal-500/20',   label: 'Sound Effects' },
  uploading:          { icon: '☁️', color: 'text-sky-400',    bg: 'bg-sky-500/20',    label: 'Uploading' },
  completed:          { icon: '🎉', color: 'text-green-400',  bg: 'bg-green-500/20',  label: 'Completed' },
};

const STEP_ORDER = [
  'initializing', 'downloading', 'speech_boundary', 'cutting',
  'person_detection', 'silence_removal', 'transcribing', 'refining_subtitles',
  'subtitle_preview', 'creating_clip', 'hook_detection', 'hook_insertion',
  'sound_effects', 'uploading', 'completed',
];

function getStepConfig(step) {
  return STEP_CONFIG[step] || { icon: '⚙️', color: 'text-gray-400', bg: 'bg-gray-500/20', label: step };
}

function getStepIndex(step) {
  const idx = STEP_ORDER.indexOf(step);
  return idx >= 0 ? idx : 0;
}

function calculateElapsed(logs) {
  if (!logs || logs.length === 0) return [];
  return logs.map((log, idx) => {
    if (idx === 0) return { ...log, elapsed: null };
    const prevTs = logs[idx - 1].ts;
    const curTs = log.ts;
    if (!prevTs || !curTs) return { ...log, elapsed: null };
    const [ph, pm, ps] = prevTs.split(':').map(Number);
    const [ch, cm, cs] = curTs.split(':').map(Number);
    if (isNaN(ph) || isNaN(ch)) return { ...log, elapsed: null };
    const prevSec = ph * 3600 + pm * 60 + ps;
    const curSec = ch * 3600 + cm * 60 + cs;
    let diff = curSec - prevSec;
    if (diff < 0) diff += 86400;
    return { ...log, elapsed: diff };
  });
}

function formatElapsed(seconds) {
  if (seconds === null || seconds === undefined) return '';
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m${s > 0 ? `${s}s` : ''}`;
}

// ─── Animated dots for "AI is working" ───
function PulsingDots() {
  return (
    <span className="inline-flex gap-0.5 ml-1">
      <span className="w-1 h-1 rounded-full bg-green-400 animate-bounce" style={{ animationDelay: '0ms' }} />
      <span className="w-1 h-1 rounded-full bg-green-400 animate-bounce" style={{ animationDelay: '150ms' }} />
      <span className="w-1 h-1 rounded-full bg-green-400 animate-bounce" style={{ animationDelay: '300ms' }} />
    </span>
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
    <div className={`relative rounded-lg overflow-hidden bg-black ${isLatest ? 'ring-2 ring-green-500/50' : 'ring-1 ring-gray-700/50'}`}>
      {/* Step badge */}
      <div className="absolute top-2 left-2 z-10 flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-black/70 backdrop-blur-sm">
        <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
        <span className="text-[10px] font-mono text-green-400 font-medium">{stepLabel}</span>
      </div>
      {/* LIVE badge when latest */}
      {isLatest && (
        <div className="absolute top-2 right-2 z-10 flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-600/90 backdrop-blur-sm">
          <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
          <span className="text-[9px] font-bold text-white tracking-wider">LIVE</span>
        </div>
      )}
      <video
        ref={videoRef}
        src={url}
        className="w-full aspect-[9/16] max-h-[280px] object-contain bg-black"
        controls
        muted
        loop
        playsInline
        preload="auto"
      />
    </div>
  );
}

// ─── Step Timeline (mini) ───
function StepTimeline({ currentStep, previewSteps }) {
  const mainSteps = ['cutting', 'silence_removal', 'creating_clip', 'hook_insertion', 'sound_effects', 'uploading', 'completed'];
  const currentIdx = getStepIndex(currentStep);

  return (
    <div className="flex items-center gap-0.5 px-1">
      {mainSteps.map((step, i) => {
        const config = getStepConfig(step);
        const stepIdx = getStepIndex(step);
        const isDone = currentIdx > stepIdx;
        const isCurrent = currentStep === step || (currentIdx >= stepIdx && currentIdx < getStepIndex(mainSteps[i + 1] || 'completed'));
        const hasPreview = previewSteps.has(step);

        return (
          <div key={step} className="flex items-center gap-0.5">
            <div className={`relative flex items-center justify-center w-5 h-5 rounded-full text-[9px] transition-all duration-300 ${
              isDone ? 'bg-green-500/30 text-green-400' :
              isCurrent ? `${config.bg} ${config.color} ring-1 ring-current` :
              'bg-gray-800 text-gray-600'
            }`}>
              {isDone ? '✓' : config.icon}
              {hasPreview && (
                <span className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-blue-500 border border-gray-950" />
              )}
            </div>
            {i < mainSteps.length - 1 && (
              <div className={`w-3 h-px ${isDone ? 'bg-green-500/50' : 'bg-gray-700'}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Main Component ───
export default function AIEditorMonitor({ logs = [], progressPct = 0, progressStep = '', status = '', compact = false, clipUrl = '' }) {
  const scrollRef = useRef(null);
  const [isExpanded, setIsExpanded] = useState(!compact);
  const [selectedPreviewIdx, setSelectedPreviewIdx] = useState(-1); // -1 = auto (latest)
  const [showLogs, setShowLogs] = useState(false);

  const enrichedLogs = useMemo(() => calculateElapsed(logs), [logs]);
  const isActive = ['processing', 'pending', 'requesting'].includes(status);
  const isCompleted = status === 'completed';
  const isFailed = status === 'failed' || status === 'dead';

  // Extract preview entries from logs
  const previewEntries = useMemo(() => {
    return logs
      .map((log, idx) => ({ ...log, logIdx: idx }))
      .filter(log => log.preview_url);
  }, [logs]);

  // Set of steps that have previews
  const previewSteps = useMemo(() => {
    return new Set(previewEntries.map(e => e.step));
  }, [previewEntries]);

  // Current preview to show (auto = latest, or user-selected)
  const currentPreview = useMemo(() => {
    if (previewEntries.length === 0) return null;
    if (selectedPreviewIdx >= 0 && selectedPreviewIdx < previewEntries.length) {
      return previewEntries[selectedPreviewIdx];
    }
    return previewEntries[previewEntries.length - 1]; // latest
  }, [previewEntries, selectedPreviewIdx]);

  // Auto-select latest preview when new ones arrive
  useEffect(() => {
    if (selectedPreviewIdx === -1 || selectedPreviewIdx >= previewEntries.length) {
      // Keep on auto (latest)
    }
  }, [previewEntries.length]);

  // Auto-scroll log panel
  useEffect(() => {
    if (scrollRef.current && showLogs) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, showLogs]);

  const stepConfig = getStepConfig(progressStep);

  // Compact collapsed state
  if (compact && !isExpanded) {
    const hasPreview = previewEntries.length > 0;
    return (
      <button
        type="button"
        onClick={() => setIsExpanded(true)}
        className="w-full mt-1 flex items-center gap-2 px-2 py-1.5 rounded-lg bg-gray-900/90 border border-gray-700/50 hover:border-green-600/50 transition-all group"
      >
        <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
        <span className="text-[10px] font-mono text-green-400 font-medium">
          {hasPreview ? '🖥️ AI Monitor' : '⚙️ AI Processing'}
        </span>
        <span className="text-[10px] font-mono text-gray-500 ml-auto">
          {progressPct}% • {stepConfig.icon} {getStepConfig(progressStep).label}
        </span>
        <svg className="w-3 h-3 text-gray-500 group-hover:text-green-400 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
    );
  }

  if (logs.length === 0 && !isActive) return null;

  return (
    <div className={`rounded-xl overflow-hidden transition-all duration-300 ${
      isFailed ? 'bg-gray-950 border border-red-700/50' :
      isCompleted ? 'bg-gray-950 border border-green-700/50' :
      'bg-gray-950 border border-gray-700/50'
    }`}>
      {/* ─── Header ─── */}
      <div className="flex items-center gap-2 px-3 py-2 bg-gray-900/80 border-b border-gray-800">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {isActive && <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse flex-shrink-0" />}
          {isCompleted && <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />}
          {isFailed && <span className="w-2 h-2 rounded-full bg-red-400 flex-shrink-0" />}
          <span className="text-xs font-semibold text-white tracking-wide">
            🖥️ AI Editor Monitor
          </span>
          {isActive && (
            <span className="text-[10px] font-mono text-gray-400 truncate">
              {stepConfig.icon} {getStepConfig(progressStep).label}
              <PulsingDots />
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {/* Log toggle */}
          <button
            type="button"
            onClick={() => setShowLogs(!showLogs)}
            className={`text-[9px] font-mono px-1.5 py-0.5 rounded transition-colors ${
              showLogs ? 'bg-gray-700 text-gray-200' : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {showLogs ? 'Hide Log' : 'Log'}
          </button>
          {/* Collapse button (compact mode) */}
          {compact && (
            <button
              type="button"
              onClick={() => setIsExpanded(false)}
              className="text-gray-500 hover:text-gray-300 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* ─── Progress bar ─── */}
      <div className="h-1 bg-gray-800">
        <div
          className={`h-full transition-all duration-700 ease-out ${
            isFailed ? 'bg-red-500' :
            isCompleted ? 'bg-green-500' :
            'bg-gradient-to-r from-blue-500 via-purple-500 to-pink-500'
          }`}
          style={{ width: `${Math.max(progressPct, 1)}%` }}
        />
      </div>

      {/* ─── Step Timeline ─── */}
      <div className="px-3 py-1.5 bg-gray-900/50 border-b border-gray-800/50">
        <StepTimeline currentStep={progressStep} previewSteps={previewSteps} />
      </div>

      {/* ─── Video Preview Area ─── */}
      {(previewEntries.length > 0 || isActive) && (
        <div className="p-3">
          {currentPreview ? (
            <div className="space-y-2">
              {/* Preview player */}
              <PreviewPlayer
                url={currentPreview.preview_url}
                stepLabel={getStepConfig(currentPreview.step).label}
                isLatest={currentPreview === previewEntries[previewEntries.length - 1] && isActive}
              />

              {/* Preview step selector (when multiple previews exist) */}
              {previewEntries.length > 1 && (
                <div className="flex items-center gap-1 overflow-x-auto pb-1" style={{ scrollbarWidth: 'thin' }}>
                  {previewEntries.map((entry, idx) => {
                    const config = getStepConfig(entry.step);
                    const isSelected = currentPreview === entry;
                    return (
                      <button
                        key={idx}
                        type="button"
                        onClick={() => setSelectedPreviewIdx(idx)}
                        className={`flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-mono whitespace-nowrap transition-all ${
                          isSelected
                            ? `${config.bg} ${config.color} ring-1 ring-current`
                            : 'bg-gray-800/50 text-gray-500 hover:text-gray-300 hover:bg-gray-800'
                        }`}
                      >
                        <span>{config.icon}</span>
                        <span>{config.label}</span>
                      </button>
                    );
                  })}
                  {/* Auto (latest) button */}
                  <button
                    type="button"
                    onClick={() => setSelectedPreviewIdx(-1)}
                    className={`flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-mono whitespace-nowrap transition-all ${
                      selectedPreviewIdx === -1
                        ? 'bg-green-500/20 text-green-400 ring-1 ring-green-500/50'
                        : 'bg-gray-800/50 text-gray-500 hover:text-gray-300 hover:bg-gray-800'
                    }`}
                  >
                    <span>📡</span>
                    <span>Latest</span>
                  </button>
                </div>
              )}
            </div>
          ) : isActive ? (
            /* Waiting for first preview */
            <div className="flex flex-col items-center justify-center py-6 gap-2">
              <div className="relative">
                <div className="w-12 h-12 rounded-full border-2 border-gray-700 border-t-green-400 animate-spin" />
                <span className="absolute inset-0 flex items-center justify-center text-lg">🎬</span>
              </div>
              <span className="text-[11px] font-mono text-gray-400">
                AI is preparing your video<PulsingDots />
              </span>
              <span className="text-[10px] font-mono text-gray-600">
                Preview will appear when editing begins
              </span>
            </div>
          ) : null}
        </div>
      )}

      {/* ─── Log entries (toggleable) ─── */}
      {showLogs && (
        <div className="border-t border-gray-800">
          <div
            ref={scrollRef}
            className={`px-3 py-2 overflow-y-auto ${compact ? 'max-h-24' : 'max-h-32'}`}
            style={{ scrollbarWidth: 'thin', scrollbarColor: '#4B5563 #111827' }}
          >
            {enrichedLogs.map((log, idx) => {
              const config = getStepConfig(log.step);
              const isLatest = idx === enrichedLogs.length - 1 && isActive;
              const hasPreview = !!log.preview_url;
              return (
                <div
                  key={idx}
                  className={`flex items-start gap-1.5 py-0.5 ${isLatest ? 'animate-fadeIn' : ''} ${
                    hasPreview ? 'cursor-pointer hover:bg-gray-800/50 rounded px-1 -mx-1' : ''
                  }`}
                  onClick={hasPreview ? () => {
                    const previewIdx = previewEntries.findIndex(e => e.logIdx === idx);
                    if (previewIdx >= 0) setSelectedPreviewIdx(previewIdx);
                  } : undefined}
                >
                  <span className="text-gray-600 text-[9px] font-mono flex-shrink-0 mt-px w-12">{log.ts || ''}</span>
                  <span className={`text-[9px] flex-shrink-0 mt-px ${config.color}`}>{config.icon}</span>
                  <span className={`text-[10px] font-mono leading-relaxed flex-1 ${
                    isLatest ? 'text-gray-200' : 'text-gray-400'
                  }`}>
                    {log.msg || ''}
                    {hasPreview && <span className="ml-1 text-blue-400">▶</span>}
                  </span>
                  {log.elapsed !== null && log.elapsed > 0 && (
                    <span className="text-[8px] font-mono text-gray-600 flex-shrink-0 mt-px">+{formatElapsed(log.elapsed)}</span>
                  )}
                </div>
              );
            })}
            {isActive && enrichedLogs.length === 0 && (
              <div className="flex items-center gap-2 py-1">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                <span className="text-gray-500 text-[10px] font-mono">Waiting for processing to start...</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ─── Footer ─── */}
      <div className="px-3 py-1.5 bg-gray-900/50 border-t border-gray-800/50 flex items-center justify-between">
        <span className="text-[9px] font-mono text-gray-600">
          {previewEntries.length > 0 ? `${previewEntries.length} previews` : ''} 
          {previewEntries.length > 0 && enrichedLogs.length > 0 ? ' • ' : ''}
          {enrichedLogs.length} steps
        </span>
        <span className="text-[9px] font-mono text-gray-600">
          {isActive ? `${progressPct}%` : ''}
          {(isCompleted || isFailed) && enrichedLogs.length > 1 
            ? `Total: ${formatElapsed(enrichedLogs.reduce((sum, l) => sum + (l.elapsed || 0), 0))}`
            : ''
          }
        </span>
      </div>
    </div>
  );
}
