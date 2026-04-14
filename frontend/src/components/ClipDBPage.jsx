import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import VideoService from "../base/services/videoService";
import Sidebar from "./Sidebar";
import {
  Search, Filter, Tag, TrendingUp, Play, Download, ChevronDown, ChevronUp,
  X, BarChart3, ShoppingBag, Users, Calendar, Star, ThumbsUp, ThumbsDown,
  Zap, ArrowUpDown, Database, Sparkles, Eye, Clock, DollarSign, Loader2,
  RefreshCw, ChevronLeft, ChevronRight, Volume2,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

// ─── Helper: API call with auth ───
async function clipDbFetch(path, params = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== "") qs.set(k, v);
  });
  const url = `${API_BASE}/api/v1/clip-db${path}?${qs.toString()}`;
  const token = localStorage.getItem("token") || sessionStorage.getItem("token");
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
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
  // English key aliases (same colors mapped via Japanese label)
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
function formatDate(d) {
  if (!d) return "--";
  return d.replace(/-/g, "/");
}

// ─── Clip Card Component ───
function ClipCard({ clip, onPlay }) {
  const [expanded, setExpanded] = useState(false);
  const tags = clip.tags || clip.sales_psychology_tags || [];

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
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-green-500 text-white shadow">
              SOLD
            </span>
          )}
          {clip.is_sold === false && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-gray-500 text-white shadow">
              未売
            </span>
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
        {/* GMV & CTA */}
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

        {/* Product */}
        {clip.product_name && (
          <div className="flex items-center gap-1 text-xs text-gray-600">
            <ShoppingBag className="w-3 h-3 text-gray-400" />
            <span className="truncate">{clip.product_name}</span>
          </div>
        )}

        {/* Liver */}
        {clip.liver_name && (
          <div className="flex items-center gap-1 text-xs text-gray-500">
            <Users className="w-3 h-3 text-gray-400" />
            <span className="truncate">{clip.liver_name}</span>
          </div>
        )}

        {/* Tags */}
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
          <p className={`text-[11px] text-gray-500 leading-relaxed ${expanded ? "" : "line-clamp-2"}`}>
            {clip.transcript_text}
          </p>
        )}

        {/* Meta row */}
        <div className="flex items-center justify-between text-[10px] text-gray-400 pt-1 border-t border-gray-100">
          <span className="flex items-center gap-0.5">
            <Calendar className="w-3 h-3" />
            {formatDate(clip.stream_date || clip.created_at?.split("T")[0])}
          </span>
          {clip.viewer_count > 0 && (
            <span className="flex items-center gap-0.5">
              <Eye className="w-3 h-3" />
              {clip.viewer_count.toLocaleString()}
            </span>
          )}
        </div>

        {/* Expand toggle */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full text-center text-[10px] text-gray-400 hover:text-gray-600 pt-1"
        >
          {expanded ? <ChevronUp className="w-3 h-3 mx-auto" /> : <ChevronDown className="w-3 h-3 mx-auto" />}
        </button>
      </div>
    </div>
  );
}

