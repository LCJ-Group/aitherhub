/**
 * AitherHub - API Client for Extension Events
 * 
 * Handles communication with the /api/v1/ext/* endpoints.
 * Features:
 * - Automatic token refresh on 401
 * - Retry with exponential backoff
 * - Fallback to local storage on failure
 * - Batch event submission
 */

const DEFAULT_API_BASE = 'https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net';
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

const ExtApiClient = {
  _apiBase: DEFAULT_API_BASE,
  _token: '',
  _refreshToken: '',
  _initialized: false,

  /**
   * Initialize the API client from storage.
   */
  async init() {
    if (this._initialized) return;
    const data = await chrome.storage.local.get([
      'apiBase', 'accessToken', 'apiToken', 'refreshToken'
    ]);
    this._apiBase = data.apiBase || DEFAULT_API_BASE;
    this._token = data.accessToken || data.apiToken || '';
    this._refreshToken = data.refreshToken || '';
    this._initialized = true;

    // Listen for token updates
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      if (changes.accessToken) this._token = changes.accessToken.newValue || '';
      if (changes.apiToken && !changes.accessToken) this._token = changes.apiToken.newValue || '';
      if (changes.refreshToken) this._refreshToken = changes.refreshToken.newValue || '';
      if (changes.apiBase) this._apiBase = changes.apiBase.newValue || DEFAULT_API_BASE;
    });
  },

  /**
   * Core fetch with auth headers.
   * @param {string} endpoint - API path (e.g., '/api/v1/ext/events')
   * @param {Object} options - fetch options
   * @returns {Promise<Response>}
   */
  async _fetch(endpoint, options = {}) {
    await this.init();

    const url = `${this._apiBase}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };

    if (this._token) {
      headers['Authorization'] = `Bearer ${this._token}`;
    }

    return fetch(url, {
      ...options,
      headers,
    });
  },

  /**
   * POST with retry and token refresh.
   * @param {string} endpoint
   * @param {Object} body
   * @returns {Promise<Object>} - Parsed JSON response
   */
  async post(endpoint, body) {
    let lastError;

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      try {
        const response = await this._fetch(endpoint, {
          method: 'POST',
          body: JSON.stringify(body),
        });

        if (response.ok) {
          return await response.json();
        }

        if (response.status === 401 && attempt === 0) {
          // Try token refresh
          const refreshed = await this._tryRefreshToken();
          if (refreshed) continue;
        }

        lastError = new Error(`API ${response.status}: ${response.statusText}`);
        lastError.status = response.status;

        // Don't retry on 4xx (except 401 handled above)
        if (response.status >= 400 && response.status < 500) {
          throw lastError;
        }
      } catch (err) {
        lastError = err;
        if (err.status && err.status >= 400 && err.status < 500) {
          throw err;
        }
      }

      // Wait before retry (exponential backoff)
      if (attempt < MAX_RETRIES - 1) {
        await new Promise(r => setTimeout(r, RETRY_DELAY_MS * Math.pow(2, attempt)));
      }
    }

    throw lastError || new Error('API request failed after retries');
  },

  /**
   * PATCH with retry.
   * @param {string} endpoint
   * @param {Object} body
   * @returns {Promise<Object>}
   */
  async patch(endpoint, body) {
    let lastError;

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      try {
        const response = await this._fetch(endpoint, {
          method: 'PATCH',
          body: JSON.stringify(body),
        });

        if (response.ok) {
          return await response.json();
        }

        if (response.status === 401 && attempt === 0) {
          const refreshed = await this._tryRefreshToken();
          if (refreshed) continue;
        }

        lastError = new Error(`API ${response.status}: ${response.statusText}`);
        lastError.status = response.status;

        if (response.status >= 400 && response.status < 500) throw lastError;
      } catch (err) {
        lastError = err;
        if (err.status && err.status >= 400 && err.status < 500) throw err;
      }

      if (attempt < MAX_RETRIES - 1) {
        await new Promise(r => setTimeout(r, RETRY_DELAY_MS * Math.pow(2, attempt)));
      }
    }

    throw lastError || new Error('API request failed after retries');
  },

  /**
   * GET with retry.
   * @param {string} endpoint
   * @returns {Promise<Object>}
   */
  async get(endpoint) {
    let lastError;

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      try {
        const response = await this._fetch(endpoint, { method: 'GET' });

        if (response.ok) {
          return await response.json();
        }

        if (response.status === 401 && attempt === 0) {
          const refreshed = await this._tryRefreshToken();
          if (refreshed) continue;
        }

        lastError = new Error(`API ${response.status}: ${response.statusText}`);
        lastError.status = response.status;

        if (response.status >= 400 && response.status < 500) throw lastError;
      } catch (err) {
        lastError = err;
        if (err.status && err.status >= 400 && err.status < 500) throw err;
      }

      if (attempt < MAX_RETRIES - 1) {
        await new Promise(r => setTimeout(r, RETRY_DELAY_MS * Math.pow(2, attempt)));
      }
    }

    throw lastError || new Error('API request failed after retries');
  },

  /**
   * Try to refresh the access token.
   * @returns {Promise<boolean>}
   */
  async _tryRefreshToken() {
    if (!this._refreshToken) return false;

    try {
      const response = await fetch(`${this._apiBase}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: this._refreshToken }),
      });

      if (!response.ok) {
        this._token = '';
        this._refreshToken = '';
        await chrome.storage.local.remove(['accessToken', 'apiToken', 'refreshToken']);
        return false;
      }

      const data = await response.json();
      this._token = data.access_token;
      this._refreshToken = data.refresh_token || this._refreshToken;

      await chrome.storage.local.set({
        accessToken: this._token,
        apiToken: this._token,
        refreshToken: this._refreshToken,
      });

      return true;
    } catch (err) {
      console.error('[AitherHub API] Token refresh error:', err);
      return false;
    }
  },

  // ══════════════════════════════════════════════════════════════
  // High-level API methods for Extension Events
  // ══════════════════════════════════════════════════════════════

  /**
   * Start a new extension session.
   * @param {Object} params
   * @returns {Promise<{session_id: string, status: string}>}
   */
  async startSession(params) {
    return this.post('/api/v1/ext/session/start', params);
  },

  /**
   * Bind a tab to the session.
   * @param {string} sessionId
   * @param {string} tabType - 'live' or 'dashboard'
   * @param {number} tabId
   * @param {string} url
   * @returns {Promise<Object>}
   */
  async bindTab(sessionId, tabType, tabId, url) {
    return this.patch(`/api/v1/ext/session/${sessionId}/bind`, {
      tab_type: tabType,
      tab_id: tabId,
      url: url,
    });
  },

  /**
   * End a session.
   * @param {string} sessionId
   * @returns {Promise<Object>}
   */
  async endSession(sessionId) {
    return this.post(`/api/v1/ext/session/${sessionId}/end`, {});
  },

  /**
   * Send a batch of raw events.
   * @param {string} sessionId
   * @param {Object[]} events
   * @returns {Promise<{inserted: number}>}
   */
  async sendEvents(sessionId, events) {
    return this.post('/api/v1/ext/events', {
      session_id: sessionId,
      events: events,
    });
  },

  /**
   * Send product snapshots.
   * @param {string} sessionId
   * @param {string} capturedAt - ISO 8601
   * @param {Object[]} products
   * @param {number} snapshotSeq
   * @returns {Promise<Object>}
   */
  async sendProductSnapshots(sessionId, capturedAt, products, snapshotSeq) {
    return this.post('/api/v1/ext/snapshots/products', {
      session_id: sessionId,
      captured_at: capturedAt,
      products: products,
      snapshot_seq: snapshotSeq,
    });
  },

  /**
   * Send trend snapshots.
   * @param {string} sessionId
   * @param {string} capturedAt
   * @param {Object[]} trends
   * @returns {Promise<Object>}
   */
  async sendTrendSnapshots(sessionId, capturedAt, trends) {
    return this.post('/api/v1/ext/snapshots/trends', {
      session_id: sessionId,
      captured_at: capturedAt,
      trends: trends,
    });
  },

  /**
   * Add a manual marker.
   * @param {string} sessionId
   * @param {string} label
   * @param {number} videoSec
   * @returns {Promise<Object>}
   */
  async addMarker(sessionId, label, videoSec) {
    return this.post('/api/v1/ext/marker', {
      session_id: sessionId,
      label: label,
      video_sec: videoSec,
    });
  },

  /**
   * Get session summary.
   * @param {string} sessionId
   * @returns {Promise<Object>}
   */
  async getSessionSummary(sessionId) {
    return this.get(`/api/v1/ext/session/${sessionId}/summary`);
  },

  /**
   * Health check.
   * @returns {Promise<Object>}
   */
  async healthCheck() {
    return this.get('/api/v1/ext/health');
  },
};

// Export
if (typeof globalThis !== 'undefined') {
  globalThis.ExtApiClient = ExtApiClient;
}
