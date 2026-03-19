import axios from "axios";

const ADMIN_ID = "aither";
const ADMIN_PASS = "hub";
const ADMIN_KEY = `${ADMIN_ID}:${ADMIN_PASS}`;

/**
 * AI Live Creator Service
 *
 * Communicates with the backend MuseTalk API endpoints.
 * Pipeline: Portrait + Audio → MuseTalk (GPU Worker) → Lip-synced MP4 Video
 */
class AiLiveCreatorService {
  constructor() {
    this.baseURL = import.meta.env.VITE_API_BASE_URL;
  }

  _headers() {
    return { "X-Admin-Key": ADMIN_KEY };
  }

  /**
   * Start a MuseTalk lip-sync video generation job.
   * @param {Object} params
   * @param {string} params.portrait_url - URL of the portrait image
   * @param {string} params.audio_url - URL of the audio file
   * @param {string} [params.job_id] - Custom job ID
   * @param {number} [params.bbox_shift] - Face bounding box vertical shift
   * @param {number} [params.extra_margin] - Extra margin below face
   * @param {number} [params.batch_size] - Inference batch size
   * @param {number} [params.output_fps] - Output video FPS
   * @returns {Promise<Object>} { success, job_id, status, error }
   */
  async generate(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/musetalk/generate`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Get job status and progress.
   * @param {string} jobId
   * @returns {Promise<Object>} { success, job_id, status, progress, error }
   */
  async getStatus(jobId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/musetalk/status/${jobId}`,
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
    // Return URL with auth header embedded as query param won't work,
    // so we return the raw URL and handle auth in the download method
    return `${this.baseURL}/api/v1/digital-human/musetalk/download/${jobId}`;
  }

  /**
   * Download the generated video as a blob.
   * @param {string} jobId
   * @returns {Promise<Blob>}
   */
  async downloadVideo(jobId) {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/musetalk/download/${jobId}`,
      {
        headers: this._headers(),
        responseType: "blob",
      }
    );
    return res.data;
  }

  /**
   * Health check for the MuseTalk GPU worker.
   * @returns {Promise<Object>} { success, status, gpu_name, ... }
   */
  async healthCheck() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/musetalk/health`,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * Upload a file (portrait image or audio) to Azure Blob Storage via SAS URL.
   * Uses the admin/generate-upload-sas endpoint (same pattern as autoVideoService).
   * @param {File} file - The file to upload
   * @param {string} type - "portrait" or "audio"
   * @param {function} [onProgress] - Progress callback (0-100)
   * @returns {Promise<string>} The permanent blob URL
   */
  /**
   * Start a MuseTalk lip-sync video generation from TEXT (TTS + MuseTalk pipeline).
   * Backend handles: Text → ElevenLabs TTS → Azure Blob → MuseTalk GPU Worker
   * @param {Object} params
   * @param {string} params.portrait_url - URL of the portrait image
   * @param {string} params.text - Text to convert to speech
   * @param {string} [params.voice_id] - ElevenLabs voice ID
   * @param {string} [params.language_code] - Language code (default: 'ja')
   * @param {Object} [params.voice_settings] - ElevenLabs voice settings
   * @param {number} [params.bbox_shift] - Face bounding box vertical shift
   * @param {number} [params.extra_margin] - Extra margin below face
   * @param {number} [params.batch_size] - Inference batch size
   * @param {number} [params.output_fps] - Output video FPS
   * @returns {Promise<Object>} { success, job_id, status, tts_duration_ms, audio_url, error }
   */
  async generateFromText(params) {
    const res = await axios.post(
      `${this.baseURL}/api/v1/digital-human/musetalk/generate-from-text`,
      params,
      { headers: this._headers() }
    );
    return res.data;
  }

  /**
   * List available ElevenLabs voices for the voice selector.
   * @returns {Promise<Object>} { success, voices: [{voice_id, name, category, is_cloned}], total_count }
   */
  async listVoices() {
    const res = await axios.get(
      `${this.baseURL}/api/v1/digital-human/musetalk/voices`,
      { headers: this._headers() }
    );
    return res.data;
  }

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
}

const aiLiveCreatorService = new AiLiveCreatorService();
export default aiLiveCreatorService;
