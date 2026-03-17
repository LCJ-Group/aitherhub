import { Header, Body, Footer } from "./main";
import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import UploadService from "../base/services/uploadService";
import VideoService from "../base/services/videoService";
import { formatUploadError, logUploadError } from "../base/services/uploadErrors";
import { toast } from "../hooks/use-toast";
import LoginModal from "./modals/LoginModal";
import ProcessingSteps from "./ProcessingSteps";
import VideoDetail from "./VideoDetail";
import FeedbackPage from "./FeedbackPage";
import CsvValidationGate from "./CsvValidationGate";
import { validateCsvDateTime } from "../base/utils/csvDateTimeValidator";
// LiveDashboard is now at /live/:sessionId route (LivePage.jsx)

/**
 * Format file size in human-readable format
 */
function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const k = 1024;
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + units[i];
}

/**
 * Get video duration from a File object using HTML5 Video API
 * Returns a Promise<number> (seconds)
 */
function getVideoDuration(file) {
  return new Promise((resolve) => {
    try {
      const video = document.createElement('video');
      video.preload = 'metadata';
      const url = URL.createObjectURL(file);
      video.src = url;
      video.onloadedmetadata = () => {
        const dur = video.duration;
        URL.revokeObjectURL(url);
        resolve(isFinite(dur) ? dur : null);
      };
      video.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(null);
      };
      // Timeout fallback (30s for large files like 11h recordings)
      setTimeout(() => resolve(null), 30000);
    } catch {
      resolve(null);
    }
  });
}

/**
 * Format relative time
 */
function formatRelativeTime(date) {
  if (!date) return '';
  const now = new Date();
  const diffMs = now - date;
  const diffMin = Math.floor(diffMs / 60000);
  const diffHour = Math.floor(diffMs / 3600000);
  const diffDay = Math.floor(diffMs / 86400000);
  if (diffMin < 1) return 'たった今';
  if (diffMin < 60) return `${diffMin}分前`;
  if (diffHour < 24) return `${diffHour}時間前`;
  if (diffDay < 7) return `${diffDay}日前`;
  return date.toLocaleDateString('ja-JP');
}

/**
 * ErrorLogPanel - Collapsible error log viewer for video processing errors
 */
