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

  // Winning patterns preview
  const [patterns, setPatterns] = useState(null);
  const [patternsLoading, setPatternsLoading] = useState(false);
  const [showPatterns, setShowPatterns] = useState(false);

  // ── Load winning patterns on mount ──
  useEffect(() => {
    loadPatterns();
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
      setError("商品名を入力してください");
      return;
    }

    setIsGenerating(true);
    setError(null);
    setGeneratedScript(null);

    // Upload all images first
    let imageUrls = [];
    if (imageFiles.length > 0) {
      try {
        imageUrls = await uploadAllImages();
      } catch (e) {
        console.warn("Some image uploads failed, continuing:", e);
      }
    }

    // Build price string
    let priceStr = "";
    if (originalPrice.trim() && discountedPrice.trim()) {
      priceStr = `通常価格: ${originalPrice.trim()} → 配信特別価格: ${discountedPrice.trim()}`;
    } else if (discountedPrice.trim()) {
      priceStr = discountedPrice.trim();
    } else if (originalPrice.trim()) {
      priceStr = originalPrice.trim();
    }

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
      setGeneratedScript(result);
    } catch (e) {
      console.error("Script generation failed:", e);
      const msg = e?.response?.data?.detail || e.message || "台本の生成に失敗しました";
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
              <h1 className="text-lg font-bold text-gray-900">売れる台本</h1>
              <p className="text-xs text-gray-500">実績データに基づくライブコマース台本生成AI</p>
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
                placeholder="例: KYOGOKU ケラチンシャンプー"
                className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
              />
            </div>

            {/* Product Images (Multiple) */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">
                商品写真 <span className="text-gray-400 font-normal">(任意・複数可)</span>
              </label>
              <p className="text-xs text-gray-500">AIが商品の特徴を読み取り、台本に反映します（最大10枚）</p>

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
                    <span className="text-[10px] text-gray-400">追加</span>
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
              <label className="block text-sm font-semibold text-gray-800">商品詳細</label>
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
                      placeholder="例: ¥5,980"
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
                      placeholder="例: ¥3,980"
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
                  placeholder="商品の特徴・説明（任意）"
                  rows={3}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent resize-none"
                />
                <input
                  type="text"
                  value={targetAudience}
                  onChange={(e) => setTargetAudience(e.target.value)}
                  placeholder="ターゲット層（例: 30代女性、髪のダメージが気になる方）"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
                />
              </div>
            </div>

            {/* Benefits / Tokuten */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800 flex items-center gap-1.5">
                <Gift className="w-4 h-4 text-orange-500" />
                特典 <span className="text-gray-400 font-normal">(任意)</span>
              </label>
              <p className="text-xs text-gray-500">配信限定の特典・おまけ・キャンペーン情報</p>
              <textarea
                value={benefits}
                onChange={(e) => setBenefits(e.target.value)}
                placeholder="例: 配信限定20%OFF、2個セットで送料無料、先着100名にサンプルプレゼント"
                rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent resize-none"
              />
            </div>

            {/* Settings */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">台本設定</label>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs text-gray-500 mb-1 block">トーン</label>
                  <select
                    value={tone}
                    onChange={(e) => setTone(e.target.value)}
                    className="w-full px-2 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
                  >
                    <option value="professional_friendly">プロ＆親しみ</option>
                    <option value="energetic">エネルギッシュ</option>
                    <option value="calm">落ち着き</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-500 mb-1 block">言語</label>
                  <select
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                    className="w-full px-2 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
                  >
                    <option value="ja">日本語</option>
                    <option value="zh">中文</option>
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
                  <span>1分</span>
                  <span>30分</span>
                  <span>60分</span>
                </div>
              </div>
            </div>

            {/* Additional Instructions */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">
                追加指示 <span className="text-gray-400 font-normal">(任意)</span>
              </label>
              <textarea
                value={additionalInstructions}
                onChange={(e) => setAdditionalInstructions(e.target.value)}
                placeholder="例: 最初に自己紹介を入れてほしい、特定のキャンペーンに触れてほしい等"
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

            {error && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
                {error}
              </div>
            )}
          </div>

          {/* ── Right: Output & Patterns ── */}
          <div className="lg:col-span-3 space-y-4">

            {/* Winning Patterns Preview */}
            <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <button
                onClick={() => setShowPatterns(!showPatterns)}
                className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-center gap-2">
                  <Zap className="w-4 h-4 text-orange-500" />
                  <span className="text-sm font-semibold text-gray-800">学習済み勝ちパターン</span>
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
              <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                {/* Script Header */}
                <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <FileText className="w-4 h-4 text-orange-500" />
                    <span className="text-sm font-semibold text-gray-800">生成された台本</span>
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
                          <span className="text-green-600">コピー済み</span>
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
                    <h4 className="text-xs font-semibold text-gray-500 mb-2">AI商品画像分析</h4>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      {generatedScript.product_analysis.product_type && (
                        <div className="bg-purple-50 px-2 py-1.5 rounded">
                          <span className="text-purple-600 font-medium">種類:</span>{" "}
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
