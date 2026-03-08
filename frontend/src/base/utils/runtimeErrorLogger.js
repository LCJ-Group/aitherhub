/**
 * runtimeErrorLogger.js
 *
 * グローバルランタイムエラーログ基盤
 *
 * 機能:
 *   - window.onerror / unhandledrejection をキャプチャ
 *   - sessionStorage に最新50件を保存
 *   - console にフォーマット済みログを出力
 *   - セクション単位のエラーログ (logSectionError) でvideo_id/section_name/endpoint紐付け
 *   - request_id 生成でフロントエンド↔バックエンドのログ相関
 *   - 開発者ツールで sessionStorage.getItem('aitherhub_runtime_errors') で確認可能
 *
 * 使い方:
 *   main.jsx で import するだけ:
 *   import './base/utils/runtimeErrorLogger';
 *
 *   セクションエラーログ:
 *   import { logSectionError, generateRequestId } from './base/utils/runtimeErrorLogger';
 */

const STORAGE_KEY = "aitherhub_runtime_errors";
const SECTION_ERROR_KEY = "aitherhub_section_errors";
const MAX_ENTRIES = 50;

/**
 * request_id を生成
 * フォーマット: "fe-{timestamp}-{random}"
 * バックエンドに X-Request-Id ヘッダーで送信し、ログ相関に使う
 */
export function generateRequestId() {
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).substring(2, 8);
  return `fe-${ts}-${rand}`;
}

/**
 * エラーログを sessionStorage に保存
 */
function saveErrorLog(entry, storageKey = STORAGE_KEY) {
  try {
    let logs = [];
    try {
      logs = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
    } catch { /* ignore */ }
    logs.unshift(entry);
    if (logs.length > MAX_ENTRIES) logs.length = MAX_ENTRIES;
    sessionStorage.setItem(storageKey, JSON.stringify(logs));
  } catch { /* storage full or unavailable - ignore */ }
}

/**
 * logSectionError - セクション単位のAPIエラーを構造化ログとして記録
 *
 * @param {Object} params
 * @param {string} params.sectionName - セクション名 (e.g., "MomentClips", "HookDetection")
 * @param {string} params.videoId - 動画ID
 * @param {string} params.endpoint - APIエンドポイント (e.g., "/api/videos/{id}/moments")
 * @param {string} params.errorType - エラータイプ (auth/not_found/timeout/server/network/parse/unknown)
 * @param {string} params.errorMessage - エラーメッセージ
 * @param {number|null} params.httpStatus - HTTPステータスコード
 * @param {string} params.requestId - リクエストID (フロント↔バックエンド相関用)
 */
export function logSectionError({
  sectionName = "",
  videoId = "",
  endpoint = "",
  errorType = "unknown",
  errorMessage = "",
  httpStatus = null,
  requestId = "",
}) {
  const entry = {
    timestamp: new Date().toISOString(),
    type: "section_api_error",
    sectionName,
    videoId: videoId || window.location.pathname.match(/\/video\/([^/]+)/)?.[1] || "",
    endpoint,
    errorType,
    errorMessage,
    httpStatus,
    requestId,
    url: window.location.href,
  };

  console.error(
    `[AitherHub Section Error] ${sectionName}\n` +
    `  Type: ${errorType} | HTTP: ${httpStatus || "N/A"}\n` +
    `  Message: ${errorMessage}\n` +
    `  Video: ${entry.videoId || "N/A"}\n` +
    `  Endpoint: ${endpoint || "N/A"}\n` +
    `  RequestId: ${requestId || "N/A"}`
  );

  // セクションエラー専用ストレージに保存
  saveErrorLog(entry, SECTION_ERROR_KEY);
  // 汎用エラーログにも保存
  saveErrorLog(entry, STORAGE_KEY);
}

/**
 * logBoundaryError - SectionErrorBoundary用のクラッシュログ
 *
 * @param {Object} params
 * @param {string} params.sectionName - セクション名
 * @param {Error} params.error - エラーオブジェクト
 * @param {Object} params.errorInfo - React errorInfo
 */
export function logBoundaryError({ sectionName, error, errorInfo }) {
  const entry = {
    timestamp: new Date().toISOString(),
    type: "component_crash",
    sectionName: sectionName || "Unknown",
    error: error?.message || String(error),
    stack: error?.stack || "",
    componentStack: errorInfo?.componentStack || "",
    url: window.location.href,
    videoId: window.location.pathname.match(/\/video\/([^/]+)/)?.[1] || "",
  };

  console.error(
    `[AitherHub Component Crash] ${entry.sectionName}\n` +
    `  Error: ${entry.error}\n` +
    `  Video: ${entry.videoId || "N/A"}\n` +
    `  URL: ${entry.url}`
  );

  saveErrorLog(entry, STORAGE_KEY);
}

/**
 * window.onerror - 同期エラーをキャプチャ
 */
window.addEventListener("error", (event) => {
  const entry = {
    timestamp: new Date().toISOString(),
    type: "runtime_error",
    message: event.message || "Unknown error",
    filename: event.filename || "",
    lineno: event.lineno || 0,
    colno: event.colno || 0,
    stack: event.error?.stack || "",
    url: window.location.href,
    videoId: window.location.pathname.match(/\/video\/([^/]+)/)?.[1] || "",
  };

  console.error(
    `[AitherHub Runtime Error] ${entry.message}\n` +
    `  File: ${entry.filename}:${entry.lineno}:${entry.colno}\n` +
    `  Video: ${entry.videoId || "N/A"}\n` +
    `  URL: ${entry.url}`
  );

  saveErrorLog(entry);
});

/**
 * unhandledrejection - Promise rejection をキャプチャ
 */
window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  const entry = {
    timestamp: new Date().toISOString(),
    type: "unhandled_promise_rejection",
    message: reason?.message || String(reason) || "Unknown rejection",
    stack: reason?.stack || "",
    url: window.location.href,
    videoId: window.location.pathname.match(/\/video\/([^/]+)/)?.[1] || "",
  };

  console.error(
    `[AitherHub Unhandled Rejection] ${entry.message}\n` +
    `  Video: ${entry.videoId || "N/A"}\n` +
    `  URL: ${entry.url}`
  );

  saveErrorLog(entry);
});

/**
 * ユーティリティ: エラーログを取得
 * console で呼べる: window.__getErrorLogs()
 */
window.__getErrorLogs = function () {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
};

/**
 * ユーティリティ: セクションエラーログを取得
 * console で呼べる: window.__getSectionErrors()
 */
window.__getSectionErrors = function () {
  try {
    return JSON.parse(sessionStorage.getItem(SECTION_ERROR_KEY) || "[]");
  } catch {
    return [];
  }
};

/**
 * ユーティリティ: エラーログをクリア
 * console で呼べる: window.__clearErrorLogs()
 */
window.__clearErrorLogs = function () {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(SECTION_ERROR_KEY);
    console.info("[AitherHub] Error logs cleared.");
  } catch { /* ignore */ }
};

console.info(
  "[AitherHub] Runtime error logger initialized.\n" +
  "  window.__getErrorLogs()    - 全エラーログ\n" +
  "  window.__getSectionErrors() - セクションAPIエラーログ\n" +
  "  window.__clearErrorLogs()   - ログクリア"
);
