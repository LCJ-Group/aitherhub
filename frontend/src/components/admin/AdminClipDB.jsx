import { useState, useEffect, useRef, useCallback } from "react";
import {
  Search, Filter, Tag, Play, X, BarChart3, ShoppingBag, Users,
  Star, ThumbsUp, ThumbsDown, ArrowUpDown, Database, Sparkles,
  Loader2, RefreshCw, ChevronLeft, ChevronRight, Building2, Plus, Minus,
  Download, Subtitles, Scissors, CheckCircle, Ban, AlertTriangle, Undo2,
  MessageSquare, SkipBack, SkipForward, Volume2, VolumeX,
  TrendingUp, Activity, Brain, Clock, Zap, Target, Eye,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

// ─── Helper: API call with admin key ───
async function clipDbFetch(path, params = {}, adminKey) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== "") qs.set(k, v);
  });
  const url = `${API_BASE}/api/v1/clip-db${path}?${qs.toString()}`;
  const res = await fetch(url, {
    headers: { "X-Admin-Key": adminKey },
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

// ─── English → Japanese tag label mapping ───
const TAG_LABEL_MAP = {
  HOOK: "フック", EMPATHY: "共感", PROBLEM: "問題提起",
  EDUCATION: "教育", SOLUTION: "解決策", DEMONSTRATION: "実演",
  COMPARISON: "比較", PROOF: "証拠", TRUST: "信頼",
  SOCIAL_PROOF: "社会的証明", OBJECTION_HANDLING: "反論処理",
  URGENCY: "緊急性", LIMITED_OFFER: "限定オファー", BONUS: "特典",
  CTA: "行動喚起", PRICE: "価格訴求", STORY: "ストーリー",
};
function getTagLabel(tag) {
  return TAG_LABEL_MAP[tag] || tag;
}

// ─── Tag color mapping ───
const TAG_COLORS = {
  "共感": { bg: "#FEF3C7", text: "#92400E", border: "#FDE68A" },
  "権威": { bg: "#DBEAFE", text: "#1E40AF", border: "#93C5FD" },
  "限定性": { bg: "#FCE7F3", text: "#9D174D", border: "#F9A8D4" },
  "実演": { bg: "#D1FAE5", text: "#065F46", border: "#6EE7B7" },
  "比較": { bg: "#E0E7FF", text: "#3730A3", border: "#A5B4FC" },
  "ストーリー": { bg: "#FEE2E2", text: "#991B1B", border: "#FCA5A5" },
  "テンション": { bg: "#FFF7ED", text: "#9A3412", border: "#FDBA74" },
  "緊急性": { bg: "#FEF9C3", text: "#854D0E", border: "#FDE047" },
  "社会的証明": { bg: "#F0FDF4", text: "#166534", border: "#86EFAC" },
  "価格訴求": { bg: "#ECFDF5", text: "#047857", border: "#6EE7B7" },
  "問題提起": { bg: "#FFF1F2", text: "#9F1239", border: "#FDA4AF" },
  "解決提示": { bg: "#F0F9FF", text: "#0C4A6E", border: "#7DD3FC" },
  HOOK: { bg: "#F5F3FF", text: "#6D28D9", border: "#C4B5FD" },
  EMPATHY: { bg: "#FEF3C7", text: "#92400E", border: "#FDE68A" },
  PROBLEM: { bg: "#FFF1F2", text: "#9F1239", border: "#FDA4AF" },
  EDUCATION: { bg: "#DBEAFE", text: "#1E40AF", border: "#93C5FD" },
  SOLUTION: { bg: "#D1FAE5", text: "#065F46", border: "#6EE7B7" },
  DEMONSTRATION: { bg: "#CCFBF1", text: "#0F766E", border: "#5EEAD4" },
  COMPARISON: { bg: "#E0E7FF", text: "#3730A3", border: "#A5B4FC" },
  PROOF: { bg: "#CFFAFE", text: "#155E75", border: "#67E8F9" },
  TRUST: { bg: "#D1FAE5", text: "#065F46", border: "#6EE7B7" },
  SOCIAL_PROOF: { bg: "#F0FDF4", text: "#166534", border: "#86EFAC" },
  OBJECTION_HANDLING: { bg: "#FEF3C7", text: "#92400E", border: "#FDE68A" },
  URGENCY: { bg: "#FFF7ED", text: "#9A3412", border: "#FDBA74" },
  LIMITED_OFFER: { bg: "#FCE7F3", text: "#9D174D", border: "#F9A8D4" },
  BONUS: { bg: "#ECFCCB", text: "#3F6212", border: "#BEF264" },
  CTA: { bg: "#FEE2E2", text: "#991B1B", border: "#FCA5A5" },
  PRICE: { bg: "#ECFDF5", text: "#047857", border: "#6EE7B7" },
  STORY: { bg: "#FEE2E2", text: "#991B1B", border: "#FCA5A5" },
};
function getTagColor(tag) {
  return TAG_COLORS[tag] || { bg: "#F3F4F6", text: "#374151", border: "#D1D5DB" };
}

// ─── Format helpers ───
function formatDuration(sec) {
  if (!sec) return "--";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
function formatGMV(val) {
  if (!val) return "¥0";
  if (val >= 10000) return `¥${(val / 10000).toFixed(1)}万`;
  return `¥${Math.round(val).toLocaleString()}`;
}

// ─── Brand Badge Colors ───
const BRAND_COLORS = [
  { bg: "#DBEAFE", text: "#1E40AF", border: "#93C5FD" },
  { bg: "#D1FAE5", text: "#065F46", border: "#6EE7B7" },
  { bg: "#FEF3C7", text: "#92400E", border: "#FDE68A" },
  { bg: "#FCE7F3", text: "#9D174D", border: "#F9A8D4" },
  { bg: "#E0E7FF", text: "#3730A3", border: "#A5B4FC" },
  { bg: "#CCFBF1", text: "#0F766E", border: "#5EEAD4" },
];
function getBrandColor(idx) {
  return BRAND_COLORS[idx % BRAND_COLORS.length];
}

// ─── Clip Card Component ───
// ─── NG Reason labels ───
const NG_REASONS = [
  { key: "low_quality", label: "画質が悪い" },
  { key: "audio_bad", label: "音声が悪い" },
  { key: "irrelevant", label: "内容が無関係" },
  { key: "too_short", label: "短すぎる" },
  { key: "too_long", label: "長すぎる" },
  { key: "cut_position_bad", label: "カット位置が悪い" },
  { key: "duplicate", label: "重複" },
  { key: "no_product", label: "商品が映っていない" },
  { key: "blurry", label: "ぼやけている" },
  { key: "other", label: "その他" },
];

function ClipCard({ clip, onPlay, brands, adminKey, onBrandChange }) {
  const [expanded, setExpanded] = useState(false);
  const [showBrandPicker, setShowBrandPicker] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [brandSearch, setBrandSearch] = useState("");
  const [showNewBrandForm, setShowNewBrandForm] = useState(false);
  const [newBrandName, setNewBrandName] = useState("");
  const [newBrandCompany, setNewBrandCompany] = useState("");
  const [creatingBrand, setCreatingBrand] = useState(false);
  const [showNgPicker, setShowNgPicker] = useState(false);
  const [ngLoading, setNgLoading] = useState(false);
  const [localUnusable, setLocalUnusable] = useState(clip.is_unusable || false);
  const [localReason, setLocalReason] = useState(clip.unusable_reason || "");
  const [localComment, setLocalComment] = useState(clip.unusable_comment || "");
  const [ngComment, setNgComment] = useState("");
  const [selectedNgReason, setSelectedNgReason] = useState(null);
  const tags = clip.tags || clip.sales_psychology_tags || [];
  const assignments = clip.brand_assignments || [];

  const handleMarkNG = async (reasonKey, comment = "") => {
    setNgLoading(true);
    try {
      const body = { reason: reasonKey };
      if (comment.trim()) body.comment = comment.trim();
      const res = await fetch(
        `${API_BASE}/api/v1/clip-db/mark-unusable?clip_id=${clip.clip_id}`,
        {
          method: "POST",
          headers: { "X-Admin-Key": adminKey, "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      );
      if (res.ok) {
        setLocalUnusable(true);
        setLocalReason(reasonKey);
        setLocalComment(comment.trim());
        setShowNgPicker(false);
        setSelectedNgReason(null);
        setNgComment("");
      }
    } catch (e) {
      console.error("Mark NG failed:", e);
    } finally {
      setNgLoading(false);
    }
  };

  const handleUnmarkNG = async () => {
    setNgLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/clip-db/unmark-unusable?clip_id=${clip.clip_id}`,
        {
          method: "POST",
          headers: { "X-Admin-Key": adminKey },
        }
      );
      if (res.ok) {
        setLocalUnusable(false);
        setLocalReason("");
      }
    } catch (e) {
      console.error("Unmark NG failed:", e);
    } finally {
      setNgLoading(false);
    }
  };

  const handleAssignBrand = async (clientId) => {
    setAssigning(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/clip-db/assign-brand?clip_id=${clip.clip_id}&client_id=${clientId}`,
        { method: "POST", headers: { "X-Admin-Key": adminKey } }
      );
      if (res.ok) {
        onBrandChange?.();
      }
    } catch (e) {
      console.error("Assign brand failed:", e);
    } finally {
      setAssigning(false);
      setShowBrandPicker(false);
    }
  };

  const handleCreateAndAssignBrand = async () => {
    if (!newBrandName.trim()) return;
    setCreatingBrand(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/clip-db/brands/create`, {
        method: "POST",
        headers: { "X-Admin-Key": adminKey, "Content-Type": "application/json" },
        body: JSON.stringify({ name: newBrandName.trim(), company_name: newBrandCompany.trim() || null }),
      });
      if (res.ok) {
        const data = await res.json();
        // Assign the newly created brand to this clip
        await fetch(
          `${API_BASE}/api/v1/clip-db/assign-brand?clip_id=${clip.clip_id}&client_id=${data.client_id}`,
          { method: "POST", headers: { "X-Admin-Key": adminKey } }
        );
        setShowNewBrandForm(false);
        setNewBrandName("");
        setNewBrandCompany("");
        setShowBrandPicker(false);
        onBrandChange?.();
      }
    } catch (e) {
      console.error("Create brand failed:", e);
    } finally {
      setCreatingBrand(false);
    }
  };

  const handleUnassignBrand = async (clientId) => {
    setAssigning(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/clip-db/unassign-brand?clip_id=${clip.clip_id}&client_id=${clientId}`,
        { method: "DELETE", headers: { "X-Admin-Key": adminKey } }
      );
      if (res.ok) {
        onBrandChange?.();
      }
    } catch (e) {
      console.error("Unassign brand failed:", e);
    } finally {
      setAssigning(false);
    }
  };

  // Brands not yet assigned, filtered by search
  const unassignedBrands = brands.filter(
    (b) => !assignments.some((a) => a.client_id === b.client_id)
  ).filter(
    (b) => {
      if (!brandSearch) return true;
      const q = brandSearch.toLowerCase();
      return (
        b.name.toLowerCase().includes(q) ||
        (b.company_name || "").toLowerCase().includes(q) ||
        (b.name_ja || "").toLowerCase().includes(q) ||
        (b.brand_keywords || "").toLowerCase().includes(q)
      );
    }
  );

  // Get NG reason label
  const ngReasonLabel = localReason
    ? (NG_REASONS.find(r => localReason.startsWith(r.key))?.label || localReason)
    : "";

  return (
    <div className={`rounded-xl border overflow-hidden hover:shadow-lg transition-all duration-300 group relative ${
      localUnusable ? "border-red-300 bg-red-50 opacity-75" : "border-gray-200 bg-white"
    }`}>
      {/* NG overlay */}
      {localUnusable && (
        <div className="absolute inset-0 z-10 pointer-events-none">
          <div className="absolute inset-0 bg-red-500/10" />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 rotate-[-20deg]">
            <span className="text-red-500/30 text-5xl font-black tracking-widest select-none">NG</span>
          </div>
        </div>
      )}
      {/* Video preview area */}
      <div className="relative aspect-[9/16] max-h-[280px] bg-gradient-to-br from-gray-900 to-gray-800 overflow-hidden">
        {clip.clip_url ? (
          <video
            src={clip.clip_url}
            className="w-full h-full object-cover"
            preload="metadata"
            muted
            onMouseEnter={(e) => { try { e.target.play(); } catch {} }}
            onMouseLeave={(e) => { try { e.target.pause(); e.target.currentTime = 0; } catch {} }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-gray-500">
            <Play className="w-8 h-8" />
          </div>
        )}
        {/* Overlay badges */}
        <div className="absolute top-2 left-2 flex flex-wrap gap-1">
          {clip.is_sold === true && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-green-500 text-white shadow">SOLD</span>
          )}
          {clip.is_sold === false && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-gray-500 text-white shadow">未売</span>
          )}
          {clip.rating === "good" && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-blue-500 text-white shadow flex items-center gap-0.5">
              <ThumbsUp className="w-2.5 h-2.5" /> Good
            </span>
          )}
          {clip.rating === "bad" && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-red-500 text-white shadow flex items-center gap-0.5">
              <ThumbsDown className="w-2.5 h-2.5" /> Bad
            </span>
          )}
          {clip.ml_model_version && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-purple-600 text-white shadow">
              AI {clip.ml_model_version}
            </span>
          )}
          {clip.detected_language && clip.detected_language !== 'ja' && clip.detected_language !== 'unknown' && (
            <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-bold shadow ${
              clip.detected_language === 'zh-TW' ? 'bg-rose-500 text-white' :
              clip.detected_language === 'zh-CN' ? 'bg-red-600 text-white' :
              clip.detected_language === 'en' ? 'bg-blue-600 text-white' :
              clip.detected_language === 'ko' ? 'bg-sky-500 text-white' :
              clip.detected_language === 'th' ? 'bg-amber-500 text-white' :
              'bg-gray-500 text-white'
            }`}>
              {clip.detected_language === 'zh-TW' ? '繁中' :
               clip.detected_language === 'zh-CN' ? '簡中' :
               clip.detected_language === 'en' ? 'EN' :
               clip.detected_language === 'ko' ? '韓' :
               clip.detected_language === 'th' ? 'TH' :
               clip.detected_language.toUpperCase()}
            </span>
          )}
        </div>
        {/* Edit/Download status badges - top right */}
        <div className="absolute top-2 right-2 flex flex-col gap-1 items-end">
          {clip.has_subtitle && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-purple-500 text-white shadow flex items-center gap-0.5">
              <Subtitles className="w-2.5 h-2.5" /> 字幕済
            </span>
          )}
          {clip.subtitle_style && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-indigo-500 text-white shadow flex items-center gap-0.5">
              {clip.subtitle_style === 'karaoke' ? '🎤' : clip.subtitle_style === 'highlight' ? '✦' : '☰'}
              {clip.subtitle_language === 'en' ? 'EN' : clip.subtitle_language === 'zh-TW' ? 'TW' : 'JA'}
              {clip.subtitle_font_size ? ` ${clip.subtitle_font_size}px` : ''}
            </span>
          )}
          {clip.trim_data && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-teal-500 text-white shadow flex items-center gap-0.5">
              <Scissors className="w-2.5 h-2.5" /> カット済
            </span>
          )}
          {clip.download_count > 0 && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-orange-500 text-white shadow flex items-center gap-0.5">
              <Download className="w-2.5 h-2.5" /> DL {clip.download_count}
            </span>
          )}
        </div>
        {/* Duration badge */}
        <div className="absolute bottom-2 right-2">
          <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-black/70 text-white">
            {formatDuration(clip.duration_sec)}
          </span>
        </div>
        {/* Play button overlay */}
        {clip.clip_url && (
          <div
            className="absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/20 transition-all cursor-pointer"
            onClick={() => onPlay(clip)}
          >
            <Play className="w-10 h-10 text-white opacity-0 group-hover:opacity-90 transition-opacity drop-shadow-lg" />
          </div>
        )}
      </div>

      {/* Info area */}
      <div className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-gray-900">
            {clip.gmv > 0 ? formatGMV(clip.gmv) : "--"}
          </span>
          {clip.cta_score != null && (
            <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
              clip.cta_score >= 70 ? "bg-green-100 text-green-700" :
              clip.cta_score >= 40 ? "bg-yellow-100 text-yellow-700" :
              "bg-gray-100 text-gray-500"
            }`}>
              CTA {clip.cta_score}
            </span>
          )}
        </div>
        {/* 生成日 */}
        {clip.created_at && (
          <div className="flex items-center gap-1 text-[10px] text-gray-400">
            <Clock className="w-3 h-3" />
            <span>{new Date(clip.created_at).toLocaleDateString('ja-JP', { year: 'numeric', month: '2-digit', day: '2-digit' })}</span>
          </div>
        )}

        {/* Brand assignments */}
        <div className="relative">
          <div className="flex flex-wrap gap-1 items-center">
            {assignments.map((a, i) => {
              const c = getBrandColor(i);
              return (
                <span
                  key={a.client_id}
                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium border cursor-default group/brand"
                  style={{ backgroundColor: c.bg, color: c.text, borderColor: c.border }}
                >
                  <Building2 className="w-2.5 h-2.5" />
                  {a.brand_name}
                  <button
                    onClick={(e) => { e.stopPropagation(); handleUnassignBrand(a.client_id); }}
                    className="ml-0.5 opacity-0 group-hover/brand:opacity-100 hover:text-red-600 transition-opacity"
                    title="ブランド解除"
                  >
                    <X className="w-2.5 h-2.5" />
                  </button>
                </span>
              );
            })}
            {/* Add brand button */}
            <button
              onClick={(e) => { e.stopPropagation(); setShowBrandPicker(!showBrandPicker); }}
              className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium border border-dashed border-gray-300 text-gray-400 hover:border-blue-400 hover:text-blue-500 transition"
              title="ブランドを追加"
            >
              <Plus className="w-2.5 h-2.5" />
              {assignments.length === 0 ? "ブランド" : ""}
            </button>
          </div>

          {/* Brand picker dropdown */}
          {showBrandPicker && (
            <div className="absolute z-20 top-full left-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[200px] max-h-[280px] flex flex-col">
              {/* Search input */}
              <div className="px-2 py-1.5 border-b border-gray-100">
                <input
                  type="text"
                  placeholder="ブランド検索..."
                  value={brandSearch}
                  onChange={(e) => setBrandSearch(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-200"
                  autoFocus
                />
              </div>
              {/* Brand list */}
              <div className="overflow-y-auto flex-1 max-h-[200px]">
                {assigning && (
                  <div className="px-3 py-2 text-xs text-gray-400 flex items-center gap-1">
                    <Loader2 className="w-3 h-3 animate-spin" /> 処理中...
                  </div>
                )}
                {!assigning && unassignedBrands.length === 0 && (
                  <div className="px-3 py-2 text-xs text-gray-400">
                    {brandSearch ? "該当なし" : "全ブランド割当済み"}
                  </div>
                )}
                {!assigning && unassignedBrands.map((b) => (
                  <button
                    key={b.client_id}
                    onClick={() => handleAssignBrand(b.client_id)}
                    className="w-full text-left px-3 py-1.5 text-xs hover:bg-blue-50 flex items-center gap-1.5 transition"
                  >
                    <Building2 className="w-3 h-3 text-gray-400 flex-shrink-0" />
                    <span className="truncate">{b.name}{b.company_name ? ` (${b.company_name})` : ""}</span>
                    <span className="text-gray-300 ml-auto flex-shrink-0">({b.clip_count})</span>
                  </button>
                ))}
              </div>
              {/* New brand form */}
              {showNewBrandForm ? (
                <div className="border-t border-gray-100 px-2 py-2 space-y-1.5">
                  <input
                    type="text"
                    placeholder="ブランド名（英語）"
                    value={newBrandName}
                    onChange={(e) => setNewBrandName(e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:border-blue-400"
                    autoFocus
                  />
                  <input
                    type="text"
                    placeholder="会社名（任意）"
                    value={newBrandCompany}
                    onChange={(e) => setNewBrandCompany(e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:border-blue-400"
                  />
                  <div className="flex gap-1">
                    <button
                      onClick={handleCreateAndAssignBrand}
                      disabled={!newBrandName.trim() || creatingBrand}
                      className="flex-1 px-2 py-1 text-xs bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50 flex items-center justify-center gap-1"
                    >
                      {creatingBrand ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                      作成&割当
                    </button>
                    <button
                      onClick={() => { setShowNewBrandForm(false); setNewBrandName(""); setNewBrandCompany(""); }}
                      className="px-2 py-1 text-xs text-gray-400 hover:text-gray-600 border border-gray-200 rounded"
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <div className="border-t border-gray-100 pt-1 pb-0.5">
                  <button
                    onClick={(e) => { e.stopPropagation(); setShowNewBrandForm(true); }}
                    className="w-full text-left px-3 py-1.5 text-xs text-blue-500 hover:bg-blue-50 flex items-center gap-1 transition"
                  >
                    <Plus className="w-3 h-3" /> 新規ブランド追加
                  </button>
                  <button
                    onClick={() => { setShowBrandPicker(false); setBrandSearch(""); }}
                    className="w-full text-left px-3 py-1 text-[10px] text-gray-400 hover:text-gray-600"
                  >
                    閉じる
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {clip.product_name && (
          <div className="flex items-center gap-1 text-xs text-gray-600">
            <ShoppingBag className="w-3 h-3 text-gray-400" />
            <span className="truncate">{clip.product_name}</span>
          </div>
        )}
        {clip.liver_name && (
          <div className="flex items-center gap-1 text-xs text-gray-500">
            <Users className="w-3 h-3 text-gray-400" />
            <span className="truncate">{clip.liver_name}</span>
          </div>
        )}
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {tags.slice(0, expanded ? tags.length : 3).map((tag, i) => {
              const c = getTagColor(tag);
              return (
                <span
                  key={i}
                  className="px-1.5 py-0.5 rounded text-[10px] font-medium border"
                  style={{ backgroundColor: c.bg, color: c.text, borderColor: c.border }}
                >
                  {getTagLabel(tag)}
                </span>
              );
            })}
            {tags.length > 3 && !expanded && (
              <button
                onClick={(e) => { e.stopPropagation(); setExpanded(true); }}
                className="text-[10px] text-blue-500 hover:text-blue-700"
              >
                +{tags.length - 3}
              </button>
            )}
          </div>
        )}
        {/* Transcript preview */}
        {clip.transcript_text && (
          <p className="text-[11px] text-gray-400 line-clamp-2 leading-relaxed">
            {clip.transcript_text}
          </p>
        )}

        {/* CLIP EDITOR link */}
        {clip.video_id && (
          <a
            href={`/video/${clip.video_id}?open_editor=1${clip.phase_index != null ? `&phase=${clip.phase_index}` : ''}${clip.time_start != null ? `&t_start=${clip.time_start}` : ''}${clip.time_end != null ? `&t_end=${clip.time_end}` : ''}`}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="flex items-center justify-center gap-1.5 w-full py-1.5 rounded-lg text-[11px] font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 transition-colors"
          >
            <Scissors className="w-3 h-3" /> CLIP EDITOR
          </a>
        )}

        {/* NG mark / unmark section */}
        <div className="relative pt-1 border-t border-gray-100 mt-1">
          {localUnusable ? (
            <div>
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-red-500 font-medium flex items-center gap-0.5">
                  <Ban className="w-3 h-3" /> NG: {ngReasonLabel}
                </span>
                <button
                  onClick={(e) => { e.stopPropagation(); handleUnmarkNG(); }}
                  disabled={ngLoading}
                  className="text-[10px] text-blue-500 hover:text-blue-700 flex items-center gap-0.5 z-20 relative"
                >
                  {ngLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Undo2 className="w-3 h-3" />}
                  NG解除
                </button>
              </div>
              {localComment && (
                <p className="text-[9px] text-red-400 mt-0.5 pl-3.5 italic">「{localComment}」</p>
              )}
            </div>
          ) : (
            <div className="relative">
              <button
                onClick={(e) => { e.stopPropagation(); setShowNgPicker(!showNgPicker); }}
                className="text-[10px] text-gray-400 hover:text-red-500 flex items-center gap-0.5 transition"
              >
                <Ban className="w-3 h-3" /> 使えない
              </button>
              {showNgPicker && (
                <div className="absolute z-30 bottom-full left-0 mb-1 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[220px]" onClick={(e) => e.stopPropagation()}>
                  {!selectedNgReason ? (
                    <>
                      <div className="px-3 py-1.5 text-[10px] font-semibold text-gray-500 border-b border-gray-100 flex items-center gap-1">
                        <AlertTriangle className="w-3 h-3 text-red-400" /> NG理由を選択
                      </div>
                      {NG_REASONS.map((r) => (
                        <button
                          key={r.key}
                          onClick={() => setSelectedNgReason(r.key)}
                          disabled={ngLoading}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-red-50 hover:text-red-600 transition flex items-center gap-1.5"
                        >
                          {r.label}
                        </button>
                      ))}
                      <div className="border-t border-gray-100 pt-0.5">
                        <button
                          onClick={() => { setShowNgPicker(false); setSelectedNgReason(null); setNgComment(""); }}
                          className="w-full text-left px-3 py-1 text-[10px] text-gray-400 hover:text-gray-600"
                        >
                          閉じる
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="px-3 py-1.5 text-[10px] font-semibold text-gray-500 border-b border-gray-100 flex items-center gap-1">
                        <AlertTriangle className="w-3 h-3 text-red-400" />
                        {NG_REASONS.find(r => r.key === selectedNgReason)?.label}
                      </div>
                      <div className="px-3 py-2">
                        <textarea
                          value={ngComment}
                          onChange={(e) => setNgComment(e.target.value)}
                          placeholder="具体的な理由を記入（AI学習に使われます）"
                          className="w-full text-xs border border-gray-200 rounded-md p-2 resize-none focus:outline-none focus:ring-1 focus:ring-red-300 placeholder:text-gray-300"
                          rows={3}
                          onClick={(e) => e.stopPropagation()}
                        />
                        <div className="flex gap-1.5 mt-2">
                          <button
                            onClick={() => handleMarkNG(selectedNgReason, ngComment)}
                            disabled={ngLoading}
                            className="flex-1 px-2 py-1.5 bg-red-500 text-white text-[10px] font-medium rounded-md hover:bg-red-600 transition disabled:opacity-50"
                          >
                            {ngLoading ? "保存中..." : "NGにする"}
                          </button>
                          <button
                            onClick={() => { setSelectedNgReason(null); setNgComment(""); }}
                            className="px-2 py-1.5 text-[10px] text-gray-400 hover:text-gray-600 border border-gray-200 rounded-md"
                          >
                            戻る
                          </button>
                        </div>
                        <p className="text-[9px] text-gray-300 mt-1">※ コメントなしでもOK</p>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Stats Overview ───
function StatsOverview({ stats }) {
  if (!stats) return null;
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
      <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
        <div className="text-2xl font-bold text-gray-900">{stats.total_clips}</div>
        <div className="text-xs text-gray-500 mt-1">総クリップ数</div>
      </div>
      <div className="bg-white rounded-xl border border-green-200 p-4 text-center">
        <div className="text-2xl font-bold text-green-600">{stats.sold_clips}</div>
        <div className="text-xs text-gray-500 mt-1">売れた</div>
      </div>
      <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
        <div className="text-2xl font-bold text-gray-400">{stats.unsold_clips}</div>
        <div className="text-xs text-gray-500 mt-1">未売</div>
      </div>
      <div className="bg-white rounded-xl border border-purple-200 p-4 text-center">
        <div className="text-2xl font-bold text-purple-600">{formatGMV(stats.total_gmv)}</div>
        <div className="text-xs text-gray-500 mt-1">総GMV</div>
      </div>
      <div className="bg-white rounded-xl border border-blue-200 p-4 text-center">
        <div className="text-2xl font-bold text-blue-600">{formatGMV(stats.avg_gmv)}</div>
        <div className="text-xs text-gray-500 mt-1">平均GMV</div>
      </div>
      {stats.avg_cta_score != null && (
        <div className="bg-white rounded-xl border border-orange-200 p-4 text-center">
          <div className="text-2xl font-bold text-orange-600">{Math.round(stats.avg_cta_score)}</div>
          <div className="text-xs text-gray-500 mt-1">平均CTA</div>
        </div>
      )}
    </div>
  );
}

// ─── Top Tags Chart ───
function TopTagsChart({ tags }) {
  if (!tags || tags.length === 0) return null;
  const maxCount = Math.max(...tags.map((t) => t.count));
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <h3 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
        <Tag className="w-4 h-4 text-purple-500" />
        トップタグ（売れた理由）
      </h3>
      <div className="space-y-2">
        {tags.slice(0, 10).map((t) => {
          const c = getTagColor(t.tag);
          const pct = (t.count / maxCount) * 100;
          return (
            <div key={t.tag} className="flex items-center gap-2">
              <span
                className="text-[11px] font-medium px-2 py-0.5 rounded border whitespace-nowrap min-w-[70px] text-center"
                style={{ backgroundColor: c.bg, color: c.text, borderColor: c.border }}
              >
                {getTagLabel(t.tag)}
              </span>
              <div className="flex-1 h-4 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{ width: `${pct}%`, backgroundColor: c.border }}
                />
              </div>
              <span className="text-xs text-gray-500 min-w-[30px] text-right">{t.count}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Top Products Chart ───
function TopProductsChart({ products }) {
  if (!products || products.length === 0) return null;
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <h3 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
        <ShoppingBag className="w-4 h-4 text-blue-500" />
        トップ商品
      </h3>
      <div className="space-y-2">
        {products.slice(0, 10).map((p, i) => (
          <div key={i} className="flex items-center justify-between text-xs">
            <span className="truncate text-gray-700 flex-1">{p.product || "不明"}</span>
            <span className="text-gray-400 ml-2">{p.count}件</span>
            <span className="text-green-600 font-medium ml-2">{formatGMV(p.gmv)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Video Player Modal (Check Mode) ───
function VideoPlayerModal({ clip, clips, onClose, brands, adminKey, onBrandChange, onNavigate }) {
  const videoRef = useRef(null);
  const [showNg, setShowNg] = useState(false);
  const [showBrands, setShowBrands] = useState(false);
  const [selectedReason, setSelectedReason] = useState(null);
  const [comment, setComment] = useState("");
  const [ngLoading, setNgLoading] = useState(false);
  const [assignLoading, setAssignLoading] = useState(false);
  const [muted, setMuted] = useState(false);
  const [actionDone, setActionDone] = useState(null); // "ng" | "brand" | null
  const [brandSearch, setBrandSearch] = useState("");
  const [showNewBrandForm, setShowNewBrandForm] = useState(false);
  const [newBrandName, setNewBrandName] = useState("");
  const [newBrandCompany, setNewBrandCompany] = useState("");
  const [creatingBrand, setCreatingBrand] = useState(false);

  if (!clip) return null;

  const currentIdx = clips.findIndex((c) => c.clip_id === clip.clip_id);
  const hasPrev = currentIdx > 0;
  const hasNext = currentIdx < clips.length - 1;
  const assignments = clip.brand_assignments || [];
  const tags = clip.tags || clip.sales_psychology_tags || [];

  const navigate = (dir) => {
    const nextIdx = currentIdx + dir;
    if (nextIdx >= 0 && nextIdx < clips.length) {
      setShowNg(false); setShowBrands(false); setSelectedReason(null);
      setComment(""); setActionDone(null); setBrandSearch("");
      onNavigate(clips[nextIdx]);
    }
  };

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (e.key === "ArrowLeft") { e.preventDefault(); navigate(-1); }
      if (e.key === "ArrowRight") { e.preventDefault(); navigate(1); }
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      if (e.key === " ") {
        e.preventDefault();
        if (videoRef.current) videoRef.current.paused ? videoRef.current.play() : videoRef.current.pause();
      }
      if (e.key === "n" || e.key === "N") { e.preventDefault(); setShowNg(true); setShowBrands(false); }
      if (e.key === "b" || e.key === "B") { e.preventDefault(); setShowBrands(true); setShowNg(false); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [currentIdx, clips]);

  const handleMarkNG = async (reasonKey) => {
    setNgLoading(true);
    try {
      const body = { reason: reasonKey };
      if (comment.trim()) body.comment = comment.trim();
      const res = await fetch(
        `${API_BASE}/api/v1/clip-db/mark-unusable?clip_id=${clip.clip_id}`,
        { method: "POST", headers: { "X-Admin-Key": adminKey, "Content-Type": "application/json" }, body: JSON.stringify(body) }
      );
      if (res.ok) {
        setActionDone("ng");
        setShowNg(false); setSelectedReason(null); setComment("");
        onBrandChange?.();
        // Auto-advance after 800ms
        setTimeout(() => { if (hasNext) navigate(1); }, 800);
      }
    } catch (e) { console.error("Mark NG failed:", e); }
    finally { setNgLoading(false); }
  };

  const handleAssignBrand = async (clientId) => {
    setAssignLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/clip-db/assign-brand?clip_id=${clip.clip_id}&client_id=${clientId}`,
        { method: "POST", headers: { "X-Admin-Key": adminKey } }
      );
      if (res.ok) {
        setActionDone("brand");
        setShowBrands(false); setBrandSearch("");
        onBrandChange?.();
        // Auto-advance after 800ms
        setTimeout(() => { if (hasNext) navigate(1); }, 800);
      }
    } catch (e) { console.error("Assign brand failed:", e); }
    finally { setAssignLoading(false); }
  };

  const handleCreateAndAssignBrand = async () => {
    if (!newBrandName.trim()) return;
    setCreatingBrand(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/clip-db/brands/create`, {
        method: "POST",
        headers: { "X-Admin-Key": adminKey, "Content-Type": "application/json" },
        body: JSON.stringify({ name: newBrandName.trim(), company_name: newBrandCompany.trim() || null }),
      });
      if (res.ok) {
        const data = await res.json();
        await fetch(
          `${API_BASE}/api/v1/clip-db/assign-brand?clip_id=${clip.clip_id}&client_id=${data.client_id}`,
          { method: "POST", headers: { "X-Admin-Key": adminKey } }
        );
        setActionDone("brand");
        setShowBrands(false); setShowNewBrandForm(false);
        setNewBrandName(""); setNewBrandCompany(""); setBrandSearch("");
        onBrandChange?.();
        setTimeout(() => { if (hasNext) navigate(1); }, 800);
      }
    } catch (e) { console.error("Create brand failed:", e); }
    finally { setCreatingBrand(false); }
  };

  const unassignedBrands = brands.filter(
    (b) => !assignments.some((a) => a.client_id === b.client_id)
  ).filter(
    (b) => {
      if (!brandSearch) return true;
      const q = brandSearch.toLowerCase();
      return (
        b.name.toLowerCase().includes(q) ||
        (b.company_name || "").toLowerCase().includes(q) ||
        (b.name_ja || "").toLowerCase().includes(q) ||
        (b.brand_keywords || "").toLowerCase().includes(q)
      );
    }
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md" onClick={onClose}>
      <div className="relative flex gap-0 max-w-5xl w-full mx-4" onClick={(e) => e.stopPropagation()} style={{ maxHeight: "90vh" }}>

        {/* Left nav arrow */}
        <button
          onClick={() => navigate(-1)}
          disabled={!hasPrev}
          className="absolute -left-14 top-1/2 -translate-y-1/2 z-10 p-2 rounded-full bg-white/10 hover:bg-white/20 text-white disabled:opacity-20 disabled:cursor-not-allowed transition-all"
        >
          <ChevronLeft className="w-7 h-7" />
        </button>

        {/* Right nav arrow */}
        <button
          onClick={() => navigate(1)}
          disabled={!hasNext}
          className="absolute -right-14 top-1/2 -translate-y-1/2 z-10 p-2 rounded-full bg-white/10 hover:bg-white/20 text-white disabled:opacity-20 disabled:cursor-not-allowed transition-all"
        >
          <ChevronRight className="w-7 h-7" />
        </button>

        {/* Close button */}
        <button onClick={onClose} className="absolute -top-12 right-0 text-white/70 hover:text-white transition-colors flex items-center gap-1 text-sm">
          <X className="w-5 h-5" /> ESC
        </button>

        {/* Video area */}
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="relative bg-black rounded-l-2xl overflow-hidden" style={{ maxHeight: "75vh" }}>
            <video
              ref={videoRef}
              src={clip.clip_url}
              controls
              autoPlay
              muted={muted}
              className="w-full h-full object-contain"
              style={{ maxHeight: "75vh" }}
            />
            {/* Counter badge */}
            <div className="absolute top-3 left-3 px-3 py-1 rounded-full bg-black/60 text-white text-xs font-medium">
              {currentIdx + 1} / {clips.length}
            </div>
            {/* Mute toggle */}
            <button
              onClick={() => setMuted(!muted)}
              className="absolute top-3 right-3 p-2 rounded-full bg-black/60 text-white hover:bg-black/80 transition-colors"
            >
              {muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
            </button>
            {/* Action done overlay */}
            {actionDone && (
              <div className={`absolute inset-0 flex items-center justify-center pointer-events-none ${
                actionDone === "ng" ? "bg-red-500/20" : "bg-green-500/20"
              }`}>
                <div className={`px-6 py-3 rounded-2xl text-lg font-bold ${
                  actionDone === "ng" ? "bg-red-500 text-white" : "bg-green-500 text-white"
                }`}>
                  {actionDone === "ng" ? "NG" : "✓ ブランド割当済"}
                </div>
              </div>
            )}
          </div>

          {/* Info bar under video */}
          <div className="bg-gray-900 rounded-bl-2xl px-4 py-3 space-y-2">
            <div className="flex items-center gap-3 text-white">
              <span className="text-sm font-bold">{clip.gmv > 0 ? formatGMV(clip.gmv) : "--"}</span>
              <span className="text-xs text-gray-400">CTA {clip.cta_score || "--"}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full ${clip.is_sold ? "bg-green-500/20 text-green-400" : "bg-gray-700 text-gray-400"}`}>
                {clip.is_sold ? "SOLD" : "未売"}
              </span>
              {clip.duration_sec && <span className="text-xs text-gray-500">{formatDuration(clip.duration_sec)}</span>}
              {clip.detected_language && clip.detected_language !== 'unknown' && (
                <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${
                  clip.detected_language === 'ja' ? 'bg-white/10 text-gray-300' :
                  clip.detected_language === 'zh-TW' ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30' :
                  clip.detected_language === 'zh-CN' ? 'bg-red-500/20 text-red-300 border border-red-500/30' :
                  clip.detected_language === 'en' ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30' :
                  clip.detected_language === 'ko' ? 'bg-sky-500/20 text-sky-300 border border-sky-500/30' :
                  clip.detected_language === 'th' ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' :
                  'bg-gray-500/20 text-gray-300'
                }`}>
                  {clip.detected_language === 'ja' ? '🇯🇵 日本語' :
                   clip.detected_language === 'zh-TW' ? '🇹🇼 繁中' :
                   clip.detected_language === 'zh-CN' ? '🇨🇳 簡中' :
                   clip.detected_language === 'en' ? '🇬🇧 EN' :
                   clip.detected_language === 'ko' ? '🇰🇷 韓国語' :
                   clip.detected_language === 'th' ? '🇹🇭 TH' :
                   clip.detected_language.toUpperCase()}
                </span>
              )}
              {assignments.length > 0 && (
                <div className="flex gap-1 ml-auto">
                  {assignments.map((a) => (
                    <span key={a.client_id} className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/30">
                      {a.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
            {tags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {tags.slice(0, 6).map((t, i) => (
                  <span key={i} className="text-[10px] px-2 py-0.5 rounded-full bg-white/10 text-gray-300">{getTagLabel(t)}</span>
                ))}
              </div>
            )}
            {clip.transcript_text && (
              <p className="text-gray-400 text-xs max-h-16 overflow-y-auto leading-relaxed">{clip.transcript_text}</p>
            )}
          </div>
        </div>

        {/* Right action panel */}
        <div className="w-64 bg-gray-900 rounded-r-2xl border-l border-gray-800 flex flex-col" style={{ maxHeight: "90vh" }}>
          <div className="px-4 py-3 border-b border-gray-800">
            <h3 className="text-white text-sm font-bold flex items-center gap-2">
              <CheckCircle className="w-4 h-4 text-green-400" />
              チェックモード
            </h3>
            <p className="text-gray-500 text-[10px] mt-1">←→ ナビ ・ Space 再生 ・ N=NG ・ B=ブランド</p>
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
            {/* NG or Usable action */}
            {!showNg && !showBrands && (
              <div className="space-y-2">
                <button
                  onClick={() => { setShowBrands(true); setShowNg(false); }}
                  className="w-full py-3 rounded-xl bg-green-600 hover:bg-green-500 text-white font-bold text-sm transition-colors flex items-center justify-center gap-2"
                >
                  <CheckCircle className="w-4 h-4" /> 使える（ブランド選択）
                </button>
                <button
                  onClick={() => { setShowNg(true); setShowBrands(false); }}
                  className="w-full py-3 rounded-xl bg-red-600/80 hover:bg-red-500 text-white font-bold text-sm transition-colors flex items-center justify-center gap-2"
                >
                  <Ban className="w-4 h-4" /> 使えない（NG）
                </button>
              </div>
            )}

            {/* NG reason selection */}
            {showNg && !selectedReason && (
              <div className="space-y-1">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-red-400 text-xs font-bold flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" /> NG理由を選択
                  </span>
                  <button onClick={() => setShowNg(false)} className="text-gray-500 hover:text-white text-xs">戻る</button>
                </div>
                {NG_REASONS.map((r) => (
                  <button
                    key={r.key}
                    onClick={() => setSelectedReason(r.key)}
                    className="w-full text-left px-3 py-2 rounded-lg text-xs text-gray-200 hover:bg-red-500/20 hover:text-red-300 transition-colors"
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            )}

            {/* NG comment input */}
            {showNg && selectedReason && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-red-400 text-xs font-bold">
                    {NG_REASONS.find((r) => r.key === selectedReason)?.label}
                  </span>
                  <button onClick={() => setSelectedReason(null)} className="text-gray-500 hover:text-white text-xs">戻る</button>
                </div>
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  placeholder="具体的な理由（AI学習に使われます）"
                  className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white text-xs placeholder-gray-500 resize-none focus:outline-none focus:border-red-500"
                  rows={3}
                />
                <button
                  onClick={() => handleMarkNG(selectedReason)}
                  disabled={ngLoading}
                  className="w-full py-2.5 rounded-xl bg-red-600 hover:bg-red-500 text-white font-bold text-sm transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  {ngLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Ban className="w-4 h-4" />}
                  NGにする
                </button>
              </div>
            )}

            {/* Brand selection */}
            {showBrands && (
              <div className="space-y-2">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-green-400 text-xs font-bold flex items-center gap-1">
                    <Building2 className="w-3 h-3" /> ブランド選択
                  </span>
                  <button onClick={() => setShowBrands(false)} className="text-gray-500 hover:text-white text-xs">戻る</button>
                </div>
                <input
                  type="text"
                  value={brandSearch}
                  onChange={(e) => setBrandSearch(e.target.value)}
                  placeholder="ブランド検索..."
                  className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white text-xs placeholder-gray-500 focus:outline-none focus:border-green-500"
                />
                <div className="space-y-1 max-h-60 overflow-y-auto">
                  {unassignedBrands.map((b, idx) => {
                    const color = getBrandColor(idx);
                    return (
                      <button
                        key={b.client_id}
                        onClick={() => handleAssignBrand(b.client_id)}
                        disabled={assignLoading}
                        className="w-full text-left px-3 py-2.5 rounded-lg text-xs transition-colors hover:bg-green-500/20 flex items-center gap-2 disabled:opacity-50"
                      >
                        <span className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold" style={{ background: color.bg, color: color.text }}>
                          {b.name.charAt(0).toUpperCase()}
                        </span>
                        <span className="text-gray-200 truncate">{b.name}{b.company_name ? ` (${b.company_name})` : ""}</span>
                        <span className="text-gray-600 text-[10px] ml-auto">{b.clip_count || 0}件</span>
                      </button>
                    );
                  })}
                  {unassignedBrands.length === 0 && (
                    <p className="text-gray-500 text-xs text-center py-4">
                      {brandSearch ? "見つかりません" : "全ブランド割当済み"}
                    </p>
                  )}
                </div>
                {/* New brand creation */}
                {showNewBrandForm ? (
                  <div className="mt-2 p-3 rounded-lg bg-gray-800 border border-gray-700 space-y-2">
                    <input
                      type="text"
                      placeholder="ブランド名（英語）"
                      value={newBrandName}
                      onChange={(e) => setNewBrandName(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-600 text-white text-xs placeholder-gray-500 focus:outline-none focus:border-green-500"
                      autoFocus
                    />
                    <input
                      type="text"
                      placeholder="会社名（任意）"
                      value={newBrandCompany}
                      onChange={(e) => setNewBrandCompany(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-600 text-white text-xs placeholder-gray-500 focus:outline-none focus:border-green-500"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={handleCreateAndAssignBrand}
                        disabled={!newBrandName.trim() || creatingBrand}
                        className="flex-1 py-2 rounded-lg bg-green-600 hover:bg-green-500 text-white text-xs font-bold disabled:opacity-50 flex items-center justify-center gap-1 transition-colors"
                      >
                        {creatingBrand ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                        作成&割当
                      </button>
                      <button
                        onClick={() => { setShowNewBrandForm(false); setNewBrandName(""); setNewBrandCompany(""); }}
                        className="px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs transition-colors"
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => setShowNewBrandForm(true)}
                    className="mt-2 w-full py-2 rounded-lg border border-dashed border-gray-600 text-gray-400 hover:text-green-400 hover:border-green-500 text-xs flex items-center justify-center gap-1 transition-colors"
                  >
                    <Plus className="w-3 h-3" /> 新規ブランド追加
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Bottom nav buttons (mobile-friendly) */}
          <div className="px-4 py-3 border-t border-gray-800 flex items-center justify-between">
            <button
              onClick={() => navigate(-1)}
              disabled={!hasPrev}
              className="flex items-center gap-1 px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-white text-xs disabled:opacity-30 transition-colors"
            >
              <SkipBack className="w-3 h-3" /> 前
            </button>
            <span className="text-gray-500 text-xs">{currentIdx + 1}/{clips.length}</span>
            <button
              onClick={() => navigate(1)}
              disabled={!hasNext}
              className="flex items-center gap-1 px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-white text-xs disabled:opacity-30 transition-colors"
            >
              次 <SkipForward className="w-3 h-3" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════
// ─── Main AdminClipDB Component ───
// ═══════════════════════════════════════════════
export default function AdminClipDB({ adminKey }) {
  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMode, setSearchMode] = useState("structured");
  const [selectedTag, setSelectedTag] = useState("");
  const [selectedProduct, setSelectedProduct] = useState("");
  const [selectedLiver, setSelectedLiver] = useState("");
  const [selectedBrand, setSelectedBrand] = useState("");
  const [soldFilter, setSoldFilter] = useState(null);
  const [ratingFilter, setRatingFilter] = useState("");
  const [unusableFilter, setUnusableFilter] = useState(null);
  const [noBrandFilter, setNoBrandFilter] = useState(null);
  const [hasSubtitleFilter, setHasSubtitleFilter] = useState(null);
  const [hasTrimFilter, setHasTrimFilter] = useState(null);
  const [languageFilter, setLanguageFilter] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState("desc");
  const [page, setPage] = useState(1);
  const [showFilters, setShowFilters] = useState(false);

  // Data state
  const [clips, setClips] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState(null);
  const [allTags, setAllTags] = useState([]);
  const [brands, setBrands] = useState([]);
  const [playerClip, setPlayerClip] = useState(null);
  const [enriching, setEnriching] = useState(false);
  const [enrichStatus, setEnrichStatus] = useState(null);
  const [showStats, setShowStats] = useState(true);

  // Analytics state
  const [showAnalytics, setShowAnalytics] = useState(false);
  const [analyticsDays, setAnalyticsDays] = useState(30);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsData, setAnalyticsData] = useState(null); // {overview, brandComparison, topClips, funnel, daily, mlInsights, heatmap}

  const pageSize = 20;
  const totalPages = Math.ceil(total / pageSize);
  const enrichTriggered = useRef(false);

  // Auto enrich-all on first mount, then load data
  useEffect(() => {
    if (enrichTriggered.current) return;
    enrichTriggered.current = true;
    autoEnrichAndLoad();
  }, []);

  // Load clips when search params change
  useEffect(() => {
    if (searchMode === "structured" && enrichTriggered.current) {
      loadClips();
    }
  }, [page, sortBy, sortOrder, selectedTag, soldFilter, ratingFilter, selectedBrand, unusableFilter, noBrandFilter, hasSubtitleFilter, hasTrimFilter, languageFilter]);

  async function autoEnrichAndLoad() {
    // 1. Auto enrich (non-blocking for already-enriched clips)
    setEnriching(true);
    setEnrichStatus("メタデータを自動更新中...");
    try {
      const res = await fetch(`${API_BASE}/api/v1/clip-db/enrich-all?force=false`, {
        method: "POST",
        headers: { "X-Admin-Key": adminKey },
      });
      const data = await res.json();
      setEnrichStatus(`更新完了: ${data.enriched}/${data.total} クリップ`);
    } catch (e) {
      console.warn("[ClipDB] Auto-enrich failed:", e);
      setEnrichStatus("自動更新スキップ（エラー）");
    } finally {
      setEnriching(false);
    }

    // 2. Load stats, tags, brands, clips
    loadStats();
    loadTags();
    loadBrands();
    loadClips();
  }

  async function loadStats() {
    try {
      const data = await clipDbFetch("/stats", {}, adminKey);
      setStats(data);
    } catch (e) {
      console.warn("[ClipDB] Failed to load stats:", e);
    }
  }

  async function loadTags() {
    try {
      const data = await clipDbFetch("/tags", {}, adminKey);
      setAllTags(data.tags || []);
    } catch (e) {
      console.warn("[ClipDB] Failed to load tags:", e);
    }
  }

  async function loadBrands() {
    try {
      const data = await clipDbFetch("/brands", {}, adminKey);
      setBrands(data.brands || []);
    } catch (e) {
      console.warn("[ClipDB] Failed to load brands:", e);
    }
  }

  async function loadClips() {
    setLoading(true);
    try {
      const params = {
        page,
        page_size: pageSize,
        sort_by: sortBy,
        sort_order: sortOrder,
      };
      if (searchQuery) params.q = searchQuery;
      if (selectedTag) params.tag = selectedTag;
      if (selectedProduct) params.product = selectedProduct;
      if (selectedLiver) params.liver = selectedLiver;
      if (selectedBrand) params.brand = selectedBrand;
      if (soldFilter !== null) params.is_sold = soldFilter;
      if (ratingFilter) params.rating = ratingFilter;
      if (unusableFilter !== null) params.is_unusable = unusableFilter;
      if (noBrandFilter !== null) params.no_brand = noBrandFilter;
      if (hasSubtitleFilter !== null) params.has_subtitle = hasSubtitleFilter;
      if (hasTrimFilter !== null) params.has_trim = hasTrimFilter;
      if (languageFilter) params.language = languageFilter;

      const data = await clipDbFetch("/search", params, adminKey);
      setClips(data.clips || []);
      setTotal(data.total || 0);
    } catch (e) {
      console.error("[ClipDB] Search failed:", e);
      setClips([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }

  async function doSemanticSearch() {
    if (!searchQuery.trim()) return;
    setLoading(true);
    try {
      const data = await clipDbFetch("/semantic-search", { q: searchQuery, limit: 20 }, adminKey);
      setClips(data.clips || []);
      setTotal(data.total || 0);
    } catch (e) {
      console.error("[ClipDB] Semantic search failed:", e);
      setClips([]);
    } finally {
      setLoading(false);
    }
  }

  function handleSearch() {
    setPage(1);
    if (searchMode === "semantic") {
      doSemanticSearch();
    } else {
      loadClips();
    }
  }

  async function handleForceEnrich() {
    if (enriching) return;
    setEnriching(true);
    setEnrichStatus("全クリップを強制更新中...");
    try {
      const res = await fetch(`${API_BASE}/api/v1/clip-db/enrich-all?force=true`, {
        method: "POST",
        headers: { "X-Admin-Key": adminKey },
      });
      const data = await res.json();
      setEnrichStatus(`完了: ${data.enriched}/${data.total} クリップ更新`);
      loadClips();
      loadStats();
    } catch (e) {
      setEnrichStatus("更新失敗: " + e.message);
    } finally {
      setEnriching(false);
    }
  }

  async function loadAnalytics(days) {
    setAnalyticsLoading(true);
    try {
      const d = days || analyticsDays;
      const headers = { "X-Admin-Key": adminKey };
      const [ov, bc, tc, fn, dy, ml, hm] = await Promise.all([
        fetch(`${API_BASE}/api/v1/admin/analytics/overview?days=${d}`, { headers }).then(r => r.json()),
        fetch(`${API_BASE}/api/v1/admin/analytics/brand-comparison?days=${d}`, { headers }).then(r => r.json()),
        fetch(`${API_BASE}/api/v1/admin/analytics/top-clips?days=${d}&limit=20`, { headers }).then(r => r.json()),
        fetch(`${API_BASE}/api/v1/admin/analytics/funnel?days=${d}`, { headers }).then(r => r.json()),
        fetch(`${API_BASE}/api/v1/admin/analytics/daily?days=${d}`, { headers }).then(r => r.json()),
        fetch(`${API_BASE}/api/v1/admin/analytics/ml-insights?days=${d}`, { headers }).then(r => r.json()),
        fetch(`${API_BASE}/api/v1/admin/analytics/hourly-heatmap?days=${d}`, { headers }).then(r => r.json()),
      ]);
      setAnalyticsData({ overview: ov, brandComparison: bc, topClips: tc, funnel: fn, daily: dy, mlInsights: ml, heatmap: hm });
    } catch (e) {
      console.error("[Analytics] Load failed:", e);
    } finally {
      setAnalyticsLoading(false);
    }
  }

  const handleBrandChange = useCallback(() => {
    // Reload clips and brands after brand assignment change
    loadClips();
    loadBrands();
  }, [searchQuery, selectedTag, selectedProduct, selectedLiver, selectedBrand, soldFilter, ratingFilter, unusableFilter, noBrandFilter, hasSubtitleFilter, hasTrimFilter, page, sortBy, sortOrder]);

  return (
    <div>
      {/* Header with search */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
              <Database className="w-5 h-5 text-purple-600" />
              クリップDB
              <span className="text-xs font-normal text-gray-400 ml-1">売れる瞬間を検索</span>
            </h2>
            {enrichStatus && (
              <p className={`text-xs mt-1 ${enriching ? "text-orange-500" : "text-green-600"}`}>
                {enriching && <Loader2 className="w-3 h-3 inline animate-spin mr-1" />}
                {enrichStatus}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowStats(!showStats)}
              className={`p-2 rounded-lg transition ${showStats ? "bg-purple-100 text-purple-700" : "hover:bg-gray-100 text-gray-500"}`}
              title="統計表示"
            >
              <BarChart3 className="w-4 h-4" />
            </button>
            <button
              onClick={() => { setShowAnalytics(!showAnalytics); if (!showAnalytics && !analyticsData) loadAnalytics(); }}
              className={`p-2 rounded-lg transition ${showAnalytics ? "bg-blue-100 text-blue-700" : "hover:bg-gray-100 text-gray-500"}`}
              title="アナリティクス"
            >
              <Activity className="w-4 h-4" />
            </button>
            <button
              onClick={handleForceEnrich}
              disabled={enriching}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-600 text-white text-xs font-medium hover:bg-purple-700 disabled:opacity-50 transition"
              title="全クリップのメタデータを強制再更新"
            >
              {enriching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              強制DB更新
            </button>
          </div>
        </div>

        {/* Search bar */}
        <div className="flex gap-2">
          <div className="flex rounded-lg border border-gray-300 overflow-hidden shrink-0">
            <button
              onClick={() => setSearchMode("structured")}
              className={`px-3 py-2 text-xs font-medium transition ${
                searchMode === "structured" ? "bg-purple-600 text-white" : "bg-white text-gray-600 hover:bg-gray-50"
              }`}
            >
              <Filter className="w-3.5 h-3.5 inline mr-1" />
              フィルタ
            </button>
            <button
              onClick={() => setSearchMode("semantic")}
              className={`px-3 py-2 text-xs font-medium transition ${
                searchMode === "semantic" ? "bg-purple-600 text-white" : "bg-white text-gray-600 hover:bg-gray-50"
              }`}
            >
              <Sparkles className="w-3.5 h-3.5 inline mr-1" />
              AI検索
            </button>
          </div>

          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder={
                searchMode === "semantic"
                  ? "例: 「シャンプーの効果を実演しながら説明」"
                  : "テキスト検索..."
              }
              className="w-full pl-9 pr-4 py-2 rounded-lg border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
            />
          </div>

          <button
            onClick={handleSearch}
            className="px-4 py-2 rounded-lg bg-gray-900 text-white text-sm font-medium hover:bg-gray-800 transition shrink-0"
          >
            検索
          </button>

          {searchMode === "structured" && (
            <button
              onClick={() => setShowFilters(!showFilters)}
              className={`p-2 rounded-lg border transition shrink-0 ${
                showFilters ? "border-purple-300 bg-purple-50 text-purple-700" : "border-gray-300 text-gray-500 hover:bg-gray-50"
              }`}
            >
              <Filter className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Filter row */}
        {showFilters && searchMode === "structured" && (
          <div className="flex flex-wrap gap-2 mt-3">
            {/* Brand filter */}
            <select
              value={selectedBrand}
              onChange={(e) => { setSelectedBrand(e.target.value); setPage(1); }}
              className="px-3 py-1.5 rounded-lg border border-blue-300 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 font-medium"
            >
              <option value="">ブランド: すべて</option>
              {brands.map((b) => (
                <option key={b.client_id} value={b.client_id}>
                  {b.name} ({b.clip_count})
                </option>
              ))}
            </select>

            <select
              value={selectedTag}
              onChange={(e) => { setSelectedTag(e.target.value); setPage(1); }}
              className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              <option value="">タグ: すべて</option>
              {allTags.map((t) => (
                <option key={t.tag} value={t.tag}>{getTagLabel(t.tag)} ({t.count})</option>
              ))}
            </select>

            <select
              value={soldFilter === null ? "" : soldFilter.toString()}
              onChange={(e) => {
                const v = e.target.value;
                setSoldFilter(v === "" ? null : v === "true");
                setPage(1);
              }}
              className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              <option value="">売上: すべて</option>
              <option value="true">売れた</option>
              <option value="false">売れてない</option>
            </select>

            <select
              value={ratingFilter}
              onChange={(e) => { setRatingFilter(e.target.value); setPage(1); }}
              className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              <option value="">評価: すべて</option>
              <option value="good">Good</option>
              <option value="bad">Bad</option>
            </select>

            <select
              value={unusableFilter === null ? "" : unusableFilter.toString()}
              onChange={(e) => {
                const v = e.target.value;
                setUnusableFilter(v === "" ? null : v === "true");
                setPage(1);
              }}
              className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              <option value="">NG: すべて</option>
              <option value="true">NGのみ</option>
              <option value="false">使えるのみ</option>
            </select>

            <select
              value={sortBy}
              onChange={(e) => { setSortBy(e.target.value); setPage(1); }}
              className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              <option value="created_at">作成日順</option>
              <option value="gmv">GMV順</option>
              <option value="cta_score">CTAスコア順</option>
              <option value="importance_score">重要度順</option>
              <option value="duration_sec">長さ順</option>
            </select>

            <button
              onClick={() => setSortOrder(sortOrder === "desc" ? "asc" : "desc")}
              className="p-1.5 rounded-lg border border-gray-300 hover:bg-gray-50 text-xs text-gray-600"
            >
              <ArrowUpDown className="w-3.5 h-3.5" />
              {sortOrder === "desc" ? "↓" : "↑"}
            </button>

            <input
              type="text"
              value={selectedProduct}
              onChange={(e) => setSelectedProduct(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder="商品名..."
              className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs w-32 focus:outline-none focus:ring-2 focus:ring-purple-500"
            />

            <input
              type="text"
              value={selectedLiver}
              onChange={(e) => setSelectedLiver(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder="ライバー名..."
              className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs w-32 focus:outline-none focus:ring-2 focus:ring-purple-500"
            />

            {(selectedTag || selectedProduct || selectedLiver || selectedBrand || soldFilter !== null || ratingFilter || unusableFilter !== null || noBrandFilter !== null || hasSubtitleFilter !== null || hasTrimFilter !== null) && (
              <button
                onClick={() => {
                  setSelectedTag(""); setSelectedProduct(""); setSelectedLiver("");
                  setSelectedBrand(""); setSoldFilter(null); setRatingFilter(""); setUnusableFilter(null);
                  setNoBrandFilter(null); setHasSubtitleFilter(null); setHasTrimFilter(null); setPage(1);
                }}
                className="px-2 py-1.5 rounded-lg text-xs text-red-500 hover:bg-red-50 border border-red-200"
              >
                <X className="w-3 h-3 inline mr-0.5" />
                クリア
              </button>
            )}
          </div>
        )}
      </div>

      {/* ═══ Cross-Brand Analytics Dashboard ═══ */}
      {showAnalytics && (
        <div className="mb-8 space-y-6">
          {/* Period selector + refresh */}
          <div className="flex items-center justify-between">
            <h3 className="text-base font-bold text-gray-900 flex items-center gap-2">
              <Activity className="w-5 h-5 text-blue-600" />
              全ブランド横断アナリティクス
            </h3>
            <div className="flex items-center gap-2">
              {[7, 14, 30, 90].map(d => (
                <button key={d} onClick={() => { setAnalyticsDays(d); loadAnalytics(d); }}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition ${
                    analyticsDays === d ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                  }`}>{d}日</button>
              ))}
              <button onClick={() => loadAnalytics()} disabled={analyticsLoading}
                className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-500">
                {analyticsLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {analyticsLoading && !analyticsData && (
            <div className="text-center py-12 text-gray-400">
              <Loader2 className="w-8 h-8 animate-spin mx-auto mb-2" />
              <p className="text-sm">アナリティクスを読み込み中...</p>
            </div>
          )}

          {analyticsData && (
            <>
              {/* KPI Cards */}
              {analyticsData.overview?.kpi && (() => {
                const k = analyticsData.overview.kpi;
                const cards = [
                  { label: "動画再生", value: k.plays, delta: k.plays_delta, icon: Play, color: "blue" },
                  { label: "ユニーク視聴者", value: k.unique_viewers, icon: Users, color: "indigo" },
                  { label: "完了率", value: `${k.completion_rate}%`, delta: k.completion_rate_delta, icon: Eye, color: "green" },
                  { label: "CTR", value: `${k.ctr}%`, delta: k.ctr_delta, icon: Target, color: "orange" },
                  { label: "CVR", value: `${k.cvr}%`, delta: k.cvr_delta, icon: Zap, color: "purple" },
                  { label: "購入クリック", value: k.purchases, icon: ShoppingBag, color: "pink" },
                  { label: "コンバージョン", value: k.conversions, icon: CheckCircle, color: "emerald" },
                  { label: "リプレイ", value: k.replays, icon: RefreshCw, color: "cyan" },
                ];
                return (
                  <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
                    {cards.map((c, i) => {
                      const Icon = c.icon;
                      return (
                        <div key={i} className="bg-white rounded-xl border border-gray-200 p-3 text-center">
                          <Icon className={`w-4 h-4 mx-auto mb-1 text-${c.color}-500`} />
                          <div className="text-lg font-bold text-gray-900">{c.value}</div>
                          <div className="text-[10px] text-gray-500">{c.label}</div>
                          {c.delta != null && (
                            <div className={`text-[10px] font-medium mt-0.5 ${c.delta >= 0 ? "text-green-600" : "text-red-500"}`}>
                              {c.delta >= 0 ? "+" : ""}{c.delta}%
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })()}

              {/* Funnel Analysis */}
              {analyticsData.funnel?.stages?.length > 0 && (
                <div className="bg-white rounded-xl border border-gray-200 p-4">
                  <h4 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
                    <TrendingUp className="w-4 h-4 text-blue-500" />
                    ファネル分析
                  </h4>
                  <div className="space-y-2">
                    {analyticsData.funnel.stages.map((s, i) => (
                      <div key={i} className="flex items-center gap-3">
                        <span className="text-xs text-gray-600 w-28 shrink-0">{s.name}</span>
                        <div className="flex-1 h-6 bg-gray-100 rounded-full overflow-hidden">
                          <div className="h-full rounded-full transition-all bg-gradient-to-r from-blue-500 to-blue-400"
                            style={{ width: `${Math.max(s.rate, 1)}%` }} />
                        </div>
                        <span className="text-xs font-bold text-gray-700 w-12 text-right">{s.rate}%</span>
                        <span className="text-[10px] text-gray-400 w-16 text-right">{s.count.toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Brand Comparison + Top Clips (side by side) */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {/* Brand Comparison */}
                {analyticsData.brandComparison?.brands?.length > 0 && (
                  <div className="bg-white rounded-xl border border-gray-200 p-4">
                    <h4 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
                      <Building2 className="w-4 h-4 text-purple-500" />
                      ブランド別パフォーマンス
                    </h4>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-gray-100">
                            <th className="text-left py-2 px-1 text-gray-500 font-medium">ブランド</th>
                            <th className="text-right py-2 px-1 text-gray-500 font-medium">再生</th>
                            <th className="text-right py-2 px-1 text-gray-500 font-medium">完了率</th>
                            <th className="text-right py-2 px-1 text-gray-500 font-medium">CTR</th>
                            <th className="text-right py-2 px-1 text-gray-500 font-medium">CVR</th>
                            <th className="text-right py-2 px-1 text-gray-500 font-medium">CV</th>
                          </tr>
                        </thead>
                        <tbody>
                          {analyticsData.brandComparison.brands.map((b, i) => (
                            <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                              <td className="py-1.5 px-1 font-medium text-gray-800 truncate max-w-[120px]">{b.brand_name}</td>
                              <td className="py-1.5 px-1 text-right text-gray-600">{b.plays.toLocaleString()}</td>
                              <td className="py-1.5 px-1 text-right text-green-600 font-medium">{b.completion_rate}%</td>
                              <td className="py-1.5 px-1 text-right text-orange-600 font-medium">{b.ctr}%</td>
                              <td className="py-1.5 px-1 text-right text-purple-600 font-medium">{b.cvr}%</td>
                              <td className="py-1.5 px-1 text-right text-blue-600 font-bold">{b.conversions}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Top Clips */}
                {analyticsData.topClips?.clips?.length > 0 && (
                  <div className="bg-white rounded-xl border border-gray-200 p-4">
                    <h4 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
                      <Star className="w-4 h-4 text-yellow-500" />
                      トップクリップ (エンゲージメント順)
                    </h4>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-gray-100">
                            <th className="text-left py-2 px-1 text-gray-500 font-medium">#</th>
                            <th className="text-left py-2 px-1 text-gray-500 font-medium">商品</th>
                            <th className="text-left py-2 px-1 text-gray-500 font-medium">ブランド</th>
                            <th className="text-right py-2 px-1 text-gray-500 font-medium">再生</th>
                            <th className="text-right py-2 px-1 text-gray-500 font-medium">完了率</th>
                            <th className="text-right py-2 px-1 text-gray-500 font-medium">CTR</th>
                            <th className="text-right py-2 px-1 text-gray-500 font-medium">スコア</th>
                          </tr>
                        </thead>
                        <tbody>
                          {analyticsData.topClips.clips.slice(0, 10).map((c, i) => (
                            <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                              <td className="py-1.5 px-1 text-gray-400 font-bold">{i + 1}</td>
                              <td className="py-1.5 px-1 text-gray-800 truncate max-w-[140px]">{c.product_name || "—"}</td>
                              <td className="py-1.5 px-1 text-gray-500 truncate max-w-[80px]">{c.brand_name}</td>
                              <td className="py-1.5 px-1 text-right text-gray-600">{c.plays.toLocaleString()}</td>
                              <td className="py-1.5 px-1 text-right text-green-600">{c.completion_rate}%</td>
                              <td className="py-1.5 px-1 text-right text-orange-600">{c.ctr}%</td>
                              <td className="py-1.5 px-1 text-right">
                                <span className="bg-yellow-100 text-yellow-700 px-1.5 py-0.5 rounded font-bold">{c.engagement_score}</span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>

              {/* ML Insights */}
              {analyticsData.mlInsights && (
                <div className="space-y-4">
                  <h4 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
                    <Brain className="w-4 h-4 text-purple-500" />
                    ML分析インサイト
                  </h4>

                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {/* Tag Effectiveness */}
                    {analyticsData.mlInsights.tag_effectiveness?.length > 0 && (
                      <div className="bg-white rounded-xl border border-gray-200 p-4">
                        <h5 className="text-xs font-bold text-gray-600 mb-2 flex items-center gap-1">
                          <Tag className="w-3.5 h-3.5 text-purple-500" />
                          タグ別効果分析
                        </h5>
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-gray-100">
                              <th className="text-left py-1.5 text-gray-500">タグ</th>
                              <th className="text-right py-1.5 text-gray-500">クリップ数</th>
                              <th className="text-right py-1.5 text-gray-500">完了率</th>
                              <th className="text-right py-1.5 text-gray-500">CTR</th>
                              <th className="text-right py-1.5 text-gray-500">CVR</th>
                            </tr>
                          </thead>
                          <tbody>
                            {analyticsData.mlInsights.tag_effectiveness.map((t, i) => (
                              <tr key={i} className="border-b border-gray-50">
                                <td className="py-1 text-gray-700 font-medium">{t.tag}</td>
                                <td className="py-1 text-right text-gray-500">{t.clip_count}</td>
                                <td className="py-1 text-right text-green-600">{t.completion_rate}%</td>
                                <td className="py-1 text-right text-orange-600 font-bold">{t.ctr}%</td>
                                <td className="py-1 text-right text-purple-600">{t.cvr}%</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {/* Duration Analysis */}
                    {analyticsData.mlInsights.duration_analysis?.length > 0 && (
                      <div className="bg-white rounded-xl border border-gray-200 p-4">
                        <h5 className="text-xs font-bold text-gray-600 mb-2 flex items-center gap-1">
                          <Clock className="w-3.5 h-3.5 text-blue-500" />
                          動画長さ別パフォーマンス
                        </h5>
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-gray-100">
                              <th className="text-left py-1.5 text-gray-500">長さ</th>
                              <th className="text-right py-1.5 text-gray-500">クリップ数</th>
                              <th className="text-right py-1.5 text-gray-500">完了率</th>
                              <th className="text-right py-1.5 text-gray-500">CTR</th>
                              <th className="text-right py-1.5 text-gray-500">CVR</th>
                            </tr>
                          </thead>
                          <tbody>
                            {analyticsData.mlInsights.duration_analysis.map((d, i) => (
                              <tr key={i} className="border-b border-gray-50">
                                <td className="py-1 text-gray-700 font-medium">{d.bucket}</td>
                                <td className="py-1 text-right text-gray-500">{d.clip_count}</td>
                                <td className="py-1 text-right text-green-600">{d.completion_rate}%</td>
                                <td className="py-1 text-right text-orange-600 font-bold">{d.ctr}%</td>
                                <td className="py-1 text-right text-purple-600">{d.cvr}%</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {/* Sold vs Unsold */}
                    {analyticsData.mlInsights.sold_vs_unsold?.length > 0 && (
                      <div className="bg-white rounded-xl border border-gray-200 p-4">
                        <h5 className="text-xs font-bold text-gray-600 mb-2 flex items-center gap-1">
                          <Sparkles className="w-3.5 h-3.5 text-green-500" />
                          売れた vs 売れなかった パターン比較
                        </h5>
                        <div className="grid grid-cols-2 gap-4">
                          {analyticsData.mlInsights.sold_vs_unsold.map((s, i) => (
                            <div key={i} className={`rounded-lg p-3 ${s.is_sold ? "bg-green-50 border border-green-200" : "bg-gray-50 border border-gray-200"}`}>
                              <div className="text-xs font-bold mb-2 ${s.is_sold ? 'text-green-700' : 'text-gray-600'}">
                                {s.is_sold ? "売れた動画" : "未売動画"}
                              </div>
                              <div className="space-y-1 text-xs">
                                <div className="flex justify-between"><span className="text-gray-500">クリップ数</span><span className="font-bold">{s.clip_count}</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">平均長さ</span><span className="font-bold">{s.avg_duration}s</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">平均タグ数</span><span className="font-bold">{s.avg_tag_count}</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">完了率</span><span className="font-bold text-green-600">{s.completion_rate}%</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">CTR</span><span className="font-bold text-orange-600">{s.ctr}%</span></div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Winning Tag Combos */}
                    {analyticsData.mlInsights.winning_combos?.length > 0 && (
                      <div className="bg-white rounded-xl border border-gray-200 p-4">
                        <h5 className="text-xs font-bold text-gray-600 mb-2 flex items-center gap-1">
                          <Zap className="w-3.5 h-3.5 text-yellow-500" />
                          勝ちパターン（タグ組み合わせ）
                        </h5>
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-gray-100">
                              <th className="text-left py-1.5 text-gray-500">タグ組み合わせ</th>
                              <th className="text-right py-1.5 text-gray-500">クリップ</th>
                              <th className="text-right py-1.5 text-gray-500">CTR</th>
                              <th className="text-right py-1.5 text-gray-500">CVR</th>
                            </tr>
                          </thead>
                          <tbody>
                            {analyticsData.mlInsights.winning_combos.map((w, i) => (
                              <tr key={i} className="border-b border-gray-50">
                                <td className="py-1 text-gray-700">{w.tags}</td>
                                <td className="py-1 text-right text-gray-500">{w.clip_count}</td>
                                <td className="py-1 text-right text-orange-600 font-bold">{w.ctr}%</td>
                                <td className="py-1 text-right text-purple-600 font-bold">{w.cvr}%</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Daily Trend (simple text-based) */}
              {analyticsData.daily?.daily?.length > 0 && (
                <div className="bg-white rounded-xl border border-gray-200 p-4">
                  <h4 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
                    <TrendingUp className="w-4 h-4 text-green-500" />
                    日別トレンド
                  </h4>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-gray-100">
                          <th className="text-left py-2 px-1 text-gray-500">日付</th>
                          <th className="text-right py-2 px-1 text-gray-500">再生</th>
                          <th className="text-right py-2 px-1 text-gray-500">視聴者</th>
                          <th className="text-right py-2 px-1 text-gray-500">完了</th>
                          <th className="text-right py-2 px-1 text-gray-500">クリック</th>
                          <th className="text-right py-2 px-1 text-gray-500">CV</th>
                          <th className="py-2 px-1 text-gray-500">再生バー</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(() => {
                          const maxPlays = Math.max(...analyticsData.daily.daily.map(d => d.plays), 1);
                          return analyticsData.daily.daily.slice(-14).map((d, i) => (
                            <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                              <td className="py-1 px-1 text-gray-600">{d.date}</td>
                              <td className="py-1 px-1 text-right font-medium text-gray-800">{d.plays}</td>
                              <td className="py-1 px-1 text-right text-indigo-600">{d.unique_viewers}</td>
                              <td className="py-1 px-1 text-right text-green-600">{d.completions}</td>
                              <td className="py-1 px-1 text-right text-orange-600">{d.cta_clicks}</td>
                              <td className="py-1 px-1 text-right text-purple-600 font-bold">{d.conversions}</td>
                              <td className="py-1 px-1 w-32">
                                <div className="h-3 bg-gray-100 rounded-full overflow-hidden">
                                  <div className="h-full bg-blue-400 rounded-full" style={{ width: `${(d.plays / maxPlays) * 100}%` }} />
                                </div>
                              </td>
                            </tr>
                          ));
                        })()}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Platform Summary */}
              {analyticsData.overview && (
                <div className="bg-gradient-to-r from-blue-50 to-purple-50 rounded-xl border border-blue-200 p-4">
                  <h4 className="text-sm font-bold text-gray-700 mb-2 flex items-center gap-1.5">
                    <Database className="w-4 h-4 text-blue-500" />
                    プラットフォーム概要
                  </h4>
                  <div className="flex gap-6 text-xs">
                    <div><span className="text-gray-500">アクティブクリップ: </span><span className="font-bold text-blue-700">{analyticsData.overview.active_clips}</span></div>
                    <div><span className="text-gray-500">アクティブブランド: </span><span className="font-bold text-purple-700">{analyticsData.overview.active_brands}</span></div>
                    <div><span className="text-gray-500">期間: </span><span className="font-bold text-gray-700">{analyticsDays}日間</span></div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Stats */}
      {showStats && stats && <StatsOverview stats={stats} />}
      {showStats && stats && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <TopTagsChart tags={stats.top_tags} />
          <TopProductsChart products={stats.top_products} />
        </div>
      )}

      {/* Status Filter Chips */}
      {stats && (
        <div className="mb-4">
          <h3 className="text-sm font-bold text-gray-700 mb-2 flex items-center gap-1.5">
            <Filter className="w-4 h-4 text-orange-500" />
            ステータスフィルター
          </h3>
          <div className="flex gap-2 flex-wrap">
            {/* NG clips */}
            <button
              onClick={() => {
                if (unusableFilter === true) { setUnusableFilter(null); }
                else { setUnusableFilter(true); }
                setPage(1);
              }}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                unusableFilter === true
                  ? "bg-red-100 border-red-400 text-red-700 shadow-sm"
                  : "bg-white border-gray-200 text-gray-600 hover:border-red-300 hover:bg-red-50"
              }`}
            >
              <Ban className="w-3 h-3 inline mr-1" />
              NG {stats.ng_clips > 0 && <span className="ml-0.5 font-bold">{stats.ng_clips}</span>}
            </button>

            {/* No brand */}
            <button
              onClick={() => {
                if (noBrandFilter === true) { setNoBrandFilter(null); }
                else { setNoBrandFilter(true); }
                setPage(1);
              }}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                noBrandFilter === true
                  ? "bg-yellow-100 border-yellow-400 text-yellow-700 shadow-sm"
                  : "bg-white border-gray-200 text-gray-600 hover:border-yellow-300 hover:bg-yellow-50"
              }`}
            >
              <AlertTriangle className="w-3 h-3 inline mr-1" />
              ブランド未割当 {stats.no_brand_clips > 0 && <span className="ml-0.5 font-bold">{stats.no_brand_clips}</span>}
            </button>

            {/* Has subtitle */}
            <button
              onClick={() => {
                if (hasSubtitleFilter === true) { setHasSubtitleFilter(null); }
                else { setHasSubtitleFilter(true); }
                setPage(1);
              }}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                hasSubtitleFilter === true
                  ? "bg-purple-100 border-purple-400 text-purple-700 shadow-sm"
                  : "bg-white border-gray-200 text-gray-600 hover:border-purple-300 hover:bg-purple-50"
              }`}
            >
              <Subtitles className="w-3 h-3 inline mr-1" />
              字幕済 {stats.subtitle_clips > 0 && <span className="ml-0.5 font-bold">{stats.subtitle_clips}</span>}
            </button>

            {/* Has trim */}
            <button
              onClick={() => {
                if (hasTrimFilter === true) { setHasTrimFilter(null); }
                else { setHasTrimFilter(true); }
                setPage(1);
              }}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                hasTrimFilter === true
                  ? "bg-blue-100 border-blue-400 text-blue-700 shadow-sm"
                  : "bg-white border-gray-200 text-gray-600 hover:border-blue-300 hover:bg-blue-50"
              }`}
            >
              <Scissors className="w-3 h-3 inline mr-1" />
              カット済 {stats.trimmed_clips > 0 && <span className="ml-0.5 font-bold">{stats.trimmed_clips}</span>}
            </button>

            {/* NG by reason breakdown */}
            {stats.ng_by_reason && stats.ng_by_reason.length > 0 && unusableFilter === true && (
              <div className="flex gap-1.5 ml-2 pl-2 border-l border-gray-200">
                {stats.ng_by_reason.map((r) => (
                  <span key={r.reason} className="px-2 py-1 rounded-full text-[10px] bg-red-50 text-red-600 border border-red-200">
                    {r.reason} ({r.count})
                  </span>
                ))}
              </div>
            )}

            {/* Clear all status filters */}
            {(unusableFilter !== null || noBrandFilter !== null || hasSubtitleFilter !== null || hasTrimFilter !== null) && (
              <button
                onClick={() => {
                  setUnusableFilter(null); setNoBrandFilter(null);
                  setHasSubtitleFilter(null); setHasTrimFilter(null);
                  setPage(1);
                }}
                className="px-2 py-1.5 rounded-full text-xs text-gray-400 hover:text-red-500 hover:bg-red-50 border border-gray-200"
              >
                <X className="w-3 h-3 inline" /> クリア
              </button>
            )}
          </div>

          {/* Language filter */}
          {stats.language_stats && stats.language_stats.length > 0 && (
            <div className="mt-3 pt-3 border-t border-gray-100">
              <h4 className="text-xs font-medium text-gray-500 mb-2">言語フィルター</h4>
              <div className="flex gap-2 flex-wrap">
                {stats.language_stats.filter(ls => ls.language !== 'unknown').map((ls) => {
                  const langLabels = {
                    'ja': { label: '\ud83c\uddef\ud83c\uddf5 日本語', active: 'bg-gray-100 border-gray-400 text-gray-700', hover: 'hover:border-gray-300 hover:bg-gray-50' },
                    'zh-TW': { label: '\ud83c\uddf9\ud83c\uddfc 繁体中文', active: 'bg-rose-100 border-rose-400 text-rose-700', hover: 'hover:border-rose-300 hover:bg-rose-50' },
                    'zh-CN': { label: '\ud83c\udde8\ud83c\uddf3 簡体中文', active: 'bg-red-100 border-red-400 text-red-700', hover: 'hover:border-red-300 hover:bg-red-50' },
                    'en': { label: '\ud83c\uddec\ud83c\udde7 English', active: 'bg-blue-100 border-blue-400 text-blue-700', hover: 'hover:border-blue-300 hover:bg-blue-50' },
                    'ko': { label: '\ud83c\uddf0\ud83c\uddf7 韓国語', active: 'bg-sky-100 border-sky-400 text-sky-700', hover: 'hover:border-sky-300 hover:bg-sky-50' },
                    'th': { label: '\ud83c\uddf9\ud83c\udded Thai', active: 'bg-amber-100 border-amber-400 text-amber-700', hover: 'hover:border-amber-300 hover:bg-amber-50' },
                  };
                  const cfg = langLabels[ls.language] || { label: ls.language, active: 'bg-gray-100 border-gray-400 text-gray-700', hover: 'hover:border-gray-300 hover:bg-gray-50' };
                  const isActive = languageFilter === ls.language;
                  return (
                    <button
                      key={ls.language}
                      onClick={() => {
                        setLanguageFilter(isActive ? '' : ls.language);
                        setPage(1);
                      }}
                      className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                        isActive ? `${cfg.active} shadow-sm` : `bg-white border-gray-200 text-gray-600 ${cfg.hover}`
                      }`}
                    >
                      {cfg.label} <span className="ml-0.5 font-bold">{ls.count}</span>
                    </button>
                  );
                })}
                {languageFilter && (
                  <button
                    onClick={() => { setLanguageFilter(''); setPage(1); }}
                    className="px-2 py-1.5 rounded-full text-xs text-gray-400 hover:text-red-500 hover:bg-red-50 border border-gray-200"
                  >
                    <X className="w-3 h-3 inline" /> クリア
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Brand Cards - Horizontal scrollable */}
      {brands.length > 0 && (
        <div className="mb-6">
          <h3 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
            <Building2 className="w-4 h-4 text-blue-500" />
            ブランド一覧
            <span className="text-xs font-normal text-gray-400">クリックでフィルタ</span>
          </h3>
          <div className="flex gap-3 overflow-x-auto pb-3 scrollbar-thin">
            {/* All brands button */}
            <button
              onClick={() => { setSelectedBrand(""); setPage(1); }}
              className={`flex-shrink-0 px-4 py-3 rounded-xl border-2 transition-all duration-200 min-w-[120px] text-center ${
                !selectedBrand
                  ? "border-purple-500 bg-purple-50 shadow-md"
                  : "border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm"
              }`}
            >
              <div className="text-lg font-bold text-gray-900">{brands.reduce((s, b) => s + b.clip_count, 0)}</div>
              <div className="text-[11px] text-gray-500">全ブランド</div>
            </button>
            {brands.filter(b => b.clip_count > 0).map((b) => {
              const isActive = selectedBrand === b.client_id;
              return (
                <button
                  key={b.client_id}
                  onClick={() => { setSelectedBrand(isActive ? "" : b.client_id); setPage(1); }}
                  className={`flex-shrink-0 px-4 py-3 rounded-xl border-2 transition-all duration-200 min-w-[140px] text-left ${
                    isActive
                      ? "border-blue-500 bg-blue-50 shadow-md"
                      : "border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    {b.logo_url ? (
                      <img src={b.logo_url} alt={b.name} className="w-6 h-6 rounded-full object-cover border border-gray-200" />
                    ) : (
                      <div className="w-6 h-6 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center text-white text-[10px] font-bold">
                        {b.name?.charAt(0)}
                      </div>
                    )}
                    <span className="text-xs font-medium text-gray-800 truncate max-w-[100px]">{b.name}</span>
                    {b.source === "aitherhub" ? (
                      <span className="px-1 py-0.5 text-[8px] font-bold rounded bg-purple-100 text-purple-600 border border-purple-200 flex-shrink-0" title="AitherHubで追加">
                        AH
                      </span>
                    ) : b.lcj_brand_id ? (
                      <span className="px-1 py-0.5 text-[8px] font-bold rounded bg-orange-100 text-orange-600 border border-orange-200 flex-shrink-0" title="LCJ Mall同期">
                        LCJ
                      </span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-lg font-bold text-gray-900">{b.clip_count}</span>
                    <span className="text-[10px] text-gray-400">クリップ</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1 text-[10px]">
                    {b.sold_count > 0 && (
                      <span className="text-green-600 font-medium">{"\u2713"} {b.sold_count}売</span>
                    )}
                    {b.subtitle_count > 0 && (
                      <span className="text-purple-600 font-medium flex items-center gap-0.5">
                        <Subtitles className="w-2.5 h-2.5" />{b.subtitle_count}
                      </span>
                    )}
                    {b.total_gmv > 0 && (
                      <span className="text-blue-600 font-medium">{formatGMV(b.total_gmv)}</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Results */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-8 h-8 animate-spin text-purple-500" />
          <span className="ml-3 text-gray-500">検索中...</span>
        </div>
      ) : clips.length === 0 ? (
        <div className="text-center py-20">
          <Database className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 text-sm">
            {searchQuery || selectedTag || soldFilter !== null || selectedBrand
              ? "条件に一致するクリップが見つかりませんでした"
              : "クリップデータを読み込み中..."}
          </p>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between mb-4">
            <span className="text-sm text-gray-600">
              {total}件中 {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, total)}件
            </span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
            {clips.map((clip) => (
              <ClipCard
                key={clip.clip_id}
                clip={clip}
                onPlay={setPlayerClip}
                brands={brands}
                adminKey={adminKey}
                onBrandChange={handleBrandChange}
              />
            ))}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-8">
              <button
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page === 1}
                className="p-2 rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-30"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-sm text-gray-600">{page} / {totalPages}</span>
              <button
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages}
                className="p-2 rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-30"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}
        </>
      )}

      {/* Video player modal (Check Mode) */}
      {playerClip && (
        <VideoPlayerModal
          clip={playerClip}
          clips={clips}
          onClose={() => setPlayerClip(null)}
          brands={brands}
          adminKey={adminKey}
          onBrandChange={handleBrandChange}
          onNavigate={setPlayerClip}
        />
      )}
    </div>
  );
}
