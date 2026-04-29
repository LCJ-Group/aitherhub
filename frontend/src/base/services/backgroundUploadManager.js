/**
 * BackgroundUploadManager
 *
 * YouTube-style background upload manager that allows users to:
 * - Upload multiple videos simultaneously (max 3 concurrent)
 * - Continue using the app while uploads run in the background
 * - See upload progress in the sidebar
 * - Automatic retry on transient network errors (timeout, signal abort)
 * - Manual retry for permanently failed tasks
 * - **Survive page refresh** — pending uploads restored from IndexedDB
 * - **beforeunload warning** — prevents accidental navigation during uploads
 *
 * This module is a singleton event emitter that manages upload tasks independently
 * from the MainContent UI state.
 */

const MAX_CONCURRENT_UPLOADS = 3;
const AUTO_RETRY_LIMIT = 3;
const AUTO_RETRY_BASE_DELAY_MS = 5_000; // 5s → 10s → 20s (exponential)

class BackgroundUploadManager {
  constructor() {
    /** @type {Map<string, UploadTask>} */
    this.tasks = new Map();
    /** @type {Set<Function>} */
    this.listeners = new Set();
    this._taskIdCounter = 0;
    this._beforeUnloadBound = this._onBeforeUnload.bind(this);
    this._beforeUnloadAttached = false;
  }

  /**
   * Generate a unique task ID
   */
  _nextId() {
    return `bg_upload_${Date.now()}_${++this._taskIdCounter}`;
  }

  /**
   * Subscribe to task state changes
   * @param {Function} listener - Called with current tasks map whenever state changes
   * @returns {Function} unsubscribe function
   */
  subscribe(listener) {
    this.listeners.add(listener);
    listener(this.getTasksSnapshot());
    return () => this.listeners.delete(listener);
  }

  /**
   * Notify all listeners of state change
   */
  _notify() {
    const snapshot = this.getTasksSnapshot();
    this.listeners.forEach(fn => {
      try { fn(snapshot); } catch (e) { console.error('[BGUpload] Listener error:', e); }
    });
    this._updateBeforeUnload();
  }

  /**
   * Get a snapshot of all tasks (for React state)
   * @returns {Array<{id, fileName, fileSize, progress, status, error, videoId, startTime, retryCount, uploadId, uploadMode}>}
   */
  getTasksSnapshot() {
    return Array.from(this.tasks.values()).map(t => ({
      id: t.id,
      fileName: t.fileName,
      fileSize: t.fileSize,
      progress: t.progress,
      status: t.status, // 'uploading' | 'completing' | 'retrying' | 'done' | 'error' | 'pending_resume'
      error: t.error,
      videoId: t.videoId,
      startTime: t.startTime,
      uploadMode: t.uploadMode,
      retryCount: t.retryCount || 0,
      uploadId: t.uploadId || null,
    }));
  }

  /**
   * Get count of active (uploading/completing) tasks
   */
  getActiveCount() {
    let count = 0;
    for (const t of this.tasks.values()) {
      if (t.status === 'uploading' || t.status === 'completing' || t.status === 'retrying') count++;
    }
    return count;
  }

  /**
   * Check if there are any active uploads (for beforeunload)
   */
  hasActiveUploads() {
    for (const t of this.tasks.values()) {
      if (t.status === 'uploading' || t.status === 'completing' || t.status === 'retrying') return true;
    }
    return false;
  }

  /**
   * Check if a new upload can be started
   * @returns {{ allowed: boolean, reason?: string }}
   */
  canStartUpload() {
    const active = this.getActiveCount();
    if (active >= MAX_CONCURRENT_UPLOADS) {
      return {
        allowed: false,
        reason: `max_concurrent`,
        activeCount: active,
        maxCount: MAX_CONCURRENT_UPLOADS,
      };
    }
    return { allowed: true };
  }

  /**
   * Detect transient (retryable) errors – network timeouts, aborts, etc.
   */
  _isTransientError(err) {
    const msg = (err?.message || '').toLowerCase();
    return (
      msg.includes('network') ||
      msg.includes('timeout') ||
      msg.includes('aborted') ||
      msg.includes('signal') ||
      msg.includes('fetch') ||
      msg.includes('econnreset') ||
      err?.name === 'AbortError' ||
      err?.name === 'TimeoutError' ||
      err?.code === 'ERR_NETWORK'
    );
  }

