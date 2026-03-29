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
} from "lucide-react";
import scriptGeneratorService from "../base/services/scriptGeneratorService";

/**
 * ScriptGeneratorPage - Standalone "売れる台本" tool
 *
 * Users input product info (name, image, description, price)
 * and the system generates a live commerce script grounded in
 * real performance data from AitherHub's analysis database.
 */
export default function ScriptGeneratorPage() {
  const navigate = useNavigate();

  // ── Form State ──
  const [productName, setProductName] = useState("");
  const [productDescription, setProductDescription] = useState("");
  const [productPrice, setProductPrice] = useState("");
  const [targetAudience, setTargetAudience] = useState("");
  const [additionalInstructions, setAdditionalInstructions] = useState("");
  const [tone, setTone] = useState("professional_friendly");
  const [language, setLanguage] = useState("ja");
  const [durationMinutes, setDurationMinutes] = useState(10);

  // Image upload
  const [imageFile, setImageFile] = useState(null);
  const [imagePreview, setImagePreview] = useState(null);
  const [imageUrl, setImageUrl] = useState(null);
  const [isUploadingImage, setIsUploadingImage] = useState(false);

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

  // ── Image Upload ──
  const handleImageSelect = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImageFile(file);
    setImagePreview(URL.createObjectURL(file));
    setImageUrl(null);
  };

  const handleImageUpload = async () => {
    if (!imageFile) return;
    setIsUploadingImage(true);
    try {
      const blobUrl = await scriptGeneratorService.uploadProductImage(imageFile);
      setImageUrl(blobUrl);
    } catch (e) {
      console.error("Image upload failed:", e);
      setError("画像のアップロードに失敗しました");
    } finally {
      setIsUploadingImage(false);
    }
  };

  const removeImage = () => {
    setImageFile(null);
    setImagePreview(null);
    setImageUrl(null);
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

    // Upload image first if selected but not yet uploaded
    let finalImageUrl = imageUrl;
    if (imageFile && !imageUrl) {
      try {
        finalImageUrl = await scriptGeneratorService.uploadProductImage(imageFile);
        setImageUrl(finalImageUrl);
      } catch (e) {
        console.warn("Image upload failed, continuing without image:", e);
      }
    }

    try {
      const result = await scriptGeneratorService.generateScript({
        product_name: productName.trim(),
        product_image_url: finalImageUrl || undefined,
        product_description: productDescription.trim() || undefined,
        product_price: productPrice.trim() || undefined,
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

            {/* Product Image */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">
                商品写真 <span className="text-gray-400 font-normal">(任意)</span>
              </label>
              <p className="text-xs text-gray-500">AIが商品の特徴を読み取り、台本に反映します</p>
              {imagePreview ? (
                <div className="relative">
                  <img
                    src={imagePreview}
                    alt="Product"
                    className="w-full h-40 object-contain rounded-lg bg-gray-50 border border-gray-200"
                  />
                  <button
                    onClick={removeImage}
                    className="absolute top-2 right-2 p-1 bg-white rounded-full shadow-md hover:bg-gray-100"
                  >
                    <X className="w-4 h-4 text-gray-600" />
                  </button>
                  {imageUrl && (
                    <div className="absolute bottom-2 left-2 bg-green-500 text-white text-xs px-2 py-0.5 rounded-full flex items-center gap-1">
                      <Check className="w-3 h-3" /> アップロード済み
                    </div>
                  )}
                </div>
              ) : (
                <label className="flex flex-col items-center justify-center w-full h-32 border-2 border-dashed border-gray-300 rounded-lg cursor-pointer hover:border-orange-400 hover:bg-orange-50 transition-colors">
                  <ImageIcon className="w-8 h-8 text-gray-400 mb-1" />
                  <span className="text-xs text-gray-500">クリックして画像を選択</span>
                  <input
                    type="file"
                    accept="image/*"
                    onChange={handleImageSelect}
                    className="hidden"
                  />
                </label>
              )}
            </div>

            {/* Product Details */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
              <label className="block text-sm font-semibold text-gray-800">商品詳細</label>
              <div className="space-y-2">
                <input
                  type="text"
                  value={productPrice}
                  onChange={(e) => setProductPrice(e.target.value)}
                  placeholder="価格（例: ¥3,980）"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent"
                />
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

                {/* Script Content */}
                <div className="p-4">
                  <div className="whitespace-pre-wrap text-sm text-gray-800 leading-relaxed font-[system-ui] max-h-[600px] overflow-y-auto">
                    {generatedScript.script}
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
                        商品画像分析済み
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
