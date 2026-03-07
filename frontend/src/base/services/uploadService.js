import BaseApiService from '../api/BaseApiService';
import { URL_CONSTANTS } from '../api/endpoints/constant';
import { BlockBlobClient } from "@azure/storage-blob";
import TokenManager from '../utils/tokenManager';
import { openDB } from 'idb';
import { UPLOAD_STAGES, UploadStageError, wrapStageError } from './uploadErrors';

const DB_NAME = 'VideoUploadDB';
const STORE_NAME = 'uploads';
const BLOCK_SIZE = 4 * 1024 * 1024; // 4MB blocks
const MAX_CONCURRENT_UPLOADS = 4;
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000; // 2 seconds base delay

class UploadService extends BaseApiService {
  constructor() {
    super(import.meta.env.VITE_API_BASE_URL);
    this.db = null;
  }

  /**
   * Initialize IndexedDB
   */
  async initDB() {
    if (this.db) return this.db;
    
    this.db = await openDB(DB_NAME, 1, {
      upgrade(db) {
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: 'uploadId' });
        }
      },
    });
    
    return this.db;
  }

  /**
   * Save upload metadata to IndexedDB
   */
  async saveUploadMetadata(metadata) {
    const db = await this.initDB();
    await db.put(STORE_NAME, metadata);
  }

  /**
   * Get upload metadata from IndexedDB
   */
  async getUploadMetadata(uploadId) {
    const db = await this.initDB();
    return await db.get(STORE_NAME, uploadId);
  }

  /**
   * Mark block as uploaded
   */
  async markBlockUploaded(uploadId, blockId) {
    const metadata = await this.getUploadMetadata(uploadId);
    if (metadata) {
      if (!metadata.uploadedBlocks) {
        metadata.uploadedBlocks = [];
      }
      if (!metadata.uploadedBlocks.includes(blockId)) {
        metadata.uploadedBlocks.push(blockId);
      }
      await this.saveUploadMetadata(metadata);
    }
  }

  /**
   * Clear upload metadata
   */
  async clearUploadMetadata(uploadId) {
    const db = await this.initDB();
    await db.delete(STORE_NAME, uploadId);
  }

  /**
   * Retry helper with exponential backoff
   * @param {Function} fn - Async function to retry
   * @param {number} maxRetries - Maximum number of retries
   * @param {string} label - Label for logging
   * @returns {Promise<*>}
   */
  async retryWithBackoff(fn, maxRetries = MAX_RETRIES, label = 'operation') {
    let lastError;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        return await fn();
      } catch (error) {
        lastError = error;
        const isNetworkError = !error.response && (
          error.message?.includes('Failed to fetch') ||
          error.message?.includes('Network') ||
          error.message?.includes('fetch') ||
          error.message?.includes('ECONNRESET') ||
          error.message?.includes('timeout') ||
          error.code === 'ERR_NETWORK'
        );

        if (attempt < maxRetries && isNetworkError) {
          const delay = RETRY_DELAY_MS * Math.pow(2, attempt); // exponential backoff
          console.warn(`[UploadService] ${label} failed (attempt ${attempt + 1}/${maxRetries + 1}), retrying in ${delay}ms...`, error.message);
          await new Promise(resolve => setTimeout(resolve, delay));
        } else {
          break;
        }
      }
    }
    throw lastError;
  }

  /**
   * Generate SAS upload URL from backend
   * @param {string} email - User email
   * @param {string} filename - File name to upload
   * @returns {Promise<{video_id, upload_url, blob_url, expires_at}>}
   */
  async generateUploadUrl(email, filename) {
    try {
      return await this.retryWithBackoff(
        () => this.post(URL_CONSTANTS.GENERATE_UPLOAD_URL, { email, filename }),
        MAX_RETRIES,
        'generateUploadUrl'
      );
    } catch (error) {
      throw wrapStageError(UPLOAD_STAGES.SAS_GENERATE, error);
    }
  }

  /**
   * Upload file directly to Azure Blob Storage with resume support
   * @param {File} file - File to upload
   * @param {string} uploadUrl - SAS URL from backend
   * @param {string} uploadId - Upload ID for resume tracking
   * @param {Function} onProgress - Callback for progress updates
   * @param {number} startFrom - Start uploading from this block index (for resume)
   * @returns {Promise<void>}
   */
  async uploadToAzure(file, uploadUrl, uploadId, onProgress, startFrom = 0) {
    const blockBlobClient = new BlockBlobClient(uploadUrl);

    // Determine proper content type for video files
    let contentType = 'video/mp4'; // Default fallback

    // Use file.type if available and valid
    if (file.type && file.type.startsWith('video/')) {
      contentType = file.type;
    } else {
      // Fallback detection based on file extension
      const fileName = file.name.toLowerCase();
      if (fileName.endsWith('.mp4')) {
        contentType = 'video/mp4';
      } else if (fileName.endsWith('.webm')) {
        contentType = 'video/webm';
      } else if (fileName.endsWith('.avi')) {
        contentType = 'video/avi';
      } else if (fileName.endsWith('.mov')) {
        contentType = 'video/quicktime';
      } else if (fileName.endsWith('.mkv')) {
        contentType = 'video/x-matroska';
      }
    }

    // 1. Create block list and metadata
    const blocks = [];
    const blockIds = [];
    
    for (let i = 0; i < file.size; i += BLOCK_SIZE) {
      const blockIndex = Math.floor(i / BLOCK_SIZE);
      // Create simple numeric block ID and encode as base64
      const blockIdString = String(blockIndex).padStart(6, '0');
      const blockId = btoa(blockIdString); // Base64 encode
      blockIds.push(blockId);
      blocks.push({
        index: blockIndex,
        data: file.slice(i, Math.min(i + BLOCK_SIZE, file.size)),
        id: blockId,
        start: i,
        end: Math.min(i + BLOCK_SIZE, file.size),
      });
    }

    // 2. Save metadata to IndexedDB (merge with existing if resuming)
    const existingMetadata = await this.getUploadMetadata(uploadId) || {};
    await this.saveUploadMetadata({
      uploadId,
      uploadUrl,
      fileName: file.name,
      fileSize: file.size,
      blockIds,
      uploadedBlocks: existingMetadata.uploadedBlocks || [],
      contentType,
      timestamp: existingMetadata.timestamp || Date.now(),
      videoId: existingMetadata.videoId, // Preserve videoId if already set
    });

    // 3. Upload blocks (concurrent with bounded pool)
    const metadata = await this.getUploadMetadata(uploadId);
    const uploadedSet = new Set(metadata?.uploadedBlocks || []);

    // Ensure skipped blocks before startFrom are recorded for resume
    if (startFrom > 0) {
      let changed = false;
      for (const block of blocks) {
        if (block.index >= startFrom) break;
        if (!uploadedSet.has(block.id)) {
          uploadedSet.add(block.id);
          changed = true;
        }
      }
      if (changed) {
        await this.saveUploadMetadata({
          ...metadata,
          uploadedBlocks: Array.from(uploadedSet),
        });
      }
    }

    let completed = uploadedSet.size;
    const totalBlocks = blocks.length;

    const updateProgress = () => {
      const percentage = Math.round((completed / totalBlocks) * 100);
      if (onProgress) onProgress(percentage);
    };

    // Serialize metadata writes to avoid race conditions
    let writeQueue = Promise.resolve();
    const safeMarkUploaded = async (blockId) => {
      writeQueue = writeQueue.then(() => this.markBlockUploaded(uploadId, blockId));
      return writeQueue;
    };

    const pendingBlocks = blocks.filter(
      (block) => block.index >= startFrom && !uploadedSet.has(block.id)
    );

    if (pendingBlocks.length === 0) {
      updateProgress();
    } else {
      let nextIndex = 0;

      const uploadWorker = async () => {
        while (true) {
          const current = nextIndex++;
          if (current >= pendingBlocks.length) break;
          const block = pendingBlocks[current];

          // Retry each block upload with exponential backoff
          try {
            await this.retryWithBackoff(async () => {
              const blockSize = block.end - block.start;
              await blockBlobClient.stageBlock(block.id, block.data, blockSize);
            }, MAX_RETRIES, `stageBlock[${block.index}]`);
          } catch (error) {
            throw wrapStageError(UPLOAD_STAGES.BLOB_PUT, error);
          }

          await safeMarkUploaded(block.id);
          uploadedSet.add(block.id);
          completed += 1;
          updateProgress();
        }
      };

      const workerCount = Math.min(MAX_CONCURRENT_UPLOADS, pendingBlocks.length);
      await Promise.all(Array.from({ length: workerCount }, uploadWorker));
    }

    // 4. Commit all blocks (with retry)
    try {
      await this.retryWithBackoff(async () => {
        await blockBlobClient.commitBlockList(blockIds, {
          blobHTTPHeaders: {
            blobContentType: contentType,
            blobCacheControl: 'public, max-age=3600',
          },
        });
      }, MAX_RETRIES, 'commitBlockList');
    } catch (error) {
      throw wrapStageError(UPLOAD_STAGES.BLOCK_COMMIT, error);
    }
    
    // Clear metadata after successful commit
    await this.clearUploadMetadata(uploadId);
  }

  /**
   * Notify backend that upload is complete
   * @param {string} email - User email
   * @param {string} video_id - Video ID
   * @param {string} filename - File name
   * @param {string} upload_id - Upload ID
   * @returns {Promise<{video_id, status, message}>}
   */
  async uploadComplete(email, video_id, filename, upload_id) {
    // Verify token is valid before making authenticated request
    const token = TokenManager.getToken();
    if (!token) {
      throw new UploadStageError(UPLOAD_STAGES.AUTH, window.__t('authTokenNotFound') || 'Auth token not found');
    }

    if (TokenManager.isTokenExpired(token)) {
      throw new UploadStageError(UPLOAD_STAGES.AUTH, window.__t('sessionExpired') || 'Session expired');
    }

    try {
      return await this.retryWithBackoff(
        () => this.post(URL_CONSTANTS.UPLOAD_COMPLETE, { email, video_id, filename, upload_id }),
        MAX_RETRIES,
        'uploadComplete'
      );
    } catch (error) {
      throw wrapStageError(UPLOAD_STAGES.UPLOAD_COMPLETE, error);
    }
  }

  /**
   * Generate SAS upload URLs for Excel files
   * @param {string} email - User email
   * @param {string} video_id - Video ID
   * @param {string} product_filename - Product Excel filename
   * @param {string} trend_filename - Trend stats Excel filename
   * @returns {Promise<{video_id, product_upload_url, product_blob_url, trend_upload_url, trend_blob_url, expires_at}>}
   */
  async generateExcelUploadUrls(email, video_id, product_filename, trend_filename) {
    try {
      return await this.retryWithBackoff(
        () => this.post(URL_CONSTANTS.GENERATE_EXCEL_UPLOAD_URL, { email, video_id, product_filename, trend_filename }),
        MAX_RETRIES,
        'generateExcelUploadUrls'
      );
    } catch (error) {
      throw wrapStageError(UPLOAD_STAGES.EXCEL_SAS, error);
    }
  }

  /**
   * Upload a single Excel file to Azure Blob Storage (with retry)
   * @param {File} file - Excel file to upload
   * @param {string} uploadUrl - SAS URL
   * @returns {Promise<void>}
   */
  async uploadExcelToAzure(file, uploadUrl) {
    try {
      await this.retryWithBackoff(async () => {
        const blockBlobClient = new BlockBlobClient(uploadUrl);
        await blockBlobClient.uploadData(file, {
          blobHTTPHeaders: {
            blobContentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          },
        });
      }, MAX_RETRIES, 'uploadExcelToAzure');
    } catch (error) {
      throw wrapStageError(UPLOAD_STAGES.EXCEL_UPLOAD, error);
    }
  }

  /**
   * Notify backend that upload is complete (with upload_type and excel URLs)
   * @param {string} email - User email
   * @param {string} video_id - Video ID
   * @param {string} filename - Video file name
   * @param {string} upload_id - Upload ID
   * @param {string} upload_type - 'screen_recording' or 'clean_video'
   * @param {string|null} excel_product_blob_url - Product Excel blob URL
   * @param {string|null} excel_trend_blob_url - Trend Excel blob URL
   * @returns {Promise<{video_id, status, message}>}
   */
  async uploadCompleteWithType(email, video_id, filename, upload_id, upload_type = 'screen_recording', excel_product_blob_url = null, excel_trend_blob_url = null) {
    const token = TokenManager.getToken();
    if (!token) {
      throw new UploadStageError(UPLOAD_STAGES.AUTH, window.__t('authTokenNotFound') || 'Auth token not found');
    }
    if (TokenManager.isTokenExpired(token)) {
      throw new UploadStageError(UPLOAD_STAGES.AUTH, window.__t('sessionExpired') || 'Session expired');
    }
    try {
      return await this.retryWithBackoff(
        () => this.post(URL_CONSTANTS.UPLOAD_COMPLETE, {
          email,
          video_id,
          filename,
          upload_id,
          upload_type,
          excel_product_blob_url,
          excel_trend_blob_url,
        }),
        MAX_RETRIES,
        'uploadCompleteWithType'
      );
    } catch (error) {
      throw wrapStageError(UPLOAD_STAGES.UPLOAD_COMPLETE, error);
    }
  }

  /**
   * Check if user has resumable upload
   * @param {number} user_id
   * @returns {Promise<{upload_resume: boolean, upload_id?: string}>}
   */
  async checkUploadResume(user_id) {
    return await this.get(`${URL_CONSTANTS.UPLOAD_RESUME_CHECK}/${user_id}`);
  }

  /**
   * Clear all uploads for a user
   * @param {number} user_id
   * @returns {Promise<{status: string, message: string, deleted_count: number}>}
   */
  async clearUserUploads(user_id) {
    return await this.delete(`${URL_CONSTANTS.UPLOADS_CLEAR}/${user_id}`);
  }

  /**
   * Complete upload workflow: generate URL + upload to Azure + notify backend
   * @param {File} file - File to upload
   * @param {string} email - User email
   * @param {Function} onProgress - Callback for progress updates
   * @returns {Promise<string>} - video_id
   */
  async uploadFile(file, email, onProgress, onUploadInit) {
    const { video_id, upload_id, upload_url } = await this.generateUploadUrl(email, file.name);

    if (onUploadInit) {
      onUploadInit({ uploadId: upload_id, videoId: video_id });
    }

    // Save initial metadata with video_id for potential resume
    await this.saveUploadMetadata({
      uploadId: upload_id,
      uploadUrl: upload_url,
      videoId: video_id,
      fileName: file.name,
      fileSize: file.size,
      blockIds: [],
      uploadedBlocks: [],
      contentType: 'video/mp4',
      timestamp: Date.now(),
    });

    await this.uploadToAzure(file, upload_url, upload_id, onProgress);

    // Notify backend that upload is complete
    await this.uploadComplete(email, video_id, file.name, upload_id);

    return video_id;
  }
  /**
   * Complete clean video upload workflow: video + Excel files
   * @param {File} videoFile - Clean video file
   * @param {File} productExcel - Product Excel file
   * @param {File} trendExcel - Trend stats Excel file
   * @param {string} email - User email
   * @param {Function} onProgress - Callback for progress updates (0-100)
   * @param {Function} onUploadInit - Callback when upload is initialized
   * @returns {Promise<string>} - video_id
   */
  async uploadCleanVideo(videoFile, productExcel, trendExcel, email, onProgress, onUploadInit) {
    // Step 1: Generate video upload URL
    const { video_id, upload_id, upload_url } = await this.generateUploadUrl(email, videoFile.name);

    if (onUploadInit) {
      onUploadInit({ uploadId: upload_id, videoId: video_id });
    }

    // Save initial metadata
    await this.saveUploadMetadata({
      uploadId: upload_id,
      uploadUrl: upload_url,
      videoId: video_id,
      fileName: videoFile.name,
      fileSize: videoFile.size,
      blockIds: [],
      uploadedBlocks: [],
      contentType: 'video/mp4',
      timestamp: Date.now(),
    });

    // Step 2: Upload video (0-80% of progress)
    await this.uploadToAzure(videoFile, upload_url, upload_id, (percentage) => {
      if (onProgress) onProgress(Math.round(percentage * 0.8));
    });

    // Step 3: Generate Excel upload URLs
    let product_blob_url = null;
    let trend_blob_url = null;

    if (productExcel && trendExcel) {
      if (onProgress) onProgress(82);
      const excelUrls = await this.generateExcelUploadUrls(
        email,
        video_id,
        productExcel.name,
        trendExcel.name
      );

      // Step 4: Upload Excel files (80-95% of progress)
      if (onProgress) onProgress(85);
      await this.uploadExcelToAzure(productExcel, excelUrls.product_upload_url);
      product_blob_url = excelUrls.product_blob_url;

      if (onProgress) onProgress(90);
      await this.uploadExcelToAzure(trendExcel, excelUrls.trend_upload_url);
      trend_blob_url = excelUrls.trend_blob_url;

      if (onProgress) onProgress(95);
    }

    // Step 5: Notify backend of completion with upload_type and excel URLs
    await this.uploadCompleteWithType(
      email,
      video_id,
      videoFile.name,
      upload_id,
      'clean_video',
      product_blob_url,
      trend_blob_url
    );

    if (onProgress) onProgress(100);
    return video_id;
  }

  /**
   * Batch upload multiple clean videos sharing the same Excel files.
   * @param {Array<{file: File, timeOffsetSeconds: number}>} videoItems - Video files with time offsets
   * @param {File} productExcel - Product Excel file
   * @param {File} trendExcel - Trend stats Excel file
   * @param {string} email - User email
   * @param {Function} onProgress - Callback for overall progress (0-100)
   * @param {Function} onUploadInit - Callback when first upload is initialized
   * @returns {Promise<string[]>} - array of video_ids
   */
  async batchUploadCleanVideos(videoItems, productExcel, trendExcel, email, onProgress, onUploadInit) {
    const totalVideos = videoItems.length;
    // Progress allocation: videos 0-75%, excel 75-90%, completion 90-100%
    const videoProgressShare = 75;
    const excelProgressShare = 15;

    // Step 1: Upload all videos to Azure
    const uploadedVideos = [];
    for (let i = 0; i < totalVideos; i++) {
      const { file, timeOffsetSeconds } = videoItems[i];
      const { video_id, upload_id, upload_url } = await this.generateUploadUrl(email, file.name);

      if (i === 0 && onUploadInit) {
        onUploadInit({ uploadId: upload_id, videoId: video_id });
      }

      await this.saveUploadMetadata({
        uploadId: upload_id,
        uploadUrl: upload_url,
        videoId: video_id,
        fileName: file.name,
        fileSize: file.size,
        blockIds: [],
        uploadedBlocks: [],
        contentType: 'video/mp4',
        timestamp: Date.now(),
      });

      const baseProgress = (i / totalVideos) * videoProgressShare;
      const perVideoShare = videoProgressShare / totalVideos;

      await this.uploadToAzure(file, upload_url, upload_id, (percentage) => {
        const overall = baseProgress + (percentage / 100) * perVideoShare;
        if (onProgress) onProgress(Math.round(overall));
      });

      uploadedVideos.push({ video_id, upload_id, filename: file.name, timeOffsetSeconds });
    }

    // Step 2: Upload Excel files (shared across all videos, use first video_id)
    let product_blob_url = null;
    let trend_blob_url = null;

    if (productExcel && trendExcel && uploadedVideos.length > 0) {
      if (onProgress) onProgress(videoProgressShare + 2);
      const excelUrls = await this.generateExcelUploadUrls(
        email,
        uploadedVideos[0].video_id,
        productExcel.name,
        trendExcel.name
      );

      if (onProgress) onProgress(videoProgressShare + 5);
      await this.uploadExcelToAzure(productExcel, excelUrls.product_upload_url);
      product_blob_url = excelUrls.product_blob_url;

      if (onProgress) onProgress(videoProgressShare + 10);
      await this.uploadExcelToAzure(trendExcel, excelUrls.trend_upload_url);
      trend_blob_url = excelUrls.trend_blob_url;

      if (onProgress) onProgress(videoProgressShare + excelProgressShare);
    }

    // Step 3: Notify backend with batch-upload-complete
    const batchPayload = {
      email,
      videos: uploadedVideos.map(v => ({
        video_id: v.video_id,
        filename: v.filename,
        upload_id: v.upload_id,
        time_offset_seconds: v.timeOffsetSeconds || 0,
      })),
      excel_product_blob_url: product_blob_url,
      excel_trend_blob_url: trend_blob_url,
    };

    try {
      await this.retryWithBackoff(
        () => this.post(URL_CONSTANTS.BATCH_UPLOAD_COMPLETE, batchPayload),
        MAX_RETRIES,
        'batchUploadComplete'
      );
    } catch (error) {
      throw wrapStageError(UPLOAD_STAGES.BATCH_COMPLETE, error);
    }

    if (onProgress) onProgress(100);
    return uploadedVideos.map(v => v.video_id);
  }
}

export default new UploadService();
