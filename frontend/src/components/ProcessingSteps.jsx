import { memo, useState, useEffect, useRef, useMemo, useCallback } from 'react';
import VideoService from '../base/services/videoService';

const normalizeProcessingStatus = (status) => {
  if (status === 'uploaded') {
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

    if (prevStatus && prevStatus !== nextStatus && prevStatusStartMsRef.current) {
      // Record duration of the previous phase
      const phaseDuration = now - prevStatusStartMsRef.current;
      phaseDurationsRef.current = {
        ...phaseDurationsRef.current,
        [prevStatus]: (phaseDurationsRef.current[prevStatus] || 0) + phaseDuration,
      };
      setPhaseDurations({ ...phaseDurationsRef.current });
    }

    if (prevStatus !== nextStatus) {
      prevStatusRef.current = nextStatus;
      prevStatusStartMsRef.current = now;
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
    { key: 'STEP_COMPRESS_1080P', label: window.__t('statusCompress') || '動画圧縮中...' },
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
  // Format video duration (seconds) to HH:MM:SS or MM:SS
  const formatVideoDuration = (sec) => {
    if (!sec || !isFinite(sec)) return null;
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${m}:${String(s).padStart(2, '0')}`;
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
        </>
      )}

      {/* Error message */}
      {isError && (
        <p className="text-sm text-red-400 mt-2">
          {window.__t('errorAnalysisMessage') || '解析中にエラーが発生しました。'}
        </p>
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
