import BaseApiService from '../api/BaseApiService';
import { URL_CONSTANTS } from '../api/endpoints/constant';
import { BlockBlobClient } from "@azure/storage-blob";
import TokenManager from '../utils/tokenManager';
import { openDB } from 'idb';
import { UPLOAD_STAGES, UploadStageError, wrapStageError } from './uploadErrors';

const DB_NAME = 'VideoUploadDB';
const STORE_NAME = 'uploads';

// ── Adaptive block sizing constants ──
const PROBE_BLOCK_SIZE   = 1 * 1024 * 1024;   // 1 MB – first block used to measure speed
const MIN_BLOCK_SIZE     = 1 * 1024 * 1024;   // 1 MB floor
const MAX_BLOCK_SIZE     = 8 * 1024 * 1024;   // 8 MB ceiling
const DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024;   // 4 MB fallback when speed is unknown
const TARGET_BLOCK_SECONDS = 30;               // aim for each block to finish in ~30 s
const MIN_TIMEOUT_MS     = 60_000;             // never less than 60 s per block
const TIMEOUT_MULTIPLIER = 3;                  // timeout = expected_time × 3

const MAX_CONCURRENT_UPLOADS = 6;
const MAX_RETRIES = 5;
const RETRY_DELAY_MS = 2000;
const FINALIZE_TIMEOUT_MS = 300_000;

// ── Proxy fallback constants ──
const PROXY_BLOCK_SIZE     = 4 * 1024 * 1024;   // 4 MB per block via proxy
const PROXY_MAX_RETRIES    = 3;
const PROXY_RETRY_DELAY_MS = 3000;
const PROBE_FAILURE_THRESHOLD = 2; // Switch to proxy after 2 consecutive probe failures

// ── Speed tracker (shared across uploads in same session) ──
class SpeedTracker {
  constructor() {
    this._samples = [];          // { bytesPerSec, timestamp }
    this._maxSamples = 20;       // rolling window
  }

  /** Record one completed block transfer */
  record(bytes, durationMs) {
    if (durationMs <= 0) return;
    const bytesPerSec = (bytes / durationMs) * 1000;
    this._samples.push({ bytesPerSec, timestamp: Date.now() });
    if (this._samples.length > this._maxSamples) {
      this._samples.shift();
    }
  }

  /** Weighted-average speed (recent samples count more) */
  getSpeed() {
    if (this._samples.length === 0) return null;
    let weightSum = 0;
    let valueSum = 0;
    this._samples.forEach((s, i) => {
      const weight = i + 1;          // newer = heavier
      valueSum += s.bytesPerSec * weight;
      weightSum += weight;
    });
    return valueSum / weightSum;
  }

  /** Compute optimal block size for current speed */
  getOptimalBlockSize() {
    const speed = this.getSpeed();
    if (!speed) return DEFAULT_BLOCK_SIZE;
    // block = speed × TARGET_BLOCK_SECONDS, clamped
    const raw = speed * TARGET_BLOCK_SECONDS;
    // Round to nearest MB for cleaner slicing
    const mb = Math.round(raw / (1024 * 1024));
    const clamped = Math.max(MIN_BLOCK_SIZE / (1024 * 1024), Math.min(MAX_BLOCK_SIZE / (1024 * 1024), mb));
    return clamped * 1024 * 1024;
  }

  /** Compute per-block timeout based on block size and measured speed */
  getTimeout(blockSize) {
    const speed = this.getSpeed();
    if (!speed) return Math.max(MIN_TIMEOUT_MS, (blockSize / (1024 * 1024)) * 60_000); // 60s per MB fallback
    const expectedMs = (blockSize / speed) * 1000;
    return Math.max(MIN_TIMEOUT_MS, Math.round(expectedMs * TIMEOUT_MULTIPLIER));
  }
}

class UploadService extends BaseApiService {
  constructor() {
    super(import.meta.env.VITE_API_BASE_URL);
    this.db = null;
    this._fileHandleCache = new Map();
    this._speedTracker = new SpeedTracker();
    this._useProxy = false;  // Fallback flag: true = route blocks through backend
    this._probeFailures = 0; // Consecutive probe failures across uploads
  }

