/**
 * CSV Date/Time Validator
 *
 * 動画ファイルとCSVファイルの日時を照合し、
 * 誤紐付けを防ぐための判定ロジック。
 *
 * 判定結果:
 *   'ok'      - 一致（0〜5分差）→ 自動続行
 *   'warning' - 近いが怪しい（5〜15分差）→ 確認ダイアログ
 *   'error'   - 明らかに違う（15分以上差 or 日付不一致）→ 強い警告
 *   'unknown' - 判定不能（情報不足）→ 確認ダイアログ
 */

import {
  extractVideoDateTime,
  extractExcelFileNameDate,
  extractTrendStartTime,
  extractProductDate,
} from './csvDateTimeExtractor';

// ─── 定数 ───

const THRESHOLD_OK_MINUTES = 5;       // 0〜5分: OK
const THRESHOLD_WARNING_MINUTES = 15; // 5〜15分: 要確認
// 15分以上: エラー

// ─── メインバリデーション関数 ───

/**
 * 動画ファイルとCSVファイルの日時を照合する
 *
 * @param {File|File[]} videoFiles - 動画ファイル（1本 or 複数）
 * @param {File} productExcel - 商品データExcel
 * @param {File} trendExcel - トレンドデータExcel
 * @returns {Promise<ValidationResult>}
 *
 * @typedef {Object} ValidationResult
 * @property {'ok'|'warning'|'error'|'unknown'} verdict - 総合判定
 * @property {string} verdictLabel - 判定ラベル（日本語）
 * @property {CheckItem[]} checks - 個別チェック結果
 * @property {string} summary - 判定サマリー
 * @property {Object} extracted - 抽出された日時情報
 */
export async function validateCsvDateTime(videoFiles, productExcel, trendExcel) {
  const files = Array.isArray(videoFiles) ? videoFiles : [videoFiles];
  const primaryVideo = files[0];

  // 1. 各ソースから日時を抽出
  const videoInfo = extractVideoDateTime(primaryVideo?.name);
  const trendInfo = await extractTrendStartTime(trendExcel);
  const productInfo = await extractProductDate(productExcel);
  const trendFileInfo = extractExcelFileNameDate(trendExcel?.name);

  const extracted = {
    video: {
      filename: primaryVideo?.name || '',
      date: videoInfo.date,
      confidence: videoInfo.confidence,
      source: videoInfo.source,
    },
    trend: {
      filename: trendExcel?.name || '',
      startTime: trendInfo.time,
      lastTime: trendInfo.lastTime,
      allTimes: trendInfo.allTimes,
      fileDate: trendFileInfo.date,
      confidence: trendInfo.confidence,
    },
    product: {
      filename: productExcel?.name || '',
      date: productInfo.date,
      confidence: productInfo.confidence,
      source: productInfo.source,
    },
  };

  // 2. 個別チェックを実行
  const checks = [];

  // Check 1: 動画日付 vs trend CSV ファイル名日付
  checks.push(checkDateMatch(
    'video_vs_trend_date',
    '動画日付 vs トレンドCSV日付',
    videoInfo.date,
    trendFileInfo.date,
    videoInfo.confidence,
    trendFileInfo.confidence,
  ));

  // Check 2: 動画日付 vs product CSV 日付
  checks.push(checkDateMatch(
    'video_vs_product_date',
    '動画日付 vs 商品CSV日付',
    videoInfo.date,
    productInfo.date,
    videoInfo.confidence,
    productInfo.confidence,
  ));

  // Check 3: 動画開始時刻 vs trend CSV 開始時刻
  checks.push(checkTimeMatch(
    'video_vs_trend_time',
    '動画開始時刻 vs トレンドCSV開始時刻',
    videoInfo.date,
    trendInfo.time,
    videoInfo.confidence,
    trendInfo.confidence,
  ));

  // Check 4: product CSV 日付 vs trend CSV 日付（相互整合性）
  checks.push(checkDateMatch(
    'product_vs_trend_date',
    '商品CSV日付 vs トレンドCSV日付',
    productInfo.date,
    trendFileInfo.date,
    productInfo.confidence,
    trendFileInfo.confidence,
  ));

  // 3. 総合判定
  const verdict = computeOverallVerdict(checks);

  return {
    verdict: verdict.level,
    verdictLabel: verdict.label,
    checks,
    summary: verdict.summary,
    extracted,
  };
}

// ─── 個別チェック関数 ───

/**
 * 日付の一致チェック（日単位）
 */
