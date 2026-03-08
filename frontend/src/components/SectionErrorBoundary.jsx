import React from "react";
import { logBoundaryError } from "../base/utils/runtimeErrorLogger";

/**
 * SectionErrorBoundary
 *
 * セクション単位のError Boundary。
 * 1コンポーネントが落ちても、ページ全体が白画面にならないようにする。
 *
 * Props:
 *   sectionName  - セクション名（エラーUI表示用）
 *   onError      - エラー発生時のコールバック (error, errorInfo) => void
 *   fallback     - カスタムfallback UI (optional)
 *   children     - 保護対象のコンポーネント
 */
export default class SectionErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    const { sectionName, onError } = this.props;

    this.setState({ errorInfo });

    // 構造化ログに記録
    logBoundaryError({ sectionName, error, errorInfo });

    // 外部コールバック
    if (typeof onError === "function") {
      try {
        onError(error, errorInfo);
      } catch { /* ignore */ }
    }
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      // カスタムfallbackが指定されている場合
      if (this.props.fallback) {
        return typeof this.props.fallback === "function"
          ? this.props.fallback({
              error: this.state.error,
              retry: this.handleRetry,
              sectionName: this.props.sectionName,
            })
          : this.props.fallback;
      }

      // デフォルトfallback UI - コンポーネントクラッシュ用
      const sectionName = this.props.sectionName || "このセクション";
      const errorMessage = this.state.error?.message || "予期しないエラーが発生しました";

      return (
        <div className="w-full my-2 mx-auto">
          <div className="rounded-2xl bg-red-50 border border-red-200 p-4 md:p-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="flex items-center justify-center w-8 h-8 rounded-full bg-red-100">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className="text-red-500"
                  >
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-red-700 text-sm font-medium">
                      {sectionName}の表示に失敗しました
                    </span>
                    <span className="text-red-400 text-[10px] px-1.5 py-0.5 rounded bg-red-100">
                      クラッシュ
                    </span>
                  </div>
                  <div className="text-red-500 text-xs mt-0.5">
                    {errorMessage}
                  </div>
                </div>
              </div>
              <button
                onClick={this.handleRetry}
                className="px-3 py-1.5 text-xs font-medium text-red-600 bg-red-100 hover:bg-red-200 rounded-lg transition-colors"
              >
                再試行
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
