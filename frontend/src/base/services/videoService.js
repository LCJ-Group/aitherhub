import BaseApiService from '../api/BaseApiService';
import { URL_CONSTANTS } from '../api/endpoints/constant';
import TokenManager from '../utils/tokenManager';

class VideoService extends BaseApiService {
  constructor() {
    super(import.meta.env.VITE_API_BASE_URL);
  }

  async getVideosByUser(userId) {
    const endpoint1 = `/api/v1/videos/user/${userId}/with-clips`;
    const endpoint2 = `/api/v1/videos/user/${userId}`;

    try {
      // Try the new endpoint with clip counts first
      const response = await this.get(endpoint1);
      if (Array.isArray(response)) {
        return response;
      } else if (response?.data && Array.isArray(response.data)) {
        return response.data;
      } else if (response?.videos && Array.isArray(response.videos)) {
        return response.videos;
      }
      // Response shape mismatch - log and return empty (not an error)
      console.warn(`[VideoService] Unexpected response shape from ${endpoint1}:`, typeof response);
      return [];
    } catch (error) {
      const status1 = error?.response?.status;
      console.warn(`[VideoService] Primary endpoint failed (${status1}):`, endpoint1);

      // Auth errors should propagate immediately - don't mask with fallback
      if (status1 === 401 || status1 === 403) {
        throw error;
      }

      // Fallback to original endpoint without clip counts
      try {
        const fallback = await this.get(endpoint2);
        if (Array.isArray(fallback)) return fallback;
        if (fallback?.data && Array.isArray(fallback.data)) return fallback.data;
        if (fallback?.videos && Array.isArray(fallback.videos)) return fallback.videos;
        console.warn(`[VideoService] Unexpected response shape from ${endpoint2}:`, typeof fallback);
        return [];
      } catch (fallbackError) {
        const status2 = fallbackError?.response?.status;
        console.error(`[VideoService] Both endpoints failed. Primary: ${status1}, Fallback: ${status2}`);
        // Propagate the error so Sidebar can show error state instead of empty
        throw fallbackError;
      }
    }
  }

