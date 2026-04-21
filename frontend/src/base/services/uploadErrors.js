/**
 * Upload Stage Error
 * ==================
 * Custom error class that carries pipeline stage information for
 * Upload Observability.  Every upload failure now includes:
 *   - stage:      which pipeline step failed
 *   - statusCode: HTTP status (if applicable)
 *   - detail:     raw error detail from server / SDK
 *
 * The UI (MainContent.jsx) uses `stage` to show a user-friendly
 * Japanese message that tells the user WHICH step failed and
 * what to try next.
 */

// ---------------------------------------------------------------------------
// Upload Stage Constants (frontend mirror of backend UploadStage)
// ---------------------------------------------------------------------------
export const UPLOAD_STAGES = Object.freeze({
  SAS_GENERATE:     'sas_generate',       // Step 1: Generate SAS upload URL
  BLOB_PUT:         'blob_put',           // Step 2: Upload blocks to Azure Blob
  BLOCK_COMMIT:     'block_commit',       // Step 3: Commit block list
  EXCEL_SAS:        'excel_sas',          // Step 3b: Generate Excel SAS URLs
  EXCEL_UPLOAD:     'excel_upload',       // Step 3c: Upload Excel files
  UPLOAD_COMPLETE:  'upload_complete',    // Step 4: Notify backend (DB + Queue)
  BATCH_COMPLETE:   'batch_complete',     // Step 4b: Batch upload complete
  AUTH:             'auth',               // Auth token missing / expired
  METADATA:         'metadata',           // IndexedDB metadata error
  VALIDATION:       'validation',         // File validation error
});

// ---------------------------------------------------------------------------
// Custom Error Class
// ---------------------------------------------------------------------------
export class UploadStageError extends Error {
  /**
   * @param {string} stage      - One of UPLOAD_STAGES values
   * @param {string} message    - Human-readable error message
   * @param {object} [options]
   * @param {number} [options.statusCode]  - HTTP status code (if applicable)
   * @param {string} [options.detail]      - Raw error detail
   * @param {Error}  [options.cause]       - Original error
   * @param {string} [options.failedStage] - Backend failed_stage (from pipeline response)
   */
  constructor(stage, message, options = {}) {
    super(message);
    this.name = 'UploadStageError';
    this.stage = stage;
    this.statusCode = options.statusCode || null;
    this.detail = options.detail || null;
    this.cause = options.cause || null;
    this.failedStage = options.failedStage || null;
    this.timestamp = new Date().toISOString();
  }
}

// ---------------------------------------------------------------------------
// Stage-specific Japanese error messages
// ---------------------------------------------------------------------------
const STAGE_MESSAGES = {
  [UPLOAD_STAGES.SAS_GENERATE]: {
    label: 'アップロードURL生成',
    userMessage: 'アップロードURLの生成に失敗しました。サーバーとの接続を確認してください。',
    retryHint: 'ページを再読み込みしてから再度お試しください。',
  },
  [UPLOAD_STAGES.BLOB_PUT]: {
    label: 'ファイル転送',
    userMessage: 'ファイルのアップロード中にエラーが発生しました。',
    retryHint: '安定したネットワーク環境で再度お試しください。アップロードは途中から再開できます。',
  },
  [UPLOAD_STAGES.BLOCK_COMMIT]: {
    label: 'ファイル確定',
    userMessage: 'アップロードしたファイルの確定処理に失敗しました。',
    retryHint: 'もう一度アップロードをお試しください。',
  },
  [UPLOAD_STAGES.EXCEL_SAS]: {
    label: 'Excel URL生成',
    userMessage: 'ExcelファイルのアップロードURL生成に失敗しました。',
    retryHint: 'ページを再読み込みしてから再度お試しください。',
  },
  [UPLOAD_STAGES.EXCEL_UPLOAD]: {
    label: 'Excelアップロード',
    userMessage: 'Excelファイルのアップロードに失敗しました。',
    retryHint: 'ファイルが正しい形式か確認してから再度お試しください。',
  },
  [UPLOAD_STAGES.UPLOAD_COMPLETE]: {
    label: 'アップロード完了処理',
    userMessage: 'アップロード完了の通知に失敗しました。',
    retryHint: 'サーバーが一時的に利用できない可能性があります。しばらく待ってから再度お試しください。',
  },
  [UPLOAD_STAGES.BATCH_COMPLETE]: {
    label: 'バッチアップロード完了処理',
    userMessage: 'バッチアップロード完了の通知に失敗しました。',
    retryHint: 'サーバーが一時的に利用できない可能性があります。しばらく待ってから再度お試しください。',
  },
  [UPLOAD_STAGES.AUTH]: {
    label: '認証',
    userMessage: '認証エラーが発生しました。セッションが期限切れの可能性があります。',
    retryHint: '再度ログインしてからお試しください。',
  },
  [UPLOAD_STAGES.METADATA]: {
    label: 'メタデータ',
    userMessage: 'アップロードのメタデータが見つかりません。',
    retryHint: '新しいアップロードを開始してください。',
  },
  [UPLOAD_STAGES.VALIDATION]: {
    label: 'ファイル検証',
    userMessage: 'ファイルの検証に失敗しました。',
    retryHint: '正しいファイルを選択してから再度お試しください。',
  },
};

