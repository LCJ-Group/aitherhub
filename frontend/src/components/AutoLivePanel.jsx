/**
 * AutoLivePanel v2 — AI自動ライブ配信コントロールパネル
 * 
 * Features:
 * - Shopee商品データ取得 OR 手動商品追加（画像+テキスト）
 * - 商品なしでも開始可能（雑談モード）
 * - AI自動スピーチの開始/停止/一時停止
 * - 言語・スタイル設定
 * - リアルタイムステータス + 現在のフェーズ表示
 * - 実行中に商品を追加可能
 */
import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Play, Square, Pause, Loader2,
  ShoppingBag, Zap, Sparkles, Plus, X, Image as ImageIcon,
  CheckCircle, AlertCircle, ChevronDown, ChevronUp,
  Settings, MessageCircle, Package, Upload, Camera,
  User, Clock,
} from "lucide-react";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";

const LANGUAGES = [
  { code: "en", label: "English", flag: "🇬🇧" },
  { code: "ja", label: "日本語", flag: "🇯🇵" },
  { code: "zh", label: "中文", flag: "🇨🇳" },
  { code: "th", label: "ไทย", flag: "🇹🇭" },
  { code: "ms", label: "Malay", flag: "🇲🇾" },
];

const STYLES = [
  { id: "professional", label: "Professional", icon: "👔" },
  { id: "casual", label: "Casual", icon: "😊" },
  { id: "energetic", label: "Energetic", icon: "🔥" },
];

const FLOW_PRESETS = [
  { id: "short", label: "Short (30分)", icon: "⚡" },
  { id: "standard", label: "Standard (1時間)", icon: "⏱️" },
  { id: "long", label: "Long (2時間)", icon: "🕐" },
];

const PHASE_LABELS = {
  opening: { label: "開場", color: "text-purple-300", bg: "bg-purple-500/20" },
  chat: { label: "雑談", color: "text-cyan-300", bg: "bg-cyan-500/20" },
  product_intro: { label: "商品紹介", color: "text-amber-300", bg: "bg-amber-500/20" },
  product_deep: { label: "商品詳細", color: "text-orange-300", bg: "bg-orange-500/20" },
  transition: { label: "過渡", color: "text-green-300", bg: "bg-green-500/20" },
  closing: { label: "締め", color: "text-pink-300", bg: "bg-pink-500/20" },
};

