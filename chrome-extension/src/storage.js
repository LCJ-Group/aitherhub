/**
 * AitherHub - Local Storage Manager
 * 
 * Handles persistent local storage for:
 * - Session state persistence across service worker restarts
 * - Event queue backup when API is unreachable
 * - Snapshot cache for diff calculations
 * - Configuration storage
 * 
 * Uses chrome.storage.local (5MB limit) with structured keys.
 */

const STORAGE_KEYS = {
  // Session
  SESSION_ID: 'ext_session_id',
  SESSION_STATUS: 'ext_session_status',
  SESSION_DATA: 'ext_session_data',

  // Event queue backup
  EVENT_QUEUE: 'ext_event_queue',
  EVENT_QUEUE_SIZE: 'ext_event_queue_size',

  // Snapshot cache (for diff calculation)
  LAST_PRODUCT_SNAPSHOT: 'ext_last_product_snapshot',
  LAST_KPI_SNAPSHOT: 'ext_last_kpi_snapshot',
  LAST_TREND_SNAPSHOT: 'ext_last_trend_snapshot',

  // Auth
  ACCESS_TOKEN: 'accessToken',
  REFRESH_TOKEN: 'refreshToken',
  API_BASE: 'apiBase',

  // Config
  TRACKING_ENABLED: 'ext_tracking_enabled',
  DEBUG_MODE: 'ext_debug_mode',
};

const AitherStorage = {
  /**
   * Get a value from storage
   * @param {string} key 
   * @returns {Promise<any>}
   */
  async get(key) {
    return new Promise((resolve) => {
      chrome.storage.local.get([key], (result) => {
        resolve(result[key] ?? null);
      });
    });
  },

  /**
   * Get multiple values from storage
   * @param {string[]} keys
   * @returns {Promise<Object>}
   */
  async getMultiple(keys) {
    return new Promise((resolve) => {
      chrome.storage.local.get(keys, (result) => {
        resolve(result);
      });
    });
  },

  /**
   * Set a value in storage
   * @param {string} key
   * @param {any} value
   * @returns {Promise<void>}
   */
  async set(key, value) {
    return new Promise((resolve) => {
      chrome.storage.local.set({ [key]: value }, resolve);
    });
  },

  /**
   * Set multiple values in storage
   * @param {Object} items
   * @returns {Promise<void>}
   */
  async setMultiple(items) {
    return new Promise((resolve) => {
      chrome.storage.local.set(items, resolve);
    });
  },

  /**
   * Remove a key from storage
   * @param {string|string[]} keys
   * @returns {Promise<void>}
   */
  async remove(keys) {
    return new Promise((resolve) => {
      chrome.storage.local.remove(
        Array.isArray(keys) ? keys : [keys],
        resolve
      );
    });
  },

  // ══════════════════════════════════════════════════════════════
  // Session helpers
  // ══════════════════════════════════════════════════════════════

  async saveSession(sessionId, sessionData) {
    await this.setMultiple({
      [STORAGE_KEYS.SESSION_ID]: sessionId,
      [STORAGE_KEYS.SESSION_STATUS]: sessionData.status || 'active',
      [STORAGE_KEYS.SESSION_DATA]: sessionData,
    });
  },

  async getSession() {
    const data = await this.getMultiple([
      STORAGE_KEYS.SESSION_ID,
      STORAGE_KEYS.SESSION_STATUS,
      STORAGE_KEYS.SESSION_DATA,
    ]);
    if (!data[STORAGE_KEYS.SESSION_ID]) return null;
    return {
      id: data[STORAGE_KEYS.SESSION_ID],
      status: data[STORAGE_KEYS.SESSION_STATUS],
      ...data[STORAGE_KEYS.SESSION_DATA],
    };
  },

  async clearSession() {
    await this.remove([
      STORAGE_KEYS.SESSION_ID,
      STORAGE_KEYS.SESSION_STATUS,
      STORAGE_KEYS.SESSION_DATA,
      STORAGE_KEYS.LAST_PRODUCT_SNAPSHOT,
      STORAGE_KEYS.LAST_KPI_SNAPSHOT,
      STORAGE_KEYS.LAST_TREND_SNAPSHOT,
    ]);
  },

  // ══════════════════════════════════════════════════════════════
  // Event queue backup (for offline resilience)
  // ══════════════════════════════════════════════════════════════

  /**
   * Save events to backup queue when API is unreachable.
   * Appends to existing queue.
   * @param {Object[]} events
   */
  async backupEvents(events) {
    const existing = (await this.get(STORAGE_KEYS.EVENT_QUEUE)) || [];
    const merged = [...existing, ...events];

    // Cap at 500 events to avoid storage overflow
    const capped = merged.slice(-500);

    await this.setMultiple({
      [STORAGE_KEYS.EVENT_QUEUE]: capped,
      [STORAGE_KEYS.EVENT_QUEUE_SIZE]: capped.length,
    });

    return capped.length;
  },

  /**
   * Get and clear the backup event queue.
   * @returns {Promise<Object[]>}
   */
  async drainBackupQueue() {
    const events = (await this.get(STORAGE_KEYS.EVENT_QUEUE)) || [];
    if (events.length > 0) {
      await this.setMultiple({
        [STORAGE_KEYS.EVENT_QUEUE]: [],
        [STORAGE_KEYS.EVENT_QUEUE_SIZE]: 0,
      });
    }
    return events;
  },

  async getBackupQueueSize() {
    return (await this.get(STORAGE_KEYS.EVENT_QUEUE_SIZE)) || 0;
  },

  // ══════════════════════════════════════════════════════════════
  // Snapshot cache (for diff calculation)
  // ══════════════════════════════════════════════════════════════

  /**
   * Save the latest product snapshot for diff calculation.
   * @param {Object[]} products - Array of product data
   */
  async saveProductSnapshot(products) {
    await this.set(STORAGE_KEYS.LAST_PRODUCT_SNAPSHOT, {
      products,
      timestamp: Date.now(),
    });
  },

  /**
   * Get the last product snapshot.
   * @returns {Promise<{products: Object[], timestamp: number}|null>}
   */
  async getLastProductSnapshot() {
    return await this.get(STORAGE_KEYS.LAST_PRODUCT_SNAPSHOT);
  },

  /**
   * Save the latest KPI snapshot.
   * @param {Object} kpi
   */
  async saveKpiSnapshot(kpi) {
    await this.set(STORAGE_KEYS.LAST_KPI_SNAPSHOT, {
      kpi,
      timestamp: Date.now(),
    });
  },

  async getLastKpiSnapshot() {
    return await this.get(STORAGE_KEYS.LAST_KPI_SNAPSHOT);
  },

  // ══════════════════════════════════════════════════════════════
  // Auth helpers
  // ══════════════════════════════════════════════════════════════

  async getAuth() {
    return this.getMultiple([
      STORAGE_KEYS.ACCESS_TOKEN,
      STORAGE_KEYS.REFRESH_TOKEN,
      STORAGE_KEYS.API_BASE,
    ]);
  },

  async saveAuth(accessToken, refreshToken) {
    await this.setMultiple({
      [STORAGE_KEYS.ACCESS_TOKEN]: accessToken,
      [STORAGE_KEYS.REFRESH_TOKEN]: refreshToken,
    });
  },
};

// Export
if (typeof globalThis !== 'undefined') {
  globalThis.AitherStorage = AitherStorage;
  globalThis.STORAGE_KEYS = STORAGE_KEYS;
}