// ---------------------------------------------------------------------------
// Helper: Extract HTTP status from Axios / Azure SDK errors
// ---------------------------------------------------------------------------
export function extractStatusCode(error) {
  // Axios error
  if (error?.response?.status) return error.response.status;
  // Azure SDK error
  if (error?.statusCode) return error.statusCode;
  // Nested cause
  if (error?.cause?.response?.status) return error.cause.response.status;
  return null;
}

// ---------------------------------------------------------------------------
// Helper: Extract detail message from various error shapes
// ---------------------------------------------------------------------------
export function extractDetail(error) {
  // Axios error with response body
  if (error?.response?.data?.detail) return error.response.data.detail;
  if (error?.response?.data?.message) return error.response.data.message;
  // Azure SDK
  if (error?.details?.message) return error.details.message;
  // Generic
  return error?.message || String(error);
}

// ---------------------------------------------------------------------------
// Helper: Wrap any error into UploadStageError
// ---------------------------------------------------------------------------
export function wrapStageError(stage, error) {
  if (error instanceof UploadStageError) return error;

  const statusCode = extractStatusCode(error);
  const detail = extractDetail(error);

  // Detect auth errors regardless of stage
  if (statusCode === 401 || statusCode === 403) {
    return new UploadStageError(UPLOAD_STAGES.AUTH, detail, {
      statusCode,
      detail,
      cause: error,
    });
  }

  return new UploadStageError(stage, detail, {
    statusCode,
    detail,
    cause: error,
  });
}

// ---------------------------------------------------------------------------
// Public: Format user-facing error message
// ---------------------------------------------------------------------------
/**
 * Build a user-facing error message from an UploadStageError.
 *
 * Format:
 *   【{stage_label}】{userMessage}
 *   {retryHint}
 *   (エラー詳細: {detail} / HTTP {statusCode})
 *
 * For non-UploadStageError, falls back to generic message.
 */
export function formatUploadError(error) {
  if (error instanceof UploadStageError) {
    const info = STAGE_MESSAGES[error.stage];
    if (!info) {
      // Unknown stage – show raw message
      return `アップロードエラー: ${error.message}`;
    }

    // Detect timeout / abort errors and provide clearer message
    const isAbortTimeout = (
      error.detail?.includes('signal is aborted') ||
      error.detail?.includes('AbortError') ||
      error.cause?.name === 'AbortError' ||
      error.cause?.name === 'TimeoutError'
    );

    let msg = `【${info.label}】`;
    if (isAbortTimeout) {
      msg += 'ネットワーク接続が不安定なため、アップロードがタイムアウトしました。Wi-Fi環境を確認して再度お試しください。';
    } else {
      msg += info.userMessage;
    }

    // Add HTTP status info for debugging
    const debugParts = [];
    if (error.statusCode) debugParts.push(`HTTP ${error.statusCode}`);
    if (error.detail && error.detail !== info.userMessage && !isAbortTimeout) {
      // Truncate very long details
      const truncated = error.detail.length > 120
        ? error.detail.substring(0, 120) + '…'
        : error.detail;
      debugParts.push(truncated);
    }

    if (debugParts.length > 0) {
      msg += `\n(${debugParts.join(' / ')})`;
    }

    return msg;
  }

  // Fallback for non-UploadStageError
  const rawMsg = error?.message || String(error);

  if (rawMsg.includes('signal is aborted') || rawMsg.includes('AbortError') || error?.name === 'AbortError' || error?.name === 'TimeoutError') {
    return 'アップロードがタイムアウトしました。ネットワーク接続が不安定な可能性があります。Wi-Fi環境を確認して再度お試しください。';
  }
  if (rawMsg.includes('Failed to fetch') || rawMsg.includes('Network') || rawMsg.includes('sending request')) {
    return 'ネットワークエラーが発生しました。インターネット接続を確認して、もう一度お試しください。';
  }
  if (rawMsg.includes('timeout') || rawMsg.includes('Timeout')) {
    return 'アップロードがタイムアウトしました。安定したネットワーク環境でお試しください。';
  }

  return rawMsg || 'アップロードに失敗しました。もう一度お試しください。';
}

// ---------------------------------------------------------------------------
// Public: Log upload error with full diagnostics (console)
// ---------------------------------------------------------------------------
export function logUploadError(context, error) {
  const timestamp = new Date().toISOString();
  const stage = error instanceof UploadStageError ? error.stage : 'unknown';
  const statusCode = error instanceof UploadStageError ? error.statusCode : extractStatusCode(error);
  const detail = error instanceof UploadStageError ? error.detail : extractDetail(error);

  console.error(
    `[Upload Observability] ${timestamp}\n` +
    `  Context: ${context}\n` +
    `  Stage: ${stage}\n` +
    `  Status: ${statusCode || 'N/A'}\n` +
    `  Detail: ${detail}\n` +
    `  Raw:`, error
  );

  // Also store in sessionStorage for diagnostic mode
  try {
    const logs = JSON.parse(sessionStorage.getItem('upload_error_logs') || '[]');
    logs.push({
      timestamp,
      context,
      stage,
      statusCode,
      detail: typeof detail === 'string' ? detail.substring(0, 500) : String(detail),
    });
    // Keep last 20 errors
    if (logs.length > 20) logs.splice(0, logs.length - 20);
    sessionStorage.setItem('upload_error_logs', JSON.stringify(logs));
  } catch {
    // sessionStorage may be full or unavailable
  }
}
