/**
 * AIEditorMonitor – Real-time AI processing log panel for clip generation.
 *
 * Shows a terminal-style log with:
 *   - Step icons and color coding
 *   - Elapsed time per step
 *   - Progress bar
 *   - Auto-scroll to latest entry
 *
 * Props:
 *   logs            – Array of { ts, pct, step, msg } from processing_logs
 *   progressPct     – Current progress percentage (0-100)
 *   progressStep    – Current step name
 *   status          – Clip status (processing, completed, failed, etc.)
 *   compact         – If true, show a smaller version (for MomentClips)
 */
import { useEffect, useRef, useState } from 'react';

const STEP_CONFIG = {
  initializing:       { icon: '📋', color: 'text-blue-400',   bg: 'bg-blue-500/20' },
  downloading:        { icon: '⬇️', color: 'text-cyan-400',   bg: 'bg-cyan-500/20' },
  speech_boundary:    { icon: '🔊', color: 'text-yellow-400', bg: 'bg-yellow-500/20' },
  cutting:            { icon: '✂️', color: 'text-orange-400',  bg: 'bg-orange-500/20' },
  person_detection:   { icon: '🧑', color: 'text-pink-400',   bg: 'bg-pink-500/20' },
  silence_removal:    { icon: '🔇', color: 'text-gray-400',   bg: 'bg-gray-500/20' },
  transcribing:       { icon: '🎤', color: 'text-green-400',  bg: 'bg-green-500/20' },
  refining_subtitles: { icon: '✨', color: 'text-purple-400', bg: 'bg-purple-500/20' },
  subtitle_preview:   { icon: '💬', color: 'text-indigo-400', bg: 'bg-indigo-500/20' },
  creating_clip:      { icon: '🎬', color: 'text-red-400',    bg: 'bg-red-500/20' },
  hook_detection:     { icon: '🎯', color: 'text-amber-400',  bg: 'bg-amber-500/20' },
  hook_insertion:     { icon: '🔥', color: 'text-orange-400', bg: 'bg-orange-500/20' },
  sound_effects:      { icon: '🔊', color: 'text-teal-400',   bg: 'bg-teal-500/20' },
  uploading:          { icon: '☁️', color: 'text-sky-400',    bg: 'bg-sky-500/20' },
  completed:          { icon: '🎉', color: 'text-green-400',  bg: 'bg-green-500/20' },
};

function getStepConfig(step) {
  return STEP_CONFIG[step] || { icon: '⚙️', color: 'text-gray-400', bg: 'bg-gray-500/20' };
}

/**
 * Calculate elapsed time between consecutive log entries.
 */
