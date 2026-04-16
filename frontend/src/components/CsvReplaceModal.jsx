import { useState, useRef } from "react";
import BaseApiService from "../base/api/BaseApiService";
import { validateCsvDateTime } from "../base/utils/csvDateTimeValidator";
import { extractTrendStartTime, extractProductDate, extractExcelFileNameDate, extractVideoDateTime } from "../base/utils/csvDateTimeExtractor";

/**
 * CsvReplaceModal - CSV（Excel）ファイルを差し替えるモーダル
 *
 * フロー:
 * 1. ユーザーが新しいExcelファイルを選択
 * 2. Validation Gate で日時照合
 * 3. Blobストレージにアップロード
 * 4. replace-excel APIで差し替え + Worker再処理
 */
export default function CsvReplaceModal({ videoData, onClose, onComplete }) {
  const [productFile, setProductFile] = useState(null);
  const [trendFile, setTrendFile] = useState(null);
  const [step, setStep] = useState("select"); // select | validating | validation_result | uploading | done | error
  const [validationResult, setValidationResult] = useState(null);
  const [uploadProgress, setUploadProgress] = useState("");
  const [error, setError] = useState(null);
  const productInputRef = useRef(null);
  const trendInputRef = useRef(null);

  const api = new BaseApiService(import.meta.env.VITE_API_BASE_URL || "");

  // ファイル選択ハンドラー
  const handleProductSelect = (e) => {
    const file = e.target.files?.[0];
    if (file) setProductFile(file);
  };

  const handleTrendSelect = (e) => {
    const file = e.target.files?.[0];
    if (file) setTrendFile(file);
  };

  // バリデーション実行
  const handleValidate = async () => {
    if (!productFile && !trendFile) return;

    setStep("validating");
    try {
      // 動画ファイル名から日時を推定
      const videoFilename = videoData?.original_filename || "";
      const videoDate = extractVideoDateTime(videoFilename);

      // トレンドExcelから開始時刻を抽出
      let trendStartTime = null;
      if (trendFile) {
        try {
          trendStartTime = await extractTrendStartTime(trendFile);
        } catch (err) {
          console.warn("[CsvReplaceModal] Failed to extract trend start time:", err);
        }
      }

      // 商品Excelから日付を抽出
      let productDate = null;
      if (productFile) {
        try {
          productDate = await extractProductDate(productFile);
        } catch (err) {
          console.warn("[CsvReplaceModal] Failed to extract product date:", err);
        }
      }

      // 照合
      const result = validateCsvDateTime({
        videoDate: videoDate?.date || null,
        videoStartTime: videoDate?.time || null,
        trendStartTime,
        productDate: productDate?.date || extractExcelFileNameDate(productFile?.name)?.date || null,
        productFilename: productFile?.name || null,
        trendFilename: trendFile?.name || null,
      });

      setValidationResult(result);

      if (result.overallVerdict === "ok") {
        // 自動続行
        await handleUpload();
      } else {
        setStep("validation_result");
      }
    } catch (err) {
      console.error("[CsvReplaceModal] Validation error:", err);
      // バリデーションエラーでも続行可能にする
      setStep("validation_result");
      setValidationResult({
        overallVerdict: "unknown",
        checks: [],
        summary: window.__t('csvReplaceModal_c2a364', 'バリデーションを実行できませんでした。ファイルの内容を確認してください。'),
      });
    }
  };

  // アップロード実行
  const handleUpload = async () => {
    setStep("uploading");
    setUploadProgress(window.__t('csvReplaceModal_685ec8', 'Excel アップロードURLを生成中...'));

    try {
      const videoId = videoData?.id;
      const email = videoData?.email || localStorage.getItem("userEmail") || "";

      let productBlobUrl = null;
      let trendBlobUrl = null;

      // 1. Excel アップロードURL生成
      if (productFile || trendFile) {
        setUploadProgress(window.__t('csvReplaceModal_a84d56', 'アップロードURLを生成中...'));
        const urlRes = await api.post("/api/v1/videos/generate-excel-upload-url", {
          email,
          video_id: videoId,
          product_filename: productFile?.name || "placeholder.xlsx",
          trend_filename: trendFile?.name || "placeholder.xlsx",
        });

        // 2. 商品データExcelをアップロード
        if (productFile && urlRes.product_upload_url) {
          setUploadProgress(window.__t('csvReplaceModal_58cb8e', '商品データをアップロード中...'));
          await uploadToBlob(urlRes.product_upload_url, productFile);
          productBlobUrl = urlRes.product_blob_url;
        }

        // 3. トレンドデータExcelをアップロード
        if (trendFile && urlRes.trend_upload_url) {
          setUploadProgress(window.__t('csvReplaceModal_076e38', 'トレンドデータをアップロード中...'));
          await uploadToBlob(urlRes.trend_upload_url, trendFile);
          trendBlobUrl = urlRes.trend_blob_url;
        }
      }

      // 4. replace-excel APIを呼び出し
      setUploadProgress(window.__t('csvReplaceModal_b93102', 'データを差し替え中...'));
      const replaceRes = await api.put(`/api/v1/videos/${videoId}/replace-excel`, {
        excel_product_blob_url: productBlobUrl,
        excel_trend_blob_url: trendBlobUrl,
        reprocess: true,
      });

      setStep("done");
      setUploadProgress("");

      // バリデーションログを送信
      try {
        await api.post("/api/v1/admin/csv-validation-log", {
          video_id: videoId,
          video_filename: videoData?.original_filename || "",
          action: "replace",
          verdict: validationResult?.overallVerdict || "ok",
          decision: "continue",
          checks: validationResult?.checks || [],
          product_filename: productFile?.name || null,
          trend_filename: trendFile?.name || null,
        });
      } catch (logErr) {
        console.warn("[CsvReplaceModal] Failed to log validation:", logErr);
      }

      // video_upload_assets のバリデーション状態を更新
      try {
        const verdict = validationResult?.overallVerdict || "ok";
        const statusMap = { ok: "ok", warning: "warning", mismatch: "error", unknown: "unknown" };
        const validationStatus = statusMap[verdict] || "unknown";

        if (trendBlobUrl) {
          await api.post(`/api/v1/videos/${videoId}/update-validation-status`, {
            asset_type: "trend_csv",
            validation_status: validationStatus,
            validation_result: validationResult || null,
          });
        }
        if (productBlobUrl) {
          await api.post(`/api/v1/videos/${videoId}/update-validation-status`, {
            asset_type: "product_csv",
            validation_status: validationStatus,
            validation_result: validationResult || null,
          });
        }
      } catch (valErr) {
        console.warn("[CsvReplaceModal] Failed to update validation status:", valErr);
      }

      if (onComplete) {
        onComplete({
          productReplaced: !!productBlobUrl,
          trendReplaced: !!trendBlobUrl,
          reprocessStatus: replaceRes?.reprocess_status || "unknown",
        });
      }
    } catch (err) {
      console.error("[CsvReplaceModal] Upload error:", err);
      setStep("error");
      setError(err.message || window.__t('uploadFailedMessage', 'アップロードに失敗しました'));
    }
  };

  // Blobストレージへのアップロード
  const uploadToBlob = async (sasUrl, file) => {
    const response = await fetch(sasUrl, {
      method: "PUT",
      headers: {
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": file.type || "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
      body: file,
    });
    if (!response.ok) {
      throw new Error(`Blob upload failed: ${response.status}`);
    }
  };

  // 判定結果の色
  const verdictColor = (verdict) => {
    switch (verdict) {
      case "ok": return { bg: "bg-green-50", border: "border-green-200", text: "text-green-700", icon: "text-green-500" };
      case "warning": return { bg: "bg-yellow-50", border: "border-yellow-200", text: "text-yellow-700", icon: "text-yellow-500" };
      case "mismatch": return { bg: "bg-red-50", border: "border-red-200", text: "text-red-700", icon: "text-red-500" };
      default: return { bg: "bg-gray-50", border: "border-gray-200", text: "text-gray-700", icon: "text-gray-500" };
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        {/* ヘッダー */}
        <div className="flex items-center justify-between p-5 border-b border-gray-100">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-blue-50 flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-blue-500">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </div>
            <div>
              <h3 className="text-lg font-semibold text-gray-900">{window.__t('csvReplaceModal_ae80d1', window.__t('csvReplaceModal_ae80d1', 'CSV / Excel 差し替え'))}</h3>
              <p className="text-xs text-gray-400 mt-0.5 truncate max-w-[280px]">
                {videoData?.original_filename}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-full transition-colors"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-gray-400">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>

        {/* コンテンツ */}
        <div className="p-5">
          {/* Step: ファイル選択 */}
          {step === "select" && (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">
                差し替えたいExcelファイルを選択してください。選択しなかったファイルは現在のまま維持されます。
              </p>

              {/* 商品データ */}
              <div className="border border-gray-200 rounded-xl p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-700">{window.__t('videoDetail_productData', window.__t('videoDetail_productData', '商品データ'))}</span>
                    <span className="text-xs text-gray-400">(Product Excel)</span>
                  </div>
                  <button
                    onClick={() => productInputRef.current?.click()}
                    className="px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
                  >
                    ファイル選択
                  </button>
                  <input
                    ref={productInputRef}
                    type="file"
                    accept=".xlsx,.xls,.csv"
                    onChange={handleProductSelect}
                    className="hidden"
                  />
                </div>
                {productFile && (
                  <div className="mt-2 flex items-center gap-2 text-xs text-green-600 bg-green-50 rounded-lg px-3 py-2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                    {productFile.name}
                    <button onClick={() => setProductFile(null)} className="ml-auto text-gray-400 hover:text-red-500">
                      <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                  </div>
                )}
              </div>

              {/* トレンドデータ */}
              <div className="border border-gray-200 rounded-xl p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-700">{window.__t('videoDetail_trendData', window.__t('videoDetail_trendData', 'トレンドデータ'))}</span>
                    <span className="text-xs text-gray-400">(Trend Stats Excel)</span>
                  </div>
                  <button
                    onClick={() => trendInputRef.current?.click()}
                    className="px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
                  >
                    ファイル選択
                  </button>
                  <input
                    ref={trendInputRef}
                    type="file"
                    accept=".xlsx,.xls,.csv"
                    onChange={handleTrendSelect}
                    className="hidden"
                  />
                </div>
                {trendFile && (
                  <div className="mt-2 flex items-center gap-2 text-xs text-green-600 bg-green-50 rounded-lg px-3 py-2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                    {trendFile.name}
                    <button onClick={() => setTrendFile(null)} className="ml-auto text-gray-400 hover:text-red-500">
                      <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                  </div>
                )}
              </div>

              {/* 注意事項 */}
              <div className="bg-amber-50 border border-amber-200 rounded-xl p-3">
                <div className="flex items-start gap-2">
                  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-amber-500 mt-0.5 flex-shrink-0">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                  </svg>
                  <div className="text-xs text-amber-700">
                    <p className="font-medium">{window.__t('csvReplaceModal_2be11b', window.__t('csvReplaceModal_2be11b', '差し替え後、AI分析が自動的に再実行されます。'))}</p>
                    <p className="mt-1">{window.__t('csvReplaceModal_718def', window.__t('csvReplaceModal_718def', 'Sales Moment / Hook Detection / Live Report などの結果が更新されます。処理には数分かかる場合があります。'))}</p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Step: バリデーション中 */}
          {step === "validating" && (
            <div className="flex flex-col items-center gap-3 py-8">
              <div className="animate-spin w-8 h-8 border-3 border-blue-200 border-t-blue-600 rounded-full" />
              <p className="text-sm text-gray-600">{window.__t('csvReplaceModal_3d8600', window.__t('csvReplaceModal_3d8600', '日時を照合中...'))}</p>
            </div>
          )}

          {/* Step: バリデーション結果 */}
          {step === "validation_result" && validationResult && (
            <div className="space-y-4">
              <div className={`rounded-xl p-4 border ${verdictColor(validationResult.overallVerdict).bg} ${verdictColor(validationResult.overallVerdict).border}`}>
                <div className="flex items-center gap-2 mb-2">
                  {validationResult.overallVerdict === "warning" && (
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-yellow-500">
                      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                    </svg>
                  )}
                  {validationResult.overallVerdict === "mismatch" && (
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-red-500">
                      <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
                    </svg>
                  )}
                  <span className={`text-sm font-semibold ${verdictColor(validationResult.overallVerdict).text}`}>
                    {validationResult.overallVerdict === "warning" ? "日時にズレがあります" :
                     validationResult.overallVerdict === "mismatch" ? "日時が一致しません" :
                     window.__t('csvReplaceModal_8c3054', '確認が必要です')}
                  </span>
                </div>
                <p className="text-xs text-gray-600">{validationResult.summary}</p>

                {/* 個別チェック結果 */}
                {validationResult.checks && validationResult.checks.length > 0 && (
                  <div className="mt-3 space-y-1.5">
                    {validationResult.checks.map((check, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                          check.verdict === "ok" ? "bg-green-400" :
                          check.verdict === "warning" ? "bg-yellow-400" :
                          check.verdict === "mismatch" ? "bg-red-400" : "bg-gray-300"
                        }`} />
                        <span className="text-gray-600">{check.label}: {check.detail || check.message}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Step: アップロード中 */}
          {step === "uploading" && (
            <div className="flex flex-col items-center gap-3 py-8">
              <div className="animate-spin w-8 h-8 border-3 border-blue-200 border-t-blue-600 rounded-full" />
              <p className="text-sm text-gray-600">{uploadProgress}</p>
            </div>
          )}

          {/* Step: 完了 */}
          {step === "done" && (
            <div className="flex flex-col items-center gap-3 py-8">
              <div className="w-12 h-12 rounded-full bg-green-100 flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-green-600">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
              </div>
              <p className="text-sm font-medium text-gray-800">{window.__t('csvReplaceModal_e11077', window.__t('csvReplaceModal_e11077', '差し替え完了'))}</p>
              <p className="text-xs text-gray-500 text-center">
                AI分析の再処理がキューに追加されました。<br />
                結果が反映されるまで数分お待ちください。
              </p>
            </div>
          )}

          {/* Step: エラー */}
          {step === "error" && (
            <div className="flex flex-col items-center gap-3 py-8">
              <div className="w-12 h-12 rounded-full bg-red-100 flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-red-600">
                  <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
                </svg>
              </div>
              <p className="text-sm font-medium text-red-700">{window.__t('csvReplaceModal_34117e', window.__t('csvReplaceModal_34117e', '差し替えに失敗しました'))}</p>
              <p className="text-xs text-gray-500">{error}</p>
            </div>
          )}
        </div>

        {/* フッター */}
        <div className="flex items-center justify-end gap-3 p-5 border-t border-gray-100">
          {step === "select" && (
            <>
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              >
                キャンセル
              </button>
              <button
                onClick={handleValidate}
                disabled={!productFile && !trendFile}
                className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
                  productFile || trendFile
                    ? "bg-blue-600 text-white hover:bg-blue-700"
                    : "bg-gray-200 text-gray-400 cursor-not-allowed"
                }`}
              >
                差し替え実行
              </button>
            </>
          )}

          {step === "validation_result" && (
            <>
              <button
                onClick={() => { setStep("select"); setValidationResult(null); }}
                className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              >
                ファイルを変更
              </button>
              {validationResult?.overallVerdict === "mismatch" ? (
                <button
                  onClick={handleUpload}
                  className="px-4 py-2 text-sm font-medium bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
                >
                  強制的に差し替え
                </button>
              ) : (
                <button
                  onClick={handleUpload}
                  className="px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                >
                  このまま差し替え
                </button>
              )}
            </>
          )}

          {(step === "done" || step === "error") && (
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium bg-gray-800 text-white rounded-lg hover:bg-gray-900 transition-colors"
            >
              閉じる
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