  // ═══════════════════════════════════════════════════════════════════
  //  beforeunload — warn user before navigating away during uploads
  // ═══════════════════════════════════════════════════════════════════

  _onBeforeUnload(e) {
    if (this.hasActiveUploads()) {
      e.preventDefault();
      // Modern browsers ignore custom messages but still show a generic warning
      e.returnValue = 'アップロード中です。ページを離れるとアップロードが中断されます。';
      return e.returnValue;
    }
  }

  _updateBeforeUnload() {
    const shouldAttach = this.hasActiveUploads();
    if (shouldAttach && !this._beforeUnloadAttached) {
      window.addEventListener('beforeunload', this._beforeUnloadBound);
      this._beforeUnloadAttached = true;
    } else if (!shouldAttach && this._beforeUnloadAttached) {
      window.removeEventListener('beforeunload', this._beforeUnloadBound);
      this._beforeUnloadAttached = false;
    }
  }

  // ═══════════════════════════════════════════════════════════════════
  //  Restore pending uploads from IndexedDB after page refresh
  // ═══════════════════════════════════════════════════════════════════

  /**
   * Restore interrupted uploads from IndexedDB.
   * Called once on app initialization.
   * Creates "pending_resume" tasks in the sidebar so the user can re-select
   * the file and resume the upload.
   *
   * @param {Function} getAllUploads - async function that returns all IndexedDB upload records
   */
  async restoreFromIndexedDB(getAllUploads) {
    try {
      const uploads = await getAllUploads();
      if (!uploads || uploads.length === 0) return;

      let restoredCount = 0;
      for (const meta of uploads) {
        // Skip if no uploadId or already completed
        if (!meta.uploadId) continue;
        // Skip if already tracked
        const existingTask = Array.from(this.tasks.values()).find(
          t => t.uploadId === meta.uploadId
        );
        if (existingTask) continue;

        const uploadedBlocks = meta.uploadedBlocks?.length || 0;
        const totalBlocks = meta.blockIds?.length || 1;
        const progress = Math.round((uploadedBlocks / totalBlocks) * 100);

        const id = this._nextId();
        const task = {
          id,
          fileName: meta.fileName || 'Unknown',
          fileSize: meta.fileSize || 0,
          progress: Math.min(progress, 99), // Show last known progress
          status: 'pending_resume',
          error: null,
          videoId: meta.videoId || null,
          startTime: meta.timestamp || Date.now(),
          uploadMode: meta.uploadMode || 'screen_recording',
          retryCount: 0,
          uploadId: meta.uploadId,
          _uploadUrl: meta.uploadUrl,
          _metadata: meta,
          _executeFn: null,
          _onComplete: null,
          _onError: null,
        };
        this.tasks.set(id, task);
        restoredCount++;
      }

      if (restoredCount > 0) {
        console.log(`[BGUpload] Restored ${restoredCount} interrupted upload(s) from IndexedDB`);
        this._notify();
      }
    } catch (err) {
      console.error('[BGUpload] Failed to restore uploads from IndexedDB:', err);
    }
  }

  /**
   * Resume a pending_resume task with a new file.
   * Called from Sidebar when user re-selects the file.
   *
   * @param {string} taskId - The background task ID
   * @param {File} file - The re-selected file
   * @param {Function} executeFn - Async function that performs the resume upload
   * @param {Function} [onComplete] - Called with video_id on success
   * @param {Function} [onError] - Called with error on failure
   * @returns {boolean} true if resume started
   */
  resumePendingTask(taskId, file, executeFn, onComplete, onError) {
    const task = this.tasks.get(taskId);
    if (!task || task.status !== 'pending_resume') return false;

    task.status = 'uploading';
    task.progress = task.progress || 0; // Keep previous progress
    task.error = null;
    task.startTime = Date.now();
    task._executeFn = executeFn;
    task._onComplete = onComplete;
    task._onError = onError;
    this._notify();
    this._runTask(task);
    return true;
  }

  /**
   * Dismiss a pending_resume task (user chooses to skip)
   * @param {string} taskId
   * @param {Function} [clearMetadata] - async function to clear IndexedDB metadata
   */
  async dismissPendingTask(taskId, clearMetadata) {
    const task = this.tasks.get(taskId);
    if (!task) return;
    if (clearMetadata && task.uploadId) {
      try {
        await clearMetadata(task.uploadId);
      } catch (e) {
        console.warn('[BGUpload] Failed to clear metadata for dismissed task:', e);
      }
    }
    this.tasks.delete(taskId);
    this._notify();
  }

