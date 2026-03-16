import axios from "axios";

const ADMIN_ID = "aither";
const ADMIN_PASS = "hub";
const ADMIN_KEY = `${ADMIN_ID}:${ADMIN_PASS}`;

/**
 * Digital Human Livestream Service
 *
 * Manages Tencent IVH digital human livestream rooms,
 * ElevenLabs voice cloning, face swap streaming, and script generation.
 */
class DigitalHumanService {
  constructor() {
    this.baseURL = import.meta.env.VITE_API_BASE_URL;
  }

  _headers() {
    return { "X-Admin-Key": ADMIN_KEY };
  }

  /** Health check */
  async health() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/health`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Full health check (ElevenLabs + Tencent + GPU) */
  async fullHealth() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/full-health`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** List available voices */
  async getVoices() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/voices`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Generate script from video analysis */
  async generateScript({ video_id, product_focus, tone, language }) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/script/generate`,
      { video_id, product_focus, tone, language },
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Pre-generate audio with cloned voice */
  async generateAudio({ texts, language, voice_id }) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/audio/generate`,
      { texts, language, voice_id },
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Create a digital human livestream room */
  async createLiveroom(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/liveroom/create`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** List all active livestream rooms */
  async listLiverooms() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/liverooms`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Get livestream room status */
  async getLiveroomStatus(liveroomId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/liveroom/${liveroomId}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Close a livestream room */
  async closeLiveroom(liveroomId) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/liveroom/${liveroomId}/close`,
      {},
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Send real-time interjection (takeover) */
  async takeover(liveroomId, params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/liveroom/${liveroomId}/takeover`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Face swap health check */
  async faceSwapHealth() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/face-swap/health`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Set source face for face swapping */
  async setSourceFace({ image_url, image_base64, face_index }) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/face-swap/set-source`,
      { image_url, image_base64, face_index },
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Start face swap livestream */
  async startFaceSwapStream(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/face-swap/stream/start`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Get face swap stream status */
  async getFaceSwapStreamStatus() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/face-swap/stream/status`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Stop face swap stream */
  async stopFaceSwapStream(session_id) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/face-swap/stream/stop`,
      { session_id },
      { headers: this._headers() }
    );
    return res.data;
  }

  /** Test face swap on a single frame */
  async testFrame({ frame_base64, quality, face_enhancer }) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/face-swap/test-frame`,
      { frame_base64, quality, face_enhancer },
      { headers: this._headers() }
    );
    return res.data;
  }
}

const digitalHumanService = new DigitalHumanService();
export default digitalHumanService;