  // ── File handle cache ──
  cacheFileHandle(uploadId, file) {
    if (uploadId && file) this._fileHandleCache.set(uploadId, file);
  }
  getCachedFileHandle(uploadId) {
    return this._fileHandleCache.get(uploadId) || null;
  }
  clearCachedFileHandle(uploadId) {
    this._fileHandleCache.delete(uploadId);
  }

  // ── IndexedDB helpers ──
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

  async saveUploadMetadata(metadata) {
    const db = await this.initDB();
    await db.put(STORE_NAME, metadata);
  }

  async getUploadMetadata(uploadId) {
    const db = await this.initDB();
    return await db.get(STORE_NAME, uploadId);
  }

  async markBlockUploaded(uploadId, blockId) {
    const metadata = await this.getUploadMetadata(uploadId);
    if (metadata) {
      if (!metadata.uploadedBlocks) metadata.uploadedBlocks = [];
      if (!metadata.uploadedBlocks.includes(blockId)) {
        metadata.uploadedBlocks.push(blockId);
      }
      await this.saveUploadMetadata(metadata);
    }
  }

  async clearUploadMetadata(uploadId) {
    const db = await this.initDB();
    await db.delete(STORE_NAME, uploadId);
    this.clearCachedFileHandle(uploadId);
  }

  // ═══════════════════════════════════════════════════════════════════
  //  PROXY: Upload blocks through backend server (fallback)
  // ═══════════════════════════════════════════════════════════════════

  /**
   * Upload a single block via the backend proxy.
   * The backend forwards the block to Azure Blob Storage server-to-server.
   */
  async _proxyUploadBlock(videoId, blockIndex, blockData, uploadUrl) {
    const token = (await import('../utils/tokenManager')).default.getToken();
    const url = `${URL_CONSTANTS.UPLOAD_PROXY_BLOCK}/${videoId}/${blockIndex}`;

    for (let attempt = 0; attempt <= PROXY_MAX_RETRIES; attempt++) {
      try {
        const resp = await this.client.put(url, blockData, {
          headers: {
            'Content-Type': 'application/octet-stream',
            'X-Upload-Url': uploadUrl,
            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
          },
          timeout: 120_000, // 2 min per block
          maxContentLength: Infinity,
          maxBodyLength: Infinity,
        });
        return resp.data;
      } catch (error) {
        if (attempt < PROXY_MAX_RETRIES) {
          const delay = PROXY_RETRY_DELAY_MS * Math.pow(2, attempt);
          console.warn(`[UploadService:Proxy] block ${blockIndex} failed (attempt ${attempt + 1}), retrying in ${delay}ms...`, error.message);
          await new Promise(r => setTimeout(r, delay));
        } else {
          throw error;
        }
      }
    }
  }

  /**
   * Commit block list via the backend proxy.
   */
  async _proxyCommitBlocks(videoId, blockIds, contentType, uploadUrl) {
    const token = (await import('../utils/tokenManager')).default.getToken();
    const url = `${URL_CONSTANTS.UPLOAD_PROXY_COMMIT}/${videoId}`;

    for (let attempt = 0; attempt <= PROXY_MAX_RETRIES; attempt++) {
      try {
        const resp = await this.client.post(url, {
          block_ids: blockIds,
          content_type: contentType,
        }, {
          headers: {
            'X-Upload-Url': uploadUrl,
            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
          },
          timeout: 300_000,
        });
        return resp.data;
      } catch (error) {
        if (attempt < PROXY_MAX_RETRIES) {
          const delay = PROXY_RETRY_DELAY_MS * Math.pow(2, attempt);
          console.warn(`[UploadService:Proxy] commit failed (attempt ${attempt + 1}), retrying in ${delay}ms...`, error.message);
          await new Promise(r => setTimeout(r, delay));
        } else {
          throw error;
        }
      }
    }
  }