export default function AutoLivePanel({ sessionId, isConnected, onStatusChange }) {
  // ── State ──
  const [isAutoMode, setIsAutoMode] = useState(false);
  const [autoStatus, setAutoStatus] = useState(null);
  const [language, setLanguage] = useState("zh");
  const [style, setStyle] = useState("casual");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  
  // Product sources
  const [productSource, setProductSource] = useState("manual"); // "shopee" | "manual" | "none"
  const [showProducts, setShowProducts] = useState(false);
  
  // Shopee products
  const [shopeeProducts, setShopeeProducts] = useState([]);
  const [selectedProductIds, setSelectedProductIds] = useState([]);
  const [isLoadingProducts, setIsLoadingProducts] = useState(false);
  
  // Shopee Livestream session
  const [shopeeLiveSession, setShopeeLiveSession] = useState(null); // { session_id, status }
  const [isCreatingShopeeSession, setIsCreatingShopeeSession] = useState(false);
  const [shopeeSessionTitle, setShopeeSessionTitle] = useState("KYOGOKU Live");
  
  // Manual products
  const [manualProducts, setManualProducts] = useState([]);
  const [showAddProduct, setShowAddProduct] = useState(false);
  const [newProduct, setNewProduct] = useState({ name: "", description: "", price: "", brand: "", image_url: "", notes: "" });
  
  // Settings panel
  const [showSettings, setShowSettings] = useState(false);
  
  // v3: Persona settings
  const [hostName, setHostName] = useState("");
  const [catchphrases, setCatchphrases] = useState([]);
  const [newCatchphrase, setNewCatchphrase] = useState("");
  const [speakingStyle, setSpeakingStyle] = useState("");
  const [expertise, setExpertise] = useState("");
  const [brandStory, setBrandStory] = useState("");
  const [selfIntroduction, setSelfIntroduction] = useState("");
  const [showPersona, setShowPersona] = useState(false);
  const [personas, setPersonas] = useState([]);
  const [selectedPersonaId, setSelectedPersonaId] = useState(null);
  const [isSavingPersona, setIsSavingPersona] = useState(false);
  const [personaSaveMsg, setPersonaSaveMsg] = useState("");
  
  // v3: Flow settings
  const [flowPreset, setFlowPreset] = useState("standard");
  
  // Photo analysis
  const [isAnalyzingPhoto, setIsAnalyzingPhoto] = useState(false);
  const [photoPreview, setPhotoPreview] = useState(null);
  const photoInputRef = useRef(null);
  const photoInputLiveRef = useRef(null);
  
  // Batch photo analysis
  const [batchAnalyzing, setBatchAnalyzing] = useState(false);
  const [batchProgress, setBatchProgress] = useState({ total: 0, done: 0, failed: 0 });
  const batchPhotoInputRef = useRef(null);
  const batchPhotoInputLiveRef = useRef(null);
  
  // Status polling
  const statusIntervalRef = useRef(null);

  // ── Load Shopee Products (with localStorage cache) ──
  const SHOPEE_PRODUCTS_CACHE_KEY = "aitherhub_shopee_products";

  const loadShopeeProducts = useCallback(async () => {
    setIsLoadingProducts(true);
    setError("");
    try {
      const data = await aiLiveCreatorService.shopeeGetProducts();
      // Check for Shopee API token errors
      if (data?.error && data.error.includes("token")) {
        console.warn("[AutoLive] Shopee token error:", data.error);
        // Try to load from cache
        const cached = localStorage.getItem(SHOPEE_PRODUCTS_CACHE_KEY);
        if (cached) {
          try {
            const cachedData = JSON.parse(cached);
            if (cachedData.items?.length > 0) {
              setShopeeProducts(cachedData.items);
              setSelectedProductIds(cachedData.items.map(p => p.item_id));
              setError("Shopeeトークン期限切れ（キャッシュデータを表示中）。Shopee Partner Centerで再認証が必要です。");
              return;
            }
          } catch (e) { /* ignore parse error */ }
        }
        setError("Shopeeトークンが期限切れです。Shopee Partner Centerで再認証してください。");
        return;
      }
      if (data?.items?.length > 0) {
        setShopeeProducts(data.items);
        setSelectedProductIds(data.items.map(p => p.item_id));
        // Cache to localStorage for offline/token-expired fallback
        try {
          localStorage.setItem(SHOPEE_PRODUCTS_CACHE_KEY, JSON.stringify({
            items: data.items,
            cached_at: new Date().toISOString(),
          }));
          console.log(`[AutoLive] Cached ${data.items.length} Shopee products to localStorage`);
        } catch (e) { /* ignore storage quota errors */ }
      } else if (data?.items?.length === 0 && !data?.error) {
        setError("Shopeeに商品が登録されていません。");
      }
    } catch (err) {
      console.error("[AutoLive] Failed to load products:", err);
      // Try to load from cache on network error
      const cached = localStorage.getItem(SHOPEE_PRODUCTS_CACHE_KEY);
      if (cached) {
        try {
          const cachedData = JSON.parse(cached);
          if (cachedData.items?.length > 0) {
            setShopeeProducts(cachedData.items);
            setSelectedProductIds(cachedData.items.map(p => p.item_id));
            setError(`Shopee API接続エラー（キャッシュデータを表示中: ${cachedData.cached_at || "不明"}）`);
            return;
          }
        } catch (e) { /* ignore parse error */ }
      }
      setError("Shopee APIへの接続に失敗しました。ネットワークを確認してください。");
    } finally {
      setIsLoadingProducts(false);
    }
  }, []);

  // ── Shopee Livestream Session Management ──
  const handleCreateShopeeSession = async () => {
    setIsCreatingShopeeSession(true);
    setError("");
    try {
      const result = await aiLiveCreatorService.shopeeCreateLiveSession({
        title: shopeeSessionTitle || "KYOGOKU Live",
      });
      if (result?.session_id) {
        setShopeeLiveSession({ session_id: result.session_id, status: "created" });
        console.log("[AutoLive] Shopee session created:", result.session_id);
      } else {
        setError("Shopeeセッション作成に失敗しました");
      }
    } catch (err) {
      console.error("[AutoLive] Failed to create Shopee session:", err);
      setError(err.response?.data?.detail || "Shopeeセッション作成に失敗しました");
    } finally {
      setIsCreatingShopeeSession(false);
    }
  };

  const handleStartShopeeSession = async () => {
    if (!shopeeLiveSession?.session_id) return;
    try {
      await aiLiveCreatorService.shopeeStartLiveSession(shopeeLiveSession.session_id);
      setShopeeLiveSession(prev => ({ ...prev, status: "live" }));
      console.log("[AutoLive] Shopee session started");
    } catch (err) {
      console.error("[AutoLive] Failed to start Shopee session:", err);
      setError(err.response?.data?.detail || "Shopeeセッション開始に失敗しました");
    }
  };

  const handleEndShopeeSession = async () => {
    if (!shopeeLiveSession?.session_id) return;
    try {
      await aiLiveCreatorService.shopeeEndLiveSession(shopeeLiveSession.session_id);
      setShopeeLiveSession(null);
      console.log("[AutoLive] Shopee session ended");
    } catch (err) {
      console.error("[AutoLive] Failed to end Shopee session:", err);
    }
  };

  // ── Analyze Product Photo ──
  const handlePhotoSelect = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    // Show preview
    const previewUrl = URL.createObjectURL(file);
    setPhotoPreview(previewUrl);
    setShowAddProduct(true);
    setIsAnalyzingPhoto(true);
    setError("");
    
    try {
      const result = await aiLiveCreatorService.analyzeProductImage(file);
      if (result?.success && result?.product) {
        const p = result.product;
        setNewProduct(prev => ({
          ...prev,
          name: p.name || prev.name,
          description: p.description || prev.description,
          price: p.price || prev.price,
          brand: p.brand || prev.brand,
          notes: p.notes || prev.notes,
          image_url: previewUrl, // Use photo preview as thumbnail
        }));
      } else {
        setError("写真から商品情報を読み取れませんでした。手動で入力してください。");
      }
    } catch (err) {
      console.error("[AutoLive] Photo analysis failed:", err);
      setError("写真解析に失敗しました。手動で入力してください。");
    } finally {
      setIsAnalyzingPhoto(false);
      // Reset file input so same file can be selected again
      if (photoInputRef.current) photoInputRef.current.value = "";
      if (photoInputLiveRef.current) photoInputLiveRef.current.value = "";
    }
  };

  // ── Batch Photo Analysis (multiple photos at once) ──
  const handleBatchPhotoSelect = async (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    
    // Reset file inputs
    if (batchPhotoInputRef.current) batchPhotoInputRef.current.value = "";
    if (batchPhotoInputLiveRef.current) batchPhotoInputLiveRef.current.value = "";
    
    setBatchAnalyzing(true);
    setBatchProgress({ total: files.length, done: 0, failed: 0 });
    setError("");
    
    // Create preview URLs for each file
    const previewUrls = files.map(file => URL.createObjectURL(file));
    
    // Process all photos in parallel
    const results = await Promise.allSettled(
      files.map(async (file, index) => {
        const result = await aiLiveCreatorService.analyzeProductImage(file);
        if (result?.success && result?.product) {
          return { ...result.product, _previewUrl: previewUrls[index] };
        }
        throw new Error("Analysis failed");
      })
    );
    
    let doneCount = 0;
    let failedCount = 0;
    
    for (const result of results) {
      if (result.status === "fulfilled" && result.value) {
        const p = result.value;
        const product = {
          item_id: Date.now() + Math.random(),
          item_name: (p.name || "").trim(),
          description: (p.description || "").trim(),
          price: (p.price || "").trim(),
          brand: (p.brand || "").trim(),
          image_url: p._previewUrl || "", // Use photo preview as thumbnail
          custom_notes: (p.notes || "").trim(),
        };
        
        if (product.item_name) {
          if (isAutoMode && sessionId) {
            // During live: add directly to running session
            try {
              await aiLiveCreatorService.autoLiveAddProduct({
                session_id: sessionId,
                item_name: product.item_name,
                description: product.description,
                price: product.price,
                brand: product.brand,
                image_url: product.image_url,
                custom_notes: product.custom_notes,
              });
              doneCount++;
            } catch {
              failedCount++;
            }
          } else {
            // Before live: add to manual products list
            setManualProducts(prev => [...prev, product]);
            doneCount++;
          }
        } else {
          failedCount++;
        }
      } else {
        failedCount++;
      }
    }
    
    setBatchProgress({ total: files.length, done: doneCount, failed: failedCount });
    
    if (failedCount > 0) {
      setError(`${files.length}枚中${failedCount}枚の解析に失敗しました`);
    }
    
    // Auto-hide progress after 3 seconds
    setTimeout(() => {
      setBatchAnalyzing(false);
      setBatchProgress({ total: 0, done: 0, failed: 0 });
    }, 3000);
  };

  // ── Add Manual Product ──
  const handleAddManualProduct = () => {
    if (!newProduct.name.trim()) return;
    const product = {
      item_id: Date.now(),
      item_name: newProduct.name.trim(),
      description: newProduct.description.trim(),
      price: newProduct.price.trim(),
      brand: newProduct.brand.trim(),
      image_url: newProduct.image_url.trim(),
      custom_notes: newProduct.notes.trim(),
    };
    setManualProducts(prev => [...prev, product]);
    setNewProduct({ name: "", description: "", price: "", brand: "", image_url: "", notes: "" });
    setPhotoPreview(null);
    setShowAddProduct(false);
  };

  const removeManualProduct = (id) => {
    setManualProducts(prev => prev.filter(p => p.item_id !== id));
  };

  // ── Add Product During Live ──
  const handleAddProductDuringLive = async () => {
    if (!newProduct.name.trim() || !sessionId) return;
    try {
      await aiLiveCreatorService.autoLiveAddProduct({
        session_id: sessionId,
        item_name: newProduct.name.trim(),
        description: newProduct.description.trim(),
        price: newProduct.price.trim(),
        brand: newProduct.brand.trim(),
        image_url: newProduct.image_url.trim(),
        custom_notes: newProduct.notes.trim(),
      });
      setNewProduct({ name: "", description: "", price: "", brand: "", image_url: "", notes: "" });
      setPhotoPreview(null);
      setShowAddProduct(false);
    } catch (err) {
      console.error("[AutoLive] Failed to add product:", err);
      setError("Failed to add product");
    }
  };

  // ── Start Auto Live ──
  const handleStart = async () => {
    if (!sessionId) {
      setError("ストリーミングを先に開始してください");
      return;
    }
    setIsLoading(true);
    setError("");
    try {
      const params = {
        session_id: sessionId,
        language,
        style,
        // v3: Persona settings
        host_name: hostName,
        catchphrases: catchphrases.length > 0 ? catchphrases : undefined,
        speaking_style: speakingStyle,
        expertise,
        brand_story: brandStory,
        self_introduction: selfIntroduction,
        // v3: Flow preset
        flow_preset: flowPreset,
      };

      if (productSource === "shopee" && selectedProductIds.length > 0) {
        const selected = shopeeProducts.filter(p => selectedProductIds.includes(p.item_id));
        if (selected.length > 0) {
          params.products_manual = selected.map(p => ({
            item_id: p.item_id,
            item_name: p.item_name || p.name,
            description: p.description || "",
            price: p.price_info?.current_price || p.price || "",
            brand: p.brand || "KYOGOKU",
            sales: p.sales || 0,
            rating: p.rating || 0,
            attributes: p.attributes || [],
            models: p.models || [],
          }));
        }
        // Pass Shopee Livestream session ID for comment monitoring
        if (shopeeLiveSession?.session_id) {
          params.shopee_session_id = shopeeLiveSession.session_id;
          // Auto-start Shopee session if not yet live
          if (shopeeLiveSession.status !== "live") {
            try {
              await aiLiveCreatorService.shopeeStartLiveSession(shopeeLiveSession.session_id);
              setShopeeLiveSession(prev => ({ ...prev, status: "live" }));
            } catch (e) {
              console.warn("[AutoLive] Could not auto-start Shopee session:", e);
            }
          }
        }
      } else if (productSource === "manual" && manualProducts.length > 0) {
        params.products_manual = manualProducts;
        params.skip_shopee = true;
      } else {
        // Chat-only mode
        params.skip_shopee = true;
      }

      const result = await aiLiveCreatorService.autoLiveStart(params);
      setAutoStatus(result);
      setIsAutoMode(true);
      startStatusPolling();
      onStatusChange?.("running");
    } catch (err) {
      console.error("[AutoLive] Failed to start:", err);
      setError(err.response?.data?.detail || "自動配信の開始に失敗しました");
    } finally {
      setIsLoading(false);
    }
  };

  // ── Stop Auto Live ──
  const handleStop = async () => {
    setIsLoading(true);
    try {
      const result = await aiLiveCreatorService.autoLiveStop(sessionId);
      setAutoStatus(result);
      setIsAutoMode(false);
      stopStatusPolling();
      onStatusChange?.("stopped");
      // End Shopee Livestream session if active
      if (shopeeLiveSession?.session_id && shopeeLiveSession.status === "live") {
        try {
          await aiLiveCreatorService.shopeeEndLiveSession(shopeeLiveSession.session_id);
          setShopeeLiveSession(null);
          console.log("[AutoLive] Shopee session ended with Auto Live stop");
        } catch (e) {
          console.warn("[AutoLive] Could not end Shopee session:", e);
        }
      }
    } catch (err) {
      console.error("[AutoLive] Failed to stop:", err);
    } finally {
      setIsLoading(false);
    }
  };

  // ── Pause/Resume ──
  const handlePause = async () => {
    try {
      if (autoStatus?.status === "paused") {
        await aiLiveCreatorService.autoLiveResume(sessionId);
      } else {
        await aiLiveCreatorService.autoLivePause(sessionId);
      }
    } catch (err) {
      console.error("[AutoLive] Pause/Resume failed:", err);
    }
  };

  // ── Status Polling ──
  const startStatusPolling = () => {
    stopStatusPolling();
    statusIntervalRef.current = setInterval(async () => {
      try {
        const status = await aiLiveCreatorService.autoLiveStatus(sessionId);
        setAutoStatus(status);
        if (status?.status === "not_running") {
          setIsAutoMode(false);
          stopStatusPolling();
        }
      } catch (err) { /* ignore */ }
    }, 3000);
  };

  const stopStatusPolling = () => {
    if (statusIntervalRef.current) {
      clearInterval(statusIntervalRef.current);
      statusIntervalRef.current = null;
    }
  };

  useEffect(() => {
    return () => stopStatusPolling();
  }, []);

  // ── Restore Shopee products from localStorage cache on mount ──
  useEffect(() => {
    if (productSource === "shopee" && shopeeProducts.length === 0) {
      const cached = localStorage.getItem(SHOPEE_PRODUCTS_CACHE_KEY);
      if (cached) {
        try {
          const cachedData = JSON.parse(cached);
          if (cachedData.items?.length > 0) {
            setShopeeProducts(cachedData.items);
            setSelectedProductIds(cachedData.items.map(p => p.item_id));
            console.log(`[AutoLive] Restored ${cachedData.items.length} Shopee products from cache (${cachedData.cached_at || "unknown"})`);
          }
        } catch (e) { /* ignore parse error */ }
      }
    }
  }, [productSource]);

  // ── Load Personas on mount ──
  useEffect(() => {
    (async () => {
      try {
        const data = await aiLiveCreatorService.getPersonas();
        if (data?.personas) {
          setPersonas(data.personas);
          // Auto-select first persona and load its config
          if (data.personas.length > 0) {
            const first = data.personas[0];
            setSelectedPersonaId(first.id);
            loadPersonaConfig(first.id);
          }
        }
      } catch (err) {
        console.error("[AutoLive] Failed to load personas:", err);
      }
    })();
  }, []);

  // ── Load persona config from backend ──
  const loadPersonaConfig = async (personaId) => {
    try {
      const data = await aiLiveCreatorService.getPersona(personaId);
      const config = data?.persona?.live_persona_config;
      if (config) {
        if (typeof config === "string") {
          try { var parsed = JSON.parse(config); } catch { return; }
        } else {
          var parsed = config;
        }
        setHostName(parsed.host_name || "");
        setCatchphrases(parsed.catchphrases || []);
        setSpeakingStyle(parsed.speaking_style || "");
        setExpertise(parsed.expertise || "");
        setBrandStory(parsed.brand_story || "");
        setSelfIntroduction(parsed.self_introduction || "");
        setPersonaSaveMsg("");
      }
    } catch (err) {
      console.error("[AutoLive] Failed to load persona config:", err);
    }
  };

  // ── Save persona config to backend ──
  const savePersonaConfig = async () => {
    if (!selectedPersonaId) {
      setPersonaSaveMsg("⚠ ペルソナを選択してください");
      return;
    }
    setIsSavingPersona(true);
    setPersonaSaveMsg("");
    try {
      await aiLiveCreatorService.updatePersona(selectedPersonaId, {
        live_persona_config: {
          host_name: hostName,
          catchphrases,
          speaking_style: speakingStyle,
          expertise,
          brand_story: brandStory,
          self_introduction: selfIntroduction,
        },
      });
      setPersonaSaveMsg("✅ 保存しました");
      setTimeout(() => setPersonaSaveMsg(""), 3000);
    } catch (err) {
      console.error("[AutoLive] Failed to save persona config:", err);
      setPersonaSaveMsg("❌ 保存に失敗しました");
    } finally {
      setIsSavingPersona(false);
    }
  };

  // ── Product Selection Toggle ──
  const toggleProduct = (itemId) => {
    setSelectedProductIds(prev => 
      prev.includes(itemId) ? prev.filter(id => id !== itemId) : [...prev, itemId]
    );
  };

  // ── Product count for display ──
  const totalProducts = productSource === "shopee" ? selectedProductIds.length : manualProducts.length;

  // ── Render ──
  return (
    <div className="bg-gray-800/50 rounded-xl border border-amber-500/30 p-3">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-[11px] font-medium text-amber-300 flex items-center gap-1.5">
          <Zap className="w-3.5 h-3.5 text-amber-400" />
          AI Auto Live
          {isAutoMode && (
            <span className={`ml-1 text-[9px] px-1.5 py-0.5 rounded-full ${
              autoStatus?.status === "paused" 
                ? "bg-yellow-500/20 text-yellow-300" 
                : "bg-green-500/20 text-green-300 animate-pulse"
            }`}>
              {autoStatus?.status === "paused" ? "PAUSED" : "AUTO"}
            </span>
          )}
          {isAutoMode && autoStatus?.current_phase && (
            <span className={`text-[8px] px-1.5 py-0.5 rounded-full ${PHASE_LABELS[autoStatus.current_phase]?.bg || "bg-gray-500/20"} ${PHASE_LABELS[autoStatus.current_phase]?.color || "text-gray-300"}`}>
              {PHASE_LABELS[autoStatus.current_phase]?.label || autoStatus.current_phase}
            </span>
          )}
        </h4>
        <button
          onClick={() => setShowSettings(!showSettings)}
          className="text-gray-400 hover:text-gray-300 transition-colors"
        >
          <Settings className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-2 p-2 bg-red-900/20 border border-red-500/30 rounded-lg text-[9px] text-red-300 flex items-start gap-1.5">
          <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
          {error}
          <button onClick={() => setError("")} className="ml-auto"><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Settings Panel (collapsible) */}
      {showSettings && (
        <div className="mb-2 p-2 bg-gray-900/50 rounded-lg border border-gray-700/30 space-y-2">
          {/* Language */}
          <div>
            <label className="text-[9px] text-gray-500 block mb-1">Language</label>
            <div className="flex gap-1 flex-wrap">
              {LANGUAGES.map(l => (
                <button
                  key={l.code}
                  onClick={() => setLanguage(l.code)}
                  className={`px-2 py-1 rounded text-[9px] border transition-colors ${
                    language === l.code
                      ? "bg-amber-500/20 border-amber-500/50 text-amber-300"
                      : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  {l.flag} {l.label}
                </button>
              ))}
            </div>
          </div>

          {/* Style */}
          <div>
            <label className="text-[9px] text-gray-500 block mb-1">Style</label>
            <div className="flex gap-1">
              {STYLES.map(s => (
                <button
                  key={s.id}
                  onClick={() => setStyle(s.id)}
                  className={`flex-1 px-2 py-1.5 rounded text-[9px] border transition-colors text-center ${
                    style === s.id
                      ? "bg-amber-500/20 border-amber-500/50 text-amber-300"
                      : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  {s.icon} {s.label}
                </button>
              ))}
            </div>
          </div>

          {/* Flow Preset */}
          <div>
            <label className="text-[9px] text-gray-500 block mb-1">
              <Clock className="w-3 h-3 inline mr-1" />Flow Preset
            </label>
            <div className="flex gap-1">
              {FLOW_PRESETS.map(f => (
                <button
                  key={f.id}
                  onClick={() => setFlowPreset(f.id)}
                  className={`flex-1 px-2 py-1.5 rounded text-[9px] border transition-colors text-center ${
                    flowPreset === f.id
                      ? "bg-violet-500/20 border-violet-500/50 text-violet-300"
                      : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  {f.icon} {f.label}
                </button>
              ))}
            </div>
          </div>

          {/* Persona Toggle */}
          <button
            onClick={() => setShowPersona(!showPersona)}
            className="w-full flex items-center justify-between px-2 py-1.5 rounded text-[9px] border border-gray-700 bg-gray-800 text-gray-300 hover:border-gray-600 transition-colors"
          >
            <span className="flex items-center gap-1">
              <User className="w-3 h-3" />
              ライバーペルソナ設定
              {hostName && <span className="text-amber-400 ml-1">• {hostName}</span>}
              {catchphrases.length > 0 && <span className="text-violet-400 ml-1">• 口癖{catchphrases.length}件</span>}
            </span>
            {showPersona ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>

          {/* Persona Settings (collapsible) */}
          {showPersona && (
            <div className="space-y-2 p-2 bg-gray-800/50 rounded-lg border border-gray-700/30">
              {/* Persona Selector */}
              {personas.length > 0 && (
                <div>
                  <label className="text-[9px] text-gray-500 block mb-1">ペルソナ選択</label>
                  <select
                    value={selectedPersonaId || ""}
                    onChange={e => {
                      const id = parseInt(e.target.value);
                      setSelectedPersonaId(id);
                      loadPersonaConfig(id);
                    }}
                    className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-[9px] text-gray-200 focus:border-amber-500/50 focus:outline-none"
                  >
                    {personas.map(p => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </div>
              )}

              {/* Host Name */}
              <div>
                <label className="text-[9px] text-gray-500 block mb-1">ライバー名</label>
                <input
                  type="text"
                  value={hostName}
                  onChange={e => setHostName(e.target.value)}
                  placeholder="例: 京極 琉"
                  className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-600 focus:border-amber-500/50 focus:outline-none"
                />
              </div>

              {/* Catchphrases */}
              <div>
                <label className="text-[9px] text-gray-500 block mb-1">口癖・決まり文句</label>
                <div className="flex gap-1 mb-1">
                  <input
                    type="text"
                    value={newCatchphrase}
                    onChange={e => setNewCatchphrase(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === "Enter" && newCatchphrase.trim()) {
                        setCatchphrases(prev => [...prev, newCatchphrase.trim()]);
                        setNewCatchphrase("");
                      }
                    }}
                    placeholder="例: めっちゃいいよね"
                    className="flex-1 px-2 py-1 bg-gray-900 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-600 focus:border-amber-500/50 focus:outline-none"
                  />
                  <button
                    onClick={() => {
                      if (newCatchphrase.trim()) {
                        setCatchphrases(prev => [...prev, newCatchphrase.trim()]);
                        setNewCatchphrase("");
                      }
                    }}
                    className="px-2 py-1 bg-amber-500/20 border border-amber-500/50 rounded text-[9px] text-amber-300 hover:bg-amber-500/30"
                  >
                    <Plus className="w-3 h-3" />
                  </button>
                </div>
                {catchphrases.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {catchphrases.map((phrase, i) => (
                      <span key={i} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 bg-violet-500/20 border border-violet-500/30 rounded text-[8px] text-violet-300">
                        「{phrase}」
                        <button onClick={() => setCatchphrases(prev => prev.filter((_, j) => j !== i))}>
                          <X className="w-2.5 h-2.5" />
                        </button>
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Speaking Style */}
              <div>
                <label className="text-[9px] text-gray-500 block mb-1">話し方の特徴</label>
                <input
                  type="text"
                  value={speakingStyle}
                  onChange={e => setSpeakingStyle(e.target.value)}
                  placeholder="例: 語尾に〜よねを多用、テンション高め"
                  className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-600 focus:border-amber-500/50 focus:outline-none"
                />
              </div>

              {/* Expertise */}
              <div>
                <label className="text-[9px] text-gray-500 block mb-1">専門分野・経歴</label>
                <input
                  type="text"
                  value={expertise}
                  onChange={e => setExpertise(e.target.value)}
                  placeholder="例: 美容師歴15年、ヘアケア専門"
                  className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-600 focus:border-amber-500/50 focus:outline-none"
                />
              </div>

              {/* Brand Story */}
              <div>
                <label className="text-[9px] text-gray-500 block mb-1">ブランドストーリー</label>
                <textarea
                  value={brandStory}
                  onChange={e => setBrandStory(e.target.value)}
                  placeholder="例: KYOGOKUは京極琉が創設したプロ仕様のヘアケアブランドで..."
                  rows={2}
                  className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-600 focus:border-amber-500/50 focus:outline-none resize-none"
                />
              </div>

              {/* Self Introduction */}
              <div>
                <label className="text-[9px] text-gray-500 block mb-1">オープニング自己紹介</label>
                <textarea
                  value={selfIntroduction}
                  onChange={e => setSelfIntroduction(e.target.value)}
                  placeholder="例: 皆さんこんにちは！京極琉です。今日も素敵な商品を..."
                  rows={2}
                  className="w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-600 focus:border-amber-500/50 focus:outline-none resize-none"
                />
              </div>

              {/* Save Button */}
              <button
                onClick={savePersonaConfig}
                disabled={isSavingPersona || !selectedPersonaId}
                className="w-full flex items-center justify-center gap-1 px-2 py-1.5 rounded text-[9px] font-medium border transition-colors bg-amber-500/20 border-amber-500/50 text-amber-300 hover:bg-amber-500/30 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSavingPersona ? (
                  <><Loader2 className="w-3 h-3 animate-spin" /> 保存中...</>
                ) : (
                  <>💾 ペルソナ設定を保存</>
                )}
              </button>
              {personaSaveMsg && (
                <p className="text-[9px] text-center">{personaSaveMsg}</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Product Source Tabs (only when not running) */}
      {!isAutoMode && (
        <div className="mb-2">
          <div className="flex gap-1 mb-2">
            {[
              { id: "manual", label: "手動追加", icon: <Plus className="w-3 h-3" /> },
              { id: "shopee", label: "Shopee", icon: <ShoppingBag className="w-3 h-3" /> },
              { id: "none", label: "雑談のみ", icon: <MessageCircle className="w-3 h-3" /> },
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setProductSource(tab.id)}
                className={`flex-1 flex items-center justify-center gap-1 px-2 py-1.5 rounded text-[9px] border transition-colors ${
                  productSource === tab.id
                    ? "bg-amber-500/20 border-amber-500/50 text-amber-300"
                    : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600"
                }`}
              >
                {tab.icon} {tab.label}
              </button>
            ))}
          </div>

          {/* Manual Products */}
          {productSource === "manual" && (
            <div className="space-y-1.5">
              {manualProducts.map(p => (
                <div key={p.item_id} className="flex items-center gap-2 p-1.5 bg-gray-900/50 rounded-lg border border-gray-700/30">
                  {p.image_url ? (
                    <img src={p.image_url} alt="" className="w-8 h-8 rounded object-cover flex-shrink-0" />
                  ) : (
                    <div className="w-8 h-8 rounded bg-gray-700 flex items-center justify-center flex-shrink-0">
                      <Package className="w-4 h-4 text-gray-500" />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-[9px] text-gray-200 truncate">{p.item_name}</p>
                    {p.price && <p className="text-[8px] text-amber-400">{p.price}</p>}
                  </div>
                  <button onClick={() => removeManualProduct(p.item_id)} className="text-gray-500 hover:text-red-400">
                    <X className="w-3 h-3" />
                  </button>
                </div>
              ))}

              {/* Add Product Form */}
              {showAddProduct ? (
                <div className="p-2 bg-gray-900/50 rounded-lg border border-amber-500/30 space-y-1.5">
                  {/* Photo preview & analyzing indicator */}
                  {(photoPreview || isAnalyzingPhoto) && (
                    <div className="flex items-center gap-2 p-1.5 bg-gray-800/50 rounded-lg border border-gray-700/30">
                      {photoPreview && (
                        <img src={photoPreview} alt="" className="w-12 h-12 rounded object-cover flex-shrink-0" />
                      )}
                      {isAnalyzingPhoto ? (
                        <div className="flex items-center gap-1.5 text-[9px] text-cyan-300">
                          <Loader2 className="w-3 h-3 animate-spin" />
                          AIが写真を解析中...
                        </div>
                      ) : error ? (
                        <p className="text-[8px] text-red-400 flex items-center gap-1">
                          <AlertCircle className="w-3 h-3" /> 解析失敗。手動で入力してください。
                        </p>
                      ) : (
                        <p className="text-[8px] text-green-400 flex items-center gap-1">
                          <CheckCircle className="w-3 h-3" /> 解析完了。内容を確認・編集してください。
                        </p>
                      )}
                    </div>
                  )}
                  <input
                    type="text"
                    placeholder="商品名 *"
                    value={newProduct.name}
                    onChange={e => setNewProduct(prev => ({ ...prev, name: e.target.value }))}
                    className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none"
                  />
                  <textarea
                    placeholder="商品説明（特徴、成分、効果など）"
                    value={newProduct.description}
                    onChange={e => setNewProduct(prev => ({ ...prev, description: e.target.value }))}
                    rows={2}
                    className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none resize-none"
                  />
                  <div className="flex gap-1.5">
                    <input
                      type="text"
                      placeholder="価格"
                      value={newProduct.price}
                      onChange={e => setNewProduct(prev => ({ ...prev, price: e.target.value }))}
                      className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none"
                    />
                    <input
                      type="text"
                      placeholder="ブランド"
                      value={newProduct.brand}
                      onChange={e => setNewProduct(prev => ({ ...prev, brand: e.target.value }))}
                      className="flex-1 px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none"
                    />
                  </div>
                  <input
                    type="text"
                    placeholder="画像URL（任意）"
                    value={newProduct.image_url}
                    onChange={e => setNewProduct(prev => ({ ...prev, image_url: e.target.value }))}
                    className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none"
                  />
                  <textarea
                    placeholder="追加メモ（セールスポイント、話してほしいことなど）"
                    value={newProduct.notes}
                    onChange={e => setNewProduct(prev => ({ ...prev, notes: e.target.value }))}
                    rows={2}
                    className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none resize-none"
                  />
                  <div className="flex gap-1.5">
                    <button
                      onClick={isAutoMode ? handleAddProductDuringLive : handleAddManualProduct}
                      disabled={!newProduct.name.trim() || isAnalyzingPhoto}
                      className="flex-1 py-1.5 bg-amber-500/20 border border-amber-500/50 rounded text-[9px] text-amber-300 hover:bg-amber-500/30 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1"
                    >
                      <Plus className="w-3 h-3" /> 追加
                    </button>
                    <button
                      onClick={() => { setShowAddProduct(false); setPhotoPreview(null); }}
                      className="px-3 py-1.5 bg-gray-700/50 border border-gray-600 rounded text-[9px] text-gray-400 hover:bg-gray-700"
                    >
                      キャンセル
                    </button>
                  </div>
                </div>
              ) : (
                <div className="space-y-1.5">
                  {/* Batch progress indicator */}
                  {batchAnalyzing && (
                    <div className="flex items-center gap-2 p-2 bg-cyan-900/20 border border-cyan-500/30 rounded-lg">
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-cyan-400" />
                      <div className="flex-1">
                        <p className="text-[9px] text-cyan-300">
                          {batchProgress.done + batchProgress.failed < batchProgress.total
                            ? `${batchProgress.total}枚を解析中... (${batchProgress.done + batchProgress.failed}/${batchProgress.total})`
                            : `完了！ ${batchProgress.done}枚追加${batchProgress.failed > 0 ? `、${batchProgress.failed}枚失敗` : ""}`
                          }
                        </p>
                        <div className="mt-1 h-1 bg-gray-700 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-cyan-400 rounded-full transition-all duration-300"
                            style={{ width: `${batchProgress.total > 0 ? ((batchProgress.done + batchProgress.failed) / batchProgress.total * 100) : 0}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  )}
                  <div className="flex gap-1.5">
                    <input
                      ref={photoInputRef}
                      type="file"
                      accept="image/*"
                      className="hidden"
                      onChange={handlePhotoSelect}
                    />
                    <input
                      ref={batchPhotoInputRef}
                      type="file"
                      accept="image/*"
                      multiple
                      className="hidden"
                      onChange={handleBatchPhotoSelect}
                    />
                    <button
                      onClick={() => photoInputRef.current?.click()}
                      disabled={isAnalyzingPhoto || batchAnalyzing}
                      className="flex-1 py-2 border-2 border-dashed border-cyan-600/50 rounded-lg text-[9px] text-cyan-400 hover:border-cyan-400 hover:text-cyan-300 transition-colors flex items-center justify-center gap-1.5"
                    >
                      <Camera className="w-3.5 h-3.5" /> 1枚読み込み
                    </button>
                    <button
                      onClick={() => batchPhotoInputRef.current?.click()}
                      disabled={isAnalyzingPhoto || batchAnalyzing}
                      className="flex-1 py-2 border-2 border-dashed border-amber-500/50 rounded-lg text-[9px] text-amber-300 hover:border-amber-400 hover:text-amber-200 transition-colors flex items-center justify-center gap-1.5"
                    >
                      <Upload className="w-3.5 h-3.5" /> 一括追加
                    </button>
                  </div>
                  <button
                    onClick={() => setShowAddProduct(true)}
                    className="w-full py-1.5 border border-dashed border-gray-600 rounded-lg text-[9px] text-gray-400 hover:border-gray-500 hover:text-gray-300 transition-colors flex items-center justify-center gap-1.5"
                  >
                    <Plus className="w-3 h-3" /> 手動で追加
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Shopee Livestream Session + Products */}
          {productSource === "shopee" && (
            <div className="space-y-1.5">
              {/* Shopee Livestream Session Management */}
              <div className="p-2 bg-gray-900/50 rounded-lg border border-orange-500/30">
                <p className="text-[9px] text-orange-300 font-medium mb-1.5 flex items-center gap-1">
                  <Zap className="w-3 h-3" /> Shopee Livestream
                </p>
                {!shopeeLiveSession ? (
                  <div className="space-y-1.5">
                    <input
                      type="text"
                      value={shopeeSessionTitle}
                      onChange={(e) => setShopeeSessionTitle(e.target.value)}
                      placeholder="ライブタイトル"
                      className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[10px] text-gray-200 placeholder-gray-500 focus:border-orange-500/50 focus:outline-none"
                    />
                    <button
                      onClick={handleCreateShopeeSession}
                      disabled={isCreatingShopeeSession}
                      className="w-full py-1.5 bg-orange-500/20 border border-orange-500/40 rounded text-[9px] text-orange-300 hover:bg-orange-500/30 transition-colors flex items-center justify-center gap-1.5"
                    >
                      {isCreatingShopeeSession ? (
                        <><Loader2 className="w-3 h-3 animate-spin" /> 作成中...</>
                      ) : (
                        <><Zap className="w-3 h-3" /> Shopeeセッション作成</>
                      )}
                    </button>
                  </div>
                ) : (
                  <div className="space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[9px] text-gray-400">
                        Session: <span className="text-orange-300">{shopeeLiveSession.session_id}</span>
                      </span>
                      <span className={`text-[8px] px-1.5 py-0.5 rounded ${
                        shopeeLiveSession.status === "live" 
                          ? "bg-green-500/20 text-green-300" 
                          : "bg-yellow-500/20 text-yellow-300"
                      }`}>
                        {shopeeLiveSession.status === "live" ? "LIVE" : "Ready"}
                      </span>
                    </div>
                    <div className="flex gap-1">
                      {shopeeLiveSession.status !== "live" && (
                        <button
                          onClick={handleStartShopeeSession}
                          className="flex-1 py-1 bg-green-500/20 border border-green-500/40 rounded text-[8px] text-green-300 hover:bg-green-500/30 transition-colors"
                        >
                          ▶ 開始
                        </button>
                      )}
                      <button
                        onClick={handleEndShopeeSession}
                        className="flex-1 py-1 bg-red-500/20 border border-red-500/40 rounded text-[8px] text-red-300 hover:bg-red-500/30 transition-colors"
                      >
                        ■ 終了
                      </button>
                    </div>
                    {shopeeLiveSession.status === "live" && (
                      <p className="text-[8px] text-green-400/70">
                        ✔ コメント監視が有効になります。AIがコメントに自動応答します。
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* Shopee Products List */}
              <button
                onClick={() => {
                  setShowProducts(!showProducts);
                  if (!showProducts && shopeeProducts.length === 0) loadShopeeProducts();
                }}
                className="w-full flex items-center justify-between px-2 py-1.5 bg-gray-900/50 border border-gray-700/30 rounded-lg text-[10px] text-gray-300 hover:border-gray-600 transition-colors"
              >
                <span className="flex items-center gap-1.5">
                  <ShoppingBag className="w-3 h-3 text-amber-400" />
                  Shopee Products
                  {selectedProductIds.length > 0 && (
                    <span className="text-[8px] bg-amber-500/20 text-amber-300 px-1 py-0.5 rounded">
                      {selectedProductIds.length} selected
                    </span>
                  )}
                </span>
                {showProducts ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </button>

              {showProducts && (
                <div className="mt-1 p-2 bg-gray-900/50 rounded-lg border border-gray-700/30 max-h-40 overflow-y-auto">
                  {isLoadingProducts ? (
                    <div className="flex items-center justify-center py-3 text-[10px] text-gray-400">
                      <Loader2 className="w-3 h-3 animate-spin mr-1.5" />
                      Loading products...
                    </div>
                  ) : shopeeProducts.length === 0 ? (
                    <div className="text-center py-2">
                      <p className="text-[10px] text-gray-500 mb-2">商品データがありません</p>
                      <button
                        onClick={loadShopeeProducts}
                        className="px-3 py-1 bg-amber-500/20 border border-amber-500/30 rounded text-[9px] text-amber-300 hover:bg-amber-500/30"
                      >
                        Shopeeから取得
                      </button>
                    </div>
                  ) : (
                    <>
                      <button
                        onClick={() => {
                          if (selectedProductIds.length === shopeeProducts.length) {
                            setSelectedProductIds([]);
                          } else {
                            setSelectedProductIds(shopeeProducts.map(p => p.item_id));
                          }
                        }}
                        className="w-full text-left px-2 py-1 mb-1 text-[9px] text-amber-300 hover:bg-amber-500/10 rounded transition-colors"
                      >
                        {selectedProductIds.length === shopeeProducts.length ? "☑ Deselect All" : "☐ Select All"} ({shopeeProducts.length})
                      </button>
                      {shopeeProducts.map(p => (
                        <button
                          key={p.item_id}
                          onClick={() => toggleProduct(p.item_id)}
                          className={`w-full text-left px-2 py-1.5 rounded text-[9px] flex items-center gap-2 transition-colors ${
                            selectedProductIds.includes(p.item_id)
                              ? "bg-amber-500/10 text-amber-200"
                              : "text-gray-400 hover:bg-gray-800"
                          }`}
                        >
                          <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center flex-shrink-0 ${
                            selectedProductIds.includes(p.item_id)
                              ? "bg-amber-500 border-amber-500"
                              : "border-gray-600"
                          }`}>
                            {selectedProductIds.includes(p.item_id) && <CheckCircle className="w-2.5 h-2.5 text-white" />}
                          </span>
                          {p.image_url && (
                            <img src={p.image_url} alt="" className="w-7 h-7 rounded object-cover flex-shrink-0" />
                          )}
                          <div className="flex-1 min-w-0">
                            <span className="truncate block">{p.item_name || p.name || `Item ${p.item_id}`}</span>
                            {p.price && (
                              <span className="text-[8px] text-amber-400/70">{p.currency || ''} {p.price}</span>
                            )}
                          </div>
                        </button>
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Chat-only mode info */}
          {productSource === "none" && (
            <div className="p-2 bg-cyan-900/20 border border-cyan-500/30 rounded-lg">
              <p className="text-[9px] text-cyan-300 flex items-center gap-1.5">
                <MessageCircle className="w-3 h-3" />
                商品なしの雑談モード。AIが自然な会話を続けます。
              </p>
              <p className="text-[8px] text-cyan-400/60 mt-1">
                配信中に商品を追加することもできます。
              </p>
            </div>
          )}
        </div>
      )}

      {/* Add product during live */}
      {isAutoMode && (
        <div className="mb-2">
          {showAddProduct ? (
            <div className="p-2 bg-gray-900/50 rounded-lg border border-amber-500/30 space-y-1.5">
              {/* Photo preview & analyzing indicator */}
              {(photoPreview || isAnalyzingPhoto) && (
                <div className="flex items-center gap-2 p-1.5 bg-gray-800/50 rounded-lg border border-gray-700/30">
                  {photoPreview && (
                    <img src={photoPreview} alt="" className="w-12 h-12 rounded object-cover flex-shrink-0" />
                  )}
                  {isAnalyzingPhoto ? (
                    <div className="flex items-center gap-1.5 text-[9px] text-cyan-300">
                      <Loader2 className="w-3 h-3 animate-spin" />
                      AIが写真を解析中...
                    </div>
                  ) : error ? (
                    <p className="text-[8px] text-red-400 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3" /> 解析失敗。手動で入力してください。
                    </p>
                  ) : (
                    <p className="text-[8px] text-green-400 flex items-center gap-1">
                      <CheckCircle className="w-3 h-3" /> 解析完了。内容を確認・編集してください。
                    </p>
                  )}
                </div>
              )}
              <input
                type="text"
                placeholder="商品名 *"
                value={newProduct.name}
                onChange={e => setNewProduct(prev => ({ ...prev, name: e.target.value }))}
                className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none"
              />
              <textarea
                placeholder="商品説明"
                value={newProduct.description}
                onChange={e => setNewProduct(prev => ({ ...prev, description: e.target.value }))}
                rows={2}
                className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none resize-none"
              />
              <input
                type="text"
                placeholder="画像URL（任意）"
                value={newProduct.image_url}
                onChange={e => setNewProduct(prev => ({ ...prev, image_url: e.target.value }))}
                className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none"
              />
              <textarea
                placeholder="追加メモ（話してほしいことなど）"
                value={newProduct.notes}
                onChange={e => setNewProduct(prev => ({ ...prev, notes: e.target.value }))}
                rows={1}
                className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-[9px] text-gray-200 placeholder-gray-500 focus:border-amber-500/50 outline-none resize-none"
              />
              <div className="flex gap-1.5">
                <button
                  onClick={handleAddProductDuringLive}
                  disabled={!newProduct.name.trim() || isAnalyzingPhoto}
                  className="flex-1 py-1.5 bg-amber-500/20 border border-amber-500/50 rounded text-[9px] text-amber-300 hover:bg-amber-500/30 disabled:opacity-50 flex items-center justify-center gap-1"
                >
                  <Plus className="w-3 h-3" /> 追加して紹介させる
                </button>
                <button
                  onClick={() => { setShowAddProduct(false); setPhotoPreview(null); }}
                  className="px-3 py-1.5 bg-gray-700/50 border border-gray-600 rounded text-[9px] text-gray-400"
                >
                  閉じる
                </button>
              </div>
            </div>
          ) : (
            <div className="flex gap-1.5">
              <input
                ref={photoInputLiveRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handlePhotoSelect}
              />
              <input
                ref={batchPhotoInputLiveRef}
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                onChange={handleBatchPhotoSelect}
              />
              <button
                onClick={() => photoInputLiveRef.current?.click()}
                disabled={isAnalyzingPhoto || batchAnalyzing}
                className="flex-1 py-1.5 border border-dashed border-cyan-600/50 rounded-lg text-[9px] text-cyan-400 hover:border-cyan-400 hover:text-cyan-300 transition-colors flex items-center justify-center gap-1"
              >
                <Camera className="w-3 h-3" /> 1枚
              </button>
              <button
                onClick={() => batchPhotoInputLiveRef.current?.click()}
                disabled={isAnalyzingPhoto || batchAnalyzing}
                className="flex-1 py-1.5 border border-dashed border-amber-500/50 rounded-lg text-[9px] text-amber-300 hover:border-amber-400 hover:text-amber-200 transition-colors flex items-center justify-center gap-1"
              >
                <Upload className="w-3 h-3" /> 一括
              </button>
              <button
                onClick={() => setShowAddProduct(true)}
                className="flex-1 py-1.5 border border-dashed border-gray-600 rounded-lg text-[9px] text-gray-400 hover:border-amber-500/50 hover:text-amber-300 transition-colors flex items-center justify-center gap-1"
              >
                <Plus className="w-3 h-3" /> 手動
              </button>
            </div>
          )}
        </div>
      )}

      {/* Control Buttons */}
      <div className="flex gap-1.5">
        {!isAutoMode ? (
          <button
            onClick={handleStart}
            disabled={isLoading || !isConnected}
            className={`flex-1 py-2 px-3 rounded-lg text-[10px] font-medium flex items-center justify-center gap-1.5 transition-all border ${
              isLoading || !isConnected
                ? "bg-gray-700/50 border-gray-700 text-gray-500 cursor-not-allowed"
                : "bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-700 hover:to-orange-700 border-amber-500/50 text-white shadow-lg shadow-amber-500/20"
            }`}
          >
            {isLoading ? (
              <><Loader2 className="w-3 h-3 animate-spin" />Starting...</>
            ) : (
              <><Play className="w-3 h-3" />
                {productSource === "none" ? "雑談モード開始" : 
                 totalProducts > 0 ? `Auto Live 開始 (${totalProducts}商品)` : "Auto Live 開始"}
              </>
            )}
          </button>
        ) : (
          <>
            <button
              onClick={handlePause}
              className={`flex-1 py-2 px-3 rounded-lg text-[10px] font-medium flex items-center justify-center gap-1.5 transition-all border ${
                autoStatus?.status === "paused"
                  ? "bg-green-500/20 border-green-500/50 text-green-300 hover:bg-green-500/30"
                  : "bg-yellow-500/20 border-yellow-500/50 text-yellow-300 hover:bg-yellow-500/30"
              }`}
            >
              {autoStatus?.status === "paused" ? (
                <><Play className="w-3 h-3" />Resume</>
              ) : (
                <><Pause className="w-3 h-3" />Pause</>
              )}
            </button>
            <button
              onClick={handleStop}
              disabled={isLoading}
              className="py-2 px-3 rounded-lg text-[10px] font-medium flex items-center justify-center gap-1.5 transition-all border bg-red-500/20 border-red-500/50 text-red-300 hover:bg-red-500/30"
            >
              {isLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <><Square className="w-3 h-3" />Stop</>}
            </button>
          </>
        )}
      </div>

      {/* Status Display */}
      {isAutoMode && autoStatus && (
        <div className="mt-2 p-2 bg-gray-900/50 rounded-lg border border-gray-700/30">
          <div className="grid grid-cols-3 gap-2 text-center">
            <div>
              <p className="text-[8px] text-gray-500">Speaks</p>
              <p className="text-[11px] font-bold text-amber-300">{autoStatus.speak_count || 0}</p>
            </div>
            <div>
              <p className="text-[8px] text-gray-500">Mode</p>
              <p className="text-[10px] font-bold text-cyan-300">{autoStatus.mode === "chat-only" ? "雑談" : "商品"}</p>
            </div>
            <div>
              <p className="text-[8px] text-gray-500">Products</p>
              <p className="text-[11px] font-bold text-green-300">{autoStatus.product_count || 0}</p>
            </div>
          </div>
          {autoStatus.current_product && (
            <div className="mt-1.5 pt-1.5 border-t border-gray-700/30">
              <p className="text-[8px] text-gray-500">Now presenting:</p>
              <p className="text-[9px] text-amber-200 truncate">{autoStatus.current_product.itemName}</p>
            </div>
          )}
          {shopeeLiveSession?.status === "live" && (
            <div className="mt-1.5 pt-1.5 border-t border-gray-700/30 flex items-center justify-between">
              <p className="text-[8px] text-orange-300 flex items-center gap-1">
                <Zap className="w-2.5 h-2.5" /> Shopee Live
              </p>
              <div className="flex items-center gap-2">
                {autoStatus.comments_responded > 0 && (
                  <span className="text-[8px] text-cyan-300">
                    {autoStatus.comments_responded} コメント応答
                  </span>
                )}
                <span className="text-[7px] bg-green-500/20 text-green-300 px-1 py-0.5 rounded animate-pulse">
                  LIVE
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Info */}
      {!isAutoMode && (
        <p className="mt-1.5 text-[8px] text-gray-500">
          {productSource === "none" 
            ? "AIが自然な雑談を続けます。配信中に商品を追加して紹介させることもできます。"
            : productSource === "manual"
            ? "商品を手動で追加してAIに紹介させます。画像URL・説明・メモを入力できます。"
            : "Shopee商品データをAIが理解し、自動でセールストークを生成してアバターが話し続けます。"
          }
        </p>
      )}
    </div>
  );
}
