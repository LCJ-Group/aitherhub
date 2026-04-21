/**
 * BackgroundUploadManager
 *
 * YouTube-style background upload manager that allows users to:
 * - Upload multiple videos simultaneously (max 3 concurrent)
 * - Continue using the app while uploads run in the background
 * - See upload progress in the sidebar
 * - Automatic retry on transient network errors (timeout, signal abort)
 * - Manual retry for permanently failed tasks
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
  }

  /**
   * Get a snapshot of all tasks (for React state)
   * @returns {Array<{id, fileName, fileSize, progress, status, error, videoId, startTime, retryCount}>}
   */
  getTasksSnapshot() {
    return Array.from(this.tasks.values()).map(t => ({
      id: t.id,
      fileName: t.fileName,
      fileSize: t.fileSize,
      progress: t.progress,
      status: t.status, // 'uploading' | 'completing' | 'retrying' | 'done' | 'error'
      error: t.error,
      videoId: t.videoId,
      startTime: t.startTime,
      uploadMode: t.uploadMode,
      retryCount: t.retryCount || 0,
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
