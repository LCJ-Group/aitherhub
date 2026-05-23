import axios from "axios";

const ADMIN_ID = "aither";
const ADMIN_PASS = "hub";
const ADMIN_KEY = `${ADMIN_ID}:${ADMIN_PASS}`;

/**
 * Liver Clone Service
 *
 * Real-time Face Swap + Voice Conversion Live Streaming.
 * Controls the Liver Clone pipeline: FaceFusion GPU Worker + ElevenLabs STS/TTS + Auto-pilot.
 */
class LiverCloneService {
  constructor() {
    this.baseURL = import.meta.env.VITE_API_BASE_URL;
  }

  _headers() {
    return { "X-Admin-Key": ADMIN_KEY };
  }

  /**
   * Create a new Liver Clone session.
   * @param {Object} config - Session configuration
   * @returns {Promise<Object>} { session_id, status, config }
   */
  async createSession(config) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/sessions`,
      config,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Start the Liver Clone pipeline for a session.
   * @param {string} sessionId
   * @returns {Promise<Object>} { status, session_id }
   */
  async startSession(sessionId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/sessions/${sessionId}/start`,
      {},
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Stop a running session.
   * @param {string} sessionId
   * @returns {Promise<Object>} { status, session_id }
   */
  async stopSession(sessionId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/sessions/${sessionId}/stop`,
      {},
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Get session status and metrics.
   * @param {string} sessionId
   * @returns {Promise<Object>}
   */
  async getSessionStatus(sessionId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/liver-clone/sessions/${sessionId}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * List all sessions.
   * @returns {Promise<Object>} { sessions: [...] }
   */
  async listSessions() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/liver-clone/sessions`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Delete a session.
   * @param {string} sessionId
   * @returns {Promise<Object>}
   */
  async deleteSession(sessionId) {
    const res = await axios.delete(
      `${this.baseURL}/api/v1/liver-clone/sessions/${sessionId}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Update session configuration.
   * @param {string} sessionId
   * @param {Object} updates - Partial config updates
   * @returns {Promise<Object>}
   */
  async updateConfig(sessionId, updates) {
    const res = await axios.patch(
      `${this.baseURL}/api/v1/liver-clone/sessions/${sessionId}/config`,
      updates,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Respond to a viewer comment.
   * @param {string} sessionId
   * @param {string} comment
   * @param {string} username
   * @returns {Promise<Object>}
   */
  async respondToComment(sessionId, comment, username = "") {
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/sessions/${sessionId}/comment`,
      { comment, username },
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Push text to be spoken via TTS.
   * @param {string} sessionId
   * @param {string} text
   * @returns {Promise<Object>}
   */
  async pushSpeakText(sessionId, text) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/sessions/${sessionId}/speak`,
      { text },
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Get stream metrics.
   * @param {string} sessionId
   * @returns {Promise<Object>}
   */
  async getMetrics(sessionId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/liver-clone/sessions/${sessionId}/metrics`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Health check.
   * @returns {Promise<Object>}
   */
  async healthCheck() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/liver-clone/health`,
      { headers: this._headers() }
    );
    return res.data;
  }
}

export default new LiverCloneService();
