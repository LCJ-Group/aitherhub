/**
 * CsvValidationGate
 *
 * アップロード前にCSVと動画の日時照合結果を表示し、
 * ユーザーに確認を求めるモーダルコンポーネント。
 *
 * Props:
 *   validationResult - validateCsvDateTime() の結果
 *   onContinue       - 「このまま続行」ボタン
 *   onReplace        - 「CSVを差し替える」ボタン
 *   onForce          - 「強制的に続行」ボタン（error時のみ表示）
 *   onClose          - モーダルを閉じる
 *   isValidating     - バリデーション中フラグ
 */
import React from 'react';
import { useTranslation } from 'react-i18next';

const VERDICT_CONFIG = {
  ok: {
    bg: 'bg-green-50',
    border: 'border-green-200',
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#16a34a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
        <polyline points="22 4 12 14.01 9 11.01" />
      </svg>
    ),
    title: window.__t('csvValidationGate_d17ea1', '日時チェック: 一致'),
    titleColor: 'text-green-800',
  },
  warning: {
    bg: 'bg-amber-50',
    border: 'border-amber-200',
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#d97706" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
    ),
    title: window.__t('csvValidationGate_2c77bd', '日時チェック: 要確認'),
    titleColor: 'text-amber-800',
  },
  error: {
    bg: 'bg-red-50',
    border: 'border-red-300',
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#dc2626" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <line x1="15" y1="9" x2="9" y2="15" />
        <line x1="9" y1="9" x2="15" y2="15" />
      </svg>
    ),
    title: window.__t('csvValidationGate_932e70', '日時チェック: 不一致'),
    titleColor: 'text-red-800',
  },
  unknown: {
    bg: 'bg-gray-50',
    border: 'border-gray-200',
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
    ),
    title: window.__t('csvValidationGate_60f465', '日時チェック: 判定不能'),
    titleColor: 'text-gray-700',
  },
};

const CHECK_RESULT_BADGE = {
  ok: { bg: 'bg-green-100', text: 'text-green-700', label: 'OK' },
  warning: { bg: 'bg-amber-100', text: 'text-amber-700', label: window.__t('csvAssetPanel_248521', '要確認') },
  error: { bg: 'bg-red-100', text: 'text-red-700', label: window.__t('csvAssetPanel_a6f60d', '不一致') },
  unknown: { bg: 'bg-gray-100', text: 'text-gray-500', label: window.__t('csvAssetPanel_efd626', '不明') },
};