  /**
   * Full proxy upload: split file into blocks, upload each via backend, then commit.
   */
  async _uploadViaProxy(file, uploadUrl, uploadId, videoId, onProgress) {
    console.log(`[UploadService:Proxy] Starting proxy upload for ${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB)`);
    const contentType = this._detectContentType(file);
    const blockSize = PROXY_BLOCK_SIZE;
    const allBlockIds = [];
    let offset = 0;
    let blockIndex = 0;
    let totalBytesUploaded = 0;

    // Build block list
    while (offset < file.size) {
      const end = Math.min(offset + blockSize, file.size);
      const blockIdString = String(blockIndex).padStart(6, '0');
      const blockId = btoa(blockIdString);
      allBlockIds.push({ id: blockId, index: blockIndex, start: offset, end });
      offset = end;
      blockIndex++;
    }

    console.log(`[UploadService:Proxy] ${allBlockIds.length} blocks to upload (${(blockSize / (1024 * 1024)).toFixed(0)}MB each)`);

    // Upload blocks sequentially (proxy mode = 1 concurrent to avoid overloading backend)
    for (const block of allBlockIds) {
      const blockData = file.slice(block.start, block.end);
      const arrayBuffer = await blockData.arrayBuffer();

      try {
        await this._proxyUploadBlock(videoId, block.index, new Uint8Array(arrayBuffer), uploadUrl);
      } catch (error) {
        console.error(`[UploadService:Proxy] Block ${block.index} failed permanently`, error);
        throw wrapStageError(UPLOAD_STAGES.BLOB_PUT, error);
      }

      totalBytesUploaded += (block.end - block.start);
      const pct = Math.round((totalBytesUploaded / file.size) * 100);
      if (onProgress) onProgress(Math.min(pct, 99));
    }

    // Commit all blocks
    console.log(`[UploadService:Proxy] Committing ${allBlockIds.length} blocks...`);
    try {
      await this._proxyCommitBlocks(
        videoId,
        allBlockIds.map(b => b.id),
        contentType,
        uploadUrl
      );
      console.log(`[UploadService:Proxy] Commit successful for ${file.name}`);
    } catch (error) {
      console.error(`[UploadService:Proxy] Commit FAILED for ${file.name}`, error);
      throw wrapStageError(UPLOAD_STAGES.BLOCK_COMMIT, error);
    }

    await this.clearUploadMetadata(uploadId);
  }

  async getAllUploads() {
    const db = await this.initDB();
    return await db.getAll(STORE_NAME);
  }

  async getResumeInfo(uploadId) {
    const metadata = await this.getUploadMetadata(uploadId);
    if (!metadata) return null;
    const totalBlocks = metadata.blockIds?.length || Math.ceil((metadata.fileSize || 0) / DEFAULT_BLOCK_SIZE) || 1;
    const uploadedBlocks = metadata.uploadedBlocks?.length || 0;
    const progress = Math.round((uploadedBlocks / totalBlocks) * 100);
    const hasFileHandle = this._fileHandleCache.has(uploadId);
    return {
      fileName: metadata.fileName || 'Unknown',
      fileSize: metadata.fileSize || 0,
      progress,
      createdAt: metadata.timestamp ? new Date(metadata.timestamp) : null,
      totalBlocks,
      uploadedBlocks,
      hasFileHandle,
      videoId: metadata.videoId,
    };
  }

