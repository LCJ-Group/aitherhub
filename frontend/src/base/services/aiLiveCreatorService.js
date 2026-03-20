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
    const fileId = `ai-live-creator-${type}-${Date.now()}`;
    const sasRes = await axios.post(
      `${this.baseURL}/api/v1/admin/generate-upload-sas`,
      {
        email: "ai-live-creator@aitherhub.com",
        video_id: fileId,
        filename: file.name,
      },
      { headers: this._headers() }
    );

    const { upload_url, blob_url } = sasRes.data;

    await axios.put(upload_url, file, {
      headers: {
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": file.type || "application/octet-stream",
      },
      onUploadProgress: (e) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    });

    return blob_url;
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
}

const aiLiveCreatorService = new AiLiveCreatorService();
export default aiLiveCreatorService;
