import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Upload,
  Sparkles,
  Loader2,
  CheckCircle,
  AlertCircle,
  Download,
  RefreshCw,
  Image as ImageIcon,
  Video,
  Mic,
  FileText,
  Zap,
  Play,
  Copy,
  ExternalLink,
} from "lucide-react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL;
const ADMIN_KEY = "aither:hub";

/**
 * AI Video Generator Page — One-Click Product Video Generation
 *
 * The killer feature: Input product info → Get a lip-synced video
 * of a real liver introducing your product.
 *
 * Flow:
 *   1. Enter product info (name, description, image, price)
 *   2. Select a liver/avatar
 *   3. Click "Generate" → AI creates script → voice → video
 *   4. Preview and download the result
 */
export default function AiVideoGeneratorPage() {
  const navigate = useNavigate();

  // ── Product Info State ──
  const [productName, setProductName] = useState("");
  const [productDescription, setProductDescription] = useState("");
  const [productImageUrl, setProductImageUrl] = useState("");
  const [productPrice, setProductPrice] = useState("");
  const [originalPrice, setOriginalPrice] = useState("");
  const [discountedPrice, setDiscountedPrice] = useState("");
  const [benefits, setBenefits] = useState("");
  const [targetAudience, setTargetAudience] = useState("");

  // ── Avatar/Liver State ──
  const [avatars, setAvatars] = useState([]);
  const [selectedAvatarId, setSelectedAvatarId] = useState("");
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [loadingAvatars, setLoadingAvatars] = useState(true);

  // ── Generation Options ──
  const [tone, setTone] = useState("energetic");
  const [language, setLanguage] = useState("ja");
  const [durationSeconds, setDurationSeconds] = useState(60);
  const [customScript, setCustomScript] = useState("");
  const [useCustomScript, setUseCustomScript] = useState(false);

  // ── Job State ──
  const [isGenerating, setIsGenerating] = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [error, setError] = useState(null);

  // ── Job History ──
  const [jobHistory, setJobHistory] = useState([]);

  // Polling ref
  const pollRef = useRef(null);

  // ── Load Avatars ──
  useEffect(() => {
    loadAvatars();
    loadJobHistory();
  }, []);

  // ── Cleanup polling on unmount ──
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const loadAvatars = async () => {
    try {
      setLoadingAvatars(true);
      const res = await axios.get(`${API_BASE}/api/v1/ai-video-generator/avatars`, {
        headers: { "X-Admin-Key": ADMIN_KEY },
      });
      setAvatars(res.data.avatars || []);
      if (res.data.avatars?.length > 0) {
        setSelectedAvatarId(res.data.avatars[0].avatar_id);
      }
    } catch (err) {
      console.error("Failed to load avatars:", err);
    } finally {
      setLoadingAvatars(false);
    }
  };

  const loadJobHistory = async () => {
    try {
      const res = await axios.get(`${API_BASE}/api/v1/ai-video-generator/jobs?limit=10`, {
        headers: { "X-Admin-Key": ADMIN_KEY },
      });
      setJobHistory(res.data.jobs || []);
    } catch (err) {
      console.error("Failed to load job history:", err);
    }
  };

  // ── Generate Video ──
  const handleGenerate = async () => {
    if (!productName.trim()) {
      setError("商品名を入力してください");
      return;
    }
    if (!selectedAvatarId) {
      setError("ライバーを選択してください");
      return;
    }

    setIsGenerating(true);
    setError(null);
    setJobStatus(null);

    try {
      const payload = {
        product_name: productName.trim(),
        product_description: productDescription.trim() || undefined,
        product_image_url: productImageUrl.trim() || undefined,
        product_price: productPrice.trim() || undefined,
        original_price: originalPrice.trim() || undefined,
        discounted_price: discountedPrice.trim() || undefined,
        benefits: benefits.trim() || undefined,
        target_audience: targetAudience.trim() || undefined,
        avatar_id: selectedAvatarId,
        voice_id: selectedVoiceId || undefined,
        tone,
        language,
        duration_seconds: durationSeconds,
        custom_script: useCustomScript ? customScript.trim() : undefined,
      };

      const res = await axios.post(
        `${API_BASE}/api/v1/ai-video-generator/generate`,
        payload,
        { headers: { "X-Admin-Key": ADMIN_KEY } }
      );

      if (res.data.success) {
        setCurrentJobId(res.data.job_id);
        setJobStatus({ status: "queued", progress: 0 });
        startPolling(res.data.job_id);
      } else {
        setError(res.data.error || "生成に失敗しました");
        setIsGenerating(false);
      }
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "生成に失敗しました");
      setIsGenerating(false);
    }
  };

  // ── Poll for Status ──
  const startPolling = (jobId) => {
    if (pollRef.current) clearInterval(pollRef.current);

    pollRef.current = setInterval(async () => {
      try {
        const res = await axios.get(
          `${API_BASE}/api/v1/ai-video-generator/status/${jobId}`,
          { headers: { "X-Admin-Key": ADMIN_KEY } }
        );
        setJobStatus(res.data);

        if (res.data.status === "completed" || res.data.status === "failed") {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setIsGenerating(false);
          loadJobHistory();
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    }, 3000);
  };

  // ── Status Display Helpers ──
  const getStatusLabel = (status) => {
    const labels = {
      queued: "キューに追加中...",
      generating_script: "🧠 AI台本を生成中...",
      generating_audio: "🎙️ 音声を生成中...",
      generating_video: "🎬 動画を生成中...",
      completed: "✅ 完成！",
      failed: "❌ エラー",
    };
    return labels[status] || status;
  };

  const getProgressColor = (status) => {
    if (status === "completed") return "bg-green-500";
    if (status === "failed") return "bg-red-500";
    return "bg-purple-500";
  };

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate("/ai-live-creator")}
              className="p-2 hover:bg-gray-800 rounded-lg transition"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <h1 className="text-2xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
                AI Video Generator
              </h1>
              <p className="text-sm text-gray-400">
                商品情報を入力 → ライバーが紹介する動画を自動生成
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="px-3 py-1 bg-purple-500/20 text-purple-300 rounded-full text-xs font-medium">
              Beta
            </span>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-6 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">

          {/* ═══ Left Column: Product Input ═══ */}
          <div className="lg:col-span-2 space-y-6">

            {/* Product Info Card */}
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <FileText className="w-5 h-5 text-purple-400" />
                商品情報
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Product Name */}
                <div className="md:col-span-2">
                  <label className="block text-sm text-gray-400 mb-1">商品名 *</label>
                  <input
                    type="text"
                    value={productName}
                    onChange={(e) => setProductName(e.target.value)}
                    placeholder="例: KYOGOKU カラーシャンプー ブルーパープル"
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-none"
                  />
                </div>

                {/* Description */}
                <div className="md:col-span-2">
                  <label className="block text-sm text-gray-400 mb-1">商品説明・特徴</label>
                  <textarea
                    value={productDescription}
                    onChange={(e) => setProductDescription(e.target.value)}
                    placeholder="商品の特徴、成分、効果などを入力..."
                    rows={3}
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-none resize-none"
                  />
                </div>

                {/* Image URL */}
                <div className="md:col-span-2">
                  <label className="block text-sm text-gray-400 mb-1">商品画像URL</label>
                  <input
                    type="text"
                    value={productImageUrl}
                    onChange={(e) => setProductImageUrl(e.target.value)}
                    placeholder="https://..."
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-none"
                  />
                  {productImageUrl && (
                    <div className="mt-2">
                      <img
                        src={productImageUrl}
                        alt="Product preview"
                        className="w-24 h-24 object-cover rounded-lg border border-gray-700"
                        onError={(e) => { e.target.style.display = 'none'; }}
                      />
                    </div>
                  )}
                </div>

                {/* Prices */}
                <div>
                  <label className="block text-sm text-gray-400 mb-1">定価</label>
                  <input
                    type="text"
                    value={originalPrice}
                    onChange={(e) => setOriginalPrice(e.target.value)}
                    placeholder="¥5,980"
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-none"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-400 mb-1">割引価格</label>
                  <input
                    type="text"
                    value={discountedPrice}
                    onChange={(e) => setDiscountedPrice(e.target.value)}
                    placeholder="¥3,980"
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-none"
                  />
                </div>

                {/* Benefits */}
                <div className="md:col-span-2">
                  <label className="block text-sm text-gray-400 mb-1">特典・限定オファー</label>
                  <input
                    type="text"
                    value={benefits}
                    onChange={(e) => setBenefits(e.target.value)}
                    placeholder="例: 今だけ送料無料、2本セットで20%OFF"
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-none"
                  />
                </div>

                {/* Target Audience */}
                <div className="md:col-span-2">
                  <label className="block text-sm text-gray-400 mb-1">ターゲット層</label>
                  <input
                    type="text"
                    value={targetAudience}
                    onChange={(e) => setTargetAudience(e.target.value)}
                    placeholder="例: 30代女性、カラーリングの色落ちに悩む方"
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-none"
                  />
                </div>
              </div>
            </div>

            {/* Generation Options Card */}
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-yellow-400" />
                生成オプション
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* Tone */}
                <div>
                  <label className="block text-sm text-gray-400 mb-1">トーン</label>
                  <select
                    value={tone}
                    onChange={(e) => setTone(e.target.value)}
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white focus:border-purple-500 outline-none"
                  >
                    <option value="energetic">🔥 元気・テンション高め</option>
                    <option value="professional_friendly">💼 プロフェッショナル</option>
                    <option value="calm">🌿 落ち着き・高級感</option>
                    <option value="sexy">✨ 魅力的・自信</option>
                  </select>
                </div>

                {/* Language */}
                <div>
                  <label className="block text-sm text-gray-400 mb-1">言語</label>
                  <select
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white focus:border-purple-500 outline-none"
                  >
                    <option value="ja">🇯🇵 日本語</option>
                    <option value="en">🇺🇸 English</option>
                    <option value="zh">🇨🇳 中文</option>
                    <option value="ko">🇰🇷 한국어</option>
                  </select>
                </div>

                {/* Duration */}
                <div>
                  <label className="block text-sm text-gray-400 mb-1">動画の長さ</label>
                  <select
                    value={durationSeconds}
                    onChange={(e) => setDurationSeconds(Number(e.target.value))}
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white focus:border-purple-500 outline-none"
                  >
                    <option value={30}>30秒（ショート）</option>
                    <option value={60}>60秒（スタンダード）</option>
                    <option value={90}>90秒（ロング）</option>
                    <option value={120}>120秒（フル）</option>
                  </select>
                </div>
              </div>

              {/* Custom Script Toggle */}
              <div className="mt-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useCustomScript}
                    onChange={(e) => setUseCustomScript(e.target.checked)}
                    className="w-4 h-4 rounded border-gray-600 text-purple-500 focus:ring-purple-500"
                  />
                  <span className="text-sm text-gray-300">自分で台本を書く（AI生成をスキップ）</span>
                </label>
                {useCustomScript && (
                  <textarea
                    value={customScript}
                    onChange={(e) => setCustomScript(e.target.value)}
                    placeholder="ライバーに話してほしい台本を入力..."
                    rows={5}
                    className="mt-2 w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-purple-500 focus:ring-1 focus:ring-purple-500 outline-none resize-none"
                  />
                )}
              </div>
            </div>

            {/* Generate Button */}
            <button
              onClick={handleGenerate}
              disabled={isGenerating || !productName.trim() || !selectedAvatarId}
              className={`w-full py-4 rounded-xl font-bold text-lg flex items-center justify-center gap-3 transition-all ${
                isGenerating || !productName.trim() || !selectedAvatarId
                  ? "bg-gray-700 text-gray-400 cursor-not-allowed"
                  : "bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white shadow-lg shadow-purple-500/25 hover:shadow-purple-500/40"
              }`}
            >
              {isGenerating ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  生成中...
                </>
              ) : (
                <>
                  <Zap className="w-5 h-5" />
                  動画を生成する
                </>
              )}
            </button>

            {/* Error Display */}
            {error && (
              <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-red-400 mt-0.5 flex-shrink-0" />
                <p className="text-red-300 text-sm">{error}</p>
              </div>
            )}

            {/* Job Status */}
            {jobStatus && (
              <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
                <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                  <Video className="w-5 h-5 text-blue-400" />
                  生成ステータス
                </h3>

                {/* Progress Bar */}
                <div className="mb-4">
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-gray-300">{getStatusLabel(jobStatus.status)}</span>
                    <span className="text-gray-400">{jobStatus.progress}%</span>
                  </div>
                  <div className="w-full h-3 bg-gray-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${getProgressColor(jobStatus.status)}`}
                      style={{ width: `${jobStatus.progress}%` }}
                    />
                  </div>
                </div>

                {/* Script Preview */}
                {jobStatus.script && (
                  <div className="mb-4">
                    <h4 className="text-sm font-medium text-gray-400 mb-2">生成された台本:</h4>
                    <div className="bg-gray-800 rounded-lg p-3 max-h-40 overflow-y-auto">
                      <p className="text-sm text-gray-200 whitespace-pre-wrap">{jobStatus.script}</p>
                    </div>
                  </div>
                )}

                {/* Completed: Video Preview */}
                {jobStatus.status === "completed" && jobStatus.video_url && (
                  <div className="mt-4">
                    <h4 className="text-sm font-medium text-green-400 mb-2 flex items-center gap-1">
                      <CheckCircle className="w-4 h-4" />
                      動画が完成しました！
                    </h4>
                    <div className="relative rounded-lg overflow-hidden bg-black max-w-sm mx-auto">
                      <video
                        src={jobStatus.video_url}
                        controls
                        className="w-full aspect-[9/16]"
                        poster=""
                      />
                    </div>
                    <div className="flex gap-3 mt-3 justify-center">
                      <a
                        href={jobStatus.video_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="px-4 py-2 bg-purple-600 hover:bg-purple-500 rounded-lg text-sm font-medium flex items-center gap-2 transition"
                      >
                        <Download className="w-4 h-4" />
                        ダウンロード
                      </a>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(jobStatus.video_url);
                        }}
                        className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium flex items-center gap-2 transition"
                      >
                        <Copy className="w-4 h-4" />
                        URLコピー
                      </button>
                    </div>
                  </div>
                )}

                {/* Failed */}
                {jobStatus.status === "failed" && (
                  <div className="mt-4 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                    <p className="text-red-300 text-sm">
                      エラー: {jobStatus.error || "不明なエラー"}
                    </p>
                    <p className="text-red-400/60 text-xs mt-1">
                      ステップ: {jobStatus.error_step}
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ═══ Right Column: Avatar Selection & History ═══ */}
          <div className="space-y-6">

            {/* Avatar Selection */}
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <Mic className="w-5 h-5 text-emerald-400" />
                ライバー選択
              </h2>

              {loadingAvatars ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin text-emerald-400" />
                  <span className="ml-2 text-gray-400 text-sm">ライバーを読み込み中...</span>
                </div>
              ) : avatars.length === 0 ? (
                <p className="text-gray-500 text-sm text-center py-4">
                  利用可能なライバーがありません
                </p>
              ) : (
                <>
                  {/* AitherHub Livers (Priority - real faces from live streams) */}
                  {avatars.filter(a => a.source === 'aitherhub').length > 0 && (
                    <div className="mb-4">
                      <p className="text-xs text-emerald-400 font-medium mb-2 flex items-center gap-1">
                        <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block animate-pulse"></span>
                        AitherHub ライバー（ライブ配信）
                      </p>
                      <div className="grid grid-cols-3 gap-2.5 max-h-52 overflow-y-auto pr-1">
                        {avatars.filter(a => a.source === 'aitherhub').map((avatar) => (
                          <button
                            key={avatar.avatar_id}
                            onClick={() => setSelectedAvatarId(avatar.avatar_id)}
                            className={`relative rounded-lg overflow-hidden border-2 transition-all ${
                              selectedAvatarId === avatar.avatar_id
                                ? "border-emerald-500 ring-2 ring-emerald-500/30 scale-[1.02]"
                                : "border-gray-700 hover:border-gray-600 hover:scale-[1.01]"
                            }`}
                          >
                            {avatar.preview_image_url ? (
                              avatar.preview_image_url.includes('.mp4') || avatar.preview_image_url.includes('/clips/') ? (
                                <video
                                  src={avatar.preview_image_url}
                                  className="w-full aspect-[9/16] object-cover"
                                  preload="metadata"
                                  muted
                                  onMouseEnter={(e) => { try { e.target.play(); } catch {} }}
                                  onMouseLeave={(e) => { try { e.target.pause(); e.target.currentTime = 0; } catch {} }}
                                />
                              ) : (
                                <img
                                  src={avatar.preview_image_url}
                                  alt={avatar.name}
                                  className="w-full aspect-[9/16] object-cover"
                                />
                              )
                            ) : (
                              <div className="w-full aspect-[9/16] bg-gray-800 flex items-center justify-center">
                                <Mic className="w-6 h-6 text-gray-600" />
                              </div>
                            )}
                            <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-1.5">
                              <p className="text-[10px] font-medium truncate">{avatar.name}</p>
                            </div>
                            {selectedAvatarId === avatar.avatar_id && (
                              <div className="absolute top-1.5 right-1.5 w-4 h-4 bg-emerald-500 rounded-full flex items-center justify-center">
                                <CheckCircle className="w-2.5 h-2.5" />
                              </div>
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* HeyGen Digital Twins (supplement) */}
                  {avatars.filter(a => a.source !== 'aitherhub').length > 0 && (
                    <div>
                      <p className="text-xs text-gray-500 font-medium mb-2 flex items-center gap-1">
                        <span className="w-2 h-2 rounded-full bg-gray-500 inline-block"></span>
                        Digital Twin
                      </p>
                      <div className="grid grid-cols-3 gap-2.5 max-h-40 overflow-y-auto pr-1">
                        {avatars.filter(a => a.source !== 'aitherhub').map((avatar) => (
                          <button
                            key={avatar.avatar_id}
                            onClick={() => setSelectedAvatarId(avatar.avatar_id)}
                            className={`relative rounded-lg overflow-hidden border-2 transition-all ${
                              selectedAvatarId === avatar.avatar_id
                                ? "border-emerald-500 ring-2 ring-emerald-500/30 scale-[1.02]"
                                : "border-gray-700 hover:border-gray-600 hover:scale-[1.01]"
                            }`}
                          >
                            {avatar.preview_image_url ? (
                              <img
                                src={avatar.preview_image_url}
                                alt={avatar.name}
                                className="w-full aspect-square object-cover"
                              />
                            ) : (
                              <div className="w-full aspect-square bg-gray-800 flex items-center justify-center">
                                <Mic className="w-6 h-6 text-gray-600" />
                              </div>
                            )}
                            <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-1.5">
                              <p className="text-[10px] font-medium truncate">{avatar.name}</p>
                            </div>
                            {selectedAvatarId === avatar.avatar_id && (
                              <div className="absolute top-1.5 right-1.5 w-4 h-4 bg-emerald-500 rounded-full flex items-center justify-center">
                                <CheckCircle className="w-2.5 h-2.5" />
                              </div>
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Job History */}
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <RefreshCw className="w-5 h-5 text-blue-400" />
                生成履歴
              </h2>

              {jobHistory.length === 0 ? (
                <p className="text-gray-500 text-sm text-center py-4">
                  まだ生成履歴がありません
                </p>
              ) : (
                <div className="space-y-3 max-h-60 overflow-y-auto pr-1">
                  {jobHistory.map((job) => (
                    <div
                      key={job.job_id}
                      className="bg-gray-800 rounded-lg p-3 border border-gray-700"
                    >
                      <div className="flex items-center justify-between">
                        <p className="text-sm font-medium truncate flex-1">
                          {job.product_name}
                        </p>
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full ${
                            job.status === "completed"
                              ? "bg-green-500/20 text-green-300"
                              : job.status === "failed"
                              ? "bg-red-500/20 text-red-300"
                              : "bg-yellow-500/20 text-yellow-300"
                          }`}
                        >
                          {job.status === "completed" ? "完成" : job.status === "failed" ? "失敗" : "処理中"}
                        </span>
                      </div>
                      {job.video_url && (
                        <a
                          href={job.video_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-purple-400 hover:text-purple-300 mt-1 flex items-center gap-1"
                        >
                          <ExternalLink className="w-3 h-3" />
                          動画を見る
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Info Card */}
            <div className="bg-gradient-to-br from-purple-900/30 to-pink-900/30 rounded-xl border border-purple-500/20 p-6">
              <h3 className="text-sm font-semibold text-purple-300 mb-2">仕組み</h3>
              <div className="space-y-2 text-xs text-gray-400">
                <div className="flex items-start gap-2">
                  <span className="text-purple-400 font-bold">1.</span>
                  <span>商品情報からAIが「売れる台本」を自動生成</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-purple-400 font-bold">2.</span>
                  <span>選択したライバーの声で台本を読み上げ</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-purple-400 font-bold">3.</span>
                  <span>ライバーの顔でリップシンク動画を生成</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-purple-400 font-bold">4.</span>
                  <span>完成動画をダウンロード・SNSに投稿</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
