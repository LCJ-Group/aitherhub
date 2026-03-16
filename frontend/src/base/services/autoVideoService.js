import axios from "axios";

const ADMIN_ID = "aither";
const ADMIN_PASS = "hub";
const ADMIN_KEY = `${ADMIN_ID}:${ADMIN_PASS}`;

/**
 * Auto Video Pipeline Service
 *
 * Communicates with the backend auto-video pipeline API.
 * Full pipeline: Script generation (GPT) → TTS (ElevenLabs) →
 *   Face Swap (FaceFusion GPU) → Lip Sync (ElevenLabs Dubbing) → Final video
 */
class AutoVideoService {
  constructor() {
    this.baseURL = import.meta.env.VITE_API_BASE_URL;
  }

  _headers() {
    return { "X-Admin-Key": ADMIN_KEY };
  }

  /**
   * Create a new auto video generation job.
   * @param {Object} params
   * @param {string} params.video_url - URL of the body double video
   * @param {string} params.topic - Topic or product name for script generation
   * @param {string} [params.voice_id] - ElevenLabs voice ID
   * @param {string} [params.language] - Script language: ja, en, zh
   * @param {string} [params.tone] - Script tone
   * @param {string} [params.script_text] - Pre-written script (skips AI generation)
   * @param {string} [params.quality] - Face swap quality: fast, balanced, high, ultra
   * @param {boolean} [params.enable_lip_sync] - Apply lip sync
   * @param {string} [params.product_info] - Additional product info for script
   * @param {number} [params.target_duration_sec] - Target video duration
   * @returns {Promise<Object>} { job_id, status, message }
   */
  async createJob(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/auto-video/create`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Get job status and progress.
   * @param {string} jobId
   * @returns {Promise<Object>} { job_id, status, step, progress, ... }
   */
  async getJobStatus(jobId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/auto-video/status/${jobId}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Get download URL for completed video.
   * @param {string} jobId
   * @returns {string}
   */
  getDownloadUrl(jobId) {
    return `${this.baseURL}/api/v1/auto-video/download/${jobId}`;
  }

  /**
   * Get the generated script for a job.
   * @param {string} jobId
   * @returns {Promise<Object>} { job_id, script, topic }
   */
  async getScript(jobId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/auto-video/script/${jobId}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * List recent auto video jobs.
   * @param {number} [limit=20]
   * @returns {Promise<Array>}
   */
  async listJobs(limit = 20) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/auto-video/list?limit=${limit}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Delete a job and cleanup.
   * @param {string} jobId
   * @returns {Promise<Object>}
   */
  async deleteJob(jobId) {
    const res = await axios.delete(
      `${this.baseURL}/api/v1/auto-video/delete/${jobId}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Health check for all pipeline components.
   * @returns {Promise<Object>}
   */
  async healthCheck() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/auto-video/health`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * List available ElevenLabs voices (reuse from face-swap endpoint).
   * @returns {Promise<Object>} { voices, total }
   */
  async listVoices() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/face-swap/voices`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Upload a video file and get a URL.
   * Uses the existing upload endpoint.
   * @param {File} file
   * @param {function} [onProgress] - Progress callback (0-100)
   * @returns {Promise<string>} Video URL
   */
  async uploadVideo(file, onProgress) {
    const formData = new FormData();
    formData.append("file", file);

    const res = await axios.post(
      `${this.baseURL}/api/v1/upload/file`,
      formData,
      {
        headers: {
          ...this._headers(),
          "Content-Type": "multipart/form-data",
        },
        onUploadProgress: (e) => {
          if (onProgress && e.total) {
            onProgress(Math.round((e.loaded / e.total) * 100));
          }
        },
      }
    );
    return res.data.url || res.data.file_url;
  }
}

export default new AutoVideoService();
