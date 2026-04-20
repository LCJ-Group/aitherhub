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
  Settings, MessageCircle, Package, Upload,
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
  
  // Manual products
  const [manualProducts, setManualProducts] = useState([]);
  const [showAddProduct, setShowAddProduct] = useState(false);
  const [newProduct, setNewProduct] = useState({ name: "", description: "", price: "", brand: "", image_url: "", notes: "" });
  
  // Settings panel
  const [showSettings, setShowSettings] = useState(false);
  
  // Status polling
  const statusIntervalRef = useRef(null);

  // ── Load Shopee Products ──
  const loadShopeeProducts = useCallback(async () => {
    setIsLoadingProducts(true);
    setError("");
    try {
      const data = await aiLiveCreatorService.shopeeGetProducts();
      if (data?.items) {
        setShopeeProducts(data.items);
        setSelectedProductIds(data.items.map(p => p.item_id));
      }
    } catch (err) {
      console.error("[AutoLive] Failed to load products:", err);
      setError("Failed to fetch products from Shopee. Check Sales Dash bridge connection.");
    } finally {
      setIsLoadingProducts(false);
    }
  }, []);

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
                      disabled={!newProduct.name.trim()}
                      className="flex-1 py-1.5 bg-amber-500/20 border border-amber-500/50 rounded text-[9px] text-amber-300 hover:bg-amber-500/30 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1"
                    >
                      <Plus className="w-3 h-3" /> 追加
                    </button>
                    <button
                      onClick={() => setShowAddProduct(false)}
                      className="px-3 py-1.5 bg-gray-700/50 border border-gray-600 rounded text-[9px] text-gray-400 hover:bg-gray-700"
                    >
                      キャンセル
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setShowAddProduct(true)}
                  className="w-full py-2 border-2 border-dashed border-gray-600 rounded-lg text-[9px] text-gray-400 hover:border-amber-500/50 hover:text-amber-300 transition-colors flex items-center justify-center gap-1.5"
                >
                  <Plus className="w-3.5 h-3.5" /> 商品を追加
                </button>
              )}
            </div>
          )}

          {/* Shopee Products */}
          {productSource === "shopee" && (
            <div>
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
                          <span className={`w-3 h-3 rounded border flex items-center justify-center flex-shrink-0 ${
                            selectedProductIds.includes(p.item_id)
                              ? "bg-amber-500 border-amber-500"
                              : "border-gray-600"
                          }`}>
                            {selectedProductIds.includes(p.item_id) && <CheckCircle className="w-2 h-2 text-white" />}
                          </span>
                          <span className="truncate flex-1">{p.item_name || p.name}</span>
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
                  disabled={!newProduct.name.trim()}
                  className="flex-1 py-1.5 bg-amber-500/20 border border-amber-500/50 rounded text-[9px] text-amber-300 hover:bg-amber-500/30 disabled:opacity-50 flex items-center justify-center gap-1"
                >
                  <Plus className="w-3 h-3" /> 追加して紹介させる
                </button>
                <button
                  onClick={() => setShowAddProduct(false)}
                  className="px-3 py-1.5 bg-gray-700/50 border border-gray-600 rounded text-[9px] text-gray-400"
                >
                  閉じる
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setShowAddProduct(true)}
              className="w-full py-1.5 border border-dashed border-gray-600 rounded-lg text-[9px] text-gray-400 hover:border-amber-500/50 hover:text-amber-300 transition-colors flex items-center justify-center gap-1"
            >
              <Plus className="w-3 h-3" /> 商品を追加
            </button>
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
