/**
 * safeFetch - 共通 API client wrapper
 *
 * 目的:
 *   - API が壊れても React が落ちない層を作る
 *   - 全APIコールで統一された状態管理 (idle/loading/success/empty/error) を実現
 *   - timeout / status code / JSON parse safety / schema validation を一元管理
 *
 * 使い方:
 *   const { data, error, status } = await safeFetch(() => VideoService.getMomentClips(id));
 *
 *   if (error) {
 *     // error.type: 'network' | 'timeout' | 'auth' | 'not_found' | 'server' | 'parse' | 'unknown'
 *     // error.message: 日本語エラーメッセージ
 *     // error.status: HTTP status code (if available)
 *   }
 */

/**
 * @typedef {'idle' | 'loading' | 'success' | 'empty' | 'error'} ApiState
 */

/**
 * @typedef {Object} SafeFetchResult
 * @property {any} data - レスポンスデータ (成功時)
 * @property {Object|null} error - エラー情報 (失敗時)
 * @property {string} error.type - エラータイプ
 * @property {string} error.message - 日本語エラーメッセージ
 * @property {number|null} error.status - HTTPステータスコード
 * @property {any} error.raw - 元のエラーオブジェクト
 * @property {ApiState} state - API状態
 */

const ERROR_MESSAGES = {
  network: "ネットワークに接続できません。接続を確認してください。",
  timeout: "リクエストがタイムアウトしました。しばらくしてから再試行してください。",
  auth: "認証エラーが発生しました。再ログインしてください。",
  not_found: "データが見つかりません。",
  server: "サーバーエラーが発生しました。しばらくしてから再試行してください。",
  parse: "レスポンスの解析に失敗しました。",
  unknown: "予期しないエラーが発生しました。",
  rate_limit: "リクエストが多すぎます。しばらくしてから再試行してください。",
};

/**
 * HTTPステータスコードからエラータイプを判定
 */
function classifyHttpError(status) {
  if (status === 401 || status === 403) return "auth";
  if (status === 404) return "not_found";
  if (status === 408 || status === 504) return "timeout";
  if (status === 413) return "server"; // payload too large
  if (status === 429) return "rate_limit";
  if (status >= 500) return "server";
  return "unknown";
}

/**
 * エラーオブジェクトからエラータイプを判定
 */
function classifyError(err) {
  if (!err) return "unknown";

  // Axios error
  if (err.response) {
    return classifyHttpError(err.response.status);
  }

  // Network error (no response)
  if (err.code === "ERR_NETWORK" || err.message === "Network Error") {
    return "network";
  }

  // Timeout
  if (
    err.code === "ECONNABORTED" ||
    err.code === "ERR_CANCELED" ||
    err.message?.includes("timeout")
  ) {
    return "timeout";
  }

  // JSON parse error
  if (err instanceof SyntaxError && err.message?.includes("JSON")) {
    return "parse";
  }

  // TypeError (e.g., cannot read property of undefined)
  if (err instanceof TypeError) {
    return "parse";
  }

  return "unknown";
}

/**
 * safeFetch - APIコールを安全にラップする
 *
 * @param {Function} apiFn - API呼び出し関数 (async)
 * @param {Object} options
 * @param {number} options.timeout - タイムアウト (ms), default 30000
 * @param {Function} options.validate - レスポンスバリデーション関数 (data) => boolean
 * @param {any} options.defaultValue - エラー時のデフォルト値
 * @returns {Promise<SafeFetchResult>}
 */
export async function safeFetch(apiFn, options = {}) {
  const { timeout = 30000, validate, defaultValue = null } = options;

  try {
    // タイムアウト付きで実行
    let result;
    if (timeout > 0) {
      const timeoutPromise = new Promise((_, reject) => {
        setTimeout(() => reject(new Error("timeout")), timeout);
      });
      result = await Promise.race([apiFn(), timeoutPromise]);
    } else {
      result = await apiFn();
    }

    // null / undefined チェック
    if (result === null || result === undefined) {
      return { data: defaultValue, error: null, state: "empty" };
    }

    // バリデーション
    if (typeof validate === "function") {
      if (!validate(result)) {
        return {
          data: defaultValue,
          error: {
            type: "parse",
            message: ERROR_MESSAGES.parse,
            status: null,
            raw: new Error("Response validation failed"),
          },
          state: "error",
        };
      }
    }

    // 空チェック
    const isEmpty =
      (Array.isArray(result) && result.length === 0) ||
      (result && typeof result === "object" && !Array.isArray(result) && Object.keys(result).length === 0);

    return {
      data: result,
      error: null,
      state: isEmpty ? "empty" : "success",
    };
  } catch (err) {
    const errorType = classifyError(err);
    const status = err?.response?.status || null;

    return {
      data: defaultValue,
      error: {
        type: errorType,
        message: ERROR_MESSAGES[errorType] || ERROR_MESSAGES.unknown,
        status,
        raw: err,
      },
      state: "error",
    };
  }
}