function calculateElapsed(logs) {
  if (!logs || logs.length === 0) return [];
  return logs.map((log, idx) => {
    if (idx === 0) return { ...log, elapsed: null };
    const prevTs = logs[idx - 1].ts;
    const curTs = log.ts;
    if (!prevTs || !curTs) return { ...log, elapsed: null };
    // Parse HH:MM:SS
    const [ph, pm, ps] = prevTs.split(':').map(Number);
    const [ch, cm, cs] = curTs.split(':').map(Number);
    if (isNaN(ph) || isNaN(ch)) return { ...log, elapsed: null };
    const prevSec = ph * 3600 + pm * 60 + ps;
    const curSec = ch * 3600 + cm * 60 + cs;
    let diff = curSec - prevSec;
    if (diff < 0) diff += 86400; // wrap around midnight
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

export default function AIEditorMonitor({ logs = [], progressPct = 0, progressStep = '', status = '', compact = false }) {
  const scrollRef = useRef(null);
  const [isExpanded, setIsExpanded] = useState(!compact);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (scrollRef.current && isExpanded) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, isExpanded]);

  const enrichedLogs = calculateElapsed(logs);
  const isActive = ['processing', 'pending', 'requesting'].includes(status);
  const isCompleted = status === 'completed';
  const isFailed = status === 'failed' || status === 'dead';

  const stepConfig = getStepConfig(progressStep);

  // Compact header for MomentClips
  if (compact && !isExpanded) {
    return (
      <button
        type="button"
        onClick={() => setIsExpanded(true)}
        className="w-full mt-1 flex items-center gap-2 px-2 py-1 rounded-md bg-gray-900/80 border border-gray-700/50 hover:border-gray-600 transition-colors"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
        <span className="text-[10px] font-mono text-green-400">AI Log</span>
        <span className="text-[10px] font-mono text-gray-500 ml-auto">{logs.length} entries</span>
        <svg className="w-3 h-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
    );
  }

  if (logs.length === 0 && !isActive) return null;

  return (
    <div className={`rounded-lg bg-gray-950 border overflow-hidden ${
      isFailed ? 'border-red-700/50' : isCompleted ? 'border-green-700/50' : 'border-gray-700/50'
    }`}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-900 border-b border-gray-800">
        {isActive && <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />}
        {isCompleted && <span className="w-2 h-2 rounded-full bg-green-400" />}
        {isFailed && <span className="w-2 h-2 rounded-full bg-red-400" />}
        <span className={`text-[10px] font-mono font-medium ${
          isFailed ? 'text-red-400' : isCompleted ? 'text-green-400' : 'text-green-400'
        }`}>
          {window.__t?.('aiProcessingLog') || 'AI Processing Log'}
        </span>
        {isActive && (
          <span className="text-[10px] font-mono text-gray-500 ml-auto">
            {stepConfig.icon} {progressStep} • {progressPct}%
          </span>
        )}
        {compact && (
          <button
            type="button"
            onClick={() => setIsExpanded(false)}
            className="ml-auto text-gray-500 hover:text-gray-300 transition-colors"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
            </svg>
          </button>
        )}
      </div>

      {/* Progress bar */}
      {isActive && (
        <div className="h-0.5 bg-gray-800">
          <div
            className="h-full bg-gradient-to-r from-purple-500 to-pink-500 transition-all duration-700 ease-out"
            style={{ width: `${Math.max(progressPct, 1)}%` }}
          />
        </div>
      )}

      {/* Log entries */}
      <div
        ref={scrollRef}
        className={`px-3 py-2 overflow-y-auto ${compact ? 'max-h-28' : 'max-h-40'}`}
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#4B5563 #111827' }}
      >
        {enrichedLogs.map((log, idx) => {
          const config = getStepConfig(log.step);
          const isLatest = idx === enrichedLogs.length - 1 && isActive;
          return (
            <div
              key={idx}
              className={`flex items-start gap-2 py-0.5 ${isLatest ? 'animate-fadeIn' : ''}`}
            >
              {/* Timestamp */}
              <span className="text-gray-600 text-[10px] font-mono flex-shrink-0 mt-px w-14">
                {log.ts || ''}
              </span>
              {/* Step icon */}
              <span className={`text-[10px] flex-shrink-0 mt-px ${config.color}`}>
                {config.icon}
              </span>
              {/* Message */}
              <span className={`text-[11px] font-mono leading-relaxed flex-1 ${
                isLatest ? 'text-gray-200' : 'text-gray-400'
              }`}>
                {log.msg || ''}
              </span>
              {/* Elapsed time */}
              {log.elapsed !== null && log.elapsed > 0 && (
                <span className="text-[9px] font-mono text-gray-600 flex-shrink-0 mt-px">
                  +{formatElapsed(log.elapsed)}
                </span>
              )}
            </div>
          );
        })}
        {isActive && enrichedLogs.length === 0 && (
          <div className="flex items-center gap-2 py-1">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            <span className="text-gray-500 text-[11px] font-mono">
              {window.__t?.('waitingForLogs') || 'Waiting for processing to start...'}
            </span>
          </div>
        )}
      </div>

      {/* Footer with total time */}
      {enrichedLogs.length > 1 && (isCompleted || isFailed) && (
        <div className="px-3 py-1 bg-gray-900 border-t border-gray-800 flex items-center justify-between">
          <span className="text-[9px] font-mono text-gray-600">
            {enrichedLogs.length} steps
          </span>
          <span className="text-[9px] font-mono text-gray-600">
            Total: {formatElapsed(enrichedLogs.reduce((sum, l) => sum + (l.elapsed || 0), 0))}
          </span>
        </div>
      )}
    </div>
  );
}
