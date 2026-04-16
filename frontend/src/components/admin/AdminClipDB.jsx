import { useState, useEffect, useRef, useCallback } from "react";
import {
  Search, Filter, Tag, Play, X, BarChart3, ShoppingBag, Users,
  Star, ThumbsUp, ThumbsDown, ArrowUpDown, Database, Sparkles,
  Loader2, RefreshCw, ChevronLeft, ChevronRight, Building2, Plus, Minus,
  Download, Subtitles, Scissors, CheckCircle,
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
function ClipCard({ clip, onPlay, brands, adminKey, onBrandChange }) {
  const [expanded, setExpanded] = useState(false);
  const [showBrandPicker, setShowBrandPicker] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [brandSearch, setBrandSearch] = useState("");
  const tags = clip.tags || clip.sales_psychology_tags || [];
  const assignments = clip.brand_assignments || [];

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
    (b) => !brandSearch || b.name.toLowerCase().includes(brandSearch.toLowerCase())
  );

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden hover:shadow-lg transition-all duration-300 group">
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
        </div>
        {/* Edit/Download status badges - top right */}
        <div className="absolute top-2 right-2 flex flex-col gap-1 items-end">
          {clip.has_subtitle && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-purple-500 text-white shadow flex items-center gap-0.5">
              <Subtitles className="w-2.5 h-2.5" /> 字幕済
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
                    <span className="truncate">{b.name}</span>
                    <span className="text-gray-300 ml-auto flex-shrink-0">({b.clip_count})</span>
                  </button>
                ))}
              </div>
              <div className="border-t border-gray-100 pt-1 pb-0.5">
                <button
                  onClick={() => { setShowBrandPicker(false); setBrandSearch(""); }}
                  className="w-full text-left px-3 py-1 text-[10px] text-gray-400 hover:text-gray-600"
                >
                  閉じる
                </button>
              </div>
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

// ─── Video Player Modal ───
function VideoPlayerModal({ clip, onClose }) {
  if (!clip) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="relative max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="absolute -top-10 right-0 text-white hover:text-gray-300">
          <X className="w-6 h-6" />
        </button>
        <video
          src={clip.clip_url}
          controls
          autoPlay
          className="w-full rounded-xl shadow-2xl"
          style={{ maxHeight: "80vh" }}
        />
        <div className="mt-3 text-white text-sm">
          {clip.product_name && <p className="font-medium">{clip.product_name}</p>}
          {clip.transcript_text && <p className="text-gray-300 text-xs mt-1 max-h-40 overflow-y-auto leading-relaxed">{clip.transcript_text}</p>}
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
  }, [page, sortBy, sortOrder, selectedTag, soldFilter, ratingFilter, selectedBrand]);

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

  const handleBrandChange = useCallback(() => {
    // Reload clips and brands after brand assignment change
    loadClips();
    loadBrands();
  }, [searchQuery, selectedTag, selectedProduct, selectedLiver, selectedBrand, soldFilter, ratingFilter, page, sortBy, sortOrder]);

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

            {(selectedTag || selectedProduct || selectedLiver || selectedBrand || soldFilter !== null || ratingFilter) && (
              <button
                onClick={() => {
                  setSelectedTag(""); setSelectedProduct(""); setSelectedLiver("");
                  setSelectedBrand(""); setSoldFilter(null); setRatingFilter(""); setPage(1);
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

      {/* Stats */}
      {showStats && stats && <StatsOverview stats={stats} />}
      {showStats && stats && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <TopTagsChart tags={stats.top_tags} />
          <TopProductsChart products={stats.top_products} />
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

      {/* Video player modal */}
      {playerClip && (
        <VideoPlayerModal clip={playerClip} onClose={() => setPlayerClip(null)} />
      )}
    </div>
  );
}
