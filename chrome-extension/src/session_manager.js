/**
 * AitherHub - Extension Session Manager
 * 
 * Manages the lifecycle of a tracking session:
 * - Session creation when tracking starts
 * - Tab binding (live + dashboard)
 * - Status transitions: waiting_live → waiting_dashboard → active → ended
 * - Session persistence across service worker restarts
 * - Heartbeat monitoring
 * 
 * Session is created when:
 * - User clicks "Start Tracking" in popup
 * - Both LIVE and Dashboard tabs are detected
 * 
 * Session ends when:
 * - User clicks "Stop Tracking"
 * - Both tabs are closed
 * - Live stream ends
 */

const HEARTBEAT_INTERVAL_MS = 15000; // 15 seconds

const SessionManager = {
  _session: null,
  _heartbeatTimer: null,
  _tabStates: {
    live: { tabId: null, url: null, connected: false, lastPing: null },
    dashboard: { tabId: null, url: null, connected: false, lastPing: null },
  },

  /**
   * Initialize session manager - restore from storage if available.
   */
  async init() {
    if (typeof AitherStorage !== 'undefined') {
      const saved = await AitherStorage.getSession();
      if (saved && saved.status !== 'ended') {
        this._session = saved;
        console.log('[AitherHub Session] Restored session:', saved.id, 'status:', saved.status);
      }
    }
  },

  /**
   * Start a new tracking session.
   * @param {Object} params - { platform, creator_id, live_url, dashboard_url, live_tab_id, dashboard_tab_id }
   * @returns {Promise<{session_id: string, status: string}>}
   */
  async startSession(params = {}) {
    try {
      const result = await ExtApiClient.startSession({
        platform: params.platform || 'tiktok_live',
        creator_id: params.creator_id || null,
        live_url: params.live_url || null,
        dashboard_url: params.dashboard_url || null,
        live_tab_id: params.live_tab_id || null,
        dashboard_tab_id: params.dashboard_tab_id || null,
        live_title: params.live_title || null,
        room_id: params.room_id || null,
      });

      this._session = {
        id: result.session_id,
        status: result.status,
        platform: params.platform || 'tiktok_live',
        creator_id: params.creator_id,
        startedAt: new Date().toISOString(),
      };

      // Update tab states
      if (params.live_tab_id) {
        this._tabStates.live = {
          tabId: params.live_tab_id,
          url: params.live_url,
          connected: true,
          lastPing: Date.now(),
        };
      }
      if (params.dashboard_tab_id) {
        this._tabStates.dashboard = {
          tabId: params.dashboard_tab_id,
          url: params.dashboard_url,
          connected: true,
          lastPing: Date.now(),
        };
      }

      // Persist
      if (typeof AitherStorage !== 'undefined') {
        await AitherStorage.saveSession(this._session.id, this._session);
      }

      // Start event buffer
      if (typeof EventBuffer !== 'undefined') {
        EventBuffer.start(this._session.id);
      }

      // Start heartbeat
      this._startHeartbeat();

      console.log('[AitherHub Session] Started:', this._session.id, 'status:', this._session.status);
      return result;
    } catch (err) {
      console.error('[AitherHub Session] Failed to start:', err);
      throw err;
    }
  },

  /**
   * Bind a tab to the current session.
   * @param {string} tabType - 'live' or 'dashboard'
   * @param {number} tabId
   * @param {string} url
   */
  async bindTab(tabType, tabId, url) {
    if (!this._session) {
      console.warn('[AitherHub Session] No active session to bind tab');
      return;
    }

    try {
      const result = await ExtApiClient.bindTab(this._session.id, tabType, tabId, url);

      this._tabStates[tabType] = {
        tabId,
        url,
        connected: true,
        lastPing: Date.now(),
      };

      this._session.status = result.status;

      if (typeof AitherStorage !== 'undefined') {
        await AitherStorage.saveSession(this._session.id, this._session);
      }

      console.log('[AitherHub Session] Tab bound:', tabType, 'status:', result.status);
      return result;
    } catch (err) {
      console.error('[AitherHub Session] Failed to bind tab:', err);
    }
  },

  /**
   * End the current session.
   * @returns {Promise<Object>} - Session end summary
   */
  async endSession() {
    if (!this._session) return null;

    // Stop heartbeat
    this._stopHeartbeat();

    // Stop event buffer (final flush)
    if (typeof EventBuffer !== 'undefined') {
      await EventBuffer.stop();
    }

    try {
      const result = await ExtApiClient.endSession(this._session.id);
      this._session.status = 'ended';
      this._session.endedAt = new Date().toISOString();

      // Clear storage
      if (typeof AitherStorage !== 'undefined') {
        await AitherStorage.clearSession();
      }

      console.log('[AitherHub Session] Ended:', this._session.id, result);

      const endedSession = { ...this._session };
      this._session = null;
      this._tabStates = {
        live: { tabId: null, url: null, connected: false, lastPing: null },
        dashboard: { tabId: null, url: null, connected: false, lastPing: null },
      };

      return { ...result, session: endedSession };
    } catch (err) {
      console.error('[AitherHub Session] Failed to end:', err);
      // Force local cleanup even if API fails
      this._session = null;
      if (typeof AitherStorage !== 'undefined') {
        await AitherStorage.clearSession();
      }
      return null;
    }
  },

  /**
   * Handle tab ping from content script (keep-alive).
   * @param {string} tabType
   * @param {number} tabId
   */
  tabPing(tabType, tabId) {
    if (this._tabStates[tabType]) {
      this._tabStates[tabType].lastPing = Date.now();
      this._tabStates[tabType].connected = true;
    }
  },

  /**
   * Handle tab close.
   * @param {number} tabId
   */
  async onTabClosed(tabId) {
    let closedType = null;

    if (this._tabStates.live.tabId === tabId) {
      this._tabStates.live.connected = false;
      closedType = 'live';
    }
    if (this._tabStates.dashboard.tabId === tabId) {
      this._tabStates.dashboard.connected = false;
      closedType = 'dashboard';
    }

    if (closedType) {
      console.log('[AitherHub Session] Tab closed:', closedType, tabId);

      // If both tabs are closed, end session
      if (!this._tabStates.live.connected && !this._tabStates.dashboard.connected) {
        console.log('[AitherHub Session] Both tabs closed, ending session');
        await this.endSession();
      }
    }
  },

  /**
   * Get current session state.
   * @returns {Object|null}
   */
  getState() {
    if (!this._session) return null;

    return {
      session: { ...this._session },
      tabs: {
        live: { ...this._tabStates.live },
        dashboard: { ...this._tabStates.dashboard },
      },
      bufferStats: typeof EventBuffer !== 'undefined' ? EventBuffer.getStats() : null,
    };
  },

  /**
   * Check if session is active.
   * @returns {boolean}
   */
  isActive() {
    return this._session && this._session.status === 'active';
  },

  /**
   * Check if any session exists (including waiting states).
   * @returns {boolean}
   */
  hasSession() {
    return !!this._session && this._session.status !== 'ended';
  },

  /**
   * Get session ID.
   * @returns {string|null}
   */
  getSessionId() {
    return this._session ? this._session.id : null;
  },

  // ══════════════════════════════════════════════════════════════
  // Private
  // ══════════════════════════════════════════════════════════════

  _startHeartbeat() {
    this._stopHeartbeat();
    this._heartbeatTimer = setInterval(() => {
      if (this._session && typeof EventBuffer !== 'undefined') {
        EventBuffer.addEvent({
          event_type: 'heartbeat',
          source_type: 'system',
          payload: {
            live_connected: this._tabStates.live.connected,
            dashboard_connected: this._tabStates.dashboard.connected,
            session_status: this._session.status,
          },
        });
      }
    }, HEARTBEAT_INTERVAL_MS);
  },

  _stopHeartbeat() {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = null;
    }
  },
};

// Export
if (typeof globalThis !== 'undefined') {
  globalThis.SessionManager = SessionManager;
}
