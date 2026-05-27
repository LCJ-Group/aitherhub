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

  // ── Preview Methods ─────────────────────────────────────────────

  /**
   * Set source face for preview (without creating a full session).
   * @param {string} imageBase64 - Base64-encoded face image
   * @returns {Promise<Object>}
   */
  async previewSetSource(imageBase64, imageUrl = null) {
    const body = {};
    if (imageBase64) body.image_base64 = imageBase64;
    if (imageUrl) body.image_url = imageUrl;
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/preview/set-source`,
      body,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Process a single frame for preview.
   * @param {string} imageBase64 - Base64-encoded webcam frame
   * @returns {Promise<Object>} { status, image_base64 }
   */
  async previewFrame(imageBase64) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/preview/frame`,
      { image_base64: imageBase64 },
      { headers: this._headers(), timeout: 30000 }
    );
    return res.data;
  }

  /**
   * Get the WebSocket URL for real-time preview streaming.
   * @returns {Promise<Object>} { ws_url }
   */
  async getPreviewWsUrl() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/liver-clone/preview/ws-url`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Generate TTS audio for preview mode (no session required).
   * Returns base64-encoded MP3 audio for browser playback.
   * @param {string} text - Text to speak
   * @param {string} voiceId - ElevenLabs voice ID
   * @param {Object} options - { voice_stability, voice_similarity, language }
   * @returns {Promise<Object>} { status, audio_base64, audio_format, text }
   */
  /**
   * Validate a Voice ID against the ElevenLabs API.
   * Returns { valid, voice_id, name, category } if valid,
   * or { valid: false, error } if not found.
   * @param {string} voiceId - ElevenLabs voice ID to validate
   * @returns {Promise<Object>}
   */
  async validateVoiceId(voiceId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/liver-clone/preview/validate-voice`,
      {
        headers: this._headers(),
        params: { voice_id: voiceId },
        timeout: 15000,
      }
    );
    return res.data;
  }

  /**
   * Convert voice using ElevenLabs STS (Speech-to-Speech) in preview mode.
   * Sends recorded audio chunk and receives converted audio.
   * @param {string} audioBase64 - Base64-encoded audio (webm/opus)
   * @param {string} voiceId - ElevenLabs voice ID
   * @param {Object} options - { voice_stability, voice_similarity }
   * @returns {Promise<Object>} { status, audio_base64, audio_format }
   */
  async previewSTS(audioBase64, voiceId, options = {}) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/preview/sts`,
      {
        audio_base64: audioBase64,
        voice_id: voiceId,
        voice_stability: options.voice_stability || 0.5,
        voice_similarity: options.voice_similarity || 0.75,
      },
      { headers: this._headers(), timeout: 30000 }
    );
    return res.data;
  }

  async previewSpeak(text, voiceId, options = {}) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/preview/speak`,
      {
        text,
        voice_id: voiceId,
        voice_stability: options.voice_stability || 0.5,
        voice_similarity: options.voice_similarity || 0.75,
        language: options.language || "ja",
      },
      { headers: this._headers(), timeout: 30000 }
    );
    return res.data;
  }

  /**
   * Generate product introduction script from image.
   * Uses GPT-4 Vision to analyze product and create live-commerce script.
   * @param {Object} params - { image_base64, image_url, product_name, product_info, language, style, max_length }
   * @returns {Promise<Object>} { status, script, language, style }
   */
  async generateProductIntro(params = {}) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/liver-clone/preview/product-intro`,
      {
        image_base64: params.image_base64 || "",
        image_url: params.image_url || "",
        product_name: params.product_name || "",
        product_info: params.product_info || "",
        language: params.language || "ja",
        style: params.style || "enthusiastic",
        max_length: params.max_length || 150,
      },
      { headers: this._headers(), timeout: 45000 }
    );
    return res.data;
  }
}

export default new LiverCloneService();
