import { useState, useEffect, useCallback, useRef } from "react";
import VideoService from "../base/services/videoService";

/**
 * 商品タイムライン表示コンポーネント
 * - AI検出された商品露出セグメントをタイムラインバーで表示
 * - インライン編集（商品名、時間範囲の変更）
 * - 手動追加・削除
 */

// ─── Color palette for products ───────────────────────────
const PRODUCT_COLORS = [
  { bg: "bg-blue-100", border: "border-blue-300", text: "text-blue-700", bar: "#3b82f6" },
  { bg: "bg-emerald-100", border: "border-emerald-300", text: "text-emerald-700", bar: "#10b981" },
  { bg: "bg-amber-100", border: "border-amber-300", text: "text-amber-700", bar: "#f59e0b" },
  { bg: "bg-purple-100", border: "border-purple-300", text: "text-purple-700", bar: "#8b5cf6" },
  { bg: "bg-rose-100", border: "border-rose-300", text: "text-rose-700", bar: "#f43f5e" },
  { bg: "bg-cyan-100", border: "border-cyan-300", text: "text-cyan-700", bar: "#06b6d4" },
  { bg: "bg-orange-100", border: "border-orange-300", text: "text-orange-700", bar: "#f97316" },
  { bg: "bg-indigo-100", border: "border-indigo-300", text: "text-indigo-700", bar: "#6366f1" },
];

function getProductColor(index) {
  return PRODUCT_COLORS[index % PRODUCT_COLORS.length];
}

