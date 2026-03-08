/**
 * CSV Date/Time Extractor
 *
 * Excelファイルとファイル名から日時情報を抽出するユーティリティ。
 * CSV Date/Time Validation Gate の基盤。
 */
import * as XLSX from 'xlsx';

// ─── ファイル名から日時を抽出 ───

/**
 * 動画ファイル名から開始日時を推定する
 *
 * 対応パターン:
 *   ryukyogoku-20260130-1959...mp4  → 2026-01-30 19:59
 *   nana.tokyoselect.no1-20260127-1637.mp4 → 2026-01-27 16:37
 *   RPReplay_Final1771074072.MP4 → Unix timestamp → Date
 *   2026-01-30_19-59-00.mp4 → 2026-01-30 19:59:00
 *   20260130_195900.mp4 → 2026-01-30 19:59:00
 *
 * @param {string} filename
 * @returns {{ date: Date|null, confidence: 'high'|'medium'|'low'|'none', source: string }}
 */
export function extractVideoDateTime(filename) {
  if (!filename) return { date: null, confidence: 'none', source: 'no_filename' };

  const name = filename.replace(/\.[^.]+$/, ''); // 拡張子を除去

  // Pattern 1: YYYYMMDD-HHMM (most common in AitherHub)
  // e.g., ryukyogoku-20260130-1959, nana.tokyoselect.no1-20260127-1637
  const p1 = name.match(/(\d{4})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[-_](\d{2})(\d{2})/);
  if (p1) {
    const d = new Date(+p1[1], +p1[2] - 1, +p1[3], +p1[4], +p1[5]);
    if (isValidDate(d)) return { date: d, confidence: 'high', source: 'filename_YYYYMMDD_HHMM' };
  }

  // Pattern 2: YYYY-MM-DD_HH-MM-SS or YYYY-MM-DD_HHMM
  const p2 = name.match(/(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])[_T](\d{2})[:-](\d{2})(?:[:-](\d{2}))?/);
  if (p2) {
    const d = new Date(+p2[1], +p2[2] - 1, +p2[3], +p2[4], +p2[5], +(p2[6] || 0));
    if (isValidDate(d)) return { date: d, confidence: 'high', source: 'filename_ISO' };
  }

  // Pattern 3: YYYYMMDD_HHMMSS
  const p3 = name.match(/(\d{4})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])_(\d{2})(\d{2})(\d{2})/);
  if (p3) {
    const d = new Date(+p3[1], +p3[2] - 1, +p3[3], +p3[4], +p3[5], +p3[6]);
    if (isValidDate(d)) return { date: d, confidence: 'high', source: 'filename_YYYYMMDDHHMMSS' };
  }

  // Pattern 4: RPReplay_Final{unix_timestamp} (iOS screen recording)
  const p4 = name.match(/RPReplay_Final(\d{10,13})/i);
  if (p4) {
    const ts = p4[1].length > 10 ? +p4[1] : +p4[1] * 1000;
    const d = new Date(ts);
    if (isValidDate(d)) return { date: d, confidence: 'medium', source: 'filename_unix_timestamp' };
  }

  // Pattern 5: YYYYMMDD only (date without time)
  const p5 = name.match(/(\d{4})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])/);
  if (p5) {
    const d = new Date(+p5[1], +p5[2] - 1, +p5[3]);
    if (isValidDate(d)) return { date: d, confidence: 'low', source: 'filename_date_only' };
  }

  return { date: null, confidence: 'none', source: 'filename_no_match' };
}

/**
 * Excelファイル名から日付を抽出する
 *
 * 対応パターン:
 *   2月16号商品数据.xlsx → 2/16
 *   商品数据_20260216.xlsx → 2026-02-16
 *   trend_stats_2026-02-16.xlsx → 2026-02-16
 *   1月30日トレンド.xlsx → 1/30
 *
 * @param {string} filename
 * @param {number} [referenceYear] - 年が不明な場合の参照年
 * @returns {{ date: Date|null, confidence: 'high'|'medium'|'low'|'none', source: string }}
 */
