import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  Upload,
  Play,
  Download,
  Trash2,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  Clock,
  Video,
  Mic,
  Settings,
  ArrowLeft,
  Loader2,
  FileText,
  Sparkles,
  Wand2,
  Volume2,
  Camera,
  ImageIcon,
  X,
} from "lucide-react";
import autoVideoService from "../base/services/autoVideoService";

/**
 * AutoVideoPage - Fully automated video generation pipeline UI
 *
 * Pipeline: Script (GPT) → Face Swap (FaceFusion) → Lip Sync + TTS (Sync.so + ElevenLabs) → Video
 *
 * User inputs:
 *  1. Body double video (upload or URL)
 *  2. Topic / product name (or pre-written script)
 *  3. Voice selection (ElevenLabs cloned voices)
 *  4. Quality preset (fast / balanced / high / ultra)
 *  5. Lip sync toggle
 */
export default function AutoVideoPage() {
  const navigate = useNavigate();

  // ── Form State ──
  const [videoUrl, setVideoUrl] = useState("");
  const [videoFile, setVideoFile] = useState(null);
  const [videoPreview, setVideoPreview] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isUploading, setIsUploading] = useState(false);

  // Script mode: "ai" (auto generate) or "manual" (user input)
  const [scriptMode, setScriptMode] = useState("ai");
  const [topic, setTopic] = useState("");
  const [productInfo, setProductInfo] = useState("");
  const [scriptText, setScriptText] = useState("");
  const [language, setLanguage] = useState("ja");
  const [tone, setTone] = useState("professional_friendly");

  // Voice & quality
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [voices, setVoices] = useState([]);
  const [voicesLoading, setVoicesLoading] = useState(false);
  const [quality, setQuality] = useState("pro");
  const [enableLipSync, setEnableLipSync] = useState(true);

  // Job state
  const [currentJobId, setCurrentJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Job history
  const [jobs, setJobs] = useState([]);
  const [jobsLoading, setJobsLoading] = useState(false);

  // Health
  const [health, setHealth] = useState(null);

  // Source face image (the face to swap onto the body double)
  const [sourceFaceFile, setSourceFaceFile] = useState(null);
  const [sourceFacePreview, setSourceFacePreview] = useState(null);
  const [sourceFaceUploadProgress, setSourceFaceUploadProgress] = useState(0);
  const [isUploadingFace, setIsUploadingFace] = useState(false);

  // Product images
  const [productImages, setProductImages] = useState([]);
  const [productImagePreviews, setProductImagePreviews] = useState([]);
  const [imageUploadProgress, setImageUploadProgress] = useState(0);
  const [isUploadingImages, setIsUploadingImages] = useState(false);

  // Settings panel
  const [showAdvanced, setShowAdvanced] = useState(false);

  const fileInputRef = useRef(null);
  const faceInputRef = useRef(null);
  const imageInputRef = useRef(null);
  const pollRef = useRef(null);

  // ── Load voices on mount ──
  useEffect(() => {
    loadVoices();
    loadJobs();
    checkHealth();
  }, []);

  // ── Poll job status ──
  useEffect(() => {
    if (!currentJobId) return;

    const poll = async () => {
      try {
        const status = await autoVideoService.getJobStatus(currentJobId);
        setJobStatus(status);

        if (status.status === "completed" || status.status === "failed" || status.status === "error") {
          clearInterval(pollRef.current);
          pollRef.current = null;
          loadJobs();
        }
      } catch (err) {
        console.error("Poll error:", err);
      }
    };

    poll();
    pollRef.current = setInterval(poll, 3000);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [currentJobId]);

  // ── Loaders ──
  const loadVoices = async () => {
    setVoicesLoading(true);
    try {
      const data = await autoVideoService.listVoices();
      const voiceList = data.voices || data || [];
      setVoices(voiceList);
      if (voiceList.length > 0 && !selectedVoiceId) {
        setSelectedVoiceId(voiceList[0].voice_id || voiceList[0].id || "");
      }
    } catch (err) {
      console.error("Failed to load voices:", err);
    } finally {
      setVoicesLoading(false);
    }
  };

  const loadJobs = async () => {
    setJobsLoading(true);
    try {
      const data = await autoVideoService.listJobs(20);
      setJobs(Array.isArray(data) ? data : data.jobs || []);
    } catch (err) {
      console.error("Failed to load jobs:", err);
    } finally {
      setJobsLoading(false);
    }
  };

  const checkHealth = async () => {
    try {
      const data = await autoVideoService.healthCheck();
      setHealth(data);
    } catch (err) {
      console.error("Health check failed:", err);
    }
  };

  // ── File handling ──
  const handleFileSelect = useCallback((e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.type.startsWith("video/")) {
      setError(window.__t('autoVideoPage_79a5a3', '動画ファイルを選択してください'));
      return;
    }

    if (file.size > 500 * 1024 * 1024) {
      setError(window.__t('autoVideoPage_9c0081', 'ファイルサイズは500MB以下にしてください'));
      return;
    }

    setVideoFile(file);
    setVideoPreview(URL.createObjectURL(file));
    setError(null);
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith("video/")) {
      setVideoFile(file);
      setVideoPreview(URL.createObjectURL(file));
      setError(null);
    }
  }, []);

  // ── Source face image handling ──
  const handleFaceSelect = useCallback((e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.type.startsWith("image/")) {
      setError(window.__t('autoVideoPage_80b7f5', '画像ファイルを選択してください'));
      return;
    }

    if (file.size > 20 * 1024 * 1024) {
      setError(window.__t('autoVideoPage_22ce55', '顔画像は20MB以下にしてください'));
      return;
    }

    setSourceFaceFile(file);
    setSourceFacePreview(URL.createObjectURL(file));
    setError(null);
  }, []);

  // ── Product image handling ──
  const handleImageSelect = useCallback((e) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    const validFiles = files.filter((f) => f.type.startsWith("image/"));
    if (validFiles.length === 0) {
      setError(window.__t('autoVideoPage_80b7f5', '画像ファイルを選択してください'));
      return;
    }

    // Max 5 images
    const newFiles = [...productImages, ...validFiles].slice(0, 5);
    setProductImages(newFiles);
    setProductImagePreviews(
      newFiles.map((f) => URL.createObjectURL(f))
    );
    setError(null);
  }, [productImages]);

  const removeProductImage = useCallback((index) => {
    setProductImages((prev) => prev.filter((_, i) => i !== index));
    setProductImagePreviews((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // ── Submit job ──
  const handleSubmit = async () => {
    setError(null);
    setIsSubmitting(true);

    try {
      // Upload video file if needed
      let finalVideoUrl = videoUrl;
      if (videoFile && !videoUrl) {
        setIsUploading(true);
        try {
          finalVideoUrl = await autoVideoService.uploadVideo(
            videoFile,
            setUploadProgress
          );
        } finally {
          setIsUploading(false);
          setUploadProgress(0);
        }
      }

      if (!finalVideoUrl) {
        setError(window.__t('autoVideoPage_ab8bdf', '動画ファイルをアップロードするか、URLを入力してください'));
        setIsSubmitting(false);
        return;
      }

      if (scriptMode === "ai" && !topic.trim()) {
        setError(window.__t('autoVideoPage_21eaf7', '商品名またはテーマを入力してください'));
        setIsSubmitting(false);
        return;
      }

      if (scriptMode === "manual" && !scriptText.trim()) {
        setError(window.__t('autoVideoPage_7b6666', '台本を入力してください'));
        setIsSubmitting(false);
        return;
      }

      if (!sourceFaceFile) {
        setError(window.__t('autoVideoPage_33192a', 'ソース顔画像をアップロードしてください（動画に合成する顔）'));
        setIsSubmitting(false);
        return;
      }

      // Upload source face image
      let sourceFaceUrl = null;
      setIsUploadingFace(true);
      try {
        sourceFaceUrl = await autoVideoService.uploadSourceFace(
          sourceFaceFile,
          setSourceFaceUploadProgress
        );
      } finally {
        setIsUploadingFace(false);
        setSourceFaceUploadProgress(0);
      }

      // Upload product images if any
      let productImageUrls = [];
      if (scriptMode === "ai" && productImages.length > 0) {
        setIsUploadingImages(true);
        try {
          productImageUrls = await autoVideoService.uploadProductImages(
            productImages,
            setImageUploadProgress
          );
        } finally {
          setIsUploadingImages(false);
          setImageUploadProgress(0);
        }
      }

      const params = {
        video_url: finalVideoUrl,
        topic: scriptMode === "ai" ? topic : `Custom: ${scriptText.slice(0, 50)}`,
        voice_id: selectedVoiceId || undefined,
        language,
        tone,
        quality,
        enable_lip_sync: enableLipSync,
        product_info: productInfo || undefined,
        product_image_urls: productImageUrls.length > 0 ? productImageUrls : undefined,
        source_face_url: sourceFaceUrl,
      };

      if (scriptMode === "manual") {
        params.script_text = scriptText;
      }

      const result = await autoVideoService.createJob(params);
      setCurrentJobId(result.job_id);
      setJobStatus({ status: "pending", step: "queued", progress: 0 });
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || window.__t('statusError', 'エラーが発生しました');
      setError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  // ── Delete job ──
  const handleDeleteJob = async (jobId) => {
    try {
      await autoVideoService.deleteJob(jobId);
      if (currentJobId === jobId) {
        setCurrentJobId(null);
        setJobStatus(null);
      }
      loadJobs();
    } catch (err) {
      console.error("Delete failed:", err);
    }
  };

  // ── Reset form ──
  const resetForm = () => {
    setCurrentJobId(null);
    setJobStatus(null);
    setVideoFile(null);
    setVideoPreview(null);
    setVideoUrl("");
    setTopic("");
    setProductInfo("");
    setScriptText("");
    setProductImages([]);
    setProductImagePreviews([]);
    setSourceFaceFile(null);
    setSourceFacePreview(null);
    setError(null);
  };

  // ── Status labels (matches backend AutoVideoStatus + step values) ──
  const statusLabels = {
    pending: window.__t('autoVideoPage_0114a5', '準備中...'),
    generating_script: window.__t('autoVideoPage_56b637', '台本を生成中 (GPT)...'),
    face_swapping: window.__t('autoVideoPage_88991c', '顔を合成中 (FaceFusion GPU)...'),
    lip_syncing: window.__t('autoVideoPage_fad2b2', 'リップシンク + 音声合成中 (Sync.so)...'),
    finalizing: window.__t('autoVideoPage_55320f', '最終処理中...'),
    completed: window.__t('clip_completed', '完了'),
    error: window.__t('sidebar_error', 'エラー'),
  };

  // ── Progress bar color ──
  const getProgressColor = (status) => {
    if (status === "completed") return "bg-green-500";
    if (status === "failed" || status === "error") return "bg-red-500";
    return "bg-blue-500";
  };

  // ── Render ──
  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* Header */}
      <div className="border-b border-gray-800 bg-gray-900/50 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate("/")}
              className="p-2 hover:bg-gray-800 rounded-lg transition"
            >
              <ArrowLeft size={20} />
            </button>
            <div className="flex items-center gap-2">
              <Sparkles size={24} className="text-purple-400" />
              <h1 className="text-xl font-bold">Auto Video</h1>
            </div>
            <span className="text-xs bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded-full">
              AI Pipeline
            </span>
          </div>
          <div className="flex items-center gap-2">
            {health && (
              <span
                className={`text-xs px-2 py-1 rounded-full ${
                  health.face_swap_worker === "ok" || health.face_swap_worker === "healthy"
                    ? "bg-green-500/20 text-green-300"
                    : "bg-red-500/20 text-red-300"
                }`}
              >
                GPU: {health.face_swap_worker === "ok" || health.face_swap_worker === "healthy" ? "Online" : "Offline"}
              </span>
            )}
            <button
              onClick={checkHealth}
              className="p-2 hover:bg-gray-800 rounded-lg transition"
              title={window.__t('autoVideoPage_586a11', 'ヘルスチェック')}
            >
              <RefreshCw size={16} />
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* ── Left: Form ── */}
          <div className="lg:col-span-2 space-y-6">
            {/* Pipeline info */}
            <div className="bg-gradient-to-r from-purple-900/30 to-blue-900/30 border border-purple-500/20 rounded-xl p-4">
              <div className="flex items-center gap-6 text-sm text-gray-300">
                <div className="flex items-center gap-1.5">
                  <FileText size={14} className="text-purple-400" />
                  <span>{window.__t('autoVideoPage_996fe3', '台本生成')}</span>
                </div>
                <span className="text-gray-600">→</span>
                <div className="flex items-center gap-1.5">
                  <Video size={14} className="text-green-400" />
                  <span>{window.__t('autoVideoPage_ddd411', '顔合成')}</span>
                </div>
                <span className="text-gray-600">→</span>
                <div className="flex items-center gap-1.5">
                  <Mic size={14} className="text-orange-400" />
                  <span>{window.__t('autoVideoPage_69b289', 'リップシンク + 音声')}</span>
                </div>
              </div>
            </div>

            {/* Video Upload */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
              <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
                <Video size={20} className="text-blue-400" />
                動画ファイル
              </h2>

              {!videoPreview ? (
                <div
                  className="border-2 border-dashed border-gray-700 rounded-xl p-8 text-center cursor-pointer hover:border-purple-500/50 transition"
                  onClick={() => fileInputRef.current?.click()}
                  onDrop={handleDrop}
                  onDragOver={(e) => e.preventDefault()}
                >
                  <Upload size={40} className="mx-auto mb-3 text-gray-500" />
                  <p className="text-gray-400 mb-1">
                    Body Doubleの動画をドラッグ＆ドロップ
                  </p>
                  <p className="text-gray-600 text-sm">
                    またはクリックして選択（MP4, MOV, 500MBまで）
                  </p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="video/*"
                    className="hidden"
                    onChange={handleFileSelect}
                  />
                </div>
              ) : (
                <div className="relative">
                  <video
                    src={videoPreview}
                    controls
                    className="w-full rounded-lg max-h-64 object-contain bg-black"
                  />
                  <button
                    onClick={() => {
                      setVideoFile(null);
                      setVideoPreview(null);
                      setVideoUrl("");
                    }}
                    className="absolute top-2 right-2 p-1.5 bg-red-500/80 hover:bg-red-500 rounded-lg transition"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              )}

              {isUploading && (
                <div className="mt-3">
                  <div className="flex justify-between text-sm text-gray-400 mb-1">
                    <span>アップロード中...</span>
                    <span>{uploadProgress}%</span>
                  </div>
                  <div className="w-full bg-gray-800 rounded-full h-2">
                    <div
                      className="bg-blue-500 h-2 rounded-full transition-all"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                </div>
              )}

              {/* URL input alternative */}
              <div className="mt-3">
                <input
                  type="text"
                  placeholder={window.__t('autoVideoPage_71df75', 'または動画URLを直接入力...')}
                  value={videoUrl}
                  onChange={(e) => setVideoUrl(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                />
              </div>
            </div>

            {/* Source Face Image */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
              <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
                <Camera size={20} className="text-pink-400" />
                ソース顔画像 <span className="text-red-400 text-sm">*</span>
              </h2>
              <p className="text-xs text-gray-500 mb-3">
                動画に合成する顔の画像をアップロードしてください。正面を向いた高解像度の写真が最適です。
              </p>

              {!sourceFacePreview ? (
                <div
                  className="border-2 border-dashed border-gray-700 rounded-xl p-6 text-center cursor-pointer hover:border-pink-500/50 transition"
                  onClick={() => faceInputRef.current?.click()}
                >
                  <Camera size={32} className="mx-auto mb-2 text-gray-500" />
                  <p className="text-gray-400 text-sm mb-1">
                    クリックして顔画像を選択
                  </p>
                  <p className="text-gray-600 text-xs">
                    JPG, PNG, WEBP（20MBまで）
                  </p>
                  <input
                    ref={faceInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={handleFaceSelect}
                  />
                </div>
              ) : (
                <div className="relative inline-block">
                  <img
                    src={sourceFacePreview}
                    alt="ソース顔"
                    className="w-32 h-32 object-cover rounded-xl border border-gray-700"
                  />
                  <button
                    onClick={() => {
                      setSourceFaceFile(null);
                      setSourceFacePreview(null);
                    }}
                    className="absolute -top-2 -right-2 w-6 h-6 bg-red-500 hover:bg-red-400 rounded-full flex items-center justify-center transition"
                  >
                    <X size={12} />
                  </button>
                </div>
              )}

              {isUploadingFace && (
                <div className="mt-3">
                  <div className="flex justify-between text-xs text-gray-400 mb-1">
                    <span>顔画像をアップロード中...</span>
                    <span>{sourceFaceUploadProgress}%</span>
                  </div>
                  <div className="w-full bg-gray-800 rounded-full h-1.5">
                    <div
                      className="bg-pink-500 h-1.5 rounded-full transition-all"
                      style={{ width: `${sourceFaceUploadProgress}%` }}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Script Section */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
              <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
                <FileText size={20} className="text-purple-400" />
                台本
              </h2>

              {/* Script mode toggle */}
              <div className="flex gap-2 mb-4">
                <button
                  onClick={() => setScriptMode("ai")}
                  className={`flex-1 py-2.5 px-4 rounded-lg text-sm font-medium transition flex items-center justify-center gap-2 ${
                    scriptMode === "ai"
                      ? "bg-purple-500/20 border border-purple-500/50 text-purple-300"
                      : "bg-gray-800 border border-gray-700 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  <Wand2 size={16} />
                  AI自動生成
                </button>
                <button
                  onClick={() => setScriptMode("manual")}
                  className={`flex-1 py-2.5 px-4 rounded-lg text-sm font-medium transition flex items-center justify-center gap-2 ${
                    scriptMode === "manual"
                      ? "bg-blue-500/20 border border-blue-500/50 text-blue-300"
                      : "bg-gray-800 border border-gray-700 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  <FileText size={16} />
                  台本を入力
                </button>
              </div>

              {scriptMode === "ai" ? (
                <div className="space-y-3">
                  <div>
                    <label className="block text-sm text-gray-400 mb-1">
                      商品名 / テーマ <span className="text-red-400">*</span>
                    </label>
                    <input
                      type="text"
                      placeholder={window.__t('autoVideoPage_b7343f', '例: KYOGOKUカラーシャンプー、春のヘアケアおすすめ')}
                      value={topic}
                      onChange={(e) => setTopic(e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2.5 focus:outline-none focus:border-purple-500"
                    />
                  </div>

                  {/* Product Image Upload */}
                  <div>
                    <label className="block text-sm text-gray-400 mb-1 flex items-center gap-1">
                      <Camera size={14} className="text-orange-400" />
                      商品写真（任意・最大5枚）
                    </label>
                    <p className="text-xs text-gray-500 mb-2">
                      商品写真をアップロードすると、AIが自動で商品名・特徴・価格を読み取って台本に反映します
                    </p>

                    {productImagePreviews.length > 0 && (
                      <div className="flex gap-2 mb-2 flex-wrap">
                        {productImagePreviews.map((preview, idx) => (
                          <div key={idx} className="relative group">
                            <img
                              src={preview}
                              alt={`商品写真 ${idx + 1}`}
                              className="w-20 h-20 object-cover rounded-lg border border-gray-700"
                            />
                            <button
                              onClick={() => removeProductImage(idx)}
                              className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-500 hover:bg-red-400 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition"
                            >
                              <X size={10} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}

                    {productImages.length < 5 && (
                      <button
                        onClick={() => imageInputRef.current?.click()}
                        className="w-full py-2.5 border-2 border-dashed border-gray-700 rounded-lg text-sm text-gray-400 hover:border-orange-500/50 hover:text-orange-300 transition flex items-center justify-center gap-2"
                      >
                        <ImageIcon size={16} />
                        {productImages.length === 0
                          ? [window.__t('autoVideoPage_dba7ee', '商品写真をアップロード')]
                          : `さらに追加（${productImages.length}/5）`}
                      </button>
                    )}
                    <input
                      ref={imageInputRef}
                      type="file"
                      accept="image/*"
                      multiple
                      className="hidden"
                      onChange={handleImageSelect}
                    />

                    {isUploadingImages && (
                      <div className="mt-2">
                        <div className="flex justify-between text-xs text-gray-400 mb-1">
                          <span>商品写真をアップロード中...</span>
                          <span>{imageUploadProgress}%</span>
                        </div>
                        <div className="w-full bg-gray-800 rounded-full h-1.5">
                          <div
                            className="bg-orange-500 h-1.5 rounded-full transition-all"
                            style={{ width: `${imageUploadProgress}%` }}
                          />
                        </div>
                      </div>
                    )}
                  </div>

                  <div>
                    <label className="block text-sm text-gray-400 mb-1">
                      商品情報（任意）
                    </label>
                    <textarea
                      placeholder="商品の特徴、価格、ターゲット層など（AIがより良い台本を生成します）"
                      value={productInfo}
                      onChange={(e) => setProductInfo(e.target.value)}
                      rows={3}
                      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2.5 focus:outline-none focus:border-purple-500 resize-none"
                    />
                  </div>
                  <p className="text-xs text-gray-500 flex items-center gap-1">
                    <Sparkles size={12} className="text-purple-400" />
                    AitherHubの分析データ（売れるパターン）を自動で活用して台本を生成します
                  </p>
                </div>
              ) : (
                <div>
                  <label className="block text-sm text-gray-400 mb-1">
                    台本テキスト <span className="text-red-400">*</span>
                  </label>
                  <textarea
                    placeholder="インフルエンサーが話す台本を入力してください..."
                    value={scriptText}
                    onChange={(e) => setScriptText(e.target.value)}
                    rows={6}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2.5 focus:outline-none focus:border-blue-500 resize-none"
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    {scriptText.length} 文字
                  </p>
                </div>
              )}
            </div>

            {/* Voice & Settings */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
              <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
                <Mic size={20} className="text-green-400" />
                声 & 設定
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Voice selection */}
                <div>
                  <label className="block text-sm text-gray-400 mb-1">
                    声の選択
                  </label>
                  {voicesLoading ? (
                    <div className="flex items-center gap-2 text-gray-500 text-sm py-2">
                      <Loader2 size={14} className="animate-spin" />
                      読み込み中...
                    </div>
                  ) : (
                    <select
                      value={selectedVoiceId}
                      onChange={(e) => setSelectedVoiceId(e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2.5 focus:outline-none focus:border-green-500"
                    >
                      <option value="">{window.__t('autoVideoPage_fb9e16', 'デフォルト音声')}</option>
                      {voices.map((v) => (
                        <option key={v.voice_id || v.id} value={v.voice_id || v.id}>
                          {v.name} {v.labels?.accent ? `(${v.labels.accent})` : ""}
                        </option>
                      ))}
                    </select>
                  )}
                </div>

                {/* Quality */}
                <div>
                  <label className="block text-sm text-gray-400 mb-1">
                    品質設定
                  </label>
                  <select
                    value={quality}
                    onChange={(e) => setQuality(e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2.5 focus:outline-none focus:border-green-500"
                  >
                    <option value="fast">{window.__t('autoVideoPage_36b102', 'Standard（プレビュー・高速）')}</option>
                    <option value="pro">{window.__t('autoVideoPage_1b41cf', 'Pro（高品質・推奨）')}</option>
                    <option value="cinema">{window.__t('autoVideoPage_55278b', 'Cinema（最高品質・時間がかかります）')}</option>
                  </select>
                </div>

                {/* Lip sync toggle */}
                <div className="flex items-center justify-between md:col-span-2 bg-gray-800/50 rounded-lg px-4 py-3">
                  <div>
                    <p className="text-sm font-medium">{window.__t('aiLiveCreatorPage_c8ac37', 'リップシンク')}</p>
                    <p className="text-xs text-gray-500">
                      音声に合わせて口の動きを同期（Sync.so + ElevenLabs TTS）
                    </p>
                  </div>
                  <button
                    onClick={() => setEnableLipSync(!enableLipSync)}
                    className={`relative w-12 h-6 rounded-full transition ${
                      enableLipSync ? "bg-green-500" : "bg-gray-600"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                        enableLipSync ? "translate-x-6" : ""
                      }`}
                    />
                  </button>
                </div>
              </div>

              {/* Advanced settings */}
              <button
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="mt-3 text-sm text-gray-500 hover:text-gray-300 flex items-center gap-1 transition"
              >
                <Settings size={14} />
                詳細設定 {showAdvanced ? "▲" : "▼"}
              </button>

              {showAdvanced && (
                <div className="mt-3 grid grid-cols-2 gap-3 p-3 bg-gray-800/30 rounded-lg border border-gray-700/50">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">{window.__t('script_language', '言語')}</label>
                    <select
                      value={language}
                      onChange={(e) => setLanguage(e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm focus:outline-none"
                    >
                      <option value="ja">{window.__t('language_japanese', '日本語')}</option>
                      <option value="en">English</option>
                      <option value="zh">{window.__t('scriptGen_langZh', '中文')}</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">{window.__t('scriptGen_toneLabel', 'トーン')}</label>
                    <select
                      value={tone}
                      onChange={(e) => setTone(e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm focus:outline-none"
                    >
                      <option value="professional_friendly">{window.__t('autoVideoPage_faaee7', 'プロフェッショナル')}</option>
                      <option value="energetic">{window.__t('autoVideoPage_3f558c', 'エネルギッシュ')}</option>
                      <option value="calm">{window.__t('autoVideoPage_561ecb', '落ち着いた')}</option>
                    </select>
                  </div>
                </div>
              )}
            </div>

            {/* Error */}
            {error && (
              <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 flex items-start gap-3">
                <AlertCircle size={20} className="text-red-400 mt-0.5 shrink-0" />
                <p className="text-red-300 text-sm">{error}</p>
              </div>
            )}

            {/* Submit button */}
            {!currentJobId ? (
              <button
                onClick={handleSubmit}
                disabled={isSubmitting || isUploading || isUploadingFace}
                className="w-full py-4 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 disabled:from-gray-700 disabled:to-gray-700 rounded-xl font-semibold text-lg transition flex items-center justify-center gap-2"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 size={20} className="animate-spin" />
                    処理開始中...
                  </>
                ) : (
                  <>
                    <Sparkles size={20} />
                    動画を自動生成
                  </>
                )}
              </button>
            ) : (
              /* Progress display */
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold flex items-center gap-2">
                    {jobStatus?.status === "completed" ? (
                      <CheckCircle size={20} className="text-green-400" />
                    ) : jobStatus?.status === "error" || jobStatus?.status === "failed" ? (
                      <AlertCircle size={20} className="text-red-400" />
                    ) : (
                      <Loader2 size={20} className="text-blue-400 animate-spin" />
                    )}
                    {jobStatus?.step_detail && jobStatus?.status !== "completed" && jobStatus?.status !== "error"
                      ? jobStatus.step_detail
                      : statusLabels[jobStatus?.status] || jobStatus?.step || window.__t('statusProcessing', '処理中...')}
                  </h3>
                  <span className="text-sm text-gray-400">
                    {jobStatus?.progress || 0}%
                  </span>
                </div>

                {/* Progress bar */}
                <div className="w-full bg-gray-800 rounded-full h-3 mb-3">
                  <div
                    className={`h-3 rounded-full transition-all duration-500 ${getProgressColor(
                      jobStatus?.status
                    )}`}
                    style={{ width: `${jobStatus?.progress || 0}%` }}
                  />
                </div>

                {/* Pipeline steps */}
                <div className="flex items-center gap-2 text-xs text-gray-500 mb-4">
                  {["generating_script", "face_swapping", "lip_syncing"].map(
                    (step, i) => {
                      const currentStatus = jobStatus?.status;
                      const steps = [
                        "pending",
                        "generating_script",
                        "face_swapping",
                        "lip_syncing",
                        "finalizing",
                        "completed",
                      ];
                      const currentIdx = steps.indexOf(currentStatus);
                      const stepIdx = steps.indexOf(step);
                      const isDone = currentIdx > stepIdx;
                      const isActive = currentStatus === step;

                      return (
                        <div key={step} className="flex items-center gap-2">
                          {i > 0 && <span className="text-gray-700">→</span>}
                          <span
                            className={`px-2 py-0.5 rounded ${
                              isDone
                                ? "bg-green-500/20 text-green-400"
                                : isActive
                                ? "bg-blue-500/20 text-blue-400"
                                : "bg-gray-800 text-gray-600"
                            }`}
                          >
                            {[window.__t('autoVideoPage_4b8c58', '台本'), window.__t('autoVideoPage_ddd411', '顔合成'), window.__t('autoVideoPage_69b289', 'リップシンク + 音声')][i]}
                          </span>
                        </div>
                      );
                    }
                  )}
                </div>

                {/* Generated script preview */}
                {jobStatus?.generated_script && (
                  <div className="bg-gray-800/50 rounded-lg p-3 mb-3">
                    <p className="text-xs text-gray-500 mb-1">{window.__t('autoVideoPage_4869f1', '生成された台本:')}</p>
                    <p className="text-sm text-gray-300 line-clamp-3">
                      {jobStatus.generated_script}
                    </p>
                  </div>
                )}

                {/* Error message */}
                {(jobStatus?.status === "failed" || jobStatus?.status === "error") && jobStatus?.error && (
                  <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3 mb-3">
                    <p className="text-sm text-red-300">{jobStatus.error}</p>
                  </div>
                )}

                {/* Elapsed time */}
                {jobStatus?.elapsed_sec > 0 && (
                  <p className="text-xs text-gray-500 flex items-center gap-1">
                    <Clock size={12} />
                    経過時間: {Math.round(jobStatus.elapsed_sec)}秒
                  </p>
                )}

                {/* Actions */}
                <div className="flex gap-2 mt-4">
                  {(jobStatus?.status === "completed" && jobStatus?.progress === 100) && (
                    <a
                      href={jobStatus?.result_video_url || autoVideoService.getDownloadUrl(currentJobId)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex-1 py-2.5 bg-green-600 hover:bg-green-500 rounded-lg font-medium text-center transition flex items-center justify-center gap-2"
                    >
                      <Download size={16} />
                      動画をダウンロード
                    </a>
                  )}
                  {(jobStatus?.status === "completed" ||
                    jobStatus?.status === "failed" ||
                    jobStatus?.status === "error") && (
                    <button
                      onClick={resetForm}
                      className="flex-1 py-2.5 bg-gray-700 hover:bg-gray-600 rounded-lg font-medium transition flex items-center justify-center gap-2"
                    >
                      <RefreshCw size={16} />
                      新しい動画を作成
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* ── Right: Job History ── */}
          <div className="space-y-4">
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-sm">{window.__t('autoVideoPage_0c10ff', '生成履歴')}</h3>
                <button
                  onClick={loadJobs}
                  className="p-1.5 hover:bg-gray-800 rounded transition"
                  disabled={jobsLoading}
                >
                  <RefreshCw
                    size={14}
                    className={jobsLoading ? "animate-spin" : ""}
                  />
                </button>
              </div>

              {jobs.length === 0 ? (
                <p className="text-sm text-gray-500 text-center py-6">
                  まだ生成履歴がありません
                </p>
              ) : (
                <div className="space-y-2 max-h-[600px] overflow-y-auto">
                  {jobs.map((job) => (
                    <div
                      key={job.job_id}
                      className={`p-3 rounded-lg border transition cursor-pointer ${
                        currentJobId === job.job_id
                          ? "bg-purple-500/10 border-purple-500/30"
                          : "bg-gray-800/50 border-gray-700/50 hover:border-gray-600"
                      }`}
                      onClick={() => {
                        setCurrentJobId(job.job_id);
                      }}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-medium truncate max-w-[180px]">
                          {job.topic}
                        </span>
                        <span
                          className={`text-xs px-1.5 py-0.5 rounded ${
                            job.status === "completed"
                              ? "bg-green-500/20 text-green-400"
                              : job.status === "failed" || job.status === "error"
                              ? "bg-red-500/20 text-red-400"
                              : "bg-blue-500/20 text-blue-400"
                          }`}
                        >
                          {job.status === "completed"
                            ? [window.__t('clip_completed', '完了')]
                            : job.status === "failed" || job.status === "error"
                            ? [window.__t('clip_failed', '失敗')]
                            : `${job.progress}%`}
                        </span>
                      </div>

                      {/* Progress mini bar */}
                      <div className="w-full bg-gray-700 rounded-full h-1 mb-1.5">
                        <div
                          className={`h-1 rounded-full ${getProgressColor(job.status)}`}
                          style={{ width: `${job.progress}%` }}
                        />
                      </div>

                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">
                          {new Date(job.created_at * 1000).toLocaleString("ja-JP", {
                            month: "short",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </span>
                        <div className="flex gap-1">
                          {job.status === "completed" && (
                            <a
                              href={autoVideoService.getDownloadUrl(job.job_id)}
                              onClick={(e) => e.stopPropagation()}
                              className="p-1 hover:bg-gray-700 rounded transition"
                              title={window.__t('clip_download', 'ダウンロード')}
                            >
                              <Download size={12} className="text-green-400" />
                            </a>
                          )}
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteJob(job.job_id);
                            }}
                            className="p-1 hover:bg-gray-700 rounded transition"
                            title={window.__t('sidebar_delete', '削除')}
                          >
                            <Trash2 size={12} className="text-gray-500" />
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