function formatTime(seconds) {
  if (seconds == null || isNaN(seconds)) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function parseTimeInput(value) {
  // Accept "m:ss" or just seconds
  if (value.includes(":")) {
    const [m, s] = value.split(":");
    return parseInt(m || 0) * 60 + parseInt(s || 0);
  }
  return parseFloat(value) || 0;
}

// ─── Timeline Bar ─────────────────────────────────────────
function TimelineBar({ exposures, videoDuration, productColorMap, onSeek }) {
  if (!videoDuration || videoDuration <= 0) return null;

  return (
    <div className="relative w-full h-10 bg-gray-100 rounded-lg overflow-hidden border border-gray-200">
      {/* Time markers */}
      {[0, 0.25, 0.5, 0.75, 1].map((pct) => (
        <div
          key={pct}
          className="absolute top-0 h-full border-l border-gray-300 opacity-40"
          style={{ left: `${pct * 100}%` }}
        >
          <span className="absolute -bottom-5 left-0 text-[9px] text-gray-400 transform -translate-x-1/2">
            {formatTime(pct * videoDuration)}
          </span>
        </div>
      ))}

      {/* Exposure bars */}
      {exposures.map((exp, idx) => {
        const left = (exp.time_start / videoDuration) * 100;
        const width = ((exp.time_end - exp.time_start) / videoDuration) * 100;
        const color = productColorMap[exp.product_name] || PRODUCT_COLORS[0];

        return (
          <div
            key={exp.id || idx}
            className="absolute top-1 h-8 rounded cursor-pointer transition-all hover:opacity-90 hover:scale-y-110"
            style={{
              left: `${Math.max(0, left)}%`,
              width: `${Math.max(0.5, width)}%`,
              backgroundColor: color.bar,
              opacity: Math.max(0.4, exp.confidence || 0.8),
            }}
            title={`${exp.product_name} (${formatTime(exp.time_start)} - ${formatTime(exp.time_end)})`}
            onClick={() => onSeek && onSeek(exp.time_start)}
          />
        );
      })}
    </div>
  );
}

// ─── Exposure Row ─────────────────────────────────────────
function ExposureRow({ exposure, color, onUpdate, onDelete, isEditing, setEditing }) {
  const [editData, setEditData] = useState({
    product_name: exposure.product_name,
    time_start: formatTime(exposure.time_start),
    time_end: formatTime(exposure.time_end),
  });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onUpdate(exposure.id, {
        product_name: editData.product_name,
        time_start: parseTimeInput(editData.time_start),
        time_end: parseTimeInput(editData.time_end),
      });
      setEditing(null);
    } catch (e) {
      console.error("Save failed:", e);
    }
    setSaving(false);
  };

  if (isEditing) {
    return (
      <div className={`flex items-center gap-2 p-2 rounded-lg ${color.bg} ${color.border} border`}>
        <input
          className="flex-1 text-sm px-2 py-1 rounded border border-gray-300 bg-white"
          value={editData.product_name}
          onChange={(e) => setEditData({ ...editData, product_name: e.target.value })}
          placeholder={window.__t('analyticsSection_de54', '商品名')}
        />
        <input
          className="w-16 text-sm px-2 py-1 rounded border border-gray-300 bg-white text-center"
          value={editData.time_start}
          onChange={(e) => setEditData({ ...editData, time_start: e.target.value })}
          placeholder="0:00"
        />
        <span className="text-gray-400 text-xs">-</span>
        <input
          className="w-16 text-sm px-2 py-1 rounded border border-gray-300 bg-white text-center"
          value={editData.time_end}
          onChange={(e) => setEditData({ ...editData, time_end: e.target.value })}
          placeholder="0:00"
        />
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-2 py-1 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50"
        >
          {saving ? "..." : window.__t('common_save', '保存')}
        </button>
        <button
          onClick={() => setEditing(null)}
          className="px-2 py-1 text-xs bg-gray-300 text-gray-700 rounded hover:bg-gray-400"
        >
          取消
        </button>
      </div>
    );
  }

  return (
    <div className={`flex items-center gap-3 p-2 rounded-lg ${color.bg} border ${color.border} group`}>
      {/* Color dot */}
      <div className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: color.bar }} />

      {/* Product name */}
      <div className="flex-1 min-w-0">
        <div className={`text-sm font-medium ${color.text} truncate`}>{exposure.product_name}</div>
        {exposure.brand_name && (
          <div className="text-xs text-gray-500">{exposure.brand_name}</div>
        )}
      </div>

      {/* Time range */}
      <div className="text-xs text-gray-500 whitespace-nowrap">
        {formatTime(exposure.time_start)} - {formatTime(exposure.time_end)}
      </div>

      {/* Confidence badge */}
      <div className={`text-[10px] px-1.5 py-0.5 rounded-full ${
        exposure.confidence >= 0.8
          ? "bg-green-100 text-green-700"
          : exposure.confidence >= 0.5
            ? "bg-yellow-100 text-yellow-700"
            : "bg-red-100 text-red-700"
      }`}>
        {Math.round((exposure.confidence || 0) * 100)}%
      </div>

      {/* Source badge */}
      <div className={`text-[10px] px-1.5 py-0.5 rounded-full ${
        exposure.source === "human"
          ? "bg-blue-100 text-blue-700"
          : "bg-gray-100 text-gray-500"
      }`}>
        {exposure.source === "human" ? "手動" : "AI"}
      </div>

      {/* Actions (visible on hover) */}
      <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={() => setEditing(exposure.id)}
          className="p-1 text-gray-400 hover:text-blue-500"
          title={window.__t('clip_edit', '編集')}
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
          </svg>
        </button>
        <button
          onClick={() => onDelete(exposure.id)}
          className="p-1 text-gray-400 hover:text-red-500"
          title={window.__t('sidebar_delete', '削除')}
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          </svg>
        </button>
      </div>
    </div>
  );
}

