import { useState, useEffect, useMemo, useCallback } from "react";
import Highcharts from "highcharts";
import HighchartsReact from "highcharts-react-official";
import VideoService from "../base/services/videoService";
import { logSectionError } from "../base/utils/runtimeErrorLogger";
import { ErrorState, LoadingSpinner } from "./SectionStateUI";

/**
 * AnalyticsSection – Unified analytics dashboard (v3 Mockup-matching)
 * - KPI cards & sub-metrics
 * - Time-series chart (sales + viewers) with integrated product exposure color bar
 * - Tooltip shows active products with sales data
 * - Product legend with sales amount, appearance count, exposure time
 * - Product ranking table with sales share, CVR, exposure pattern
 * - Expandable Gantt-chart detail per product
 * - Inline edit / add / delete for product exposures
 *
 * Props:
 *   reports1  – array of phase objects (each with csv_metrics, time_start, time_end)
 *   videoData – the full video detail object
 */

// ─── Color palette for products ───────────────────────────
const PRODUCT_COLORS = [
  "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6",
  "#f43f5e", "#06b6d4", "#f97316", "#6366f1",
  "#ec4899", "#14b8a6", "#eab308", "#a855f7",
  "#ef4444", "#22d3ee", "#fb923c", "#818cf8",
];

const PRODUCT_BG_CLASSES = [
  "bg-blue-100 text-blue-700 border-blue-300",
  "bg-emerald-100 text-emerald-700 border-emerald-300",
  "bg-amber-100 text-amber-700 border-amber-300",
  "bg-purple-100 text-purple-700 border-purple-300",
  "bg-rose-100 text-rose-700 border-rose-300",
  "bg-cyan-100 text-cyan-700 border-cyan-300",
  "bg-orange-100 text-orange-700 border-orange-300",
  "bg-indigo-100 text-indigo-700 border-indigo-300",
  "bg-pink-100 text-pink-700 border-pink-300",
  "bg-teal-100 text-teal-700 border-teal-300",
  "bg-yellow-100 text-yellow-700 border-yellow-300",
  "bg-violet-100 text-violet-700 border-violet-300",
  "bg-red-100 text-red-700 border-red-300",
  "bg-sky-100 text-sky-700 border-sky-300",
  "bg-amber-100 text-amber-700 border-amber-300",
  "bg-indigo-100 text-indigo-700 border-indigo-300",
];

function getColor(idx) { return PRODUCT_COLORS[idx % PRODUCT_COLORS.length]; }
function getBgClass(idx) { return PRODUCT_BG_CLASSES[idx % PRODUCT_BG_CLASSES.length]; }

function formatTime(seconds) {
  if (seconds == null || isNaN(seconds)) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function formatDurationMinutes(seconds) {
  if (seconds == null || isNaN(seconds)) return `0${window.__t('analytics_minutes', '分')}`;
  const mins = Math.round(seconds / 60);
  return `${mins}${window.__t('analytics_minutes', '分')}`;
}

function parseTimeInput(value) {
  if (value.includes(":")) {
    const [m, s] = value.split(":");
    return parseInt(m || 0) * 60 + parseInt(s || 0);
  }
  return parseFloat(value) || 0;
}

// ─── Exposure Row (for edit list) ─────────────────────────
function ExposureRow({ exposure, colorIdx, onUpdate, onDelete, isEditing, setEditing, streamStartTime }) {
  const [editData, setEditData] = useState({
    product_name: exposure.product_name,
    time_start: formatTime(exposure.time_start),
    time_end: formatTime(exposure.time_end),
  });
  const [saving, setSaving] = useState(false);

  const realStart = streamStartTime ? addSecondsToTime(streamStartTime, exposure.time_start) : null;
  const realEnd = streamStartTime ? addSecondsToTime(streamStartTime, exposure.time_end) : null;

  const handleSave = async () => {
    setSaving(true);
    try {
      await onUpdate(exposure.id, {
        product_name: editData.product_name,
        time_start: parseTimeInput(editData.time_start),
        time_end: parseTimeInput(editData.time_end),
      });
      setEditing(null);
    } catch (e) { console.error("Save failed:", e); }
    setSaving(false);
  };

  if (isEditing) {
    return (
      <div className={`flex items-center gap-2 p-2 rounded-lg border ${getBgClass(colorIdx)}`}>
        <input className="flex-1 text-sm px-2 py-1 rounded border border-gray-300 bg-white"
          value={editData.product_name} onChange={(e) => setEditData({ ...editData, product_name: e.target.value })} placeholder={window.__t('analytics_productName', '商品名')} />
        <input className="w-16 text-sm px-2 py-1 rounded border border-gray-300 bg-white text-center"
          value={editData.time_start} onChange={(e) => setEditData({ ...editData, time_start: e.target.value })} placeholder="0:00" />
        <span className="text-gray-400 text-xs">-</span>
        <input className="w-16 text-sm px-2 py-1 rounded border border-gray-300 bg-white text-center"
          value={editData.time_end} onChange={(e) => setEditData({ ...editData, time_end: e.target.value })} placeholder="0:00" />
        <button onClick={handleSave} disabled={saving} className="px-2 py-1 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50">
          {saving ? "..." : window.__t('common_save', '保存')}
        </button>
        <button onClick={() => setEditing(null)} className="px-2 py-1 text-xs bg-gray-300 text-gray-700 rounded hover:bg-gray-400">{window.__t('cancelBtn', '取消')}</button>
      </div>
    );
  }

  return (
    <div className={`flex items-center gap-3 p-2 rounded-lg border group ${getBgClass(colorIdx)}`}>
      <div className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: getColor(colorIdx) }} />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{exposure.product_name}</div>
      </div>
      <div className="text-xs text-gray-500 whitespace-nowrap">
        {realStart && realEnd ? `${realStart} - ${realEnd}` : `${formatTime(exposure.time_start)} - ${formatTime(exposure.time_end)}`}
        <span className="text-gray-400 ml-1">({formatTime(exposure.time_start)} - {formatTime(exposure.time_end)})</span>
      </div>
      <div className={`text-[10px] px-1.5 py-0.5 rounded-full ${
        exposure.confidence >= 0.8 ? "bg-green-100 text-green-700" :
        exposure.confidence >= 0.5 ? "bg-yellow-100 text-yellow-700" : "bg-red-100 text-red-700"
      }`}>{Math.round((exposure.confidence || 0) * 100)}%</div>
      <div className={`text-[10px] px-1.5 py-0.5 rounded-full ${
        exposure.source === "human" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-500"
      }`}>{exposure.source === "human" ? window.__t('analytics_manual', '手動') : "AI"}</div>
      <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button onClick={() => setEditing(exposure.id)} className="p-1 text-gray-400 hover:text-blue-500" title={window.__t('common_edit', '編集')}>
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
        </button>
        <button onClick={() => onDelete(exposure.id)} className="p-1 text-gray-400 hover:text-red-500" title={window.__t('common_delete', '削除')}>
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
        </button>
      </div>
    </div>
  );
}

// ─── Add Exposure Form ────────────────────────────────────
function AddExposureForm({ onAdd, onCancel }) {
  const [data, setData] = useState({ product_name: "", brand_name: "", time_start: "", time_end: "" });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async () => {
    if (!data.product_name || !data.time_start || !data.time_end) return;
    setSaving(true);
    try {
      await onAdd({ product_name: data.product_name, brand_name: data.brand_name, time_start: parseTimeInput(data.time_start), time_end: parseTimeInput(data.time_end), confidence: 1.0 });
      setData({ product_name: "", brand_name: "", time_start: "", time_end: "" });
    } catch (e) { console.error("Add failed:", e); }
    setSaving(false);
  };

  return (
    <div className="flex flex-wrap items-center gap-2 p-3 rounded-lg bg-blue-50 border border-blue-200">
      <input className="flex-1 min-w-[120px] text-sm px-2 py-1.5 rounded border border-gray-300 bg-white"
        value={data.product_name} onChange={(e) => setData({ ...data, product_name: e.target.value })} placeholder={`${window.__t('analytics_productName', '商品名')} *`} />
      <input className="w-16 text-sm px-2 py-1.5 rounded border border-gray-300 bg-white text-center"
        value={data.time_start} onChange={(e) => setData({ ...data, time_start: e.target.value })} placeholder={window.__t('analytics_start', '開始')} />
      <span className="text-gray-400 text-xs">-</span>
      <input className="w-16 text-sm px-2 py-1.5 rounded border border-gray-300 bg-white text-center"
        value={data.time_end} onChange={(e) => setData({ ...data, time_end: e.target.value })} placeholder={window.__t('analytics_end', '終了')} />
      <button onClick={handleSubmit} disabled={saving || !data.product_name || !data.time_start || !data.time_end}
        className="px-3 py-1.5 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50">
        {saving ? `${window.__t('common_save', '保存')}...` : window.__t('analytics_add', '追加')}
      </button>
      <button onClick={onCancel} className="px-3 py-1.5 text-xs bg-gray-200 text-gray-700 rounded hover:bg-gray-300">{window.__t('cancelBtn', 'キャンセル')}</button>
    </div>
  );
}

