/**
 * ╔══════════════════════════════════════════════════════════════════╗
 * ║  AitherHub セクション追加テンプレート                              ║
 * ║                                                                  ║
 * ║  新しいセクションを追加する際は、このファイルをコピーして使う。       ║
 * ║  useSectionState + SectionStateUI を必ず使用すること。             ║
 * ╚══════════════════════════════════════════════════════════════════╝
 *
 * 使い方:
 *   1. このファイルをコピーして、新しいセクション名にリネーム
 *   2. SECTION_NAME を変更
 *   3. API_ENDPOINT を変更
 *   4. renderContent() にセクション固有のUIを実装
 *   5. VideoDetail.jsx に <SectionErrorBoundary> で囲んで配置
 *
 * 必須ルール:
 *   - useSectionState を使って API コールすること
 *   - SectionStateUI で loading / empty / error を表示すること
 *   - SectionErrorBoundary で囲むこと（VideoDetail.jsx 側）
 *   - セクション名は英語で統一すること（ログ検索のため）
 */

import { useState } from "react";
import useSectionState from "../base/hooks/useSectionState";
import SectionStateUI from "./SectionStateUI";

// ── 設定 ──────────────────────────────────────────────────
const SECTION_NAME = "NewSection";       // ← 変更: セクション名（英語）
const API_ENDPOINT = "/new-section";     // ← 変更: APIエンドポイント（/video/{id} の後に続くパス）
const SECTION_TITLE = "新しいセクション";   // ← 変更: 表示用タイトル
const SECTION_SUBTITLE = "セクションの説明文"; // ← 変更: 表示用サブタイトル
// ──────────────────────────────────────────────────────────

export default function NewSection({ videoId }) {
  const [collapsed, setCollapsed] = useState(false);

  // ── API 状態管理 ──
  // useSectionState は loading / data / error / status / retry / reset を提供する。
  // fetchOnMount: true にすると、コンポーネントマウント時に自動フェッチする。
  // fetchOnMount: false にすると、ボタン押下で手動フェッチする。
  const {
    data,
    status,     // 'idle' | 'loading' | 'success' | 'empty' | 'error'
    errorInfo,  // { type, message, httpStatus }
    execute,    // 手動フェッチ関数
    retry,      // リトライ関数
    reset,      // リセット関数
  } = useSectionState({
    sectionName: SECTION_NAME,
    videoId,
    fetchFn: async (safeFetchInstance) => {
      const baseURL = import.meta.env.VITE_API_BASE_URL;
      // GET の場合:
      return await safeFetchInstance(`${baseURL}/api/v1/video/${videoId}${API_ENDPOINT}`);
      // POST の場合:
      // return await safeFetchInstance(`${baseURL}/api/v1/video/${videoId}${API_ENDPOINT}`, {
      //   method: 'POST',
      //   headers: { 'Content-Type': 'application/json' },
      //   body: JSON.stringify({ key: 'value' }),
      // });
    },
    fetchOnMount: false,  // true: 自動フェッチ, false: ボタン押下でフェッチ
  });

  // ── コンテンツ描画 ──
  const renderContent = () => {
    if (!data) return null;

    // ここにセクション固有のUIを実装する
    return (
      <div className="p-4">
        <pre className="text-xs text-gray-500 bg-gray-50 p-3 rounded-lg overflow-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      </div>
    );
  };

  return (
    <div className="w-full mt-6 mx-auto">
      <div className="rounded-2xl bg-gray-50 border border-gray-200">
        {/* ヘッダー */}
        <div
          onClick={() => setCollapsed((s) => !s)}
          className="flex items-center justify-between p-5 cursor-pointer hover:bg-gray-100 transition-all duration-200"
        >
          <div className="flex items-center gap-4">
            {/* アイコン - 適宜変更 */}
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-blue-400 to-blue-600 flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" /><path d="M12 16v-4" /><path d="M12 8h.01" />
              </svg>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-gray-900 text-xl font-semibold">{SECTION_TITLE}</span>
                {/* バッジ - 必要に応じて追加 */}
                {/* <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-600 font-medium">Beta</span> */}
              </div>
              <div className="text-gray-500 text-sm mt-1">{SECTION_SUBTITLE}</div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* アクションボタン（fetchOnMount: false の場合） */}
            {status === "idle" && (
              <button
                onClick={(e) => { e.stopPropagation(); execute(); }}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-500 rounded-full hover:bg-blue-600 transition-colors"
              >
                実行
              </button>
            )}

            {/* 折りたたみボタン */}
            <button
              type="button"
              aria-expanded={!collapsed}
              className="text-gray-400 p-2 rounded focus:outline-none transition-colors"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={`w-6 h-6 transform transition-transform duration-200 ${!collapsed ? "rotate-180" : ""}`}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>
        </div>

        {/* コンテンツ */}
        {!collapsed && (
          <div className="px-5 pb-5">
            {/* SectionStateUI が loading / empty / error を統一表示する */}
            <SectionStateUI
              status={status}
              errorInfo={errorInfo}
              onRetry={retry}
              sectionName={SECTION_NAME}
            >
              {/* status === 'success' の場合のみ children が描画される */}
              {renderContent()}
            </SectionStateUI>
          </div>
        )}
      </div>
    </div>
  );
}