function ErrorLogPanel({ videoId }) {
  const [errorLogs, setErrorLogs] = useState([]);
  const [showLogs, setShowLogs] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);

  const fetchLogs = useCallback(async () => {
    if (!videoId || loading) return;
    setLoading(true);
    try {
      const res = await VideoService.getErrorLogs(videoId);
      if (res?.error_logs) setErrorLogs(res.error_logs);
      setFetched(true);
    } catch (err) {
      console.error('Failed to fetch error logs:', err);
    } finally {
      setLoading(false);
    }
  }, [videoId, loading]);

  // Auto-fetch on first render
  useEffect(() => {
    if (videoId && !fetched) fetchLogs();
  }, [videoId]);

  return (
    <div className="mt-4 w-full">
      <button
        onClick={() => {
          setShowLogs(!showLogs);
          if (!showLogs && !fetched) fetchLogs();
        }}
        className="flex items-center gap-2 text-xs text-gray-400 hover:text-gray-300 transition-colors mx-auto"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
        </svg>
        エラーログ {errorLogs.length > 0 ? `(${errorLogs.length}件)` : ''}
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className={`transition-transform duration-200 ${showLogs ? 'rotate-180' : ''}`}>
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </button>

      {showLogs && (
        <div className="mt-2 max-h-60 overflow-y-auto space-y-2">
          {loading && (
            <p className="text-xs text-gray-400 text-center py-2">読み込み中...</p>
          )}
          {!loading && errorLogs.length === 0 && (
            <p className="text-xs text-gray-500 text-center py-2">エラーログはありません</p>
          )}
          {errorLogs.map((log, idx) => (
            <div key={log.id || idx} className="p-2.5 bg-gray-800/50 border border-gray-700 rounded-md text-left">
              <div className="flex items-center gap-2 mb-1">
                <span className="inline-block px-1.5 py-0.5 text-[10px] font-mono font-semibold bg-red-900/50 text-red-400 rounded">
                  {log.error_code || 'UNKNOWN'}
                </span>
                {log.error_step && (
                  <span className="text-[10px] text-gray-500">@ {log.error_step}</span>
                )}
                {log.source && (
                  <span className="text-[10px] text-gray-600">({log.source})</span>
                )}
              </div>
              <p className="text-[11px] text-gray-400 break-words">{log.error_message}</p>
              {log.created_at && (
                <p className="text-[10px] text-gray-600 mt-1">
                  {new Date(log.created_at).toLocaleString('ja-JP')}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {showLogs && (
        <div className="text-center mt-2">
          <button
            onClick={fetchLogs}
            disabled={loading}
            className="text-[11px] text-gray-500 hover:text-gray-300 underline transition-colors"
          >
            {loading ? '読み込み中...' : 'エラーログを更新'}
          </button>
        </div>
      )}
    </div>
  );
}

export default function MainContent({
  children,
  onOpenSidebar,
  user,
  setUser,
  onUploadSuccess,
  selectedVideoId,
  showFeedback,
  onCloseFeedback,
  editorParams,
}) {
  const navigate = useNavigate();
  const postLoginRedirectKey = "postLoginRedirect";
  const isLoggedIn = Boolean(
    user &&
    (user.token ||
      user.accessToken ||
      user.id ||
      user.email ||
      user.username ||
      user.isAuthenticated ||
      user.isLoggedIn)
  );
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadStartTime, setUploadStartTime] = useState(null);
  const [uploadDurationMs, setUploadDurationMs] = useState(null);
  const [videoDurationSec, setVideoDurationSec] = useState(null);
  const [processingResume, setProcessingResume] = useState(false);
  const [checkingResume, setCheckingResume] = useState(false);
  const [progress, setProgress] = useState(0);
  const [uploadedVideoId, setUploadedVideoId] = useState(null);
  const [videoData, setVideoData] = useState(null);
  const [loadingVideo, setLoadingVideo] = useState(false);
  const [videoLoadError, setVideoLoadError] = useState(null); // null | 'timeout' | 'error' | 'auth'
  const videoLoadTimeoutRef = useRef(null);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState("");
  const [showLoginModal, setShowLoginModal] = useState(false);
  const [resumeUploadId, setResumeUploadId] = useState(null);
  const [resumeInfo, setResumeInfo] = useState(null); // {fileName, fileSize, progress, createdAt, hasFileHandle}
  // Clean video upload states
  const [uploadMode, setUploadMode] = useState(null); // null | 'screen_recording' | 'clean_video'
  const [cleanVideoFile, setCleanVideoFile] = useState(null);
  const [cleanVideoFiles, setCleanVideoFiles] = useState([]); // multiple video files for batch upload
  const [productExcelFile, setProductExcelFile] = useState(null);
  const [trendExcelFile, setTrendExcelFile] = useState(null);
  const prevIsLoggedInRef = useRef(isLoggedIn);
  const resumeFileInputRef = useRef(null);
  const videoRequestIdRef = useRef(0);
  const lastRequestedVideoIdRef = useRef(null);
  const videoAbortControllerRef = useRef(null);
  const activeResumeUploadStorageKeyRef = useRef(null);
  const prevSelectedVideoIdRef = useRef(selectedVideoId);
  const processingVideoTitleRef = useRef("");
  const [duplicateVideo, setDuplicateVideo] = useState(null); // { id, original_filename } of existing video
  // Live capture states
  const [liveUrl, setLiveUrl] = useState('');
  const [liveChecking, setLiveChecking] = useState(false);
  const [liveInfo, setLiveInfo] = useState(null); // { is_live, username, title }
  const [liveCapturing, setLiveCapturing] = useState(false);
  const [showLiveDashboard, setShowLiveDashboard] = useState(false);
  const [liveDashboardData, setLiveDashboardData] = useState(null);
  // CSV Validation Gate states
  const [csvValidating, setCsvValidating] = useState(false);
  const [csvValidationResult, setCsvValidationResult] = useState(null);
  const [showCsvValidationGate, setShowCsvValidationGate] = useState(false);

  useEffect(() => {
    console.log("[MainContent] user", user);
    console.log("[MainContent] isLoggedIn", isLoggedIn);
  }, [user, isLoggedIn]);

  useEffect(() => {
    if (selectedVideoId && !isLoggedIn) {
      sessionStorage.setItem(postLoginRedirectKey, `/video/${selectedVideoId}`);
      setShowLoginModal(true);
    }
  }, [selectedVideoId, isLoggedIn]);

  const normalizeVideoData = (data, fallbackVideoId) => {
    const r1 = Array.isArray(data.reports_1) ? data.reports_1 : (data.reports_1 ? [data.reports_1] : []);
    let r2 = Array.isArray(data.reports_2) ? data.reports_2 : (data.reports_2 ? [data.reports_2] : []);
    if ((!r2 || r2.length === 0) && r1 && r1.length > 0) {
      r2 = r1.map((it) => ({
        phase_index: it.phase_index,
        time_start: it.time_start,
        time_end: it.time_end,
        insight: it.insight ?? it.phase_description ?? "",
        video_clip_url: it.video_clip_url,
      }));
    }

    return {
      id: data.id || fallbackVideoId,
      original_filename: data.original_filename,
      status: data.status,
      created_at: data.created_at,
      upload_type: data.upload_type,
      excel_product_blob_url: data.excel_product_blob_url,
      excel_trend_blob_url: data.excel_trend_blob_url,
      compressed_blob_url: data.compressed_blob_url,
      preview_url: data.preview_url,
      reports_1: r1,
      reports_2: r2,
      report3: Array.isArray(data.report3) ? data.report3 : (data.report3 ? [data.report3] : []),
    };
  };

  const buildResumeUploadStorageKey = (userId, uploadId) => {
    if (!userId || !uploadId) return null;
    return `resumeUpload:${userId}:${uploadId}`;
  };

  const clearActiveResumeUploadStorageKey = () => {
    const key = activeResumeUploadStorageKeyRef.current;
    if (key) {
      localStorage.removeItem(key);
      activeResumeUploadStorageKeyRef.current = null;
    }
  };

  // If token expires and user logs in again via modal, clear old upload result message.
  useEffect(() => {
    const prev = prevIsLoggedInRef.current;
    // Logged in -> logged out: clear uploader state so stale file/message doesn't remain.
    if (prev && !isLoggedIn) {
      setSelectedFile(null);
      setUploading(false);
      setProgress(0);
      setUploadedVideoId(null);
      setVideoData(null);
      setMessage("");
      setMessageType("");
      setUploadMode(null);
      setCleanVideoFile(null);
      setCleanVideoFiles([]);
      setProductExcelFile(null);
      setTrendExcelFile(null);
      setDuplicateVideo(null);
    }
    if (!prev && isLoggedIn) {
      setMessage("");
      setMessageType("");
    }
    prevIsLoggedInRef.current = isLoggedIn;
  }, [isLoggedIn]);

  const checkForResumableUpload = async () => {
    if (!user?.id) return;
    setCheckingResume(true);
    try {
      const result = await UploadService.checkUploadResume(user.id);
      if (result?.upload_resume && result?.upload_id) {
        setResumeUploadId(result.upload_id);
        // Fetch detailed resume info from IndexedDB
        try {
          const info = await UploadService.getResumeInfo(result.upload_id);
          setResumeInfo(info);
        } catch (e) {
          console.warn('Failed to get resume info from IndexedDB:', e);
          setResumeInfo(null);
        }
      } else {
        setResumeUploadId(null);
        setResumeInfo(null);
      }
    } catch (error) {
      console.error("Failed to check upload resume:", error);
      setResumeUploadId(null);
      setResumeInfo(null);
    } finally {
      setCheckingResume(false);
    }
  };

  useEffect(() => {
    if (isLoggedIn && user?.id) {
      checkForResumableUpload();
    } else {
      setResumeUploadId(null);
    }
  }, [isLoggedIn, user?.id]);

  const checkDuplicateVideo = async (filename) => {
    try {
      const userId = user?.id || user?.email;
      if (!userId) return null;
      const videoList = await VideoService.getVideosByUser(userId);
      if (!Array.isArray(videoList)) return null;
      const match = videoList.find(v => v.original_filename === filename);
      return match || null;
    } catch (e) {
      console.warn('Duplicate check failed:', e);
      return null;
    }
  };

  const handleFileSelect = async (e) => {
    if (!isLoggedIn) {
      setShowLoginModal(true);
      return;
    }

    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.type.startsWith("video/")) {
      setMessageType("error");
      setMessage(window.__t('selectValidVideoError'));
      return;
    }

    // Check for duplicate
    const existing = await checkDuplicateVideo(file.name);
    if (existing) {
      setDuplicateVideo(existing);
      setSelectedFile(file);
      return;
    }

    setDuplicateVideo(null);
    setSelectedFile(file);
    setResumeUploadId(null);
    setUploadedVideoId(null);
    setVideoData(null);
    setMessage("");
    setProgress(0);
    // Get video duration
    getVideoDuration(file).then(dur => { if (dur) setVideoDurationSec(dur); });
  };

  // Clean video file handlers (single file - legacy)
  const handleCleanVideoFileSelect = async (e) => {
    const file = e.target.files?.[0];
    if (file && file.type.startsWith("video/")) {
      // Check for duplicate
      const existing = await checkDuplicateVideo(file.name);
      if (existing) {
        setDuplicateVideo(existing);
        setCleanVideoFile(file);
        return;
      }
      setDuplicateVideo(null);
      setCleanVideoFile(file);
      // Get video duration
      getVideoDuration(file).then(dur => { if (dur) setVideoDurationSec(dur); });
    }
  };

  // Multiple clean video files handler (batch upload)
  const handleCleanVideoFilesSelect = (e) => {
    const files = Array.from(e.target.files || []);
    const videoFiles = files.filter(f => f.type.startsWith("video/"));
    if (videoFiles.length > 0) {
      // Sort by filename to maintain order (part1, part2, etc.)
      videoFiles.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
      setCleanVideoFiles(videoFiles);
      setCleanVideoFile(videoFiles[0]); // set first for compatibility
      setDuplicateVideo(null);
      // Get duration of first video
      getVideoDuration(videoFiles[0]).then(dur => { if (dur) setVideoDurationSec(dur); });
    }
  };

  const handleRemoveCleanVideoFile = (index) => {
    setCleanVideoFiles(prev => {
      const updated = prev.filter((_, i) => i !== index);
      setCleanVideoFile(updated[0] || null);
      return updated;
    });
  };

  const handleProductExcelSelect = (e) => {
    const file = e.target.files?.[0];
    if (file) setProductExcelFile(file);
  };

  const handleTrendExcelSelect = (e) => {
    const file = e.target.files?.[0];
    if (file) setTrendExcelFile(file);
  };

  // CSV Validation Gate: バリデーション実行
  const handleCleanVideoUpload = async () => {
    const filesToUpload = cleanVideoFiles.length > 0 ? cleanVideoFiles : (cleanVideoFile ? [cleanVideoFile] : []);
    if (!isLoggedIn || filesToUpload.length === 0 || !productExcelFile || !trendExcelFile || uploading) return;

    // CSV日時バリデーションを実行
    setCsvValidating(true);
    setShowCsvValidationGate(true);
    try {
      const result = await validateCsvDateTime(filesToUpload, productExcelFile, trendExcelFile);
      setCsvValidationResult(result);
      setCsvValidating(false);

      // OK判定の場合は自動的にアップロード開始
      if (result.verdict === 'ok') {
        setShowCsvValidationGate(false);
        setCsvValidationResult(null);
        await executeCleanVideoUpload(filesToUpload);
      }
      // warning/error/unknown の場合はモーダルで確認を待つ
    } catch (err) {
      console.error('[CsvValidationGate] Validation failed:', err);
      setCsvValidating(false);
      setShowCsvValidationGate(false);
      // バリデーション自体が失敗した場合はそのままアップロード
      await executeCleanVideoUpload(filesToUpload);
    }
  };

  // CSV Validation Gate: 確認後のアップロード実行
  const executeCleanVideoUpload = async (filesToUpload) => {
    if (!filesToUpload) {
      filesToUpload = cleanVideoFiles.length > 0 ? cleanVideoFiles : (cleanVideoFile ? [cleanVideoFile] : []);
    }
    if (filesToUpload.length === 0) return;

    setUploading(true);
    setUploadStartTime(Date.now());
    setUploadDurationMs(null);
    setMessage("");
    setProgress(0);

    try {
      if (filesToUpload.length === 1) {
        // Single video: use existing flow
        const video_id = await UploadService.uploadCleanVideo(
          filesToUpload[0],
          productExcelFile,
          trendExcelFile,
          user.email,
          (percentage) => {
            setProgress(percentage);
          },
          ({ uploadId }) => {
            const storageKey = buildResumeUploadStorageKey(user?.id, uploadId);
            if (storageKey) {
              activeResumeUploadStorageKeyRef.current = storageKey;
              localStorage.setItem(storageKey, "active");
            }
          },
        );
        setMessageType("success");
        setUploadDurationMs(Date.now() - (uploadStartTime || Date.now()));
        setCleanVideoFile(null);
        setCleanVideoFiles([]);
        setProductExcelFile(null);
        setTrendExcelFile(null);
        setUploadMode(null);
        setResumeUploadId(null);
        setUploadedVideoId(video_id);
        if (onUploadSuccess) {
          onUploadSuccess(video_id);
        }
      } else {
        // Multiple videos: use batch upload with auto time offsets
        // Auto-calculate time offsets: we don't know durations upfront,
        // so we set offset=0 for all and let the user optionally adjust.
        // For now, offset is 0 for all (user can set manually if needed).
        const videoItems = filesToUpload.map((file, idx) => ({
          file,
          timeOffsetSeconds: 0, // Will be enhanced later with duration detection
        }));

        const videoIds = await UploadService.batchUploadCleanVideos(
          videoItems,
          productExcelFile,
          trendExcelFile,
          user.email,
          (percentage) => {
            setProgress(percentage);
          },
          ({ uploadId }) => {
            const storageKey = buildResumeUploadStorageKey(user?.id, uploadId);
            if (storageKey) {
              activeResumeUploadStorageKeyRef.current = storageKey;
              localStorage.setItem(storageKey, "active");
            }
          },
        );
        setMessageType("success");
        setUploadDurationMs(Date.now() - (uploadStartTime || Date.now()));
        setCleanVideoFile(null);
        setCleanVideoFiles([]);
        setProductExcelFile(null);
        setTrendExcelFile(null);
        setUploadMode(null);
        setResumeUploadId(null);
        // Navigate to first video
        if (videoIds.length > 0) {
          setUploadedVideoId(videoIds[0]);
          if (onUploadSuccess) {
            onUploadSuccess(videoIds[0]);
          }
        }
      }
    } catch (error) {
      logUploadError('handleCleanVideoUpload', error);
      toast.error(formatUploadError(error));
    } finally {
      clearActiveResumeUploadStorageKey();
      setUploading(false);
    }
  };

  // CSV Validation Gate: コールバックハンドラー
  const handleCsvValidationContinue = async () => {
    setShowCsvValidationGate(false);
    const result = csvValidationResult;
    setCsvValidationResult(null);
    // 判定結果とユーザーの選択をログ保存
    logCsvValidationDecision(result, 'continue');
    await executeCleanVideoUpload();
  };

  const handleCsvValidationReplace = () => {
    const result = csvValidationResult;
    setShowCsvValidationGate(false);
    setCsvValidationResult(null);
    setProductExcelFile(null);
    setTrendExcelFile(null);
    logCsvValidationDecision(result, 'replace');
    toast.info('CSVファイルを再選択してください');
  };

  const handleCsvValidationForce = async () => {
    setShowCsvValidationGate(false);
    const result = csvValidationResult;
    setCsvValidationResult(null);
    logCsvValidationDecision(result, 'force');
    await executeCleanVideoUpload();
  };

  const logCsvValidationDecision = (result, decision) => {
    try {
      const payload = {
        verdict: result?.verdict,
        decision,
        checks: result?.checks?.map(c => ({ id: c.id, result: c.result, detail: c.detail })),
        video_filename: result?.extracted?.video?.filename,
        trend_filename: result?.extracted?.trend?.filename,
        product_filename: result?.extracted?.product?.filename,
        timestamp: new Date().toISOString(),
        user_email: user?.email,
      };
      console.log('[CsvValidationGate] Decision:', payload);
      // バックエンドに非同期送信
      const apiBase = import.meta.env.VITE_API_BASE_URL || '';
      fetch(`${apiBase}/api/v1/admin/csv-validation-log`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).catch(() => {}); // fire-and-forget
    } catch (e) {
      console.warn('[CsvValidationGate] Failed to log decision:', e);
    }
  };

  const handleCancelCleanVideo = () => {
    setCleanVideoFile(null);
    setCleanVideoFiles([]);
    setProductExcelFile(null);
    setTrendExcelFile(null);
    setDuplicateVideo(null);
    setUploadMode(null);
    setUploading(false);
    setProgress(0);
    setMessage("");
  };

  const handleResumeUpload = async () => {
    if (!resumeUploadId || processingResume) return;

    // Check if we have a cached file handle (same session)
    const cachedFile = UploadService.getCachedFileHandle(resumeUploadId);
    if (cachedFile) {
      // Resume directly without file picker
      await executeResume(cachedFile);
      return;
    }

    // No cached file - need user to re-select
    setProcessingResume(true);
    try {
      resumeFileInputRef.current?.click();
    } finally {
      setTimeout(() => setProcessingResume(false), 300);
    }
  };

  const handleResumeWithNewFile = async () => {
    if (!resumeUploadId || !user?.id || processingResume) return;
    setProcessingResume(true);
    try {
      // Clear existing upload record
      await UploadService.clearUserUploads(user.id);
      await UploadService.clearUploadMetadata(resumeUploadId);
      // Reset state to go back to normal upload mode
      setResumeUploadId(null);
      setResumeInfo(null);
      toast.info('前回のアップロードを削除しました。新しい動画を選択してください。');
    } catch (error) {
      console.error('Failed to clear resume for new file:', error);
      setResumeUploadId(null);
      setResumeInfo(null);
    } finally {
      setProcessingResume(false);
    }
  };

  const handleSkipResume = async () => {
    if (!resumeUploadId || !user?.id || processingResume) return;
    setProcessingResume(true);
    try {
      // Clear upload record from backend
      await UploadService.clearUserUploads(user.id);

      // Clear upload metadata from IndexedDB
      await UploadService.clearUploadMetadata(resumeUploadId);

      // Clear UI state
      setResumeUploadId(null);

      toast.info(window.__t('uploadResumeCleared') || 'Upload resume cleared');
    } catch (error) {
      console.error('Failed to clear resume:', error);
      // Still clear UI state even if backend call fails
      setResumeUploadId(null);
    } finally {
      setProcessingResume(false);
    }
  };

  /**
   * Core resume execution - shared by both cached file handle and file picker resume
   */
  const executeResume = async (file) => {
    if (!file || !resumeUploadId || uploading) return;

    setProcessingResume(true);
    setUploading(true);
    setUploadStartTime(Date.now());
    setUploadDurationMs(null);
    setMessage("");
    setProgress(0);

    try {
      const storageKey = buildResumeUploadStorageKey(user?.id, resumeUploadId);
      if (storageKey) {
        activeResumeUploadStorageKeyRef.current = storageKey;
        localStorage.setItem(storageKey, "active");
      }
      // Get metadata from IndexedDB
      const metadata = await UploadService.getUploadMetadata(resumeUploadId);
      if (!metadata) {
        throw new Error('アップロード情報が見つかりません。新しいアップロードを開始してください。');
      }

      // Validate that the selected file is the same as the original file
      if (file.name !== metadata.fileName || file.size !== metadata.fileSize) {
        throw new Error(`選択したファイルが元のファイルと一致しません。\nファイル名: ${metadata.fileName}\nサイズ: ${formatFileSize(metadata.fileSize)}`);
      }

      const uploadedBlockIds = metadata.uploadedBlocks || [];
      const maxUploadedIndex = uploadedBlockIds.length > 0
        ? Math.max(...uploadedBlockIds.map(id => {
          const decoded = atob(id);
          return parseInt(decoded, 10);
        }))
        : -1;
      const startFrom = maxUploadedIndex + 1;

      // Resume upload from where it left off
      await UploadService.uploadToAzure(
        file,
        metadata.uploadUrl,
        resumeUploadId,
        (percentage) => {
          setProgress(percentage);
        },
        startFrom
      );

      const video_id = metadata.videoId;
      if (!video_id) {
        throw new Error('Video ID not found in metadata. Please start a new upload.');
      }

      // Use correct completion method based on upload mode
      const uploadMode = metadata.uploadMode || 'screen_recording';
      if (uploadMode === 'clean_video') {
        // Clean video: include upload_type and Excel URLs from metadata
        await UploadService.uploadCompleteWithType(
          user.email,
          video_id,
          file.name,
          resumeUploadId,
          'clean_video',
          metadata.excelProductBlobUrl || null,
          metadata.excelTrendBlobUrl || null
        );
      } else {
        // Screen recording: simple completion
        await UploadService.uploadComplete(
          user.email,
          video_id,
          file.name,
          resumeUploadId
        );
      }

      await UploadService.clearUploadMetadata(resumeUploadId);

      setMessageType("success");
      setUploadDurationMs(Date.now() - (uploadStartTime || Date.now()));
      setSelectedFile(null);
      setResumeUploadId(null);
      setResumeInfo(null);
      setUploadedVideoId(video_id);

      if (onUploadSuccess) {
        onUploadSuccess(video_id);
      }
    } catch (error) {
      logUploadError('executeResume', error);
      toast.error(formatUploadError(error));
    } finally {
      clearActiveResumeUploadStorageKey();
      setUploading(false);
      setProcessingResume(false);
      if (resumeFileInputRef.current) {
        resumeFileInputRef.current.value = '';
      }
    }
  };

  const handleResumeFileSelect = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    // Cache the newly selected file for potential future resume
    UploadService.cacheFileHandle(resumeUploadId, file);
    await executeResume(file);
  };

  const handleUpload = async () => {
    if (!isLoggedIn) {
      setShowLoginModal(true);
      return;
    }

    if (!selectedFile) {
      toast.error(window.__t('selectFileFirstError'));
      return;
    }

    if (uploading) return;

    setUploading(true);
    setUploadStartTime(Date.now());
    setUploadDurationMs(null);
    setMessage("");
    setProgress(0);

    try {
      const video_id = await UploadService.uploadFile(
        selectedFile,
        user.email,
        (percentage) => {
          setProgress(percentage);
        },
        ({ uploadId }) => {
          const storageKey = buildResumeUploadStorageKey(user?.id, uploadId);
          if (storageKey) {
            activeResumeUploadStorageKeyRef.current = storageKey;
            localStorage.setItem(storageKey, "active");
          }
        },
      );
      setMessageType("success");

      // Record upload duration
      const duration = Date.now() - (uploadStartTime || Date.now());
      setUploadDurationMs(duration);
      console.log(`[Upload] Upload completed: video_id=${video_id}, file=${selectedFile?.name}, size=${((selectedFile?.size || 0) / (1024*1024)).toFixed(1)}MB, duration=${(duration/1000).toFixed(1)}s`);

      setSelectedFile(null);
      setResumeUploadId(null);
      // Set uploaded video ID to start processing tracking
      setUploadedVideoId(video_id);

      // Trigger refresh sidebar
      if (onUploadSuccess) {
        onUploadSuccess(video_id);
      }
    } catch (error) {
      logUploadError('handleUpload', error);
      toast.error(formatUploadError(error));
    } finally {
      clearActiveResumeUploadStorageKey();
      setUploading(false);
    }
  };

  const handleCancel = () => {
    setSelectedFile(null);
    setDuplicateVideo(null);
    setUploading(false);
    setProgress(0);
    setUploadedVideoId(null);
    setVideoData(null);
    setMessage("");
  };

  // =========================================================
  // Live Capture Handlers
  // =========================================================
  const handleLiveCheck = async () => {
    if (!liveUrl.trim()) {
      setMessage('URLを入力してください');
      setMessageType('error');
      return;
    }
    setLiveChecking(true);
    setLiveInfo(null);
    setMessage('');
    try {
      const result = await VideoService.checkLiveStatus(liveUrl.trim());
      setLiveInfo(result);
      if (!result.is_live) {
        setMessage(`@${result.username || 'unknown'} は現在ライブ配信していません`);
        setMessageType('error');
      }
    } catch (err) {
      const detail = err?.response?.data?.detail || err.message || 'ライブチェックに失敗しました';
      setMessage(detail);
      setMessageType('error');
    } finally {
      setLiveChecking(false);
    }
  };

  const handleLiveCapture = async () => {
    if (!liveUrl.trim()) return;
    setLiveCapturing(true);
    setMessage('');
    try {
      const result = await VideoService.startLiveCapture(liveUrl.trim());
      setMessage(`@${result.username} のライブ録画を開始しました`);
      setMessageType('success');
      setUploadedVideoId(result.video_id);
      // Navigate to LiveDashboard page
      navigate(`/live/${result.video_id}`);
    } catch (err) {
      const detail = err?.response?.data?.detail || err.message || 'ライブキャプチャの開始に失敗しました';
      setMessage(detail);
      setMessageType('error');
    } finally {
      setLiveCapturing(false);
    }
  };

  const handleCloseLiveDashboard = () => {
    setShowLiveDashboard(false);
    // Navigate to video detail page for post-processing
    if (liveDashboardData?.videoId) {
      if (onUploadSuccess) onUploadSuccess();
      navigate(`/video/${liveDashboardData.videoId}`);
    }
    setLiveDashboardData(null);
  };

  const handleCancelLive = () => {
    setUploadMode(null);
    setLiveUrl('');
    setLiveInfo(null);
    setLiveCapturing(false);
    setMessage('');
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = async (e) => {
    e.preventDefault();
    e.stopPropagation();

    if (!isLoggedIn) {
      setShowLoginModal(true);
      return;
    }

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      const file = files[0];
      if (!file.type.startsWith("video/")) {
        setMessageType("error");
        setMessage(window.__t('selectValidVideoError'));
        return;
      }
      // Check for duplicate
      const existing = await checkDuplicateVideo(file.name);
      if (existing) {
        setDuplicateVideo(existing);
        setSelectedFile(file);
        return;
      }
      setDuplicateVideo(null);
      setSelectedFile(file);
      setUploadedVideoId(null);
      setVideoData(null);
      setMessage("");
      setProgress(0);
    }
  };

  // When switching to a different history item, clear draft upload UI only
  // if no active upload/processing is running.
  useEffect(() => {
    if (selectedVideoId && !uploading && !uploadedVideoId) {
      console.log("[MainContent] Clearing uploadedVideoId due to selectedVideoId:", selectedVideoId);
      setUploadedVideoId(null);
      setSelectedFile(null);
      setProgress(0);
      setUploading(false);
    }
  }, [selectedVideoId, uploadedVideoId, uploading]);

  // When leaving a selected history video and returning to home, clear upload-tracking UI state.
  useEffect(() => {
    const prevSelectedVideoId = prevSelectedVideoIdRef.current;
    if (prevSelectedVideoId && !selectedVideoId && !uploading && !uploadedVideoId) {
      setUploadedVideoId(null);
      setSelectedFile(null);
      setProgress(0);
      setUploading(false);
    }
    prevSelectedVideoIdRef.current = selectedVideoId;
  }, [selectedVideoId, uploading, uploadedVideoId]);

  useEffect(() => {
    return () => {
      if (videoAbortControllerRef.current) {
        videoAbortControllerRef.current.abort();
        videoAbortControllerRef.current = null;
      }
    };
  }, []);

  // Fetch video details when uploadedVideoId OR selectedVideoId changes
  useEffect(() => {
    const videoId = selectedVideoId || uploadedVideoId;
    console.log("[MainContent] Fetching video details for:", videoId);

    if (!isLoggedIn) {
      lastRequestedVideoIdRef.current = null;
      setVideoData(null);
      setLoadingVideo(false);
      return;
    }

    if (videoAbortControllerRef.current) {
      videoAbortControllerRef.current.abort();
      videoAbortControllerRef.current = null;
    }

    if (!videoId) {
      videoRequestIdRef.current += 1;
      lastRequestedVideoIdRef.current = null;
      setVideoData(null);
      setLoadingVideo(false);
      return;
    }

    // Ignore duplicate fetch trigger for same effective video id.
    // This avoids remount flicker when selectedVideoId is auto-set
    // right after uploadedVideoId with the same value.
    if (lastRequestedVideoIdRef.current === videoId) {
      return;
    }
    lastRequestedVideoIdRef.current = videoId;

    const currentRequestId = ++videoRequestIdRef.current;
    const controller = new AbortController();
    videoAbortControllerRef.current = controller;

    setVideoData(null);
    setLoadingVideo(true);
    setVideoLoadError(null);

    // Safety timeout: if API doesn't respond within 20s, show error instead of spinner
    if (videoLoadTimeoutRef.current) clearTimeout(videoLoadTimeoutRef.current);
    videoLoadTimeoutRef.current = setTimeout(() => {
      if (currentRequestId === videoRequestIdRef.current) {
        console.warn('[MainContent] Video loading timed out after 20s');
        setLoadingVideo(false);
        setVideoLoadError('timeout');
      }
    }, 20000);

    const fetchVideoDetails = async () => {
      try {
        const response = await VideoService.getVideoById(videoId, { signal: controller.signal });
        if (currentRequestId !== videoRequestIdRef.current) return;
        const data = response || {};
        setVideoData(normalizeVideoData(data, videoId));
        setVideoLoadError(null);
      } catch (err) {
        if (controller.signal.aborted) return;
        if (currentRequestId !== videoRequestIdRef.current) return;
        if (err?.response?.status === 403) {
          navigate("/");
          setVideoData(null);
          return;
        }
        console.error('Failed to fetch video details:', err);
        setVideoData(null);
        const status = err?.response?.status;
        if (status === 401) {
          setVideoLoadError('auth');
        } else {
          setVideoLoadError('error');
        }
      } finally {
        if (currentRequestId === videoRequestIdRef.current) {
          if (videoLoadTimeoutRef.current) {
            clearTimeout(videoLoadTimeoutRef.current);
            videoLoadTimeoutRef.current = null;
          }
          setLoadingVideo(false);
          if (videoAbortControllerRef.current === controller) {
            videoAbortControllerRef.current = null;
          }
        }
      }
    };

    fetchVideoDetails();
    return () => {
      controller.abort();
      if (videoLoadTimeoutRef.current) {
        clearTimeout(videoLoadTimeoutRef.current);
        videoLoadTimeoutRef.current = null;
      }
    };
  }, [uploadedVideoId, selectedVideoId, isLoggedIn]);

  // ── Auto-detect active live session ──
  // NOTE: Auto-open LiveDashboard removed. LiveDashboard is now at /live/:sessionId route.

  // NOTE: Auto-detect extension session removed. Use /live/:sessionId route instead.

  // NOTE: Auto-detect on page load removed. Use /live/:sessionId route instead.

  // Handle processing complete - reload video data
  const handleProcessingComplete = useCallback(async () => {
    const videoId = selectedVideoId || uploadedVideoId;
    if (!videoId) return;
    lastRequestedVideoIdRef.current = videoId;

    if (videoAbortControllerRef.current) {
      videoAbortControllerRef.current.abort();
      videoAbortControllerRef.current = null;
    }

    const currentRequestId = ++videoRequestIdRef.current;
    const controller = new AbortController();
    videoAbortControllerRef.current = controller;

    setLoadingVideo(true);
    try {
      const response = await VideoService.getVideoById(videoId, { signal: controller.signal });
      if (currentRequestId !== videoRequestIdRef.current) return;
      const data = response || {};
      setVideoData(normalizeVideoData(data, videoId));
    } catch (err) {
      if (controller.signal.aborted) return;
      if (currentRequestId !== videoRequestIdRef.current) return;
      if (err?.response?.status === 403) {
        navigate("/");
        setVideoData(null);
        return;
      }
      console.error('Failed to reload video after processing:', err);
    } finally {
      if (currentRequestId === videoRequestIdRef.current) {
        setLoadingVideo(false);
        if (videoAbortControllerRef.current === controller) {
          videoAbortControllerRef.current = null;
        }
      }
    }
  }, [uploadedVideoId, selectedVideoId]);

  const shouldShowGlobalVideoLoading =
    loadingVideo &&
    Boolean(selectedVideoId) &&
    selectedVideoId !== uploadedVideoId;
  const activeProcessingVideoId = uploadedVideoId || selectedVideoId;
  const shouldRenderProcessing =
    !showFeedback &&
    !shouldShowGlobalVideoLoading &&
    (uploading || Boolean(activeProcessingVideoId)) &&
    (!videoData || (videoData.status !== 'DONE' && videoData.status !== 'ERROR'));
  const processingInitialStatus = useMemo(() => {
    if (uploading) return "UPLOADING";
    if (videoData?.status) return videoData.status;
    // Optimistic transition after upload complete:
    // keep upload step completed and immediately show first analysis step loading
    // while waiting for backend status stream/API response.
    if (activeProcessingVideoId) return "STEP_COMPRESS_1080P";
    return "NEW";
  }, [uploading, videoData?.status, activeProcessingVideoId]);
  const stableProcessingVideoTitle = useMemo(() => {
    const nextTitle = videoData?.original_filename || selectedFile?.name || cleanVideoFile?.name || "";
    if (nextTitle) {
      processingVideoTitleRef.current = nextTitle;
    }
    return processingVideoTitleRef.current;
  }, [videoData?.original_filename, selectedFile?.name, cleanVideoFile?.name]);

  useEffect(() => {
    if (!activeProcessingVideoId && !uploading) {
      processingVideoTitleRef.current = "";
    }
  }, [activeProcessingVideoId, uploading]);

  return (
    <div className="flex flex-col h-screen">
      {/* LiveDashboard is now at /live/:sessionId route - no overlay here */}
      <Header onOpenSidebar={onOpenSidebar} user={user} setUser={setUser} />

      <LoginModal
        open={showLoginModal}
        onOpenChange={(nextOpen) => {
          setShowLoginModal(nextOpen);
          if (!nextOpen) {
            try {
              const storedUser = localStorage.getItem("user");
              if (storedUser && setUser) {
                const parsedUser = JSON.parse(storedUser);
                setUser(parsedUser);
                if (parsedUser?.isLoggedIn) {
                  const redirectTo = sessionStorage.getItem(postLoginRedirectKey);
                  if (redirectTo) {
                    sessionStorage.removeItem(postLoginRedirectKey);
                    navigate(redirectTo);
                  }
                }
              }
            } catch {
              // ignore JSON/localStorage errors
            }
          }
        }}
        onSwitchToRegister={() => setShowLoginModal(false)}
      />

      {/* CSV Date/Time Validation Gate */}
      {showCsvValidationGate && (
        <CsvValidationGate
          validationResult={csvValidationResult}
          isValidating={csvValidating}
          onContinue={handleCsvValidationContinue}
          onReplace={handleCsvValidationReplace}
          onForce={handleCsvValidationForce}
          onClose={() => {
            setShowCsvValidationGate(false);
            setCsvValidationResult(null);
            setCsvValidating(false);
          }}
        />
      )}

      <Body>
        {showFeedback ? (
          <FeedbackPage onBack={onCloseFeedback} />
        ) : videoLoadError ? (
          <div className="w-full flex flex-col items-center justify-center">
            <div className="rounded-2xl p-8 border transition-all duration-200 border-red-100 bg-red-50">
              <div className="flex flex-col items-center text-center space-y-4">
                <div className="w-12 h-12 rounded-full bg-red-100 flex items-center justify-center">
                  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-red-400">
                    <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
                  </svg>
                </div>
                <p className="text-red-600 text-sm font-medium">
                  {videoLoadError === 'timeout' ? '読み込みがタイムアウトしました' : videoLoadError === 'auth' ? 'ログイン状態を確認してください' : '動画の読み込みに失敗しました'}
                </p>
                <button
                  onClick={() => {
                    setVideoLoadError(null);
                    lastRequestedVideoIdRef.current = null;
                    const videoId = selectedVideoId || uploadedVideoId;
                    if (videoId) {
                      // Force re-fetch by resetting the ref and triggering effect
                      setLoadingVideo(true);
                      setVideoLoadError(null);
                      const reqId = ++videoRequestIdRef.current;
                      const ctrl = new AbortController();
                      videoAbortControllerRef.current = ctrl;
                      lastRequestedVideoIdRef.current = videoId;
                      if (videoLoadTimeoutRef.current) clearTimeout(videoLoadTimeoutRef.current);
                      videoLoadTimeoutRef.current = setTimeout(() => {
                        if (reqId === videoRequestIdRef.current) {
                          setLoadingVideo(false);
                          setVideoLoadError('timeout');
                        }
                      }, 20000);
                      VideoService.getVideoById(videoId, { signal: ctrl.signal }).then((response) => {
                        if (reqId !== videoRequestIdRef.current) return;
                        setVideoData(normalizeVideoData(response || {}, videoId));
                        setVideoLoadError(null);
                      }).catch((err) => {
                        if (ctrl.signal.aborted || reqId !== videoRequestIdRef.current) return;
                        setVideoData(null);
                        setVideoLoadError(err?.response?.status === 401 ? 'auth' : 'error');
                      }).finally(() => {
                        if (reqId === videoRequestIdRef.current) {
                          if (videoLoadTimeoutRef.current) { clearTimeout(videoLoadTimeoutRef.current); videoLoadTimeoutRef.current = null; }
                          setLoadingVideo(false);
                          if (videoAbortControllerRef.current === ctrl) videoAbortControllerRef.current = null;
                        }
                      });
                    }
                  }}
                  className="px-4 py-2 text-sm font-medium text-white bg-red-500 hover:bg-red-600 rounded-lg transition-colors"
                >
                  再試行
                </button>
              </div>
            </div>
          </div>
        ) : shouldShowGlobalVideoLoading ? (
          <div className="w-full flex flex-col items-center justify-center">
            <div className="rounded-2xl p-8 border transition-all duration-200 border-gray-200 bg-gray-50">
              <div className="flex flex-col items-center text-center space-y-4">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-gray-600"></div>
                <p className="text-gray-700 text-sm">読み込み中...</p>
              </div>
            </div>
          </div>
        ) : shouldRenderProcessing ? (
          <div className="w-full flex flex-col items-center justify-center">
            <div className="w-full">
              <h4 className="w-full text-center">
                {window.__t('header').split('\n').map((line, idx, arr) => (
                  <span key={idx} className="text-gray-500 italic text-lg">
                    {line}
                    {idx < arr.length - 1 && <br className="block md:hidden" />}
                  </span>
                ))}
              </h4>
            </div>
            <div className="w-full mt-[20px] [@media(max-height:650px)]:mt-[20px]">
              <h4 className="w-full mb-[22px] text-center">
                {window.__t('uploadText').split('\n').map((line, idx, arr) => (
                  <span key={idx} className="text-gray-900 text-2xl !font-bold font-cabin">
                    {line}
                    {idx < arr.length - 1 && <br className="block md:hidden" />}
                  </span>
                ))}
              </h4>
              <div className="w-full max-w-xl mx-auto">
                <div
                  className="rounded-2xl p-8 border transition-all duration-200 border-gray-200 bg-gray-50 hover:border-gray-300 hover:bg-gray-100"
                  onDragOver={handleDragOver}
                  onDrop={handleDrop}
                >
                  <div className="flex flex-col items-center text-center space-y-6">
                    <ProcessingSteps
                      videoId={activeProcessingVideoId}
                      initialStatus={processingInitialStatus}
                      videoTitle={stableProcessingVideoTitle}
                      externalProgress={uploading ? progress : undefined}
                      onProcessingComplete={handleProcessingComplete}
                      uploadDurationMs={uploadDurationMs}
                      uploadStartTime={uploading ? uploadStartTime : null}
                      videoDurationSec={videoDurationSec}
                    />
                  </div>
                  {/* Allow uploading another video or starting live analysis while current one is processing */}
                  {!uploading && activeProcessingVideoId && (
                    <div className="mt-6 pt-4 border-t border-gray-200 flex flex-col sm:flex-row items-center justify-center gap-3">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          e.preventDefault();
                          setUploadedVideoId(null);
                          setVideoData(null);
                          setSelectedFile(null);
                          setCleanVideoFile(null);
                          setCleanVideoFiles([]);
                          setProductExcelFile(null);
                          setTrendExcelFile(null);
                          setUploadMode(null);
                          setProgress(0);
                          setMessage("");
                          setDuplicateVideo(null);
                          navigate('/');
                        }}
                        className="px-6 py-3 text-sm text-[#7D01FF] border-2 border-[#7D01FF] rounded-lg hover:bg-purple-50 transition-colors cursor-pointer bg-white shadow-sm"
                      >
                        + {window.__t('newUploadButton') || '新しい動画をアップロード'}
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          e.preventDefault();
                          setUploadedVideoId(null);
                          setVideoData(null);
                          setSelectedFile(null);
                          setCleanVideoFile(null);
                          setCleanVideoFiles([]);
                          setProductExcelFile(null);
                          setTrendExcelFile(null);
                          setProgress(0);
                          setMessage("");
                          setDuplicateVideo(null);
                          setUploadMode('live_capture');
                          navigate('/');
                        }}
                        className="px-6 py-3 text-sm text-white bg-gradient-to-r from-[#FF0050] to-[#00F2EA] rounded-lg hover:opacity-90 transition-opacity cursor-pointer shadow-sm flex items-center gap-2"
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg>
                        ライブURLで分析
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        ) : videoData ? (
          videoData.status === 'DONE' || (videoData.status === 'ERROR' && videoData.reports_1 && videoData.reports_1.length > 0) ? (
            console.log("[MainContent] Rendering VideoDetail for videoData:", videoData) ||
            <VideoDetail videoData={videoData} editorParams={editorParams} />
          ) : videoData.status === 'ERROR' ? (
            <div className="w-full flex flex-col items-center justify-center">
              <div className="w-full max-w-md mx-auto">
                <div className="rounded-2xl p-8 border transition-all duration-200 border-red-300/30 bg-red-500/10 backdrop-blur-sm">
                  <div className="flex flex-col items-center text-center space-y-4">
                    <div className="text-4xl">⚠️</div>
                    <p className="text-base font-semibold text-red-200">
                      {window.__t('errorAnalysisMessage') || '解析中にエラーが発生しました。'}
                    </p>
                    <p className="text-sm text-gray-300">
                      動画データは保存されています。解析のみ再試行できます。
                    </p>
                    <p className="text-xs text-gray-400">
                      {videoData.original_filename || ''}
                      {videoData.error_message ? ` — ${videoData.error_message}` : ''}
                    </p>
                    {/* Primary: Retry Analysis */}
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        try {
                          const btn = e.currentTarget;
                          btn.disabled = true;
                          btn.textContent = '再試行中...';
                          const result = await VideoService.retryAnalysis(videoData.id);
                          // Use resume status from API response
                          const resumeStatus = result?.new_status || 'uploaded';
                          setVideoData({ ...videoData, status: resumeStatus });
                          // Trigger Sidebar refresh immediately (optimistic UI)
                          if (onUploadSuccess) onUploadSuccess();
                          toast({ title: '解析を再開しました', description: '動画データはそのまま保持されています。' });
                        } catch (err) {
                          console.error('Retry analysis failed:', err);
                          toast({ title: '再試行に失敗しました', description: err?.message || 'しばらくしてからもう一度お試しください。', variant: 'destructive' });
                          e.currentTarget.disabled = false;
                          e.currentTarget.textContent = '解析を再試行';
                        }
                      }}
                      className="mt-2 px-6 py-3 text-sm text-white bg-[#7D01FF] rounded-lg hover:bg-[#6B00DD] transition-colors cursor-pointer shadow-sm font-semibold"
                    >
                      解析を再試行
                    </button>
                    {/* Secondary: New Upload */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        setSelectedFile(null);
                        setUploading(false);
                        setProgress(0);
                        setUploadedVideoId(null);
                        setVideoData(null);
                        setMessage('');
                        setMessageType('');
                        setUploadMode(null);
                        setCleanVideoFile(null);
                        setCleanVideoFiles([]);
                        setProductExcelFile(null);
                        setTrendExcelFile(null);
                        setDuplicateVideo(null);
                        navigate('/');
                      }}
                      className="px-6 py-2 text-xs text-gray-400 border border-gray-600 rounded-lg hover:bg-gray-800 transition-colors cursor-pointer"
                    >
                      + {window.__t('newUploadButton') || '新しい動画をアップロード'}
                    </button>
                  </div>
                </div>
                {/* Error Log Section */}
                <ErrorLogPanel videoId={videoData.id} />
              </div>
            </div>
          ) : null
        )
          : children ?? (
            <>
              <div className="w-full flex flex-col items-center justify-center">
                <div className="w-full">
                  <h4 className="w-full text-center">
                    {window.__t('header').split('\n').map((line, idx, arr) => (
                      <span key={idx} className="text-gray-500 italic text-lg">
                    {line}
                    {idx < arr.length - 1 && <br className="block md:hidden" />}
                  </span>
                ))}
              </h4>
            </div>
                <div className="w-full mt-[20px] [@media(max-height:650px)]:mt-[20px]">
                  <h4 className="w-full mb-[22px] text-center">
                    {window.__t('uploadText').split('\n').map((line, idx, arr) => (
                      <span key={idx} className="text-gray-900 text-2xl !font-bold font-cabin">
                        {line}
                        {idx < arr.length - 1 && <br className="block md:hidden" />}
                      </span>
                    ))}
                  </h4>
                  <div className={`w-full ${(uploading || uploadedVideoId) ? 'max-w-xl' : 'max-w-md'} mx-auto`}>
                    <div
                      className="rounded-2xl p-8 border transition-all duration-200 border-gray-200 bg-gray-50 hover:border-gray-300 hover:bg-gray-100"
                      onDragOver={handleDragOver}
                      onDrop={handleDrop}
                    >
                      {selectedFile && duplicateVideo ? (
                        <>
                          <div className="flex flex-col items-center text-center space-y-4">
                            <div className="w-14 h-14 rounded-full bg-amber-50 flex items-center justify-center">
                              <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#d97706" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                            </div>
                            <div>
                              <p className="text-sm font-semibold text-gray-800">
                                この動画はすでに解析済みです
                              </p>
                              <p className="text-xs text-gray-500 mt-1">
                                「{duplicateVideo.original_filename}」の解析結果が見つかりました
                              </p>
                            </div>
                            <div className="flex flex-col sm:flex-row gap-2 w-full max-w-xs">
                              <button
                                onClick={() => {
                                  const vid = duplicateVideo.id;
                                  setSelectedFile(null);
                                  setDuplicateVideo(null);
                                  navigate(`/video/${vid}`);
                                }}
                                className="flex-1 h-[41px] flex items-center justify-center bg-[#7D01FF] text-white rounded-md text-sm cursor-pointer hover:bg-[#6a01d9] transition-colors"
                              >
                                解析結果を見る
                              </button>
                              <button
                                onClick={() => {
                                  setDuplicateVideo(null);
                                  setResumeUploadId(null);
                                  setUploadedVideoId(null);
                                  setVideoData(null);
                                  setMessage("");
                                  setProgress(0);
                                }}
                                className="flex-1 h-[41px] flex items-center justify-center bg-white text-gray-600 border border-gray-300 rounded-md text-sm cursor-pointer hover:bg-gray-50 transition-colors"
                              >
                                再解析する
                              </button>
                              <button
                                onClick={handleCancel}
                                className="flex-1 h-[41px] bg-gray-200 text-gray-500 rounded-md text-sm cursor-pointer hover:bg-gray-300 transition-colors"
                              >
                                キャンセル
                              </button>
                            </div>
                          </div>
                        </>
                      ) : selectedFile ? (
                        <>
                          <div className="flex flex-col items-center text-center space-y-6">
                            <div className="text-4xl">🎬</div>
                            <div>
                              <p className="text-sm font-semibold">
                                {selectedFile.name}
                              </p>
                              <p className="text-xs text-gray-500">
                                {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                              </p>
                            </div>
                            <div className="flex gap-2">
                              <button
                                onClick={handleUpload}
                                disabled={uploading}
                                className="w-[143px] h-[41px] flex items-center justify-center bg-white text-[#7D01FF] border border-[#7D01FF] rounded-md leading-[28px] cursor-pointer hover:bg-gray-100"
                              >
                                {window.__t('uploadButton')}
                              </button>
                              <button
                                onClick={handleCancel}
                                className="w-[143px] h-[41px] bg-gray-300 text-gray-700 rounded-md text-sm cursor-pointer hover:bg-gray-100"
                              >
                                {window.__t('cancelButton')}
                              </button>
                            </div>
                          </div>
                        </>
                      ) : resumeUploadId ? (
                        <>
                          <div className="flex flex-col items-center text-center space-y-4 w-full max-w-sm">
                            <div className="text-4xl">⏸️</div>
                            <div>
                              <p className="text-sm font-semibold">
                                {window.__t('resumeUploadTitle') || 'アップロードを再開できます'}
                              </p>
                              <p className="text-xs text-gray-500 mt-1">
                                {window.__t('resumeUploadDesc') || '前回のアップロードが途中で止まっています。続きから再開しますか？'}
                              </p>
                            </div>
                            {/* File info card */}
                            {resumeInfo && (
                              <div className="w-full bg-gray-50 rounded-lg p-3 text-left">
                                <div className="flex items-start gap-3">
                                  <div className="w-8 h-8 rounded bg-purple-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7D01FF" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
                                  </div>
                                  <div className="flex-1 min-w-0">
                                    <p className="text-xs font-medium text-gray-800 truncate" title={resumeInfo.fileName}>
                                      {resumeInfo.fileName || '不明なファイル'}
                                    </p>
                                    <div className="flex items-center gap-2 mt-1">
                                      <span className="text-[10px] text-gray-500">
                                        {formatFileSize(resumeInfo.fileSize)}
                                      </span>
                                      {resumeInfo.createdAt && (
                                        <span className="text-[10px] text-gray-400">
                                          {formatRelativeTime(resumeInfo.createdAt)}
                                        </span>
                                      )}
                                    </div>
                                    {/* Progress bar */}
                                    <div className="mt-2">
                                      <div className="flex justify-between text-[10px] text-gray-500 mb-0.5">
                                        <span>アップロード済み</span>
                                        <span>{resumeInfo.progress}%</span>
                                      </div>
                                      <div className="w-full bg-gray-200 rounded-full h-1.5">
                                        <div
                                          className="bg-[#7D01FF] h-1.5 rounded-full transition-all"
                                          style={{ width: `${resumeInfo.progress}%` }}
                                        />
                                      </div>
                                    </div>
                                  </div>
                                </div>
                              </div>
                            )}
                            {/* Action buttons */}
                            <div className="flex flex-col gap-2 w-full">
                              <button
                                onClick={handleResumeUpload}
                                disabled={uploading || processingResume}
                                className="w-full h-[41px] flex items-center justify-center bg-[#7D01FF] text-white rounded-md text-sm font-medium hover:bg-[#6a01d9] transition-colors"
                              >
                                {resumeInfo?.hasFileHandle
                                  ? '続きから再開'
                                  : 'ファイルを選んで再開'
                                }
                              </button>
                              <div className="flex gap-2">
                                <button
                                  onClick={handleResumeWithNewFile}
                                  disabled={uploading || processingResume}
                                  className="flex-1 h-[36px] flex items-center justify-center bg-white text-gray-600 border border-gray-300 rounded-md text-xs hover:bg-gray-50 transition-colors"
                                >
                                  別の動画を選ぶ
                                </button>
                                <button
                                  onClick={handleSkipResume}
                                  disabled={uploading || processingResume}
                                  className="flex-1 h-[36px] flex items-center justify-center bg-white text-red-500 border border-red-200 rounded-md text-xs hover:bg-red-50 transition-colors"
                                >
                                  削除
                                </button>
                              </div>
                            </div>
                          </div>
                        </>
                      ) : uploadMode === 'clean_video' && duplicateVideo ? (
                        <>
                          <div className="flex flex-col items-center text-center space-y-4">
                            <div className="w-14 h-14 rounded-full bg-amber-50 flex items-center justify-center">
                              <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#d97706" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                            </div>
                            <div>
                              <p className="text-sm font-semibold text-gray-800">
                                この動画はすでに解析済みです
                              </p>
                              <p className="text-xs text-gray-500 mt-1">
                                「{duplicateVideo.original_filename}」の解析結果が見つかりました
                              </p>
                            </div>
                            <div className="flex flex-col sm:flex-row gap-2 w-full max-w-xs">
                              <button
                                onClick={() => {
                                  const vid = duplicateVideo.id;
                                  setCleanVideoFile(null);
                                  setProductExcelFile(null);
                                  setTrendExcelFile(null);
                                  setDuplicateVideo(null);
                                  setUploadMode(null);
                                  navigate(`/video/${vid}`);
                                }}
                                className="flex-1 h-[41px] flex items-center justify-center bg-[#7D01FF] text-white rounded-md text-sm cursor-pointer hover:bg-[#6a01d9] transition-colors"
                              >
                                解析結果を見る
                              </button>
                              <button
                                onClick={() => {
                                  setDuplicateVideo(null);
                                }}
                                className="flex-1 h-[41px] flex items-center justify-center bg-white text-gray-600 border border-gray-300 rounded-md text-sm cursor-pointer hover:bg-gray-50 transition-colors"
                              >
                                再解析する
                              </button>
                              <button
                                onClick={handleCancelCleanVideo}
                                className="flex-1 h-[41px] bg-gray-200 text-gray-500 rounded-md text-sm cursor-pointer hover:bg-gray-300 transition-colors"
                              >
                                キャンセル
                              </button>
                            </div>
                          </div>
                        </>
                      ) : uploadMode === 'clean_video' ? (
                        <>
                          <div className="flex flex-col items-center text-center space-y-4">
                            <div className="text-3xl">🎬</div>
                            <p className="text-gray-800 text-sm font-semibold">クリーン動画 + Excelデータ</p>

                            {/* Clean Video Files (multiple) */}
                            <div className="w-full">
                              <label className="block text-left text-xs text-gray-400 mb-1">クリーン動画（複数選択可）</label>
                              <label className="w-full h-[38px] flex items-center justify-center bg-gray-100 border border-gray-300 rounded-md text-sm text-gray-700 cursor-pointer hover:bg-gray-200 transition-colors">
                                {cleanVideoFiles.length > 1
                                  ? `${cleanVideoFiles.length}本の動画を選択中`
                                  : cleanVideoFile
                                    ? cleanVideoFile.name
                                    : "動画を選択"}
                                <input type="file" accept="video/*" multiple onChange={handleCleanVideoFilesSelect} className="hidden" />
                              </label>
                              {/* Show file list when multiple files selected */}
                              {cleanVideoFiles.length > 1 && (
                                <div className="mt-2 space-y-1 max-h-[120px] overflow-y-auto">
                                  {cleanVideoFiles.map((f, idx) => (
                                    <div key={idx} className="flex items-center justify-between bg-white border border-gray-200 rounded px-2 py-1 text-xs">
                                      <span className="text-gray-700 truncate flex-1 text-left">
                                        {idx + 1}. {f.name}
                                        <span className="text-gray-400 ml-1">({(f.size / 1024 / 1024).toFixed(0)}MB)</span>
                                      </span>
                                      <button
                                        onClick={() => handleRemoveCleanVideoFile(idx)}
                                        className="ml-2 text-red-400 hover:text-red-600 text-xs flex-shrink-0"
                                      >
                                        ✕
                                      </button>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>

                            {/* Product Excel */}
                            <div className="w-full">
                              <label className="block text-left text-xs text-gray-400 mb-1">商品データ (product.xlsx)</label>
                              <label className="w-full h-[38px] flex items-center justify-center bg-gray-100 border border-gray-300 rounded-md text-sm text-gray-700 cursor-pointer hover:bg-gray-200 transition-colors">
                                {productExcelFile ? productExcelFile.name : "Excelを選択"}
                                <input type="file" accept=".xlsx,.xls" onChange={handleProductExcelSelect} className="hidden" />
                              </label>
                            </div>

                            {/* Trend Stats Excel */}
                            <div className="w-full">
                              <label className="block text-left text-xs text-gray-400 mb-1">トレンドデータ (trend_stats.xlsx)</label>
                              <label className="w-full h-[38px] flex items-center justify-center bg-gray-100 border border-gray-300 rounded-md text-sm text-gray-700 cursor-pointer hover:bg-gray-200 transition-colors">
                                {trendExcelFile ? trendExcelFile.name : "Excelを選択"}
                                <input type="file" accept=".xlsx,.xls" onChange={handleTrendExcelSelect} className="hidden" />
                              </label>
                            </div>

                            {cleanVideoFiles.length > 1 && (
                              <p className="text-xs text-gray-400">
                                同じExcelデータが全{cleanVideoFiles.length}本の動画に適用されます
                              </p>
                            )}

                            <div className="flex gap-2 pt-2">
                              <button
                                onClick={handleCleanVideoUpload}
                                disabled={uploading || (!cleanVideoFile && cleanVideoFiles.length === 0) || !productExcelFile || !trendExcelFile}
                                className="w-[143px] h-[41px] flex items-center justify-center bg-white text-[#7D01FF] border border-[#7D01FF] rounded-md leading-[28px] cursor-pointer hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
                              >
                                {cleanVideoFiles.length > 1 ? `${cleanVideoFiles.length}本アップロード` : 'アップロード'}
                              </button>
                              <button
                                onClick={handleCancelCleanVideo}
                                className="w-[143px] h-[41px] bg-gray-300 text-gray-700 rounded-md text-sm cursor-pointer hover:bg-gray-100"
                              >
                                {window.__t('cancelButton')}
                              </button>
                            </div>
                          </div>
                        </>
                      ) : uploadMode === 'live_capture' ? (
                        <>
                          <div className="flex flex-col items-center text-center space-y-6">
                            <div className="w-20 h-20 rounded-full bg-gradient-to-r from-[#FF0050] to-[#00F2EA] flex items-center justify-center shadow-lg">
                              <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg>
                            </div>
                            <div className="w-full max-w-sm">
                              <p className="text-sm font-semibold text-gray-800 mb-3">
                                TikTokライブURLを貼り付け
                              </p>
                              <input
                                type="text"
                                value={liveUrl}
                                onChange={(e) => setLiveUrl(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter' && !liveChecking && !liveCapturing) {
                                    handleLiveCheck();
                                  }
                                }}
                                placeholder="https://www.tiktok.com/@user/live"
                                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#FF0050] focus:border-transparent transition-all"
                                disabled={liveCapturing}
                              />
                              {liveInfo && liveInfo.is_live && (
                                <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg">
                                  <div className="flex items-center gap-2">
                                    <span className="relative flex h-3 w-3">
                                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                                      <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500"></span>
                                    </span>
                                    <span className="text-sm font-medium text-green-800">
                                      @{liveInfo.username} がライブ配信中
                                    </span>
                                  </div>
                                  {liveInfo.title && (
                                    <p className="text-xs text-green-600 mt-1 truncate">
                                      {liveInfo.title}
                                    </p>
                                  )}
                                </div>
                              )}
                            </div>
                            <div className="flex gap-2">
                              {liveInfo && liveInfo.is_live ? (
                                <button
                                  onClick={handleLiveCapture}
                                  disabled={liveCapturing}
                                  className="w-[180px] h-[41px] flex items-center justify-center bg-gradient-to-r from-[#FF0050] to-[#00F2EA] text-white rounded-md text-sm cursor-pointer hover:opacity-90 transition-opacity disabled:opacity-50"
                                >
                                  {liveCapturing ? (
                                    <>
                                      <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                                      接続中...
                                    </>
                                  ) : (
                                    <>録画・解析開始</>
                                  )}
                                </button>
                              ) : (
                                <button
                                  onClick={handleLiveCheck}
                                  disabled={liveChecking || !liveUrl.trim()}
                                  className="w-[180px] h-[41px] flex items-center justify-center bg-white text-[#7D01FF] border border-[#7D01FF] rounded-md text-sm cursor-pointer hover:bg-gray-100 transition-colors disabled:opacity-50"
                                >
                                  {liveChecking ? (
                                    <>
                                      <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-[#7D01FF]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                                      チェック中...
                                    </>
                                  ) : (
                                    <>ライブチェック</>
                                  )}
                                </button>
                              )}
                              <button
                                onClick={handleCancelLive}
                                disabled={liveCapturing}
                                className="w-[143px] h-[41px] bg-gray-300 text-gray-700 rounded-md text-sm cursor-pointer hover:bg-gray-100 disabled:opacity-50"
                              >
                                戻る
                              </button>
                            </div>
                          </div>
                        </>
                      ) : (
                        <>
                          <div className="flex flex-col items-center text-center space-y-6">
                            <div className="w-20 h-20 rounded-full bg-white flex items-center justify-center shadow-lg">
                              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#5e29ff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-8 h-8 text-primary"><path d="M12 3v12" /><path d="m17 8-5-5-5 5" /><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /></svg>
                            </div>
                            <h5 className="hidden md:inline text-gray-600 text-lg font-cabin text-center">
                              {window.__t('dragDropText')}
                            </h5>
                            <div className="flex flex-col sm:flex-row gap-3">
                              <label
                                className="
                                  w-[180px] h-[41px]
                                  flex items-center justify-center
                                  bg-white text-[#7D01FF]
                                  border border-[#7D01FF]
                                  rounded-md
                                  text-[13px] leading-[28px]
                                  font-extralight
                                  cursor-pointer
                                  transition-transform duration-150 ease-out
                                  active:scale-[0.96]
                                  select-none
                                  hover:bg-gray-100
                                "
                                onMouseDown={(e) => {
                                  if (!isLoggedIn || checkingResume) {
                                    e.preventDefault();
                                    if (!isLoggedIn) setShowLoginModal(true);
                                  }
                                }}
                              >
                                画面収録アップ
                                <input
                                  type="file"
                                  accept="video/*"
                                  disabled={!isLoggedIn || checkingResume}
                                  onMouseDown={(e) => {
                                    if (!isLoggedIn || checkingResume) {
                                      e.preventDefault();
                                    }
                                  }}
                                  onClick={(e) => {
                                    if (!isLoggedIn || checkingResume) {
                                      e.preventDefault();
                                    }
                                  }}
                                  onChange={(e) => {
                                    setUploadMode('screen_recording');
                                    handleFileSelect(e);
                                  }}
                                  className="hidden"
                                />
                              </label>
                              <button
                                className="
                                  w-[180px] h-[41px]
                                  flex items-center justify-center
                                  bg-[#7D01FF] text-white
                                  border border-[#7D01FF]
                                  rounded-md
                                  text-[13px] leading-[28px]
                                  font-extralight
                                  cursor-pointer
                                  transition-transform duration-150 ease-out
                                  active:scale-[0.96]
                                  select-none
                                  hover:bg-[#6a01d9]
                                "
                                onClick={() => {
                                  if (!isLoggedIn) {
                                    setShowLoginModal(true);
                                    return;
                                  }
                                  setUploadMode('clean_video');
                                }}
                              >
                                クリーン動画アップ
                              </button>
                              <button
                                className="
                                  w-[180px] h-[41px]
                                  flex items-center justify-center
                                  bg-gradient-to-r from-[#FF0050] to-[#00F2EA] text-white
                                  border-0
                                  rounded-md
                                  text-[13px] leading-[28px]
                                  font-extralight
                                  cursor-pointer
                                  transition-transform duration-150 ease-out
                                  active:scale-[0.96]
                                  select-none
                                  hover:opacity-90
                                "
                                onClick={() => {
                                  if (!isLoggedIn) {
                                    setShowLoginModal(true);
                                    return;
                                  }
                                  setUploadMode('live_capture');
                                }}
                              >
                                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mr-1"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/></svg>
                                ライブURL
                              </button>
                            </div>
                          </div>
                        </>
                      )}
                      {message && (
                        <p
                          className={`text-xs text-center ${messageType === "success"
                            ? "text-green-600"
                            : "text-red-600"
                            }`}
                        >
                          {message}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}
        {/* Hidden file input for resume functionality */}
        <input
          ref={resumeFileInputRef}
          type="file"
          accept="video/*"
          onChange={handleResumeFileSelect}
          className="hidden"
        />
      </Body>

      <div className={children ? "md:hidden" : ""}>
        <Footer showChatInput={videoData?.status === 'DONE' && !showFeedback} />
      </div>
    </div>
  );
}
