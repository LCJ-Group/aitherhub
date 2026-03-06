/**
 * AitherHub - Event Buffer
 * 
 * Buffers raw events from content scripts and flushes to the API
 * at regular intervals (3-5 seconds). On failure, backs up to local storage.
 * 
 * Design:
 * - Events are collected in memory buffer
 * - Flushed every FLUSH_INTERVAL_MS to API
 * - On API failure, events are saved to chrome.storage.local
 * - On next successful flush, backed-up events are drained and sent first
 * - Product snapshots and trend snapshots have separate buffers
 */

const FLUSH_INTERVAL_MS = 4000; // 4 seconds
const MAX_BUFFER_SIZE = 200;    // Max events before force flush
const SNAPSHOT_INTERVAL_MS = 30000; // Product snapshot every 30s

const EventBuffer = {
  _eventBuffer: [],
  _productSnapshotBuffer: null,
  _trendSnapshotBuffer: null,
  _flushTimer: null,
  _sessionId: null,
  _snapshotSeq: 0,
  _isRunning: false,
  _stats: {
    eventsSent: 0,
    eventsDropped: 0,
    flushCount: 0,
    failCount: 0,
    lastFlushAt: null,
    lastError: null,
  },

  /**
   * Start the event buffer for a session.
   * @param {string} sessionId
   */
  start(sessionId) {
    this._sessionId = sessionId;
    this._eventBuffer = [];
    this._productSnapshotBuffer = null;
    this._trendSnapshotBuffer = null;
    this._snapshotSeq = 0;
    this._isRunning = true;
    this._stats = {
      eventsSent: 0,
      eventsDropped: 0,
      flushCount: 0,
      failCount: 0,
      lastFlushAt: null,
      lastError: null,
    };

    // Start periodic flush
    this._flushTimer = setInterval(() => this.flush(), FLUSH_INTERVAL_MS);
    console.log('[AitherHub Buffer] Started for session:', sessionId);
  },

  /**
   * Stop the event buffer.
   * Performs a final flush before stopping.
   */
  async stop() {
    this._isRunning = false;
    if (this._flushTimer) {
      clearInterval(this._flushTimer);
      this._flushTimer = null;
    }

    // Final flush
    await this.flush();
    console.log('[AitherHub Buffer] Stopped. Stats:', this._stats);
  },

  /**
   * Add a raw event to the buffer.
   * @param {Object} event - Event object matching RawEventItem schema
   */
  addEvent(event) {
    if (!this._isRunning) return;

    this._eventBuffer.push({
      event_type: event.event_type,
      source_type: event.source_type || 'live_dom',
      captured_at: event.captured_at || new Date().toISOString(),
      video_sec: event.video_sec || null,
      product_id: event.product_id || null,
      numeric_value: event.numeric_value || null,
      text_value: event.text_value || null,
      payload: event.payload || null,
      confidence_score: event.confidence_score || null,
    });

    // Force flush if buffer is too large
    if (this._eventBuffer.length >= MAX_BUFFER_SIZE) {
      this.flush();
    }
  },

  /**
   * Set the latest product snapshot (will be sent on next flush).
   * @param {Object[]} products - Array of product data
   */
  setProductSnapshot(products) {
    if (!this._isRunning) return;
    this._snapshotSeq++;
    this._productSnapshotBuffer = {
      products,
      captured_at: new Date().toISOString(),
      snapshot_seq: this._snapshotSeq,
    };
  },

  /**
   * Set the latest trend snapshot.
   * @param {Object[]} trends - Array of trend data
   */
  setTrendSnapshot(trends) {
    if (!this._isRunning) return;
    this._trendSnapshotBuffer = {
      trends,
      captured_at: new Date().toISOString(),
    };
  },

  /**
   * Flush all buffered data to the API.
   */
  async flush() {
    if (!this._sessionId) return;

    // 1. Drain backup queue first (events from previous failed flushes)
    try {
      if (typeof AitherStorage !== 'undefined') {
        const backupEvents = await AitherStorage.drainBackupQueue();
        if (backupEvents.length > 0) {
          console.log('[AitherHub Buffer] Draining', backupEvents.length, 'backed-up events');
          await ExtApiClient.sendEvents(this._sessionId, backupEvents);
          this._stats.eventsSent += backupEvents.length;
        }
      }
    } catch (err) {
      console.warn('[AitherHub Buffer] Failed to drain backup queue:', err.message);
    }

    // 2. Send current event buffer
    if (this._eventBuffer.length > 0) {
      const events = [...this._eventBuffer];
      this._eventBuffer = [];

      try {
        const result = await ExtApiClient.sendEvents(this._sessionId, events);
        this._stats.eventsSent += result.inserted || events.length;
        this._stats.flushCount++;
        this._stats.lastFlushAt = new Date().toISOString();
      } catch (err) {
        console.error('[AitherHub Buffer] Event flush failed:', err.message);
        this._stats.failCount++;
        this._stats.lastError = err.message;

        // Backup to local storage
        if (typeof AitherStorage !== 'undefined') {
          const backed = await AitherStorage.backupEvents(events);
          console.log('[AitherHub Buffer] Backed up', events.length, 'events (total queued:', backed, ')');
        } else {
          this._stats.eventsDropped += events.length;
        }
      }
    }

    // 3. Send product snapshot
    if (this._productSnapshotBuffer) {
      const snapshot = this._productSnapshotBuffer;
      this._productSnapshotBuffer = null;

      try {
        await ExtApiClient.sendProductSnapshots(
          this._sessionId,
          snapshot.captured_at,
          snapshot.products,
          snapshot.snapshot_seq
        );
      } catch (err) {
        console.error('[AitherHub Buffer] Product snapshot flush failed:', err.message);
      }
    }

    // 3. Send trend snapshot
    if (this._trendSnapshotBuffer) {
      const snapshot = this._trendSnapshotBuffer;
      this._trendSnapshotBuffer = null;

      try {
        await ExtApiClient.sendTrendSnapshots(
          this._sessionId,
          snapshot.captured_at,
          snapshot.trends
        );
      } catch (err) {
        console.error('[AitherHub Buffer] Trend snapshot flush failed:', err.message);
      }
    }
  },

  /**
   * Get buffer statistics.
   * @returns {Object}
   */
  getStats() {
    return {
      ...this._stats,
      pendingEvents: this._eventBuffer.length,
      hasPendingProductSnapshot: !!this._productSnapshotBuffer,
      hasPendingTrendSnapshot: !!this._trendSnapshotBuffer,
      isRunning: this._isRunning,
      sessionId: this._sessionId,
    };
  },
};

// Export
if (typeof globalThis !== 'undefined') {
  globalThis.EventBuffer = EventBuffer;
}