  // ── Retry with exponential backoff ──
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
          error.message?.includes('Timeout') ||
          error.message?.includes('AbortError') ||
          error.message?.includes('aborted') ||
          error.message?.includes('signal') ||
          error.name === 'AbortError' ||
          error.name === 'TimeoutError' ||
          error.code === 'ERR_NETWORK'
        );
        if (attempt < maxRetries && isNetworkError) {
          const delay = RETRY_DELAY_MS * Math.pow(2, attempt);
          console.warn(`[UploadService] ${label} failed (attempt ${attempt + 1}/${maxRetries + 1}), retrying in ${delay}ms...`, error.message);
          await new Promise(resolve => setTimeout(resolve, delay));
        } else {
          break;
        }
      }
    }
    throw lastError;
  }

  // ── Backend API calls ──
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

  async uploadComplete(email, video_id, filename, upload_id, language = 'ja') {
    const token = TokenManager.getToken();
    if (!token) throw new UploadStageError(UPLOAD_STAGES.AUTH, window.__t('authTokenNotFound') || 'Auth token not found');
    if (TokenManager.isTokenExpired(token)) throw new UploadStageError(UPLOAD_STAGES.AUTH, window.__t('sessionExpired') || 'Session expired');
    try {
      return await this.retryWithBackoff(
        () => this.post(URL_CONSTANTS.UPLOAD_COMPLETE, { email, video_id, filename, upload_id, language }),
        MAX_RETRIES,
        'uploadComplete'
      );
    } catch (error) {
      throw wrapStageError(UPLOAD_STAGES.UPLOAD_COMPLETE, error);
    }
  }

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

  async uploadCompleteWithType(email, video_id, filename, upload_id, upload_type = 'screen_recording', excel_product_blob_url = null, excel_trend_blob_url = null, language = 'ja') {
    const token = TokenManager.getToken();
    if (!token) throw new UploadStageError(UPLOAD_STAGES.AUTH, window.__t('authTokenNotFound') || 'Auth token not found');
    if (TokenManager.isTokenExpired(token)) throw new UploadStageError(UPLOAD_STAGES.AUTH, window.__t('sessionExpired') || 'Session expired');
    try {
      return await this.retryWithBackoff(
        () => this.post(URL_CONSTANTS.UPLOAD_COMPLETE, {
          email, video_id, filename, upload_id, upload_type,
          excel_product_blob_url, excel_trend_blob_url, language,
        }),
        MAX_RETRIES,
        'uploadCompleteWithType'
      );
    } catch (error) {
      throw wrapStageError(UPLOAD_STAGES.UPLOAD_COMPLETE, error);
    }
  }

  async checkUploadResume(user_id) {
    return await this.get(`${URL_CONSTANTS.UPLOAD_RESUME_CHECK}/${user_id}`);
  }

  async clearUserUploads(user_id) {
    return await this.delete(`${URL_CONSTANTS.UPLOADS_CLEAR}/${user_id}`);
  }

  // ── Content type detection ──
  _detectContentType(file) {
    if (file.type && file.type.startsWith('video/')) return file.type;
    const ext = file.name.toLowerCase().split('.').pop();
    const map = { mp4: 'video/mp4', webm: 'video/webm', avi: 'video/avi', mov: 'video/quicktime', mkv: 'video/x-matroska' };
    return map[ext] || 'video/mp4';
  }

  // ═══════════════════════════════════════════════════════════════════
  //  CORE: Adaptive block upload to Azure Blob Storage
  //  With automatic proxy fallback when direct upload fails
  // ═══════════════════════════════════════════════════════════════════
  async uploadToAzure(file, uploadUrl, uploadId, onProgress, startFrom = 0) {
    // ── Check if we should use proxy mode (from previous failures) ──
    if (this._useProxy || this._probeFailures >= PROBE_FAILURE_THRESHOLD) {
      const metadata = await this.getUploadMetadata(uploadId) || {};
      const videoId = metadata.videoId || uploadId;
      console.log(`[UploadService] Using PROXY mode (previous failures: ${this._probeFailures})`);
      return this._uploadViaProxy(file, uploadUrl, uploadId, videoId, onProgress);
    }

    const uploadStartTime = Date.now();
    const contentType = this._detectContentType(file);
    const blockBlobClient = new BlockBlobClient(uploadUrl);
    const tracker = this._speedTracker;

    // ── Phase 1: Probe block (1 MB) to measure initial speed ──
    const probeSize = Math.min(PROBE_BLOCK_SIZE, file.size);
    const probeId = btoa(String(0).padStart(6, '0'));
    const allBlockIds = [probeId];

    // Save initial metadata
    const existingMetadata = await this.getUploadMetadata(uploadId) || {};
    await this.saveUploadMetadata({
      uploadId, uploadUrl,
      fileName: file.name, fileSize: file.size,
      blockIds: allBlockIds,
      uploadedBlocks: existingMetadata.uploadedBlocks || [],
      contentType,
      timestamp: existingMetadata.timestamp || Date.now(),
      videoId: existingMetadata.videoId,
    });

    const uploadedSet = new Set(existingMetadata.uploadedBlocks || []);
    let totalBytesUploaded = 0;

    // Helper: upload a single block with timing
    const uploadBlock = async (blockId, data, blockSize, label) => {
      const timeout = tracker.getTimeout(blockSize);
      const t0 = Date.now();
      await this.retryWithBackoff(async () => {
        await blockBlobClient.stageBlock(blockId, data, blockSize, {
          abortSignal: AbortSignal.timeout(timeout),
        });
      }, MAX_RETRIES, label);
      const elapsed = Date.now() - t0;
      tracker.record(blockSize, elapsed);
      return elapsed;
    };

    // Serialize metadata writes
    let writeQueue = Promise.resolve();
    const safeMarkUploaded = async (blockId) => {
      writeQueue = writeQueue.then(() => this.markBlockUploaded(uploadId, blockId));
      return writeQueue;
    };

    // ── Upload probe block (with proxy fallback) ──
    if (!uploadedSet.has(probeId) && startFrom === 0) {
      try {
        const probeData = file.slice(0, probeSize);
        const probeMs = await uploadBlock(probeId, probeData, probeSize, 'probe');
        await safeMarkUploaded(probeId);
        uploadedSet.add(probeId);
        totalBytesUploaded += probeSize;
        // Reset probe failure counter on success
        this._probeFailures = 0;

        const probeMBps = ((probeSize / (1024 * 1024)) / (probeMs / 1000)).toFixed(2);
        console.log(`[UploadService] Probe: ${probeSize / 1024}KB in ${probeMs}ms (${probeMBps} MB/s)`);
      } catch (probeError) {
        // Probe failed after all retries → switch to proxy mode
        this._probeFailures++;
        console.error(
          `[UploadService] Direct upload probe FAILED (failure #${this._probeFailures}). ` +
          `Switching to server proxy mode.`, probeError.message
        );
        this._useProxy = true;
        const videoId = existingMetadata.videoId || uploadId;
        return this._uploadViaProxy(file, uploadUrl, uploadId, videoId, onProgress);
      }
    } else {
      // Probe already uploaded (resume) – assume default speed initially
      totalBytesUploaded += probeSize;
    }

    // ── Phase 2: Determine adaptive block size from probe ──
    let adaptiveBlockSize = tracker.getOptimalBlockSize();
    console.log(`[UploadService] Adaptive block size: ${(adaptiveBlockSize / (1024 * 1024)).toFixed(1)} MB (speed: ${tracker.getSpeed() ? (tracker.getSpeed() / (1024 * 1024)).toFixed(2) + ' MB/s' : 'unknown'})`);

    // ── Phase 3: Build remaining block list with adaptive size ──
    const remainingBytes = file.size - probeSize;
    const remainingBlocks = [];
    let offset = probeSize;
    let blockIndex = 1;

    while (offset < file.size) {
      // Re-evaluate block size periodically (every 5 blocks)
      if (blockIndex > 1 && blockIndex % 5 === 0) {
        const newSize = tracker.getOptimalBlockSize();
        if (newSize !== adaptiveBlockSize) {
          console.log(`[UploadService] Block size adjusted: ${(adaptiveBlockSize / (1024 * 1024)).toFixed(1)}MB → ${(newSize / (1024 * 1024)).toFixed(1)}MB`);
          adaptiveBlockSize = newSize;
        }
      }

      const end = Math.min(offset + adaptiveBlockSize, file.size);
      const blockIdString = String(blockIndex).padStart(6, '0');
      const blockId = btoa(blockIdString);
      allBlockIds.push(blockId);
      remainingBlocks.push({ index: blockIndex, id: blockId, start: offset, end });
      offset = end;
      blockIndex++;
    }

    // Update metadata with full block list
    await this.saveUploadMetadata({
      ...(await this.getUploadMetadata(uploadId)),
      blockIds: allBlockIds,
    });

    const totalBlocks = allBlockIds.length;
    const updateProgress = () => {
      const pct = Math.round((totalBytesUploaded / file.size) * 100);
      if (onProgress) onProgress(Math.min(pct, 99)); // cap at 99 until commit
    };
    updateProgress();

    // ── Phase 4: Upload remaining blocks concurrently ──
    const pendingBlocks = remainingBlocks.filter(
      (block) => block.index >= startFrom && !uploadedSet.has(block.id)
    );

    if (pendingBlocks.length > 0) {
      let nextIdx = 0;

      const uploadWorker = async () => {
        while (true) {
          const cur = nextIdx++;
          if (cur >= pendingBlocks.length) break;
          const block = pendingBlocks[cur];
          const blockSize = block.end - block.start;

          try {
            await uploadBlock(block.id, file.slice(block.start, block.end), blockSize, `block[${block.index}]`);
          } catch (error) {
            // Block failed after all retries → switch to proxy mode for remaining blocks
            console.error(`[UploadService] Block ${block.index}/${totalBlocks} failed. Switching to proxy mode for remaining blocks.`, error);
            this._useProxy = true;
            this._probeFailures++;
            const metadata = await this.getUploadMetadata(uploadId) || {};
            const videoId = metadata.videoId || uploadId;
            // Upload remaining file via proxy (from current offset)
            return this._uploadViaProxy(file, uploadUrl, uploadId, videoId, onProgress);
          }

          await safeMarkUploaded(block.id);
          uploadedSet.add(block.id);
          totalBytesUploaded += blockSize;
          updateProgress();
        }
      };

      const workerCount = Math.min(MAX_CONCURRENT_UPLOADS, pendingBlocks.length);
      await Promise.all(Array.from({ length: workerCount }, uploadWorker));
    }

    const blockUploadDuration = ((Date.now() - uploadStartTime) / 1000).toFixed(1);
    const avgSpeedMBps = (file.size / (1024 * 1024)) / (blockUploadDuration || 1);
    console.log(`[UploadService] All ${totalBlocks} blocks uploaded in ${blockUploadDuration}s (avg ${avgSpeedMBps.toFixed(1)} MB/s, final block size: ${(adaptiveBlockSize / (1024 * 1024)).toFixed(1)}MB)`);

    // ── Phase 5: Commit ──
    console.log(`[UploadService] Committing ${allBlockIds.length} blocks (${(file.size / (1024 * 1024)).toFixed(1)} MB)...`);
    try {
      await this.retryWithBackoff(async () => {
        await blockBlobClient.commitBlockList(allBlockIds, {
          blobHTTPHeaders: {
            blobContentType: contentType,
            blobCacheControl: 'public, max-age=3600',
          },
          abortSignal: AbortSignal.timeout(FINALIZE_TIMEOUT_MS),
        });
      }, MAX_RETRIES, 'commitBlockList');
      console.log(`[UploadService] Block commit successful for ${file.name}`);
    } catch (error) {
      console.error(`[UploadService] Block commit FAILED for ${file.name}`, error);
      throw wrapStageError(UPLOAD_STAGES.BLOCK_COMMIT, error);
    }

    await this.clearUploadMetadata(uploadId);
  }

  // ── High-level upload workflows ──

  async uploadFile(file, email, onProgress, onUploadInit, language = 'ja') {
    const { video_id, upload_id, upload_url } = await this.generateUploadUrl(email, file.name);
    if (onUploadInit) onUploadInit({ uploadId: upload_id, videoId: video_id });
    this.cacheFileHandle(upload_id, file);
    await this.saveUploadMetadata({
      uploadId: upload_id, uploadUrl: upload_url, videoId: video_id,
      fileName: file.name, fileSize: file.size,
      blockIds: [], uploadedBlocks: [],
      contentType: 'video/mp4', timestamp: Date.now(),
    });
    await this.uploadToAzure(file, upload_url, upload_id, onProgress);
    await this.uploadComplete(email, video_id, file.name, upload_id, language);
    return video_id;
  }

  async uploadCleanVideo(videoFile, productExcel, trendExcel, email, onProgress, onUploadInit, language = 'ja') {
    const { video_id, upload_id, upload_url } = await this.generateUploadUrl(email, videoFile.name);
    if (onUploadInit) onUploadInit({ uploadId: upload_id, videoId: video_id });
    this.cacheFileHandle(upload_id, videoFile);
    await this.saveUploadMetadata({
      uploadId: upload_id, uploadUrl: upload_url, videoId: video_id,
      fileName: videoFile.name, fileSize: videoFile.size,
      blockIds: [], uploadedBlocks: [],
      contentType: 'video/mp4', timestamp: Date.now(),
      uploadMode: 'clean_video',
    });

    // Video upload (0-80%)
    await this.uploadToAzure(videoFile, upload_url, upload_id, (pct) => {
      if (onProgress) onProgress(Math.round(pct * 0.8));
    });

    // Excel uploads (80-95%)
    let product_blob_url = null;
    let trend_blob_url = null;
    if (productExcel && trendExcel) {
      if (onProgress) onProgress(82);
      const excelUrls = await this.generateExcelUploadUrls(email, video_id, productExcel.name, trendExcel.name);
      if (onProgress) onProgress(85);
      await this.uploadExcelToAzure(productExcel, excelUrls.product_upload_url);
      product_blob_url = excelUrls.product_blob_url;
      if (onProgress) onProgress(90);
      await this.uploadExcelToAzure(trendExcel, excelUrls.trend_upload_url);
      trend_blob_url = excelUrls.trend_blob_url;
      if (onProgress) onProgress(95);
      await this.saveUploadMetadata({
        uploadId: upload_id, uploadUrl: upload_url, videoId: video_id,
        fileName: videoFile.name, fileSize: videoFile.size,
        contentType: 'video/mp4', timestamp: Date.now(),
        uploadMode: 'clean_video',
        excelProductBlobUrl: product_blob_url, excelTrendBlobUrl: trend_blob_url,
      });
    }

    await this.uploadCompleteWithType(email, video_id, videoFile.name, upload_id, 'clean_video', product_blob_url, trend_blob_url, language);
    if (onProgress) onProgress(100);
    return video_id;
  }

  async batchUploadCleanVideos(videoItems, productExcel, trendExcel, email, onProgress, onUploadInit, language = 'ja') {
    const totalVideos = videoItems.length;
    const videoProgressShare = 75;
    const excelProgressShare = 15;

    const uploadedVideos = [];
    for (let i = 0; i < totalVideos; i++) {
      const { file, timeOffsetSeconds } = videoItems[i];
      const { video_id, upload_id, upload_url } = await this.generateUploadUrl(email, file.name);
      if (i === 0 && onUploadInit) onUploadInit({ uploadId: upload_id, videoId: video_id });
      this.cacheFileHandle(upload_id, file);
      await this.saveUploadMetadata({
        uploadId: upload_id, uploadUrl: upload_url, videoId: video_id,
        fileName: file.name, fileSize: file.size,
        blockIds: [], uploadedBlocks: [],
        contentType: 'video/mp4', timestamp: Date.now(),
        uploadMode: 'clean_video',
      });
      const baseProgress = (i / totalVideos) * videoProgressShare;
      const perVideoShare = videoProgressShare / totalVideos;
      await this.uploadToAzure(file, upload_url, upload_id, (pct) => {
        const overall = baseProgress + (pct / 100) * perVideoShare;
        if (onProgress) onProgress(Math.round(overall));
      });
      uploadedVideos.push({ video_id, upload_id, filename: file.name, timeOffsetSeconds });
    }

    let product_blob_url = null;
    let trend_blob_url = null;
    if (productExcel && trendExcel && uploadedVideos.length > 0) {
      if (onProgress) onProgress(videoProgressShare + 2);
      const excelUrls = await this.generateExcelUploadUrls(email, uploadedVideos[0].video_id, productExcel.name, trendExcel.name);
      if (onProgress) onProgress(videoProgressShare + 5);
      await this.uploadExcelToAzure(productExcel, excelUrls.product_upload_url);
      product_blob_url = excelUrls.product_blob_url;
      if (onProgress) onProgress(videoProgressShare + 10);
      await this.uploadExcelToAzure(trendExcel, excelUrls.trend_upload_url);
      trend_blob_url = excelUrls.trend_blob_url;
      if (onProgress) onProgress(videoProgressShare + excelProgressShare);
    }

    const batchPayload = {
      email,
      videos: uploadedVideos.map(v => ({
        video_id: v.video_id, filename: v.filename,
        upload_id: v.upload_id, time_offset_seconds: v.timeOffsetSeconds || 0,
      })),
      excel_product_blob_url: product_blob_url,
      excel_trend_blob_url: trend_blob_url,
      language,
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
