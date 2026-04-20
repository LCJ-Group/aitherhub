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

  async generatePremiumHeyGen(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/heygen/generate-from-text`,
      params,
      { headers: this._headers(), timeout: 360000 }
    );
    return res.data;
  }

  async generatePremiumHeyGenAvatar(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/heygen/generate-from-text-avatar`,
      params,
      { headers: this._headers(), timeout: 360000 }
    );
    return res.data;
  }

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
    if (engine === "heygen") {
      const res = await axios.get(
        `${this.baseURL}/api/v1/digital-human/heygen/status/${jobId}`,
        { headers: this._headers() }
      );
      return res.data;
    }
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

  async heygenHealthCheck() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/heygen/health`,
      { headers: this._headers() }
    );
    return res.data;
  }

  async heygenListTalkingPhotos() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/heygen/talking-photos`,
      { headers: this._headers() }
    );
    return res.data;
  }

  async heygenListAvatars(customOnly = true) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/heygen/avatars`,
      { headers: this._headers(), timeout: 180000, params: { custom_only: customOnly } }
    );
    return res.data;
  }

  async heygenListAvatarGroups() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/heygen/avatar-groups`,
      { headers: this._headers(), timeout: 120000 }
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

  // ══════════════════════════════════════════════════════════
  // HeyGen Streaming Avatar (Real-time)
  // ══════════════════════════════════════════════════════════

  /**
   * Start a HeyGen Streaming Avatar session.
   * Returns session_id, access_token, and LiveKit WebSocket URL.
   */
  async heygenStreamingStart(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/heygen/streaming/start`,
      params,
      { headers: this._headers(), timeout: 60000 }
    );
    return res.data;
  }

  /**
   * Send text to a streaming avatar to speak in real-time.
   */
  async heygenStreamingSpeak(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/heygen/streaming/speak`,
      params,
      { headers: this._headers(), timeout: 30000 }
    );
    return res.data;
  }

  /**
   * Stop a streaming avatar session.
   */
  async heygenStreamingStop(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/heygen/streaming/stop`,
      params,
      { headers: this._headers(), timeout: 30000 }
    );
    return res.data;
  }

  /**
   * Interrupt current speech in a streaming session.
   */
  async heygenStreamingInterrupt(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/heygen/streaming/interrupt`,
      params,
      { headers: this._headers(), timeout: 30000 }
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
  // ── LiveAvatar Streaming (replaces HeyGen Streaming) ──────

  /**
   * List available LiveAvatar avatars (custom + public).
   */
  async liveAvatarListAvatars(includePublic = true) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/liveavatar/avatars`,
      {
        headers: this._headers(),
        params: { include_public: includePublic },
        timeout: 60000,
      }
    );
    return res.data;
  }

  /**
   * Start a LiveAvatar FULL Mode streaming session.
   * Returns session_id and session_token for LiveKit connection.
   * Text is sent via LiveKit data channel (not backend API).
   */
  async liveAvatarStreamingStart(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/liveavatar/streaming/start`,
      params,
      { headers: this._headers(), timeout: 60000 }
    );
    return res.data;
  }

  /**
   * Stop a LiveAvatar streaming session.
   */
  async liveAvatarStreamingStop(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/liveavatar/streaming/stop`,
      params,
      { headers: this._headers(), timeout: 30000 }
    );
    return res.data;
  }

  /**
   * LiveAvatar health check.
   */
  async liveAvatarHealth() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/liveavatar/health`,
      { headers: this._headers(), timeout: 15000 }
    );
    return res.data;
  }

  /**
   * Push speak text to the OBS relay queue.
   * Called by the main page when it sends speakText to its own LiveKit room.
   * OBS polls this queue and sends the text to its own session.
   */
  async liveAvatarSpeakQueuePush(text) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/liveavatar/speak-queue/push`,
      { text },
      { headers: this._headers(), timeout: 10000 }
    );
     return res.data;
  }

  // ── Auto Live (AI自動配信) ──────────────────────────

  /**
   * Start auto live — AI自動セールストーク生成を開始
   */
  async autoLiveStart(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/auto-live/start`,
      params,
      { headers: this._headers(), timeout: 15000 }
    );
    return res.data;
  }

  /**
   * Stop auto live
   */
  async autoLiveStop(sessionId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/auto-live/stop`,
      { session_id: sessionId },
      { headers: this._headers(), timeout: 5000 }
    );
    return res.data;
  }

  /**
   * Pause auto live
   */
  async autoLivePause(sessionId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/auto-live/pause`,
      { session_id: sessionId },
      { headers: this._headers(), timeout: 5000 }
    );
    return res.data;
  }

  /**
   * Resume auto live
   */
  async autoLiveResume(sessionId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/auto-live/resume`,
      { session_id: sessionId },
      { headers: this._headers(), timeout: 5000 }
    );
    return res.data;
  }

  /**
   * Get auto live status
   */
  async autoLiveStatus(sessionId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/auto-live/status/${sessionId}`,
      { headers: this._headers(), timeout: 5000 }
    );
    return res.data;
  }

  /**
   * Add product to running auto live session
   */
  async autoLiveAddProduct(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/auto-live/add-product`,
      params,
      { headers: this._headers(), timeout: 5000 }
    );
    return res.data;
  }

  /**
   * Mark items as consumed from speak queue (so backend keeps generating)
   */
  async autoLiveMarkConsumed(sessionId, count = 1) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/auto-live/mark-consumed`,
      { session_id: sessionId, count },
      { headers: this._headers(), timeout: 5000 }
    );
    return res.data;
  }

  // ── Shopee Live ───────────────────────────────────────────────────

  /**
   * Get Shopee products
   */
  async shopeeGetProducts(shopId = 1542634108) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/shopee-live/products/${shopId}`,
      { headers: this._headers(), timeout: 15000 }
    );
    return res.data;
  }

  /**
   * Get Shopee product details
   */
  async shopeeGetProductDetail(itemIds, shopId = 1542634108) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/shopee-live/products/detail`,
      { item_ids: itemIds, shop_id: shopId },
      { headers: this._headers(), timeout: 15000 }
    );
    return res.data;
  }

  /**
   * Create Shopee livestream session
   */
  async shopeeCreateLiveSession(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/shopee-live/session/create`,
      params,
      { headers: this._headers(), timeout: 15000 }
    );
    return res.data;
  }

  /**
   * Start Shopee livestream
   */
  async shopeeStartLiveSession(sessionId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/shopee-live/session/start`,
      { session_id: sessionId },
      { headers: this._headers(), timeout: 15000 }
    );
    return res.data;
  }

  /**
   * End Shopee livestream
   */
  async shopeeEndLiveSession(sessionId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/shopee-live/session/end`,
      { session_id: sessionId },
      { headers: this._headers(), timeout: 15000 }
    );
    return res.data;
  }
}

const aiLiveCreatorService = new AiLiveCreatorService();
export default aiLiveCreatorService;