export function extractExcelFileNameDate(filename, referenceYear) {
  if (!filename) return { date: null, confidence: 'none', source: 'no_filename' };

  const name = filename.replace(/\.[^.]+$/, '');
  const year = referenceYear || new Date().getFullYear();

  // Pattern 1: YYYYMMDD or YYYY-MM-DD in filename
  const p1 = name.match(/(\d{4})[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])/);
  if (p1) {
    const d = new Date(+p1[1], +p1[2] - 1, +p1[3]);
    if (isValidDate(d)) return { date: d, confidence: 'high', source: 'excel_filename_YYYYMMDD' };
  }

  // Pattern 2: Chinese date format - M月DD号 or M月DD日
  const p2 = name.match(/(\d{1,2})月(\d{1,2})[号日]/);
  if (p2) {
    const d = new Date(year, +p2[1] - 1, +p2[2]);
    if (isValidDate(d)) return { date: d, confidence: 'medium', source: 'excel_filename_chinese_date' };
  }

  // Pattern 3: MM-DD or MM/DD
  const p3 = name.match(/(?:^|[^0-9])(\d{1,2})[-/](\d{1,2})(?:[^0-9]|$)/);
  if (p3 && +p3[1] >= 1 && +p3[1] <= 12 && +p3[2] >= 1 && +p3[2] <= 31) {
    const d = new Date(year, +p3[1] - 1, +p3[2]);
    if (isValidDate(d)) return { date: d, confidence: 'low', source: 'excel_filename_MMDD' };
  }

  return { date: null, confidence: 'none', source: 'excel_filename_no_match' };
}

// ─── Excelの中身から時間を抽出 ───

/**
 * trend_stats Excelファイルの「時間」列から最初の時刻を抽出する
 *
 * @param {File} file - trend_stats Excelファイル
 * @returns {Promise<{ time: string|null, date: Date|null, allTimes: string[], confidence: 'high'|'medium'|'none' }>}
 */
export async function extractTrendStartTime(file) {
  try {
    const data = await readFileAsArrayBuffer(file);
    const workbook = XLSX.read(data, { type: 'array' });

    // 最初のシートを使用
    const sheetName = workbook.SheetNames[0];
    const sheet = workbook.Sheets[sheetName];
    const rows = XLSX.utils.sheet_to_json(sheet, { header: 1 });

    if (!rows || rows.length < 2) {
      return { time: null, date: null, allTimes: [], confidence: 'none' };
    }

    // 「時間」列を探す
    const headerRow = rows[0];
    let timeColIndex = -1;

    for (let i = 0; i < headerRow.length; i++) {
      const header = String(headerRow[i] || '').trim();
      if (header === '時間' || header === 'Time' || header === '时间' ||
          header.toLowerCase() === 'time' || header === 'タイム') {
        timeColIndex = i;
        break;
      }
    }

    // 「時間」列が見つからない場合、最初の列を試す
    if (timeColIndex === -1) {
      // 最初の列の値が時刻っぽいかチェック
      if (rows.length > 1 && isTimeString(String(rows[1][0] || ''))) {
        timeColIndex = 0;
      }
    }

    if (timeColIndex === -1) {
      return { time: null, date: null, allTimes: [], confidence: 'none' };
    }

    // 全時刻を抽出
    const allTimes = [];
    for (let i = 1; i < rows.length; i++) {
      const val = rows[i][timeColIndex];
      if (val != null) {
        const timeStr = parseTimeValue(val);
        if (timeStr) allTimes.push(timeStr);
      }
    }

    if (allTimes.length === 0) {
      return { time: null, date: null, allTimes: [], confidence: 'none' };
    }

    const firstTime = allTimes[0];
    const lastTime = allTimes[allTimes.length - 1];

    return {
      time: firstTime,
      lastTime,
      date: null, // 日付はファイル名から取得
      allTimes,
      confidence: 'high',
    };
  } catch (err) {
    console.error('[csvDateTimeExtractor] Failed to read trend Excel:', err);
    return { time: null, date: null, allTimes: [], confidence: 'none' };
  }
}

