import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  FileText,
  Sparkles,
  Upload,
  Loader2,
  Copy,
  Check,
  TrendingUp,
  Clock,
  MessageSquare,
  Image as ImageIcon,
  X,
  ChevronDown,
  ChevronUp,
  Zap,
  Plus,
  Gift,
  Tag,
  Star,
  ThumbsUp,
  ThumbsDown,
  Send,
  History,
  ChevronRight,
  Calendar,
  Eye,
} from "lucide-react";
import scriptGeneratorService from "../base/services/scriptGeneratorService";

/**
 * ScriptGeneratorPage - Standalone "売れる台本" tool
 *
 * Users input product info (name, images, description, prices, benefits)
 * and the system generates a live commerce script grounded in
 * real performance data from AitherHub's analysis database.
 */
export default function ScriptGeneratorPage() {
  const navigate = useNavigate();

  // ── Form State ──
  const [productName, setProductName] = useState("");
  const [productDescription, setProductDescription] = useState("");
  const [originalPrice, setOriginalPrice] = useState("");
  const [discountedPrice, setDiscountedPrice] = useState("");
  const [targetAudience, setTargetAudience] = useState("");
  const [benefits, setBenefits] = useState("");
  const [additionalInstructions, setAdditionalInstructions] = useState("");
  const [tone, setTone] = useState("professional_friendly");
  const [language, setLanguage] = useState("ja");
  const [durationMinutes, setDurationMinutes] = useState(10);

  // Multiple image upload
  const [imageFiles, setImageFiles] = useState([]); // [{file, preview, url, uploading}]

  // Generation state
  const [isGenerating, setIsGenerating] = useState(false);
  const [generatedScript, setGeneratedScript] = useState(null);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  // Progress bar state
  const [generationStep, setGenerationStep] = useState(0); // 0-5
  const [generationProgress, setGenerationProgress] = useState(0); // 0-100

  // Rating state
  const [ratingValue, setRatingValue] = useState(0);
  const [ratingHover, setRatingHover] = useState(0);
  const [ratingComment, setRatingComment] = useState("");
  const [ratingGoodTags, setRatingGoodTags] = useState([]);
  const [ratingBadTags, setRatingBadTags] = useState([]);
  const [isSubmittingRating, setIsSubmittingRating] = useState(false);
  const [ratingSubmitted, setRatingSubmitted] = useState(false);

  // Winning patterns preview
  const [patterns, setPatterns] = useState(null);
  const [patternsLoading, setPatternsLoading] = useState(false);
  const [showPatterns, setShowPatterns] = useState(false);

  // Generation history
  const [historyList, setHistoryList] = useState([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [historyDetailLoading, setHistoryDetailLoading] = useState(null); // script_id being loaded

  // ── Load winning patterns + history on mount ──
  useEffect(() => {
    loadPatterns();
    loadHistory();
  }, []);

  const loadPatterns = async () => {
    setPatternsLoading(true);
    try {
      const data = await scriptGeneratorService.getWinningPatterns(50);
      setPatterns(data);
    } catch (e) {
      console.warn("Failed to load winning patterns:", e);
    } finally {
      setPatternsLoading(false);
    }
  };

  const loadHistory = async () => {
    setHistoryLoading(true);
    try {
      const data = await scriptGeneratorService.getHistory(20, 0);
      setHistoryList(data.generations || []);
      setHistoryTotal(data.total || 0);
    } catch (e) {
      console.warn("Failed to load history:", e);
    } finally {
      setHistoryLoading(false);
    }
  };

  const loadHistoryDetail = async (scriptId) => {
    setHistoryDetailLoading(scriptId);
    try {
      const detail = await scriptGeneratorService.getHistoryDetail(scriptId);
      // Set the detail as the current generated script
      setGeneratedScript({
        script: detail.generated_script,
        char_count: detail.char_count,
        estimated_duration_minutes: detail.duration_minutes,
        model: detail.model_used,
        script_id: detail.id,
        patterns_used: detail.patterns_used,
        product_analysis: detail.product_analysis,
      });
      // Also populate the form fields
      setProductName(detail.product_name || "");
      setProductDescription(detail.product_description || "");
      setOriginalPrice(detail.original_price || "");
      setDiscountedPrice(detail.discounted_price || "");
      setTargetAudience(detail.target_audience || "");
      setBenefits(detail.benefits || "");
      if (detail.tone) setTone(detail.tone);
      if (detail.language) setLanguage(detail.language);
      if (detail.duration_minutes) setDurationMinutes(detail.duration_minutes);
      // Set rating if exists
      if (detail.rating) {
        setRatingValue(detail.rating);
        setRatingComment(detail.rating_comment || "");
        setRatingGoodTags(detail.rating_good_tags || []);
        setRatingBadTags(detail.rating_bad_tags || []);
        setRatingSubmitted(true);
      } else {
        setRatingValue(0);
        setRatingComment("");
        setRatingGoodTags([]);
        setRatingBadTags([]);
        setRatingSubmitted(false);
      }
    } catch (e) {
      console.error("Failed to load history detail:", e);
      alert(window.__t('scriptGeneratorPage_26c6a0', '履歴の読み込みに失敗しました'));
    } finally {
      setHistoryDetailLoading(null);
    }
  };

  // ── Multiple Image Upload ──
  const handleImageSelect = (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;

    const newImages = files.map((file) => ({
      file,
      preview: URL.createObjectURL(file),
      url: null,
      uploading: false,
    }));

    setImageFiles((prev) => [...prev, ...newImages].slice(0, 10)); // max 10 images
    // Reset the input so the same file can be re-selected
    e.target.value = "";
  };

  const removeImage = (index) => {
    setImageFiles((prev) => {
      const updated = [...prev];
      if (updated[index]?.preview) {
        URL.revokeObjectURL(updated[index].preview);
      }
      updated.splice(index, 1);
      return updated;
    });
  };

  const uploadAllImages = async () => {
    const uploaded = [];
    for (let i = 0; i < imageFiles.length; i++) {
      const img = imageFiles[i];
      if (img.url) {
        uploaded.push(img.url);
        continue;
      }
      try {
        setImageFiles((prev) => {
          const updated = [...prev];
          if (updated[i]) updated[i] = { ...updated[i], uploading: true };
          return updated;
        });
        const blobUrl = await scriptGeneratorService.uploadProductImage(img.file);
        uploaded.push(blobUrl);
        setImageFiles((prev) => {
          const updated = [...prev];
          if (updated[i]) updated[i] = { ...updated[i], url: blobUrl, uploading: false };
          return updated;
        });
      } catch (e) {
        console.warn(`Image upload failed for image ${i}:`, e);
        setImageFiles((prev) => {
          const updated = [...prev];
          if (updated[i]) updated[i] = { ...updated[i], uploading: false };
          return updated;
        });
      }
    }
    return uploaded;
  };

  // ── Generate Script ──
  const handleGenerate = async () => {
    if (!productName.trim()) {
      setError(window.__t('scriptGeneratorPage_083feb', '商品名を入力してください'));
      return;
    }

    setIsGenerating(true);
    setError(null);
    setGeneratedScript(null);
    setRatingValue(0);
    setRatingHover(0);
    setRatingComment("");
    setRatingGoodTags([]);
    setRatingBadTags([]);
    setRatingSubmitted(false);
    setGenerationStep(0);
    setGenerationProgress(0);

    // Step 1: Upload images
    setGenerationStep(1);
    setGenerationProgress(5);
    let imageUrls = [];
    if (imageFiles.length > 0) {
      try {
        imageUrls = await uploadAllImages();
        setGenerationProgress(15);
      } catch (e) {
        console.warn("Some image uploads failed, continuing:", e);
      }
    } else {
      setGenerationProgress(15);
    }

    // Step 2: Prepare data
    setGenerationStep(2);
    setGenerationProgress(20);

    // Build price string
    let priceStr = "";
    if (originalPrice.trim() && discountedPrice.trim()) {
      priceStr = `通常価格: ${originalPrice.trim()} → 配信特別価格: ${discountedPrice.trim()}`;
    } else if (discountedPrice.trim()) {
      priceStr = discountedPrice.trim();
    } else if (originalPrice.trim()) {
      priceStr = originalPrice.trim();
    }

    // Step 3: Generate (this is the long step)
    setGenerationStep(3);
    setGenerationProgress(25);

    // Start a fake progress timer for the long generation step
    const progressInterval = setInterval(() => {
      setGenerationProgress((prev) => {
        if (prev >= 90) return prev; // cap at 90 until done
        // Slow down as we approach 90
        const increment = prev < 50 ? 2 : prev < 70 ? 1 : 0.5;
        return Math.min(prev + increment, 90);
      });
      // Update step based on progress
      setGenerationProgress((prev) => {
        if (prev >= 60) setGenerationStep(4);
        return prev;
      });
    }, 1500);

    try {
      const result = await scriptGeneratorService.generateScript({
        product_name: productName.trim(),
        product_image_url: imageUrls[0] || undefined,
        product_image_urls: imageUrls.length > 0 ? imageUrls : undefined,
        product_description: productDescription.trim() || undefined,
        product_price: priceStr || undefined,
        original_price: originalPrice.trim() || undefined,
        discounted_price: discountedPrice.trim() || undefined,
        benefits: benefits.trim() || undefined,
        target_audience: targetAudience.trim() || undefined,
        tone,
        language,
        duration_minutes: durationMinutes,
        additional_instructions: additionalInstructions.trim() || undefined,
      });
      clearInterval(progressInterval);
      setGenerationStep(5);
      setGenerationProgress(100);
      setGeneratedScript(result);
      // Refresh history after generation
      loadHistory();
    } catch (e) {
      clearInterval(progressInterval);
      console.error("Script generation failed:", e);
      const msg = e?.response?.data?.detail || e.message || window.__t('scriptGen_generateFailed', '台本の生成に失敗しました');
      setError(msg);
    } finally {
      setIsGenerating(false);
    }
  };

  // ── Copy to clipboard ──
  const handleCopy = useCallback(() => {
    if (!generatedScript?.script) return;
    navigator.clipboard.writeText(generatedScript.script);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [generatedScript]);

  // ── Submit Rating ──
  const GOOD_TAG_OPTIONS = [
    window.__t('scriptGeneratorPage_689eb7', '構成が良い'), window.__t('scriptGeneratorPage_f1bcab', 'CTA効果的'), window.__t('scriptGeneratorPage_7a2162', '自然な話し方'), window.__t('scriptGeneratorPage_144367', '商品説明が的確'),
    window.__t('scriptGeneratorPage_8a98a5', '時間配分が良い'), window.__t('scriptGeneratorPage_9f0d26', '視聴者交流が良い'), window.__t('scriptGeneratorPage_25e67b', '特典の訴求が良い')
  ];
  const BAD_TAG_OPTIONS = [
    window.__t('scriptGeneratorPage_01a760', '内容が薄い'), window.__t('scriptGeneratorPage_a5c3aa', '不自然な表現'), window.__t('scriptGeneratorPage_f4dbda', 'CTA弱い'), window.__t('scriptGeneratorPage_c4c1cd', '商品説明不足'),
    window.__t('scriptGeneratorPage_7c6774', '長すぎる'), window.__t('scriptGeneratorPage_0b27b4', '短すぎる'), window.__t('scriptGeneratorPage_3a36db', '構成が悪い'), window.__t('scriptGeneratorPage_ec5a58', '特典の訴求が弱い')
  ];

  const toggleTag = (tag, list, setter) => {
    setter(prev => prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]);
  };

  const handleSubmitRating = async () => {
    if (!generatedScript?.script_id || ratingValue === 0) return;
    setIsSubmittingRating(true);
    try {
      await scriptGeneratorService.rateScript(generatedScript.script_id, {
        rating: ratingValue,
        comment: ratingComment.trim() || undefined,
        good_tags: ratingGoodTags.length > 0 ? ratingGoodTags : undefined,
        bad_tags: ratingBadTags.length > 0 ? ratingBadTags : undefined,
      });
      setRatingSubmitted(true);
    } catch (e) {
      console.error("Rating submission failed:", e);
      alert(window.__t('scriptGeneratorPage_ffb7ff', '評価の送信に失敗しました。もう一度お試しください。'));
    } finally {
      setIsSubmittingRating(false);
    }
  };

  // ── Render formatted script (separate lines/dialogue/direction) ──
  const renderFormattedScript = (scriptText) => {
    if (!scriptText) return null;
    const lines = scriptText.split("\n");
    return lines.map((line, idx) => {
      const trimmed = line.trim();
      if (!trimmed) return <div key={idx} className="h-2" />;

      // Section header: ⏱ ...
      if (trimmed.startsWith("\u23F1") || trimmed.match(/^[\u{23F0}-\u{23FF}\u{1F550}-\u{1F567}]/u)) {
        return (
          <div key={idx} className="mt-5 mb-2 px-3 py-2 bg-gradient-to-r from-orange-50 to-amber-50 border-l-4 border-orange-400 rounded-r-lg">
            <span className="text-sm font-bold text-orange-800">{trimmed}</span>
          </div>
        );
      }

      // Dialogue: 🎤 ...
      if (trimmed.startsWith("\uD83C\uDFA4") || trimmed.startsWith("🎤")) {
        const quoteMatch = trimmed.match(/[「「](.+?)[」」]/);
        return (
          <div key={idx} className="flex items-start gap-2 ml-2 my-1 px-3 py-2 bg-blue-50 border-l-3 border-blue-400 rounded-r-lg">
            <span className="text-lg flex-shrink-0 mt-0.5">🎤</span>
            <div className="text-sm text-gray-800">
              {quoteMatch ? (
                <span className="font-medium">「{quoteMatch[1]}」</span>
              ) : (
                <span>{trimmed.replace(/^🎤\s*/, "")}</span>
              )}
            </div>
          </div>
        );
      }

      // Stage direction: 📋 ...
      if (trimmed.startsWith("\uD83D\uDCCB") || trimmed.startsWith("📋")) {
        return (
          <div key={idx} className="flex items-start gap-2 ml-2 my-1 px-3 py-1.5 bg-gray-50 rounded-lg border border-dashed border-gray-300">
            <span className="text-lg flex-shrink-0">📋</span>
            <span className="text-xs text-gray-500 italic">{trimmed.replace(/^📋\s*/, "")}</span>
          </div>
        );
      }

      // Section markers like 【...】
      if (trimmed.startsWith("【") || trimmed.match(/^##\s/)) {
        return (
          <div key={idx} className="mt-5 mb-2 px-3 py-2 bg-gradient-to-r from-orange-50 to-amber-50 border-l-4 border-orange-400 rounded-r-lg">
            <span className="text-sm font-bold text-orange-800">{trimmed}</span>
          </div>
        );
      }

      // Default text
      return (
        <div key={idx} className="text-sm text-gray-700 ml-2 my-0.5 leading-relaxed">
          {trimmed}
        </div>
      );
    });
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex items-center gap-3">
          <button
            onClick={() => navigate("/")}
            className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-gray-600" />
          </button>
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-orange-400 to-red-500 flex items-center justify-center">
              <FileText className="w-4 h-4 text-white" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-gray-900">{window.__t('sidebar_scriptGenerator', '売れる台本')}</h1>
              <p className="text-xs text-gray-500">{window.__t('scriptGeneratorPage_dfe294', '実績データに基づくライブコマース台本生成AI')}</p>
            </div>
          </div>
          {patterns && (
            <div className="ml-auto flex items-center gap-1.5 text-xs text-gray-500 bg-gray-100 px-2.5 py-1 rounded-full">
              <TrendingUp className="w-3 h-3" />
              <span>{patterns.videos_analyzed}本の配信データ学習済み</span>
            </div>
          )}
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">

          {/* ── Left: Input Form ── */}
          <div className="lg:col-span-2 space-y-4">

            {/* Product Name (Required) */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">
                商品名 <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={productName}
                onChange={(e) => setProductName(e.target.value)}
                placeholder={window.__t('scriptGeneratorPage_7cb419', '例: KYOGOKU ケラチンシャンプー')}
                className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
              />
            </div>

            {/* Product Images (Multiple) */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">
                商品写真 <span className="text-gray-400 font-normal">{window.__t('scriptGeneratorPage_3ff100', '(任意・複数可)')}</span>
              </label>
              <p className="text-xs text-gray-500">{window.__t('scriptGeneratorPage_df806d', 'AIが商品の特徴を読み取り、台本に反映します（最大10枚）')}</p>

              {/* Image Grid */}
              <div className="grid grid-cols-3 gap-2">
                {imageFiles.map((img, index) => (
                  <div key={index} className="relative group aspect-square">
                    <img
                      src={img.preview}
                      alt={`Product ${index + 1}`}
                      className="w-full h-full object-cover rounded-lg border border-gray-200"
                    />
                    {/* Remove button */}
                    <button
                      onClick={() => removeImage(index)}
                      className="absolute top-1 right-1 p-0.5 bg-white rounded-full shadow-md opacity-0 group-hover:opacity-100 transition-opacity"
                    >
                      <X className="w-3 h-3 text-gray-600" />
                    </button>
                    {/* Upload status */}
                    {img.uploading && (
                      <div className="absolute inset-0 bg-black/30 rounded-lg flex items-center justify-center">
                        <Loader2 className="w-5 h-5 text-white animate-spin" />
                      </div>
                    )}
                    {img.url && (
                      <div className="absolute bottom-1 left-1 bg-green-500 text-white text-[10px] px-1.5 py-0.5 rounded-full flex items-center gap-0.5">
                        <Check className="w-2.5 h-2.5" />
                      </div>
                    )}
                    {/* Image number */}
                    <div className="absolute top-1 left-1 bg-black/50 text-white text-[10px] px-1.5 py-0.5 rounded-full">
                      {index + 1}
                    </div>
                  </div>
                ))}

                {/* Add image button */}
                {imageFiles.length < 10 && (
                  <label className="aspect-square flex flex-col items-center justify-center border-2 border-dashed border-gray-300 rounded-lg cursor-pointer hover:border-orange-400 hover:bg-orange-50 transition-colors">
                    <Plus className="w-6 h-6 text-gray-400 mb-0.5" />
                    <span className="text-[10px] text-gray-400">{window.__t('analytics_add', '追加')}</span>
                    <input
                      type="file"
                      accept="image/*"
                      multiple
                      onChange={handleImageSelect}
                      className="hidden"
                    />
                  </label>
                )}
              </div>

              {imageFiles.length === 0 && (
                <label className="flex flex-col items-center justify-center w-full h-28 border-2 border-dashed border-gray-300 rounded-lg cursor-pointer hover:border-orange-400 hover:bg-orange-50 transition-colors">
                  <ImageIcon className="w-7 h-7 text-gray-400 mb-1" />
                  <span className="text-xs text-gray-500">クリックして画像を選択</span>
                  <span className="text-[10px] text-gray-400 mt-0.5">複数選択可</span>
                  <input
                    type="file"
                    accept="image/*"
                    multiple
                    onChange={handleImageSelect}
                    className="hidden"
                  />
                </label>
              )}
            </div>

            {/* Product Details - Price Split */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">{window.__t('scriptGeneratorPage_1bf863', '商品詳細')}</label>
              <div className="space-y-2">
                {/* Price fields - side by side */}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-xs text-gray-500 mb-1 block flex items-center gap-1">
                      <Tag className="w-3 h-3" /> 販売価格
                    </label>
                    <input
                      type="text"
                      value={originalPrice}
                      onChange={(e) => setOriginalPrice(e.target.value)}
                      placeholder={window.__t('scriptGeneratorPage_fc2ec3', '例: ¥5,980')}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-gray-500 mb-1 block flex items-center gap-1">
                      <Zap className="w-3 h-3 text-red-500" /> 割引後価格
                    </label>
                    <input
                      type="text"
                      value={discountedPrice}
                      onChange={(e) => setDiscountedPrice(e.target.value)}
                      placeholder={window.__t('scriptGeneratorPage_0c5cad', '例: ¥3,980')}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
                    />
                  </div>
                </div>

                {/* Show discount badge if both prices are filled */}
                {originalPrice.trim() && discountedPrice.trim() && (
                  <div className="flex items-center gap-1.5 text-xs">
                    <span className="bg-red-100 text-red-600 px-2 py-0.5 rounded-full font-medium">
                      配信限定価格
                    </span>
                    <span className="text-gray-400 line-through">{originalPrice}</span>
                    <span className="text-gray-600">→</span>
                    <span className="text-red-600 font-bold">{discountedPrice}</span>
                  </div>
                )}

                <textarea
                  value={productDescription}
                  onChange={(e) => setProductDescription(e.target.value)}
                  placeholder={window.__t('scriptGeneratorPage_0d9a33', '商品の特徴・説明（任意）')}
                  rows={3}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent resize-none"
                />
                <input
                  type="text"
                  value={targetAudience}
                  onChange={(e) => setTargetAudience(e.target.value)}
                  placeholder={window.__t('scriptGeneratorPage_2e2c42', 'ターゲット層（例: 30代女性、髪のダメージが気になる方）')}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
                />
              </div>
            </div>

            {/* Benefits / Tokuten */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800 flex items-center gap-1.5">
                <Gift className="w-4 h-4 text-orange-500" />
                特典 <span className="text-gray-400 font-normal">{window.__t('scriptGeneratorPage_8d9b0b', '(任意)')}</span>
              </label>
              <p className="text-xs text-gray-500">{window.__t('scriptGeneratorPage_8ac449', '配信限定の特典・おまけ・キャンペーン情報')}</p>
              <textarea
                value={benefits}
                onChange={(e) => setBenefits(e.target.value)}
                placeholder={window.__t('scriptGeneratorPage_af44d3', '例: 配信限定20%OFF、2個セットで送料無料、先着100名にサンプルプレゼント')}
                rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent resize-none"
              />
            </div>

            {/* Settings */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">{window.__t('scriptGen_scriptSettingsTitle', '台本設定')}</label>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs text-gray-500 mb-1 block">{window.__t('scriptGen_toneLabel', 'トーン')}</label>
                  <select
                    value={tone}
                    onChange={(e) => setTone(e.target.value)}
                    className="w-full px-2 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
                  >
                    <option value="professional_friendly">{window.__t('scriptGeneratorPage_ea2102', 'プロ＆親しみ')}</option>
                    <option value="energetic">{window.__t('autoVideoPage_3f558c', 'エネルギッシュ')}</option>
                    <option value="calm">{window.__t('scriptGeneratorPage_f16566', '落ち着き')}</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-500 mb-1 block">{window.__t('script_language', '言語')}</label>
                  <select
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                    className="w-full px-2 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
                  >
                    <option value="ja">{window.__t('language_japanese', '日本語')}</option>
                    <option value="zh">{window.__t('scriptGen_langZh', '中文')}</option>
                    <option value="en">English</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">
                  台本の長さ: 約{durationMinutes}分（{durationMinutes * 250}文字）
                </label>
                <input
                  type="range"
                  min={1}
                  max={60}
                  value={durationMinutes}
                  onChange={(e) => setDurationMinutes(Number(e.target.value))}
                  className="w-full accent-orange-500"
                />
                <div className="flex justify-between text-xs text-gray-400">
                  <span>{window.__t('scriptGeneratorPage_de8203', '1分')}</span>
                  <span>{window.__t('scriptGeneratorPage_a872fb', '30分')}</span>
                  <span>{window.__t('scriptGeneratorPage_fa1f81', '60分')}</span>
                </div>
              </div>
            </div>

            {/* Additional Instructions */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">
                追加指示 <span className="text-gray-400 font-normal">{window.__t('scriptGeneratorPage_8d9b0b', '(任意)')}</span>
              </label>
              <textarea
                value={additionalInstructions}
                onChange={(e) => setAdditionalInstructions(e.target.value)}
                placeholder={window.__t('scriptGeneratorPage_0b6e4a', '例: 最初に自己紹介を入れてほしい、特定のキャンペーンに触れてほしい等')}
                rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent resize-none"
              />
            </div>

            {/* Generate Button */}
            <button
              onClick={handleGenerate}
              disabled={isGenerating || !productName.trim()}
              className={`w-full py-3.5 rounded-xl text-white font-semibold text-sm flex items-center justify-center gap-2 transition-all ${
                isGenerating || !productName.trim()
                  ? "bg-gray-300 cursor-not-allowed"
                  : "bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-600 hover:to-red-600 shadow-lg hover:shadow-xl"
              }`}
            >
              {isGenerating ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  台本を生成中...（最大2分）
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" />
                  売れる台本を生成する
                </>
              )}
            </button>

            {/* ── Progress Bar (visible during generation) ── */}
            {isGenerating && (
              <div className="bg-gradient-to-br from-orange-50 to-amber-50 border border-orange-200 rounded-xl p-4 space-y-3 animate-in fade-in duration-300">
                {/* Progress bar */}
                <div className="relative w-full h-2.5 bg-orange-100 rounded-full overflow-hidden">
                  <div
                    className="absolute inset-y-0 left-0 bg-gradient-to-r from-orange-400 to-red-500 rounded-full transition-all duration-700 ease-out"
                    style={{ width: `${generationProgress}%` }}
                  />
                  <div
                    className="absolute inset-y-0 left-0 bg-gradient-to-r from-white/30 to-transparent rounded-full animate-pulse"
                    style={{ width: `${generationProgress}%` }}
                  />
                </div>

                {/* Progress percentage */}
                <div className="flex items-center justify-between text-xs">
                  <span className="text-orange-700 font-medium">
                    {generationProgress < 100 ? `${Math.round(generationProgress)}%` : window.__t('scriptGeneratorPage_03800b', '✅ 完了')}
                  </span>
                  <span className="text-orange-500">
                    {generationStep <= 1 && window.__t('scriptGeneratorPage_ea7b59', '↑ 画像アップロード中...')}
                    {generationStep === 2 && window.__t('scriptGeneratorPage_9c1812', '📊 データ準備中...')}
                    {generationStep === 3 && window.__t('scriptGeneratorPage_e60bf6', '🧠 AIが台本を生成中...')}
                    {generationStep === 4 && window.__t('scriptGeneratorPage_9f8bc7', '✨ 台本を仕上げ中...')}
                    {generationStep >= 5 && window.__t('scriptGeneratorPage_bdc498', '✅ 完了！')}
                  </span>
                </div>

                {/* Step indicators */}
                <div className="grid grid-cols-5 gap-1">
                  {[
                    { step: 1, icon: '📷', label: window.__t('scriptGeneratorPage_39a213', '画像アップロード') },
                    { step: 2, icon: '📊', label: window.__t('scriptGeneratorPage_189bb6', 'データ収集') },
                    { step: 3, icon: '🧠', label: window.__t('scriptGeneratorPage_eb55e9', 'AI分析・生成') },
                    { step: 4, icon: '✨', label: window.__t('scriptGeneratorPage_501765', '台本仕上げ') },
                    { step: 5, icon: '✅', label: window.__t('clip_completed', '完了') },
                  ].map(({ step, icon, label }) => (
                    <div
                      key={step}
                      className={`flex flex-col items-center gap-0.5 py-1.5 rounded-lg text-center transition-all duration-300 ${
                        generationStep >= step
                          ? generationStep === step
                            ? 'bg-orange-100 border border-orange-300 scale-105'
                            : 'bg-green-50 border border-green-200'
                          : 'bg-white/60 border border-gray-100'
                      }`}
                    >
                      <span className={`text-base ${
                        generationStep === step ? 'animate-bounce' : ''
                      }`}>{icon}</span>
                      <span className={`text-[10px] leading-tight ${
                        generationStep >= step
                          ? generationStep === step ? 'text-orange-700 font-semibold' : 'text-green-700'
                          : 'text-gray-400'
                      }`}>{label}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {error && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
                {error}
              </div>
            )}
          </div>

          {/* ── Right: Output & Patterns ── */}
          <div className="lg:col-span-3 space-y-4">

            {/* Generation History */}
            <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <button
                onClick={() => setShowHistory(!showHistory)}
                className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-center gap-2">
                  <History className="w-4 h-4 text-indigo-500" />
                  <span className="text-sm font-semibold text-gray-800">{window.__t('autoVideoPage_0c10ff', '生成履歴')}</span>
                  {historyTotal > 0 && (
                    <span className="text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-full">
                      {historyTotal}件
                    </span>
                  )}
                  {historyLoading && <Loader2 className="w-3 h-3 animate-spin text-gray-400" />}
                </div>
                {showHistory ? (
                  <ChevronUp className="w-4 h-4 text-gray-400" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-gray-400" />
                )}
              </button>
              {showHistory && (
                <div className="border-t border-gray-100">
                  {historyList.length === 0 ? (
                    <div className="px-4 py-6 text-center text-sm text-gray-400">
                      まだ生成履歴がありません
                    </div>
                  ) : (
                    <div className="divide-y divide-gray-100 max-h-[320px] overflow-y-auto">
                      {historyList.map((item) => (
                        <button
                          key={item.id}
                          onClick={() => loadHistoryDetail(item.id)}
                          disabled={historyDetailLoading === item.id}
                          className="w-full px-4 py-3 flex items-center gap-3 hover:bg-gray-50 transition-colors text-left group"
                        >
                          {/* Product icon */}
                          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-100 to-purple-100 flex items-center justify-center flex-shrink-0">
                            <FileText className="w-4 h-4 text-indigo-500" />
                          </div>
                          {/* Info */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-gray-800 truncate">
                                {item.product_name}
                              </span>
                              {item.rating && (
                                <span className="flex items-center gap-0.5 text-xs">
                                  <Star className="w-3 h-3 text-yellow-400 fill-yellow-400" />
                                  <span className="text-yellow-600">{item.rating}</span>
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-2 mt-0.5">
                              <span className="text-xs text-gray-400 flex items-center gap-1">
                                <Calendar className="w-3 h-3" />
                                {new Date(item.created_at).toLocaleDateString('ja-JP', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                              </span>
                              <span className="text-xs text-gray-400">
                                {item.char_count}文字 / {item.duration_minutes}分
                              </span>
                            </div>
                          </div>
                          {/* Arrow / Loading */}
                          {historyDetailLoading === item.id ? (
                            <Loader2 className="w-4 h-4 animate-spin text-indigo-400 flex-shrink-0" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-indigo-400 transition-colors flex-shrink-0" />
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Winning Patterns Preview */}
            <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <button
                onClick={() => setShowPatterns(!showPatterns)}
                className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-center gap-2">
                  <Zap className="w-4 h-4 text-orange-500" />
                  <span className="text-sm font-semibold text-gray-800">{window.__t('scriptGeneratorPage_de2238', '学習済み勝ちパターン')}</span>
                  {patternsLoading && <Loader2 className="w-3 h-3 animate-spin text-gray-400" />}
                </div>
                {showPatterns ? (
                  <ChevronUp className="w-4 h-4 text-gray-400" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-gray-400" />
                )}
              </button>
              {showPatterns && patterns && (
                <div className="px-4 pb-4 space-y-3 border-t border-gray-100">
                  {/* CTA Phrases */}
                  {patterns.cta_phrases?.length > 0 && (
                    <div className="mt-3">
                      <h4 className="text-xs font-semibold text-gray-500 mb-1.5 flex items-center gap-1">
                        <MessageSquare className="w-3 h-3" /> 売れたCTAパターン
                      </h4>
                      <div className="flex flex-wrap gap-1.5">
                        {patterns.cta_phrases.slice(0, 8).map((cta, i) => (
                          <span
                            key={i}
                            className="inline-flex items-center gap-1 px-2 py-1 bg-orange-50 text-orange-700 rounded-full text-xs"
                          >
                            {cta.pattern}
                            <span className="text-orange-400">({cta.occurrence_count}回)</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {/* Duration Insights */}
                  {patterns.duration_insights?.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold text-gray-500 mb-1.5 flex items-center gap-1">
                        <Clock className="w-3 h-3" /> 商品説明の最適時間
                      </h4>
                      <div className="space-y-1">
                        {patterns.duration_insights.slice(0, 5).map((d, i) => (
                          <div key={i} className="text-xs text-gray-600 bg-gray-50 px-2 py-1 rounded">
                            {d.category}: {d.value}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {/* Top Techniques */}
                  {patterns.top_techniques?.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold text-gray-500 mb-1.5 flex items-center gap-1">
                        <TrendingUp className="w-3 h-3" /> 効果的な販売テクニック
                      </h4>
                      <div className="space-y-1">
                        {patterns.top_techniques.slice(0, 5).map((t, i) => (
                          <div key={i} className="text-xs text-gray-600 bg-gray-50 px-2 py-1 rounded flex justify-between">
                            <span>{t.technique}</span>
                            <span className="text-gray-400">{t.frequency}回使用</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Generated Script Output */}
            {generatedScript ? (
              <>
              <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                {/* Script Header */}
                <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <FileText className="w-4 h-4 text-orange-500" />
                    <span className="text-sm font-semibold text-gray-800">{window.__t('scriptGen_generatedScriptTitle', '生成された台本')}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-gray-500">
                      {generatedScript.char_count}文字 / 約{generatedScript.estimated_duration_minutes}分
                    </span>
                    <button
                      onClick={handleCopy}
                      className="flex items-center gap-1 px-2.5 py-1 bg-gray-100 hover:bg-gray-200 rounded-lg text-xs text-gray-700 transition-colors"
                    >
                      {copied ? (
                        <>
                          <Check className="w-3 h-3 text-green-600" />
                          <span className="text-green-600">{window.__t('scriptGen_copied', 'コピー済み')}</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3 h-3" />
                          コピー
                        </>
                      )}
                    </button>
                  </div>
                </div>

                {/* Script Content - Formatted */}
                <div className="p-4">
                  <div className="max-h-[600px] overflow-y-auto space-y-0">
                    {renderFormattedScript(generatedScript.script)}
                  </div>
                </div>

                {/* Data Insights Footer */}
                <div className="px-4 py-3 bg-gray-50 border-t border-gray-100">
                  <div className="flex flex-wrap gap-3 text-xs text-gray-500">
                    {generatedScript.patterns_used?.cross_video_patterns && (
                      <span className="flex items-center gap-1">
                        <TrendingUp className="w-3 h-3 text-green-500" />
                        {generatedScript.patterns_used.videos_in_cross_analysis}本の配信データ反映
                      </span>
                    )}
                    {generatedScript.patterns_used?.cta_patterns_found > 0 && (
                      <span className="flex items-center gap-1">
                        <MessageSquare className="w-3 h-3 text-blue-500" />
                        CTA {generatedScript.patterns_used.cta_patterns_found}パターン活用
                      </span>
                    )}
                    {generatedScript.patterns_used?.product_image_analyzed && (
                      <span className="flex items-center gap-1">
                        <ImageIcon className="w-3 h-3 text-purple-500" />
                        商品画像{generatedScript.patterns_used.images_analyzed_count || 1}枚分析済み
                      </span>
                    )}
                    {generatedScript.patterns_used?.feedback_knowledge_used && (
                      <span className="flex items-center gap-1">
                        <Zap className="w-3 h-3 text-yellow-500" />
                        フィードバックknowledge反映済み
                      </span>
                    )}
                    <span className="text-gray-400">model: {generatedScript.model}</span>
                  </div>
                </div>

                {/* Product Image Analysis Results */}
                {generatedScript.product_analysis && (
                  <div className="px-4 py-3 border-t border-gray-100">
                    <h4 className="text-xs font-semibold text-gray-500 mb-2">{window.__t('scriptGeneratorPage_dee910', 'AI商品画像分析')}</h4>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      {generatedScript.product_analysis.product_type && (
                        <div className="bg-purple-50 px-2 py-1.5 rounded">
                          <span className="text-purple-600 font-medium">{window.__t('scriptGeneratorPage_b9d1dd', '種類:')}</span>{" "}
                          {generatedScript.product_analysis.product_type}
                        </div>
                      )}
                      {generatedScript.product_analysis.selling_points?.map((sp, i) => (
                        <div key={i} className="bg-green-50 px-2 py-1.5 rounded text-green-700">
                          {sp}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* ── Rating Section ── */}
              {generatedScript.script_id && (
                <div className="bg-white rounded-xl border border-gray-200 overflow-hidden mt-4">
                  <div className="px-4 py-3 border-b border-gray-100">
                    <div className="flex items-center gap-2">
                      <Star className="w-4 h-4 text-yellow-500" />
                      <span className="text-sm font-semibold text-gray-800">{window.__t('scriptGeneratorPage_8d0dcf', 'この台本を評価')}</span>
                      {ratingSubmitted && (
                        <span className="ml-auto text-xs text-green-600 bg-green-50 px-2 py-0.5 rounded-full flex items-center gap-1">
                          <Check className="w-3 h-3" /> 評価送信済み
                        </span>
                      )}
                    </div>
                  </div>

                  {ratingSubmitted ? (
                    <div className="p-6 text-center">
                      <div className="flex justify-center gap-1 mb-2">
                        {[1,2,3,4,5].map(s => (
                          <Star key={s} className={`w-6 h-6 ${s <= ratingValue ? 'text-yellow-400 fill-yellow-400' : 'text-gray-300'}`} />
                        ))}
                      </div>
                      <p className="text-sm text-gray-600">{window.__t('scriptGeneratorPage_a2098d', 'ご評価ありがとうございます！AIの学習に活用されます。')}</p>
                    </div>
                  ) : (
                    <div className="p-4 space-y-4">
                      {/* Star Rating */}
                      <div className="flex flex-col items-center gap-2">
                        <div className="flex gap-1">
                          {[1,2,3,4,5].map(s => (
                            <button
                              key={s}
                              onClick={() => setRatingValue(s)}
                              onMouseEnter={() => setRatingHover(s)}
                              onMouseLeave={() => setRatingHover(0)}
                              className="p-0.5 transition-transform hover:scale-110"
                            >
                              <Star className={`w-7 h-7 transition-colors ${
                                s <= (ratingHover || ratingValue)
                                  ? 'text-yellow-400 fill-yellow-400'
                                  : 'text-gray-300 hover:text-yellow-200'
                              }`} />
                            </button>
                          ))}
                        </div>
                        <span className="text-xs text-gray-500">
                          {ratingValue === 0 ? [window.__t('scriptGeneratorPage_78cf37', '★をクリックして評価')] :
                           ratingValue === 1 ? [window.__t('scriptGeneratorPage_4946ec', '★ もう少し...')] :
                           ratingValue === 2 ? [window.__t('scriptGeneratorPage_9f48a0', '★★ いまいち')] :
                           ratingValue === 3 ? [window.__t('scriptGeneratorPage_323d25', '★★★ まあまあ')] :
                           ratingValue === 4 ? [window.__t('scriptGeneratorPage_dd2132', '★★★★ 良い！')] :
                           window.__t('scriptGeneratorPage_2f0df0', '★★★★★ 最高！')}
                        </span>
                      </div>

                      {/* Good Tags */}
                      {ratingValue >= 3 && (
                        <div>
                          <h5 className="text-xs font-medium text-gray-600 mb-1.5 flex items-center gap-1">
                            <ThumbsUp className="w-3 h-3 text-green-500" /> 良かった点
                          </h5>
                          <div className="flex flex-wrap gap-1.5">
                            {GOOD_TAG_OPTIONS.map(tag => (
                              <button
                                key={tag}
                                onClick={() => toggleTag(tag, ratingGoodTags, setRatingGoodTags)}
                                className={`px-2.5 py-1 rounded-full text-xs transition-colors ${
                                  ratingGoodTags.includes(tag)
                                    ? 'bg-green-100 text-green-700 border border-green-300'
                                    : 'bg-gray-100 text-gray-600 hover:bg-green-50 border border-transparent'
                                }`}
                              >
                                {tag}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Bad Tags */}
                      {ratingValue > 0 && ratingValue <= 3 && (
                        <div>
                          <h5 className="text-xs font-medium text-gray-600 mb-1.5 flex items-center gap-1">
                            <ThumbsDown className="w-3 h-3 text-red-500" /> 改善してほしい点
                          </h5>
                          <div className="flex flex-wrap gap-1.5">
                            {BAD_TAG_OPTIONS.map(tag => (
                              <button
                                key={tag}
                                onClick={() => toggleTag(tag, ratingBadTags, setRatingBadTags)}
                                className={`px-2.5 py-1 rounded-full text-xs transition-colors ${
                                  ratingBadTags.includes(tag)
                                    ? 'bg-red-100 text-red-700 border border-red-300'
                                    : 'bg-gray-100 text-gray-600 hover:bg-red-50 border border-transparent'
                                }`}
                              >
                                {tag}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Comment */}
                      {ratingValue > 0 && (
                        <div>
                          <textarea
                            value={ratingComment}
                            onChange={(e) => setRatingComment(e.target.value)}
                            placeholder={window.__t('commentPlaceholder', 'コメント（任意）')}
                            className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm resize-none focus:outline-none focus:ring-2 focus:ring-orange-300 focus:border-orange-300"
                            rows={2}
                          />
                        </div>
                      )}

                      {/* Submit */}
                      {ratingValue > 0 && (
                        <button
                          onClick={handleSubmitRating}
                          disabled={isSubmittingRating}
                          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-orange-500 to-red-500 text-white rounded-lg text-sm font-medium hover:from-orange-600 hover:to-red-600 transition-all disabled:opacity-50"
                        >
                          {isSubmittingRating ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Send className="w-4 h-4" />
                          )}
                          評価を送信
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
              </>
            ) : (
              /* Empty State */
              <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
                <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gradient-to-br from-orange-100 to-red-100 flex items-center justify-center">
                  <FileText className="w-8 h-8 text-orange-400" />
                </div>
                <h3 className="text-lg font-semibold text-gray-800 mb-2">
                  実績データに基づく台本生成
                </h3>
                <p className="text-sm text-gray-500 max-w-md mx-auto mb-4">
                  商品情報を入力するだけで、過去のライブコマース配信データから学習した
                  「売れるパターン」を反映した台本を自動生成します。
                </p>
                <div className="flex flex-wrap justify-center gap-2 text-xs">
                  <span className="px-2.5 py-1 bg-orange-50 text-orange-600 rounded-full">
                    売れたCTAを自動反映
                  </span>
                  <span className="px-2.5 py-1 bg-blue-50 text-blue-600 rounded-full">
                    最適な商品説明時間
                  </span>
                  <span className="px-2.5 py-1 bg-green-50 text-green-600 rounded-full">
                    勝ちパターン構成
                  </span>
                  <span className="px-2.5 py-1 bg-purple-50 text-purple-600 rounded-full">
                    商品画像AI分析
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