  async getVideoById(videoId, config = {}) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}`, config);
      if (response?.data) {
        return response.data;
      }
      return response;
    } catch (error) {
      if (error.response?.status === 404 || error.response?.status === 501) {
        const mockVideoDetails = {
          "1": {
            "id": "1",
            "original_filename": "富士山.mp4",
            "status": "completed",
            "created_at": "2026-01-08T00:00:00.000Z",
            "description": {
              "title1": "富士山",
              "content1": "富士山の壮大な景色を収めたこの動画では、四季折々の自然美と静寂な空気感を臨場感たっぷりに感じることができます。朝日や夕焼け、雲海、雪景色が心を癒やし旅情を深めてくれます感動が広がる映像ですとても美しい 富士山の壮大な景色を収めたこの動画では、四季折々の自然美と静寂な空気感を臨場感たっぷりに感じることができます。朝日や夕焼け、雲海、雪景色が心を癒やし旅情を深めてくれます感動が広がる映像ですとても美しい富士山の壮大な景色を収めたこの動画では、四季折々の自然美と静寂な空気感を臨場感たっぷりに感じることができます。朝日や夕焼け、雲海、雪景色が心を癒やし旅情を深めてくれます感動が広がる映像ですとても美しい 富士山の壮大な景色を収めたこの動画では、四季折々の自然美と静寂な空気感を臨場感たっぷりに感じることができます。朝日や夕焼け、雲海、雪景色が心を癒やし旅情を深めてくれます感動が広がる映像ですとても美しい",
              "title2": "tiêu điểm 2",
              "content2": "người dùng đang mỉm cười với người xem",
              "title3": "tiêu điểm 3",
              "content3": "người dùng cúi đầu chào kết thúc video"
            }
          },
          "2": {
            "id": "2",
            "original_filename": "video 2",
            "status": "processing",
            "created_at": "2026-01-08T00:00:00.000Z",
            "description": {
              "title": "tiêu điểm 2",
              "content": "người dùng đang show sản phẩm"
            }
          }
        };
        
        if (mockVideoDetails[videoId]) {
          return mockVideoDetails[videoId];
        }
      }
      throw error;
    }
  }

  /**
   * Stream chat responses from backend SSE endpoint.
   * params: { videoId, messages, token, onMessage, onDone, onError }
   * Returns: { cancel: () => void }
   */
  streamChat({ videoId, messages = [], onMessage = () => {}, onDone = () => {}, onError = () => {} }) {
    const base = (this.client && this.client.defaults && this.client.defaults.baseURL) || import.meta.env.VITE_API_BASE_URL || "";
    const url = `${base.replace(/\/$/, "")}/api/v1/chat/stream?video_id=${encodeURIComponent(videoId)}`;

    const controller = new AbortController();
    const signal = controller.signal;

    (async () => {
      try {
        const headers = {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
        };

        const token = TokenManager.getToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const resp = await fetch(url, {
          method: "POST",
          headers,
          body: JSON.stringify({ messages }),
          credentials: "same-origin",
          signal,
        });

        if (!resp.ok) {
          const txt = await resp.text();
          throw new Error(`Stream request failed: ${resp.status} ${txt}`);
        }

        if (!resp.body) {
          throw new Error("Stream response has no body");
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const lines = raw.split(/\r?\n/);
            for (const line of lines) {
              if (line === "") continue;
              if (line.startsWith("data:")) {
                let payload = line.slice(5);
                if (payload.charAt(0) === " ") payload = payload.slice(1);
                if ((payload.startsWith('"') && payload.endsWith('"')) || (payload.startsWith("'") && payload.endsWith("'"))) {
                  payload = payload.slice(1, -1).trim();
                }
                const isDone = payload === "[DONE]" || payload === "DONE";
                if (isDone) {
                  try { onDone(); } catch (e) {}
                } else if (payload.startsWith("[ERROR]")) {
                  try { onError(new Error(payload)); } catch (e) {}
                } else {
                  try { onMessage(payload); } catch (e) {}
                }
              }
            }
          }
        }

        if (buffer) {
          const lines = buffer.split(/\r?\n/);
          for (const line of lines) {
            if (line === "") continue;
            if (line.startsWith("data:")) {
              let payload = line.slice(5);
              if (payload.charAt(0) === " ") payload = payload.slice(1);
              if ((payload.startsWith('"') && payload.endsWith('"')) || (payload.startsWith("'") && payload.endsWith("'"))) {
                payload = payload.slice(1, -1);
              }
              const isDone = payload === "[DONE]" || payload === "DONE";
              if (isDone) onDone();
              else if (payload.startsWith("[ERROR]")) onError(new Error(payload));
              else onMessage(payload);
            }
          }
        }

        try { onDone(); } catch (e) {}
      } catch (err) {
        if (err.name === 'AbortError') {
          return;
        }
        try { onError(err); } catch (e) {}
      }
    })();

    return { cancel: () => controller.abort() };
  }

  async deleteVideo(videoId) {
    try {
      const response = await this.delete(`/api/v1/videos/${videoId}`);
      return response;
    } catch (error) {
      console.error('Failed to delete video:', error);
      throw error;
    }
  }

  async renameVideo(videoId, newName) {
    try {
      const response = await this.patch(`/api/v1/videos/${videoId}/rename`, { name: newName });
      return response;
    } catch (error) {
      console.error('Failed to rename video:', error);
      throw error;
    }
  }

  async getChatHistory(videoId) {
    try {
      const response = await this.get(`/api/v1/chat/history?video_id=${encodeURIComponent(videoId)}`);
      if (response?.data) return response.data;
      return response;
    } catch (err) {
      throw err;
    }
  }


  /**
   * Generate a download URL for a video from Azure Blob Storage
   * @param {string} videoId - The video ID
   * @param {Object} options
   * @param {number} [options.expiresInMinutes=60] - URL expiration time in minutes
   * @param {string} [options.email] - User email (required by backend). Will fallback to localStorage if not provided.
   * @returns {Promise<string>} - Download URL with SAS token
   */
  async getDownloadUrl(videoId, { expiresInMinutes = 60, email } = {}) {
    try {
      const payload = {
        video_id: videoId,
        expires_in_minutes: expiresInMinutes,
      };

      // Backend expects email; try to supply if available
      if (email) {
        payload.email = email;
      } else {
        try {
          const storedUser = localStorage.getItem("user");
          if (storedUser) {
            const parsed = JSON.parse(storedUser);
            if (parsed && parsed.email) {
              payload.email = parsed.email;
            }
          }
        } catch {
          // ignore storage access / parse errors
        }
      }

      const response = await this.post(`/api/v1/videos/generate-download-url`, payload);
      return response?.download_url || response?.data?.download_url || response;
    } catch (err) {
      console.error("Failed to get download URL:", err);
      throw err;
    }
  }
  /**
   * Stream video processing status updates via Server-Sent Events (SSE).
   *
   * @param {Object} params - Stream parameters
   * @param {string} params.videoId - Video ID to monitor
   * @param {Function} params.onStatusUpdate - Callback when status updates: (data) => void
   *   data shape: { video_id, status, progress, message, updated_at }
   * @param {Function} params.onDone - Callback when processing completes
   * @param {Function} params.onError - Callback on error: (error) => void
   * @returns {Object} - Control object with close() method to stop streaming
   *
   * @example
   * const stream = VideoService.streamVideoStatus({
   *   videoId: 'video-123',
   *   onStatusUpdate: (data) => {
   *     console.log(`Status: ${data.status}, Progress: ${data.progress}%`);
   *   },
   *   onDone: () => console.log('Processing complete'),
   *   onError: (err) => console.error('Stream error:', err),
   * });
   *
   * // Later: stream.close();
   */
  streamVideoStatus({ videoId, onStatusUpdate = () => {}, onDone = () => {}, onError = () => {} }) {
    const base = (this.client && this.client.defaults && this.client.defaults.baseURL) || import.meta.env.VITE_API_BASE_URL || "";
    const url = `${base.replace(/\/$/, "")}/api/v1/videos/${encodeURIComponent(videoId)}/status/stream`;

    const controller = new AbortController();
    const signal = controller.signal;

    // Retry configuration
    const MAX_RETRIES = 5;
    const RETRY_DELAY = 3000; // 3 seconds
    const HEARTBEAT_TIMEOUT = 120000; // 2 minutes without heartbeat = connection lost
    let retryCount = 0;
    let lastHeartbeatTime = Date.now();
    let heartbeatTimeoutId = null;

    const connectWithRetry = async () => {
      try {
        console.log(`SSE: Connecting to video ${videoId} status stream${retryCount > 0 ? ` (retry ${retryCount}/${MAX_RETRIES})` : ''}`);

        const headers = {
          Accept: "text/event-stream",
        };

        const token = TokenManager.getToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const resp = await fetch(url, {
          method: "GET",
          headers,
          credentials: "same-origin",
          signal,
        });

        if (!resp.ok) {
          const txt = await resp.text();
          throw new Error(`SSE request failed: ${resp.status} ${txt}`);
        }

        if (!resp.body) {
          throw new Error("SSE response has no body");
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        // Set up heartbeat timeout check
        const checkHeartbeat = () => {
          const timeSinceLastHeartbeat = Date.now() - lastHeartbeatTime;
          if (timeSinceLastHeartbeat > HEARTBEAT_TIMEOUT) {
            console.warn(`SSE: No heartbeat received for ${Math.round(timeSinceLastHeartbeat/1000)}s, connection may be stale`);
          }
          heartbeatTimeoutId = setTimeout(checkHeartbeat, 30000); // Check every 30 seconds
        };
        checkHeartbeat();

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);

            const lines = raw.split(/\r?\n/);
            for (const line of lines) {
              if (line === "") continue;
              if (line.startsWith("data:")) {
                let payload = line.slice(5).trim();

                // Handle [DONE] marker
                if (payload === "[DONE]" || payload === "DONE") {
                  console.log('SSE: Stream completed successfully');
                  clearTimeout(heartbeatTimeoutId);
                  onDone();
                  return;
                }

                // Parse JSON payload
                try {
                  const data = JSON.parse(payload);

                  // Update heartbeat timestamp for any message
                  lastHeartbeatTime = Date.now();

                  // Handle heartbeat messages
                  if (data.heartbeat) {
                    console.debug(`SSE: Heartbeat received (poll ${data.poll_count})`);
                    continue; // Don't pass heartbeat to onStatusUpdate
                  }

                  // Handle error from server
                  if (data.error) {
                    console.error('SSE: Server error:', data.error);
                    clearTimeout(heartbeatTimeoutId);
                    onError(new Error(data.error));
                    return;
                  }

                  // Send status update
                  onStatusUpdate(data);

                  // Auto-close on completion
                  if (data.status === 'DONE' || data.status === 'ERROR') {
                    console.log(`SSE: Processing ${data.status}, closing stream`);
                    clearTimeout(heartbeatTimeoutId);
                    onDone();
                    return;
                  }
                } catch (parseErr) {
                  console.error('SSE JSON parse error:', parseErr, 'payload:', payload);
                }
              }
            }
          }
        }

        // Handle remaining buffer
        if (buffer) {
          const lines = buffer.split(/\r?\n/);
          for (const line of lines) {
            if (line === "") continue;
            if (line.startsWith("data:")) {
              const payload = line.slice(5).trim();
              if (payload === "[DONE]" || payload === "DONE") {
                console.log('SSE: Stream completed (from buffer)');
                clearTimeout(heartbeatTimeoutId);
                onDone();
              }
            }
          }
        }

        clearTimeout(heartbeatTimeoutId);
        onDone();
      } catch (err) {
        clearTimeout(heartbeatTimeoutId);

        if (err.name === 'AbortError') {
          console.log('SSE: Stream aborted by user');
          return;
        }

        console.error(`SSE: Connection failed: ${err.message}`);

        // Retry logic
        if (retryCount < MAX_RETRIES) {
          retryCount++;
          console.log(`SSE: Retrying connection in ${RETRY_DELAY}ms... (${retryCount}/${MAX_RETRIES})`);
          await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
          return connectWithRetry();
        } else {
          console.error(`SSE: Max retries (${MAX_RETRIES}) exceeded, giving up`);
          onError(err);
        }
      }
    };

    // Start the connection
    connectWithRetry();

    return {
      close: () => controller.abort(),
      cancel: () => controller.abort(), // Alias for compatibility
    };
  }

  async getProductData(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/product-data`);
      return response;
    } catch (error) {
      console.warn('Failed to fetch product data:', error);
      return { products: [], trends: [], has_product_data: false, has_trend_data: false };
    }
  }

  /**
   * Request clip generation for a specific phase.
   * @param {string} videoId
   * @param {number} phaseIndex
   * @param {number} timeStart - Start time in seconds
   * @param {number} timeEnd - End time in seconds
   * @param {number} [speedFactor=1.2] - Playback speed (1.0-1.5x)
   * @returns {Promise<{clip_id, status, message}>}
   */
  async requestClipGeneration(videoId, phaseIndex, timeStart, timeEnd, speedFactor = 1.2) {
    try {
      const response = await this.post(`/api/v1/videos/${videoId}/clips`, {
        phase_index: phaseIndex,
        time_start: timeStart,
        time_end: timeEnd,
        speed_factor: speedFactor,
      });
      return response;
    } catch (error) {
      console.error('Failed to request clip generation:', error);
      throw error;
    }
  }

  /**
   * Get clip generation status for a specific phase.
   * @param {string} videoId
   * @param {number} phaseIndex
   * @returns {Promise<{clip_id, status, clip_url?}>}
   */
  async getClipStatus(videoId, phaseIndex) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/clips/${phaseIndex}`);
      return response;
    } catch (error) {
      console.warn('Failed to get clip status:', error);
      return { status: 'not_found' };
    }
  }

  /**
   * List all clips for a video.
   * @param {string} videoId
   * @returns {Promise<{clips: Array}>}
   */
  async listClips(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/clips`);
      return response;
    } catch (error) {
      console.warn('Failed to list clips:', error);
      return { clips: [] };
    }
  }

  /**
   * Rate a specific phase of a video (1-5 stars + optional comment).
   * @param {string} videoId
   * @param {number} phaseIndex
   * @param {number} rating - 1 to 5
   * @param {string} [comment] - optional feedback comment
   * @returns {Promise<Object>}
   */
  async ratePhase(videoId, phaseIndex, rating, comment = '', reviewerName = '') {
    try {
      const body = { rating, comment };
      if (reviewerName) body.reviewer_name = reviewerName;
      const response = await this.put(`/api/v1/videos/${videoId}/phases/${phaseIndex}/rating`, body);
      return response;
    } catch (error) {
      console.warn('Failed to rate phase:', error);
      throw error;
    }
  }

  async savePhaseComment(videoId, phaseIndex, comment = '', reviewerName = '') {
    try {
      const body = { comment };
      if (reviewerName) body.reviewer_name = reviewerName;
      const response = await this.put(`/api/v1/videos/${videoId}/phases/${phaseIndex}/comment`, body);
      return response;
    } catch (error) {
      console.warn('Failed to save phase comment:', error);
      throw error;
    }
  }

  // =========================================================
  // Human Sales Tags API (Human-in-the-loop)
  // =========================================================

  async updateHumanSalesTags(videoId, phaseIndex, tags, reviewerName = '') {
    try {
      const body = { human_sales_tags: tags };
      if (reviewerName) body.reviewer_name = reviewerName;
      const response = await this.patch(`/api/v1/videos/${videoId}/phases/${phaseIndex}/tags`, body);
      return response;
    } catch (error) {
      console.warn('Failed to update human sales tags:', error);
      throw error;
    }
  }

  // =========================================================
  // Product Exposure Timeline API
  // =========================================================

  async getProductExposures(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/product-exposures`);
      return response;
    } catch (error) {
      console.warn('Failed to fetch product exposures:', error);
      return { exposures: [], count: 0 };
    }
  }

  async updateProductExposure(videoId, exposureId, data) {
    try {
      const response = await this.put(
        `/api/v1/videos/${videoId}/product-exposures/${exposureId}`,
        data,
      );
      return response;
    } catch (error) {
      console.warn('Failed to update product exposure:', error);
      throw error;
    }
  }

  async createProductExposure(videoId, data) {
    try {
      const response = await this.post(
        `/api/v1/videos/${videoId}/product-exposures`,
        data,
      );
      return response;
    } catch (error) {
      console.warn('Failed to create product exposure:', error);
      throw error;
    }
  }

  async deleteProductExposure(videoId, exposureId) {
    try {
      const response = await this.delete(
        `/api/v1/videos/${videoId}/product-exposures/${exposureId}`,
      );
      return response;
    } catch (error) {
      console.warn('Failed to delete product exposure:', error);
      throw error;
    }
  }

  // =========================================================
  // TikTok Live Capture API
  // =========================================================

  /**
   * Check if a TikTok user is currently live.
   * @param {string} liveUrl - TikTok live URL
   * @returns {Promise<{is_live, username, room_id, title, message}>}
   */
  async checkLiveStatus(liveUrl) {
    try {
      const response = await this.post(URL_CONSTANTS.LIVE_CHECK, {
        live_url: liveUrl,
      });
      return response;
    } catch (error) {
      console.error('Failed to check live status:', error);
      throw error;
    }
  }

  /**
   * Start capturing a TikTok live stream.
   * @param {string} liveUrl - TikTok live URL
   * @param {number} [duration=0] - Max recording duration in seconds (0 = until stream ends)
   * @returns {Promise<{video_id, status, stream_title, username, message}>}
   */
  async startLiveCapture(liveUrl, duration = 0) {
    try {
      const response = await this.post(URL_CONSTANTS.LIVE_CAPTURE, {
        live_url: liveUrl,
        duration,
      });
      return response;
    } catch (error) {
      console.error('Failed to start live capture:', error);
      throw error;
    }
  }

  // =========================================================
  // Real-time Live Monitoring API
  // =========================================================

  /**
   * Start real-time monitoring for a live capture.
   * @param {string} videoId - Video ID
   * @param {string} liveUrl - TikTok live URL
   * @returns {Promise}
   */
  async startLiveMonitor(videoId, liveUrl) {
    try {
      const response = await this.post(`${URL_CONSTANTS.LIVE_START_MONITOR}/${videoId}/start-monitor`, {
        live_url: liveUrl,
        video_id: videoId,
      });
      return response;
    } catch (error) {
      console.error('Failed to start live monitor:', error);
      throw error;
    }
  }

  /**
   * Get current live monitoring status.
   * @param {string} videoId - Video ID
   * @returns {Promise}
   */
  async getLiveStatus(videoId) {
    try {
      const response = await this.get(`${URL_CONSTANTS.LIVE_STATUS}/${videoId}/status`);
      return response;
    } catch (error) {
      console.error('Failed to get live status:', error);
      throw error;
    }
  }

  /**
   * Get all active live monitoring sessions.
   * @returns {Promise}
   */
  async getActiveLiveSessions() {
    try {
      const response = await this.get(URL_CONSTANTS.LIVE_ACTIVE);
      return response;
    } catch (error) {
      console.error('Failed to get active sessions:', error);
      throw error;
    }
  }

  /**
   * Get active Chrome extension sessions for the current user.
   * @returns {Promise}
   */
  async getActiveExtensionSessions() {
    try {
      const response = await this.get(`${URL_CONSTANTS.LIVE_EXTENSION_SESSIONS}?active_only=true`);
      return response;
    } catch (error) {
      console.error('Failed to get extension sessions:', error);
      return { sessions: [], count: 0 };
    }
  }

  async cleanupStaleSessions() {
    try {
      const response = await this.post(`${URL_CONSTANTS.LIVE_EXTENSION_SESSIONS}/cleanup`);
      return response;
    } catch (error) {
      console.error('Failed to cleanup stale sessions:', error);
      return { cleaned: 0 };
    }
  }

  /**
   * Stream real-time live events via SSE.
   * @param {Object} params
   * @param {string} params.videoId - Video ID to monitor
   * @param {Function} params.onMetrics - Callback for metrics updates
   * @param {Function} params.onAdvice - Callback for AI advice
   * @param {Function} params.onStreamUrl - Callback for stream URL
   * @param {Function} params.onStreamEnded - Callback when stream ends
   * @param {Function} params.onError - Callback on error
   * @returns {Object} - Control object with close() method
   */
  /**
   * Stream AI chat for LIVE dashboard.
   * Uses the dedicated /api/v1/live/ai/chat endpoint that doesn't require video DONE status.
   * @param {Object} params
   * @param {Array} params.messages - Chat messages array
   * @param {Function} params.onMessage - Callback for each token
   * @param {Function} params.onDone - Callback when streaming is done
   * @param {Function} params.onError - Callback on error
   * @returns {Object} - { cancel: () => void }
   */
  streamLiveAiChat({ messages = [], onMessage = () => {}, onDone = () => {}, onError = () => {} }) {
    const base = (this.client && this.client.defaults && this.client.defaults.baseURL) || import.meta.env.VITE_API_BASE_URL || "";
    const url = `${base.replace(/\/$/, "")}${URL_CONSTANTS.LIVE_AI_CHAT}`;

    const controller = new AbortController();
    const signal = controller.signal;

    (async () => {
      try {
        const headers = {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
        };

        const token = TokenManager.getToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const resp = await fetch(url, {
          method: "POST",
          headers,
          body: JSON.stringify({ messages }),
          credentials: "same-origin",
          signal,
        });

        if (!resp.ok) {
          const txt = await resp.text();
          throw new Error(`Live AI chat request failed: ${resp.status} ${txt}`);
        }

        if (!resp.body) {
          throw new Error("Live AI chat response has no body");
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const lines = raw.split(/\r?\n/);
            for (const line of lines) {
              if (line === "") continue;
              if (line.startsWith("data:")) {
                let payload = line.slice(5);
                if (payload.charAt(0) === " ") payload = payload.slice(1);
                if ((payload.startsWith('"') && payload.endsWith('"')) || (payload.startsWith("'") && payload.endsWith("'"))) {
                  payload = payload.slice(1, -1).trim();
                }
                const isDone = payload === "[DONE]" || payload === "DONE";
                if (isDone) {
                  try { onDone(); } catch (e) {}
                } else if (payload.startsWith("[ERROR]")) {
                  try { onError(new Error(payload)); } catch (e) {}
                } else {
                  try { onMessage(payload); } catch (e) {}
                }
              }
            }
          }
        }

        // Process remaining buffer
        if (buffer) {
          const lines = buffer.split(/\r?\n/);
          for (const line of lines) {
            if (line === "") continue;
            if (line.startsWith("data:")) {
              let payload = line.slice(5);
              if (payload.charAt(0) === " ") payload = payload.slice(1);
              if ((payload.startsWith('"') && payload.endsWith('"')) || (payload.startsWith("'") && payload.endsWith("'"))) {
                payload = payload.slice(1, -1);
              }
              const isDone = payload === "[DONE]" || payload === "DONE";
              if (isDone) onDone();
              else if (payload.startsWith("[ERROR]")) onError(new Error(payload));
              else onMessage(payload);
            }
          }
        }

        try { onDone(); } catch (e) {}
      } catch (err) {
        if (err.name === 'AbortError') return;
        try { onError(err); } catch (e) {}
      }
    })();

    return { cancel: () => controller.abort() };
  }

  streamLiveEvents({ videoId, onMetrics = () => {}, onAdvice = () => {}, onStreamUrl = () => {}, onStreamEnded = () => {}, onExtensionComments = () => {}, onExtensionProducts = () => {}, onExtensionActivities = () => {}, onExtensionTraffic = () => {}, onExtensionConnected = () => {}, onExtensionDisconnected = () => {}, onError = () => {} }) {
    const base = (this.client && this.client.defaults && this.client.defaults.baseURL) || import.meta.env.VITE_API_BASE_URL || "";
    const url = `${base.replace(/\/$/, "")}/api/v1/live/${encodeURIComponent(videoId)}/stream`;

    const controller = new AbortController();
    const signal = controller.signal;

    const MAX_RETRIES = 5;
    const RETRY_DELAY = 3000;
    let retryCount = 0;

    const connect = async () => {
      try {
        console.log(`LiveSSE: Connecting to live stream ${videoId}`);

        const headers = { Accept: "text/event-stream" };
        const token = TokenManager.getToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const resp = await fetch(url, {
          method: "GET",
          headers,
          credentials: "same-origin",
          signal,
        });

        if (!resp.ok) {
          throw new Error(`LiveSSE request failed: ${resp.status}`);
        }

        if (!resp.body) {
          throw new Error("LiveSSE response has no body");
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        retryCount = 0; // Reset on successful connection

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);

            const lines = raw.split(/\r?\n/);
            for (const line of lines) {
              if (!line.startsWith("data:")) continue;
              const payload = line.slice(5).trim();

              try {
                const data = JSON.parse(payload);
                const { event_type, payload: eventPayload } = data;

                switch (event_type) {
                  case 'metrics':
                    onMetrics(eventPayload);
                    break;
                  case 'advice':
                    onAdvice(eventPayload);
                    break;
                  case 'stream_url':
                    onStreamUrl(eventPayload);
                    break;
                  case 'stream_ended':
                    onStreamEnded(eventPayload);
                    return;
                  case 'extension_comments':
                    onExtensionComments(eventPayload);
                    break;
                  case 'extension_products':
                    onExtensionProducts(eventPayload);
                    break;
                  case 'extension_activities':
                    onExtensionActivities(eventPayload);
                    break;
                  case 'extension_traffic':
                    onExtensionTraffic(eventPayload);
                    break;
                  case 'extension_connected':
                    onExtensionConnected(eventPayload);
                    break;
                  case 'extension_disconnected':
                    onExtensionDisconnected(eventPayload);
                    break;
                  case 'extension_metrics':
                    onMetrics({ ...eventPayload, source: 'extension' });
                    break;
                  case 'extension_suggestions':
                    // Treat TikTok suggestions as advice
                    if (eventPayload.suggestions) {
                      eventPayload.suggestions.forEach(s => {
                        onAdvice({ message: s.text || s, urgency: 'low', source: 'tiktok_suggestion', timestamp: Date.now() / 1000 });
                      });
                    }
                    break;
                  case 'heartbeat':
                    break; // Ignore heartbeats
                  default:
                    console.log('LiveSSE: Unknown event type:', event_type);
                }
              } catch (parseErr) {
                console.error('LiveSSE parse error:', parseErr);
              }
            }
          }
        }
      } catch (err) {
        if (signal.aborted) return;
        console.error('LiveSSE error:', err);

        if (retryCount < MAX_RETRIES) {
          retryCount++;
          console.log(`LiveSSE: Retrying in ${RETRY_DELAY}ms (${retryCount}/${MAX_RETRIES})`);
          setTimeout(connect, RETRY_DELAY);
        } else {
          onError(err);
        }
      }
    };

    connect();

    return {
      close: () => {
        controller.abort();
      },
    };
  }

  // =========================================================
  // Sales Moments API
  // =========================================================

  /**
   * Get sales moments for a video.
   * @param {string} videoId
   * @returns {Promise<{sales_moments: Array, count: number}>}
   */
  async getSalesMoments(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/sales-moments`);
      return response || { sales_moments: [], count: 0 };
    } catch (error) {
      console.warn('[VideoService] getSalesMoments failed:', error);
      return { sales_moments: [], count: 0 };
    }
  }

  /**
   * Backfill sales moments for a video (trigger detection from trend_stats).
   * @param {string} videoId
   * @returns {Promise<{status: string, count: number, moments?: Array}>}
   */
  async backfillSalesMoments(videoId) {
    try {
      const response = await this.post(`/api/v1/videos/${videoId}/sales-moments/backfill`);
      return response || { status: 'error', count: 0 };
    } catch (error) {
      console.warn('[VideoService] backfillSalesMoments failed:', error);
      return { status: 'error', count: 0 };
    }
  }

  /**
   * Get AI event scores (sell-ability prediction) for each phase.
   * @param {string} videoId
   * @returns {Promise<Array<{phase_index: number, ai_score: number, score_source: string, rank: number}>>}
   */
  async getEventScores(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/event-scores`);
      return Array.isArray(response) ? response : [];
    } catch (error) {
      console.warn('[VideoService] getEventScores failed:', error);
      return [];
    }
  }

  // ── Live Report v1 ─────────────────────────────────────

  /**
   * Generate Live Report v1 for a video.
   * @param {string} videoId - Video ID
   * @returns {Promise} Report data
   */
  async generateLiveReport(videoId) {
    try {
      const response = await this.post(`${URL_CONSTANTS.REPORT_GENERATE}/${videoId}/generate`);
      return response;
    } catch (error) {
      console.error('Failed to generate live report:', error);
      throw error;
    }
  }

  /**
   * Get the latest Live Report for a video.
   * @param {string} videoId - Video ID
   * @returns {Promise} Report data
   */
  async getLiveReport(videoId) {
    try {
      const response = await this.get(`${URL_CONSTANTS.REPORT_GET}/${videoId}`);
      return response;
    } catch (error) {
      console.error('Failed to get live report:', error);
      throw error;
    }
  }

  /**
   * Get AI-powered sales clip candidates for a video.
   * Returns TOP3-5 clip candidates with sales_score and reasons.
   * @param {string} videoId
   * @param {number} topN - Number of candidates (1-10, default 5)
   * @returns {Promise<{candidates: Array, phase_scores: Array, total_phases: number, moments_count: number}>}
   */
  async getSalesClipCandidates(videoId, topN = 5) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/sales-clip-candidates?top_n=${topN}`);
      return response;
    } catch (error) {
      console.warn('[VideoService] getSalesClipCandidates failed:', error);
      return { candidates: [], phase_scores: [], total_phases: 0, moments_count: 0 };
    }
  }

  /**
   * Submit adopt/reject feedback for a clip candidate.
   * This data becomes the training signal for the Clip Rank AI model.
   * @param {string} videoId
   * @param {Object} payload - { phase_index, time_start, time_end, feedback, clip_id?, reviewer_name?, ai_score_at_feedback?, score_breakdown?, ai_reasons_at_feedback? }
   */
  async submitClipFeedback(videoId, payload) {
    try {
      const response = await this.post(`/api/v1/clips/${videoId}/feedback`, payload);
      return response;
    } catch (error) {
      console.warn('[VideoService] submitClipFeedback failed:', error);
      throw error;
    }
  }

  /**
   * Get existing feedback for a video (to restore UI state on load).
   * @param {string} videoId
   * @returns {Promise<Array>} - Array of feedback objects
   */
  async getClipFeedback(videoId) {
    try {
      const response = await this.get(`/api/v1/clips/${videoId}/feedback`);
      return Array.isArray(response) ? response : [];
    } catch (error) {
      console.warn('[VideoService] getClipFeedback failed:', error);
      return [];
    }
  }

  /**
   * Delete feedback for a specific phase (undo adopt/reject).
   * @param {string} videoId
   * @param {number} phaseIndex
   */
  async deleteClipFeedback(videoId, phaseIndex) {
    try {
      const response = await this.delete(`/api/v1/clips/${videoId}/feedback/${phaseIndex}`);
      return response;
    } catch (error) {
      console.warn('[VideoService] deleteClipFeedback failed:', error);
      throw error;
    }
  }

  /**
   * Trim clip with new start/end times.
   * @param {string} videoId
   * @param {string} clipId
   * @param {number} timeStart - New start time in seconds
   * @param {number} timeEnd - New end time in seconds
   * @param {number} [speedFactor=1.2]
   * @returns {Promise<{clip_id, status, message}>}
   */
  async trimClip(videoId, clipId, timeStart, timeEnd, speedFactor = 1.2) {
    try {
      const response = await this.patch(`/api/v1/videos/${videoId}/clips/${clipId}/trim`, {
        time_start: timeStart,
        time_end: timeEnd,
        speed_factor: speedFactor,
      });
      return response;
    } catch (error) {
      console.error('Failed to trim clip:', error);
      throw error;
    }
  }

  /**
   * Update clip captions.
   * @param {string} videoId
   * @param {string} clipId
   * @param {Array} captions - Array of {start, end, text, emphasis}
   * @returns {Promise<{clip_id, captions_count, message}>}
   */
  async updateClipCaptions(videoId, clipId, captions) {
    try {
      const response = await this.patch(`/api/v1/videos/${videoId}/clips/${clipId}/captions`, {
        captions,
      });
      return response;
    } catch (error) {
      console.error('Failed to update captions:', error);
      throw error;
    }
  }

  /**
   * Save subtitle feedback (vote + tags) for a clip.
   * @param {string} videoId
   * @param {string} clipId
   * @param {object} feedback - { style, vote, tags, position, ai_recommended_style }
   */
  async saveSubtitleFeedback(videoId, clipId, feedback) {
    try {
      const response = await this.post(`/api/v1/videos/${videoId}/clips/${clipId}/subtitle-feedback`, feedback);
      return response;
    } catch (error) {
      console.error('Failed to save subtitle feedback:', error);
      throw error;
    }
  }

  /**
   * Save subtitle style and position for a clip.
   * @param {string} videoId
   * @param {string} clipId
   * @param {object} styleData - { style, position_x, position_y }
   */
  async saveSubtitleStyle(videoId, clipId, styleData) {
    try {
      const response = await this.patch(`/api/v1/videos/${videoId}/clips/${clipId}/subtitle-style`, styleData);
      return response;
    } catch (error) {
      console.error('Failed to save subtitle style:', error);
      throw error;
    }
  }

  /**
   * Get AI-recommended subtitle style for a video.
   * @param {string} videoId
   * @returns {Promise<{video_id, recommendation, user_feedback_count}>}
   */
  async getSubtitleRecommendation(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/subtitle-recommend`);
      return response;
    } catch (error) {
      console.error('Failed to get subtitle recommendation:', error);
      throw error;
    }
  }

  /**
   * Get sales moment clips (spike-based clip candidates).
   * @param {string} videoId
   * @param {number} topN
   * @returns {Promise<{video_id, spike_count, candidates}>}
   */
  async getSalesMomentClips(videoId, topN = 5) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/sales-moment-clips?top_n=${topN}`);
      return response;
    } catch (error) {
      console.error('Failed to get sales moment clips:', error);
      throw error;
    }
  }

  /**
   * Detect hooks (strong opening phrases) in a video.
   * @param {string} videoId
   * @param {number} maxCandidates
   * @returns {Promise<{video_id, hook_count, hooks, placement_suggestion}>}
   */
  async getHookDetection(videoId, maxCandidates = 10) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/hook-detection?max_candidates=${maxCandidates}`);
      return response;
    } catch (error) {
      console.error('Failed to detect hooks:', error);
      throw error;
    }
  }

  // ─── Feedback Loop APIs ─────────────────────────────────────────────────

  /**
   * Submit clip rating (good/bad) with optional reason tags.
   * @param {string} videoId
   * @param {object} data - { phase_index, time_start, time_end, rating, reason_tags?, clip_id? }
   */
  async submitClipRating(videoId, data) {
    try {
      const response = await this.post(`/api/v1/feedback/${videoId}/clip-rating`, data);
      return response;
    } catch (error) {
      console.error('Failed to submit clip rating:', error);
      throw error;
    }
  }

  /**
   * Get all clip ratings for a video.
   * @param {string} videoId
   */
  async getClipRatings(videoId) {
    try {
      const response = await this.get(`/api/v1/feedback/${videoId}/clip-ratings`);
      return response;
    } catch (error) {
      console.error('Failed to get clip ratings:', error);
      throw error;
    }
  }

  /**
   * Log a clip edit (trim/caption change) for AI learning.
   * @param {string} videoId
   * @param {object} data - { clip_id, edit_type, before_value, after_value, delta_seconds? }
   */
  async logClipEdit(videoId, data) {
    try {
      const response = await this.post(`/api/v1/feedback/${videoId}/edit-log`, data);
      return response;
    } catch (error) {
      console.error('Failed to log clip edit:', error);
      throw error;
    }
  }

  /**
   * Submit sales confirmation (is this clip the selling moment?).
   * @param {string} videoId
   * @param {object} data - { phase_index, time_start, time_end, is_sales_moment, clip_id?, confidence?, note? }
   */
  async submitSalesConfirmation(videoId, data) {
    try {
      const response = await this.post(`/api/v1/feedback/${videoId}/sales-confirmation`, data);
      return response;
    } catch (error) {
      console.error('Failed to submit sales confirmation:', error);
      throw error;
    }
  }

  /**
   * Get all sales confirmations for a video.
   * @param {string} videoId
   */
  async getSalesConfirmations(videoId) {
    try {
      const response = await this.get(`/api/v1/feedback/${videoId}/sales-confirmations`);
      return response;
    } catch (error) {
      console.error('Failed to get sales confirmations:', error);
      throw error;
    }
  }

  /**
   * Get moment-based clips grouped by category.
   * @param {string} videoId
   * @returns {{ video_id, categories, total_moments, auto_zoom_data }}
   */
  async getMomentClips(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/moment-clips`);
      return response;
    } catch (error) {
      console.error('Failed to get moment clips:', error);
      throw error;
    }
  }

  // ─── Clip Editor v2 APIs ──────────────────────────────────────────────

  /**
   * Get timeline data (phases, markers, scores, feedback) in one call.
   * @param {string} videoId
   * @returns {Promise<{phases, markers, event_scores, feedback, phase_count}>}
   */
  async getTimelineData(videoId) {
    try {
      const response = await this.get(`/api/v1/editor/${videoId}/timeline`);
      return response;
    } catch (error) {
      console.error('Failed to get timeline data:', error);
      return { phases: [], markers: [], event_scores: [], feedback: [], phase_count: 0 };
    }
  }

  /**
   * Get segment-level AI scores for heatmap.
   * @param {string} videoId
   * @returns {Promise<{segments, count}>}
   */
  async getSegmentScores(videoId) {
    try {
      const response = await this.get(`/api/v1/editor/${videoId}/segments`);
      return response;
    } catch (error) {
      console.error('Failed to get segment scores:', error);
      return { segments: [], count: 0 };
    }
  }

  /**
   * Get video-level overall score.
   * @param {string} videoId
   * @returns {Promise<Object>}
   */
  async getVideoScore(videoId) {
    try {
      const response = await this.get(`/api/v1/editor/${videoId}/score`);
      return response;
    } catch (error) {
      console.error('Failed to get video score:', error);
      return { overall_score: null };
    }
  }

  /**
   * Submit segment feedback.
   * @param {string} videoId
   * @param {Object} data - { start_sec, end_sec, feedback_type, label?, note? }
   * @returns {Promise<Object>}
   */
  async submitSegmentFeedback(videoId, data) {
    try {
      const response = await this.post(`/api/v1/editor/${videoId}/segment-feedback`, data);
      return response;
    } catch (error) {
      console.error('Failed to submit segment feedback:', error);
      throw error;
    }
  }

  /**
   * Get all segment feedback for a video.
   * @param {string} videoId
   * @returns {Promise<{feedback, count}>}
   */
  async getSegmentFeedback(videoId) {
    try {
      const response = await this.get(`/api/v1/editor/${videoId}/segment-feedback`);
      return response;
    } catch (error) {
      console.error('Failed to get segment feedback:', error);
      return { feedback: [], count: 0 };
    }
  }

  /**
   * Retry analysis for a failed video without re-uploading.
   * The uploaded video asset is preserved in Blob storage.
   * @param {string} videoId - The video ID to retry analysis for
   * @returns {Promise<{success: boolean, video_id: string, message: string, new_status: string}>}
   */
  /**
   * On-demand Whisper transcription for a clip.
   * @param {string} videoId
   * @param {Object} data - { clip_url, time_start, time_end, phase_index? }
   * @returns {Promise<{segments, segment_count, source}>}
   */
  async transcribeClip(videoId, data) {
    try {
      // Whisper transcription can take 2-3 minutes (download + audio extraction + API)
      const response = await this.post(`/api/v1/editor/${videoId}/transcribe`, data, {
        timeout: 300000, // 5 minutes
      });
      return response;
    } catch (error) {
      console.error('Failed to transcribe clip:', error);
      throw error;
    }
  }

  /**
   * Export a subtitled clip (burns subtitles into MP4 via ffmpeg).
   * @param {string} videoId
   * @param {object} data - { clip_url, captions, style, position_x, position_y, time_start }
   * @returns {Promise<{video_id, download_url, style, caption_count, file_size}>}
   */
  async exportSubtitledClip(videoId, data, { onProgress } = {}) {
    try {
      // Step 1: Start the export job (Azure cold-start can take 30s+)
      const startRes = await this.post(`/api/v1/editor/${videoId}/export-subtitled`, data, {
        timeout: 120000, // 2 minutes for cold-start + initial processing
      });
      const jobId = startRes?.job_id || startRes?.data?.job_id;

      // Cache hit: server returns done + download_url immediately
      const startStatus = startRes?.status || startRes?.data?.status;
      if (startStatus === 'done') {
        if (onProgress) onProgress('done', 100);
        return {
          download_url: startRes?.download_url || startRes?.data?.download_url,
          file_size: startRes?.file_size || startRes?.data?.file_size,
          video_id: videoId,
        };
      }

      if (!jobId) {
        // Fallback: old API returned download_url directly
        return startRes;
      }

      // Step 2: Poll for completion with progressive intervals
      // First 15s: poll every 2s (fast feedback)
      // After 15s: poll every 4s (reduce server load)
      const maxAttempts = 180; // ~10 minutes max
      for (let i = 0; i < maxAttempts; i++) {
        const delay = i < 8 ? 2000 : 4000; // 2s for first 16s, then 4s
        await new Promise(r => setTimeout(r, delay));
        const status = await this.get(`/api/v1/editor/${videoId}/export-subtitled/${jobId}`, {
          timeout: 30000,
        });
        const st = status?.status || status?.data?.status;
        const pct = status?.progress_pct ?? status?.data?.progress_pct ?? 0;
        if (onProgress) onProgress(st, pct);

        if (st === 'done') {
          return {
            download_url: status?.download_url || status?.data?.download_url,
            file_size: status?.file_size || status?.data?.file_size,
            video_id: videoId,
          };
        } else if (st === 'failed') {
          throw new Error(status?.error || status?.data?.error || 'Export failed');
        }
        // else: queued, downloading, encoding, uploading - keep polling
      }
      throw new Error('Export timed out after 10 minutes');
    } catch (error) {
      console.error('Failed to export subtitled clip:', error);
      throw error;
    }
  }

  /**
   * Record a clip download event for ML training signals.
   * @param {string} videoId
   * @param {Object} data - { phase_index, time_start, time_end, clip_id?, export_type }
   */
  async recordClipDownload(videoId, data) {
    try {
      const response = await this.post(`/api/v1/feedback/${videoId}/clip-download`, data);
      return response;
    } catch (error) {
      // Non-blocking: don't let download tracking failure break the export flow
      console.warn('[recordClipDownload] Failed (non-blocking):', error.message);
      return null;
    }
  }

  async retryAnalysis(videoId) {
    try {
      const response = await this.post(`/api/v1/videos/${videoId}/retry-analysis`);
      return response;
    } catch (error) {
      console.error('Failed to retry analysis:', error);
      throw error;
    }
  }

  /**
   * Fetch error log history for a video.
   * @param {string} videoId
   * @returns {Promise<{video_id: string, error_logs: Array, total: number}>}
   */
  async getErrorLogs(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/error-logs`);
      return response;
    } catch (error) {
      console.error('Failed to fetch error logs:', error);
      throw error;
    }
  }

  /**
   * Get winning patterns (CTA phrases, product durations, top phases)
   * extracted from real performance data.
   * @param {string} videoId
   * @returns {Promise<Object>}
   */
  async getWinningPatterns(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/winning-patterns`);
      return response;
    } catch (error) {
      console.error('Failed to fetch winning patterns:', error);
      throw error;
    }
  }

  /**
   * Generate a data-driven live commerce script based on real performance data.
   * @param {string} videoId
   * @param {Object} options
   * @returns {Promise<Object>}
   */
  async generateScript(videoId, options = {}) {
    try {
      const response = await this.post(`/api/v1/videos/${videoId}/generate-script`, {
        product_focus: options.product_focus || null,
        tone: options.tone || 'professional_friendly',
        language: options.language || 'ja',
        duration_minutes: options.duration_minutes || 10,
        cross_video: options.cross_video !== false,
      });
      return response;
    } catch (error) {
      console.error('Failed to generate script:', error);
      throw error;
    }
  }
}
export default new VideoService();
