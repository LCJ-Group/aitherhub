import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from "react";
import {
  Brain,
  MessageSquare,
  Send,
  Plus,
  Trash2,
  Play,
  Pause,
  SkipForward,
  Loader2,
  CheckCircle,
  AlertCircle,
  Clock,
  ShoppingBag,
  Sparkles,
  RefreshCw,
  Download,
  Volume2,
  ChevronDown,
  ChevronUp,
  Package,
  Mic,
  Video,
  Radio,
  ListOrdered,
  X,
  Edit3,
  Copy,
  Zap,
  Crown,
  Link,
  ExternalLink,
  Image,
  Globe,
} from "lucide-react";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";

/**
 * LiveStreamPanel — Livestream Control Panel for AI Live Creator
 *
 * Features:
 *   - Product Management (add/remove products)
 *   - Sales Brain (帯貨大脳): Generate scripts per product
 *   - Comment Response: Reply to viewer comments
 *   - Video Queue: Track generated video segments
 *   - Generate & Queue: Script → TTS → Digital Human → Queue
 */
const LiveStreamPanel = forwardRef(function LiveStreamPanel({
  sessionId,
  setSessionId,
  portraitUrl,
  portraitType = "image",
  engine,
  voiceId,
  language,
  onVideoGenerated,
  onQueueUpdate,
  onCommentHistoryUpdate,
  onProductsUpdate,
}, ref) {
  // ── Tab State ──
  const [activeTab, setActiveTab] = useState("products"); // products | comments | queue

  // ── Products ──
  const [products, setProducts] = useState([]);
  const [newProduct, setNewProduct] = useState({ name: "", description: "", price: "", features: "" });
  const [showAddProduct, setShowAddProduct] = useState(false);
  const [tiktokUrl, setTiktokUrl] = useState("");
  const [isImportingTiktok, setIsImportingTiktok] = useState(false);
  const [importError, setImportError] = useState("");
  const [showTiktokImport, setShowTiktokImport] = useState(true); // Show TikTok import by default

  // ── Scripts ──
  const [scripts, setScripts] = useState({}); // productName → {type → script}
  const [generatingScript, setGeneratingScript] = useState(null); // productName being generated
  const [scriptType, setScriptType] = useState("introduction");
  const [scriptTone, setScriptTone] = useState("professional_friendly");

  // ── Comments ──
  const [commentText, setCommentText] = useState("");
  const [commenterName, setCommenterName] = useState("");
  const [commentHistory, setCommentHistory] = useState([]);
  const [isGeneratingReply, setIsGeneratingReply] = useState(false);

  // ── Video Queue ──
  const [videoQueue, setVideoQueue] = useState([]);
  const [isGeneratingVideo, setIsGeneratingVideo] = useState(false);
  const queuePollRef = useRef(null);

  // ── Session ──
  const [isCreatingSession, setIsCreatingSession] = useState(false);

  // ── Toast Notification ──
  const [toast, setToast] = useState(null); // { type: 'success'|'error'|'info', message: string }
  const toastTimeoutRef = useRef(null);
  const showToast = (type, message) => {
    setToast({ type, message });
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    toastTimeoutRef.current = setTimeout(() => setToast(null), 4000);
  };

  // ── Notify parent of state changes ──
  useEffect(() => { onProductsUpdate?.(products); }, [products]);
  useEffect(() => { onCommentHistoryUpdate?.(commentHistory); }, [commentHistory]);
  useEffect(() => { onQueueUpdate?.(videoQueue); }, [videoQueue]);

  // ── Poll queue status ──
  useEffect(() => {
    if (!sessionId) return;
    const pollQueue = async () => {
      try {
        const res = await aiLiveCreatorService.getSessionQueue(sessionId);
        if (res.success && res.queue) {
          setVideoQueue(res.queue);
          onQueueUpdate?.(res.queue);
        }
      } catch (err) {
        console.error("Queue poll error:", err);
      }
    };
    pollQueue();
    queuePollRef.current = setInterval(pollQueue, 5000);
    return () => {
      if (queuePollRef.current) clearInterval(queuePollRef.current);
    };
  }, [sessionId]);

  // ══════════════════════════════════════════════
  // Session Management
  // ══════════════════════════════════════════════

  const handleCreateSession = async () => {
    if (!portraitUrl) {
      showToast('error', 'Portrait is required to start a session');
      return;
    }
    setIsCreatingSession(true);
    showToast('info', 'Creating session...');
    try {
      console.log('[LiveStreamPanel] Creating session...', { portraitUrl, engine, voiceId, language, products: products.length });
      const res = await aiLiveCreatorService.createLiveSession({
        portrait_url: portraitUrl,
        portrait_type: portraitType || "image",
        engine: engine || "imtalker",
        voice_id: voiceId,
        language: language || "ja",
        products: products.map((p) => ({
          name: p.name,
          description: p.description,
          price: p.price,
          features: p.features ? p.features.split(",").map((f) => f.trim()) : [],
        })),
      });
      console.log('[LiveStreamPanel] Create session result:', res);
      if (res.success) {
        setSessionId(res.session_id);
        showToast('success', `Session started: ${res.session_id}`);
      } else {
        showToast('error', res.error || 'Failed to create session');
      }
    } catch (err) {
      console.error("Create session error:", err);
      showToast('error', `Session error: ${err.message}`);
    } finally {
      setIsCreatingSession(false);
    }
  };

  // ══════════════════════════════════════════════
  // Product Management
  // ══════════════════════════════════════════════

  const handleAddProduct = () => {
    if (!newProduct.name.trim()) return;
    setProducts((prev) => [...prev, { ...newProduct, id: Date.now() }]);
    setNewProduct({ name: "", description: "", price: "", features: "" });
    setShowAddProduct(false);
  };

  const handleRemoveProduct = (id) => {
    setProducts((prev) => prev.filter((p) => p.id !== id));
  };

  // ══════════════════════════════════════════════
  // TikTok Shop Product Import
  // ══════════════════════════════════════════════

  const handleTiktokImport = async () => {
    if (!tiktokUrl.trim()) return;
    setIsImportingTiktok(true);
    setImportError("");
    showToast('info', 'Importing TikTok product...');
    try {
      console.log('[LiveStreamPanel] Importing TikTok product:', tiktokUrl);
      const res = await aiLiveCreatorService.importTikTokProduct({
        product_url: tiktokUrl.trim(),
        language: language || "ja",
        session_id: sessionId || null,
      });
      if (res.success && res.product) {
        const p = res.product;
        setProducts((prev) => [
          ...prev,
          {
            id: Date.now(),
            name: p.name || "Unknown Product",
            description: p.description || "",
            price: p.price || "",
            features: (p.features || []).join(", "),
            image_url: p.image_url || "",
            original_url: p.original_url || tiktokUrl,
            tiktok_product_id: p.tiktok_product_id || "",
            source: "tiktok_shop",
            category: p.category || "",
            target_audience: p.target_audience || "",
            selling_points: p.selling_points || [],
          },
        ]);
        setTiktokUrl("");
      } else {
        setImportError(res.error || "Import failed");
      }
    } catch (err) {
      console.error("TikTok import error:", err);
      setImportError(err?.response?.data?.error || "Failed to import product");
    } finally {
      setIsImportingTiktok(false);
    }
  };

  // ══════════════════════════════════════════════
  // Sales Brain — Script Generation
  // ══════════════════════════════════════════════

  const handleGenerateScript = async (product) => {
    setGeneratingScript(product.name);
    showToast('info', `Generating ${scriptType} script for "${product.name}"...`);
    try {
      console.log('[LiveStreamPanel] Generating script:', { sessionId, productName: product.name, scriptType, scriptTone });
      const res = await aiLiveCreatorService.generateProductScript({
        session_id: sessionId,
        product_name: product.name,
        product_description: product.description,
        product_price: product.price,
        product_features: product.features ? product.features.split(",").map((f) => f.trim()) : [],
        tone: scriptTone,
        language: language || "ja",
        script_type: scriptType,
      });
      console.log('[LiveStreamPanel] Script generation result:', res);
      if (res.success) {
        setScripts((prev) => ({
          ...prev,
          [product.name]: {
            ...(prev[product.name] || {}),
            [scriptType]: res.script_text,
          },
        }));
        showToast('success', `Script generated! (${res.script_text?.length || 0} chars)`);
      } else {
        showToast('error', res.error || 'Failed to generate script');
      }
    } catch (err) {
      console.error("Script generation error:", err);
      showToast('error', `Script error: ${err.response?.data?.error || err.message}`);
    } finally {
      setGeneratingScript(null);
    }
  };

  const handleGenerateAllScripts = async () => {
    if (!sessionId) {
      showToast('error', 'No active session. Start a session first.');
      return;
    }
    setGeneratingScript("__all__");
    showToast('info', 'Generating scripts for all products...');
    try {
      console.log('[LiveStreamPanel] Generating all scripts for session:', sessionId);
      const res = await aiLiveCreatorService.generateAllSessionScripts(sessionId);
      console.log('[LiveStreamPanel] Generate all scripts result:', res);
      if (res.success && res.scripts) {
        const newScripts = {};
        for (const s of res.scripts) {
          if (!newScripts[s.product_name]) newScripts[s.product_name] = {};
          newScripts[s.product_name][s.script_type] = s.script_text;
        }
        setScripts((prev) => ({ ...prev, ...newScripts }));
        showToast('success', `Generated ${res.scripts.length} script(s)!`);
      } else {
        showToast('error', res.error || 'Failed to generate scripts');
      }
    } catch (err) {
      console.error("Generate all scripts error:", err);
      showToast('error', `Script error: ${err.message}`);
    } finally {
      setGeneratingScript(null);
    }
  };

  // ══════════════════════════════════════════════
  // Generate Video from Script & Add to Queue
  // ══════════════════════════════════════════════

  const handleGenerateVideo = async (text, productName, queueType = "product_intro") => {
    if (!sessionId) {
      showToast('error', 'No active session. Start a session first.');
      return;
    }
    if (!text) {
      showToast('error', 'No script text to generate video from.');
      return;
    }
    setIsGeneratingVideo(true);
    showToast('info', `Generating video for "${productName}"...`);
    try {
      console.log('[LiveStreamPanel] Generating video:', { sessionId, text: text.substring(0, 50), productName, queueType });
      const res = await aiLiveCreatorService.generateAndQueueVideo(sessionId, {
        text,
        queue_type: queueType,
        product_name: productName,
      });
      console.log('[LiveStreamPanel] Generate video result:', res);
      if (res.success) {
        setVideoQueue((prev) => [
          ...prev,
          {
            job_id: res.job_id,
            type: queueType,
            status: "processing",
            text_preview: text.substring(0, 100),
            product_name: productName,
          },
        ]);
        onVideoGenerated?.(res.job_id);
        showToast('success', `Video queued! Job: ${res.job_id}`);
        // Auto-switch to Queue tab
        setActiveTab('queue');
      } else {
        showToast('error', res.error || 'Failed to generate video');
      }
    } catch (err) {
      console.error("Generate video error:", err);
      showToast('error', `Video error: ${err.response?.data?.error || err.message}`);
    } finally {
      setIsGeneratingVideo(false);
    }
  };

  // ══════════════════════════════════════════════
  // Auto-Generate Next Video (for infinite loop)
  // ══════════════════════════════════════════════

  const autoGenIndexRef = useRef(0);

  const generateNextVideo = useCallback(async () => {
    if (!sessionId || products.length === 0) {
      console.log('[LiveStreamPanel] generateNextVideo: no session or products');
      return;
    }
    if (isGeneratingVideo) {
      console.log('[LiveStreamPanel] generateNextVideo: already generating');
      return;
    }

    // Cycle through products and script types
    const scriptTypes = ['introduction', 'highlight', 'promotion', 'closing'];
    const productIndex = autoGenIndexRef.current % products.length;
    const typeIndex = Math.floor(autoGenIndexRef.current / products.length) % scriptTypes.length;
    autoGenIndexRef.current++;

    const product = products[productIndex];
    const sType = scriptTypes[typeIndex];

    console.log(`[LiveStreamPanel] Auto-generating next video: product=${product.name}, type=${sType}`);

    // First generate a script, then generate video from it
    try {
      const scriptRes = await aiLiveCreatorService.generateProductScript({
        product_name: product.name,
        product_description: product.description || '',
        product_price: product.price || '',
        product_features: product.features || [],
        tone: 'professional_friendly',
        language: language || 'ja',
        script_type: sType,
      });

      if (scriptRes.success && scriptRes.script_text) {
        // Update scripts state
        setScripts(prev => ({
          ...prev,
          [product.name]: {
            ...(prev[product.name] || {}),
            [sType]: scriptRes.script_text,
          },
        }));

        // Generate video from the new script
        await handleGenerateVideo(scriptRes.script_text, product.name, 'product_intro');
      } else {
        console.error('[LiveStreamPanel] Auto script generation failed:', scriptRes.error);
        // Fallback: use existing script if available
        const existingScript = scripts[product.name]?.[sType] || scripts[product.name]?.introduction;
        if (existingScript) {
          await handleGenerateVideo(existingScript, product.name, 'product_intro');
        }
      }
    } catch (err) {
      console.error('[LiveStreamPanel] Auto-generate next video error:', err);
    }
  }, [sessionId, products, scripts, language, isGeneratingVideo, handleGenerateVideo]);

  // Expose generateNextVideo to parent via ref
  useImperativeHandle(ref, () => ({
    generateNextVideo,
  }), [generateNextVideo]);

  // ══════════════════════════════════════════════
  // Comment Response
  // ══════════════════════════════════════════════

  const handleCommentResponse = async () => {
    if (!commentText.trim()) {
      showToast('error', 'Please enter a comment');
      return;
    }
    setIsGeneratingReply(true);
    showToast('info', 'AI is generating a reply...');

    const currentProduct = products.length > 0 ? {
      name: products[0].name,
      description: products[0].description,
      price: products[0].price,
    } : null;

    try {
      console.log('[LiveStreamPanel] Generating comment response:', { commentText, commenterName, sessionId });
      const res = await aiLiveCreatorService.generateCommentResponse({
        session_id: sessionId,
        comment_text: commentText,
        commenter_name: commenterName,
        current_product: currentProduct,
        language: language || "ja",
        auto_generate_video: !!sessionId,
        portrait_url: portraitUrl,
        engine: engine || "musetalk", // Use fast engine for comments
        voice_id: voiceId,
      });
      console.log('[LiveStreamPanel] Comment response result:', res);
      if (res.success) {
        setCommentHistory((prev) => [
          {
            comment: commentText,
            commenter: commenterName,
            reply: res.reply_text,
            job_id: res.video_job_id,
            timestamp: Date.now(),
          },
          ...prev,
        ]);
        setCommentText("");
        setCommenterName("");
        showToast('success', `AI replied: "${(res.reply_text || '').substring(0, 50)}..."`);
      } else {
        showToast('error', res.error || 'Failed to generate reply');
      }
    } catch (err) {
      console.error("Comment response error:", err);
      showToast('error', `Reply error: ${err.response?.data?.error || err.message}`);
    } finally {
      setIsGeneratingReply(false);
    }
  };

  // ══════════════════════════════════════════════
  // Copy to Clipboard
  // ══════════════════════════════════════════════

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text).catch(() => {});
  };

  // ══════════════════════════════════════════════
  // Status Helpers
  // ══════════════════════════════════════════════

  const getQueueStatusIcon = (status) => {
    const map = {
      completed: <CheckCircle className="w-4 h-4 text-green-500" />,
      processing: <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />,
      queued: <Clock className="w-4 h-4 text-yellow-500" />,
      error: <AlertCircle className="w-4 h-4 text-red-500" />,
    };
    return map[status] || <Clock className="w-4 h-4 text-gray-400" />;
  };

  const getQueueTypeLabel = (type) => {
    const map = {
      product_intro: "Product Intro",
      comment_reply: "Comment Reply",
      custom: "Custom",
    };
    return map[type] || type;
  };

  // ══════════════════════════════════════════════
  // Render
  // ══════════════════════════════════════════════

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden relative">
      {/* Toast Notification */}
      {toast && (
        <div
          className={`absolute top-2 left-2 right-2 z-50 px-3 py-2 rounded-lg text-xs font-medium shadow-lg flex items-center gap-2 animate-fade-in ${
            toast.type === 'success'
              ? 'bg-green-500 text-white'
              : toast.type === 'error'
              ? 'bg-red-500 text-white'
              : 'bg-blue-500 text-white'
          }`}
        >
          {toast.type === 'success' && <CheckCircle className="w-3.5 h-3.5 flex-shrink-0" />}
          {toast.type === 'error' && <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />}
          {toast.type === 'info' && <Loader2 className="w-3.5 h-3.5 flex-shrink-0 animate-spin" />}
          <span className="truncate">{toast.message}</span>
          <button onClick={() => setToast(null)} className="ml-auto flex-shrink-0 hover:opacity-80">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {/* Panel Header */}
      <div className="bg-gradient-to-r from-indigo-600 to-purple-600 px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-white" />
            <h2 className="text-sm font-bold text-white">Livestream Brain</h2>
            <span className="text-[10px] bg-white/20 text-white px-2 py-0.5 rounded-full">
              帯貨大脳
            </span>
          </div>
          {sessionId ? (
            <span className="text-[10px] bg-green-400/20 text-green-100 px-2 py-0.5 rounded-full flex items-center gap-1">
              <span className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse" />
              Session Active
            </span>
          ) : (
            <button
              onClick={handleCreateSession}
              disabled={!portraitUrl || isCreatingSession}
              className="text-[10px] bg-white/20 hover:bg-white/30 text-white px-3 py-1 rounded-full transition-colors disabled:opacity-50 flex items-center gap-1"
            >
              {isCreatingSession ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Play className="w-3 h-3" />
              )}
              Start Session
            </button>
          )}
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex border-b border-gray-200">
        {[
          { id: "products", icon: ShoppingBag, label: "Products", count: products.length },
          { id: "comments", icon: MessageSquare, label: "Comments", count: commentHistory.length },
          { id: "queue", icon: ListOrdered, label: "Queue", count: videoQueue.length },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 text-xs font-medium transition-colors relative ${
              activeTab === tab.id
                ? "text-indigo-600 bg-indigo-50/50"
                : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
            }`}
          >
            <tab.icon className="w-3.5 h-3.5" />
            {tab.label}
            {tab.count > 0 && (
              <span
                className={`text-[9px] px-1.5 py-0.5 rounded-full ${
                  activeTab === tab.id
                    ? "bg-indigo-100 text-indigo-600"
                    : "bg-gray-100 text-gray-500"
                }`}
              >
                {tab.count}
              </span>
            )}
            {activeTab === tab.id && (
              <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-indigo-600" />
            )}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="p-4 max-h-[600px] overflow-y-auto">
        {/* ══════════════════════════════════════════════ */}
        {/* Products Tab */}
        {/* ══════════════════════════════════════════════ */}
        {activeTab === "products" && (
          <div className="space-y-3">
            {/* Product List */}
            {products.map((product) => (
              <div
                key={product.id}
                className={`border rounded-lg p-3 hover:border-indigo-200 transition-colors ${
                  product.source === "tiktok_shop"
                    ? "border-pink-200 bg-gradient-to-r from-pink-50/30 to-white"
                    : "border-gray-200"
                }`}
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    {product.image_url ? (
                      <img
                        src={product.image_url}
                        alt={product.name}
                        className="w-10 h-10 rounded-lg object-cover border border-gray-200 flex-shrink-0"
                        onError={(e) => { e.target.style.display = 'none'; }}
                      />
                    ) : (
                      <Package className="w-4 h-4 text-indigo-500 flex-shrink-0" />
                    )}
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm font-medium text-gray-800 truncate">{product.name}</span>
                        {product.source === "tiktok_shop" && (
                          <span className="text-[8px] bg-gradient-to-r from-pink-500 to-red-500 text-white px-1.5 py-0.5 rounded-full flex-shrink-0 flex items-center gap-0.5">
                            <Globe className="w-2 h-2" />
                            TikTok
                          </span>
                        )}
                      </div>
                      {product.price && (
                        <span className="text-xs text-green-600 font-medium">{product.price}</span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    {product.original_url && (
                      <a
                        href={product.original_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="p-1 hover:bg-blue-50 rounded transition-colors"
                        title="Open in TikTok"
                      >
                        <ExternalLink className="w-3.5 h-3.5 text-gray-400 hover:text-blue-500" />
                      </a>
                    )}
                    <button
                      onClick={() => handleRemoveProduct(product.id)}
                      className="p-1 hover:bg-red-50 rounded transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5 text-gray-400 hover:text-red-500" />
                    </button>
                  </div>
                </div>
                {product.description && (
                  <p className="text-xs text-gray-500 mb-2 line-clamp-2">{product.description}</p>
                )}
                {product.selling_points && product.selling_points.length > 0 && (
                  <div className="flex flex-wrap gap-1 mb-2">
                    {product.selling_points.map((sp, i) => (
                      <span key={i} className="text-[9px] bg-amber-50 text-amber-700 px-1.5 py-0.5 rounded-full">
                        {sp}
                      </span>
                    ))}
                  </div>
                )}

                {/* Script Generation */}
                <div className="flex items-center gap-2 mb-2">
                  <select
                    value={scriptType}
                    onChange={(e) => setScriptType(e.target.value)}
                    className="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-600"
                  >
                    <option value="introduction">Introduction</option>
                    <option value="highlight">Highlight</option>
                    <option value="promotion">Promotion</option>
                    <option value="closing">Closing</option>
                  </select>
                  <select
                    value={scriptTone}
                    onChange={(e) => setScriptTone(e.target.value)}
                    className="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-600"
                  >
                    <option value="professional_friendly">Professional</option>
                    <option value="energetic">Energetic</option>
                    <option value="calm">Calm</option>
                    <option value="casual">Casual</option>
                  </select>
                  <button
                    onClick={() => handleGenerateScript(product)}
                    disabled={generatingScript === product.name}
                    className="flex items-center gap-1 text-[10px] bg-indigo-50 hover:bg-indigo-100 text-indigo-600 px-2 py-1 rounded transition-colors disabled:opacity-50"
                  >
                    {generatingScript === product.name ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Brain className="w-3 h-3" />
                    )}
                    Generate Script
                  </button>
                </div>

                {/* Generated Script Display */}
                {scripts[product.name] &&
                  Object.entries(scripts[product.name]).map(([type, text]) => (
                    <div key={type} className="bg-gray-50 rounded-lg p-2.5 mb-2">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[9px] uppercase tracking-wider text-gray-400 font-medium">
                          {type}
                        </span>
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => copyToClipboard(text)}
                            className="p-0.5 hover:bg-gray-200 rounded transition-colors"
                            title="Copy"
                          >
                            <Copy className="w-3 h-3 text-gray-400" />
                          </button>
                          <button
                            onClick={() => handleGenerateVideo(text, product.name, "product_intro")}
                            disabled={!sessionId || isGeneratingVideo}
                            className="flex items-center gap-0.5 text-[9px] bg-purple-50 hover:bg-purple-100 text-purple-600 px-1.5 py-0.5 rounded transition-colors disabled:opacity-50"
                            title="Generate video from this script"
                          >
                            {isGeneratingVideo ? (
                              <Loader2 className="w-2.5 h-2.5 animate-spin" />
                            ) : (
                              <Video className="w-2.5 h-2.5" />
                            )}
                            Video
                          </button>
                        </div>
                      </div>
                      <p className="text-xs text-gray-700 leading-relaxed whitespace-pre-wrap">
                        {text}
                      </p>
                      <p className="text-[9px] text-gray-400 mt-1">{text.length} chars</p>
                    </div>
                  ))}
              </div>
            ))}

            {/* Add Product Form */}
            {showAddProduct ? (
              <div className="border border-indigo-200 rounded-lg p-3 bg-indigo-50/30">
                <div className="space-y-2">
                  <input
                    type="text"
                    placeholder="Product name *"
                    value={newProduct.name}
                    onChange={(e) => setNewProduct((p) => ({ ...p, name: e.target.value }))}
                    className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                  <textarea
                    placeholder="Description (optional)"
                    value={newProduct.description}
                    onChange={(e) => setNewProduct((p) => ({ ...p, description: e.target.value }))}
                    rows={2}
                    className="w-full text-xs border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="text"
                      placeholder="Price (e.g., ¥3,980)"
                      value={newProduct.price}
                      onChange={(e) => setNewProduct((p) => ({ ...p, price: e.target.value }))}
                      className="text-xs border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                    <input
                      type="text"
                      placeholder="Features (comma-separated)"
                      value={newProduct.features}
                      onChange={(e) => setNewProduct((p) => ({ ...p, features: e.target.value }))}
                      className="text-xs border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleAddProduct}
                      disabled={!newProduct.name.trim()}
                      className="flex-1 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
                    >
                      Add Product
                    </button>
                    <button
                      onClick={() => {
                        setShowAddProduct(false);
                        setNewProduct({ name: "", description: "", price: "", features: "" });
                      }}
                      className="py-2 px-3 border border-gray-200 text-gray-600 text-xs rounded-lg hover:bg-gray-50 transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                {/* TikTok Shop Import */}
                <div className="border-2 border-dashed border-pink-300 hover:border-pink-400 rounded-lg p-3 bg-gradient-to-r from-pink-50/50 to-red-50/30 transition-colors">
                  <div className="flex items-center gap-2 mb-2">
                    <Globe className="w-4 h-4 text-pink-500" />
                    <span className="text-xs font-medium text-gray-700">TikTok Shop Import</span>
                    <span className="text-[8px] bg-pink-100 text-pink-600 px-1.5 py-0.5 rounded-full">AI Auto-Analyze</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      placeholder="Paste TikTok product URL (vt.tiktok.com/...)"
                      value={tiktokUrl}
                      onChange={(e) => { setTiktokUrl(e.target.value); setImportError(""); }}
                      onKeyDown={(e) => { if (e.key === "Enter") handleTiktokImport(); }}
                      className="flex-1 text-xs border border-pink-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pink-400 bg-white placeholder-gray-400"
                    />
                    <button
                      onClick={handleTiktokImport}
                      disabled={!tiktokUrl.trim() || isImportingTiktok}
                      className="flex items-center gap-1.5 px-3 py-2 bg-gradient-to-r from-pink-500 to-red-500 hover:from-pink-600 hover:to-red-600 text-white text-xs font-medium rounded-lg transition-all disabled:opacity-50 flex-shrink-0"
                    >
                      {isImportingTiktok ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Sparkles className="w-3.5 h-3.5" />
                      )}
                      {isImportingTiktok ? "Analyzing..." : "Import"}
                    </button>
                  </div>
                  {importError && (
                    <p className="text-[10px] text-red-500 mt-1.5 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3" />
                      {importError}
                    </p>
                  )}
                  <p className="text-[9px] text-gray-400 mt-1.5">
                    Supports: vt.tiktok.com/... or tiktok.com/view/product/...
                  </p>
                </div>

                {/* Manual Add Product */}
                <button
                  onClick={() => setShowAddProduct(true)}
                  className="w-full py-2 border border-gray-200 hover:border-indigo-300 rounded-lg text-xs text-gray-500 hover:text-indigo-600 flex items-center justify-center gap-1.5 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  Add Manually
                </button>
              </div>
            )}

            {/* Generate All Scripts Button */}
            {products.length > 0 && sessionId && (
              <button
                onClick={handleGenerateAllScripts}
                disabled={generatingScript === "__all__"}
                className="w-full py-2.5 bg-gradient-to-r from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white text-xs font-medium rounded-lg flex items-center justify-center gap-2 transition-all disabled:opacity-50"
              >
                {generatingScript === "__all__" ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Brain className="w-4 h-4" />
                )}
                Generate All Scripts (Sales Brain)
              </button>
            )}
          </div>
        )}

        {/* ══════════════════════════════════════════════ */}
        {/* Comments Tab */}
        {/* ══════════════════════════════════════════════ */}
        {activeTab === "comments" && (
          <div className="space-y-3">
            {/* Comment Input */}
            <div className="border border-gray-200 rounded-lg p-3">
              <div className="flex items-center gap-2 mb-2">
                <MessageSquare className="w-4 h-4 text-indigo-500" />
                <span className="text-xs font-medium text-gray-700">Viewer Comment</span>
              </div>
              <input
                type="text"
                placeholder="Viewer name (optional)"
                value={commenterName}
                onChange={(e) => setCommenterName(e.target.value)}
                className="w-full text-xs border border-gray-200 rounded-lg px-3 py-1.5 mb-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <div className="flex items-end gap-2">
                <textarea
                  placeholder="Enter viewer comment..."
                  value={commentText}
                  onChange={(e) => setCommentText(e.target.value)}
                  rows={2}
                  className="flex-1 text-xs border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleCommentResponse();
                    }
                  }}
                />
                <button
                  onClick={handleCommentResponse}
                  disabled={!commentText.trim() || isGeneratingReply}
                  className="p-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors disabled:opacity-50"
                >
                  {isGeneratingReply ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Send className="w-4 h-4" />
                  )}
                </button>
              </div>
              {sessionId && (
                <p className="text-[9px] text-gray-400 mt-1.5">
                  Auto-generates digital human video response (MuseTalk fast mode)
                </p>
              )}
            </div>

            {/* Comment History */}
            {commentHistory.length === 0 ? (
              <div className="text-center py-8 text-gray-400">
                <MessageSquare className="w-8 h-8 mx-auto mb-2 opacity-30" />
                <p className="text-xs">No comments yet</p>
                <p className="text-[10px] text-gray-300 mt-1">
                  Enter a viewer comment to generate an AI response
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {commentHistory.map((item, idx) => (
                  <div key={idx} className="border border-gray-100 rounded-lg p-3 space-y-2">
                    {/* Comment */}
                    <div className="flex items-start gap-2">
                      <div className="w-6 h-6 bg-gray-200 rounded-full flex items-center justify-center shrink-0">
                        <span className="text-[9px] text-gray-500">
                          {item.commenter ? item.commenter[0].toUpperCase() : "?"}
                        </span>
                      </div>
                      <div>
                        {item.commenter && (
                          <span className="text-[10px] font-medium text-gray-600">
                            {item.commenter}
                          </span>
                        )}
                        <p className="text-xs text-gray-700">{item.comment}</p>
                      </div>
                    </div>
                    {/* AI Reply */}
                    <div className="flex items-start gap-2 pl-2 border-l-2 border-indigo-200">
                      <div className="w-6 h-6 bg-indigo-100 rounded-full flex items-center justify-center shrink-0">
                        <Sparkles className="w-3 h-3 text-indigo-500" />
                      </div>
                      <div>
                        <span className="text-[10px] font-medium text-indigo-600">AI Host</span>
                        <p className="text-xs text-gray-700">{item.reply}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <button
                            onClick={() => copyToClipboard(item.reply)}
                            className="text-[9px] text-gray-400 hover:text-gray-600 flex items-center gap-0.5"
                          >
                            <Copy className="w-2.5 h-2.5" /> Copy
                          </button>
                          {item.job_id && (
                            <span className="text-[9px] text-indigo-400 flex items-center gap-0.5">
                              <Video className="w-2.5 h-2.5" /> Video: {item.job_id}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ══════════════════════════════════════════════ */}
        {/* Queue Tab */}
        {/* ══════════════════════════════════════════════ */}
        {activeTab === "queue" && (
          <div className="space-y-3">
            {/* Queue Summary */}
            <div className="grid grid-cols-3 gap-2">
              {[
                {
                  label: "Total",
                  value: videoQueue.length,
                  color: "text-gray-700",
                  bg: "bg-gray-50",
                },
                {
                  label: "Processing",
                  value: videoQueue.filter((q) => q.status === "processing").length,
                  color: "text-blue-600",
                  bg: "bg-blue-50",
                },
                {
                  label: "Ready",
                  value: videoQueue.filter((q) => q.status === "completed").length,
                  color: "text-green-600",
                  bg: "bg-green-50",
                },
              ].map((stat) => (
                <div key={stat.label} className={`${stat.bg} rounded-lg p-2 text-center`}>
                  <p className={`text-lg font-bold ${stat.color}`}>{stat.value}</p>
                  <p className="text-[9px] text-gray-500">{stat.label}</p>
                </div>
              ))}
            </div>

            {/* Queue Items */}
            {videoQueue.length === 0 ? (
              <div className="text-center py-8 text-gray-400">
                <ListOrdered className="w-8 h-8 mx-auto mb-2 opacity-30" />
                <p className="text-xs">No videos in queue</p>
                <p className="text-[10px] text-gray-300 mt-1">
                  Generate scripts and click "Video" to add to queue
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {videoQueue.map((item, idx) => (
                  <div
                    key={item.job_id || idx}
                    className={`border rounded-lg p-3 transition-colors ${
                      item.status === "completed"
                        ? "border-green-200 bg-green-50/30"
                        : item.status === "processing"
                        ? "border-blue-200 bg-blue-50/30"
                        : item.status === "error"
                        ? "border-red-200 bg-red-50/30"
                        : "border-gray-200"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        {getQueueStatusIcon(item.status)}
                        <span className="text-xs font-medium text-gray-700">
                          #{idx + 1} — {getQueueTypeLabel(item.type)}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        {item.status === "completed" && (
                          <button
                            onClick={() => {
                              const engine_prefix = engine === "imtalker" ? "imtalker" : "musetalk";
                              const url = aiLiveCreatorService.getDownloadUrl(item.job_id, engine_prefix);
                              window.open(url, "_blank");
                            }}
                            className="p-1 hover:bg-green-100 rounded transition-colors"
                            title="Download"
                          >
                            <Download className="w-3.5 h-3.5 text-green-600" />
                          </button>
                        )}
                      </div>
                    </div>
                    {item.product_name && (
                      <p className="text-[10px] text-indigo-500 mb-0.5">{item.product_name}</p>
                    )}
                    {item.text_preview && (
                      <p className="text-[10px] text-gray-500 line-clamp-2">{item.text_preview}</p>
                    )}
                    <p className="text-[9px] text-gray-400 mt-1 font-mono">{item.job_id}</p>
                    {item.progress != null && item.status === "processing" && (
                      <div className="mt-1.5 h-1 bg-gray-200 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-blue-500 rounded-full transition-all duration-500"
                          style={{ width: `${item.progress}%` }}
                        />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Refresh Queue */}
            {sessionId && (
              <button
                onClick={async () => {
                  try {
                    const res = await aiLiveCreatorService.getSessionQueue(sessionId);
                    if (res.success && res.queue) setVideoQueue(res.queue);
                  } catch {}
                }}
                className="w-full py-2 border border-gray-200 text-gray-600 text-xs rounded-lg hover:bg-gray-50 flex items-center justify-center gap-1.5 transition-colors"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                Refresh Queue
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
});

export default LiveStreamPanel;
