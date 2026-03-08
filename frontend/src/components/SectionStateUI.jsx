import React from "react";

/**
 * SectionStateUI - セクションの4状態（loading / empty / error / success）を統一表示するコンポーネント
 *
 * Props:
 *   state        - "idle" | "loading" | "success" | "empty" | "error"
 *   error        - { type, message, status } (error状態時)
 *   onRetry      - リトライ関数
 *   sectionName  - セクション名（表示用）
 *   loadingText  - ローディング中のテキスト（デフォルト: "読み込み中..."）
 *   emptyText    - データなし時のテキスト（デフォルト: "データがありません"）
 *   emptyIcon    - データなし時のアイコン（デフォルト: 📭）
 *   children     - success状態時に表示するコンテンツ
 *   compact      - コンパクト表示モード（デフォルト: false）
 */

// エラータイプ別の表示設定
const ERROR_STYLES = {
  network: {
    bg: "bg-yellow-50", border: "border-yellow-200",
    text: "text-yellow-700", subtext: "text-yellow-500",
    icon: "wifi_off", iconBg: "bg-yellow-100", iconColor: "text-yellow-500",
    btn: "text-yellow-600 bg-yellow-100 hover:bg-yellow-200",
    label: "ネットワーク",
  },
  timeout: {
    bg: "bg-yellow-50", border: "border-yellow-200",
    text: "text-yellow-700", subtext: "text-yellow-500",
    icon: "schedule", iconBg: "bg-yellow-100", iconColor: "text-yellow-500",
    btn: "text-yellow-600 bg-yellow-100 hover:bg-yellow-200",
    label: "タイムアウト",
  },
  auth: {
    bg: "bg-orange-50", border: "border-orange-200",
    text: "text-orange-700", subtext: "text-orange-500",
    icon: "lock", iconBg: "bg-orange-100", iconColor: "text-orange-500",
    btn: "text-orange-600 bg-orange-100 hover:bg-orange-200",
    label: "認証エラー",
  },
  not_found: {
    bg: "bg-gray-50", border: "border-gray-200",
    text: "text-gray-600", subtext: "text-gray-400",
    icon: "search_off", iconBg: "bg-gray-100", iconColor: "text-gray-400",
    btn: "text-gray-600 bg-gray-100 hover:bg-gray-200",
    label: "未生成",
  },
  server: {
    bg: "bg-red-50", border: "border-red-200",
    text: "text-red-700", subtext: "text-red-500",
    icon: "error", iconBg: "bg-red-100", iconColor: "text-red-500",
    btn: "text-red-600 bg-red-100 hover:bg-red-200",
    label: "サーバーエラー",
  },
  rate_limit: {
    bg: "bg-yellow-50", border: "border-yellow-200",
    text: "text-yellow-700", subtext: "text-yellow-500",
    icon: "speed", iconBg: "bg-yellow-100", iconColor: "text-yellow-500",
    btn: "text-yellow-600 bg-yellow-100 hover:bg-yellow-200",
    label: "レート制限",
  },
  parse: {
    bg: "bg-red-50", border: "border-red-200",
    text: "text-red-700", subtext: "text-red-500",
    icon: "code_off", iconBg: "bg-red-100", iconColor: "text-red-500",
    btn: "text-red-600 bg-red-100 hover:bg-red-200",
    label: "データエラー",
  },
  unknown: {
    bg: "bg-gray-50", border: "border-gray-200",
    text: "text-gray-600", subtext: "text-gray-400",
    icon: "help", iconBg: "bg-gray-100", iconColor: "text-gray-400",
    btn: "text-gray-600 bg-gray-100 hover:bg-gray-200",
    label: "エラー",
  },
};

// SVGアイコン（Material Design風）
function ErrorIcon({ type }) {
  const iconMap = {
    network: (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="1" y1="1" x2="23" y2="23" /><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55" /><path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39" /><path d="M10.71 5.05A16 16 0 0 1 22.56 9" /><path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88" /><path d="M8.53 16.11a6 6 0 0 1 6.95 0" /><line x1="12" y1="20" x2="12.01" y2="20" />
      </svg>
    ),
    timeout: (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
      </svg>
    ),
    auth: (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
      </svg>
    ),
    not_found: (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><line x1="8" y1="11" x2="14" y2="11" />
      </svg>
    ),
    server: (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
    ),
    rate_limit: (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
      </svg>
    ),
    parse: (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /><line x1="12" y1="2" x2="12" y2="22" />
      </svg>
    ),
    unknown: (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
    ),
  };
  return iconMap[type] || iconMap.unknown;
}

