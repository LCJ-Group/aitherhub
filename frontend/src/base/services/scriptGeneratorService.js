import BaseApiService from '../api/BaseApiService';

/**
 * Script Generator Tool Service
 *
 * Standalone "売れる台本" tool — generates live commerce scripts
 * based on real performance data from AitherHub's analysis database.
 *
 * Extends BaseApiService which automatically handles:
 * - Bearer token injection via request interceptor
 * - Token refresh on 401/403
 * - Auto-logout on refresh failure
 */
class ScriptGeneratorService extends BaseApiService {
  constructor() {
    super(import.meta.env.VITE_API_BASE_URL);
  }

  /**
   * Generate a live commerce script from product info + real performance data.
   * @param {Object} params
   * @param {string} params.product_name - Product name (required)
   * @param {string} [params.product_image_url] - Product image URL
   * @param {string} [params.product_description] - Product description
   * @param {string} [params.product_price] - Product price
   * @param {string} [params.target_audience] - Target audience
   * @param {string} [params.tone] - Script tone
   * @param {string} [params.language] - Output language
   * @param {number} [params.duration_minutes] - Target duration
   * @param {string} [params.additional_instructions] - Extra instructions
   * @returns {Promise<Object>} { script, char_count, estimated_duration_minutes, patterns_used, data_insights, product_analysis, model }
   */
  async generateScript(params) {
    const response = await this.client.post(
      '/api/v1/script-generator/generate',
      params,
      {
        timeout: 120000, // 2 min timeout for LLM generation
      }
    );
    return response.data;
  }

  /**
   * Get aggregated winning patterns preview.
   * @param {number} [limitVideos=50] - Number of videos to analyze
   * @returns {Promise<Object>} { videos_analyzed, cta_phrases, duration_insights, top_techniques }
   */
  async getWinningPatterns(limitVideos = 50) {
    const response = await this.client.get(
      `/api/v1/script-generator/patterns?limit_videos=${limitVideos}`
    );
    return response.data;
  }

  /**
   * Get a SAS URL for uploading a product image.
   * @returns {Promise<Object>} { upload_url, blob_url, expiry }
   */
  async getImageUploadUrl() {
    const response = await this.client.post(
      '/api/v1/script-generator/upload-image',
      {}
    );
    return response.data;
  }

  /**
   * Upload a product image to Azure Blob Storage.
   * @param {File} file - Image file to upload
   * @returns {Promise<string>} blob_url - The public URL of the uploaded image
   */
  async uploadProductImage(file) {
    // Step 1: Get SAS upload URL
    const { upload_url, blob_url } = await this.getImageUploadUrl();

    // Step 2: Upload directly to Azure Blob Storage
    const response = await fetch(upload_url, {
      method: 'PUT',
      headers: {
        'x-ms-blob-type': 'BlockBlob',
        'Content-Type': file.type || 'image/jpeg',
      },
      body: file,
    });

    if (!response.ok) {
      throw new Error(`Image upload failed: ${response.status}`);
    }

    return blob_url;
  }
}

const scriptGeneratorService = new ScriptGeneratorService();
export default scriptGeneratorService;