// ─── Stats Overview Component ───
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
        <div className="text-xs text-gray-500 mt-1">売れたクリップ</div>
      </div>
      <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
        <div className="text-2xl font-bold text-gray-400">{stats.unsold_clips}</div>
        <div className="text-xs text-gray-500 mt-1">未売クリップ</div>
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
  const maxCount = Math.max(...tags.map(t => t.count));
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1.5">
        <Tag className="w-4 h-4 text-purple-500" />
        トップタグ（売れた理由）
      </h3>
      <div className="space-y-2">
        {tags.slice(0, 10).map((t, i) => {
          const c = getTagColor(t.tag);
          const pct = (t.count / maxCount) * 100;
          return (
            <div key={i} className="flex items-center gap-2">
              <span
                className="text-[11px] font-medium w-24 text-right px-1.5 py-0.5 rounded border shrink-0"
                style={{ backgroundColor: c.bg, color: c.text, borderColor: c.border }}
                title={t.tag}
              >
                {getTagLabel(t.tag)}
              </span>
              <div className="flex-1 h-5 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{ width: `${pct}%`, backgroundColor: c.text + "33" }}
                />
              </div>
              <span className="text-[11px] text-gray-500 w-8 text-right">{t.count}</span>
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
      <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1.5">
        <ShoppingBag className="w-4 h-4 text-blue-500" />
        トップ商品
      </h3>
      <div className="space-y-2">
        {products.slice(0, 8).map((p, i) => (
          <div key={i} className="flex items-center justify-between text-xs py-1.5 border-b border-gray-50 last:border-0">
            <span className="text-gray-700 truncate flex-1">{p.product}</span>
            <span className="text-gray-400 mx-2">{p.count}件</span>
            <span className="font-semibold text-green-600">{formatGMV(p.gmv)}</span>
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
        <button
          onClick={onClose}
          className="absolute -top-10 right-0 text-white hover:text-gray-300 transition"
        >
          <X className="w-6 h-6" />
        </button>
        <div className="rounded-2xl overflow-hidden bg-black shadow-2xl">
          <video
            src={clip.clip_url}
            controls
            autoPlay
            playsInline
            className="w-full aspect-[9/16] max-h-[80vh]"
          />
          <div className="p-3 bg-gray-900 text-white">
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-bold">{clip.product_name || "Unknown"}</span>
              <span className="text-sm font-bold text-green-400">{formatGMV(clip.gmv)}</span>
            </div>
            {clip.transcript_text && (
              <p className="text-[11px] text-gray-400 leading-relaxed">{clip.transcript_text}</p>
            )}
            {(clip.tags || []).length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {clip.tags.map((tag, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded text-[10px] bg-white/10 text-white/80">
                    {getTagLabel(tag)}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main ClipDB Page ───
export default function ClipDBPage() {
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMode, setSearchMode] = useState("structured"); // structured | semantic
  const [selectedTag, setSelectedTag] = useState("");
  const [selectedProduct, setSelectedProduct] = useState("");
  const [selectedLiver, setSelectedLiver] = useState("");
  const [soldFilter, setSoldFilter] = useState(null); // null | true | false
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
  const [playerClip, setPlayerClip] = useState(null);
  const [enriching, setEnriching] = useState(false);
  const [showStats, setShowStats] = useState(true);

  const pageSize = 20;
  const totalPages = Math.ceil(total / pageSize);

  // Load stats and tags on mount
  useEffect(() => {
    loadStats();
    loadTags();
  }, []);

  // Load clips when search params change
  useEffect(() => {
    if (searchMode === "structured") {
      loadClips();
    }
  }, [page, sortBy, sortOrder, selectedTag, soldFilter, ratingFilter]);

  async function loadStats() {
    try {
      const data = await clipDbFetch("/stats");
      setStats(data);
    } catch (e) {
      console.warn("[ClipDB] Failed to load stats:", e);
    }
  }

  async function loadTags() {
    try {
      const data = await clipDbFetch("/tags");
      setAllTags(data.tags || []);
    } catch (e) {
      console.warn("[ClipDB] Failed to load tags:", e);
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
      if (soldFilter !== null) params.is_sold = soldFilter;
      if (ratingFilter) params.rating = ratingFilter;

      const data = await clipDbFetch("/search", params);
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
      const data = await clipDbFetch("/semantic-search", {
        q: searchQuery,
        limit: 20,
      });
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

  async function handleEnrichAll() {
    if (enriching) return;
    setEnriching(true);
    try {
      const token = localStorage.getItem("token") || sessionStorage.getItem("token");
      const res = await fetch(`${API_BASE}/api/v1/clip-db/enrich-all?force=false`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      alert(`エンリッチ完了: ${data.enriched}/${data.total} クリップ`);
      loadClips();
      loadStats();
    } catch (e) {
      alert("エンリッチ失敗: " + e.message);
    } finally {
      setEnriching(false);
    }
  }

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <Sidebar
        videos={[]}
        selectedVideo={null}
        onSelectVideo={() => {}}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      {/* Main content */}
      <div className="flex-1 overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 z-20 bg-white/80 backdrop-blur-md border-b border-gray-200">
          <div className="max-w-7xl mx-auto px-4 py-3">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setSidebarOpen(true)}
                  className="lg:hidden p-2 rounded-lg hover:bg-gray-100"
                >
                  <Database className="w-5 h-5" />
                </button>
                <div>
                  <h1 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                    <Database className="w-5 h-5 text-purple-600" />
                    クリップDB
                  </h1>
                  <p className="text-xs text-gray-500">
                    {total}件のクリップ・「売れる瞬間」を検索
                  </p>
                </div>
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
                  onClick={handleEnrichAll}
                  disabled={enriching}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-600 text-white text-xs font-medium hover:bg-purple-700 disabled:opacity-50 transition"
                  title="全クリップのメタデータを自動付与"
                >
                  {enriching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                  DB更新
                </button>
              </div>
            </div>

            {/* Search bar */}
            <div className="flex gap-2">
              {/* Mode toggle */}
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

              {/* Search input */}
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
              <div className="flex flex-wrap gap-2 mt-3 pb-1">
                {/* Tag filter */}
                <select
                  value={selectedTag}
                  onChange={(e) => { setSelectedTag(e.target.value); setPage(1); }}
                  className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                >
                  <option value="">タグ: すべて</option>
                  {allTags.map((t) => (
                    <option key={t.tag} value={t.tag}>{t.tag} ({t.count})</option>
                  ))}
                </select>

                {/* Sold filter */}
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

                {/* Rating filter */}
                <select
                  value={ratingFilter}
                  onChange={(e) => { setRatingFilter(e.target.value); setPage(1); }}
                  className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                >
                  <option value="">評価: すべて</option>
                  <option value="good">Good</option>
                  <option value="bad">Bad</option>
                </select>

                {/* Sort */}
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

                {/* Product search */}
                <input
                  type="text"
                  value={selectedProduct}
                  onChange={(e) => setSelectedProduct(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                  placeholder="商品名..."
                  className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs w-32 focus:outline-none focus:ring-2 focus:ring-purple-500"
                />

                {/* Liver search */}
                <input
                  type="text"
                  value={selectedLiver}
                  onChange={(e) => setSelectedLiver(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                  placeholder="ライバー名..."
                  className="px-3 py-1.5 rounded-lg border border-gray-300 text-xs w-32 focus:outline-none focus:ring-2 focus:ring-purple-500"
                />

                {/* Clear all */}
                {(selectedTag || selectedProduct || selectedLiver || soldFilter !== null || ratingFilter) && (
                  <button
                    onClick={() => {
                      setSelectedTag(""); setSelectedProduct(""); setSelectedLiver("");
                      setSoldFilter(null); setRatingFilter(""); setPage(1);
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
        </div>

        {/* Content */}
        <div className="max-w-7xl mx-auto px-4 py-6">
          {/* Stats */}
          {showStats && stats && <StatsOverview stats={stats} />}

          {/* Stats charts */}
          {showStats && stats && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
              <TopTagsChart tags={stats.top_tags} />
              <TopProductsChart products={stats.top_products} />
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
                {searchQuery || selectedTag || soldFilter !== null
                  ? "条件に一致するクリップが見つかりませんでした"
                  : "「検索」をクリックしてクリップを表示"}
              </p>
              <p className="text-gray-400 text-xs mt-1">
                まず「DB更新」ボタンで既存クリップのメタデータを付与してください
              </p>
            </div>
          ) : (
            <>
              {/* Results header */}
              <div className="flex items-center justify-between mb-4">
                <span className="text-sm text-gray-600">
                  {total}件中 {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, total)}件
                </span>
              </div>

              {/* Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
                {clips.map((clip) => (
                  <ClipCard
                    key={clip.clip_id}
                    clip={clip}
                    onPlay={setPlayerClip}
                  />
                ))}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2 mt-8">
                  <button
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page === 1}
                    className="p-2 rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-30"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <span className="text-sm text-gray-600">
                    {page} / {totalPages}
                  </span>
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
        </div>
      </div>

      {/* Video player modal */}
      {playerClip && (
        <VideoPlayerModal clip={playerClip} onClose={() => setPlayerClip(null)} />
      )}
    </div>
  );
}
