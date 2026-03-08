/**
 * runtimeErrorLogger.js
 *
 * グローバルランタイムエラーログ基盤
 *
 * 機能:
 *   - window.onerror / unhandledrejection をキャプチャ
 *   - sessionStorage に最新20件を保存
 *   - console にフォーマット済みログを出力
 *   - 開発者ツールで sessionStorage.getItem('aitherhub_runtime_errors') で確認可能
 *
 * 使い方:
 *   main.jsx で import するだけ:
 *   import './base/utils/runtimeErrorLogger';
 */

const STORAGE_KEY = "aitherhub_runtime_errors";
const MAX_ENTRIES = 20;

/**
 * エラーログを sessionStorage に保存
 */
function saveErrorLog(entry) {
  try {
    let logs = [];
    try {
      logs = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
    } catch { /* ignore */ }
    logs.unshift(entry);
    if (logs.length > MAX_ENTRIES) logs.length = MAX_ENTRIES;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(logs));
  } catch { /* storage full or unavailable - ignore */ }
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
 * ユーティリティ: エラーログをクリア
 * console で呼べる: window.__clearErrorLogs()
 */
window.__clearErrorLogs = function () {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
    console.info("[AitherHub] Error logs cleared.");
  } catch { /* ignore */ }
};

console.info("[AitherHub] Runtime error logger initialized. Use window.__getErrorLogs() to view logs.");