// ─── Helper: add seconds to HH:MM time string ────────────
function addSecondsToTime(baseTime, seconds) {
  if (!baseTime) return null;
  const match = baseTime.match(/^(\d{1,2}):(\d{2})/);
  if (!match) return null;
  const baseMinutes = parseInt(match[1]) * 60 + parseInt(match[2]);
  const totalMinutes = baseMinutes + Math.floor(seconds / 60);
  const h = Math.floor(totalMinutes / 60) % 24;
  const m = totalMinutes % 60;
  return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}`;
}

// ─── Main Component ───────────────────────────────────────
export default function AnalyticsSection({ reports1, videoData, onPreviewSegment }) {
  const [collapsed, setCollapsed] = useState(false);
  const [excelData, setExcelData] = useState(null);
  const [loadingExcel, setLoadingExcel] = useState(false);
  const [excelError, setExcelError] = useState(null);
  const [productCollapsed, setProductCollapsed] = useState(false);
  const [phaseProductCollapsed, setPhaseProductCollapsed] = useState(true);

  // Product exposure states (merged from ProductTimeline)
  const [exposures, setExposures] = useState([]);
  const [loadingExposures, setLoadingExposures] = useState(false);
  const [exposuresError, setExposuresError] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [ganttExpanded, setGanttExpanded] = useState(false);
  const [exposureListExpanded, setExposureListExpanded] = useState(false);

  // Selected product for filtering
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [rankingCollapsed, setRankingCollapsed] = useState(false);

  // ── Fetch Excel product/trend data ────────────────────────────
  useEffect(() => {
    if (!videoData?.id) return;
    if (!videoData.excel_product_blob_url && !videoData.excel_trend_blob_url) return;
    let cancelled = false;
    setLoadingExcel(true);
    setExcelError(null);
    VideoService.getProductData(videoData.id)
      .then((data) => { if (!cancelled) setExcelData(data); })
      .catch((e) => {
        if (!cancelled) {
          const errType = e?.response?.status === 404 ? "not_found" : e?.response?.status >= 500 ? "server" : "unknown";
          setExcelError({ type: errType, message: e?.message || "Excel data load failed", status: e?.response?.status });
          logSectionError({ sectionName: "配信パフォーマンス/Excel", videoId: videoData.id, endpoint: "getProductData", errorType: errType, errorMessage: e?.message, httpStatus: e?.response?.status });
        }
      })
      .finally(() => { if (!cancelled) setLoadingExcel(false); });
    return () => { cancelled = true; };
  }, [videoData?.id, videoData?.excel_product_blob_url, videoData?.excel_trend_blob_url]);

  // ── Fetch product exposures ───────────────────────────────────
  const fetchExposures = useCallback(async () => {
    if (!videoData?.id) return;
    setLoadingExposures(true);
    setExposuresError(null);
    try {
      const result = await VideoService.getProductExposures(videoData.id);
      setExposures(result?.exposures || []);
    } catch (e) {
      console.error("Failed to fetch exposures:", e);
      const errType = e?.response?.status === 404 ? "not_found" : e?.response?.status >= 500 ? "server" : "unknown";
      setExposuresError({ type: errType, message: e?.message || "Exposures load failed", status: e?.response?.status });
      logSectionError({ sectionName: "配信パフォーマンス/商品露出", videoId: videoData.id, endpoint: "getProductExposures", errorType: errType, errorMessage: e?.message, httpStatus: e?.response?.status });
    }
    setLoadingExposures(false);
  }, [videoData?.id]);

  useEffect(() => { fetchExposures(); }, [fetchExposures]);

  // CRUD handlers for exposures
  const handleUpdateExposure = async (exposureId, data) => {
    await VideoService.updateProductExposure(videoData.id, exposureId, data);
    await fetchExposures();
  };
  const handleDeleteExposure = async (exposureId) => {
    if (!window.confirm(window.__t('analytics_confirmDeleteSegment', 'この商品露出セグメントを削除しますか？'))) return;
    await VideoService.deleteProductExposure(videoData.id, exposureId);
    await fetchExposures();
  };
  const handleAddExposure = async (data) => {
    await VideoService.createProductExposure(videoData.id, data);
    await fetchExposures();
    setShowAddForm(false);
  };

  // ── Build product color map & stats from exposures ────────────
  const exposureStats = useMemo(() => {
    // Build a name-resolution map: Product_N → real product name from Excel
    const excelNames = (excelData?.products || []).map(p => {
      const keys = Object.keys(p);
      const nameKey = keys.find(k => k === '商品名')
        || keys.find(k => /product_name|name/i.test(k) && !/id/i.test(k))
        || keys.find(k => /商品名/.test(k))
        || keys.find(k => /商品/.test(k) && !/ID|id|ＩＤ/.test(k))
        || keys[1] || keys[0];
      return nameKey ? p[nameKey] : null;
    }).filter(Boolean);

    const nameMap = {};
    for (const exp of exposures) {
      const raw = exp.product_name;
      if (nameMap[raw] !== undefined) continue;
      // Check if it matches Product_N pattern
      const m = raw.match(/^Product_(\d+)$/i);
      if (m) {
        const idx = parseInt(m[1], 10);
        if (idx < excelNames.length) {
          nameMap[raw] = excelNames[idx];
        } else {
          nameMap[raw] = raw; // no match, keep original
        }
      } else {
        nameMap[raw] = raw; // already a real name
      }
    }

    const resolveName = (raw) => nameMap[raw] ?? raw;

    const uniqueProducts = [...new Set(exposures.map((e) => resolveName(e.product_name)))];
    const colorMap = {};
    uniqueProducts.forEach((name, idx) => { colorMap[name] = idx; });

    // Per-product stats (using resolved names)
    const stats = {};
    for (const exp of exposures) {
      const name = resolveName(exp.product_name);
      if (!stats[name]) {
        stats[name] = { name, segments: [], totalDuration: 0, count: 0, colorIdx: colorMap[name] };
      }
      const dur = (exp.time_end || 0) - (exp.time_start || 0);
      stats[name].segments.push(exp);
      stats[name].totalDuration += dur;
      stats[name].count += 1;
    }

    // Sort by total duration descending
    const sorted = Object.values(stats).sort((a, b) => b.totalDuration - a.totalDuration);
    return { colorMap, uniqueProducts, sorted, resolveName };
  }, [exposures, excelData]);

  // ── Aggregate metrics from all phases ──────────────────────────
  const agg = useMemo(() => {
    if (!reports1 || reports1.length === 0) return null;

    let totalGmv = 0, totalOrders = 0, totalViewers = 0, totalLikes = 0;
    let totalComments = 0, totalShares = 0, totalFollowers = 0, totalClicks = 0;
    let maxTime = 0, hasAnyData = false;
    const productMap = {};
    const timeSeriesGmv = [], timeSeriesViewers = [];

    for (const item of reports1) {
      const m = item.csv_metrics;
      if (!m) continue;
      if (m.gmv > 0 || m.order_count > 0 || m.viewer_count > 0 || m.like_count > 0) hasAnyData = true;

      totalGmv += m.gmv || 0;
      totalOrders += m.order_count || 0;
      totalViewers = Math.max(totalViewers, m.viewer_count || 0);
      totalLikes += m.like_count || 0;
      totalComments += m.comment_count || 0;
      totalShares += m.share_count || 0;
      totalFollowers += m.new_followers || 0;
      totalClicks += m.product_clicks || 0;

      if (item.time_end != null && Number(item.time_end) > maxTime) maxTime = Number(item.time_end);

      const midTime = item.time_start != null && item.time_end != null
        ? (Number(item.time_start) + Number(item.time_end)) / 2
        : item.time_start != null ? Number(item.time_start) : null;

      if (midTime != null) {
        timeSeriesGmv.push({ x: midTime, y: m.gmv || 0 });
        timeSeriesViewers.push({ x: midTime, y: m.viewer_count || 0 });
      }

      if (m.product_names && Array.isArray(m.product_names)) {
        for (const name of m.product_names) {
          if (!productMap[name]) productMap[name] = { name, gmv: 0, orders: 0, clicks: 0, phases: 0 };
          productMap[name].gmv += m.gmv || 0;
          productMap[name].orders += m.order_count || 0;
          productMap[name].clicks += m.product_clicks || 0;
          productMap[name].phases += 1;
        }
      }
    }

    if (!hasAnyData) return null;

    timeSeriesGmv.sort((a, b) => a.x - b.x);
    timeSeriesViewers.sort((a, b) => a.x - b.x);

    let cumGmv = 0;
    const cumulativeGmv = timeSeriesGmv.map((p) => { cumGmv += p.y; return { x: p.x, y: cumGmv }; });

    const durationMin = Math.round(maxTime / 60);
    const cvr = totalClicks > 0 ? ((totalOrders / totalClicks) * 100).toFixed(1) : "0.0";
    const avgOrderValue = totalOrders > 0 ? Math.round(totalGmv / totalOrders) : 0;
    const gmvPerHour = durationMin > 0 ? Math.round(totalGmv / (durationMin / 60)) : 0;
    const gpm = totalViewers > 0 ? Math.round(totalGmv / totalViewers) : 0;
    const products = Object.values(productMap).sort((a, b) => b.gmv - a.gmv);

    return {
      totalGmv, totalOrders, totalViewers, totalLikes, totalComments,
      totalShares, totalFollowers, totalClicks, durationMin, cvr,
      avgOrderValue, gmvPerHour, gpm, products, cumulativeGmv, timeSeriesViewers,
    };
  }, [reports1]);

  // ── Process Excel trend data for chart with real timestamps ────
  const trendChart = useMemo(() => {
    if (!excelData?.has_trend_data || !excelData.trends || excelData.trends.length === 0) return null;

    const trends = excelData.trends;
    const keys = Object.keys(trends[0] || {});
    const timeKey = keys.find(k => /时间|time|時間/.test(k.toLowerCase())) || keys[0];
    const gmvKey = keys.find(k => /gmv|销售额|売上|revenue|成交金额/.test(k.toLowerCase()));
    const viewerKey = keys.find(k => /观看|viewer|視聴|在线|观众|人数/.test(k.toLowerCase()));
    const orderKey = keys.find(k => /订单|order|注文|成交/.test(k.toLowerCase()));

    if (!timeKey) return null;

    const parsed = [];
    let firstMinutes = null;

    for (const row of trends) {
      const timeVal = row[timeKey];
      if (!timeVal) continue;
      const timeStr = String(timeVal).trim();
      const match = timeStr.match(/^(\d{1,2}):(\d{2})/);
      if (!match) continue;
      const hours = parseInt(match[1], 10);
      const minutes = parseInt(match[2], 10);
      const totalMinutes = hours * 60 + minutes;
      if (firstMinutes === null) firstMinutes = totalMinutes;
      let elapsed = totalMinutes - firstMinutes;
      if (elapsed < 0) elapsed += 24 * 60;

      parsed.push({
        realTime: timeStr,
        elapsedMin: elapsed,
        elapsedSec: elapsed * 60,
        gmv: gmvKey ? (parseFloat(row[gmvKey]) || 0) : null,
        viewers: viewerKey ? (parseInt(row[viewerKey]) || 0) : null,
        orders: orderKey ? (parseInt(row[orderKey]) || 0) : null,
      });
    }

    if (parsed.length < 2) return null;
    const startTime = parsed[0]?.realTime || null;
    // Calculate totals from Excel trend data
    const excelTotalGmv = gmvKey ? parsed.reduce((sum, d) => sum + (d.gmv || 0), 0) : null;
    const excelTotalOrders = orderKey ? parsed.reduce((sum, d) => sum + (d.orders || 0), 0) : null;
    const excelMaxViewers = viewerKey ? Math.max(...parsed.map(d => d.viewers || 0)) : null;

    return { data: parsed, hasGmv: !!gmvKey, hasViewers: !!viewerKey, hasOrders: !!orderKey, startTime, excelTotalGmv, excelTotalOrders, excelMaxViewers };
  }, [excelData]);

  // Stream start time (from trend data or video filename)
  const streamStartTime = useMemo(() => {
    if (trendChart?.startTime) return trendChart.startTime;
    // Try to extract from filename: ryukyogoku-20260129-0802.mp4 → 08:02
    const fn = videoData?.original_filename || "";
    const m = fn.match(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
    if (m) return `${m[4]}:${m[5]}`;
    return null;
  }, [trendChart, videoData]);

  // ── Video duration from various sources ───────────────────────
  const videoDuration = useMemo(() => {
    if (videoData?.duration) return videoData.duration;
    if (agg?.durationMin) return agg.durationMin * 60;
    if (exposures.length > 0) return Math.max(...exposures.map(e => e.time_end || 0), 60);
    return 0;
  }, [videoData, agg, exposures]);

  // ── Format helpers ─────────────────────────────────────────────
  const fmtTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  const fmtElapsed = (seconds) => {
    const m = Math.floor(seconds / 60);
    return `${window.__t('analytics_streamAt', '配信')}${m}${window.__t('analytics_minutesAround', '分頃')}`;
  };

  // ── Process Excel product data ────────────────────────────────
  const excelProducts = useMemo(() => {
    if (!excelData?.has_product_data || !excelData.products || excelData.products.length === 0) return null;

    const products = excelData.products;
    const keys = Object.keys(products[0] || {});
    const nameKey = keys.find(k => k === '商品名')
      || keys.find(k => /product_name|name/i.test(k) && !/id/i.test(k))
      || keys.find(k => /商品名/.test(k))
      || keys.find(k => /商品/.test(k) && !/ID|id|ＩＤ/.test(k))
      || keys[1] || keys[0];
    const priceKey = keys.find(k => /価格|price|单价|金額/.test(k.toLowerCase()));
    const quantityKey = keys.find(k => /数量|quantity|qty|販売数/.test(k.toLowerCase()));
    const revenueKey = keys.find(k => /^GMV$/i.test(k))
      || keys.find(k => /売上|revenue|gmv|销售额|金額/.test(k.toLowerCase()));
    const categoryKey = keys.find(k => /カテゴリ|category|分類/.test(k.toLowerCase()));
    const displayKeys = keys.filter(k => k && !k.startsWith("col_"));
    const parseNum = (v) => { if (typeof v === 'number') return v; if (typeof v === 'string') { const n = parseFloat(v.replace(/,/g, '')); return isNaN(n) ? 0 : n; } return 0; };
    const sortedItems = revenueKey ? [...products].sort((a, b) => parseNum(b[revenueKey]) - parseNum(a[revenueKey])) : products;

    // Calculate total GMV from product data
    const totalProductGmv = revenueKey ? products.reduce((sum, p) => sum + parseNum(p[revenueKey]), 0) : null;
    const orderCountKey = keys.find(k => /注文|成交件数|order|orders|件数/i.test(k));
    const totalProductOrders = orderCountKey ? products.reduce((sum, p) => sum + parseNum(p[orderCountKey]), 0) : null;

    return { items: sortedItems, nameKey, priceKey, quantityKey, revenueKey, categoryKey, displayKeys, top5: sortedItems.slice(0, 5), totalProductGmv, totalProductOrders };
  }, [excelData]);

  // ── Match exposure product names with Excel product sales data ─
  const productRanking = useMemo(() => {
    if (exposureStats.sorted.length === 0) return [];

    // Build a lookup from Excel products by name
    const excelLookup = {};
    if (excelProducts?.items && excelProducts.nameKey && excelProducts.revenueKey) {
      const parseNum = (v) => { if (typeof v === 'number') return v; if (typeof v === 'string') { const n = parseFloat(v.replace(/,/g, '')); return isNaN(n) ? 0 : n; } return 0; };
      for (const p of excelProducts.items) {
        const name = p[excelProducts.nameKey];
        if (name) {
          excelLookup[name] = {
            gmv: parseNum(p[excelProducts.revenueKey]),
            quantity: excelProducts.quantityKey ? parseNum(p[excelProducts.quantityKey]) : 0,
            clicks: 0,
          };
        }
      }
    }

    // Also check phase-based product data
    const phaseLookup = {};
    if (agg?.products) {
      for (const p of agg.products) {
        phaseLookup[p.name] = { gmv: p.gmv, orders: p.orders, clicks: p.clicks };
      }
    }

    // Total GMV for share calculation
    let totalProductGmv = 0;

    const ranked = exposureStats.sorted.map((stat) => {
      // Try to match with Excel data (fuzzy: contains match)
      let salesData = excelLookup[stat.name] || null;
      if (!salesData) {
        // Fuzzy match: check if exposure name is contained in any Excel product name or vice versa
        for (const [excelName, data] of Object.entries(excelLookup)) {
          if (excelName.includes(stat.name) || stat.name.includes(excelName)) {
            salesData = data;
            break;
          }
        }
      }

      // Fallback to phase data
      const phaseData = phaseLookup[stat.name] || null;

      const gmv = salesData?.gmv || phaseData?.gmv || 0;
      const orders = salesData?.quantity || phaseData?.orders || 0;
      const clicks = phaseData?.clicks || salesData?.clicks || 0;
      totalProductGmv += gmv;

      return {
        ...stat,
        gmv,
        orders,
        clicks,
      };
    });

    // Add share percentage and CVR
    return ranked.map(p => ({
      ...p,
      sharePercent: totalProductGmv > 0 ? ((p.gmv / totalProductGmv) * 100) : 0,
      cvr: p.clicks > 0 ? ((p.orders / p.clicks) * 100) : (p.orders > 0 && agg?.totalClicks > 0 ? ((p.orders / agg.totalClicks) * 100) : 0),
    })).sort((a, b) => b.gmv - a.gmv || b.totalDuration - a.totalDuration);
  }, [exposureStats, excelProducts, agg]);

  // ── Build a lookup for quick product data access in tooltips ───
  const productDataLookup = useMemo(() => {
    const lookup = {};
    for (const p of productRanking) {
      lookup[p.name] = p;
    }
    return lookup;
  }, [productRanking]);

  // ── Filtered exposures based on selected product ──────────────
  const filteredExposures = useMemo(() => {
    if (!selectedProduct) return exposures;
    return exposures.filter(e => exposureStats.resolveName(e.product_name) === selectedProduct);
  }, [exposures, selectedProduct, exposureStats]);

  // ── Handle product click in ranking table ──────────────────────
  const handleProductClick = (productName) => {
    if (selectedProduct === productName) {
      setSelectedProduct(null); // Toggle off
    } else {
      setSelectedProduct(productName);
    }
  };

  // ── Handle segment click to preview video ─────────────────────
  const handleSegmentClick = (timeStart, timeEnd) => {
    if (onPreviewSegment) {
      onPreviewSegment(timeStart, timeEnd);
    }
  };

  // ── Don't render if no data ────────────────────────────────────
  if (!agg) return null;

  // ── Highcharts options (phase-based fallback) ─────────────────
  const chartOptions = {
    chart: { height: 180, backgroundColor: "transparent", style: { fontFamily: "inherit" } },
    title: { text: null },
    credits: { enabled: false },
    legend: { align: "right", verticalAlign: "top", floating: true, itemStyle: { fontSize: "10px", color: "#6b7280" } },
    xAxis: {
      type: "linear",
      crosshair: { width: 1, color: "#d1d5db", dashStyle: "Dash" },
      labels: { formatter: function () { return fmtTime(this.value); }, style: { fontSize: "10px", color: "#9ca3af" } },
      lineColor: "#e5e7eb", tickColor: "#e5e7eb",
    },
    yAxis: [
      { title: { text: null }, labels: { formatter: function () { return "¥" + (this.value >= 1000 ? Math.round(this.value / 1000) + "k" : this.value); }, style: { fontSize: "10px", color: "#f97316" } }, gridLineColor: "#f3f4f6" },
      { title: { text: null }, opposite: true, labels: { style: { fontSize: "10px", color: "#3b82f6" } }, gridLineWidth: 0 },
    ],
    tooltip: {
      shared: true,
      formatter: function () {
        const time = fmtTime(this.x);
        const elapsed = fmtElapsed(this.x);
        let s = `<b>${time}</b> (${elapsed})<br/>`;
        for (const p of this.points) {
          const val = p.series.name === window.__t('analytics_cumulativeSales', '売上（累積）') ? "¥" + Math.round(p.y).toLocaleString() : p.y.toLocaleString() + window.__t('analytics_personUnit', '人');
          s += `<span style="color:${p.color}">\u25CF</span> ${p.series.name}: <b>${val}</b><br/>`;
        }
        return s;
      },
    },
    plotOptions: { areaspline: { fillOpacity: 0.15, marker: { enabled: false, radius: 2 }, lineWidth: 2 } },
    series: [
      { name: window.__t('analytics_cumulativeSales', '売上（累積）'), type: "areaspline", color: "#f97316", data: agg.cumulativeGmv.map((p) => [p.x, p.y]), yAxis: 0 },
      { name: window.__t('analytics_viewerCount', '視聴者数'), type: "areaspline", color: "#3b82f6", data: agg.timeSeriesViewers.map((p) => [p.x, p.y]), yAxis: 1 },
    ],
  };

  // ── Trend chart options (from Excel with real timestamps) ─────
  // Build tooltip formatter that includes active product info with sales data
  const trendTooltipFormatter = function () {
    const elapsed = Math.round(this.x / 60);
    const timeStr = streamStartTime
      ? addSecondsToTime(streamStartTime, this.x)
      : trendChart.data.reduce((prev, curr) =>
          Math.abs(curr.elapsedSec - this.x) < Math.abs(prev.elapsedSec - this.x) ? curr : prev
        ).realTime;
    let s = `<div style="padding:4px 2px;">`;
    s += `<b style="font-size:13px">${timeStr}</b> <span style="color:#6b7280">(${window.__t('analytics_streamAt', '配信')}${elapsed}${window.__t('analytics_minutes', '分')})</span><br/>`;
    for (const p of this.points) {
      let val;
      if (p.series.name.includes(window.__t('analytics_cumulativeSales', '売上（累積）').substring(0, 2))) val = "¥" + Math.round(p.y).toLocaleString();
      else if (p.series.name.includes(window.__t('analytics_viewerCount', '視聴者数').substring(0, 2))) val = p.y.toLocaleString() + window.__t('analytics_personUnit', '人');
      else val = p.y.toLocaleString();
      s += `<span style="color:${p.color}">\u25CF</span> ${p.series.name}${window.__t('analyticsSection_dec892', '：')}<b>${val}</b><br/>`;
    }
    // Show active products at this time with sales data
    const currentSec = this.x;
    const activeProducts = exposures.filter(e => e.time_start <= currentSec && e.time_end >= currentSec);
    if (activeProducts.length > 0) {
      const seen = new Set();
      const uniqueActive = [];
      for (const ap of activeProducts) {
        const resolvedName = exposureStats.resolveName(ap.product_name);
        if (!seen.has(resolvedName)) {
          seen.add(resolvedName);
          uniqueActive.push({ ...ap, resolvedName });
        }
      }
      s += `<br/><span style="font-size:10px;color:#6b7280">\uD83D\uDCE6 ${window.__t('analytics_displaying', '表示中')}:</span><br/>`;
      for (const ap of uniqueActive) {
        const ci = exposureStats.colorMap[ap.resolvedName] ?? 0;
        const pData = productDataLookup[ap.resolvedName];
        const gmvStr = pData?.gmv > 0 ? ` <span style="color:#f97316;font-weight:bold">¥${Math.round(pData.gmv).toLocaleString()}</span>` : "";
        const statsStr = pData ? ` <span style="color:#9ca3af">(${pData.count}${window.__t('analytics_timesUnit', '回')}/${formatDurationMinutes(pData.totalDuration)})</span>` : "";
        s += `<span style="color:${getColor(ci)}">\u25CF</span> <span style="font-size:11px">${ap.resolvedName}</span>${gmvStr}${statsStr}<br/>`;
      }
    }
    s += `</div>`;
    return s;
  };

  const trendChartOptions = trendChart ? {
    chart: { height: 280, backgroundColor: "transparent", style: { fontFamily: "inherit" } },
    title: { text: null },
    credits: { enabled: false },
    legend: { align: "right", verticalAlign: "top", floating: true, itemStyle: { fontSize: "10px", color: "#6b7280" } },
    xAxis: {
      type: "linear",
      crosshair: { width: 1, color: "#d1d5db", dashStyle: "Dash" },
      tickInterval: 20 * 60,
      labels: {
        formatter: function () {
          const min = Math.round(this.value / 60);
          const timeStr = streamStartTime
            ? addSecondsToTime(streamStartTime, this.value)
            : trendChart.data.reduce((prev, curr) =>
                Math.abs(curr.elapsedSec - this.value) < Math.abs(prev.elapsedSec - this.value) ? curr : prev
              ).realTime;
          return `${timeStr} (${min}${window.__t('analytics_minutes', '分')})`;
        },
        style: { fontSize: "9px", color: "#9ca3af" },
      },
      lineColor: "#e5e7eb", tickColor: "#e5e7eb",
    },
    yAxis: [
      ...(trendChart.hasGmv ? [{ title: { text: null }, labels: { formatter: function () { return "¥" + (this.value >= 1000 ? Math.round(this.value / 1000) + "k" : this.value); }, style: { fontSize: "10px", color: "#f97316" } }, gridLineColor: "#f3f4f6" }] : []),
      ...(trendChart.hasViewers ? [{ title: { text: null }, opposite: true, labels: { style: { fontSize: "10px", color: "#3b82f6" } }, gridLineWidth: 0 }] : []),
    ],
    tooltip: {
      shared: true,
      useHTML: true,
      snap: 1,
      formatter: trendTooltipFormatter,
    },
    plotOptions: { areaspline: { fillOpacity: 0.15, marker: { enabled: false, radius: 2 }, lineWidth: 2, stickyTracking: true, trackByArea: true } },
    series: [
      ...(trendChart.hasGmv ? [{
        name: window.__t('analytics_cumulativeSales', '売上（累積）'), type: "areaspline", color: "#f97316",
        data: (() => { let cum = 0; return trendChart.data.filter(d => d.gmv != null).map(d => { cum += d.gmv; return [d.elapsedSec, cum]; }); })(),
        yAxis: 0,
      }] : []),
      ...(trendChart.hasViewers ? [{
        name: window.__t('analytics_viewerCount', '視聴者数'), type: "areaspline", color: "#3b82f6",
        data: trendChart.data.filter(d => d.viewers != null).map(d => [d.elapsedSec, d.viewers]),
        yAxis: trendChart.hasGmv ? 1 : 0,
      }] : []),
    ],
  } : null;

  // ── Render ─────────────────────────────────────────────────────
  return (
    <div className="w-full mt-6 mx-auto mb-4">
      <div className="rounded-2xl bg-gray-50 border border-gray-200">
        {/* Header */}
        <div onClick={() => setCollapsed((s) => !s)}
          className="flex items-center justify-between p-5 cursor-pointer hover:bg-gray-100 transition-all duration-200">
          <div className="flex items-center gap-4">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              className="w-5 h-5 text-gray-700">
              <path d="M3 3v18h18" /><path d="m19 9-5 5-4-4-3 3" />
            </svg>
            <div>
              <div className="text-gray-900 text-xl font-semibold">{window.__t('analytics_streamPerformance', '配信パフォーマンス')}</div>
              <div className="text-gray-500 text-sm mt-1">
                {window.__t('analytics_streamPerformanceDesc', '売上・視聴者推移 + 商品露出タイムライン + 商品別売上')}
              </div>
            </div>
          </div>
          <button type="button" className="text-gray-400 p-2 rounded focus:outline-none transition-colors">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
              className={`w-6 h-6 transform transition-transform duration-200 ${!collapsed ? "rotate-180" : ""}`}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>

        {!collapsed && (
          <div className="px-5 pb-5 space-y-4">

            {/* ── Trend Chart (from Excel with real timestamps) ── */}
            {trendChartOptions && (
              <div className="rounded-xl bg-white border border-gray-200 p-4 shadow-sm">
                <HighchartsReact highcharts={Highcharts} options={trendChartOptions} />

                {/* ── Integrated Product Color Bar ── */}
                {exposures.length > 0 && videoDuration > 0 && (
                               <div className="mt-3 px-1">
                    <p className="text-xs text-gray-500 mb-2 flex items-center gap-1">
                      <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-gray-400">
                        <rect x="3" y="4" width="18" height="18" rx="2" ry="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" />
                      </svg>
                      {window.__t('analytics_productExposureTimeline', '商品露出タイムライン')}（{exposureStats.sorted.length}{window.__t('analytics_products', '商品')} / {exposures.length}{window.__t('analytics_segments', 'セグメント')}）
                    </p>
                    <div className="relative w-full h-8 bg-gray-50 rounded-lg overflow-hidden border border-gray-100">
                      {exposures.map((exp, idx) => {
                        const left = (exp.time_start / videoDuration) * 100;
                        const width = ((exp.time_end - exp.time_start) / videoDuration) * 100;
                        const resolved = exposureStats.resolveName(exp.product_name);
                        const ci = exposureStats.colorMap[resolved] ?? 0;
                        const isFiltered = selectedProduct && resolved !== selectedProduct;
                        if (isFiltered) return null;
                        return (
                          <div key={exp.id || idx}
                            className="absolute top-0 h-full rounded-sm cursor-pointer transition-all duration-200 hover:opacity-90"
                            onClick={() => handleSegmentClick(exp.time_start, exp.time_end)}
                            style={{
                              left: `${Math.max(0, left)}%`,
                              width: `${Math.max(0.3, width)}%`,
                              backgroundColor: getColor(ci),
                              opacity: Math.max(0.5, exp.confidence || 0.8),
                            }}
                            title={`${resolved} (${formatTime(exp.time_start)} - ${formatTime(exp.time_end)}) ▶ ${window.__t('analytics_clickToPlay', 'クリックで再生')}`}
                          />
                        );
                      })}
                    </div>

                    {/* ── Product Legend with sales data ── */}
                    <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-3">
                      {productRanking.map((p) => {
                        const isLegendSelected = selectedProduct === p.name;
                        const isLegendDimmed = selectedProduct && selectedProduct !== p.name;
                        return (
                          <div key={p.name}
                            onClick={() => handleProductClick(p.name)}
                            className={`flex items-center gap-1.5 text-xs cursor-pointer rounded-md px-1.5 py-0.5 transition-all duration-200 ${isLegendSelected ? 'bg-blue-50 ring-1 ring-blue-200' : 'hover:bg-gray-100'} ${isLegendDimmed ? 'hidden' : ''}`}>
                            <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: getColor(p.colorIdx) }} />
                            <span className="text-gray-700 font-medium">{p.name}</span>
                            {p.gmv > 0 && (
                              <span className="text-orange-500 font-bold">¥{Math.round(p.gmv).toLocaleString()}</span>
                            )}
                            <span className="text-gray-400">{p.count}{window.__t('analytics_timesUnit', '回')} {formatDurationMinutes(p.totalDuration)}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── Phase-based Chart (fallback if no trend data) ── */}
            {!trendChartOptions && agg.cumulativeGmv.length > 1 && (
              <div className="rounded-xl bg-white border border-gray-200 p-4 shadow-sm">
                <HighchartsReact highcharts={Highcharts} options={chartOptions} />
                {/* Color bar for fallback chart too */}
                {exposures.length > 0 && videoDuration > 0 && (
                  <div className="mt-2 px-1">
                    <div className="relative w-full h-7 bg-gray-100 rounded-lg overflow-hidden">
                      {exposures.map((exp, idx) => {
                        const left = (exp.time_start / videoDuration) * 100;
                        const width = ((exp.time_end - exp.time_start) / videoDuration) * 100;
                        const resolved = exposureStats.resolveName(exp.product_name);
                        const ci = exposureStats.colorMap[resolved] ?? 0;
                        return (
                          <div key={exp.id || idx}
                            className="absolute top-0 h-full rounded-sm"
                            style={{ left: `${Math.max(0, left)}%`, width: `${Math.max(0.3, width)}%`, backgroundColor: getColor(ci), opacity: 0.7 }}
                          />
                        );
                      })}
                    </div>
                    {/* Product Legend */}
                    <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-3">
                      {productRanking.map((p) => (
                        <div key={p.name} className="flex items-center gap-1.5 text-xs">
                          <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: getColor(p.colorIdx) }} />
                          <span className="text-gray-700 font-medium">{p.name}</span>
                          {p.gmv > 0 && <span className="text-orange-500 font-bold">¥{Math.round(p.gmv).toLocaleString()}</span>}
                          <span className="text-gray-400">{p.count}{window.__t('analytics_timesUnit', '回')} {formatDurationMinutes(p.totalDuration)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── Product Ranking Table (Mockup-matching design) ── */}
            {productRanking.length > 0 && (
              <div className="rounded-xl bg-white border border-gray-200 shadow-sm">
                <div className="p-4">
                  <div className="flex items-center justify-between cursor-pointer" onClick={() => setRankingCollapsed(!rankingCollapsed)}>
                    <div className="flex items-center gap-2">
                      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-amber-500">
                        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                      </svg>
                      <span className="text-sm font-semibold text-gray-700">{window.__t('analytics_productSalesRanking', '商品別売上ランキング')}</span>
                      <span className="text-xs text-gray-400">({productRanking.length}{window.__t('analytics_products', '商品')})</span>
                    </div>
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={`text-gray-400 transition-transform duration-200 ${rankingCollapsed ? '' : 'rotate-180'}`}>
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </div>

                  {!rankingCollapsed && <div className="overflow-x-auto mt-4">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-gray-200">
                          <th className="text-left text-xs text-gray-400 font-medium py-3 pr-2 w-10">#</th>
                          <th className="text-left text-xs text-gray-400 font-medium py-3 pr-4">{window.__t('analytics_productName', '商品名')}</th>
                          <th className="text-right text-xs text-gray-400 font-medium py-3 px-2">{window.__t('analytics_sales', '売上')}</th>
                          <th className="text-right text-xs text-gray-400 font-medium py-3 px-2">{window.__t('analytics_orders', '注文')}</th>
                          <th className="text-center text-xs text-gray-400 font-medium py-3 px-2">{window.__t('analytics_salesShare', '売上シェア')}</th>
                          <th className="text-center text-xs text-gray-400 font-medium py-3 px-2">CVR</th>
                          <th className="text-center text-xs text-gray-400 font-medium py-3 px-2">{window.__t('analytics_appearances', '登場')}</th>
                          <th className="text-left text-xs text-gray-400 font-medium py-3 px-2 hidden md:table-cell" style={{ minWidth: 80 }}>{window.__t('analytics_exposurePattern', '露出パターン')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {productRanking.map((p, i) => {
                          const rankBadge = i < 3 ? "bg-gradient-to-br from-amber-400 to-orange-400 text-white shadow-sm" : "bg-gray-100 text-gray-500";
                          const isSelected = selectedProduct === p.name;

                          return (
                            <tr key={p.name}
                              onClick={() => handleProductClick(p.name)}
                              className={`border-b border-gray-100 last:border-0 cursor-pointer transition-all duration-200 ${isSelected ? 'bg-blue-50 ring-1 ring-blue-200' : 'hover:bg-gray-50/50'}`}>
                              <td className="py-5 pr-2">
                                <span className={`inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold ${rankBadge}`}>{i + 1}</span>
                              </td>
                              <td className="py-5 pr-4">
                                <div className="flex items-center gap-2">
                                  <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: getColor(p.colorIdx) }} />
                                  <span className="text-sm font-medium text-gray-800 truncate max-w-[300px]" title={p.name}>{p.name}</span>
                                </div>
                              </td>
                              <td className="text-right py-5 px-2">
                                <span className="text-sm font-bold text-gray-800">
                                  {p.gmv > 0 ? `¥${Math.round(p.gmv).toLocaleString()}` : "-"}
                                </span>
                              </td>
                              <td className="text-right py-5 px-2 text-gray-600">{p.orders > 0 ? `${p.orders}${window.__t('analytics_itemUnit', '件')}` : "-"}</td>
                              <td className="text-center py-5 px-2">
                                {p.sharePercent > 0 ? (
                                  <div className="flex items-center justify-center gap-2">
                                    <div className="w-20 h-2.5 bg-gray-100 rounded-full overflow-hidden">
                                      <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(p.sharePercent, 100)}%`, backgroundColor: getColor(p.colorIdx) }} />
                                    </div>
                                    <span className="text-xs text-gray-600 font-medium w-10 text-right">{p.sharePercent.toFixed(1)}%</span>
                                  </div>
                                ) : <span className="text-xs text-gray-400">-</span>}
                              </td>
                              <td className="text-center py-5 px-2">
                                <span className={`text-xs font-semibold ${p.cvr >= 1.0 ? "text-green-600" : "text-gray-500"}`}>
                                  {p.cvr > 0 ? `${p.cvr.toFixed(1)}%` : "-"}
                                </span>
                              </td>
                              <td className="text-center py-5 px-2">
                                <div>
                                  <div className="text-sm font-bold text-gray-700">{p.count}{window.__t('analytics_timesUnit', '回')}</div>
                                  <div className="text-[10px] text-gray-400">{window.__t('analytics_total', '合計')} {formatDurationMinutes(p.totalDuration)}</div>
                                </div>
                              </td>
                              <td className="py-5 px-2 hidden md:table-cell">
                                {/* Mini exposure pattern bar */}
                                {videoDuration > 0 && (
                                  <div className="relative h-3 bg-gray-100 rounded-full overflow-hidden" style={{ minWidth: 80 }}>
                                    {p.segments.map((seg, si) => {
                                      const left = (seg.time_start / videoDuration) * 100;
                                      const width = ((seg.time_end - seg.time_start) / videoDuration) * 100;
                                      return (
                                        <div key={si} className="absolute top-0 h-full rounded-sm cursor-pointer hover:ring-1 hover:ring-blue-300"
                                          onClick={(e) => { e.stopPropagation(); handleSegmentClick(seg.time_start, seg.time_end); }}
                                          style={{ left: `${left}%`, width: `${Math.max(1.5, width)}%`, backgroundColor: getColor(p.colorIdx) }}
                                          title={`${formatTime(seg.time_start)} - ${formatTime(seg.time_end)} ▶ ${window.__t('analytics_clickToPlay', 'クリックで再生')}`}
                                        />
                                      );
                                    })}
                                  </div>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>}
                </div>
              </div>
            )}

            {/* ── Filtered Segments Panel (when product is selected) ── */}
            {selectedProduct && filteredExposures.length > 0 && (
              <div className="rounded-xl bg-white border-2 border-blue-200 shadow-sm overflow-hidden">
                <div className="bg-blue-50 px-4 py-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-3 h-3 rounded-full" style={{ backgroundColor: getColor(exposureStats.colorMap[selectedProduct] ?? 0) }} />
                    <span className="text-sm font-semibold text-blue-800">{selectedProduct}</span>
                    <span className="text-xs text-blue-500">({filteredExposures.length}{window.__t('analytics_segments', 'セグメント')})</span>
                  </div>
                  <button onClick={() => setSelectedProduct(null)}
                    className="text-xs text-blue-500 hover:text-blue-700 bg-white px-2 py-1 rounded-md border border-blue-200 hover:bg-blue-50 transition-colors">
                    {window.__t('analytics_clearFilter', '✕ フィルタ解除')}
                  </button>
                </div>
                <div className="p-4">
                  <div className="text-xs text-gray-500 mb-2">{window.__t('analytics_tapToPlay', 'タップして動画を再生 ▶')}</div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2 max-h-64 overflow-y-auto">
                    {filteredExposures.map((seg, idx) => {
                      const duration = (seg.time_end || 0) - (seg.time_start || 0);
                      const realStart = streamStartTime ? addSecondsToTime(streamStartTime, seg.time_start) : null;
                      const realEnd = streamStartTime ? addSecondsToTime(streamStartTime, seg.time_end) : null;
                      return (
                        <div key={seg.id || idx}
                          onClick={() => handleSegmentClick(seg.time_start, seg.time_end)}
                          className="flex items-center gap-3 p-3 rounded-lg border border-gray-200 hover:border-blue-300 hover:bg-blue-50 cursor-pointer transition-all duration-150 group">
                          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-blue-100 text-blue-600 group-hover:bg-blue-200 transition-colors">
                            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                              <polygon points="5 3 19 12 5 21 5 3" />
                            </svg>
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium text-gray-700">
                              {realStart || formatTime(seg.time_start)} - {realEnd || formatTime(seg.time_end)}
                            </div>
                            <div className="text-xs text-gray-400">
                              {formatDurationMinutes(duration)} ({formatTime(seg.time_start)} - {formatTime(seg.time_end)})
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}

            {/* ── KPI Cards ── */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="rounded-xl bg-white border border-gray-200 p-4 shadow-sm">
                <div className="flex items-center gap-2 text-gray-500 text-xs font-medium mb-1">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-orange-500">
                    <line x1="12" y1="1" x2="12" y2="23" /><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
                  </svg>
                  {window.__t('analytics_salesGMV', '売上 (GMV)')}
                </div>
                <div className="text-2xl font-bold text-gray-900">¥{Math.round(trendChart?.excelTotalGmv ?? excelProducts?.totalProductGmv ?? agg.totalGmv).toLocaleString()}</div>
                <div className="text-xs text-gray-400 mt-1">{(trendChart?.excelTotalOrders ?? excelProducts?.totalProductOrders ?? agg.totalOrders)}{window.__t('analytics_ordersCount', '件の注文')}</div>
              </div>
              <div className="rounded-xl bg-white border border-gray-200 p-4 shadow-sm">
                <div className="flex items-center gap-2 text-gray-500 text-xs font-medium mb-1">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-blue-500">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" />
                  </svg>
                  {window.__t('analytics_viewerCount', '視聴者数')}
                </div>
                <div className="text-2xl font-bold text-gray-900">{(trendChart?.excelMaxViewers ?? agg.totalViewers).toLocaleString()}</div>
                <div className="text-xs text-gray-400 mt-1">{window.__t('analytics_peakViewers', 'ピーク視聴者')}</div>
              </div>
              <div className="rounded-xl bg-white border border-gray-200 p-4 shadow-sm">
                <div className="flex items-center gap-2 text-gray-500 text-xs font-medium mb-1">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none" className="text-pink-500">
                    <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                  </svg>
                  {window.__t('analytics_likes', 'いいね')}
                </div>
                <div className="text-2xl font-bold text-gray-900">{agg.totalLikes.toLocaleString()}</div>
                <div className="text-xs text-gray-400 mt-1">{window.__t('analytics_comments', 'コメント')} {agg.totalComments}</div>
              </div>
              <div className="rounded-xl bg-white border border-gray-200 p-4 shadow-sm">
                <div className="flex items-center gap-2 text-gray-500 text-xs font-medium mb-1">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-green-500">
                    <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" /><polyline points="10 17 15 12 10 7" /><line x1="15" y1="12" x2="3" y2="12" />
                  </svg>
                  {window.__t('analytics_productClicks', '商品クリック')}
                </div>
                <div className="text-2xl font-bold text-gray-900">{agg.totalClicks.toLocaleString()}</div>
                <div className="text-xs text-gray-400 mt-1">CVR {agg.cvr}%</div>
              </div>
            </div>

            {/* ── Sub Metrics ── */}
            <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
              {[
                { label: "GPM", value: `¥${agg.gpm}` },
                { label: window.__t('analytics_newFollowers', '新規フォロワー'), value: `+${agg.totalFollowers}` },
                { label: window.__t('analytics_shares', 'シェア'), value: agg.totalShares.toString() },
                { label: window.__t('analytics_avgOrderValue', '客単価'), value: `¥${agg.avgOrderValue.toLocaleString()}` },
                { label: window.__t('analytics_streamDuration', '配信時間'), value: `${agg.durationMin}${window.__t('analytics_minutes', '分')}` },
                { label: window.__t('analytics_salesPerHour', '売上/時間'), value: `¥${agg.gmvPerHour.toLocaleString()}/h` },
              ].map((item, i) => (
                <div key={i} className="rounded-lg bg-white border border-gray-100 px-3 py-2 text-center">
                  <div className="text-[10px] text-gray-400 font-medium">{item.label}</div>
                  <div className="text-sm font-semibold text-gray-700 mt-0.5">{item.value}</div>
                </div>
              ))}
            </div>

            {/* ── Expandable Gantt Chart Detail ── */}
            {exposures.length > 0 && videoDuration > 0 && (
              <div className="rounded-xl bg-white border border-gray-200 shadow-sm">
                <div onClick={() => setGanttExpanded(s => !s)}
                  className="flex items-center justify-between p-4 cursor-pointer hover:bg-gray-50 transition-all duration-200">
                  <div className="flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-blue-500">
                      <rect x="3" y="3" width="18" height="18" rx="2" ry="2" /><line x1="3" y1="9" x2="21" y2="9" /><line x1="3" y1="15" x2="21" y2="15" /><line x1="9" y1="3" x2="9" y2="21" />
                    </svg>
                    <span className="text-sm font-semibold text-gray-700">{window.__t('analytics_productTimelineDetail', '商品別タイムライン詳細')}</span>
                  </div>
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
                    className={`w-5 h-5 text-gray-400 transform transition-transform duration-200 ${ganttExpanded ? "rotate-180" : ""}`}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </div>

                {ganttExpanded && (
                  <div className="px-4 pb-4">
                    <div className="space-y-1.5">
                      {exposureStats.sorted.map((product) => {
                        const matchedRank = productRanking.find(r => r.name === product.name);
                        return (
                          <div key={product.name} className="flex items-center gap-2">
                            {/* Product name */}
                            <div className="w-32 md:w-48 flex-shrink-0 truncate text-xs font-medium text-gray-700 flex items-center gap-1.5" title={product.name}>
                              <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: getColor(product.colorIdx) }} />
                              <span className="truncate">{product.name}</span>
                            </div>
                            {/* Gantt bar */}
                            <div className="flex-1 relative h-5 bg-gray-50 rounded border border-gray-100">
                              {product.segments.map((seg, si) => {
                                const left = (seg.time_start / videoDuration) * 100;
                                const width = ((seg.time_end - seg.time_start) / videoDuration) * 100;
                                return (
                                  <div key={si} className="absolute top-0.5 h-4 rounded-sm cursor-pointer hover:opacity-80 hover:ring-2 hover:ring-blue-300"
                                    onClick={() => handleSegmentClick(seg.time_start, seg.time_end)}
                                    style={{ left: `${left}%`, width: `${Math.max(0.5, width)}%`, backgroundColor: getColor(product.colorIdx) }}
                                    title={`${formatTime(seg.time_start)} - ${formatTime(seg.time_end)}${streamStartTime ? ` (${addSecondsToTime(streamStartTime, seg.time_start)} - ${addSecondsToTime(streamStartTime, seg.time_end)})` : ""} ▶ ${window.__t('analytics_clickToPlay', 'クリックで再生')}`}
                                  />
                                );
                              })}
                            </div>
                            {/* Stats */}
                            <div className="w-20 flex-shrink-0 text-right text-[10px] text-gray-500">
                              {matchedRank?.gmv > 0 ? `¥${Math.round(matchedRank.gmv).toLocaleString()}` : ""}
                            </div>
                            <div className="w-16 flex-shrink-0 text-right text-[10px] text-gray-400">
                              {product.count}{window.__t('analytics_timesUnit', '回')} / {formatTime(product.totalDuration)}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    {/* X-axis labels */}
                    <div className="flex justify-between mt-2 px-32 md:px-48 text-[9px] text-gray-400">
                      {[0, 0.25, 0.5, 0.75, 1].map(pct => (
                        <span key={pct}>
                          {streamStartTime ? addSecondsToTime(streamStartTime, pct * videoDuration) : formatTime(pct * videoDuration)}
                          <br />({Math.round(pct * videoDuration / 60)}{window.__t('analytics_minutes', '分')})
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── Exposure List (edit/add/delete) ── */}
            {exposures.length > 0 && (
              <div className="rounded-xl bg-white border border-gray-200 shadow-sm">
                <div onClick={() => setExposureListExpanded(s => !s)}
                  className="flex items-center justify-between p-4 cursor-pointer hover:bg-gray-50 transition-all duration-200">
                  <div className="flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-gray-500">
                      <path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z" /><line x1="3" y1="6" x2="21" y2="6" /><path d="M16 10a4 4 0 0 1-8 0" />
                    </svg>
                    <span className="text-sm font-semibold text-gray-700">{window.__t('analytics_productSegmentList', '商品セグメント一覧')}</span>
                    <span className="text-xs text-gray-400">（{exposures.length}{window.__t('analytics_itemUnit', '件')} / {window.__t('analytics_editAddDelete', '編集・追加・削除')}）</span>
                  </div>
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
                    className={`w-5 h-5 text-gray-400 transform transition-transform duration-200 ${exposureListExpanded ? "rotate-180" : ""}`}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </div>

                {exposureListExpanded && (
                  <div className="px-4 pb-4">
                    <div className="flex flex-col gap-2 max-h-96 overflow-y-auto">
                      {exposures.map((exp, idx) => {
                        const resolved = exposureStats.resolveName(exp.product_name);
                        return (
                          <ExposureRow key={exp.id || idx}
                            exposure={{ ...exp, product_name: resolved }}
                            colorIdx={exposureStats.colorMap[resolved] ?? 0}
                            onUpdate={handleUpdateExposure} onDelete={handleDeleteExposure}
                            isEditing={editingId === exp.id} setEditing={setEditingId}
                            streamStartTime={streamStartTime}
                          />
                        );
                      })}
                    </div>
                    <div className="mt-3">
                      {showAddForm ? (
                        <AddExposureForm onAdd={handleAddExposure} onCancel={() => setShowAddForm(false)} />
                      ) : (
                        <button onClick={() => setShowAddForm(true)}
                          className="w-full py-2 text-sm text-gray-500 border border-dashed border-gray-300 rounded-lg hover:bg-gray-100 hover:text-gray-700 transition-colors">
                          {window.__t('analytics_addSegmentManually', '+ 商品セグメントを手動追加')}
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── Loading Excel data indicator ── */}
            {loadingExcel && (
              <div className="text-center text-xs text-gray-400 py-2">{window.__t('analytics_loadingProductData', '商品データを読み込み中...')}</div>
            )}

            {/* ── Excel Product Data Table ── */}
            {excelProducts && (
              <div className="rounded-xl bg-white border border-gray-200 shadow-sm">
                <div onClick={() => setProductCollapsed((s) => !s)}
                  className="flex items-center justify-between p-4 cursor-pointer hover:bg-gray-50 transition-all duration-200">
                  <div className="flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-emerald-500">
                      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
                      <polyline points="3.27 6.96 12 12.01 20.73 6.96" /><line x1="12" y1="22.08" x2="12" y2="12" />
                    </svg>
                    <span className="text-sm font-semibold text-gray-700">{window.__t('analytics_productData', '商品データ')}</span>
                    <span className="text-xs text-gray-400">（{excelProducts.items.length}{window.__t('analytics_products', '商品')}）</span>
                  </div>
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
                    className={`w-5 h-5 text-gray-400 transform transition-transform duration-200 ${!productCollapsed ? "rotate-180" : ""}`}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </div>
                {!productCollapsed && (
                  <div className="px-4 pb-4">
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-gray-100">
                            {excelProducts.displayKeys.map((key, i) => (
                              <th key={i} className={`text-xs text-gray-400 font-medium py-2 px-2 ${i === 0 ? 'text-left' : 'text-right'}`}>{key}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {excelProducts.items.slice(0, 30).map((product, i) => (
                            <tr key={i} className="border-b border-gray-50 last:border-0 hover:bg-gray-50">
                              {excelProducts.displayKeys.map((key, j) => {
                                const val = product[key];
                                const isName = key === excelProducts.nameKey;
                                const isNumeric = typeof val === 'number';
                                return (
                                  <td key={j} className={`py-2 px-2 ${isName ? 'text-left text-gray-700 font-medium' : 'text-right text-gray-600'}`}>
                                    {isName ? (
                                      <span className="inline-flex items-center gap-1.5">
                                        <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" />
                                        {val || '-'}
                                      </span>
                                    ) : isNumeric ? (
                                      Number.isInteger(val) ? val.toLocaleString() : val.toLocaleString(undefined, { maximumFractionDigits: 2 })
                                    ) : (val || '-')}
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {excelProducts.items.length > 30 && (
                        <div className="text-center text-xs text-gray-400 py-2">{window.__t('analytics_otherProducts', '他')} {excelProducts.items.length - 30} {window.__t('analytics_products', '商品')}</div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── Phase-based Product Breakdown (from csv_metrics) ── */}
            {agg.products.length > 0 && (
              <div className="rounded-xl bg-white border border-gray-200 shadow-sm">
                <div onClick={() => setPhaseProductCollapsed((s) => !s)}
                  className="flex items-center justify-between p-4 cursor-pointer hover:bg-gray-50 transition-all duration-200">
                  <div className="flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-indigo-500">
                      <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                      <line x1="7" y1="7" x2="7.01" y2="7" />
                    </svg>
                    <span className="text-sm font-semibold text-gray-700">{window.__t('analytics_productSalesPhase', '商品別売上（フェーズ分析）')}</span>
                  </div>
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
                    className={`w-5 h-5 text-gray-400 transform transition-transform duration-200 ${!phaseProductCollapsed ? "rotate-180" : ""}`}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </div>
                {!phaseProductCollapsed && (
                  <div className="px-4 pb-4">
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-gray-100">
                            <th className="text-left text-xs text-gray-400 font-medium py-2 pr-4">{window.__t('analytics_productName', '商品名')}</th>
                            <th className="text-right text-xs text-gray-400 font-medium py-2 px-2">{window.__t('analytics_sales', '売上')}</th>
                            <th className="text-right text-xs text-gray-400 font-medium py-2 px-2">{window.__t('analytics_orders', '注文')}</th>
                            <th className="text-right text-xs text-gray-400 font-medium py-2 px-2">{window.__t('analytics_clicks', 'クリック')}</th>
                            <th className="text-right text-xs text-gray-400 font-medium py-2 pl-2">{window.__t('analytics_appearanceCount', '登場回数')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {agg.products.map((p, i) => (
                            <tr key={i} className="border-b border-gray-50 last:border-0">
                              <td className="py-2 pr-4">
                                <span className="inline-flex items-center gap-1.5 text-gray-700">
                                  <span className="w-2 h-2 rounded-full bg-indigo-400 flex-shrink-0" />
                                  {p.name}
                                </span>
                              </td>
                              <td className="text-right py-2 px-2 text-gray-700 font-medium">¥{Math.round(p.gmv).toLocaleString()}</td>
                              <td className="text-right py-2 px-2 text-gray-600">{p.orders}{window.__t('analytics_itemUnit', '件')}</td>
                              <td className="text-right py-2 px-2 text-gray-600">{p.clicks}</td>
                              <td className="text-right py-2 pl-2 text-gray-500">{p.phases}{window.__t('analytics_timesUnit', '回')}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