// ローディングスピナー
function LoadingSpinner({ text = "読み込み中...", compact = false }) {
  if (compact) {
    return (
      <div className="flex items-center justify-center gap-2 py-4">
        <div className="w-4 h-4 border-2 border-purple-200 border-t-purple-600 rounded-full animate-spin" />
        <span className="text-xs text-gray-500">{text}</span>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center py-8 gap-3">
      <div className="w-8 h-8 border-3 border-purple-200 border-t-purple-600 rounded-full animate-spin" />
      <span className="text-sm text-gray-500">{text}</span>
    </div>
  );
}

// 空状態表示
function EmptyState({ text = "データがありません", icon = null, compact = false }) {
  if (compact) {
    return (
      <div className="flex items-center justify-center gap-2 py-4 text-gray-400">
        <span className="text-sm">{icon || "📭"}</span>
        <span className="text-xs">{text}</span>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center py-8 gap-2">
      <span className="text-2xl">{icon || "📭"}</span>
      <span className="text-sm text-gray-500">{text}</span>
    </div>
  );
}

// エラー表示
function ErrorState({ error, onRetry, sectionName, compact = false }) {
  const errorType = error?.type || "unknown";
  const s = ERROR_STYLES[errorType] || ERROR_STYLES.unknown;
  const retryable = errorType !== "auth";

  // not_found は「未生成」として自然に表示
  if (errorType === "not_found") {
    return (
      <div className={`rounded-xl ${s.bg} border ${s.border} p-3`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className={`flex items-center justify-center w-7 h-7 rounded-full ${s.iconBg} ${s.iconColor}`}>
              <ErrorIcon type={errorType} />
            </div>
            <div>
              <span className={`${s.text} text-sm`}>
                {sectionName ? `${sectionName}はまだ生成されていません` : "データが未生成です"}
              </span>
            </div>
          </div>
          {onRetry && (
            <button
              onClick={onRetry}
              className={`px-3 py-1 text-xs font-medium ${s.btn} rounded-lg transition-colors`}
            >
              再読込
            </button>
          )}
        </div>
      </div>
    );
  }

  if (compact) {
    return (
      <div className={`rounded-lg ${s.bg} border ${s.border} px-3 py-2 flex items-center justify-between`}>
        <div className="flex items-center gap-2">
          <span className={`${s.iconColor}`}><ErrorIcon type={errorType} /></span>
          <span className={`${s.text} text-xs`}>{error?.message || "エラーが発生しました"}</span>
          <span className={`${s.subtext} text-[10px] px-1.5 py-0.5 rounded ${s.iconBg}`}>{s.label}</span>
        </div>
        {retryable && onRetry && (
          <button onClick={onRetry} className={`px-2 py-0.5 text-[10px] font-medium ${s.btn} rounded transition-colors`}>
            再試行
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={`rounded-xl ${s.bg} border ${s.border} p-4`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`flex items-center justify-center w-8 h-8 rounded-full ${s.iconBg} ${s.iconColor}`}>
            <ErrorIcon type={errorType} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className={`${s.text} text-sm font-medium`}>
                {sectionName || "セクション"}
              </span>
              <span className={`${s.subtext} text-[10px] px-1.5 py-0.5 rounded ${s.iconBg}`}>
                {s.label}
              </span>
            </div>
            <div className={`${s.subtext} text-xs mt-0.5`}>
              {error?.message || "エラーが発生しました"}
            </div>
            {error?.status && (
              <div className={`${s.subtext} text-[10px] mt-0.5 opacity-60`}>
                HTTP {error.status}
              </div>
            )}
          </div>
        </div>
        {retryable && onRetry && (
          <button
            onClick={onRetry}
            className={`px-3 py-1.5 text-xs font-medium ${s.btn} rounded-lg transition-colors`}
          >
            再試行
          </button>
        )}
        {errorType === "auth" && (
          <button
            onClick={() => window.location.reload()}
            className={`px-3 py-1.5 text-xs font-medium ${s.btn} rounded-lg transition-colors`}
          >
            再ログイン
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * SectionStateUI - メインコンポーネント
 */
export default function SectionStateUI({
  state,
  error,
  onRetry,
  sectionName,
  loadingText,
  emptyText,
  emptyIcon,
  compact = false,
  children,
}) {
  switch (state) {
    case "idle":
      return null;

    case "loading":
      return <LoadingSpinner text={loadingText} compact={compact} />;

    case "empty":
      return <EmptyState text={emptyText} icon={emptyIcon} compact={compact} />;

    case "error":
      return (
        <ErrorState
          error={error}
          onRetry={onRetry}
          sectionName={sectionName}
          compact={compact}
        />
      );

    case "success":
    default:
      return <>{children}</>;
  }
}

// Named exports for individual use
export { LoadingSpinner, EmptyState, ErrorState, ERROR_STYLES };