export default function CsvValidationGate({
  validationResult,
  onContinue,
  onReplace,
  onForce,
  onClose,
  isValidating,
}) {
  useTranslation(); // triggers re-render on language change
  // バリデーション中
  if (isValidating) {
    return (
      <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
        <div className="bg-white rounded-2xl p-8 max-w-md w-full mx-4 shadow-xl">
          <div className="flex flex-col items-center space-y-4">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-[#7D01FF]" />
            <p className="text-sm text-gray-600">{window.__t('csvValidationGate_d13857', 'CSVファイルの日時を確認中...')}</p>
          </div>
        </div>
      </div>
    );
  }

  if (!validationResult) return null;

  const { verdict, summary, checks, extracted } = validationResult;
  const config = VERDICT_CONFIG[verdict] || VERDICT_CONFIG.unknown;

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl max-w-lg w-full mx-4 shadow-xl overflow-hidden max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className={`${config.bg} ${config.border} border-b px-6 py-4 flex items-center gap-3`}>
          {config.icon}
          <div>
            <h3 className={`text-base font-bold ${config.titleColor}`}>{config.title}</h3>
            <p className="text-xs text-gray-600 mt-0.5">{summary}</p>
          </div>
        </div>

        {/* Body - scrollable */}
        <div className="px-6 py-4 overflow-y-auto flex-1">
          {/* 抽出情報サマリー */}
          <div className="mb-4">
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">{window.__t('csvValidationGate_a9d16e', '抽出された日時情報')}</h4>
            <div className="space-y-2">
              <InfoRow
                label={window.__t('videoTitleFallback', '動画')}
                filename={extracted.video.filename}
                value={extracted.video.date ? formatDateTime(extracted.video.date) : window.__t('csvValidationGate_4176ad', '取得不可')}
                confidence={extracted.video.confidence}
              />
              <InfoRow
                label={window.__t('csvValidationGate_5070f7', 'トレンドCSV')}
                filename={extracted.trend.filename}
                value={extracted.trend.startTime ? `開始 ${extracted.trend.startTime}${extracted.trend.lastTime ? ` 〜 ${extracted.trend.lastTime}` : ''}` : window.__t('csvValidationGate_4176ad', '取得不可')}
                confidence={extracted.trend.confidence}
                subValue={extracted.trend.fileDate ? `ファイル日付: ${formatDateOnly(extracted.trend.fileDate)}` : null}
              />
              <InfoRow
                label={window.__t('csvValidationGate_ce3200', '商品CSV')}
                filename={extracted.product.filename}
                value={extracted.product.date ? formatDateOnly(extracted.product.date) : window.__t('csvValidationGate_4176ad', '取得不可')}
                confidence={extracted.product.confidence}
              />
            </div>
          </div>

          {/* 個別チェック結果 */}
          <div className="mb-2">
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">{window.__t('csvValidationGate_91364d', 'チェック結果')}</h4>
            <div className="space-y-1.5">
              {checks.map((check) => {
                const badge = CHECK_RESULT_BADGE[check.result] || CHECK_RESULT_BADGE.unknown;
                return (
                  <div key={check.id} className="flex items-center justify-between py-1.5 px-3 rounded-lg bg-gray-50">
                    <span className="text-xs text-gray-700">{check.label}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500">{check.detail}</span>
                      <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${badge.bg} ${badge.text}`}>
                        {check.resultLabel}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Footer - Action Buttons */}
        <div className="px-6 py-4 border-t border-gray-100 bg-gray-50">
          {verdict === 'ok' ? (
            <div className="flex gap-2">
              <button
                onClick={onContinue}
                className="flex-1 h-[41px] flex items-center justify-center bg-[#7D01FF] text-white rounded-lg text-sm font-medium hover:bg-[#6a01d9] transition-colors"
              >
                アップロード開始
              </button>
              <button
                onClick={onReplace}
                className="h-[41px] px-4 flex items-center justify-center bg-white text-gray-600 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 transition-colors"
              >
                CSVを差し替える
              </button>
            </div>
          ) : verdict === 'warning' || verdict === 'unknown' ? (
            <div className="flex flex-col gap-2">
              <div className="flex gap-2">
                <button
                  onClick={onContinue}
                  className="flex-1 h-[41px] flex items-center justify-center bg-amber-500 text-white rounded-lg text-sm font-medium hover:bg-amber-600 transition-colors"
                >
                  このまま続行
                </button>
                <button
                  onClick={onReplace}
                  className="flex-1 h-[41px] flex items-center justify-center bg-white text-gray-700 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
                >
                  CSVを差し替える
                </button>
              </div>
            </div>
          ) : (
            /* error */
            <div className="flex flex-col gap-2">
              <button
                onClick={onReplace}
                className="w-full h-[41px] flex items-center justify-center bg-[#7D01FF] text-white rounded-lg text-sm font-medium hover:bg-[#6a01d9] transition-colors"
              >
                CSVを差し替える
              </button>
              <button
                onClick={onForce}
                className="w-full h-[36px] flex items-center justify-center bg-white text-red-500 border border-red-200 rounded-lg text-xs hover:bg-red-50 transition-colors"
              >
                日時が違うことを理解した上で強制的に続行
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── サブコンポーネント ───

function InfoRow({ label, filename, value, confidence, subValue }) {
  const confColor = {
    high: 'text-green-600',
    medium: 'text-amber-600',
    low: 'text-gray-400',
    none: 'text-gray-300',
  };

  return (
    <div className="flex items-start gap-2 py-1.5 px-3 rounded-lg bg-white border border-gray-100">
      <span className="text-xs font-medium text-gray-500 w-20 flex-shrink-0 pt-0.5">{label}</span>
      <div className="flex-1 min-w-0">
        <p className="text-xs text-gray-400 truncate" title={filename}>{filename}</p>
        <p className={`text-sm font-medium ${confidence === 'none' ? 'text-gray-400' : 'text-gray-800'}`}>
          {value}
        </p>
        {subValue && (
          <p className="text-xs text-gray-500 mt-0.5">{subValue}</p>
        )}
      </div>
      <span className={`text-[10px] ${confColor[confidence] || confColor.none} flex-shrink-0 pt-0.5`}>
        {confidence === 'high' ? [window.__t('csvValidationGate_242abd', '高信頼')] : confidence === 'medium' ? [window.__t('csvValidationGate_a585a1', '中信頼')] : confidence === 'low' ? [window.__t('csvValidationGate_597cb3', '低信頼')] : window.__t('csvAssetPanel_efd626', '不明')}
      </span>
    </div>
  );
}

// ─── ユーティリティ ───

function formatDateTime(d) {
  if (!d) return '-';
  return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function formatDateOnly(d) {
  if (!d) return '-';
  return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
}
