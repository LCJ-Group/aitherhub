import axios from "axios";

const ADMIN_ID = "aither";
const ADMIN_PASS = "hub";
const ADMIN_KEY = `${ADMIN_ID}:${ADMIN_PASS}`;

/**
 * Face Swap Video Service
 *
 * Communicates with the backend face-swap video pipeline API.
 * Uses admin key authentication (same as AdminDashboard).
 */
class FaceSwapService {
  constructor() {
    this.baseURL = import.meta.env.VITE_API_BASE_URL;
  }

  _headers() {
    return { "X-Admin-Key": ADMIN_KEY };
  }

  /**
   * Start a video face swap + voice conversion job.
   * @param {Object} params
   * @param {string} params.video_url - URL of the input video
   * @param {string} [params.voice_id] - ElevenLabs voice ID
   * @param {string} [params.quality] - Face swap quality: fast, balanced, high
   * @param {boolean} [params.face_enhancer] - Enable GFPGAN
   * @param {boolean} [params.enable_voice_conversion] - Enable voice conversion
   * @param {boolean} [params.remove_background_noise] - Remove background noise
   * @returns {Promise<Object>} { status, job_id, poll_url }
   */
  async startJob(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/face-swap/start-job`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Get job status and progress.
   * @param {string} jobId
   * @returns {Promise<Object>} { job_id, status, step, progress, error, ... }
   */
  async getJobStatus(jobId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/face-swap/status/${jobId}`,
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
    return `${this.baseURL}/api/v1/face-swap/download/${jobId}`;
  }

  /**
   * List recent jobs.
   * @param {number} [limit=20]
   * @returns {Promise<Object>} { jobs, total }
   */
  async listJobs(limit = 20) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/face-swap/jobs?limit=${limit}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Delete a job.
   * @param {string} jobId
   * @returns {Promise<Object>}
   */
  async deleteJob(jobId) {
    const res = await axios.delete(
      `${this.baseURL}/api/v1/face-swap/job/${jobId}`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * List available ElevenLabs voices.
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
   * Health check for the pipeline.
   * @returns {Promise<Object>}
   */
  async healthCheck() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/face-swap/health`,
      { headers: this._headers() }
    );
    return res.data;
  }
}

export default new FaceSwapService();
