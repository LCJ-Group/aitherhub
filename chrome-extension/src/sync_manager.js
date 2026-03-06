/**
 * AitherHub - Sync Manager
 * 
 * Manages time synchronization between LIVE page and Dashboard page.
 * 
 * Key design decisions:
 * - LIVE events: second-level granularity (video_sec)
 * - Dashboard events: 5-minute bucket granularity
 * - Moment matching: ±150 second window
 * - Three timestamps per event: captured_at_client, captured_at_server, video_sec
 * 
 * Sync strategy (MVP):
 * - Use client timestamps as primary (both tabs on same machine)
 * - Server timestamps for drift detection
 * - sync_offset_ms for manual correction if needed
 */

const SyncManager = {
  _log(...args) {
    console.log('[AitherHub SyncManager]', ...args);
  },

  // ══════════════════════════════════════════════════════════════
  // State
  // ══════════════════════════════════════════════════════════════
  _sessionStartTime: null,
  _liveStartVideoSec: null,
  _syncOffsetMs: 0,
  _serverClockDrift: 0,
  _lastLiveTimestamp: null,
  _lastDashboardTimestamp: null,

  // ══════════════════════════════════════════════════════════════
  // Initialization
  // ══════════════════════════════════════════════════════════════

  /**
   * Initialize sync for a new session.
   * @param {string} sessionStartTime - ISO timestamp of session start
   * @param {number} syncOffsetMs - Manual offset correction (default 0)
   */
  init(sessionStartTime, syncOffsetMs = 0) {
    this._sessionStartTime = new Date(sessionStartTime).getTime();
    this._syncOffsetMs = syncOffsetMs;
    this._serverClockDrift = 0;
    this._lastLiveTimestamp = null;
    this._lastDashboardTimestamp = null;
    this._liveStartVideoSec = null;
    this._log('Initialized. Session start:', sessionStartTime, 'Offset:', syncOffsetMs, 'ms');
  },

  /**
   * Set the video_sec at the moment tracking started.
   * Used to map video_sec back to wall-clock time.
   * @param {number} videoSec
   */
  setLiveStartVideoSec(videoSec) {
    this._liveStartVideoSec = videoSec;
    this._log('Live start video_sec:', videoSec);
  },

  // ══════════════════════════════════════════════════════════════
  // Timestamp Enrichment
  // ══════════════════════════════════════════════════════════════

  /**
   * Enrich an event with synchronized timestamps.
   * @param {Object} event - Raw event object
   * @param {string} source - 'live' or 'dashboard'
   * @returns {Object} - Event with enriched timestamps
   */
  enrichEvent(event, source) {
    const clientTime = event.captured_at || new Date().toISOString();
    const clientMs = new Date(clientTime).getTime();

    // Apply sync offset
    const adjustedMs = clientMs + this._syncOffsetMs;

    // Track last timestamps per source
    if (source === 'live') {
      this._lastLiveTimestamp = adjustedMs;
    } else if (source === 'dashboard') {
      this._lastDashboardTimestamp = adjustedMs;
    }

    return {
      ...event,
      captured_at_client: new Date(adjustedMs).toISOString(),
      _sync_meta: {
        source,
        raw_client_time: clientTime,
        sync_offset_ms: this._syncOffsetMs,
        video_sec: event.video_sec || null,
      },
    };
  },

  // ══════════════════════════════════════════════════════════════
  // Time Conversion
  // ══════════════════════════════════════════════════════════════

  /**
   * Convert video_sec to estimated wall-clock time.
   * @param {number} videoSec
   * @returns {string|null} ISO timestamp
   */
  videoSecToWallClock(videoSec) {
    if (this._sessionStartTime === null || this._liveStartVideoSec === null) {
      return null;
    }

    // Wall clock = session_start + (videoSec - liveStartVideoSec) * 1000
    const wallMs = this._sessionStartTime +
      (videoSec - this._liveStartVideoSec) * 1000 +
      this._syncOffsetMs;

    return new Date(wallMs).toISOString();
  },

  /**
   * Convert wall-clock time to estimated video_sec.
   * @param {string} isoTimestamp
   * @returns {number|null}
   */
  wallClockToVideoSec(isoTimestamp) {
    if (this._sessionStartTime === null || this._liveStartVideoSec === null) {
      return null;
    }

    const wallMs = new Date(isoTimestamp).getTime();
    const deltaMs = wallMs - this._sessionStartTime - this._syncOffsetMs;
    return this._liveStartVideoSec + Math.round(deltaMs / 1000);
  },

  // ══════════════════════════════════════════════════════════════
  // Moment Matching Window
  // ══════════════════════════════════════════════════════════════

  /**
   * Check if two events are within the matching window.
   * @param {Object} eventA - Event with captured_at_client
   * @param {Object} eventB - Event with captured_at_client
   * @param {number} windowMs - Window size in ms (default 150000 = 150s)
   * @returns {boolean}
   */
  isWithinWindow(eventA, eventB, windowMs = 150000) {
    const timeA = new Date(eventA.captured_at_client || eventA.captured_at).getTime();
    const timeB = new Date(eventB.captured_at_client || eventB.captured_at).getTime();
    return Math.abs(timeA - timeB) <= windowMs;
  },

  /**
   * Get the time distance between two events in milliseconds.
   * @param {Object} eventA
   * @param {Object} eventB
   * @returns {number}
   */
  getTimeDistance(eventA, eventB) {
    const timeA = new Date(eventA.captured_at_client || eventA.captured_at).getTime();
    const timeB = new Date(eventB.captured_at_client || eventB.captured_at).getTime();
    return timeA - timeB;
  },

  // ══════════════════════════════════════════════════════════════
  // Server Clock Drift Detection
  // ══════════════════════════════════════════════════════════════

  /**
   * Update server clock drift estimate from API response.
   * @param {number} clientSendMs - Client time when request was sent
   * @param {number} serverTimeMs - Server time from response header
   * @param {number} clientReceiveMs - Client time when response was received
   */
  updateServerDrift(clientSendMs, serverTimeMs, clientReceiveMs) {
    const rtt = clientReceiveMs - clientSendMs;
    const estimatedServerTime = clientSendMs + rtt / 2;
    const drift = serverTimeMs - estimatedServerTime;

    // Exponential moving average
    this._serverClockDrift = this._serverClockDrift * 0.7 + drift * 0.3;
    this._log('Server clock drift:', Math.round(this._serverClockDrift), 'ms');
  },

  // ══════════════════════════════════════════════════════════════
  // Sync Health
  // ══════════════════════════════════════════════════════════════

  /**
   * Get sync health status.
   * @returns {Object}
   */
  getHealth() {
    const now = Date.now();
    const liveAge = this._lastLiveTimestamp ? now - this._lastLiveTimestamp : null;
    const dashAge = this._lastDashboardTimestamp ? now - this._lastDashboardTimestamp : null;

    // Gap between live and dashboard last events
    let crossGap = null;
    if (this._lastLiveTimestamp && this._lastDashboardTimestamp) {
      crossGap = Math.abs(this._lastLiveTimestamp - this._lastDashboardTimestamp);
    }

    return {
      live_connected: liveAge !== null && liveAge < 30000,
      dashboard_connected: dashAge !== null && dashAge < 30000,
      live_last_event_age_ms: liveAge,
      dashboard_last_event_age_ms: dashAge,
      cross_source_gap_ms: crossGap,
      sync_offset_ms: this._syncOffsetMs,
      server_drift_ms: Math.round(this._serverClockDrift),
      healthy: (liveAge !== null && liveAge < 30000) &&
               (dashAge !== null && dashAge < 30000),
    };
  },

  /**
   * Adjust sync offset manually.
   * @param {number} offsetMs
   */
  setSyncOffset(offsetMs) {
    this._syncOffsetMs = offsetMs;
    this._log('Sync offset updated to:', offsetMs, 'ms');
  },

  /**
   * Reset all state.
   */
  reset() {
    this._sessionStartTime = null;
    this._liveStartVideoSec = null;
    this._syncOffsetMs = 0;
    this._serverClockDrift = 0;
    this._lastLiveTimestamp = null;
    this._lastDashboardTimestamp = null;
  },
};

// Export for background.js
if (typeof globalThis !== 'undefined') {
  globalThis.SyncManager = SyncManager;
}