function checkDateMatch(id, label, date1, date2, conf1, conf2) {
  if (!date1 || !date2 || conf1 === 'none' || conf2 === 'none') {
    return {
      id,
      label,
      result: 'unknown',
      resultLabel: '判定不能',
      detail: date1 ? (date2 ? '信頼度が低い' : '比較対象の日付を取得できません') : '動画の日付を取得できません',
      date1: date1 ? formatDate(date1) : null,
      date2: date2 ? formatDate(date2) : null,
    };
  }

  const sameDay = date1.getFullYear() === date2.getFullYear() &&
                  date1.getMonth() === date2.getMonth() &&
                  date1.getDate() === date2.getDate();

  if (sameDay) {
    return {
      id,
      label,
      result: 'ok',
      resultLabel: '一致',
      detail: `${formatDate(date1)} = ${formatDate(date2)}`,
      date1: formatDate(date1),
      date2: formatDate(date2),
    };
  }

  // 1日差なら warning（深夜のライブで日付をまたぐケース）
  const diffDays = Math.abs(daysBetween(date1, date2));
  if (diffDays === 1) {
    return {
      id,
      label,
      result: 'warning',
      resultLabel: '1日ズレ',
      detail: `${formatDate(date1)} vs ${formatDate(date2)}（1日差 - 深夜ライブの可能性）`,
      date1: formatDate(date1),
      date2: formatDate(date2),
      diffDays,
    };
  }

  return {
    id,
    label,
    result: 'error',
    resultLabel: '日付不一致',
    detail: `${formatDate(date1)} vs ${formatDate(date2)}（${diffDays}日差）`,
    date1: formatDate(date1),
    date2: formatDate(date2),
    diffDays,
  };
}

/**
 * 時刻の一致チェック（分単位）
 */
function checkTimeMatch(id, label, videoDate, trendTimeStr, videoConf, trendConf) {
  if (!videoDate || !trendTimeStr || videoConf === 'none' || trendConf === 'none') {
    return {
      id,
      label,
      result: 'unknown',
      resultLabel: '判定不能',
      detail: !videoDate ? '動画の開始時刻を取得できません' : 'トレンドCSVの開始時刻を取得できません',
      time1: videoDate ? formatTime(videoDate) : null,
      time2: trendTimeStr || null,
    };
  }

  const videoMinutes = videoDate.getHours() * 60 + videoDate.getMinutes();
  const [trendH, trendM] = trendTimeStr.split(':').map(Number);
  const trendMinutes = trendH * 60 + trendM;

  // 差分計算（24時間をまたぐケースも考慮）
  let diffMinutes = Math.abs(videoMinutes - trendMinutes);
  if (diffMinutes > 720) diffMinutes = 1440 - diffMinutes; // 12時間以上なら逆回り

  const time1 = formatTime(videoDate);
  const time2 = trendTimeStr;

  if (diffMinutes <= THRESHOLD_OK_MINUTES) {
    return {
      id,
      label,
      result: 'ok',
      resultLabel: '一致',
      detail: `${time1} vs ${time2}（${diffMinutes}分差）`,
      time1,
      time2,
      diffMinutes,
    };
  }

  if (diffMinutes <= THRESHOLD_WARNING_MINUTES) {
    return {
      id,
      label,
      result: 'warning',
      resultLabel: '要確認',
      detail: `${time1} vs ${time2}（${diffMinutes}分差）`,
      time1,
      time2,
      diffMinutes,
    };
  }

  return {
    id,
    label,
    result: 'error',
    resultLabel: '時刻不一致',
    detail: `${time1} vs ${time2}（${diffMinutes}分差）`,
    time1,
    time2,
    diffMinutes,
  };
}

// ─── 総合判定 ───

function computeOverallVerdict(checks) {
  const hasError = checks.some(c => c.result === 'error');
  const hasWarning = checks.some(c => c.result === 'warning');
  const hasOk = checks.some(c => c.result === 'ok');
  const allUnknown = checks.every(c => c.result === 'unknown');

  if (hasError) {
    const errorChecks = checks.filter(c => c.result === 'error');
    return {
      level: 'error',
      label: '不一致',
      summary: `${errorChecks.map(c => c.label).join('、')} が一致しません。CSVファイルが正しいか確認してください。`,
    };
  }

  if (hasWarning) {
    const warningChecks = checks.filter(c => c.result === 'warning');
    return {
      level: 'warning',
      label: '要確認',
      summary: `${warningChecks.map(c => c.label).join('、')} にズレがあります。内容を確認してください。`,
    };
  }

  if (allUnknown) {
    return {
      level: 'unknown',
      label: '判定不能',
      summary: 'ファイル名や内容から日時情報を十分に取得できませんでした。手動で確認してください。',
    };
  }

  if (hasOk) {
    return {
      level: 'ok',
      label: '一致',
      summary: '動画とCSVの日時が一致しています。',
    };
  }

  return {
    level: 'unknown',
    label: '判定不能',
    summary: '日時の照合ができませんでした。',
  };
}

// ─── ユーティリティ ───

function formatDate(d) {
  if (!d) return '-';
  return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
}

function formatTime(d) {
  if (!d) return '-';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function daysBetween(d1, d2) {
  const oneDay = 24 * 60 * 60 * 1000;
  const t1 = new Date(d1.getFullYear(), d1.getMonth(), d1.getDate()).getTime();
  const t2 = new Date(d2.getFullYear(), d2.getMonth(), d2.getDate()).getTime();
  return Math.round((t2 - t1) / oneDay);
}
