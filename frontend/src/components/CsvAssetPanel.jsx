import { useState, useEffect, useCallback } from "react";
import BaseApiService from "../base/api/BaseApiService";

/**
 * CsvAssetPanel - CSV/Excelアセットの包括的管理パネル
 *
 * 機能:
 * - アセット情報表示（ファイル名、バージョン、アップロード日時、サイズ、バリデーション状態）
 * - バリデーション状態の常時表示
 * - CSVプレビュー（先頭行・カラム情報）
 * - バージョン履歴表示
 * - 差し替え・再検証・再分析アクション
 */
export default function CsvAssetPanel({ videoData, onReplace, onRefresh }) {
  const [excelInfo, setExcelInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [showPreview, setShowPreview] = useState(null); // 'trend_csv' | 'product_csv' | null
  const [previewData, setPreviewData] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [historyData, setHistoryData] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [revalidating, setRevalidating] = useState(false);

  const api = new BaseApiService(import.meta.env.VITE_API_BASE_URL || "");

  const loadExcelInfo = useCallback(async () => {
    if (!videoData?.id) return;
    setLoading(true);
    // Safety timeout: prevent infinite spinner
    const timeout = setTimeout(() => {
      console.warn("[CsvAssetPanel] Loading timed out after 10s");
      setLoading(false);
    }, 10000);
    try {
      const res = await api.get(`/api/v1/videos/${videoData.id}/excel-info`);
      setExcelInfo(res);
    } catch (err) {
      console.warn("[CsvAssetPanel] Failed to load excel info:", err);
      setExcelInfo(null);
    } finally {
      clearTimeout(timeout);
      setLoading(false);
    }
  }, [videoData?.id]);

  useEffect(() => {
    loadExcelInfo();
  }, [loadExcelInfo]);

  // CSVプレビュー読み込み
  const loadPreview = async (assetType) => {
    if (showPreview === assetType) {
      setShowPreview(null);
      return;
    }
    setShowPreview(assetType);
    setPreviewLoading(true);
    setPreviewData(null);
    try {
      const res = await api.get(`/api/v1/videos/${videoData.id}/csv-preview?asset_type=${assetType}&max_rows=10`);
      setPreviewData(res);
    } catch (err) {
      console.warn("[CsvAssetPanel] Failed to load preview:", err);
      setPreviewData({ available: false, message: window.__t('csvAssetPanel_0e987b', 'プレビューの読み込みに失敗しました') });
    } finally {
      setPreviewLoading(false);
    }
  };

  // バージョン履歴読み込み
  const loadHistory = async () => {
    if (showHistory) {
      setShowHistory(false);
      return;
    }
    setShowHistory(true);
    setHistoryLoading(true);
    try {
      const res = await api.get(`/api/v1/videos/${videoData.id}/asset-history`);
      setHistoryData(res);
    } catch (err) {
      console.warn("[CsvAssetPanel] Failed to load history:", err);
      setHistoryData({ history: [] });
    } finally {
      setHistoryLoading(false);
    }
  };

  // 再検証 - recalc-csv-metrics APIを呼び出してCSVメトリクスを再計算
  // retry-analysisは使わない（DONE動画のステータスをリセットしてしまうため）
  const handleRevalidate = async () => {
    if (!videoData?.id) return;
    setRevalidating(true);
    try {
      const res = await api.post(`/api/v1/admin/videos/${videoData.id}/recalc-csv-metrics`);
      if (res?.success || res?.status === 'recalculated') {
        alert(window.__t('csvAssetPanel_ccc667', 'CSVメトリクスの再計算が完了しました。ページを再読み込みします。'));
        // 親コンポーネントにリフレッシュを通知
        if (onRefresh) onRefresh();
        // ページをリロードしてグラフを更新
        window.location.reload();
      } else {
        alert(`再検証に失敗しました: ${res?.detail || res?.message || window.__t('csvAssetPanel_2d20c4', '不明なエラー')}`);
      }
    } catch (err) {
      console.error('[CsvAssetPanel] recalc-csv-metrics failed:', err);
      const msg = err?.response?.data?.detail || err?.message || window.__t('csvAssetPanel_2d20c4', '不明なエラー');
      alert(`再検証に失敗しました: ${msg}`);
    } finally {
      setRevalidating(false);
      // Excel情報も再取得
      await loadExcelInfo();
    }
  };

  // バリデーション状態バッジ
  const ValidationBadge = ({ status }) => {
    const config = {
      ok: { bg: "bg-green-100", text: "text-green-700", label: "OK", icon: "check" },
      warning: { bg: "bg-amber-100", text: "text-amber-700", label: window.__t('csvAssetPanel_248521', '要確認'), icon: "alert" },
      error: { bg: "bg-red-100", text: "text-red-700", label: window.__t('csvAssetPanel_a6f60d', '不一致'), icon: "x" },
      mismatch: { bg: "bg-red-100", text: "text-red-700", label: window.__t('csvAssetPanel_a6f60d', '不一致'), icon: "x" },
      unknown: { bg: "bg-gray-100", text: "text-gray-500", label: window.__t('videoDetail_unverified', '未検証'), icon: "?" },
    };
    const c = config[status] || config.unknown;
    return (
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold ${c.bg} ${c.text}`}>
        {c.icon === "check" && (
          <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        )}
        {c.icon === "alert" && (
          <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        )}
        {c.icon === "x" && (
          <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        )}
        {c.icon === "?" && (
          <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        )}
        {c.label}
      </span>
    );
  };

  // ファイルサイズフォーマット
  const formatFileSize = (bytes) => {
    if (!bytes || bytes === 0) return "-";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  // アップロードタイプバッジ
  const uploadTypeBadge = (type) => {
    const config = {
      clean_video: { bg: "bg-green-100", text: "text-green-700", label: window.__t('csvAssetPanel_af4a16', 'クリーン動画') },
      live_capture: { bg: "bg-red-100", text: "text-red-700", label: window.__t('csvAssetPanel_7a9bb9', 'ライブキャプチャ') },
      screen_recording: { bg: "bg-gray-100", text: "text-gray-600", label: window.__t('videoDetail_screenRecording', '画面収録') },
    };
    const c = config[type] || config.screen_recording;
    return (
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${c.bg} ${c.text}`}>
        {c.label}
      </span>
    );
  };

  // アセット行コンポーネント
  const AssetRow = ({ label, hasFile, filename, asset }) => {
    const assetType = label === "商品データ" ? "product_csv" : "trend_csv";
    return (
      <div className="rounded-xl border border-gray-100 bg-gray-50/50 p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <div className={`w-2 h-2 rounded-full flex-shrink-0 ${hasFile ? "bg-green-400" : "bg-gray-300"}`} />
            <span className="text-xs font-semibold text-gray-700">{label}</span>
            {asset && <ValidationBadge status={asset.validation_status} />}
          </div>
          {hasFile && (
            <button
              onClick={() => loadPreview(assetType)}
              className={`text-[10px] px-2 py-1 rounded-md transition-colors ${
                showPreview === assetType
                  ? "bg-blue-100 text-blue-700"
                  : "text-gray-400 hover:text-blue-600 hover:bg-blue-50"
              }`}
            >
              {showPreview === assetType ? [window.__t('common_close', '閉じる')] : window.__t('clip_preview', 'プレビュー')}
            </button>
          )}
        </div>

        {hasFile ? (
          <div className="mt-2 space-y-1">
            <div className="flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-gray-400 flex-shrink-0">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><polyline points="14 2 14 8 20 8"/>
              </svg>
              <span className="text-xs text-gray-600 truncate" title={filename || asset?.filename}>
                {filename || asset?.filename || window.__t('csvAssetPanel_efd626', '不明')}
              </span>
            </div>
            {asset && (
              <div className="flex items-center gap-4 text-[10px] text-gray-400 pl-5">
                {asset.version && <span>v{asset.version}</span>}
                {asset.file_size > 0 && <span>{formatFileSize(asset.file_size)}</span>}
                {asset.uploaded_at && (
                  <span>{new Date(asset.uploaded_at).toLocaleString("ja-JP", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="mt-1.5 pl-5">
            <span className="text-xs text-gray-400">{window.__t('videoDetail_notAttached', '未添付')}</span>
          </div>
        )}

        {/* プレビュー表示 */}
        {showPreview === assetType && (
          <div className="mt-3 border-t border-gray-200 pt-3">
            {previewLoading ? (
              <div className="flex items-center gap-2 text-xs text-gray-400 py-4 justify-center">
                <div className="animate-spin w-3 h-3 border-2 border-gray-300 border-t-gray-600 rounded-full" />
                プレビューを読み込み中...
              </div>
            ) : previewData?.available === false ? (
              <div className="text-xs text-gray-400 text-center py-2">{previewData.message || window.__t('csvAssetPanel_ea8239', 'プレビューを利用できません')}</div>
            ) : previewData ? (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
                    {previewData.sheet_name || "Sheet"} - {previewData.total_rows}行
                  </span>
                  {previewData.datetime_columns?.length > 0 && (
                    <span className="text-[10px] text-blue-500">
                      日時カラム: {previewData.datetime_columns.join(", ")}
                    </span>
                  )}
                </div>
                <div className="overflow-x-auto rounded-lg border border-gray-200">
                  <table className="w-full text-[10px]">
                    <thead>
                      <tr className="bg-gray-100">
                        {previewData.columns?.map((col, i) => (
                          <th key={i} className="px-2 py-1.5 text-left font-semibold text-gray-600 whitespace-nowrap border-b border-gray-200">
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {previewData.preview_rows?.slice(0, 5).map((row, ri) => (
                        <tr key={ri} className={ri % 2 === 0 ? "bg-white" : "bg-gray-50/50"}>
                          {previewData.columns?.map((col, ci) => (
                            <td key={ci} className="px-2 py-1 text-gray-600 whitespace-nowrap max-w-[150px] truncate border-b border-gray-100">
                              {row[col] != null ? String(row[col]) : <span className="text-gray-300">-</span>}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {previewData.total_rows > 5 && (
                  <div className="text-center text-[10px] text-gray-400 mt-1">
                    他 {previewData.total_rows - 5} 行...
                  </div>
                )}
              </div>
            ) : null}
          </div>
        )}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="w-full mt-2 mx-auto">
        <div className="rounded-2xl bg-white border border-gray-200 p-4">
          <div className="flex items-center gap-2 text-gray-400 text-sm">
            <div className="animate-spin w-4 h-4 border-2 border-gray-300 border-t-gray-600 rounded-full" />
            CSVアセット情報を読み込み中...
          </div>
        </div>
      </div>
    );
  }

  if (!excelInfo) return null;

  // 全体のバリデーション状態を計算
  const overallValidation = (() => {
    const trendStatus = excelInfo.current_assets?.trend_csv?.validation_status || "unknown";
    const productStatus = excelInfo.current_assets?.product_csv?.validation_status || "unknown";
    if (trendStatus === "error" || trendStatus === "mismatch" || productStatus === "error" || productStatus === "mismatch") return "error";
    if (trendStatus === "warning" || productStatus === "warning") return "warning";
    if (trendStatus === "ok" && (productStatus === "ok" || !excelInfo.has_product)) return "ok";
    return "unknown";
  })();

  const validationBorderColor = {
    ok: "border-l-green-400",
    warning: "border-l-amber-400",
    error: "border-l-red-400",
    unknown: "border-l-gray-300",
  };

  return (
    <div className="w-full mt-2 mx-auto">
      <div className={`rounded-2xl bg-white border border-gray-200 overflow-hidden border-l-4 ${validationBorderColor[overallValidation]}`}>
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
                <span className="text-sm font-semibold text-gray-800">{window.__t('videoDetail_csvAssetManagement', 'CSVアセット管理')}</span>
                {uploadTypeBadge(excelInfo.upload_type)}
                <ValidationBadge status={overallValidation} />
              </div>
              <div className="flex items-center gap-3 mt-0.5">
                <span className="text-[10px] text-gray-400">
                  商品: {excelInfo.has_product ? [window.__t('csvAssetPanel_beb409', 'あり')] : window.__t('csvAssetPanel_3609f9', 'なし')} / トレンド: {excelInfo.has_trend ? [window.__t('csvAssetPanel_beb409', 'あり')] : window.__t('csvAssetPanel_3609f9', 'なし')}
                  {excelInfo.asset_history?.length > 0 && ` / ${excelInfo.asset_history.length}${window.__t('csvAssetPanel_690db4', '件の履歴')}`}
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* アクションボタン群 */}
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
              差し替え
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
              {/* トレンドデータ */}
              <AssetRow
                label={window.__t('videoDetail_trendData', 'トレンドデータ')}
                hasFile={excelInfo.has_trend}
                filename={excelInfo.trend_filename}
                asset={excelInfo.current_assets?.trend_csv}
              />

              {/* 商品データ */}
              <AssetRow
                label={window.__t('videoDetail_productData', '商品データ')}
                hasFile={excelInfo.has_product}
                filename={excelInfo.product_filename}
                asset={excelInfo.current_assets?.product_csv}
              />

              {/* メタ情報 */}
              <div className="flex items-center justify-between px-1">
                <span className="text-[10px] text-gray-400">
                  アップロード: {excelInfo.created_at ? new Date(excelInfo.created_at).toLocaleString("ja-JP") : "-"}
                </span>
                {excelInfo.updated_at && excelInfo.updated_at !== excelInfo.created_at && (
                  <span className="text-[10px] text-gray-400">
                    更新: {new Date(excelInfo.updated_at).toLocaleString("ja-JP")}
                  </span>
                )}
              </div>

              {/* アクションバー */}
              <div className="flex items-center gap-2 pt-2 border-t border-gray-100">
                <button
                  onClick={loadHistory}
                  className={`flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg transition-colors ${
                    showHistory ? "bg-gray-200 text-gray-700" : "text-gray-500 hover:bg-gray-100"
                  }`}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
                  </svg>
                  履歴
                </button>

                <button
                  onClick={handleRevalidate}
                  disabled={revalidating}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={revalidating ? "animate-spin" : ""}>
                    <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/>
                  </svg>
                  {revalidating ? [window.__t('csvAssetPanel_f5ae62', '検証中...')] : window.__t('videoDetail_reVerify', '再検証')}
                </button>

                <button
                  onClick={() => {
                    if (onReplace) onReplace();
                  }}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs text-blue-600 hover:bg-blue-50 rounded-lg transition-colors ml-auto"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="17 8 12 3 7 8"/>
                    <line x1="12" y1="3" x2="12" y2="15"/>
                  </svg>
                  CSV差し替え
                </button>
              </div>

              {/* バージョン履歴 */}
              {showHistory && (
                <div className="mt-2 rounded-xl border border-gray-200 overflow-hidden">
                  <div className="bg-gray-50 px-3 py-2 border-b border-gray-200">
                    <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">{window.__t('csvAssetPanel_8514f2', 'バージョン履歴')}</span>
                  </div>
                  {historyLoading ? (
                    <div className="flex items-center gap-2 text-xs text-gray-400 p-4 justify-center">
                      <div className="animate-spin w-3 h-3 border-2 border-gray-300 border-t-gray-600 rounded-full" />
                      読み込み中...
                    </div>
                  ) : historyData?.history?.length > 0 ? (
                    <div className="divide-y divide-gray-100">
                      {historyData.history.map((item) => (
                        <div key={item.id} className={`px-3 py-2 text-xs ${item.is_active ? "bg-blue-50/30" : ""}`}>
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                                item.asset_type === "trend_csv" ? "bg-purple-100 text-purple-700" : "bg-teal-100 text-teal-700"
                              }`}>
                                {item.asset_type === "trend_csv" ? "トレンド" : "商品"}
                              </span>
                              <span className="text-gray-600 truncate max-w-[200px]">{item.filename || window.__t('csvAssetPanel_efd626', '不明')}</span>
                              <span className="text-gray-400">v{item.version}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <ValidationBadge status={item.validation_status} />
                              {item.is_active && (
                                <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-blue-100 text-blue-700">{window.__t('csvAssetPanel_684a6b', '現在')}</span>
                              )}
                            </div>
                          </div>
                          <div className="mt-1 text-[10px] text-gray-400 pl-0">
                            {item.uploaded_at ? new Date(item.uploaded_at).toLocaleString("ja-JP") : "-"}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-xs text-gray-400 text-center py-4">
                      {excelInfo.replace_history?.length > 0 ? (
                        // 旧形式の差し替え履歴を表示
                        <div className="text-left px-3">
                          <p className="text-center mb-2">{window.__t('csvAssetPanel_76b4cb', '新形式の履歴はまだありません。旧形式の差し替え履歴:')}</p>
                          {excelInfo.replace_history.map((h) => (
                            <div key={h.id} className="py-2 border-b border-gray-100 last:border-0">
                              <div className="flex items-center justify-between">
                                <span className="text-gray-500">
                                  {h.created_at ? new Date(h.created_at).toLocaleString("ja-JP") : "-"}
                                </span>
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                                  h.reprocess_status === "queued" ? "bg-green-50 text-green-600" :
                                  h.reprocess_status === "skipped" ? "bg-gray-100 text-gray-500" :
                                  "bg-red-50 text-red-600"
                                }`}>
                                  {h.reprocess_status === "queued" ? "再処理済" : h.reprocess_status === "skipped" ? "スキップ" : h.reprocess_status}
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
                      ) : (
                        window.__t('csvAssetPanel_71d318', '履歴がありません')
                      )}
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
