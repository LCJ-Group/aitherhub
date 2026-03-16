import { useState, useEffect, useRef, useCallback, useMemo, memo } from 'react';
import VideoService from '../base/services/videoService';

// ── Stall detection config ──────────────────────────────────────────
const STALL_DETECT_MINUTES = 10;  // Minutes without progress change → show stall warning
const STALL_DETECT_MS = STALL_DETECT_MINUTES * 60 * 1000;

const normalizeProcessingStatus = (status) => {
  if (status === 'uploaded' || status === 'QUEUED') {
    // 'uploaded' means enqueued but worker hasn't started yet.
    // Show as first analysis step (解析準備中) instead of misleading "圧縮中".
    return 'STEP_COMPRESS_1080P';
  }
  return status;
};

// ── localStorage helpers ──────────────────────────────────────────
const STORAGE_PREFIX = 'video-progress:';

function loadTimingState(videoId) {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + videoId);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function saveTimingState(videoId, state) {
  try {
    localStorage.setItem(STORAGE_PREFIX + videoId, JSON.stringify(state));
  } catch { /* quota exceeded – ignore */ }
}

function clearTimingState(videoId) {
  try { localStorage.removeItem(STORAGE_PREFIX + videoId); } catch { /* noop */ }
}

// ── Time formatting ───────────────────────────────────────────────
function formatDuration(ms) {
  if (!ms || ms < 0) return '--:--';
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// ── EMA helper ────────────────────────────────────────────────────
function emaSmooth(prev, next, alpha = 0.3) {
  if (prev === null || prev === undefined) return next;
  return alpha * next + (1 - alpha) * prev;
}

function ProcessingSteps({ videoId, initialStatus, videoTitle, onProcessingComplete, externalProgress, uploadDurationMs, uploadStartTime, videoDurationSec }) {
  // Upload elapsed time (live counter during upload)
  const [uploadElapsedMs, setUploadElapsedMs] = useState(0);
  const [currentStatus, setCurrentStatus] = useState(initialStatus || 'NEW');
  const [smoothProgress, setSmoothProgress] = useState(externalProgress || 0);
  const [stepProgress, setStepProgress] = useState(0);
  const [errorMessage, setErrorMessage] = useState(null);
  const [_usePolling, setUsePolling] = useState(false);

  // Enqueue & worker evidence state
  const [enqueueStatus, setEnqueueStatus] = useState(null);
  const [workerClaimedAt, setWorkerClaimedAt] = useState(null);
  const [enqueueError, setEnqueueError] = useState(null);

  // ── Stall detection state ──
  const [isStalled, setIsStalled] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [errorLogs, setErrorLogs] = useState([]);
  const [showErrorLogs, setShowErrorLogs] = useState(false);
  const [latestError, setLatestError] = useState(null);
  const [loadingErrorLogs, setLoadingErrorLogs] = useState(false);
  const lastProgressRef = useRef(null);       // { progress, timestamp }
  const stallCheckTimerRef = useRef(null);

  // ── Timing state ──
  const [elapsedMs, setElapsedMs] = useState(0);
  const [estimatedRemainingMs, setEstimatedRemainingMs] = useState(null);
  const [phaseDurations, setPhaseDurations] = useState({}); // { stepKey: durationMs }

  const statusStreamRef = useRef(null);
  const progressIntervalRef = useRef(null);
  const pollingIntervalRef = useRef(null);
  const lastStatusChangeRef = useRef(0);
  const retryCountRef = useRef(0);
  const lastInitializedVideoIdRef = useRef(null);
  const maxProgressRef = useRef(0);
  const MAX_SSE_RETRIES = 5;

  // ── Timing refs (not state to avoid re-renders) ──
  const clockSkewMsRef = useRef(null);        // clientNow - serverNow (measured once)
  const processingStartMsRef = useRef(null);   // server created_at in client-adjusted ms
  const prevStatusRef = useRef(null);
  const prevStatusStartMsRef = useRef(null);   // when prev status started (client-adjusted)
  const emaRemainingRef = useRef(null);
  const elapsedTimerRef = useRef(null);
  const phaseDurationsRef = useRef({});

  // Upload elapsed time ticker
  useEffect(() => {
    if (!uploadStartTime) {
      return;
    }
    setUploadElapsedMs(Date.now() - uploadStartTime);
    const timer = setInterval(() => {
      setUploadElapsedMs(Date.now() - uploadStartTime);
    }, 1000);
    return () => clearInterval(timer);
  }, [uploadStartTime]);

  // Update smooth progress from external prop if provided (for upload progress)
  const setMonotonicProgress = useCallback((nextProgress) => {
    if (nextProgress < 0) {
      setSmoothProgress(nextProgress);
      return;
    }
    setSmoothProgress((prev) => {
      const safeProgress = Math.max(prev, nextProgress, maxProgressRef.current);
      maxProgressRef.current = safeProgress;
      return safeProgress;
    });
  }, []);

  useEffect(() => {
    if (externalProgress !== undefined && externalProgress !== null) {
      queueMicrotask(() => setMonotonicProgress(externalProgress));
    }
  }, [externalProgress, setMonotonicProgress]);

  // Helper to calculate progress percentage from status
  const calculateProgressFromStatus = useCallback((status) => {
    const statusMap = {
      NEW: 0,
      uploaded: 0,
      STEP_COMPRESS_1080P: 1,
      STEP_0_EXTRACT_FRAMES: 5,
      STEP_1_DETECT_PHASES: 10,
      STEP_2_EXTRACT_METRICS: 20,
      STEP_3_TRANSCRIBE_AUDIO: 55,
      STEP_4_IMAGE_CAPTION: 70,
      STEP_5_BUILD_PHASE_UNITS: 80,
      STEP_6_BUILD_PHASE_DESCRIPTION: 85,
      STEP_7_GROUPING: 90,
      STEP_8_UPDATE_BEST_PHASE: 92,
      STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES: 94,
      STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP: 95,
      STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS: 96,
      STEP_12_UPDATE_VIDEO_STRUCTURE_BEST: 97,
      STEP_12_5_PRODUCT_DETECTION: 97.5,
      STEP_13_BUILD_REPORTS: 98,
      STEP_14_FINALIZE: 99,
      STEP_14_SPLIT_VIDEO: 99,
      DONE: 100,
      ERROR: -1,
    };
    return statusMap[status] || 0;
  }, []);

  const calculateProgressCeilingFromStatus = useCallback((status) => {
    const ceilingMap = {
      NEW: 0,
      uploaded: 1,
      STEP_COMPRESS_1080P: 5,
      STEP_0_EXTRACT_FRAMES: 10,
      STEP_1_DETECT_PHASES: 20,
      STEP_2_EXTRACT_METRICS: 54,
      STEP_3_TRANSCRIBE_AUDIO: 69,
      STEP_4_IMAGE_CAPTION: 79,
      STEP_5_BUILD_PHASE_UNITS: 84,
      STEP_6_BUILD_PHASE_DESCRIPTION: 89,
      STEP_7_GROUPING: 91,
      STEP_8_UPDATE_BEST_PHASE: 93,
      STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES: 94,
      STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP: 95,
      STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS: 96,
      STEP_12_UPDATE_VIDEO_STRUCTURE_BEST: 97,
      STEP_12_5_PRODUCT_DETECTION: 97.9,
      STEP_13_BUILD_REPORTS: 98,
      STEP_14_FINALIZE: 99,
      STEP_14_SPLIT_VIDEO: 99,
      DONE: 100,
      ERROR: -1,
    };
    return ceilingMap[status] ?? 99;
  }, []);

  // ── Timing: calibrate clock skew & track phase transitions ──
  const handleTimingUpdate = useCallback((data) => {
    const now = Date.now();

    // 1) Calibrate clock skew (once per SSE session)
    if (clockSkewMsRef.current === null && data.server_now) {
      const serverNowMs = Date.parse(data.server_now);
      if (!isNaN(serverNowMs)) {
        clockSkewMsRef.current = now - serverNowMs;
      }
    }
    const skew = clockSkewMsRef.current || 0;

    // 2) Set processing start time from created_at (once)
    if (processingStartMsRef.current === null && data.created_at) {
      const createdMs = Date.parse(data.created_at);
      if (!isNaN(createdMs)) {
        processingStartMsRef.current = createdMs + skew;
        // Also try to restore from localStorage
        const saved = loadTimingState(data.video_id || videoId);
        if (saved && saved.phaseDurations) {
          phaseDurationsRef.current = saved.phaseDurations;
          setPhaseDurations(saved.phaseDurations);
        }
        if (saved && saved.emaRemaining !== undefined) {
          emaRemainingRef.current = saved.emaRemaining;
        }
      }
    }

    // 3) Track phase transitions
    const nextStatus = normalizeProcessingStatus(data.status);
    const prevStatus = prevStatusRef.current;

    // Use server updated_at as step start time (survives page reload)
    const serverUpdatedMs = data.updated_at ? Date.parse(data.updated_at) : null;
    const adjustedStepStart = (serverUpdatedMs && !isNaN(serverUpdatedMs)) ? serverUpdatedMs + skew : now;

    if (prevStatus && prevStatus !== nextStatus && prevStatusStartMsRef.current) {
      // Record duration of the previous phase
      const phaseDuration = adjustedStepStart - prevStatusStartMsRef.current;
      if (phaseDuration > 0) {
        phaseDurationsRef.current = {
          ...phaseDurationsRef.current,
          [prevStatus]: phaseDuration,
        };
        setPhaseDurations({ ...phaseDurationsRef.current });
      }
    }

    if (prevStatus !== nextStatus) {
      prevStatusRef.current = nextStatus;
      // Use server-based time so it persists across reloads
      prevStatusStartMsRef.current = adjustedStepStart;
    } else if (!prevStatusStartMsRef.current && serverUpdatedMs && !isNaN(serverUpdatedMs)) {
      // First SSE message after reload - restore step start from server time
      prevStatusRef.current = nextStatus;
      prevStatusStartMsRef.current = adjustedStepStart;
    }

    // 4) Compute elapsed time
    if (processingStartMsRef.current) {
      const elapsed = now - processingStartMsRef.current;
      setElapsedMs(elapsed);

      // 5) Compute estimated remaining (only when progress >= 3%)
      const progress = typeof data.progress === 'number' ? data.progress : calculateProgressFromStatus(nextStatus);
      if (progress >= 3 && progress < 100) {
        const rawRemaining = (elapsed / progress) * (100 - progress);
        // Clamp between 1 min and 60 min
        const clamped = Math.max(60_000, Math.min(rawRemaining, 3_600_000));
        emaRemainingRef.current = emaSmooth(emaRemainingRef.current, clamped, 0.3);
        setEstimatedRemainingMs(emaRemainingRef.current);
      } else if (progress >= 100) {
        setEstimatedRemainingMs(0);
      }
    }

    // 6) Persist to localStorage
    saveTimingState(data.video_id || videoId, {
      clockSkewMs: clockSkewMsRef.current,
      lastStatus: nextStatus,
      stepStartServerAt: data.updated_at,
      lastProgress: data.progress,
      phaseDurations: phaseDurationsRef.current,
      emaRemaining: emaRemainingRef.current,
    });
  }, [videoId, calculateProgressFromStatus]);

  // ── Elapsed time ticker (updates every second) ──
  useEffect(() => {
    elapsedTimerRef.current = setInterval(() => {
      if (processingStartMsRef.current && currentStatus !== 'DONE' && currentStatus !== 'ERROR') {
        const elapsed = Date.now() - processingStartMsRef.current;
        setElapsedMs(elapsed);

        // Also update remaining estimate based on current progress
        const progress = smoothProgress;
        if (progress >= 3 && progress < 100 && elapsed > 0) {
          const rawRemaining = (elapsed / progress) * (100 - progress);
          const clamped = Math.max(60_000, Math.min(rawRemaining, 3_600_000));
          emaRemainingRef.current = emaSmooth(emaRemainingRef.current, clamped, 0.15);
          setEstimatedRemainingMs(emaRemainingRef.current);
        }
      }
    }, 1000);

    return () => {
      if (elapsedTimerRef.current) {
        clearInterval(elapsedTimerRef.current);
        elapsedTimerRef.current = null;
      }
    };
  }, [currentStatus, smoothProgress]);

  // Start gradual progress increase
  const startGradualProgress = useCallback((targetProgress, status) => {
    // Clear any existing interval
    if (progressIntervalRef.current) {
      clearInterval(progressIntervalRef.current);
    }

    const ceiling = calculateProgressCeilingFromStatus(status);
    const boundedTarget = Math.min(targetProgress, ceiling);
    setMonotonicProgress(boundedTarget);

    const isQuickStep = status === 'STEP_COMPRESS_1080P' || status === 'STEP_0_EXTRACT_FRAMES';
    const isLongStep = status === 'STEP_3_TRANSCRIBE_AUDIO' || status === 'STEP_2_EXTRACT_METRICS';
    const minIncrement = isQuickStep ? 0.3 : isLongStep ? 0.1 : 0.5;
    const maxIncrement = isQuickStep ? 0.8 : isLongStep ? 0.3 : 1.0;
    const minInterval = isQuickStep ? 500 : isLongStep ? 3000 : 1500;
    const maxInterval = isQuickStep ? 1200 : isLongStep ? 6000 : 3000;

    progressIntervalRef.current = setInterval(() => {
      setSmoothProgress(prev => {
        if (prev < 0) return prev;
        const increment = Math.random() * (maxIncrement - minIncrement) + minIncrement;
        const newProgress = Math.min(prev + increment, ceiling);
        const monotonicProgress = Math.max(newProgress, maxProgressRef.current);
        maxProgressRef.current = monotonicProgress;

        if (monotonicProgress >= ceiling) {
          if (progressIntervalRef.current) {
            clearInterval(progressIntervalRef.current);
            progressIntervalRef.current = null;
          }
          return ceiling;
        }

        return monotonicProgress;
      });
    }, minInterval + Math.random() * (maxInterval - minInterval));
  }, [calculateProgressCeilingFromStatus, setMonotonicProgress]);

  // Callback when processing completes
  const handleProcessingComplete = useCallback(() => {
    if (onProcessingComplete) {
      onProcessingComplete();
    }
  }, [onProcessingComplete]);

  // ── Fetch error logs handler ──
  const fetchErrorLogs = useCallback(async () => {
    if (!videoId || loadingErrorLogs) return;
    setLoadingErrorLogs(true);
    try {
      const res = await VideoService.getErrorLogs(videoId);
      if (res?.error_logs) setErrorLogs(res.error_logs);
    } catch (err) {
      console.error('Failed to fetch error logs:', err);
    } finally {
      setLoadingErrorLogs(false);
    }
  }, [videoId, loadingErrorLogs]);

  // ── Retry analysis handler ──
  const handleRetryAnalysis = useCallback(async () => {
    if (!videoId || isRetrying) return;
    setIsRetrying(true);
    try {
      const result = await VideoService.retryAnalysis(videoId);
      // Use the resume status from API response.
      // Fallback to STEP_COMPRESS_1080P which now shows as "解析準備中" (not "圧縮中")
      const resumeStatus = normalizeProcessingStatus(result?.new_status || 'uploaded');
      setIsStalled(false);
      setErrorMessage(null);
      setCurrentStatus(resumeStatus);
      setSmoothProgress(0);
      maxProgressRef.current = 0;
      setStepProgress(0);
      setElapsedMs(0);
      setEstimatedRemainingMs(null);
      lastProgressRef.current = null;
      retryCountRef.current = 0;
      lastInitializedVideoIdRef.current = null; // Force SSE re-init
      // Trigger re-render which will re-establish SSE
    } catch (err) {
      console.error('Retry analysis failed:', err);
      setErrorMessage('再試行に失敗しました。しばらく待ってからもう一度お試しください。');
    } finally {
      setIsRetrying(false);
    }
  }, [videoId, isRetrying]);

  // ── Stall detection: track progress changes ──
  useEffect(() => {
    const isProcessing = currentStatus !== 'NEW' && currentStatus !== 'UPLOADING'
      && currentStatus !== 'DONE' && currentStatus !== 'ERROR';

    if (!isProcessing) {
      // Not processing → clear stall state
      setIsStalled(false);
      lastProgressRef.current = null;
      if (stallCheckTimerRef.current) {
        clearInterval(stallCheckTimerRef.current);
        stallCheckTimerRef.current = null;
      }
      return;
    }

    // Record progress change
    const currentProgress = smoothProgress;
    const now = Date.now();
    if (!lastProgressRef.current || lastProgressRef.current.progress !== currentProgress) {
      lastProgressRef.current = { progress: currentProgress, timestamp: now };
      setIsStalled(false); // Progress moved, clear stall
    }

    // Start periodic stall check if not already running
    if (!stallCheckTimerRef.current) {
      stallCheckTimerRef.current = setInterval(() => {
        if (lastProgressRef.current) {
          const elapsed = Date.now() - lastProgressRef.current.timestamp;
          if (elapsed >= STALL_DETECT_MS) {
            setIsStalled(true);
          }
        }
      }, 30000); // Check every 30 seconds
    }

    return () => {
      if (stallCheckTimerRef.current) {
        clearInterval(stallCheckTimerRef.current);
        stallCheckTimerRef.current = null;
      }
    };
  }, [currentStatus, smoothProgress]);

  // Polling fallback
  const startPolling = useCallback(() => {
    if (!videoId) return;

    console.log('📊 Starting polling fallback for video status');
    setUsePolling(true);
    setErrorMessage(null);

    const poll = async () => {
      try {
        const response = await VideoService.getVideoById(videoId);
        if (response && response.status) {
          const newStatus = normalizeProcessingStatus(response.status);
          setCurrentStatus(newStatus);

          const serverStepProgress = typeof response.step_progress === 'number' ? response.step_progress : 0;
          setStepProgress(serverStepProgress);

          const floor = calculateProgressFromStatus(newStatus);
          const ceiling = calculateProgressCeilingFromStatus(newStatus);
          let progress;
          if (serverStepProgress > 0 && serverStepProgress < 100) {
            progress = Math.round(floor + (ceiling - floor) * serverStepProgress / 100);
          } else {
            const serverProgress = typeof response.progress === 'number' ? response.progress : 0;
            progress = Math.max(serverProgress, floor);
          }

          if (serverStepProgress > 0) {
            setMonotonicProgress(progress);
          } else {
            startGradualProgress(progress, newStatus);
          }
          lastStatusChangeRef.current = Date.now();

          // Update timing from polling data
          handleTimingUpdate({
            status: newStatus,
            progress,
            created_at: response.created_at,
            updated_at: response.updated_at,
            server_now: response.server_now,
            video_id: videoId,
          });

          if (newStatus === 'DONE' || newStatus === 'ERROR') {
            if (pollingIntervalRef.current) {
              clearInterval(pollingIntervalRef.current);
              pollingIntervalRef.current = null;
            }
            if (newStatus === 'DONE' && handleProcessingComplete) {
              handleProcessingComplete();
            }
          }
        }
      } catch (err) {
        console.error('Polling error:', err);
      }
    };

    poll();
    pollingIntervalRef.current = setInterval(poll, 5000);
  }, [videoId, calculateProgressFromStatus, calculateProgressCeilingFromStatus, startGradualProgress, handleProcessingComplete, handleTimingUpdate, setMonotonicProgress]);

  // Stream status updates if video is processing
  useEffect(() => {
    // Only reset state when videoId actually changes
    if (lastInitializedVideoIdRef.current !== videoId) {
      const initial = normalizeProcessingStatus(initialStatus || 'NEW');
      queueMicrotask(() => {
        setCurrentStatus(initial);
        const initialProgress = calculateProgressFromStatus(initial);
        maxProgressRef.current = Math.max(initialProgress, 0);
        setSmoothProgress(initialProgress);
        setErrorMessage(null);
        setUsePolling(false);
      });
      lastStatusChangeRef.current = Date.now();
      retryCountRef.current = 0;

      // Reset timing state for new video
      clockSkewMsRef.current = null;
      processingStartMsRef.current = null;
      prevStatusRef.current = null;
      prevStatusStartMsRef.current = null;
      emaRemainingRef.current = null;
      phaseDurationsRef.current = {};
      setElapsedMs(0);
      setEstimatedRemainingMs(null);
      setPhaseDurations({});

      // Try to restore timing from localStorage
      const saved = loadTimingState(videoId);
      if (saved) {
        if (saved.clockSkewMs !== undefined) clockSkewMsRef.current = saved.clockSkewMs;
        if (saved.phaseDurations) {
          phaseDurationsRef.current = saved.phaseDurations;
          setPhaseDurations(saved.phaseDurations);
        }
        if (saved.emaRemaining !== undefined) emaRemainingRef.current = saved.emaRemaining;
      }
    }

    if (!videoId) {
      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current);
        progressIntervalRef.current = null;
      }
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
        pollingIntervalRef.current = null;
      }
      return;
    }

    if (lastInitializedVideoIdRef.current === videoId) {
      console.log(`⚠️  Stream already initialized for video ${videoId}, skipping duplicate`);
      return;
    }

    if (statusStreamRef.current) {
      statusStreamRef.current.close();
      statusStreamRef.current = null;
    }

    lastInitializedVideoIdRef.current = videoId;

    statusStreamRef.current = VideoService.streamVideoStatus({
      videoId: videoId,

      onStatusUpdate: (data) => {
        console.log(`📡 SSE Update: ${data.status}, step_progress: ${data.step_progress}`);
        const nextStatus = normalizeProcessingStatus(data.status);
        setCurrentStatus(nextStatus);
        setErrorMessage(null);
        retryCountRef.current = 0;

        // Track enqueue & worker evidence
        if (data.enqueue_status !== undefined) setEnqueueStatus(data.enqueue_status);
        if (data.worker_claimed_at !== undefined) setWorkerClaimedAt(data.worker_claimed_at);
        if (data.enqueue_error !== undefined) setEnqueueError(data.enqueue_error);

        const serverStepProgress = typeof data.step_progress === 'number' ? data.step_progress : 0;
        setStepProgress(serverStepProgress);

        const floor = calculateProgressFromStatus(nextStatus);
        const ceiling = calculateProgressCeilingFromStatus(nextStatus);
        let safeProgress;
        if (serverStepProgress > 0 && serverStepProgress < 100) {
          safeProgress = Math.round(floor + (ceiling - floor) * serverStepProgress / 100);
        } else {
          const serverProgress = typeof data.progress === 'number' ? data.progress : 0;
          safeProgress = Math.max(serverProgress, floor);
        }

        if (serverStepProgress > 0) {
          if (progressIntervalRef.current) {
            clearInterval(progressIntervalRef.current);
            progressIntervalRef.current = null;
          }
          setMonotonicProgress(safeProgress);
        } else {
          startGradualProgress(safeProgress, nextStatus);
        }
        lastStatusChangeRef.current = Date.now();

        // ── Timing update ──
        handleTimingUpdate(data);

        if (nextStatus === 'DONE' || nextStatus === 'ERROR') {
          console.log(`✅ Stream auto-closing due to status: ${nextStatus}`);
          if (statusStreamRef.current) {
            statusStreamRef.current.close();
            statusStreamRef.current = null;
          }
          if (nextStatus === 'DONE') {
            // Record final phase duration
            if (prevStatusRef.current && prevStatusStartMsRef.current) {
              const finalDuration = Date.now() - prevStatusStartMsRef.current;
              phaseDurationsRef.current = {
                ...phaseDurationsRef.current,
                [prevStatusRef.current]: (phaseDurationsRef.current[prevStatusRef.current] || 0) + finalDuration,
              };
              setPhaseDurations({ ...phaseDurationsRef.current });
            }
            setEstimatedRemainingMs(0);
            if (handleProcessingComplete) {
              handleProcessingComplete();
            }
          }
          if (nextStatus === 'ERROR') {
            setEstimatedRemainingMs(null);
            // Capture latest_error from SSE payload
            if (data.latest_error) {
              setLatestError(data.latest_error);
            }
            // Auto-fetch error logs when error occurs
            VideoService.getErrorLogs(videoId).then(res => {
              if (res?.error_logs) setErrorLogs(res.error_logs);
            }).catch(() => {});
          }
        }
      },

      onDone: async () => {
        console.log('✅ SSE Stream completed');
        setCurrentStatus('DONE');
        maxProgressRef.current = 100;
        setSmoothProgress(100);
        setEstimatedRemainingMs(0);
        if (handleProcessingComplete) {
          handleProcessingComplete();
        }
      },

      onError: (error) => {
        console.error('❌ Status stream error:', error);
        retryCountRef.current++;

        if (retryCountRef.current > MAX_SSE_RETRIES) {
          console.warn(`SSE failed ${MAX_SSE_RETRIES} times, falling back to polling`);
          startPolling();
          // Clear error message after polling starts - polling works fine
          setErrorMessage(null);
        } else {
          // Don't show alarming error for transient SSE reconnects
          console.log(`SSE retry ${retryCountRef.current}/${MAX_SSE_RETRIES}`);
        }
      },
    });

    return () => {
      const videoIdChanged = lastInitializedVideoIdRef.current !== videoId;

      if (videoIdChanged) {
        console.log(`🧹 Cleaning up SSE stream for video ${videoId} (videoId changed)`);
        if (statusStreamRef.current) {
          statusStreamRef.current.close();
          statusStreamRef.current = null;
        }
      }

      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current);
        progressIntervalRef.current = null;
      }
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
        pollingIntervalRef.current = null;
      }
    };
  }, [videoId]);

  // ── Visibility change: refresh status when tab becomes active ──
  useEffect(() => {
    if (!videoId) return;

    const handleVisibilityChange = async () => {
      if (document.hidden) return; // Only act when tab becomes visible

      // Skip if already DONE or ERROR
      if (currentStatus === 'DONE' || currentStatus === 'ERROR') return;

      console.log('👁️ Tab became visible, refreshing video status');
      try {
        const response = await VideoService.getVideoById(videoId);
        if (response && response.status) {
          const newStatus = normalizeProcessingStatus(response.status);
          console.log(`👁️ Refreshed status: ${newStatus} (was: ${currentStatus})`);
          setCurrentStatus(newStatus);

          const serverStepProgress = typeof response.step_progress === 'number' ? response.step_progress : 0;
          setStepProgress(serverStepProgress);

          const floor = calculateProgressFromStatus(newStatus);
          const ceiling = calculateProgressCeilingFromStatus(newStatus);
          let progress;
          if (serverStepProgress > 0 && serverStepProgress < 100) {
            progress = Math.round(floor + (ceiling - floor) * serverStepProgress / 100);
          } else {
            const serverProgress = typeof response.progress === 'number' ? response.progress : 0;
            progress = Math.max(serverProgress, floor);
          }
          setMonotonicProgress(progress);
          lastStatusChangeRef.current = Date.now();

          handleTimingUpdate({
            status: newStatus,
            progress,
            created_at: response.created_at,
            updated_at: response.updated_at,
            server_now: response.server_now,
            video_id: videoId,
          });

          if (newStatus === 'DONE') {
            // Close SSE if still open
            if (statusStreamRef.current) {
              statusStreamRef.current.close();
              statusStreamRef.current = null;
            }
            if (pollingIntervalRef.current) {
              clearInterval(pollingIntervalRef.current);
              pollingIntervalRef.current = null;
            }
            if (handleProcessingComplete) {
              handleProcessingComplete();
            }
          } else if (newStatus === 'ERROR') {
            if (statusStreamRef.current) {
              statusStreamRef.current.close();
              statusStreamRef.current = null;
            }
            if (pollingIntervalRef.current) {
              clearInterval(pollingIntervalRef.current);
              pollingIntervalRef.current = null;
            }
            VideoService.getErrorLogs(videoId).then(res => {
              if (res?.error_logs) setErrorLogs(res.error_logs);
            }).catch(() => {});
          } else {
            // Still processing: ensure SSE or polling is active
            if (!statusStreamRef.current && !pollingIntervalRef.current) {
              console.log('👁️ No active SSE or polling, restarting polling');
              startPolling();
            }
          }
        }
      } catch (err) {
        console.warn('👁️ Failed to refresh status on visibility change:', err);
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [videoId, currentStatus, calculateProgressFromStatus, calculateProgressCeilingFromStatus, setMonotonicProgress, handleTimingUpdate, handleProcessingComplete, startPolling]);

  // Fetch error logs on mount and when status changes to ERROR
  // Always fetch on mount so that previous error history is available during re-analysis
  useEffect(() => {
    if (videoId) {
      VideoService.getErrorLogs(videoId).then(res => {
        if (res?.error_logs) setErrorLogs(res.error_logs);
      }).catch(() => {});
    }
  }, [videoId]);

  // Also re-fetch when status transitions to ERROR (SSE notification)
  useEffect(() => {
    if (videoId && currentStatus === 'ERROR') {
      VideoService.getErrorLogs(videoId).then(res => {
        if (res?.error_logs) setErrorLogs(res.error_logs);
      }).catch(() => {});
    }
  }, [videoId, currentStatus]);

  // Clean up localStorage when DONE
  useEffect(() => {
    if (currentStatus === 'DONE' && videoId) {
      // Keep data for 5 minutes after completion for review, then clean up
      const timeout = setTimeout(() => clearTimingState(videoId), 5 * 60 * 1000);
      return () => clearTimeout(timeout);
    }
  }, [currentStatus, videoId]);

  // Dynamic upload step label: show "アップロード中..." during upload, "アップロード完了" when done
  const uploadStepLabel = (() => {
    if (currentStatus === 'UPLOADING' || (externalProgress !== undefined && externalProgress < 100 && externalProgress >= 0)) {
      return window.__t('statusUploading') || 'アップロード中...';
    }
    return window.__t('statusUploaded') || 'アップロード完了';
  })();
  const uploadStep = { key: 'uploaded', label: uploadStepLabel };

  // Derive queue/worker status label for display between upload and compress
  const getQueueStatusLabel = () => {
    if (enqueueError) return `キュー投入失敗: ${enqueueError}`;
    if (!enqueueStatus) return 'キュー投入中...';
    if (enqueueStatus === 'FAILED') return 'キュー投入失敗';
    if (!workerClaimedAt) return 'キュー待ち（ワーカー未受信）';
    return 'ワーカー受信済み';
  };

  const queueStep = { key: 'QUEUE_WAITING', label: getQueueStatusLabel() };

  const analysisSteps = [
    { key: 'STEP_COMPRESS_1080P', label: window.__t('statusCompress') || '解析準備中（ダウンロード・前処理）...' },
    { key: 'STEP_0_EXTRACT_FRAMES', label: window.__t('statusStep0') || 'フレーム抽出中...' },
    { key: 'STEP_1_DETECT_PHASES', label: window.__t('statusStep1') || 'フェーズ検出中...' },
    { key: 'STEP_2_EXTRACT_METRICS', label: window.__t('statusStep2') || 'メトリクス抽出中...' },
    { key: 'STEP_3_TRANSCRIBE_AUDIO', label: window.__t('statusStep3') || '音声文字起こし中...' },
    { key: 'STEP_4_IMAGE_CAPTION', label: window.__t('statusStep4') || '画像キャプション生成中...' },
    { key: 'STEP_5_BUILD_PHASE_UNITS', label: window.__t('statusStep5') || 'フェーズユニット構築中...' },
    { key: 'STEP_6_BUILD_PHASE_DESCRIPTION', label: window.__t('statusStep6') || 'フェーズ説明構築中...' },
    { key: 'STEP_7_GROUPING', label: window.__t('statusStep7') || 'グループ化中...' },
    { key: 'STEP_8_UPDATE_BEST_PHASE', label: window.__t('statusStep8') || 'ベストフェーズ更新中...' },
    { key: 'STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES', label: window.__t('statusStep9') || '動画構造特徴構築中...' },
    { key: 'STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP', label: window.__t('statusStep10') || '動画構造グループ割り当て中...' },
    { key: 'STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS', label: window.__t('statusStep11') || '動画構造グループ統計更新中...' },
    { key: 'STEP_12_UPDATE_VIDEO_STRUCTURE_BEST', label: window.__t('statusStep12') || '動画構造ベスト更新中...' },
    { key: 'STEP_12_5_PRODUCT_DETECTION', label: window.__t('statusStep12_5') || '商品検出中...' },
    { key: 'STEP_13_BUILD_REPORTS', label: window.__t('statusStep13') || 'レポート構築中...' },
    { key: 'STEP_14_FINALIZE', label: window.__t('statusStep14') || '動画分割中...' },
    { key: 'DONE', label: window.__t('statusDone') || '完了' },
  ];

  const getUploadStepStatus = () => {
    if (currentStatus === 'UPLOADING') return 'current';
    if (currentStatus === 'NEW') return 'pending';
    return 'completed';
  };

  const getAnalysisStepStatus = (stepKey) => {
    if (currentStatus === 'ERROR') return 'error';
    if (currentStatus === 'NEW' || currentStatus === 'UPLOADING') {
      return 'pending';
    }

    const currentIndex = analysisSteps.findIndex(s => s.key === currentStatus);
    const stepIndex = analysisSteps.findIndex(s => s.key === stepKey);

    if (currentIndex === -1) return 'pending';
    if (stepIndex < currentIndex) return 'completed';
    if (stepIndex === currentIndex) return 'current';
    return 'pending';
  };

  const renderStepIcon = (status) => {
    if (status === 'completed') {
      return (
        <div className="flex items-center justify-center w-6 h-6 rounded-full bg-green-500 text-white transition-all duration-500 ease-out">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
            <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
          </svg>
        </div>
      );
    }

    if (status === 'current') {
      return (
        <div className="flex items-center justify-center w-6 h-6 rounded-full scale-105 transition-all duration-500 ease-out">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#374151"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="lucide lucide-loader-circle-icon lucide-loader-circle w-[18px] h-[18px] animate-spin"
          >
            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
          </svg>
        </div>
      );
    }

    if (status === 'error') {
      return (
        <div className="flex items-center justify-center w-6 h-6 rounded-full bg-red-500/20 text-red-400">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-5a.75.75 0 01.75.75v4.5a.75.75 0 01-1.5 0v-4.5A.75.75 0 0110 5zm0 10a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
          </svg>
        </div>
      );
    }

    // Pending - hollow circle
    return (
      <div className="flex items-center justify-center w-6 h-6 rounded-full transition-all duration-500 ease-out">
        <svg
          className="w-5 h-5"
          xmlns="http://www.w3.org/2000/svg"
          width="24"
          height="24"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#ffffff"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="10" />
        </svg>
      </div>
    );
  };

  // Get visible analysis steps window
  const { visibleAnalysisSteps, isAnalysisFirst, isAnalysisLast, currentAnalysisIndex } = useMemo(() => {
    const totalSteps = analysisSteps.length;
    const foundIndex = analysisSteps.findIndex(s => s.key === currentStatus);
    const currentIndex = foundIndex >= 0 ? foundIndex : 0;

    let startIndex = Math.max(0, currentIndex - 2);
    let endIndex = Math.min(totalSteps, startIndex + 5);

    if (endIndex - startIndex < 5) {
      startIndex = Math.max(0, endIndex - 5);
    }

    return {
      visibleAnalysisSteps: analysisSteps.slice(startIndex, endIndex),
      isAnalysisFirst: startIndex === 0,
      isAnalysisLast: endIndex === totalSteps,
      currentAnalysisIndex: currentIndex,
    };
  }, [currentStatus]);

  const isError = currentStatus === 'ERROR';
  const isDone = currentStatus === 'DONE';
  const uploadStepStatus = getUploadStepStatus();
  const currentAnalysisLabel = visibleAnalysisSteps.find(
    (step) => getAnalysisStepStatus(step.key) === 'current',
  )?.label;
  const progressLabel = uploadStepStatus === 'current'
    ? (window.__t('statusUploading') || 'アップロード中...')
    : (currentAnalysisLabel || (window.__t('statusAnalyzing') || '解析中...'));
  // Format video duration (seconds) to human-readable Japanese format
  const formatVideoDuration = (sec) => {
    if (!sec || !isFinite(sec)) return null;
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    if (h > 0) return `${h}時間${m > 0 ? m + '分' : ''}${s > 0 ? s + '秒' : ''}`;
    if (m > 0) return `${m}分${s > 0 ? s + '秒' : ''}`;
    return `${s}秒`;
  };

  // Format upload duration (ms) to human-readable
  const formatUploadDur = (ms) => {
    if (!ms || ms < 1000) return null;
    const totalSec = Math.floor(ms / 1000);
    if (totalSec < 60) return `${totalSec}秒`;
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return s > 0 ? `${m}分${s}秒` : `${m}分`;
  };

  const videoTitleNode = useMemo(() => {
    if (!videoTitle) return null;
    const durLabel = formatVideoDuration(videoDurationSec);
    const uploadDurLabel = formatUploadDur(uploadDurationMs);
    const hasInfo = durLabel || uploadDurLabel;
    return (
      <div className="flex justify-center mb-5">
        <div className="inline-flex flex-col items-center px-4 py-2 rounded-full border border-gray-200 bg-gray-50">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium whitespace-nowrap text-gray-700">
              {videoTitle}
            </span>
            {hasInfo && (
              <span className="text-[11px] text-gray-400 whitespace-nowrap">
                {durLabel && (
                  <span title="動画の再生時間">🎬 {durLabel}</span>
                )}
                {durLabel && uploadDurLabel && (
                  <span className="mx-1">|</span>
                )}
                {uploadDurLabel && (
                  <span title="アップロード所要時間">⬆ {uploadDurLabel}</span>
                )}
              </span>
            )}
          </div>
        </div>
      </div>
    );
  }, [videoTitle, videoDurationSec, uploadDurationMs]);

  // ── Determine if we should show timing info ──
  const showTiming = elapsedMs > 0 && currentStatus !== 'NEW' && currentStatus !== 'UPLOADING';
  const showRemaining = estimatedRemainingMs !== null && estimatedRemainingMs > 0 && smoothProgress >= 3 && !isDone;

  return (
    <div className="w-full">
      {/* Video title */}
      {videoTitleNode}

      {/* Fixed upload step + scrolling analysis steps */}
      <div className="mb-4 space-y-2">
        <div className="flex items-center gap-3 transition-all duration-500 ease-out">
          {renderStepIcon(uploadStepStatus)}
          <span className={`text-sm transition-all duration-500 ease-out ${uploadStepStatus === 'current' ? 'text-gray-800 font-medium' : 'text-green-600'}`}>
            {uploadStep.label}
            {/* Show upload duration for completed upload */}
            {uploadStepStatus === 'completed' && uploadDurationMs && uploadDurationMs > 1000 && (
              <span className="ml-2 text-[11px] text-gray-400 font-normal">{formatDuration(uploadDurationMs)}</span>
            )}
            {/* Show live upload elapsed time */}
            {uploadStepStatus === 'current' && uploadElapsedMs > 1000 && (
              <span className="ml-2 text-[11px] text-gray-400 font-normal">{formatDuration(uploadElapsedMs)}</span>
            )}
          </span>
        </div>

        {/* Queue / Worker status step (between upload and analysis) */}
        {uploadStepStatus === 'completed' && currentStatus !== 'DONE' && currentStatus !== 'ERROR' && (
          <div className="flex items-center gap-3 transition-all duration-500 ease-out">
            {workerClaimedAt ? (
              renderStepIcon('completed')
            ) : enqueueStatus === 'FAILED' ? (
              renderStepIcon('error')
            ) : enqueueStatus === 'OK' ? (
              renderStepIcon('current')
            ) : (
              renderStepIcon('current')
            )}
            <span className={`text-sm transition-all duration-500 ease-out ${
              enqueueStatus === 'FAILED' ? 'text-red-500 font-medium' :
              workerClaimedAt ? 'text-green-600' :
              'text-gray-800 font-medium'
            }`}>
              {queueStep.label}
            </span>
          </div>
        )}

        <div className="pt-1 pb-1 text-left">
          <p className="text-[11px] text-gray-400">
            {window.__t('analysisSectionHint') || 'アップロード完了後、解析ステップを実行中'}
          </p>
        </div>

        {/* Show ellipsis if analysis window is not at start */}
        {!isAnalysisFirst && (
          <div className="flex items-center gap-3 text-gray-500">
            <div className="flex items-center justify-center w-6">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clipRule="evenodd" />
              </svg>
            </div>
            <span className="text-xs text-gray-500">...</span>
          </div>
        )}

        {/* Visible analysis steps */}
        {visibleAnalysisSteps.map((step) => {
          const stepStatus = getAnalysisStepStatus(step.key);
          const isActive = stepStatus === 'current';
          const isCompleted = stepStatus === 'completed';
          const stepGlobalIndex = analysisSteps.findIndex((analysisStep) => analysisStep.key === step.key);
          const distanceFromCurrent = currentAnalysisIndex >= 0
            ? Math.abs(stepGlobalIndex - currentAnalysisIndex)
            : 0;
          const transitionDelay = `${Math.min(distanceFromCurrent, 4) * 45}ms`;
          const phaseDur = phaseDurations[step.key];

          return (
            <div
              key={step.key}
              className={`flex items-center gap-3 transition-all duration-500 ease-out will-change-transform ${isActive
                ? 'opacity-100 translate-y-0 scale-[1.01] ml-1'
                : isCompleted
                  ? 'opacity-95 translate-y-0 scale-100'
                  : 'opacity-70 translate-y-px scale-[0.99]'
                }`}
              style={{ transitionDelay }}
            >
              {renderStepIcon(stepStatus)}
              <span className={`text-sm transition-all duration-500 ease-out ${isActive ? 'text-gray-800 font-medium' :
                isCompleted ? 'text-green-600' :
                  'text-gray-400'
                }`}>
                {step.label}
                {isActive && stepProgress > 0 && stepProgress < 100 && (
                  <span className="ml-2 text-xs text-indigo-500 font-semibold">{stepProgress}%</span>
                )}
                {/* Show phase duration for completed steps */}
                {isCompleted && phaseDur && phaseDur > 1000 && (
                  <span className="ml-2 text-[11px] text-gray-400 font-normal">{formatDuration(phaseDur)}</span>
                )}
                {/* Show live elapsed time for current step */}
                {isActive && prevStatusStartMsRef.current && (
                  <LiveStepTimer startMs={prevStatusStartMsRef.current} />
                )}
              </span>
            </div>
          );
        })}

        {/* Show ellipsis if analysis window is not at end */}
        {!isAnalysisLast && (
          <div className="flex items-center gap-3 text-gray-500">
            <div className="flex items-center justify-center w-6">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M14.77 12.79a.75.75 0 01-1.06-.02L10 8.832l-3.71 3.938a.75.75 0 11-1.08-1.04l4.25-4.5a.75.75 0 011.08 0l4.25 4.5a.75.75 0 01-.02 1.06z" clipRule="evenodd" />
              </svg>
            </div>
            <span className="text-xs text-gray-500">...</span>
          </div>
        )}
      </div>

      {/* Progress bar */}
      {!isError && smoothProgress >= 0 && (
        <>
          <div className="w-full h-2 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-2 rounded-full transition-all duration-700 ease-in-out bg-linear-to-r from-indigo-500 to-violet-400"
              style={{ width: `${smoothProgress}%` }}
            />
          </div>
          {/* Current status + progress + timing */}
          <div className="flex items-center justify-between mb-1 mt-2">
            <span className="text-sm text-gray-600">
              {progressLabel}
            </span>
            <span className="text-sm text-gray-600">
              {Math.round(smoothProgress)}%
            </span>
          </div>

          {/* ── Elapsed & Remaining time display ── */}
          {showTiming && (
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs text-gray-400 flex items-center gap-1">
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                {formatDuration(elapsedMs)} 経過
                {showRemaining && (
                  <span className="text-gray-300 mx-1">/</span>
                )}
                {showRemaining && (
                  <span>残り 約{formatDuration(estimatedRemainingMs)}</span>
                )}
              </span>
              {isDone && (
                <span className="text-xs text-green-500 font-medium">
                  合計 {formatDuration(elapsedMs)}
                </span>
              )}
            </div>
          )}

          {!showTiming && <div className="mb-3" />}

          <p className="text-sm text-gray-400 mt-5 text-center">
            {window.__t('progressCompleteMessage') || '解析が完了すると、自動的に結果が表示されます。'}
          </p>

          {/* Show warning if using polling fallback */}
          {errorMessage && (
            <p className="text-xs text-yellow-400 mt-2 text-center">
              {errorMessage}
            </p>
          )}

          {/* ── Stall warning + retry button ── */}
          {isStalled && !isDone && !isError && (
            <div className="mt-4 p-3 bg-amber-50 border border-amber-200 rounded-lg text-center">
              <p className="text-sm text-amber-700 mb-2">
                解析が停止している可能性があります（{STALL_DETECT_MINUTES}分以上進捗なし）
              </p>
              <button
                onClick={handleRetryAnalysis}
                disabled={isRetrying}
                className="px-4 py-1.5 text-sm font-medium text-white bg-amber-500 hover:bg-amber-600 disabled:bg-amber-300 rounded-md transition-colors"
              >
                {isRetrying ? '再試行中...' : '解析を再試行'}
              </button>
            </div>
          )}
        </>
      )}

      {/* Error message + error details + retry button */}
      {isError && (
        <div className="mt-2">
          <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-sm text-red-600 font-medium mb-2 text-center">
              {window.__t('errorAnalysisMessage') || '解析中にエラーが発生しました。'}
            </p>

            {/* Latest error detail */}
            {latestError && (
              <div className="mt-2 p-3 bg-white border border-red-100 rounded-md text-left">
                <div className="flex items-center gap-2 mb-1">
                  <span className="inline-block px-2 py-0.5 text-[11px] font-mono font-semibold bg-red-100 text-red-700 rounded">
                    {latestError.error_code || 'UNKNOWN'}
                  </span>
                  {latestError.error_step && (
                    <span className="text-[11px] text-gray-500">
                      @ {latestError.error_step}
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-700 break-words">
                  {latestError.error_message || 'エラーの詳細情報がありません'}
                </p>
                {latestError.created_at && (
                  <p className="text-[10px] text-gray-400 mt-1">
                    {new Date(latestError.created_at).toLocaleString('ja-JP')}
                  </p>
                )}
              </div>
            )}

            {/* Retry button */}
            <div className="text-center mt-3">
              <button
                onClick={handleRetryAnalysis}
                disabled={isRetrying}
                className="px-4 py-1.5 text-sm font-medium text-white bg-red-500 hover:bg-red-600 disabled:bg-red-300 rounded-md transition-colors"
              >
                {isRetrying ? '再試行中...' : '解析を再試行'}
              </button>
            </div>
          </div>

          {/* Error log history toggle - always show button */}
          <div className="mt-3">
            <button
              onClick={() => {
                setShowErrorLogs(!showErrorLogs);
                if (!showErrorLogs && errorLogs.length === 0) fetchErrorLogs();
              }}
              className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 transition-colors mx-auto"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
              </svg>
              エラーログ {errorLogs.length > 0 ? `(${errorLogs.length}件)` : ''}
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                className={`transition-transform duration-200 ${showErrorLogs ? 'rotate-180' : ''}`}>
                <polyline points="6 9 12 15 18 9"/>
              </svg>
            </button>

            {showErrorLogs && (
              <div className="mt-2 max-h-60 overflow-y-auto space-y-2">
                {loadingErrorLogs && (
                  <p className="text-xs text-gray-400 text-center py-2">読み込み中...</p>
                )}
                {!loadingErrorLogs && errorLogs.length === 0 && (
                  <p className="text-xs text-gray-400 text-center py-2">エラーログはありません</p>
                )}
                {errorLogs.map((log, idx) => (
                  <div key={log.id || idx} className="p-2.5 bg-gray-50 border border-gray-200 rounded-md text-left">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="inline-block px-1.5 py-0.5 text-[10px] font-mono font-semibold bg-red-50 text-red-600 rounded">
                        {log.error_code || 'UNKNOWN'}
                      </span>
                      {log.error_step && (
                        <span className="text-[10px] text-gray-500">@ {log.error_step}</span>
                      )}
                      {log.source && (
                        <span className="text-[10px] text-gray-400">({log.source})</span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-600 break-words">{log.error_message}</p>
                    {log.created_at && (
                      <p className="text-[10px] text-gray-400 mt-1">
                        {new Date(log.created_at).toLocaleString('ja-JP')}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}

            {showErrorLogs && (
              <div className="text-center mt-2">
                <button
                  onClick={fetchErrorLogs}
                  disabled={loadingErrorLogs}
                  className="text-[11px] text-gray-400 hover:text-gray-600 underline transition-colors"
                >
                  {loadingErrorLogs ? '読み込み中...' : 'エラーログを更新'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Error log history - shown even during re-analysis if previous errors exist */}
      {!isError && errorLogs.length > 0 && (
        <div className="mt-3">
          <button
            onClick={() => {
              setShowErrorLogs(!showErrorLogs);
            }}
            className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 transition-colors mx-auto"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/>
              <line x1="16" y1="17" x2="8" y2="17"/>
            </svg>
            前回のエラー履歴 ({errorLogs.length}件)
            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              className={`transition-transform duration-200 ${showErrorLogs ? 'rotate-180' : ''}`}>
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </button>

          {showErrorLogs && (
            <div className="mt-2 max-h-40 overflow-y-auto space-y-2">
              {errorLogs.map((log, idx) => (
                <div key={log.id || idx} className="p-2 bg-amber-50 border border-amber-200 rounded-md text-left">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="inline-block px-1.5 py-0.5 text-[10px] font-mono font-semibold bg-amber-100 text-amber-700 rounded">
                      {log.error_code || 'UNKNOWN'}
                    </span>
                    {log.error_step && (
                      <span className="text-[10px] text-gray-500">@ {log.error_step}</span>
                    )}
                  </div>
                  <p className="text-[11px] text-gray-600 break-words">{log.error_message}</p>
                  {log.created_at && (
                    <p className="text-[10px] text-gray-400 mt-1">
                      {new Date(log.created_at).toLocaleString('ja-JP')}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Live timer component for current step elapsed time
function LiveStepTimer({ startMs }) {
  const [elapsed, setElapsed] = useState(Date.now() - startMs);
  useEffect(() => {
    const timer = setInterval(() => {
      setElapsed(Date.now() - startMs);
    }, 1000);
    return () => clearInterval(timer);
  }, [startMs]);
  if (elapsed < 1000) return null;
  return <span className="ml-2 text-[11px] text-gray-400 font-normal">{formatDuration(elapsed)}</span>;
}

const areProcessingStepsPropsEqual = (prevProps, nextProps) =>
  prevProps.videoId === nextProps.videoId &&
  prevProps.initialStatus === nextProps.initialStatus &&
  prevProps.videoTitle === nextProps.videoTitle &&
  prevProps.externalProgress === nextProps.externalProgress &&
  prevProps.onProcessingComplete === nextProps.onProcessingComplete &&
  prevProps.uploadDurationMs === nextProps.uploadDurationMs &&
  prevProps.uploadStartTime === nextProps.uploadStartTime &&
  prevProps.videoDurationSec === nextProps.videoDurationSec;

export default memo(ProcessingSteps, areProcessingStepsPropsEqual);
