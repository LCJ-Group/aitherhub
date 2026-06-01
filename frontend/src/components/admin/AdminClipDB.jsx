import { useState, useEffect, useRef, useCallback } from "react";
import {
  Search, Filter, Tag, Play, X, BarChart3, ShoppingBag, Users,
  Star, ThumbsUp, ThumbsDown, ArrowUpDown, Database, Sparkles,
  Loader2, RefreshCw, ChevronLeft, ChevronRight, Building2, Plus, Minus,
  Download, Subtitles, Scissors, CheckCircle, Ban, AlertTriangle, Undo2,
  MessageSquare, SkipBack, SkipForward, Volume2, VolumeX,
  TrendingUp, Activity, Brain, Clock, Zap, Target, Eye, Layers,
  ListPlus, List, Trash2, Palette, Edit3, CheckSquare, Square,
} from "lucide-react";
import { TikTokUrlRegisterButton } from "./TikTokTrackingPanel";
import CaptionOverlayPlayer from "./CaptionOverlayPlayer";
import RegenList from "./RegenList";

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

function ClipCard({ clip, onPlay, brands, adminKey, onBrandChange, allPlaylists, onPlaylistChange, isSelected, onToggleSelect }) {
  const [expanded, setExpanded] = useState(false);
  const [showParams, setShowParams] = useState(false);
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
      localUnusable ? "border-red-300 bg-red-50 opacity-75" :
      isSelected ? "border-blue-400 bg-blue-50 ring-2 ring-blue-200" : "border-gray-200 bg-white"
    }`}>
      {/* Selection checkbox */}
      {onToggleSelect && (
        <button
          onClick={(e) => { e.stopPropagation(); onToggleSelect(clip.clip_id || clip.id); }}
          className={`absolute top-2 left-2 z-20 w-6 h-6 rounded flex items-center justify-center transition-all ${
            isSelected
              ? "bg-blue-500 text-white shadow-md"
              : "bg-white/90 text-gray-500 hover:bg-blue-100 hover:text-blue-500 shadow-sm border border-gray-200"
          }`}
          title={isSelected ? "選択解除" : "選択"}
        >
          {isSelected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />}
        </button>
      )}
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
          {clip.has_regeneration && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-blue-500 text-white shadow flex items-center gap-0.5">
              <RefreshCw className="w-2.5 h-2.5" /> 再生成済
            </span>
          )}
          {clip.regen_skipped && (
            <span
              className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-gray-500 text-white shadow flex items-center gap-0.5 cursor-help"
              title={clip.skip_reason || 'GPT判定によりスキップ'}
            >
              <Ban className="w-2.5 h-2.5" /> スキップ済
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
        {/* AI Version + 生成日 */}
        <div className="flex items-center gap-2 flex-wrap">
          {clip.ml_model_version ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold bg-gradient-to-r from-purple-500 to-indigo-500 text-white shadow-sm">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
              AI {clip.ml_model_version}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium bg-gray-100 text-gray-500">
              Pre-AI
            </span>
          )}
          {clip.created_at && (
            <span className="flex items-center gap-0.5 text-[10px] text-gray-400">
              <Clock className="w-3 h-3" />
              {new Date(clip.created_at).toLocaleDateString('ja-JP', { month: '2-digit', day: '2-digit' })}
            </span>
          )}
        </div>

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

        {/* Parameters tab (collapsible) */}
        <div className="border-t border-gray-100 pt-1.5 mt-1">
          <button
            onClick={(e) => { e.stopPropagation(); setShowParams(!showParams); }}
            className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-gray-600 transition w-full"
          >
            <Activity className="w-3 h-3" />
            <span>パラメータ</span>
            <span className="ml-auto text-[9px]">{showParams ? '▲' : '▼'}</span>
          </button>
          {showParams && (
            <div className="mt-1.5 space-y-1 text-[10px] text-gray-500 bg-gray-50 rounded-lg p-2">
              {clip.duration_sec != null && (
                <div className="flex justify-between"><span>長さ</span><span className="font-medium text-gray-700">{Math.round(clip.duration_sec)}秒</span></div>
              )}
              {clip.time_start != null && clip.time_end != null && (
                <div className="flex justify-between"><span>区間</span><span className="font-medium text-gray-700">{clip.time_start.toFixed(1)}s - {clip.time_end.toFixed(1)}s</span></div>
              )}
              {clip.phase_index != null && (
                <div className="flex justify-between"><span>フェーズ</span><span className="font-medium text-gray-700">#{clip.phase_index}</span></div>
              )}
              {clip.phase_description && (
                <div className="flex justify-between gap-2"><span className="shrink-0">説明</span><span className="font-medium text-gray-700 text-right truncate">{clip.phase_description}</span></div>
              )}
              {clip.subtitle_style && (
                <div className="flex justify-between"><span>字幕</span><span className="font-medium text-gray-700">{clip.subtitle_style}</span></div>
              )}
              {clip.detected_language && (
                <div className="flex justify-between"><span>言語</span><span className="font-medium text-gray-700">{clip.detected_language}</span></div>
              )}
              {clip.viewer_count != null && clip.viewer_count > 0 && (
                <div className="flex justify-between"><span>視聴者数</span><span className="font-medium text-gray-700">{clip.viewer_count.toLocaleString()}</span></div>
              )}
              {clip.importance_score != null && (
                <div className="flex justify-between"><span>重要度</span><span className="font-medium text-gray-700">{(clip.importance_score * 100).toFixed(0)}%</span></div>
              )}
              {clip.stream_date && (
                <div className="flex justify-between"><span>配信日</span><span className="font-medium text-gray-700">{clip.stream_date}</span></div>
              )}
              {clip.download_count != null && clip.download_count > 0 && (
                <div className="flex justify-between"><span>DL数</span><span className="font-medium text-gray-700">{clip.download_count}</span></div>
              )}
              {clip.video_id && (
                <div className="flex justify-between gap-1"><span className="shrink-0">Video</span><span className="font-mono text-[9px] text-gray-400 truncate">{clip.video_id.slice(0, 8)}...</span></div>
              )}
              {clip.clip_id && (
                <div className="flex justify-between gap-1"><span className="shrink-0">Clip ID</span><span className="font-mono text-[9px] text-gray-400 truncate">{clip.clip_id.slice(0, 8)}...</span></div>
              )}
            </div>
          )}
        </div>

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

        {/* AI Clip generation button */}
        {clip.clip_url && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (typeof window.__openAiClipModal === 'function') {
                window.__openAiClipModal(clip);
              }
            }}
            className="flex items-center justify-center gap-1.5 w-full py-1.5 rounded-lg text-[11px] font-medium text-emerald-600 bg-emerald-50 hover:bg-emerald-100 transition-colors"
          >
            <Zap className="w-3 h-3" /> AIクリップ生成
          </button>
        )}

        {/* V10: Regenerate from source button */}
        {clip.clip_url && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (typeof window.__openV10RegenModal === 'function') {
                window.__openV10RegenModal(clip);
              }
            }}
            className="flex items-center justify-center gap-1.5 w-full py-1.5 rounded-lg text-[11px] font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 transition-colors"
          >
            <Sparkles className="w-3 h-3" /> AI再生成
          </button>
        )}
        {/* PiP合成 button */}
        {clip.clip_url && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (typeof window.__openPipModal === 'function') {
                window.__openPipModal(clip);
              }
            }}
            className="flex items-center justify-center gap-1.5 w-full py-1.5 rounded-lg text-[11px] font-medium text-purple-600 bg-purple-50 hover:bg-purple-100 transition-colors"
          >
            <Layers className="w-3 h-3" /> PiP合成
          </button>
        )}

        {/* Playlist assignment */}
        <div className="flex items-center gap-1.5 pt-1 border-t border-gray-100 mt-1">
          <ClipPlaylistPopover
            clipId={clip.clip_id || clip.id}
            clipPlaylists={clip.playlists}
            allPlaylists={allPlaylists || []}
            adminKey={adminKey}
            onUpdate={onPlaylistChange}
          />
          {clip.playlists && clip.playlists.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {clip.playlists.map((pl) => (
                <span
                  key={pl.id}
                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-medium text-white"
                  style={{ backgroundColor: pl.color || '#6366f1' }}
                >
                  {pl.name}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* TikTok tracking button */}
        <TikTokUrlRegisterButton clipId={clip.id} adminKey={adminKey} />

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

      {stats.avg_cta_score != null && (
        <div className="bg-white rounded-xl border border-orange-200 p-4 text-center">
          <div className="text-2xl font-bold text-orange-600">{Math.round(stats.avg_cta_score)}</div>
          <div className="text-xs text-gray-500 mt-1">平均CTA</div>
        </div>
      )}
    </div>
  );
}

// ─── Review Stats Chart (日別採点数) ───
function ReviewStatsChart({ data }) {
  if (!data || !data.daily || data.daily.length === 0) return null;
  const daily = data.daily.slice(-14); // 直近14日
  const maxTotal = Math.max(...daily.map(d => d.total_reviewed), 1);
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
          <CheckCircle className="w-4 h-4 text-green-500" />
          日別採点数（直近14日）
        </h3>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <span className="w-3 h-3 rounded-sm bg-green-500 inline-block"></span>
            ブランド割当: <strong className="text-green-700">{data.total_brand_assigned}</strong>
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-3 rounded-sm bg-red-400 inline-block"></span>
            NG: <strong className="text-red-600">{data.total_ng_marked}</strong>
          </span>
          <span className="font-medium text-gray-700">合計: {data.total_reviewed}</span>
        </div>
      </div>
      <div className="flex items-end gap-1" style={{ height: "100px" }}>
        {daily.map((d) => {
          const brandH = (d.brand_assigned / maxTotal) * 100;
          const ngH = (d.ng_marked / maxTotal) * 100;
          const dateLabel = d.date.slice(5); // MM-DD
          return (
            <div key={d.date} className="flex-1 flex flex-col items-center gap-0.5">
              <div className="w-full flex flex-col items-center justify-end" style={{ height: "80px" }}>
                <div className="w-full flex flex-col items-stretch justify-end" style={{ height: "80px" }}>
                  {d.ng_marked > 0 && (
                    <div
                      className="w-full bg-red-400 rounded-t-sm"
                      style={{ height: `${ngH}%`, minHeight: d.ng_marked > 0 ? "2px" : "0" }}
                      title={`NG: ${d.ng_marked}`}
                    />
                  )}
                  {d.brand_assigned > 0 && (
                    <div
                      className="w-full bg-green-500 rounded-t-sm"
                      style={{ height: `${brandH}%`, minHeight: d.brand_assigned > 0 ? "2px" : "0" }}
                      title={`ブランド: ${d.brand_assigned}`}
                    />
                  )}
                </div>
              </div>
              <span className="text-[9px] text-gray-400 mt-0.5">{dateLabel}</span>
              <span className="text-[10px] font-medium text-gray-600">{d.total_reviewed}</span>
            </div>
          );
        })}
      </div>
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
  // Read initial state from URL params
  const urlParams = new URLSearchParams(window.location.search);
  const initSort = urlParams.get("sort_by") || "uploaded_at";
  const initOrder = urlParams.get("sort_order") || "desc";
  const initPage = parseInt(urlParams.get("clip_page") || "1", 10) || 1;
  const initClipId = urlParams.get("clip_id") || "";

  // Clip ID filter (from TikTok Performance "クリップDBで見る" button)
  const [clipIdFilter, setClipIdFilter] = useState(initClipId);

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
  const [notDownloadedFilter, setNotDownloadedFilter] = useState(null);
  const [showRegenList, setShowRegenList] = useState(false);
  const [languageFilter, setLanguageFilter] = useState("");
  const [aiVersionFilter, setAiVersionFilter] = useState("");
  const [playlists, setPlaylists] = useState([]);
  const [selectedPlaylistFilter, setSelectedPlaylistFilter] = useState("");
  const [showPlaylistManager, setShowPlaylistManager] = useState(false);
  const [sortBy, setSortBy] = useState(initSort);
  const [sortOrder, setSortOrder] = useState(initOrder);
  const [page, setPage] = useState(initPage);
  const [showFilters, setShowFilters] = useState(false);

  // Sync sort/page state to URL params (without page reload)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    params.set("sort_by", sortBy);
    params.set("sort_order", sortOrder);
    if (page > 1) params.set("clip_page", String(page));
    else params.delete("clip_page");
    const newUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState({}, "", newUrl);
  }, [sortBy, sortOrder, page]);

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
  const [reviewStats, setReviewStats] = useState(null);

  // AI Clip generation state
   const [aiClipModalClip, setAiClipModalClip] = useState(null);
  const [aiClipJobId, setAiClipJobId] = useState(null);
  const [aiClipJobStatus, setAiClipJobStatus] = useState(null);
  const [aiClipGenerating, setAiClipGenerating] = useState(false);
  // V10: Regeneration modal state
  const [v10RegenClip, setV10RegenClip] = useState(null);
  const [v10RegenJobId, setV10RegenJobId] = useState(null);
  const [v10RegenStatus, setV10RegenStatus] = useState(null);
  const [v10RegenGenerating, setV10RegenGenerating] = useState(false);
  // PiP合成 modal state
  const [pipModalClip, setPipModalClip] = useState(null);
  const [pipJobId, setPipJobId] = useState(null);
  const [pipJobStatus, setPipJobStatus] = useState(null);
  const [pipGenerating, setPipGenerating] = useState(false);
  // V12: Batch regeneration (bulk select) state
  const [selectedClips, setSelectedClips] = useState([]);
  const [batchRegenJobId, setBatchRegenJobId] = useState(null);
  const [batchRegenStatus, setBatchRegenStatus] = useState(null);
  const [batchRegenRunning, setBatchRegenRunning] = useState(false);
  const [showBatchRegenModal, setShowBatchRegenModal] = useState(false);
  // Register global callback for ClipCard AI clip button
  useEffect(() => {
    window.__openAiClipModal = (clip) => setAiClipModalClip(clip);
    window.__openV10RegenModal = (clip) => setV10RegenClip(clip);
    window.__openPipModal = (clip) => setPipModalClip(clip);
    return () => { delete window.__openAiClipModal; delete window.__openV10RegenModal; delete window.__openPipModal; };
  }, []);

  // Poll AI clip job status with stall detection
  const [aiClipStalled, setAiClipStalled] = useState(false);
  const lastProgressRef = useRef({ pct: 0, time: Date.now() });
  useEffect(() => {
    if (!aiClipJobId) return;
    setAiClipStalled(false);
    lastProgressRef.current = { pct: 0, time: Date.now() };
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/ai-clip/jobs/${aiClipJobId}`, {
          headers: { "X-Admin-Key": adminKey },
        });
        const data = await res.json();
        setAiClipJobStatus(data);
        if (data.status === "done" || data.status === "failed") {
          clearInterval(interval);
          setAiClipGenerating(false);
          setAiClipStalled(false);
        } else {
          // Stall detection: if progress hasn't changed for 5 minutes
          const currentPct = data.progress_pct || 0;
          if (currentPct !== lastProgressRef.current.pct) {
            lastProgressRef.current = { pct: currentPct, time: Date.now() };
            setAiClipStalled(false);
          } else if (Date.now() - lastProgressRef.current.time > 5 * 60 * 1000) {
            setAiClipStalled(true);
          }
        }
      } catch (e) {
        console.warn("[AI Clip] Poll failed:", e);
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [aiClipJobId]);

  async function startAiClipGeneration(clip, options = {}) {
    setAiClipGenerating(true);
    setAiClipJobStatus(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ai-clip/generate-from-clip`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Key": adminKey,
        },
        body: JSON.stringify({
          clip_id: clip.clip_id || clip.id,
          ...options,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `API error ${res.status}`);
      }
      const data = await res.json();
      setAiClipJobId(data.job_id);
      setAiClipJobStatus({ status: "queued", progress_pct: 0, current_step: "ジョブをキューに追加しました" });
    } catch (e) {
      alert(`AIクリップ生成エラー: ${e.message}`);
      setAiClipGenerating(false);
    }
  }

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
  }, [page, sortBy, sortOrder, selectedTag, soldFilter, ratingFilter, selectedBrand, unusableFilter, noBrandFilter, hasSubtitleFilter, hasTrimFilter, notDownloadedFilter, languageFilter, aiVersionFilter, selectedPlaylistFilter, clipIdFilter]);

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

    // 2. Load stats, tags, brands, playlists, clips
    loadStats();
    loadReviewStats();
    loadTags();
    loadBrands();
    loadPlaylists();
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

  async function loadReviewStats() {
    try {
      const data = await clipDbFetch("/review-stats", { days: 30 }, adminKey);
      setReviewStats(data);
    } catch (e) {
      console.warn("[ClipDB] Failed to load review stats:", e);
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
    const CACHE_KEY = "aitherhub_brands_cache";
    const MAX_RETRIES = 3;
    let lastError = null;

    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
      try {
        const data = await clipDbFetch("/brands", {}, adminKey);
        const brandsList = data.brands || [];
        if (brandsList.length > 0) {
          // Save to localStorage as backup cache
          try {
            localStorage.setItem(CACHE_KEY, JSON.stringify({
              brands: brandsList,
              timestamp: Date.now(),
              count: brandsList.length,
            }));
          } catch (cacheErr) {
            // localStorage might be full, ignore
          }
          setBrands(brandsList);
          return;
        }
      } catch (e) {
        lastError = e;
        console.warn(`[ClipDB] Brand load attempt ${attempt}/${MAX_RETRIES} failed:`, e);
        if (attempt < MAX_RETRIES) {
          await new Promise(r => setTimeout(r, 1000 * attempt)); // exponential backoff
        }
      }
    }

    // All retries failed - try localStorage cache
    console.warn("[ClipDB] All brand load retries failed, trying cache...", lastError);
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      if (cached) {
        const { brands: cachedBrands, timestamp, count } = JSON.parse(cached);
        const ageMinutes = Math.round((Date.now() - timestamp) / 60000);
        console.info(`[ClipDB] Using cached brands: ${count} brands, ${ageMinutes}min old`);
        setBrands(cachedBrands);
        return;
      }
    } catch (cacheErr) {
      console.warn("[ClipDB] Cache read failed:", cacheErr);
    }

    console.error("[ClipDB] CRITICAL: Failed to load brands from API and cache");
  }

  async function loadPlaylists() {
    try {
      const data = await clipDbFetch("/playlists", {}, adminKey);
      setPlaylists(data.playlists || []);
    } catch (e) {
      console.warn("[ClipDB] Failed to load playlists:", e);
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
      if (notDownloadedFilter !== null) params.not_downloaded = notDownloadedFilter;
      if (languageFilter) params.language = languageFilter;
      if (aiVersionFilter) params.ai_version = aiVersionFilter;
      if (selectedPlaylistFilter) params.playlist_id = selectedPlaylistFilter;
      if (clipIdFilter) params.clip_id = clipIdFilter;

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
    loadReviewStats();
  }, [searchQuery, selectedTag, selectedProduct, selectedLiver, selectedBrand, soldFilter, ratingFilter, unusableFilter, noBrandFilter, hasSubtitleFilter, hasTrimFilter, notDownloadedFilter, page, sortBy, sortOrder]);

  // V12: Toggle clip selection
  const toggleClipSelect = useCallback((clipId) => {
    setSelectedClips((prev) =>
      prev.includes(clipId) ? prev.filter((id) => id !== clipId) : [...prev, clipId]
    );
  }, []);

  // V12: Select all / deselect all on current page
  const selectAllOnPage = useCallback(() => {
    const pageClipIds = clips.map((c) => c.clip_id || c.id);
    setSelectedClips((prev) => {
      const newSet = new Set(prev);
      pageClipIds.forEach((id) => newSet.add(id));
      return [...newSet];
    });
  }, [clips]);

  const deselectAll = useCallback(() => {
    setSelectedClips([]);
  }, []);

  // V12: Start batch regeneration
  async function startBatchRegeneration() {
    if (selectedClips.length === 0) return;
    setBatchRegenRunning(true);
    setBatchRegenStatus(null);
    setShowBatchRegenModal(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ai-clip/clips/batch-regenerate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Key": adminKey },
        body: JSON.stringify({ clip_ids: selectedClips }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `API error ${res.status}`);
      }
      const data = await res.json();
      setBatchRegenJobId(data.job_id);
      setBatchRegenStatus({ status: "processing", progress_pct: 0, current_step: "GPT事前評価開始..." });
    } catch (e) {
      alert(`一括再生成エラー: ${e.message}`);
      setBatchRegenRunning(false);
      setShowBatchRegenModal(false);
    }
  }

  // V12: Poll batch regen job
  useEffect(() => {
    if (!batchRegenJobId) return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/ai-clip/jobs/${batchRegenJobId}`, {
          headers: { "X-Admin-Key": adminKey },
        });
        if (res.ok) {
          const data = await res.json();
          setBatchRegenStatus(data);
          if (data.status === "done" || data.status === "error") {
            setBatchRegenRunning(false);
            clearInterval(interval);
          }
        }
      } catch (e) {
        console.warn("[BatchRegen] Poll failed:", e);
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [batchRegenJobId]);

  // Show RegenList view when active
  if (showRegenList) {
    return <RegenList adminKey={adminKey} onBack={() => setShowRegenList(false)} />;
  }

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
        {/* Clip ID filter badge (from TikTok Performance) */}
        {clipIdFilter && (
          <div className="flex items-center gap-2 mt-2 px-3 py-1.5 rounded-lg bg-orange-50 border border-orange-300">
            <span className="text-xs font-medium text-orange-700">🎬 TikTok動画のクリップを表示中</span>
            <button
              onClick={() => { setClipIdFilter(""); setPage(1); const u = new URL(window.location); u.searchParams.delete("clip_id"); window.history.replaceState({}, "", u); }}
              className="text-orange-500 hover:text-orange-700 text-xs font-bold"
            >
              ✕ 解除
            </button>
          </div>
        )}
        {/* Playlist filter - always visible */}
        <div className="flex flex-wrap items-center gap-2 mt-2">
          <select
            value={selectedPlaylistFilter}
            onChange={(e) => { setSelectedPlaylistFilter(e.target.value); setPage(1); }}
            className="px-3 py-1.5 rounded-lg border border-purple-400 text-xs bg-purple-50 focus:outline-none focus:ring-2 focus:ring-purple-500 font-medium text-purple-700"
          >
            <option value="">📋 プレイリスト: すべて</option>
            {playlists.map((pl) => (
              <option key={pl.id} value={pl.id}>
                {pl.name} ({pl.clip_count})
              </option>
            ))}
          </select>
          <button
            onClick={() => setShowPlaylistManager(true)}
            className="px-3 py-1.5 rounded-lg border border-purple-400 text-xs text-purple-700 hover:bg-purple-100 font-medium transition"
            title="プレイリスト管理"
          >
            <ListPlus className="w-3.5 h-3.5 inline mr-0.5" />
            管理
          </button>
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
              value={aiVersionFilter}
              onChange={(e) => { setAiVersionFilter(e.target.value); setPage(1); }}
              className="px-3 py-1.5 rounded-lg border border-purple-300 text-xs bg-purple-50 focus:outline-none focus:ring-2 focus:ring-purple-500 font-medium"
            >
              <option value="">AI Ver: すべて</option>
              <option value="pre-ai">Pre-AI（従来）</option>
              <option value="v7.20260501">AI v7.20260501</option>
            </select>


            <select
              value={sortBy}
              onChange={(e) => { setSortBy(e.target.value); setPage(1); }}
              className="px-3 py-1.5 rounded-lg border border-purple-400 text-xs bg-purple-50 font-medium text-purple-700 focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              <option value="uploaded_at">アップロード日順</option>
              <option value="created_at">作成日順</option>
              <option value="stream_date">配信日順</option>
              <option value="gmv">GMV順</option>
              <option value="cta_score">CTAスコア順</option>
              <option value="importance_score">重要度順</option>
              <option value="duration_sec">長さ順</option>
              <option value="rating">評価順</option>
            </select>

            <button
              onClick={() => setSortOrder(sortOrder === "desc" ? "asc" : "desc")}
              className={`px-2 py-1.5 rounded-lg border text-xs font-medium transition-all ${
                sortOrder === "desc"
                  ? "border-purple-400 bg-purple-50 text-purple-700"
                  : "border-orange-400 bg-orange-50 text-orange-700"
              }`}
              title={sortOrder === "desc" ? "降順（大きい/新しい順）" : "昇順（小さい/古い順）"}
            >
              {sortOrder === "desc" ? "↓ 新しい順" : "↑ 古い順"}
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

            {(selectedTag || selectedProduct || selectedLiver || selectedBrand || soldFilter !== null || ratingFilter || unusableFilter !== null || noBrandFilter !== null || hasSubtitleFilter !== null || hasTrimFilter !== null || notDownloadedFilter !== null || selectedPlaylistFilter) && (
              <button
                onClick={() => {
                  setSelectedTag(""); setSelectedProduct(""); setSelectedLiver("");
                  setSelectedBrand(""); setSoldFilter(null); setRatingFilter(""); setUnusableFilter(null);
                  setNoBrandFilter(null); setHasSubtitleFilter(null); setHasTrimFilter(null); setNotDownloadedFilter(null); setSelectedPlaylistFilter(""); setPage(1);
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
      {showStats && reviewStats && <ReviewStatsChart data={reviewStats} />}
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

            {/* Not downloaded */}
            <button
              onClick={() => {
                if (notDownloadedFilter === true) { setNotDownloadedFilter(null); }
                else { setNotDownloadedFilter(true); }
                setPage(1);
              }}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                notDownloadedFilter === true
                  ? "bg-orange-100 border-orange-400 text-orange-700 shadow-sm"
                  : "bg-white border-gray-200 text-gray-600 hover:border-orange-300 hover:bg-orange-50"
              }`}
            >
              <Download className="w-3 h-3 inline mr-1" />
              未DL {stats.not_downloaded_clips > 0 && <span className="ml-0.5 font-bold">{stats.not_downloaded_clips}</span>}
            </button>

            {/* Downloaded */}
            <button
              onClick={() => {
                if (notDownloadedFilter === false) { setNotDownloadedFilter(null); }
                else { setNotDownloadedFilter(false); }
                setPage(1);
              }}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                notDownloadedFilter === false
                  ? "bg-green-100 border-green-400 text-green-700 shadow-sm"
                  : "bg-white border-gray-200 text-gray-600 hover:border-green-300 hover:bg-green-50"
              }`}
            >
              <Download className="w-3 h-3 inline mr-1" />
              DL済 {stats.downloaded_clips > 0 && <span className="ml-0.5 font-bold">{stats.downloaded_clips}</span>}
            </button>

            {/* 再生成済み button */}
            <button
              onClick={() => setShowRegenList(true)}
              className="px-3 py-1.5 rounded-full text-xs font-medium border transition-all bg-white border-blue-200 text-blue-600 hover:border-blue-400 hover:bg-blue-50"
            >
              <RefreshCw className="w-3 h-3 inline mr-1" />
              再生成済み
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
            {(unusableFilter !== null || noBrandFilter !== null || hasSubtitleFilter !== null || hasTrimFilter !== null || notDownloadedFilter !== null) && (
              <button
                onClick={() => {
                  setUnusableFilter(null); setNoBrandFilter(null);
                  setHasSubtitleFilter(null); setHasTrimFilter(null);
                  setNotDownloadedFilter(null);
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
                    {b.subtitle_count > 0 && (
                      <span className="text-purple-600 font-medium flex items-center gap-0.5">
                        <Subtitles className="w-2.5 h-2.5" />{b.subtitle_count}
                      </span>
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
            {searchQuery || selectedTag || selectedBrand
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
                allPlaylists={playlists}
                onPlaylistChange={() => { loadClips(); loadPlaylists(); }}
                isSelected={selectedClips.includes(clip.clip_id || clip.id)}
                onToggleSelect={toggleClipSelect}
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

      {/* AI Clip Generation Modal */}
      {aiClipModalClip && (
          <AiClipGenerationModal
          clip={aiClipModalClip}
          onClose={() => {
            setAiClipModalClip(null);
            if (!aiClipGenerating) {
              setAiClipJobId(null);
              setAiClipJobStatus(null);
            }
          }}
          onGenerate={(options) => startAiClipGeneration(aiClipModalClip, options)}
          generating={aiClipGenerating}
          jobStatus={aiClipJobStatus}
          stalled={aiClipStalled}
          adminKey={adminKey}
        />
      )}
      {/* V10 Regeneration Modal */}
      {v10RegenClip && (
        <V10RegenerationModal
          clip={v10RegenClip}
          onClose={() => {
            setV10RegenClip(null);
            if (!v10RegenGenerating) {
              setV10RegenJobId(null);
              setV10RegenStatus(null);
            }
          }}
          adminKey={adminKey}
          generating={v10RegenGenerating}
          setGenerating={setV10RegenGenerating}
          jobId={v10RegenJobId}
          setJobId={setV10RegenJobId}
          jobStatus={v10RegenStatus}
          setJobStatus={setV10RegenStatus}
        />
      )}
      {/* PiP合成 Modal */}
      {pipModalClip && (
        <PipCompositionModal
          clip={pipModalClip}
          onClose={() => {
            setPipModalClip(null);
            if (!pipGenerating) {
              setPipJobId(null);
              setPipJobStatus(null);
            }
          }}
          adminKey={adminKey}
          generating={pipGenerating}
          setGenerating={setPipGenerating}
          jobId={pipJobId}
          setJobId={setPipJobId}
          jobStatus={pipJobStatus}
          setJobStatus={setPipJobStatus}
        />
      )}

      {/* Playlist Manager Modal */}
      {showPlaylistManager && (
        <PlaylistManagerModal
          onClose={() => setShowPlaylistManager(false)}
          adminKey={adminKey}
          playlists={playlists}
          onRefresh={loadPlaylists}
        />
      )}

      {/* V12: Floating action bar for bulk selection */}
      {selectedClips.length > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-white border border-gray-200 rounded-2xl shadow-2xl px-6 py-3 flex items-center gap-4 animate-in slide-in-from-bottom-4">
          <div className="flex items-center gap-2">
            <CheckSquare className="w-5 h-5 text-blue-500" />
            <span className="text-sm font-bold text-gray-800">{selectedClips.length}件選択中</span>
          </div>
          <div className="w-px h-6 bg-gray-200" />
          <button
            onClick={selectAllOnPage}
            className="text-xs text-blue-600 hover:text-blue-800 font-medium transition"
          >
            ページ全選択
          </button>
          <button
            onClick={deselectAll}
            className="text-xs text-gray-500 hover:text-gray-700 font-medium transition"
          >
            全解除
          </button>
          <div className="w-px h-6 bg-gray-200" />
          <button
            onClick={startBatchRegeneration}
            disabled={batchRegenRunning}
            className="px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-sm font-bold rounded-xl hover:from-blue-700 hover:to-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all flex items-center gap-2 shadow-lg"
          >
            {batchRegenRunning ? (
              <><Loader2 className="w-4 h-4 animate-spin" /> 処理中...</>
            ) : (
              <><Sparkles className="w-4 h-4" /> 一括AI再生成</>
            )}
          </button>
        </div>
      )}

      {/* V12: Batch Regeneration Modal */}
      {showBatchRegenModal && (
        <BatchRegenModal
          jobStatus={batchRegenStatus}
          running={batchRegenRunning}
          selectedCount={selectedClips.length}
          onClose={() => {
            setShowBatchRegenModal(false);
            if (!batchRegenRunning) {
              setBatchRegenJobId(null);
              setBatchRegenStatus(null);
              setSelectedClips([]);
              loadClips();
            }
          }}
        />
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════
// ─── V12: Batch Regeneration Modal ───
// ═══════════════════════════════════════════════
function BatchRegenModal({ jobStatus, running, selectedCount, onClose }) {
  const results = jobStatus?.results || [];
  const doneResults = results.filter((r) => r.status === "done");
  const skippedResults = results.filter((r) => r.status === "skipped");
  const errorResults = results.filter((r) => r.status === "error");
  const config = jobStatus?.config || {};

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl max-w-xl w-full mx-4 max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-500 flex items-center justify-center">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-gray-900">一括AI再生成</h3>
              <p className="text-xs text-gray-500">{selectedCount}件のクリップを処理中</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-gray-100 transition">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Progress */}
        <div className="px-6 py-4">
          {jobStatus && (
            <div className="space-y-3">
              {/* Status indicator */}
              <div className="flex items-center gap-2">
                {running && <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />}
                {jobStatus.status === "done" && <CheckCircle className="w-4 h-4 text-green-500" />}
                {jobStatus.status === "error" && <AlertTriangle className="w-4 h-4 text-red-500" />}
                <span className="text-sm font-medium text-gray-700">
                  {jobStatus.status === "done" ? "完了" : jobStatus.status === "error" ? "エラー" : jobStatus.current_step || "処理中..."}
                </span>
              </div>
              {/* Progress bar */}
              {running && (
                <div className="space-y-1">
                  <div className="w-full bg-gray-200 rounded-full h-2.5">
                    <div
                      className="bg-gradient-to-r from-blue-500 to-indigo-500 h-2.5 rounded-full transition-all duration-700"
                      style={{ width: `${jobStatus.progress_pct || 0}%` }}
                    />
                  </div>
                  <p className="text-xs text-gray-500 text-right">{jobStatus.progress_pct || 0}%</p>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Results summary (shown when done) */}
        {jobStatus?.status === "done" && results.length > 0 && (
          <div className="px-6 pb-4 space-y-4">
            {/* Summary cards */}
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-green-50 border border-green-200 rounded-xl p-3 text-center">
                <div className="text-2xl font-bold text-green-600">{doneResults.length}</div>
                <div className="text-[10px] text-green-700 font-medium">再生成成功</div>
              </div>
              <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-center">
                <div className="text-2xl font-bold text-amber-600">{skippedResults.length}</div>
                <div className="text-[10px] text-amber-700 font-medium">スキップ</div>
              </div>
              <div className="bg-red-50 border border-red-200 rounded-xl p-3 text-center">
                <div className="text-2xl font-bold text-red-600">{errorResults.length}</div>
                <div className="text-[10px] text-red-700 font-medium">エラー</div>
              </div>
            </div>

            {/* Skipped details */}
            {skippedResults.length > 0 && (
              <div className="bg-amber-50 border border-amber-100 rounded-xl p-3">
                <h4 className="text-xs font-bold text-amber-800 mb-2 flex items-center gap-1">
                  <Ban className="w-3.5 h-3.5" /> スキップされたクリップ（GPT判定: 商品訴求力不足）
                </h4>
                <div className="space-y-1.5 max-h-32 overflow-y-auto">
                  {skippedResults.map((r) => (
                    <div key={r.clip_id} className="flex items-start gap-2 text-xs">
                      <span className="text-amber-500 flex-shrink-0 mt-0.5">●</span>
                      <div className="min-w-0">
                        <span className="text-gray-600 font-mono text-[10px]">{r.clip_id?.slice(0, 8)}...</span>
                        <span className="text-amber-700 ml-1">{r.reason || "商品訴求力不足"}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Success details */}
            {doneResults.length > 0 && (
              <div className="bg-green-50 border border-green-100 rounded-xl p-3">
                <h4 className="text-xs font-bold text-green-800 mb-2 flex items-center gap-1">
                  <CheckCircle className="w-3.5 h-3.5" /> 再生成成功
                </h4>
                <div className="space-y-1.5 max-h-32 overflow-y-auto">
                  {doneResults.map((r) => (
                    <div key={r.clip_id} className="flex items-center gap-2 text-xs">
                      <span className="text-green-500 flex-shrink-0">●</span>
                      <span className="text-gray-600 font-mono text-[10px]">{r.clip_id?.slice(0, 8)}...</span>
                      {r.result?.new_quality_score != null && (
                        <span className="text-green-700 font-medium">
                          スコア: {r.result.new_quality_score.toFixed(1)}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Error details */}
            {errorResults.length > 0 && (
              <div className="bg-red-50 border border-red-100 rounded-xl p-3">
                <h4 className="text-xs font-bold text-red-800 mb-2 flex items-center gap-1">
                  <AlertTriangle className="w-3.5 h-3.5" /> エラー
                </h4>
                <div className="space-y-1.5 max-h-32 overflow-y-auto">
                  {errorResults.map((r) => (
                    <div key={r.clip_id} className="flex items-start gap-2 text-xs">
                      <span className="text-red-500 flex-shrink-0 mt-0.5">●</span>
                      <div className="min-w-0">
                        <span className="text-gray-600 font-mono text-[10px]">{r.clip_id?.slice(0, 8)}...</span>
                        <span className="text-red-600 ml-1">{r.error || "不明なエラー"}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Error state */}
        {jobStatus?.status === "error" && (
          <div className="px-6 pb-4">
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl">
              <p className="text-sm text-red-700">{jobStatus.error || "不明なエラーが発生しました"}</p>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-100 flex items-center justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-800 bg-gray-100 hover:bg-gray-200 rounded-lg transition"
          >
            {running ? "バックグラウンドで継続" : "閉じる"}
          </button>
        </div>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════
// ─── AI Clip Generation Modal ───
// ═══════════════════════════════════════════════
function AiClipGenerationModal({ clip, onClose, onGenerate, generating, jobStatus, stalled, adminKey }) {
  const [options, setOptions] = useState({
    subtitle_style: "auto",
    enable_sfx: true,
    enable_transitions: true,
    enable_hook: true,
    hook_text: "",
    enable_silence_cut: true,
    enable_zoom_pulse: true,
    enable_progress_bar: true,
    enable_flash_intro: true,
    enable_loop_fade: true,
    enable_cta: true,
    enable_keyword_highlight: true,
    enable_subtitle_animation: true,
    zoom_intensity: 1.08,
    target_language: "auto",
    editing_profile_id: "",
  });
  const [editingProfiles, setEditingProfiles] = useState([]);
  useEffect(() => {
    fetch(`${API_BASE}/api/v1/editing-style/profiles`, { headers: { 'X-Admin-Key': adminKey } })
      .then(r => r.json())
      .then(d => setEditingProfiles((d.profiles || []).filter(p => p.status === 'active')))
      .catch(() => {});
  }, [adminKey]);

  const isDone = jobStatus?.status === "done";
  const isFailed = jobStatus?.status === "failed";
  const isProcessing = generating || (jobStatus && !isDone && !isFailed);
  const resultClip = isDone && jobStatus?.results?.[0];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="relative bg-white rounded-2xl shadow-2xl max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="sticky top-0 z-10 bg-white rounded-t-2xl border-b border-gray-100 px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-emerald-500" />
            <h3 className="text-base font-bold text-gray-800">AIクリップ生成 <span className="text-xs bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded-full ml-1">V2.17</span></h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Source clip info */}
          <div className="flex gap-3 p-3 bg-gray-50 rounded-xl">
            {clip.thumbnail_url && (
              <img src={clip.thumbnail_url} alt="" className="w-16 h-24 object-cover rounded-lg" />
            )}
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-gray-800 truncate">{clip.product_name || "不明な商品"}</p>
              <p className="text-xs text-gray-500 mt-0.5">{clip.liver_name || "不明"}</p>
              <p className="text-xs text-gray-400 mt-0.5">{clip.duration_sec ? `${Math.round(clip.duration_sec)}秒` : ""}</p>
              {clip.transcript_text && (
                <p className="text-[10px] text-gray-400 mt-1 line-clamp-2">{clip.transcript_text}</p>
              )}
            </div>
          </div>

          {/* Generation options */}
          {!isProcessing && !isDone && (
            <div className="space-y-3">
              <h4 className="text-xs font-semibold text-gray-600 uppercase tracking-wide">エフェクト設定</h4>

              {/* Subtitle style */}
              <div className="flex items-center justify-between">
                <label className="text-xs text-gray-600">字幕スタイル</label>
                <select
                  value={options.subtitle_style}
                  onChange={(e) => setOptions({ ...options, subtitle_style: e.target.value })}
                  className="text-xs border border-gray-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                >
                  <option value="auto">自動</option>
                  <option value="simple">シンプル</option>
                  <option value="box">ボックス</option>
                  <option value="outline">アウトライン</option>
                  <option value="pop">ポップ</option>
                  <option value="gradient">グラデーション</option>
                  <option value="karaoke">カラオケ</option>
                </select>
              </div>

              {/* Target language */}
              <div className="flex items-center justify-between">
                <label className="text-xs text-gray-600">字幕言語</label>
                <select
                  value={options.target_language}
                  onChange={(e) => setOptions({ ...options, target_language: e.target.value })}
                  className="text-xs border border-gray-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                >
                  <option value="auto">自動検出</option>
                  <option value="ja">日本語</option>
                  <option value="zh">中文(簡体)</option>
                  <option value="zh-tw">中文(繁体)</option>
                </select>
              </div>

              {/* Hook text */}
              <div>
                <label className="text-xs text-gray-600 block mb-1">フックテキスト（空ならAI自動生成）</label>
                <input
                  type="text"
                  value={options.hook_text}
                  onChange={(e) => setOptions({ ...options, hook_text: e.target.value })}
                  placeholder="例: これが話題のセラム！"
                  className="w-full text-xs border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>

              {/* Zoom intensity */}
              <div className="flex items-center justify-between">
                <label className="text-xs text-gray-600">ズーム強度</label>
                <div className="flex items-center gap-2">
                  <input
                    type="range"
                    min="1.0" max="1.3" step="0.02"
                    value={options.zoom_intensity}
                    onChange={(e) => setOptions({ ...options, zoom_intensity: parseFloat(e.target.value) })}
                    className="w-24 accent-emerald-500"
                  />
                  <span className="text-xs text-gray-500 w-10 text-right">{options.zoom_intensity.toFixed(2)}x</span>
                </div>
              </div>

              {/* Toggle switches */}
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: "enable_silence_cut", label: "無音カット" },
                  { key: "enable_zoom_pulse", label: "ズームパルス" },
                  { key: "enable_progress_bar", label: "進行バー" },
                  { key: "enable_flash_intro", label: "フラッシュイントロ" },
                  { key: "enable_loop_fade", label: "ループフェード" },
                  { key: "enable_cta", label: "CTAテキスト" },
                  { key: "enable_keyword_highlight", label: "キーワードハイライト" },
                  { key: "enable_subtitle_animation", label: "字幕アニメーション" },
                  { key: "enable_hook", label: "フック" },
                  { key: "enable_sfx", label: "効果音" },
                ].map(({ key, label }) => (
                  <label key={key} className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={options[key]}
                      onChange={(e) => setOptions({ ...options, [key]: e.target.checked })}
                      className="rounded border-gray-300 text-emerald-500 focus:ring-emerald-500"
                    />
                    {label}
                  </label>
                ))}
              </div>

              {/* V12: Editing Profile Selection */}
              {editingProfiles.length > 0 && (
                <div className="flex items-center justify-between">
                  <label className="text-xs text-gray-600">🎨 編集スタイル</label>
                  <select
                    value={options.editing_profile_id}
                    onChange={(e) => setOptions({ ...options, editing_profile_id: e.target.value })}
                    className="text-xs border border-gray-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  >
                    <option value="">なし（デフォルト）</option>
                    {editingProfiles.map(p => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </div>
              )}

              {/* Generate button */}
              <button
                onClick={() => {
                  const opts = { ...options };
                  if (!opts.editing_profile_id) delete opts.editing_profile_id;
                  onGenerate(opts);
                }}
                className="w-full py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white font-semibold text-sm transition-colors flex items-center justify-center gap-2"
              >
                <Zap className="w-4 h-4" /> AIクリップを生成
              </button>
            </div>
          )}

          {/* Processing status */}
          {isProcessing && (
            <div className="space-y-3">
              {/* Stall warning */}
              {stalled && (
                <div className="flex items-center gap-2 p-3 bg-amber-50 border border-amber-200 rounded-xl">
                  <AlertTriangle className="w-4 h-4 text-amber-500 flex-shrink-0" />
                  <div className="flex-1">
                    <p className="text-xs font-medium text-amber-700">処理が停止している可能性があります</p>
                    <p className="text-[10px] text-amber-600 mt-0.5">5分以上進捗が更新されていません。サーバー側で自動リカバリーが実行されます。</p>
                  </div>
                </div>
              )}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Loader2 className={`w-5 h-5 animate-spin ${stalled ? 'text-amber-500' : 'text-emerald-500'}`} />
                  <span className="text-sm font-medium text-gray-700">{stalled ? '応答待ち...' : '生成中...'}</span>
                </div>
                <span className={`text-lg font-bold ${stalled ? 'text-amber-600' : 'text-emerald-600'}`}>{jobStatus?.progress_pct || 0}%</span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-3">
                <div
                  className="bg-gradient-to-r from-emerald-400 to-emerald-600 h-3 rounded-full transition-all duration-500"
                  style={{ width: `${jobStatus?.progress_pct || 0}%` }}
                />
              </div>
              <p className="text-xs text-gray-600 font-medium">{jobStatus?.current_step || "処理を開始しています..."}</p>
              {/* Time info - step-based ETA */}
              {(() => {
                const createdAt = jobStatus?.created_at ? new Date(jobStatus.created_at) : null;
                const now = new Date();
                const elapsedMs = createdAt ? now - createdAt : 0;
                const elapsedSec = Math.floor(elapsedMs / 1000);
                const elapsedMin = Math.floor(elapsedSec / 60);
                const elapsedRemSec = elapsedSec % 60;
                const pct = jobStatus?.progress_pct || 0;
                // Step-based ETA: use typical total time (3min for 60s clip) and progress %
                const typicalTotalSec = 180; // 3 minutes typical
                const remainPct = Math.max(0, 100 - pct);
                const estimatedRemainSec = Math.round((remainPct / 100) * typicalTotalSec);
                let etaText;
                if (pct < 3) {
                  etaText = "約3分";
                } else if (pct >= 95) {
                  etaText = "まもなく完了";
                } else {
                  const etaMin = Math.floor(estimatedRemainSec / 60);
                  const etaSec = estimatedRemainSec % 60;
                  etaText = etaMin > 0 ? `約${etaMin}分${etaSec}秒` : `約${etaSec}秒`;
                }
                return (
                  <div className="flex items-center justify-between text-xs text-gray-500">
                    <span>⏱ 経過: {elapsedMin > 0 ? `${elapsedMin}分${elapsedRemSec}秒` : `${elapsedRemSec}秒`}</span>
                    <span>⏳ 残り: {etaText}</span>
                  </div>
                );
              })()}
              <p className="text-[10px] text-gray-400 text-center">※ このモーダルを閉じても生成は続行されます</p>
            </div>
          )}

          {/* Failed */}
          {isFailed && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-red-500">
                <AlertTriangle className="w-5 h-5" />
                <span className="text-sm font-medium">生成失敗</span>
              </div>
              <p className="text-xs text-red-400">{jobStatus?.error || "不明なエラー"}</p>
              <button
                onClick={() => onGenerate(options)}
                className="w-full py-2 rounded-xl bg-red-50 hover:bg-red-100 text-red-600 font-medium text-sm transition-colors flex items-center justify-center gap-2"
              >
                <RefreshCw className="w-4 h-4" /> 再試行
              </button>
            </div>
          )}

          {/* Success result */}
          {isDone && resultClip && (
            <SuccessResult resultClip={resultClip} jobStatus={jobStatus} adminKey={adminKey} />
          )}
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════
// ─── Success Result with Caption Overlay Player ───
// ═══════════════════════════════════════════════
function SuccessResult({ resultClip, jobStatus, adminKey }) {
  const [captions, setCaptions] = useState([]);
  const [hookText, setHookText] = useState(resultClip.hook_text || "");
  const [ctaText, setCtaText] = useState(resultClip.cta_text || "");
  const [captionsLoaded, setCaptionsLoaded] = useState(false);

  // Load captions from API
  useEffect(() => {
    if (!jobStatus?.job_id || !adminKey) return;
    const loadCaptions = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/ai-clip/jobs/${jobStatus.job_id}/captions`, {
          headers: { "X-Admin-Key": adminKey },
        });
        if (res.ok) {
          const data = await res.json();
          setCaptions(data.captions || []);
          if (data.hook_text) setHookText(data.hook_text);
          if (data.cta_text) setCtaText(data.cta_text);
        }
      } catch (e) {
        console.warn("[SuccessResult] Failed to load captions:", e);
      } finally {
        setCaptionsLoaded(true);
      }
    };
    loadCaptions();
  }, [jobStatus?.job_id, adminKey]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-emerald-600">
        <CheckCircle className="w-5 h-5" />
        <span className="text-sm font-bold">生成完了！</span>
      </div>

      {/* Caption Overlay Player */}
      {resultClip.download_url && (
        <CaptionOverlayPlayer
          videoUrl={resultClip.download_url}
          captions={captions}
          onCaptionsChange={setCaptions}
          jobId={jobStatus?.job_id}
          adminKey={adminKey}
          apiBase={API_BASE}
          hookText={hookText}
          ctaText={ctaText}
          onHookChange={setHookText}
          onCtaChange={setCtaText}
          showEditPanel={captionsLoaded && captions.length > 0}
          compact={true}
          subtitlesBurnedIn={true}
        />
      )}

      {/* Result info */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        {resultClip.duration_sec && (
          <div className="bg-gray-50 rounded-lg p-2">
            <span className="text-gray-400">長さ</span>
            <p className="font-semibold text-gray-700">{Math.round(resultClip.duration_sec)}秒</p>
          </div>
        )}
        {resultClip.file_size_mb && (
          <div className="bg-gray-50 rounded-lg p-2">
            <span className="text-gray-400">サイズ</span>
            <p className="font-semibold text-gray-700">{resultClip.file_size_mb.toFixed(1)}MB</p>
          </div>
        )}
        {resultClip.effects_applied && (
          <div className="bg-gray-50 rounded-lg p-2 col-span-2">
            <span className="text-gray-400">適用エフェクト</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {Array.isArray(resultClip.effects_applied)
                ? resultClip.effects_applied.map((e, i) => (
                    <span key={i} className="px-1.5 py-0.5 bg-emerald-50 text-emerald-600 rounded text-[10px] font-medium">{e}</span>
                  ))
                : Object.entries(resultClip.effects_applied).filter(([_, v]) => v).map(([key, _], i) => (
                    <span key={i} className="px-1.5 py-0.5 bg-emerald-50 text-emerald-600 rounded text-[10px] font-medium">{key}</span>
                  ))
              }
            </div>
          </div>
        )}
      </div>

      {/* Direct download button (original with burned subtitles) */}
      {resultClip.download_url && (
        <a
          href={resultClip.download_url}
          target="_blank"
          rel="noopener noreferrer"
          className="w-full py-2.5 rounded-xl bg-blue-500 hover:bg-blue-600 text-white font-semibold text-sm transition-colors flex items-center justify-center gap-2"
        >
          <Download className="w-4 h-4" /> そのままダウンロード
        </a>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════
// ─── V10: Regeneration from Source Modal ───
// ═══════════════════════════════════════════════
function V10RegenerationModal({ clip, onClose, adminKey, generating, setGenerating, jobId, setJobId, jobStatus, setJobStatus }) {
  const [comparison, setComparison] = useState(null);
  const [loadingComparison, setLoadingComparison] = useState(false);

  // Fetch comparison data on mount
  useEffect(() => {
    const fetchComparison = async () => {
      setLoadingComparison(true);
      try {
        const res = await fetch(`${API_BASE}/api/v1/ai-clip/clips/${clip.id || clip.clip_id}/regen-compare`, {
          headers: { "X-Admin-Key": adminKey },
        });
        if (res.ok) {
          const data = await res.json();
          setComparison(data);
        }
      } catch (e) {
        console.warn("Failed to fetch comparison:", e);
      } finally {
        setLoadingComparison(false);
      }
    };
    fetchComparison();
  }, [clip.id, clip.clip_id]);

  // Poll job status
  useEffect(() => {
    if (!jobId) return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/ai-clip/jobs/${jobId}`, {
          headers: { "X-Admin-Key": adminKey },
        });
        if (res.ok) {
          const data = await res.json();
          setJobStatus(data);
          if (data.status === "done" || data.status === "error") {
            setGenerating(false);
            clearInterval(interval);
          }
        }
      } catch (e) {
        console.warn("Poll failed:", e);
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [jobId]);

  const handleRegenerate = async () => {
    setGenerating(true);
    setJobStatus(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ai-clip/clips/${clip.id || clip.clip_id}/regenerate-from-source`, {
        method: "POST",
        headers: { "X-Admin-Key": adminKey, "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.ok) {
        const data = await res.json();
        setJobId(data.job_id);
        setJobStatus({ status: "processing", progress_pct: 0, current_step: "開始中..." });
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`再生成エラー: ${err.detail || res.statusText}`);
        setGenerating(false);
      }
    } catch (e) {
      alert(`再生成エラー: ${e.message}`);
      setGenerating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-blue-100 flex items-center justify-center">
              <RefreshCw className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-gray-900">AI 再生成</h3>
              <p className="text-xs text-gray-500">AIが自動で最適なクリップを生成します</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-gray-100 transition">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Source clip info */}
        <div className="px-6 py-3 bg-gray-50 border-b border-gray-100">
          <div className="flex items-center gap-3">
            {clip.thumbnail_url && (
              <img src={clip.thumbnail_url} alt="" className="w-16 h-24 object-cover rounded-lg" />
            )}
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-900 truncate">{clip.product_name || "商品名なし"}</p>
              <p className="text-xs text-gray-500">{clip.liver_name || "ライバー不明"}</p>
              <div className="flex items-center gap-3 mt-1">
                <span className="text-xs text-gray-400">
                  <Clock className="w-3 h-3 inline mr-0.5" />
                  {clip.duration_sec ? `${Math.round(clip.duration_sec)}秒` : "不明"}
                </span>
                {comparison?.original?.quality_score != null && (
                  <span className="text-xs text-amber-600 font-medium">
                    <Star className="w-3 h-3 inline mr-0.5" />
                    品質スコア: {comparison.original.quality_score.toFixed(1)}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* AI自動最適化説明 */}
        <div className="px-6 py-4">
          <div className="p-4 bg-blue-50 rounded-xl border border-blue-100">
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                <Sparkles className="w-4 h-4 text-blue-600" />
              </div>
              <div>
                <p className="text-sm font-medium text-gray-800 mb-1">AIが自動で最適化</p>
                <ul className="text-xs text-gray-600 space-y-1">
                  <li>• 元動画から最適な範囲を自動判定</li>
                  <li>• 字幕・フック・CTA・効果音を自動適用</li>
                  <li>• 品質スコア最大化を目指して処理</li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        {/* Job Status */}
        {jobStatus && (
          <div className="px-6 py-3 border-t border-gray-100">
            <div className="flex items-center gap-2 mb-2">
              {jobStatus.status === "processing" && <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />}
              {jobStatus.status === "done" && <CheckCircle className="w-4 h-4 text-green-500" />}
              {jobStatus.status === "error" && <AlertTriangle className="w-4 h-4 text-red-500" />}
              <span className="text-sm font-medium text-gray-700">
                {jobStatus.status === "done" ? "完了" : jobStatus.status === "error" ? "エラー" : "処理中..."}
              </span>
            </div>
            {jobStatus.status === "processing" && (
              <div className="space-y-1">
                <div className="w-full bg-gray-200 rounded-full h-2">
                  <div
                    className="bg-blue-500 h-2 rounded-full transition-all duration-500"
                    style={{ width: `${jobStatus.progress_pct || 0}%` }}
                  />
                </div>
                <p className="text-xs text-gray-500">{jobStatus.current_step || ""} ({jobStatus.progress_pct || 0}%)</p>
              </div>
            )}
            {jobStatus.status === "done" && jobStatus.results?.[0] && (
              <div className="mt-2 p-3 bg-green-50 rounded-lg">
                <div className="flex items-center gap-4 text-sm mb-3">
                  <span className="text-gray-600">
                    旧スコア: <strong className="text-amber-600">{jobStatus.results[0].original_quality_score?.toFixed(1) || "N/A"}</strong>
                  </span>
                  <span className="text-gray-400">→</span>
                  <span className="text-gray-600">
                    新スコア: <strong className="text-green-600">{jobStatus.results[0].new_quality_score?.toFixed(1) || "N/A"}</strong>
                  </span>
                </div>
                {/* Video Preview */}
                {jobStatus.results[0].download_url && (
                  <div className="mb-3">
                    <video
                      src={jobStatus.results[0].download_url}
                      controls
                      playsInline
                      className="w-full max-h-[400px] rounded-lg bg-black"
                      style={{ aspectRatio: '9/16', maxWidth: '240px' }}
                    />
                  </div>
                )}
                {jobStatus.results[0].download_url && (
                  <a
                    href={jobStatus.results[0].download_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-700 transition"
                  >
                    <Download className="w-3 h-3" /> ダウンロード
                  </a>
                )}
              </div>
            )}
            {jobStatus.status === "error" && (
              <p className="text-xs text-red-500 mt-1">{jobStatus.error || "不明なエラー"}</p>
            )}
          </div>
        )}

        {/* Previous regenerations */}
        {comparison?.regenerations?.length > 0 && (
          <div className="px-6 py-3 border-t border-gray-100">
            <h4 className="text-xs font-semibold text-gray-600 mb-2">過去の再生成履歴</h4>
            <div className="space-y-1">
              {comparison.regenerations.slice(0, 5).map((regen) => (
                <div key={regen.job_id} className="flex items-center justify-between text-xs p-2 bg-gray-50 rounded-lg">
                  <span className="text-gray-500">{new Date(regen.created_at).toLocaleString("ja-JP")}</span>
                  <span className={`font-medium ${regen.status === "done" ? "text-green-600" : regen.status === "error" ? "text-red-500" : "text-blue-500"}`}>
                    {regen.status === "done" ? `完了 (スコア: ${regen.new_quality_score?.toFixed(1) || "N/A"})` : regen.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-100 flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition"
          >
            閉じる
          </button>
          <button
            onClick={handleRegenerate}
            disabled={generating}
            className="px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition flex items-center gap-2"
          >
            {generating ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" /> 処理中...
              </>
            ) : (
              <>
                <Sparkles className="w-4 h-4" /> AI再生成を実行
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════
// ─── PiP Composition Modal ───
// ═══════════════════════════════════════════════
function PipCompositionModal({ clip, onClose, adminKey, generating, setGenerating, jobId, setJobId, jobStatus, setJobStatus }) {
  const [productImages, setProductImages] = useState([]);
  const [productVideos, setProductVideos] = useState([]);  // V12: 商品動画サポート
  const [productSearch, setProductSearch] = useState('');
  const [productResults, setProductResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const pollRef = useRef(null);

  const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

  // Search product master
  const searchProducts = async (query) => {
    setProductSearch(query);
    if (!query.trim()) {
      setProductResults([]);
      setShowDropdown(false);
      return;
    }
    setSearching(true);
    setShowDropdown(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ai-clip/product-master/search?q=${encodeURIComponent(query)}`, {
        headers: { "X-Admin-Key": adminKey },
      });
      const data = await res.json();
      setProductResults(data.products || []);
    } catch (e) {
      console.error('Product search error:', e);
      setProductResults([]);
    } finally {
      setSearching(false);
    }
  };

  // Select product from master
  const selectProduct = (product) => {
    if (product.product_image_urls && product.product_image_urls.length > 0) {
      setProductImages(product.product_image_urls.map(url => ({
        preview: url,
        url: url,
        uploading: false,
      })));
    }
    setProductSearch(product.product_name);
    setShowDropdown(false);
  };

  // Upload product image or video (V12: 動画サポート追加)
  const handleMediaUpload = async (files) => {
    const fileArray = Array.from(files);
    const imageFiles = fileArray.filter(f => f.type.startsWith('image/'));
    const videoFiles = fileArray.filter(f => f.type.startsWith('video/') || /\.(mp4|mov|avi|webm|mkv)$/i.test(f.name));

    // Handle images
    if (imageFiles.length > 0) {
      const newImages = imageFiles.map(file => ({
        file,
        preview: URL.createObjectURL(file),
        uploading: true,
        url: null,
      }));
      setProductImages(prev => [...prev, ...newImages]);

      for (let i = 0; i < newImages.length; i++) {
        const formData = new FormData();
        formData.append('file', newImages[i].file);
        formData.append('file_type', 'product-media');
        try {
          const res = await fetch(`${API_BASE}/api/v1/ai-clip/upload-product-media`, {
            method: 'POST',
            headers: { "X-Admin-Key": adminKey },
            body: formData,
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || 'Upload failed');
          const readUrl = data.blob_url || data.read_url;
          setProductImages(prev => prev.map(img =>
            img.preview === newImages[i].preview ? { ...img, uploading: false, url: readUrl } : img
          ));
        } catch (e) {
          console.error('Image upload failed:', e);
          setProductImages(prev => prev.map(img =>
            img.preview === newImages[i].preview ? { ...img, uploading: false, url: null, error: e.message } : img
          ));
        }
      }
    }

    // Handle videos (V12)
    if (videoFiles.length > 0) {
      const newVideos = videoFiles.map(file => ({
        file,
        name: file.name,
        uploading: true,
        url: null,
      }));
      setProductVideos(prev => [...prev, ...newVideos]);

      for (let i = 0; i < newVideos.length; i++) {
        const formData = new FormData();
        formData.append('file', newVideos[i].file);
        formData.append('file_type', 'product-video');
        try {
          const res = await fetch(`${API_BASE}/api/v1/ai-clip/upload-product-media`, {
            method: 'POST',
            headers: { "X-Admin-Key": adminKey },
            body: formData,
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || 'Upload failed');
          const readUrl = data.blob_url || data.read_url;
          setProductVideos(prev => prev.map(vid =>
            vid.name === newVideos[i].name ? { ...vid, uploading: false, url: readUrl } : vid
          ));
        } catch (e) {
          console.error('Video upload failed:', e);
          setProductVideos(prev => prev.map(vid =>
            vid.name === newVideos[i].name ? { ...vid, uploading: false, url: null, error: e.message } : vid
          ));
        }
      }
    }
  };

  // Legacy handler for backward compatibility
  const handleImageUpload = handleMediaUpload;

  // Remove image
  const removeImage = (idx) => {
    setProductImages(prev => prev.filter((_, i) => i !== idx));
  };

  // Remove video (V12)
  const removeVideo = (idx) => {
    setProductVideos(prev => prev.filter((_, i) => i !== idx));
  };

  // Start PiP generation
  const startGeneration = async () => {
    const uploadedImageUrls = productImages.filter(img => img.url).map(img => img.url);
    const uploadedVideoUrls = productVideos.filter(v => v.url).map(v => v.url);
    if (uploadedImageUrls.length === 0 && uploadedVideoUrls.length === 0) {
      alert('商品画像または商品動画を1つ以上アップロードしてください');
      return;
    }
    setGenerating(true);
    setJobStatus(null);
    try {
      const body = {
        clip_id: clip.clip_id || clip.id,
        video_mode: "product_overlay",
        subtitle_style: "auto",
        enable_sfx: true,
        enable_transitions: true,
        enable_hook: true,
        enable_silence_cut: true,
        enable_zoom_pulse: true,
        enable_progress_bar: true,
        enable_flash_intro: true,
        enable_loop_fade: true,
        enable_cta: true,
        enable_keyword_highlight: true,
        enable_subtitle_animation: true,
      };
      // V12: 画像と動画の両方を送信
      if (uploadedImageUrls.length > 0) {
        body.product_image_urls = uploadedImageUrls;
      }
      if (uploadedVideoUrls.length > 0) {
        body.product_video_urls = uploadedVideoUrls;
      }
      const res = await fetch(`${API_BASE}/api/v1/ai-clip/generate-from-clip`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Key": adminKey,
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `API error ${res.status}`);
      }
      const data = await res.json();
      setJobId(data.job_id);
      setJobStatus({ status: "queued", progress_pct: 0, current_step: "キューに追加しました" });
    } catch (e) {
      alert(`PiP合成エラー: ${e.message}`);
      setGenerating(false);
    }
  };

  // Poll job status
  useEffect(() => {
    if (!jobId) return;
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/ai-clip/jobs/${jobId}`, {
          headers: { "X-Admin-Key": adminKey },
        });
        if (res.ok) {
          const data = await res.json();
          setJobStatus(data);
          if (data.status === "done" || data.status === "failed") {
            setGenerating(false);
            clearInterval(pollRef.current);
          }
        }
      } catch (e) {
        console.error('Poll error:', e);
      }
    };
    pollRef.current = setInterval(poll, 3000);
    poll();
    return () => clearInterval(pollRef.current);
  }, [jobId]);

  const isDone = jobStatus?.status === "done";
  const isFailed = jobStatus?.status === "failed";
  const isProcessing = generating || (jobStatus && !isDone && !isFailed);
  const resultClip = isDone && jobStatus?.results?.[0];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="relative bg-white rounded-2xl shadow-2xl max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="sticky top-0 z-10 bg-white rounded-t-2xl border-b border-gray-100 px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers className="w-5 h-5 text-purple-500" />
            <h3 className="text-base font-bold text-gray-800">PiP合成 <span className="text-xs bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded-full ml-1">商品オーバーレイ</span></h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Source clip info */}
          <div className="flex gap-3 p-3 bg-gray-50 rounded-xl">
            {clip.thumbnail_url && (
              <img src={clip.thumbnail_url} alt="" className="w-16 h-24 object-cover rounded-lg" />
            )}
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-gray-800 truncate">{clip.product_name || "不明な商品"}</p>
              <p className="text-xs text-gray-500 mt-0.5">{clip.liver_name || "不明"}</p>
              <p className="text-xs text-gray-400 mt-0.5">{clip.duration_sec ? `${Math.round(clip.duration_sec)}秒` : ""}</p>
            </div>
          </div>

          {/* Product image section */}
          {!isProcessing && !isDone && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-purple-700">📦 商品マスターから選択</span>
              </div>
              <div className="relative">
                <input
                  type="text"
                  value={productSearch}
                  onChange={(e) => searchProducts(e.target.value)}
                  onFocus={() => { if (productResults.length > 0) setShowDropdown(true); }}
                  placeholder="商品名・ブランド名で検索..."
                  className="w-full px-3 py-2 text-xs border border-purple-200 rounded-lg focus:outline-none focus:border-purple-400 bg-white"
                />
                {searching && (
                  <span className="absolute right-3 top-2.5 text-[9px] text-gray-400">検索中...</span>
                )}
                {showDropdown && productResults.length > 0 && (
                  <div className="absolute z-50 w-full mt-1 bg-white border border-purple-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                    {productResults.map(p => (
                      <button
                        key={p.id}
                        onClick={() => selectProduct(p)}
                        className="w-full text-left px-3 py-2 hover:bg-purple-50 border-b border-gray-100 last:border-0 flex items-center gap-2"
                      >
                        {p.product_image_urls && p.product_image_urls[0] && (
                          <img src={p.product_image_urls[0]} alt="" className="w-8 h-8 object-cover rounded" />
                        )}
                        <div>
                          <div className="text-xs font-medium text-gray-800">{p.product_name}</div>
                          {p.brand_name && <div className="text-[9px] text-gray-500">{p.brand_name}</div>}
                          <div className="text-[9px] text-purple-500">{p.product_image_urls?.length || 0}枚の画像</div>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Upload area - Images */}
              <div className="border-2 border-dashed border-purple-200 rounded-lg p-3 bg-purple-50/30">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] font-bold text-purple-700">📸 商品画像</span>
                  <label className="cursor-pointer px-2 py-1 bg-purple-600 text-white text-[10px] rounded hover:bg-purple-700 transition-colors">
                    + 画像追加
                    <input
                      type="file"
                      accept="image/*"
                      multiple
                      className="hidden"
                      onChange={(e) => handleMediaUpload(e.target.files)}
                    />
                  </label>
                </div>
                {productImages.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-3 text-gray-400">
                    <span className="text-xl">🖼️</span>
                    <span className="text-[9px] mt-1">商品画像をアップロード（JPG/PNG/WebP）</span>
                  </div>
                ) : (
                  <div className="grid grid-cols-4 gap-2">
                    {productImages.map((img, idx) => (
                      <div key={idx} className="relative group">
                        <img src={img.preview} alt="" className="w-full h-16 object-cover rounded-lg border border-purple-200" />
                        {img.uploading && (
                          <div className="absolute inset-0 bg-white/70 flex items-center justify-center rounded-lg">
                            <Loader2 className="w-4 h-4 animate-spin text-purple-500" />
                          </div>
                        )}
                        {img.error && (
                          <div className="absolute inset-0 bg-red-100/80 flex items-center justify-center rounded-lg">
                            <span className="text-[8px] text-red-600">失敗</span>
                          </div>
                        )}
                        <button
                          onClick={() => removeImage(idx)}
                          className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                          <X className="w-2.5 h-2.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Upload area - Videos (V12) */}
              <div className="border-2 border-dashed border-blue-200 rounded-lg p-3 bg-blue-50/30">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] font-bold text-blue-700">🎬 商品動画（PiPオーバーレイ）</span>
                  <label className="cursor-pointer px-2 py-1 bg-blue-600 text-white text-[10px] rounded hover:bg-blue-700 transition-colors">
                    + 動画追加
                    <input
                      type="file"
                      accept="video/mp4,video/quicktime,video/x-msvideo,video/webm,.mp4,.mov,.avi,.webm,.mkv"
                      multiple
                      className="hidden"
                      onChange={(e) => handleMediaUpload(e.target.files)}
                    />
                  </label>
                </div>
                {productVideos.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-3 text-gray-400">
                    <span className="text-xl">🎬</span>
                    <span className="text-[9px] mt-1">商品動画をアップロード（MP4/MOV/AVI/WebM）</span>
                    <span className="text-[8px] text-gray-300 mt-0.5">短動画: 5-10秒時点で3秒間表示 / ロング: 3秒表示→4秒非表示ループ</span>
                  </div>
                ) : (
                  <div className="space-y-1">
                    {productVideos.map((vid, idx) => (
                      <div key={idx} className="flex items-center gap-2 p-1.5 bg-blue-50 rounded-lg border border-blue-200">
                        <span className="text-lg">🎬</span>
                        <span className="text-[10px] text-blue-800 flex-1 truncate">{vid.name}</span>
                        {vid.uploading && <Loader2 className="w-3 h-3 animate-spin text-blue-500" />}
                        {vid.url && <span className="text-[9px] text-green-600">✓</span>}
                        {vid.error && <span className="text-[8px] text-red-600">失敗</span>}
                        <button
                          onClick={() => removeVideo(idx)}
                          className="w-4 h-4 bg-red-500 text-white rounded-full flex items-center justify-center hover:bg-red-600"
                        >
                          <X className="w-2.5 h-2.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <p className="text-[9px] text-purple-600 bg-purple-50 px-3 py-2 rounded-lg">
                💡 商品画像: メイン表示し配信者は右下ワイプに。商品動画: 右下にPiPオーバーレイ表示。両方登録可。
              </p>

              {/* Generate button */}
              <button
                onClick={startGeneration}
                disabled={productImages.filter(img => img.url).length === 0 && productVideos.filter(v => v.url).length === 0}
                className="w-full py-2.5 rounded-xl bg-purple-500 hover:bg-purple-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold text-sm transition-colors flex items-center justify-center gap-2"
              >
                <Layers className="w-4 h-4" /> PiP合成を開始
              </button>
            </div>
          )}

          {/* Processing status */}
          {isProcessing && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Loader2 className="w-5 h-5 animate-spin text-purple-500" />
                  <span className="text-sm font-medium text-gray-700">PiP合成中...</span>
                </div>
                <span className="text-xs text-gray-500">{jobStatus?.progress_pct || 0}%</span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-2">
                <div
                  className="bg-purple-500 h-2 rounded-full transition-all duration-500"
                  style={{ width: `${jobStatus?.progress_pct || 0}%` }}
                />
              </div>
              <p className="text-xs text-gray-500 text-center">{jobStatus?.current_step || "処理中..."}</p>
            </div>
          )}

          {/* Done */}
          {isDone && resultClip && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-green-600">
                <CheckCircle className="w-5 h-5" />
                <span className="text-sm font-semibold">PiP合成完了！</span>
              </div>
              {resultClip.clip_url && (
                <video
                  src={resultClip.clip_url}
                  controls
                  className="w-full rounded-xl border border-gray-200"
                  style={{ maxHeight: 300 }}
                />
              )}
              {resultClip.clip_url && (
                <a
                  href={resultClip.clip_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-center gap-2 w-full py-2 rounded-lg text-xs font-medium text-purple-600 bg-purple-50 hover:bg-purple-100 transition-colors"
                >
                  <Download className="w-3.5 h-3.5" /> ダウンロード
                </a>
              )}
            </div>
          )}

          {/* Failed */}
          {isFailed && (
            <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-xl">
              <AlertTriangle className="w-4 h-4 text-red-500 flex-shrink-0" />
              <div>
                <p className="text-xs font-medium text-red-700">PiP合成に失敗しました</p>
                <p className="text-[10px] text-red-600 mt-0.5">{jobStatus?.error || "不明なエラー"}</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════
// ─── Playlist Manager Modal ───
// ═══════════════════════════════════════════════
function PlaylistManagerModal({ onClose, adminKey, playlists, onRefresh }) {
  const [newName, setNewName] = useState("");
  const [newColor, setNewColor] = useState("#6366f1");
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState("");
  const [editColor, setEditColor] = useState("");

  const PRESET_COLORS = [
    "#6366f1", "#ef4444", "#f59e0b", "#10b981", "#3b82f6",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#64748b",
  ];

  async function handleCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/clip-db/playlists`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Key": adminKey },
        body: JSON.stringify({ name: newName.trim(), color: newColor }),
      });
      if (!res.ok) throw new Error("作成失敗");
      setNewName("");
      onRefresh();
    } catch (e) {
      alert(e.message);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id, name) {
    if (!confirm(`「${name}」を削除しますか？\n※ クリップとの紐付けも全て解除されます`)) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/clip-db/playlists/${id}`, {
        method: "DELETE",
        headers: { "X-Admin-Key": adminKey },
      });
      if (!res.ok) throw new Error("削除失敗");
      onRefresh();
    } catch (e) {
      alert(e.message);
    }
  }

  async function handleUpdate(id) {
    if (!editName.trim()) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/clip-db/playlists/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Admin-Key": adminKey },
        body: JSON.stringify({ name: editName.trim(), color: editColor }),
      });
      if (!res.ok) throw new Error("更新失敗");
      setEditingId(null);
      onRefresh();
    } catch (e) {
      alert(e.message);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="relative bg-white rounded-2xl shadow-2xl max-w-md w-full mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="sticky top-0 z-10 bg-white rounded-t-2xl border-b border-gray-100 px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <List className="w-5 h-5 text-indigo-500" />
            <h3 className="text-base font-bold text-gray-800">プレイリスト管理</h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Create new playlist */}
          <div className="p-3 bg-gray-50 rounded-xl space-y-2">
            <p className="text-xs font-semibold text-gray-600">新しいプレイリスト</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                placeholder="例: お気に入り、バグあり、本番用..."
                className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <button
                onClick={handleCreate}
                disabled={creating || !newName.trim()}
                className="px-3 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition"
              >
                {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              </button>
            </div>
            <div className="flex gap-1.5 flex-wrap">
              {PRESET_COLORS.map((c) => (
                <button
                  key={c}
                  onClick={() => setNewColor(c)}
                  className={`w-5 h-5 rounded-full border-2 transition ${newColor === c ? "border-gray-800 scale-125" : "border-transparent hover:scale-110"}`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
          </div>

          {/* Existing playlists */}
          <div className="space-y-2">
            {playlists.length === 0 && (
              <p className="text-center text-sm text-gray-400 py-6">プレイリストがありません</p>
            )}
            {playlists.map((pl) => (
              <div key={pl.id} className="flex items-center gap-3 p-3 bg-white border border-gray-200 rounded-xl hover:border-gray-300 transition">
                {editingId === pl.id ? (
                  <div className="flex-1 space-y-2">
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="flex-1 px-2 py-1 text-sm border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      />
                      <button onClick={() => handleUpdate(pl.id)} className="px-2 py-1 bg-indigo-600 text-white text-xs rounded-md hover:bg-indigo-700">保存</button>
                      <button onClick={() => setEditingId(null)} className="px-2 py-1 text-xs text-gray-500 border border-gray-200 rounded-md">取消</button>
                    </div>
                    <div className="flex gap-1.5 flex-wrap">
                      {PRESET_COLORS.map((c) => (
                        <button
                          key={c}
                          onClick={() => setEditColor(c)}
                          className={`w-4 h-4 rounded-full border-2 transition ${editColor === c ? "border-gray-800 scale-125" : "border-transparent hover:scale-110"}`}
                          style={{ backgroundColor: c }}
                        />
                      ))}
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: pl.color || "#6366f1" }} />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-800 truncate">{pl.name}</p>
                      <p className="text-[10px] text-gray-400">{pl.clip_count} クリップ</p>
                    </div>
                    <button
                      onClick={() => { setEditingId(pl.id); setEditName(pl.name); setEditColor(pl.color || "#6366f1"); }}
                      className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-md transition"
                    >
                      <Edit3 className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => handleDelete(pl.id, pl.name)}
                      className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-md transition"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════
// ─── Clip Playlist Popover (add/remove clip from playlists) ───
// ═══════════════════════════════════════════════
function ClipPlaylistPopover({ clipId, clipPlaylists, allPlaylists, adminKey, onUpdate }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(null); // playlist_id being toggled

  const clipPlaylistIds = (clipPlaylists || []).map(p => p.id);

  async function togglePlaylist(playlistId) {
    setLoading(playlistId);
    const isInPlaylist = clipPlaylistIds.includes(playlistId);
    try {
      const url = `${API_BASE}/api/v1/clip-db/playlists/${playlistId}/clips?clip_id=${clipId}`;
      const res = await fetch(url, {
        method: isInPlaylist ? "DELETE" : "POST",
        headers: { "X-Admin-Key": adminKey },
      });
      if (!res.ok) throw new Error("Failed");
      onUpdate();
    } catch (e) {
      console.error("[Playlist] Toggle failed:", e);
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="relative">
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className={`p-1 rounded-md transition ${
          clipPlaylists?.length > 0
            ? "text-indigo-600 bg-indigo-50 hover:bg-indigo-100"
            : "text-gray-400 hover:text-indigo-600 hover:bg-indigo-50"
        }`}
        title="プレイリストに追加/削除"
      >
        <ListPlus className="w-3.5 h-3.5" />
      </button>

      {open && (
        <div
          className="absolute bottom-full right-0 mb-1 w-48 bg-white border border-gray-200 rounded-xl shadow-lg z-30 py-1 max-h-48 overflow-y-auto"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="px-3 py-1.5 text-[10px] font-semibold text-gray-500 border-b border-gray-100">
            プレイリスト
          </div>
          {allPlaylists.length === 0 && (
            <p className="px-3 py-2 text-xs text-gray-400">プレイリストなし</p>
          )}
          {allPlaylists.map((pl) => {
            const isIn = clipPlaylistIds.includes(pl.id);
            const isToggling = loading === pl.id;
            return (
              <button
                key={pl.id}
                onClick={() => togglePlaylist(pl.id)}
                disabled={isToggling}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-gray-50 transition ${isIn ? "font-medium" : ""}`}
              >
                <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: pl.color || "#6366f1" }} />
                <span className="flex-1 text-left truncate">{pl.name}</span>
                {isToggling ? (
                  <Loader2 className="w-3 h-3 animate-spin text-gray-400" />
                ) : isIn ? (
                  <CheckCircle className="w-3.5 h-3.5 text-indigo-600" />
                ) : (
                  <Plus className="w-3 h-3 text-gray-300" />
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
