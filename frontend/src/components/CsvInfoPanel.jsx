import { useState, useEffect } from "react";
import BaseApiService from "../base/api/BaseApiService";

/**
 * CsvInfoPanel - 動画に紐付いているCSV（Excel）情報を表示するパネル
 *
 * 表示内容:
 * - アップロードタイプ（クリーン動画 / 画面収録）
 * - 商品データExcelのファイル名・ステータス
 * - トレンドデータExcelのファイル名・ステータス
 * - 差し替えボタン
 * - 差し替え履歴
 */
export default function CsvInfoPanel({ videoData, onReplace }) {
  const [excelInfo, setExcelInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState(true);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    if (!videoData?.id) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const api = new BaseApiService(import.meta.env.VITE_API_BASE_URL || "");
        const res = await api.get(`/api/v1/videos/${videoData.id}/excel-info`);
        if (!cancelled) setExcelInfo(res);
      } catch (err) {
        console.warn("[CsvInfoPanel] Failed to load excel info:", err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [videoData?.id]);

  // アップロードタイプに応じたバッジ
  const uploadTypeBadge = (type) => {
    if (type === "clean_video") {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
          クリーン動画
        </span>
      );
    }
    if (type === "live_capture") {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
          ライブキャプチャ
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
        画面収録
      </span>
    );
  };

  // ファイルステータスバッジ
  const fileBadge = (hasFile, filename) => {
    if (hasFile) {
      return (
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-green-50 text-green-600 font-medium">
            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
            あり
          </span>
          <span className="text-xs text-gray-500 truncate max-w-[200px]" title={filename}>
            {filename || window.__t('csvAssetPanel_efd626', '不明')}
          </span>
        </div>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-gray-50 text-gray-400 font-medium">
        <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        なし
      </span>
    );
  };

  if (loading) {
    return (
      <div className="w-full mt-2 mx-auto">
        <div className="rounded-2xl bg-white border border-gray-200 p-4">
          <div className="flex items-center gap-2 text-gray-400 text-sm">
            <div className="animate-spin w-4 h-4 border-2 border-gray-300 border-t-gray-600 rounded-full" />
            CSV情報を読み込み中...
          </div>
        </div>
      </div>
    );
  }

  if (!excelInfo) return null;

  return (
    <div className="w-full mt-2 mx-auto">
      <div className="rounded-2xl bg-white border border-gray-200 overflow-hidden">
        {/* ヘッダー */}
        <div
          onClick={() => setCollapsed((s) => !s)}
          className="flex items-center justify-between p-4 cursor-pointer hover:bg-gray-50 transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-blue-50 flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-blue-500">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
                <polyline points="10 9 9 9 8 9"/>
              </svg>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-gray-800">{window.__t('csvInfoPanel_6452b6', window.__t('csvInfoPanel_6452b6', 'CSV / Excelデータ'))}</span>
                {uploadTypeBadge(excelInfo.upload_type)}
              </div>
              <div className="flex items-center gap-3 mt-1">
                <span className="text-xs text-gray-400">
                  商品: {excelInfo.has_product ? window.__t('csvAssetPanel_beb409', 'あり') : window.__t('csvAssetPanel_3609f9', 'なし')} / トレンド: {excelInfo.has_trend ? window.__t('csvAssetPanel_beb409', 'あり') : window.__t('csvAssetPanel_3609f9', 'なし')}
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* 差し替えボタン */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (onReplace) onReplace();
              }}
              className="px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors flex items-center gap-1"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
              CSV差し替え
            </button>

            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
              className={`w-5 h-5 text-gray-400 transform transition-transform duration-200 ${!collapsed ? "rotate-180" : ""}`}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>

        {/* 展開時の詳細 */}
        {!collapsed && (
          <div className="px-4 pb-4 border-t border-gray-100">
            <div className="mt-3 space-y-3">
              {/* 商品データ */}
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-gray-500 w-24">{window.__t('videoDetail_productData', window.__t('videoDetail_productData', '商品データ'))}</span>
                {fileBadge(excelInfo.has_product, excelInfo.product_filename)}
              </div>

              {/* トレンドデータ */}
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-gray-500 w-24">{window.__t('videoDetail_trendData', window.__t('videoDetail_trendData', 'トレンドデータ'))}</span>
                {fileBadge(excelInfo.has_trend, excelInfo.trend_filename)}
              </div>

              {/* メタ情報 */}
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-gray-500 w-24">{window.__t('csvInfoPanel_d721e8', window.__t('csvInfoPanel_d721e8', 'アップロード日'))}</span>
                <span className="text-xs text-gray-500">
                  {excelInfo.created_at ? new Date(excelInfo.created_at).toLocaleString("ja-JP") : "-"}
                </span>
              </div>

              {excelInfo.updated_at && excelInfo.updated_at !== excelInfo.created_at && (
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-gray-500 w-24">{window.__t('csvInfoPanel_c2a798', window.__t('csvInfoPanel_c2a798', '最終更新'))}</span>
                  <span className="text-xs text-gray-500">
                    {new Date(excelInfo.updated_at).toLocaleString("ja-JP")}
                  </span>
                </div>
              )}

              {/* 差し替え履歴 */}
              {excelInfo.replace_history && excelInfo.replace_history.length > 0 && (
                <div className="mt-2 pt-2 border-t border-gray-100">
                  <button
                    onClick={() => setShowHistory((s) => !s)}
                    className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
                    </svg>
                    差し替え履歴 ({excelInfo.replace_history.length}件)
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                      className={`w-3 h-3 transform transition-transform ${showHistory ? "rotate-180" : ""}`}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>

                  {showHistory && (
                    <div className="mt-2 space-y-2">
                      {excelInfo.replace_history.map((h) => (
                        <div key={h.id} className="text-xs bg-gray-50 rounded-lg p-2">
                          <div className="flex items-center justify-between">
                            <span className="text-gray-400">
                              {h.created_at ? new Date(h.created_at).toLocaleString("ja-JP") : "-"}
                            </span>
                            <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                              h.reprocess_status === "queued"
                                ? "bg-green-50 text-green-600"
                                : h.reprocess_status === "skipped"
                                ? "bg-gray-100 text-gray-500"
                                : "bg-red-50 text-red-600"
                            }`}>
                              {h.reprocess_status === "queued" ? "再処理済み" :
                               h.reprocess_status === "skipped" ? "スキップ" : h.reprocess_status}
                            </span>
                          </div>
                          <div className="mt-1 text-gray-500">
                            {h.new_product && <span>商品: {h.new_product}</span>}
                            {h.new_product && h.new_trend && <span> / </span>}
                            {h.new_trend && <span>トレンド: {h.new_trend}</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
