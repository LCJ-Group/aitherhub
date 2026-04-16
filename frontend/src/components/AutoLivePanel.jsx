/**
 * AutoLivePanel — AI自動ライブ配信コントロールパネル
 * 
 * Features:
 * - Shopee商品データ取得・選択
 * - AI自動スピーチの開始/停止/一時停止
 * - 言語・スタイル設定
 * - リアルタイムステータス表示
 * - コメント応答モード
 */
import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Play, Square, Pause, RotateCcw, Loader2,
  ShoppingBag, MessageSquare, Zap, Globe, Sparkles,
  CheckCircle, AlertCircle, Package, TrendingUp,
  Volume2, VolumeX, Settings, ChevronDown, ChevronUp,
} from "lucide-react";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";

const LANGUAGES = [
  { code: "en", label: "English", flag: "🇬🇧" },
  { code: "ja", label: window.__t('language_japanese', '日本語'), flag: "🇯🇵" },
  { code: "zh", label: window.__t('scriptGen_langZh', '中文'), flag: "🇨🇳" },
  { code: "th", label: "ไทย", flag: "🇹🇭" },
  { code: "ms", label: "Malay", flag: "🇲🇾" },
];

const STYLES = [
  { id: "professional", label: "Professional", icon: "👔", desc: window.__t('autoLivePanel_b930dd', '信頼感のあるプロの解説') },
  { id: "casual", label: "Casual", icon: "😊", desc: window.__t('autoLivePanel_48c3d1', '友達と話すようなカジュアル') },
  { id: "energetic", label: "Energetic", icon: "🔥", desc: window.__t('autoLivePanel_e70981', 'テンション高めの販促トーク') },
];

export default function AutoLivePanel({ sessionId, isConnected, onStatusChange }) {
  // ── State ──
  const [isAutoMode, setIsAutoMode] = useState(false);
  const [autoStatus, setAutoStatus] = useState(null); // running, paused, not_running
  const [language, setLanguage] = useState("en");
  const [style, setStyle] = useState("professional");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  
  // Shopee products
  const [products, setProducts] = useState([]);
  const [selectedProductIds, setSelectedProductIds] = useState([]);
  const [isLoadingProducts, setIsLoadingProducts] = useState(false);
  const [showProducts, setShowProducts] = useState(false);
  
  // Settings panel
  const [showSettings, setShowSettings] = useState(false);
  
  // Status polling
  const statusIntervalRef = useRef(null);

  // ── Load Shopee Products ──
  const loadProducts = useCallback(async () => {
    setIsLoadingProducts(true);
    setError("");
    try {
      const data = await aiLiveCreatorService.shopeeGetProducts();
      if (data?.items) {
        setProducts(data.items);
        // デフォルトで全商品を選択
        setSelectedProductIds(data.items.map(p => p.item_id));
      }
    } catch (err) {
      console.error("[AutoLive] Failed to load products:", err);
      setError(window.__t('autoLivePanel_ac5479', 'Shopee商品の取得に失敗しました。Sales Dashのブリッジ接続を確認してください。'));
    } finally {
      setIsLoadingProducts(false);
    }
  }, []);

  // ── Start Auto Live ──
  const handleStart = async () => {
    if (!sessionId) {
      setError(window.__t('autoLivePanel_5e568f', 'ストリーミングを先に開始してください'));
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

      // 選択された商品がある場合はIDを送信
      if (selectedProductIds.length > 0) {
        params.product_item_ids = selectedProductIds;
      }

      // 商品データが手動で用意されている場合
      if (products.length > 0 && selectedProductIds.length > 0) {
        const selected = products.filter(p => selectedProductIds.includes(p.item_id));
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
      }

      const result = await aiLiveCreatorService.autoLiveStart(params);
      setAutoStatus(result);
      setIsAutoMode(true);
      startStatusPolling();
      onStatusChange?.("running");
    } catch (err) {
      console.error("[AutoLive] Failed to start:", err);
      setError(err.response?.data?.detail || window.__t('autoLivePanel_a2190f', '自動配信の開始に失敗しました'));
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
      } catch (err) {
        // ignore
      }
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
      prev.includes(itemId) 
        ? prev.filter(id => id !== itemId)
        : [...prev, itemId]
    );
  };

  const selectAllProducts = () => {
    if (selectedProductIds.length === products.length) {
      setSelectedProductIds([]);
    } else {
      setSelectedProductIds(products.map(p => p.item_id));
    }
  };

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
                  title={s.desc}
                >
                  {s.icon} {s.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Product Selection */}
      <div className="mb-2">
        <button
          onClick={() => {
            setShowProducts(!showProducts);
            if (!showProducts && products.length === 0) loadProducts();
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
            ) : products.length === 0 ? (
              <div className="text-center py-3">
                <p className="text-[10px] text-gray-500 mb-2">{window.__t('autoLivePanel_32a50b', window.__t('autoLivePanel_32a50b', '商品データがありません'))}</p>
                <button
                  onClick={loadProducts}
                  className="px-3 py-1 bg-amber-500/20 border border-amber-500/30 rounded text-[9px] text-amber-300 hover:bg-amber-500/30"
                >
                  Shopeeから取得
                </button>
              </div>
            ) : (
              <>
                <button
                  onClick={selectAllProducts}
                  className="w-full text-left px-2 py-1 mb-1 text-[9px] text-amber-300 hover:bg-amber-500/10 rounded transition-colors"
                >
                  {selectedProductIds.length === products.length ? "☑ Deselect All" : "☐ Select All"} ({products.length})
                </button>
                {products.map(p => (
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
                      {selectedProductIds.includes(p.item_id) && (
                        <CheckCircle className="w-2 h-2 text-white" />
                      )}
                    </span>
                    <span className="truncate flex-1">{p.item_name || p.name}</span>
                    {p.price_info?.current_price && (
                      <span className="text-[8px] text-gray-500 flex-shrink-0">
                        ${p.price_info.current_price}
                      </span>
                    )}
                  </button>
                ))}
              </>
            )}
          </div>
        )}
      </div>

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
              <><Play className="w-3 h-3" />{window.__t('autoLivePanel_7508db', window.__t('autoLivePanel_7508db', 'Auto Live 開始'))}</>
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
              {isLoading ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <><Square className="w-3 h-3" />Stop</>
              )}
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
              <p className="text-[8px] text-gray-500">Comments</p>
              <p className="text-[11px] font-bold text-cyan-300">{autoStatus.comment_count || 0}</p>
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
          Shopee商品データをAIが理解し、自動でセールストークを生成してアバターが話し続けます。コメントにも自動応答します。
        </p>
      )}
    </div>
  );
}