/**
 * useApiState - API状態管理用のカスタムフック
 *
 * 使い方:
 *   const { state, data, error, execute, retry } = useApiState();
 *
 *   useEffect(() => {
 *     execute(async () => VideoService.getMomentClips(id));
 *   }, [id]);
 *
 *   if (state === 'loading') return <Spinner />;
 *   if (state === 'error') return <ErrorUI error={error} onRetry={retry} />;
 *   if (state === 'empty') return <EmptyUI />;
 *   return <DataUI data={data} />;
 */
export function createApiStateHelpers() {
  return {
    idle: { state: "idle", data: null, error: null },
    loading: { state: "loading", data: null, error: null },
    success: (data) => ({ state: "success", data, error: null }),
    empty: { state: "empty", data: null, error: null },
    error: (error) => ({ state: "error", data: null, error }),
  };
}

/**
 * SectionErrorUI - セクション内のAPIエラー表示用コンポーネント
 * (React import不要 - 呼び出し側でJSXとして使う)
 */
export function renderSectionError({ error, onRetry, sectionName }) {
  const message = error?.message || ERROR_MESSAGES.unknown;
  const errorType = error?.type || "unknown";

  // エラータイプ別のアイコンと色
  const styles = {
    network: { bg: "bg-yellow-50", border: "border-yellow-200", text: "text-yellow-700", icon: "text-yellow-500", btnBg: "bg-yellow-100", btnHover: "hover:bg-yellow-200", btnText: "text-yellow-600" },
    timeout: { bg: "bg-yellow-50", border: "border-yellow-200", text: "text-yellow-700", icon: "text-yellow-500", btnBg: "bg-yellow-100", btnHover: "hover:bg-yellow-200", btnText: "text-yellow-600" },
    auth: { bg: "bg-orange-50", border: "border-orange-200", text: "text-orange-700", icon: "text-orange-500", btnBg: "bg-orange-100", btnHover: "hover:bg-orange-200", btnText: "text-orange-600" },
    not_found: { bg: "bg-gray-50", border: "border-gray-200", text: "text-gray-600", icon: "text-gray-400", btnBg: "bg-gray-100", btnHover: "hover:bg-gray-200", btnText: "text-gray-600" },
    server: { bg: "bg-red-50", border: "border-red-200", text: "text-red-700", icon: "text-red-500", btnBg: "bg-red-100", btnHover: "hover:bg-red-200", btnText: "text-red-600" },
    parse: { bg: "bg-red-50", border: "border-red-200", text: "text-red-700", icon: "text-red-500", btnBg: "bg-red-100", btnHover: "hover:bg-red-200", btnText: "text-red-600" },
    unknown: { bg: "bg-gray-50", border: "border-gray-200", text: "text-gray-600", icon: "text-gray-400", btnBg: "bg-gray-100", btnHover: "hover:bg-gray-200", btnText: "text-gray-600" },
    rate_limit: { bg: "bg-yellow-50", border: "border-yellow-200", text: "text-yellow-700", icon: "text-yellow-500", btnBg: "bg-yellow-100", btnHover: "hover:bg-yellow-200", btnText: "text-yellow-600" },
  };

  const s = styles[errorType] || styles.unknown;

  return {
    className: `rounded-xl ${s.bg} border ${s.border} p-3`,
    textClassName: `${s.text} text-sm`,
    message,
    sectionName,
    retryable: errorType !== "auth" && errorType !== "not_found",
    btnClassName: `px-3 py-1 text-xs font-medium ${s.btnText} ${s.btnBg} ${s.btnHover} rounded-lg transition-colors`,
    onRetry,
  };
}

export default safeFetch;
