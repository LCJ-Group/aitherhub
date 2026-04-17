/**
 * BackgroundUploadManager
 * 
 * YouTube-style background upload manager that allows users to:
 * - Upload multiple videos simultaneously (max 3 concurrent)
 * - Continue using the app while uploads run in the background
 * - See upload progress in the sidebar
 * 
 * This module is a singleton event emitter that manages upload tasks independently
 * from the MainContent UI state.
 */

const MAX_CONCURRENT_UPLOADS = 3;

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
    // Immediately notify with current state
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
   * @returns {Array<{id, fileName, fileSize, progress, status, error, videoId, startTime}>}
   */
  getTasksSnapshot() {
    return Array.from(this.tasks.values()).map(t => ({
      id: t.id,
      fileName: t.fileName,
      fileSize: t.fileSize,
      progress: t.progress,
      status: t.status, // 'uploading' | 'completing' | 'done' | 'error'
      error: t.error,
      videoId: t.videoId,
      startTime: t.startTime,
      uploadMode: t.uploadMode,
    }));
  }

  /**
   * Get count of active (uploading/completing) tasks
   */
  getActiveCount() {
    let count = 0;
    for (const t of this.tasks.values()) {
      if (t.status === 'uploading' || t.status === 'completing') count++;
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
    };
    this.tasks.set(id, task);
    this._notify();

    // Execute the upload in background
    const run = async () => {
      try {
        const videoId = await executeFn({
          onProgress: (pct) => {
            task.progress = pct;
            this._notify();
          },
          onVideoId: (vid) => {
            task.videoId = vid;
          },
        });

        task.status = 'done';
        task.progress = 100;
        task.videoId = videoId || task.videoId;
        this._notify();

        if (onComplete) onComplete(task.videoId);

        // Auto-remove completed tasks after 5 seconds
        setTimeout(() => {
          this.tasks.delete(id);
          this._notify();
        }, 5000);

      } catch (err) {
        console.error(`[BGUpload] Task ${id} failed:`, err);
        task.status = 'error';
        task.error = err?.message || 'Upload failed';
        this._notify();

        if (onError) onError(err);
      }
    };

    run();
    return id;
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
