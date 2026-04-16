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
  if (seconds == null || isNaN(seconds)) return window.__t('analyticsSection_e8b5');
  const mins = Math.round(seconds / 60);
  return `${mins}分`;
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
          value={editData.product_name} onChange={(e) =>{window.__t('analyticsSection_29cc')}<input className="w-16 text-sm px-2 py-1 rounded border border-gray-300 bg-white text-center"
          value={editData.time_start} onChange={(e) => setEditData({ ...editData, time_start: e.target.value })} placeholder="0:00" />
        <span className="text-gray-400 text-xs">-</span>
        <input className="w-16 text-sm px-2 py-1 rounded border border-gray-300 bg-white text-center"
          value={editData.time_end} onChange={(e) => setEditData({ ...editData, time_end: e.target.value })} placeholder="0:00" />
        <button onClick={handleSave} disabled={saving} className="px-2 py-1 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50">{window.__t('analyticsSection_f220')}</button>
        <button onClick={() =>{window.__t('analyticsSection_9dbc')}</button>
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
      }`}>{window.__t('analyticsSection_e006')}</div>
      <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button onClick={() =>{window.__t('analyticsSection_3e9c')}<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
        </button>
        <button onClick={() =>{window.__t('analyticsSection_7bb8')}<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
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
        value={data.product_name} onChange={(e) =>{window.__t('analyticsSection_351a')}<input className="w-16 text-sm px-2 py-1.5 rounded border border-gray-300 bg-white text-center"
        value={data.time_start} onChange={(e) =>{window.__t('analyticsSection_fde6')}<span className="text-gray-400 text-xs">-</span>
      <input className="w-16 text-sm px-2 py-1.5 rounded border border-gray-300 bg-white text-center"
        value={data.time_end} onChange={(e) =>{window.__t('analyticsSection_d2de')}<button onClick={handleSubmit} disabled={saving || !data.product_name || !data.time_start || !data.time_end}
        className="px-3 py-1.5 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50">{window.__t('analyticsSection_bd82')}</button>
      <button onClick={onCancel} className="px-3 py-1.5 text-xs bg-gray-200 text-gray-700 rounded hover:bg-gray-300">{window.__t('cancelBtn')}</button>
    </div>{window.__t('analyticsSection_6257')}< excelNames.length) {
          nameMap[raw] = excelNames[idx];
        } else {
          nameMap[raw] = raw; // no match, keep original
        }
      } else {
        nameMap[raw] = raw; // already a real name
      }
    }

    const resolveName = (raw) =>{window.__t('analyticsSection_d0ba')}< 0) elapsed += 24 * 60;

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
    const excelTotalGmv = gmvKey ? parsed.reduce((sum, d) =>{window.__t('analyticsSection_b6eb')}<b>${time}</b> (${elapsed})<br/>{window.__t('analyticsSection_4b14')}<span style="color:${p.color}">\u25CF</span> ${p.series.name}: <b>${val}</b><br/>{window.__t('analyticsSection_57b3')}< Math.abs(prev.elapsedSec - this.x) ? curr : prev
        ).realTime;
    let s = `<div style="padding:4px 2px;">`;
    s += `<b style="font-size:13px">${timeStr}</b> <span style="color:#6b7280">{window.__t('analyticsSection_d9ec')}</span><br/>{window.__t('analyticsSection_0b32')}<span style="color:${p.color}">\u25CF</span> ${p.series.name}：<b>${val}</b><br/>`;
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
      s += `<br/><span style="font-size:10px;color:#6b7280">{window.__t('analyticsSection_c1e2')}</span><br/>`;
      for (const ap of uniqueActive) {
        const ci = exposureStats.colorMap[ap.resolvedName] ?? 0;
        const pData = productDataLookup[ap.resolvedName];
        const gmvStr = pData?.gmv > 0 ? ` <span style="color:#f97316;font-weight:bold">¥${Math.round(pData.gmv).toLocaleString()}</span>` : "";
        const statsStr = pData ? ` <span style="color:#9ca3af">{window.__t('analyticsSection_cf29')}</span>` : "";
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
          return `${timeStr} (${min}分)`;
        },
        style: { fontSize: "9px", color: "#9ca3af" },
      },
      lineColor: "#e5e7eb", tickColor: "#e5e7eb",
    },
    yAxis: [
      ...(trendChart.hasGmv ? [{ title: { text: null }, labels: { formatter: function () { return "¥" + (this.value >{window.__t('analyticsSection_e6e4')}<div className="w-full mt-6 mx-auto mb-4">
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
              <div className="text-gray-900 text-xl font-semibold">{window.__t('livePerformance')}</div>
              <div className="text-gray-500 text-sm mt-1">{window.__t('analyticsSection_e870')}</div>
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
                      </svg>{window.__t('analyticsSection_29a7')}</p>
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
                            onClick={() =>{window.__t('analyticsSection_cd82')}</div>

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
                            <span className="text-gray-400">{window.__t('analyticsSection_8212')}</span>
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
                          <span className="text-gray-400">{window.__t('analyticsSection_8212')}</span>
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
                      <span className="text-sm font-semibold text-gray-700">{window.__t('analyticsSection_b9b5')}</span>
                      <span className="text-xs text-gray-400">{window.__t('analyticsSection_50fa')}</span>
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
                          <th className="text-left text-xs text-gray-400 font-medium py-3 pr-4">{window.__t('analyticsSection_de54')}</th>
                          <th className="text-right text-xs text-gray-400 font-medium py-3 px-2">{window.__t('live_sales')}</th>
                          <th className="text-right text-xs text-gray-400 font-medium py-3 px-2">{window.__t('analyticsSection_c4c7')}</th>
                          <th className="text-center text-xs text-gray-400 font-medium py-3 px-2">{window.__t('analyticsSection_a3cf')}</th>
                          <th className="text-center text-xs text-gray-400 font-medium py-3 px-2">CVR</th>
                          <th className="text-center text-xs text-gray-400 font-medium py-3 px-2">{window.__t('analyticsSection_7161')}</th>
                          <th className="text-left text-xs text-gray-400 font-medium py-3 px-2 hidden md:table-cell" style={{ minWidth: 80 }}>{window.__t('analyticsSection_0814')}</th>
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
                              <td className="text-right py-5 px-2 text-gray-600">{window.__t('analyticsSection_288c')}</td>
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
                                  <div className="text-sm font-bold text-gray-700">{window.__t('analyticsSection_a980')}</div>
                                  <div className="text-[10px] text-gray-400">{window.__t('analyticsSection_9586')}</div>
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
                                          onClick={(e) =>{window.__t('analyticsSection_4edb')}</div>
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
                    <span className="text-xs text-blue-500">{window.__t('analyticsSection_e77a')}</span>
                  </div>
                  <button onClick={() =>{window.__t('analyticsSection_c18c')}</button>
                </div>
                <div className="p-4">
                  <div className="text-xs text-gray-500 mb-2">{window.__t('analyticsSection_c195')}</div>
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
                  </svg>{window.__t('analyticsSection_044b')}</div>
                <div className="text-2xl font-bold text-gray-900">¥{Math.round(trendChart?.excelTotalGmv ?? excelProducts?.totalProductGmv ?? agg.totalGmv).toLocaleString()}</div>
                <div className="text-xs text-gray-400 mt-1">{window.__t('analyticsSection_1cc3')}</div>
              </div>
              <div className="rounded-xl bg-white border border-gray-200 p-4 shadow-sm">
                <div className="flex items-center gap-2 text-gray-500 text-xs font-medium mb-1">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-blue-500">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" />
                  </svg>{window.__t('analyticsSection_2da6')}</div>
                <div className="text-2xl font-bold text-gray-900">{(trendChart?.excelMaxViewers ?? agg.totalViewers).toLocaleString()}</div>
                <div className="text-xs text-gray-400 mt-1">{window.__t('analyticsSection_f785')}</div>
              </div>
              <div className="rounded-xl bg-white border border-gray-200 p-4 shadow-sm">
                <div className="flex items-center gap-2 text-gray-500 text-xs font-medium mb-1">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none" className="text-pink-500">
                    <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                  </svg>{window.__t('live_likes')}</div>
                <div className="text-2xl font-bold text-gray-900">{agg.totalLikes.toLocaleString()}</div>
                <div className="text-xs text-gray-400 mt-1">{window.__t('analyticsSection_285e')}</div>
              </div>
              <div className="rounded-xl bg-white border border-gray-200 p-4 shadow-sm">
                <div className="flex items-center gap-2 text-gray-500 text-xs font-medium mb-1">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-green-500">
                    <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" /><polyline points="10 17 15 12 10 7" /><line x1="15" y1="12" x2="3" y2="12" />
                  </svg>{window.__t('analyticsSection_9133')}</div>
                <div className="text-2xl font-bold text-gray-900">{agg.totalClicks.toLocaleString()}</div>
                <div className="text-xs text-gray-400 mt-1">CVR {agg.cvr}%</div>
              </div>
            </div>

            {/* ── Sub Metrics ── */}
            <div className="grid grid-cols-3 md:grid-cols-6 gap-2">{window.__t('analyticsSection_4f4b')}<div key={i} className="rounded-lg bg-white border border-gray-100 px-3 py-2 text-center">
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
                    <span className="text-sm font-semibold text-gray-700">{window.__t('analyticsSection_d464')}</span>
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
                                    onClick={() =>{window.__t('analyticsSection_a22f')}</div>
                            {/* Stats */}
                            <div className="w-20 flex-shrink-0 text-right text-[10px] text-gray-500">
                              {matchedRank?.gmv > 0 ? `¥${Math.round(matchedRank.gmv).toLocaleString()}` : ""}
                            </div>
                            <div className="w-16 flex-shrink-0 text-right text-[10px] text-gray-400">{window.__t('analyticsSection_ad47')}</div>
                          </div>
                        );
                      })}
                    </div>
                    {/* X-axis labels */}
                    <div className="flex justify-between mt-2 px-32 md:px-48 text-[9px] text-gray-400">
                      {[0, 0.25, 0.5, 0.75, 1].map(pct => (
                        <span key={pct}>
                          {streamStartTime ? addSecondsToTime(streamStartTime, pct * videoDuration) : formatTime(pct * videoDuration)}
                          <br />{window.__t('analyticsSection_e218')}</span>
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
                    <span className="text-sm font-semibold text-gray-700">{window.__t('analyticsSection_db3f')}</span>
                    <span className="text-xs text-gray-400">{window.__t('analyticsSection_0dd6')}</span>
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
                        <button onClick={() =>{window.__t('analyticsSection_c820')}</button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── Loading Excel data indicator ── */}
            {loadingExcel && (
              <div className="text-center text-xs text-gray-400 py-2">{window.__t('analyticsSection_00b1')}</div>
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
                    <span className="text-sm font-semibold text-gray-700">{window.__t('videoDetail_productData')}</span>
                    <span className="text-xs text-gray-400">{window.__t('analyticsSection_b2f9')}</span>
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
                        <div className="text-center text-xs text-gray-400 py-2">{window.__t('analyticsSection_cd6c')}</div>
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
                    <span className="text-sm font-semibold text-gray-700">{window.__t('analyticsSection_2b7b')}</span>
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
                            <th className="text-left text-xs text-gray-400 font-medium py-2 pr-4">{window.__t('analyticsSection_de54')}</th>
                            <th className="text-right text-xs text-gray-400 font-medium py-2 px-2">{window.__t('live_sales')}</th>
                            <th className="text-right text-xs text-gray-400 font-medium py-2 px-2">{window.__t('analyticsSection_c4c7')}</th>
                            <th className="text-right text-xs text-gray-400 font-medium py-2 px-2">{window.__t('clickCount')}</th>
                            <th className="text-right text-xs text-gray-400 font-medium py-2 pl-2">{window.__t('analyticsSection_8cf9')}</th>
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
                              <td className="text-right py-2 px-2 text-gray-600">{window.__t('analyticsSection_eb0c')}</td>
                              <td className="text-right py-2 px-2 text-gray-600">{p.clicks}</td>
                              <td className="text-right py-2 pl-2 text-gray-500">{window.__t('analyticsSection_0838')}</td>
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