// ─── Add New Form ─────────────────────────────────────────
function AddExposureForm({ onAdd, onCancel }) {
  const [data, setData] = useState({
    product_name: "",
    brand_name: "",
    time_start: "",
    time_end: "",
  });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async () => {
    if (!data.product_name || !data.time_start || !data.time_end) return;
    setSaving(true);
    try {
      await onAdd({
        product_name: data.product_name,
        brand_name: data.brand_name,
        time_start: parseTimeInput(data.time_start),
        time_end: parseTimeInput(data.time_end),
        confidence: 1.0,
      });
      setData({ product_name: "", brand_name: "", time_start: "", time_end: "" });
    } catch (e) {
      console.error("Add failed:", e);
    }
    setSaving(false);
  };

  return (
    <div className="flex flex-wrap items-center gap-2 p-3 rounded-lg bg-blue-50 border border-blue-200">
      <input
        className="flex-1 min-w-[120px] text-sm px-2 py-1.5 rounded border border-gray-300 bg-white"
        value={data.product_name}
        onChange={(e) => setData({ ...data, product_name: e.target.value })}
        placeholder={window.__t('productTimeline_5ef1b7', '商品名 *')}
      />
      <input
        className="w-24 text-sm px-2 py-1.5 rounded border border-gray-300 bg-white"
        value={data.brand_name}
        onChange={(e) => setData({ ...data, brand_name: e.target.value })}
        placeholder={window.__t('productTimeline_d21f2c', 'ブランド')}
      />
      <input
        className="w-16 text-sm px-2 py-1.5 rounded border border-gray-300 bg-white text-center"
        value={data.time_start}
        onChange={(e) => setData({ ...data, time_start: e.target.value })}
        placeholder={window.__t('analytics_start', '開始')}
      />
      <span className="text-gray-400 text-xs">-</span>
      <input
        className="w-16 text-sm px-2 py-1.5 rounded border border-gray-300 bg-white text-center"
        value={data.time_end}
        onChange={(e) => setData({ ...data, time_end: e.target.value })}
        placeholder={window.__t('analytics_end', '終了')}
      />
      <button
        onClick={handleSubmit}
        disabled={saving || !data.product_name || !data.time_start || !data.time_end}
        className="px-3 py-1.5 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50"
      >
        {saving ? [window.__t('productTimeline_f5915d', '追加中...')] : window.__t('analytics_add', '追加')}
      </button>
      <button
        onClick={onCancel}
        className="px-3 py-1.5 text-xs bg-gray-200 text-gray-700 rounded hover:bg-gray-300"
      >
        キャンセル
      </button>
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────
export default function ProductTimeline({ videoId, videoDuration }) {
  const [exposures, setExposures] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  // Build product -> color map
  const productColorMap = {};
  const uniqueProducts = [...new Set(exposures.map((e) => e.product_name))];
  uniqueProducts.forEach((name, idx) => {
    productColorMap[name] = getProductColor(idx);
  });

  // Fetch exposures
  const fetchExposures = useCallback(async () => {
    if (!videoId) return;
    setLoading(true);
    try {
      const result = await VideoService.getProductExposures(videoId);
      setExposures(result?.exposures || []);
    } catch (e) {
      console.error("Failed to fetch exposures:", e);
    }
    setLoading(false);
  }, [videoId]);

  useEffect(() => {
    fetchExposures();
  }, [fetchExposures]);

  // CRUD handlers
  const handleUpdate = async (exposureId, data) => {
    await VideoService.updateProductExposure(videoId, exposureId, data);
    await fetchExposures();
  };

  const handleDelete = async (exposureId) => {
    if (!window.confirm(window.__t('analytics_confirmDeleteSegment', 'この商品露出セグメントを削除しますか？'))) return;
    await VideoService.deleteProductExposure(videoId, exposureId);
    await fetchExposures();
  };

  const handleAdd = async (data) => {
    await VideoService.createProductExposure(videoId, data);
    await fetchExposures();
    setShowAddForm(false);
  };

  // Summary stats
  const totalDuration = exposures.reduce(
    (sum, e) => sum + (e.time_end - e.time_start),
    0,
  );
  const productCount = uniqueProducts.length;

  if (loading) {
    return (
      <div className="w-full mt-4 mx-auto">
        <div className="rounded-2xl bg-gray-50 border border-gray-200 p-5">
          <div className="flex items-center gap-3">
            <div className="w-5 h-5 border-2 border-gray-300 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm text-gray-500">{window.__t('productTimeline_08ec86', '商品タイムラインを読み込み中...')}</span>
          </div>
        </div>
      </div>
    );
  }

  if (exposures.length === 0 && !showAddForm) {
    return (
      <div className="w-full mt-4 mx-auto">
        <div className="rounded-2xl bg-gray-50 border border-gray-200 p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-gray-400">
                <path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z" />
                <line x1="3" y1="6" x2="21" y2="6" />
                <path d="M16 10a4 4 0 0 1-8 0" />
              </svg>
              <span className="text-sm text-gray-500">{window.__t('productTimeline_a80f01', '商品タイムラインデータがありません')}</span>
            </div>
            <button
              onClick={() => setShowAddForm(true)}
              className="px-3 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
            >
              + 手動追加
            </button>
          </div>
          {showAddForm && (
            <div className="mt-3">
              <AddExposureForm onAdd={handleAdd} onCancel={() => setShowAddForm(false)} />
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="w-full mt-4 mx-auto">
      <div className="rounded-2xl bg-gray-50 border border-gray-200">
        {/* Header */}
        <div
          onClick={() => setCollapsed((s) => !s)}
          className="flex items-center justify-between p-5 cursor-pointer hover:bg-gray-100 transition-all duration-200"
        >
          <div className="flex items-center gap-4">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-5 h-5 text-gray-700">
              <path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z" />
              <line x1="3" y1="6" x2="21" y2="6" />
              <path d="M16 10a4 4 0 0 1-8 0" />
            </svg>
            <div>
              <div className="text-gray-900 text-xl font-semibold">{window.__t('productTimeline_65f31f', '商品タイムライン')}</div>
              <div className="text-gray-500 text-sm mt-1">
                {productCount}商品 / {exposures.length}セグメント / 合計 {formatTime(totalDuration)}
              </div>
            </div>
          </div>

          <button className="p-2 rounded-full hover:bg-gray-200 transition-colors">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`transition-transform duration-300 ${collapsed ? "" : "rotate-180"}`}
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
        </div>

        {/* Content */}
        {!collapsed && (
          <div className="px-5 pb-5">
            {/* Timeline Bar */}
            <div className="mb-6">
              <TimelineBar
                exposures={exposures}
                videoDuration={videoDuration || Math.max(...exposures.map((e) => e.time_end), 60)}
                productColorMap={productColorMap}
              />
              <div className="h-5" /> {/* Spacer for time labels */}
            </div>

            {/* Product Legend */}
            <div className="flex flex-wrap gap-2 mb-4">
              {uniqueProducts.map((name, idx) => {
                const color = getProductColor(idx);
                const count = exposures.filter((e) => e.product_name === name).length;
                return (
                  <div
                    key={name}
                    className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-xs ${color.bg} ${color.text}`}
                  >
                    <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color.bar }} />
                    {name} ({count})
                  </div>
                );
              })}
            </div>

            {/* Exposure List */}
            <div className="flex flex-col gap-2">
              {exposures.map((exp, idx) => (
                <ExposureRow
                  key={exp.id || idx}
                  exposure={exp}
                  color={productColorMap[exp.product_name] || PRODUCT_COLORS[0]}
                  onUpdate={handleUpdate}
                  onDelete={handleDelete}
                  isEditing={editingId === exp.id}
                  setEditing={setEditingId}
                />
              ))}
            </div>

            {/* Add button / form */}
            <div className="mt-3">
              {showAddForm ? (
                <AddExposureForm onAdd={handleAdd} onCancel={() => setShowAddForm(false)} />
              ) : (
                <button
                  onClick={() => setShowAddForm(true)}
                  className="w-full py-2 text-sm text-gray-500 border border-dashed border-gray-300 rounded-lg hover:bg-gray-100 hover:text-gray-700 transition-colors"
                >
                  + 商品セグメントを手動追加
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
