import axios from "axios";

const ADMIN_ID = "aither";
const ADMIN_PASS = "hub";
const ADMIN_KEY = `${ADMIN_ID}:${ADMIN_PASS}`;

/**
 * AI Live Creator Service
 *
 * Communicates with the backend MuseTalk + IMTalker API endpoints.
 * Standard (MuseTalk): Portrait + Audio → Lip-sync only
 * Premium (IMTalker):  Portrait + Audio → Full facial animation
 *
 * Also includes:
 *   - Live Session management
 *   - Sales Brain (帯貨大脳) script generation
 *   - Comment Response generation
 *   - Video Queue management
 */
class AiLiveCreatorService {
  constructor() {
    this.baseURL = import.meta.env.VITE_API_BASE_URL;
  }

  _headers() {
    return { "X-Admin-Key": ADMIN_KEY };
  }

  // ── Standard (MuseTalk) ────────────────────────────────

  async generate(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/musetalk/generate`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  async generateFromText(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/musetalk/generate-from-text`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  // ── Premium (IMTalker) ─────────────────────────────────

  async generatePremium(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/imtalker/generate`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  async generatePremiumFromText(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/imtalker/generate-from-text`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  // ── Shared: Status / Download / Health ──────────────────

  async getStatus(jobId, engine = "musetalk") {
    const prefix = engine === "imtalker" ? "imtalker" : "musetalk";
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/${prefix}/status/${jobId}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  getDownloadUrl(jobId, engine = "musetalk") {
    const prefix = engine === "imtalker" ? "imtalker" : "musetalk";
    return `${this.baseURL}/api/v1/digital-human/${prefix}/download/${jobId}`;
  }

  async downloadVideo(jobId, engine = "musetalk") {
    const prefix = engine === "imtalker" ? "imtalker" : "musetalk";
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/${prefix}/download/${jobId}`,
      {
        headers: this._headers(),
        responseType: "blob",
      }
    );
    return res.data;
  }

  async healthCheck() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/musetalk/health`,
      { headers: this._headers() }
    );
    return res.data;
  }

  // ── Voice list ──────────────────────────────────────────

  async listVoices() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/musetalk/voices`,
      { headers: this._headers() }
    );
    return res.data;
  }

  // ── File upload ─────────────────────────────────────────

  async uploadFile(file, type, onProgress) {
    // Upload via backend proxy to avoid CORS / timeout issues with Azure Blob
    const formData = new FormData();
    formData.append("file", file);
    formData.append("file_type", type);

    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/upload-file`,
      formData,
      {
        headers: {
          ...this._headers(),
          "Content-Type": "multipart/form-data",
        },
        timeout: 300000, // 5 minutes for large files
        onUploadProgress: (e) => {
          if (onProgress && e.total) {
            onProgress(Math.round((e.loaded / e.total) * 100));
          }
        },
      }
    );

    return res.data.blob_url;
  }

  // ══════════════════════════════════════════════════════════
  // Live Session Management
  // ══════════════════════════════════════════════════════════

  async createLiveSession(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/live-session/create`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  async getLiveSession(sessionId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/live-session/${sessionId}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  async listLiveSessions() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/live-sessions`,
      { headers: this._headers() }
    );
    return res.data;
  }

  async closeLiveSession(sessionId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/live-session/${sessionId}/close`,
      {},
      { headers: this._headers() }
    );
    return res.data;
  }

  // ══════════════════════════════════════════════════════════
  // Sales Brain (帯貨大脳) — Product Script Generation
  // ══════════════════════════════════════════════════════════

  async generateProductScript(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/sales-brain/generate-script`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  async generateAllSessionScripts(sessionId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/live-session/${sessionId}/generate-all-scripts`,
      {},
      { headers: this._headers() }
    );
    return res.data;
  }

  // ══════════════════════════════════════════════════════════
  // Comment Response
  // ══════════════════════════════════════════════════════════

  async generateCommentResponse(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/comment-response/generate`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  // ══════════════════════════════════════════════════════════
  // Video Queue
  // ══════════════════════════════════════════════════════════

  async generateAndQueueVideo(sessionId, params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/live-session/${sessionId}/generate-video`,
      { session_id: sessionId, ...params },
      { headers: this._headers() }
    );
    return res.data;
  }

  async getSessionQueue(sessionId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/live-session/${sessionId}/queue`,
      { headers: this._headers() }
    );
    return res.data;
  }

  // ══════════════════════════════════════════════════════════
  // TikTok Shop Product Import
  // ══════════════════════════════════════════════════════════

  async importTikTokProduct(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/tiktok-product/import`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  // ══════════════════════════════════════════════════════════
  // Real-time TTS Speak (Video Loop + Audio Overlay)
  // ══════════════════════════════════════════════════════════

  /**
   * Generate TTS audio for real-time playback over looping video.
   * Returns an MP3 audio URL that the frontend plays alongside the video loop.
   */
  async speak(sessionId, params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/live-session/${sessionId}/speak`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Start the auto-pilot livestream brain.
   * The brain will automatically cycle through greeting → product intro →
   * comment response → sales pitch → next product.
   */
  async startAutoPilot(sessionId, params = {}) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/live-session/${sessionId}/autopilot/start`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Get the next speech segment from the auto-pilot brain.
   * Call this after each audio finishes playing.
   * Returns: { action, audio_url, text, script_type, product_name, next_state }
   */
  async getAutoPilotNext(sessionId, params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/live-session/${sessionId}/autopilot/next`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }
}

const aiLiveCreatorService = new AiLiveCreatorService();
export default aiLiveCreatorService;