  /**
   * Start a background upload task
   *
   * @param {Object} params
   * @param {string} params.fileName - Display name
   * @param {number} params.fileSize - File size in bytes
   * @param {string} params.uploadMode - 'screen_recording' | 'clean_video' | 'batch_clean_video'
   * @param {Function} params.executeFn - Async function that performs the actual upload.
   *   Receives { onProgress(pct), onVideoId(id) } callbacks.
   *   Should return the video_id on success.
   * @param {Function} [params.onComplete] - Called with video_id when upload + complete API succeeds
   * @param {Function} [params.onError] - Called with error when upload fails
   * @returns {string} taskId
   */
  startUpload({ fileName, fileSize, uploadMode, executeFn, onComplete, onError }) {
    const id = this._nextId();
    const task = {
      id,
      fileName,
      fileSize,
      progress: 0,
      status: 'uploading',
      error: null,
      videoId: null,
      startTime: Date.now(),
      uploadMode: uploadMode || 'screen_recording',
      retryCount: 0,
      uploadId: null,
      _executeFn: executeFn,
      _onComplete: onComplete,
      _onError: onError,
    };
    this.tasks.set(id, task);
    this._notify();
    this._runTask(task);
    return id;
  }

  /**
   * Internal: execute task with auto-retry on transient errors
   */
  async _runTask(task) {
    try {
      const videoId = await task._executeFn({
        onProgress: (pct) => {
          task.progress = pct;
          task.status = 'uploading';
          this._notify();
        },
        onVideoId: (vid) => {
          task.videoId = vid;
        },
      });

      task.status = 'done';
      task.progress = 100;
      task.videoId = videoId || task.videoId;
      task.error = null;
      this._notify();

      if (task._onComplete) task._onComplete(task.videoId);

      // Auto-remove completed tasks after 5 seconds
      setTimeout(() => {
        this.tasks.delete(task.id);
        this._notify();
      }, 5000);

    } catch (err) {
      console.error(`[BGUpload] Task ${task.id} failed (attempt ${task.retryCount + 1}):`, err);

      // Auto-retry for transient errors
      if (this._isTransientError(err) && task.retryCount < AUTO_RETRY_LIMIT) {
        task.retryCount += 1;
        const delay = AUTO_RETRY_BASE_DELAY_MS * Math.pow(2, task.retryCount - 1);
        task.status = 'retrying';
        task.error = `${err?.message || 'Upload failed'} — retrying in ${Math.round(delay / 1000)}s (${task.retryCount}/${AUTO_RETRY_LIMIT})`;
        this._notify();

        console.warn(`[BGUpload] Auto-retry ${task.retryCount}/${AUTO_RETRY_LIMIT} for task ${task.id} in ${delay}ms`);
        await new Promise(resolve => setTimeout(resolve, delay));

        task.error = null;
        this._runTask(task);
        return;
      }

      // Permanent failure
      task.status = 'error';
      task.error = err?.message || 'Upload failed';
      this._notify();

      if (task._onError) task._onError(err);
    }
  }

  /**
   * Manual retry for a failed task
   * @param {string} taskId
   * @returns {boolean} true if retry started
   */
  retryTask(taskId) {
    const task = this.tasks.get(taskId);
    if (!task || task.status !== 'error') return false;
    if (!task._executeFn) {
      console.error(`[BGUpload] Cannot retry task ${taskId}: no executeFn stored`);
      return false;
    }
    task.retryCount = 0;
    task.progress = 0;
    task.error = null;
    task.status = 'uploading';
    task.startTime = Date.now();
    this._notify();
    this._runTask(task);
    return true;
  }

  /**
   * Remove a task (e.g., dismiss error)
   */
  removeTask(taskId) {
    this.tasks.delete(taskId);
    this._notify();
  }

  /**
   * Clear all completed/error tasks
   */
  clearFinished() {
    for (const [id, task] of this.tasks) {
      if (task.status === 'done' || task.status === 'error') {
        this.tasks.delete(id);
      }
    }
    this._notify();
  }
}

// Singleton instance
const backgroundUploadManager = new BackgroundUploadManager();
export default backgroundUploadManager;
export { MAX_CONCURRENT_UPLOADS };