/**
 * product Excelファイルの日付情報を抽出する（ファイル名 + 中身）
 *
 * @param {File} file - product Excelファイル
 * @returns {Promise<{ date: Date|null, confidence: 'high'|'medium'|'none', source: string }>}
 */
export async function extractProductDate(file) {
  // まずファイル名から日付を試みる
  const fromName = extractExcelFileNameDate(file.name);
  if (fromName.date) return fromName;

  // ファイル名から取れなかった場合、中身を確認
  try {
    const data = await readFileAsArrayBuffer(file);
    const workbook = XLSX.read(data, { type: 'array' });
    const sheetName = workbook.SheetNames[0];
    const sheet = workbook.Sheets[sheetName];
    const rows = XLSX.utils.sheet_to_json(sheet, { header: 1 });

    // 日付列を探す
    if (rows.length > 0) {
      const headerRow = rows[0];
      for (let i = 0; i < headerRow.length; i++) {
        const header = String(headerRow[i] || '').trim().toLowerCase();
        if (header.includes('date') || header.includes('日付') || header.includes('日期')) {
          if (rows.length > 1 && rows[1][i]) {
            const dateVal = parseDateValue(rows[1][i]);
            if (dateVal) return { date: dateVal, confidence: 'medium', source: 'excel_content_date_column' };
          }
        }
      }
    }
  } catch (err) {
    console.error('[csvDateTimeExtractor] Failed to read product Excel:', err);
  }

  return { date: null, confidence: 'none', source: 'product_no_date' };
}

// ─── ヘルパー関数 ───

function readFileAsArrayBuffer(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => resolve(e.target.result);
    reader.onerror = (e) => reject(e);
    reader.readAsArrayBuffer(file);
  });
}

function isValidDate(d) {
  return d instanceof Date && !isNaN(d.getTime()) && d.getFullYear() >= 2020 && d.getFullYear() <= 2030;
}

function isTimeString(str) {
  return /^\d{1,2}:\d{2}(:\d{2})?$/.test(str.trim());
}

/**
 * Excelの時刻値をHH:MM形式の文字列に変換する
 * Excelは時刻を0〜1の小数で保存する場合がある（例: 0.75 = 18:00）
 */
function parseTimeValue(val) {
  if (typeof val === 'string') {
    const trimmed = val.trim();
    // HH:MM or HH:MM:SS
    const m = trimmed.match(/^(\d{1,2}):(\d{2})(:\d{2})?$/);
    if (m) return `${m[1].padStart(2, '0')}:${m[2]}`;
    return null;
  }

  if (typeof val === 'number') {
    // Excel serial time (0-1 range)
    if (val >= 0 && val < 1) {
      const totalMinutes = Math.round(val * 24 * 60);
      const hours = Math.floor(totalMinutes / 60);
      const minutes = totalMinutes % 60;
      return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
    }
    // Excel serial date+time (> 1)
    if (val > 1 && val < 100000) {
      const date = XLSX.SSF.parse_date_code(val);
      if (date) {
        return `${String(date.H || 0).padStart(2, '0')}:${String(date.M || 0).padStart(2, '0')}`;
      }
    }
  }

  return null;
}

function parseDateValue(val) {
  if (typeof val === 'string') {
    const d = new Date(val);
    if (isValidDate(d)) return d;
  }
  if (typeof val === 'number' && val > 1 && val < 100000) {
    const parsed = XLSX.SSF.parse_date_code(val);
    if (parsed) {
      const d = new Date(parsed.y, parsed.m - 1, parsed.d);
      if (isValidDate(d)) return d;
    }
  }
  return null;
}
