import { useState, useEffect, useRef } from "react";
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
  Link,
  Camera,
  ChevronDown,
  ChevronUp,
  Search,
  Globe,
  User,
  Trash2,
  Volume2,
  Eye,
  Package,
  UserPlus,
} from "lucide-react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL;
const ADMIN_KEY = "aither:hub";

export default function AiVideoGeneratorPage() {
  const navigate = useNavigate();

  // ── Input Mode ──
  const [inputMode, setInputMode] = useState("upload"); // "upload" | "image_url" | "page_url"

  // ── Product Input State ──
  const [imageFile, setImageFile] = useState(null); // { file, preview }
  const [imageUrl, setImageUrl] = useState("");
  const [pageUrl, setPageUrl] = useState("");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analyzedProduct, setAnalyzedProduct] = useState(null);

  // ── Manual Override (editable after analysis) ──
  const [productName, setProductName] = useState("");
  const [productDescription, setProductDescription] = useState("");
  const [productImageUrl, setProductImageUrl] = useState("");
  const [productPrice, setProductPrice] = useState("");

  // ── Avatar/Liver State ──
  const [avatars, setAvatars] = useState([]);
  const [selectedAvatarId, setSelectedAvatarId] = useState("");
  const [loadingAvatars, setLoadingAvatars] = useState(true);

  // ── Voice Selection (NEW) ──
  const [voices, setVoices] = useState([]);
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [loadingVoices, setLoadingVoices] = useState(false);

  // ── Language Selection (IMPROVED) ──
  const [languages, setLanguages] = useState([]);
  const [language, setLanguage] = useState("ja");

  // ── Generation Options ──
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [tone, setTone] = useState("energetic");
  const [durationSeconds, setDurationSeconds] = useState(60);
  const [customScript, setCustomScript] = useState("");
  const [useCustomScript, setUseCustomScript] = useState(false);
  const [benefits, setBenefits] = useState("");
  const [targetAudience, setTargetAudience] = useState("");

  // ── Product Showcase (NEW) ──
  const [showcaseMode, setShowcaseMode] = useState("");
  const [showcaseDescription, setShowcaseDescription] = useState("");

  // ── Person Photo Analysis (NEW) ──
  const [personImageFile, setPersonImageFile] = useState(null);
  const [personImageUrl, setPersonImageUrl] = useState("");
  const [personAnalysis, setPersonAnalysis] = useState(null);
  const [isAnalyzingPerson, setIsAnalyzingPerson] = useState(false);

  // ── Custom Person Upload for ライバー選択 (NEW) ──
  const [customPersonPreview, setCustomPersonPreview] = useState(null); // preview URL for display
  const [customPersonUploading, setCustomPersonUploading] = useState(false);
  const customPersonInputRef = useRef(null);

  // ── My Persons (我の人物) - Saved persons list ──
  const [myPersons, setMyPersons] = useState([]);
  const [loadingMyPersons, setLoadingMyPersons] = useState(false);
  const [showSavePersonDialog, setShowSavePersonDialog] = useState(false);
  const [savePersonName, setSavePersonName] = useState("");

  // ── Job State ──
  const [isGenerating, setIsGenerating] = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [error, setError] = useState(null);

  // ── Job History (DB-backed) ──
  const [jobHistory, setJobHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // ── Active Tab ──
  const [activeTab, setActiveTab] = useState("generate"); // "generate" | "history" | "person"

  const pollRef = useRef(null);
  const fileInputRef = useRef(null);
  const personFileInputRef = useRef(null);

  // ── Load Data on Mount ──
  useEffect(() => {
    loadAvatars();
    loadVoices();
    loadLanguages();
    loadJobHistory();
    loadMyPersons();
  }, []);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const loadAvatars = async (retryCount = 0) => {
    try {
      setLoadingAvatars(true);
      const res = await axios.get(`${API_BASE}/api/v1/ai-video-generator/avatars`, {
        headers: { "X-Admin-Key": ADMIN_KEY },
        timeout: 60000,
      });
      setAvatars(res.data.avatars || []);
      if (res.data.avatars?.length > 0) {
        setSelectedAvatarId(res.data.avatars[0].avatar_id);
      }
    } catch (err) {
      console.error("Failed to load avatars:", err);
      if (retryCount < 1 && (err.code === 'ECONNABORTED' || err.message?.includes('timeout') || !err.response)) {
        return loadAvatars(retryCount + 1);
      }
    } finally {
      setLoadingAvatars(false);
    }
  };

  const loadVoices = async () => {
    try {
      setLoadingVoices(true);
      const res = await axios.get(`${API_BASE}/api/v1/ai-video-generator/voices`, {
        headers: { "X-Admin-Key": ADMIN_KEY },
        timeout: 30000,
      });
      const voiceList = res.data.voices || [];
      setVoices(voiceList);
      if (voiceList.length > 0 && !selectedVoiceId) {
        setSelectedVoiceId(voiceList[0].voice_id);
      }
    } catch (err) {
      console.error("Failed to load voices:", err);
    } finally {
      setLoadingVoices(false);
    }
  };

  const loadLanguages = async () => {
    try {
      const res = await axios.get(`${API_BASE}/api/v1/ai-video-generator/languages`, {
        headers: { "X-Admin-Key": ADMIN_KEY },
      });
      setLanguages(res.data.languages || []);
    } catch (err) {
      console.error("Failed to load languages:", err);
      // Fallback
      setLanguages([
        { code: "ja", name: "日本語" },
        { code: "en", name: "English" },
        { code: "zh", name: "中文" },
        { code: "ko", name: "한국어" },
      ]);
    }
  };

  const loadJobHistory = async () => {
    try {
      setHistoryLoading(true);
      const res = await axios.get(`${API_BASE}/api/v1/ai-video-generator/jobs?limit=50`, {
        headers: { "X-Admin-Key": ADMIN_KEY },
      });
      setJobHistory(res.data.jobs || []);
    } catch (err) {
      console.error("Failed to load job history:", err);
    } finally {
      setHistoryLoading(false);
    }
  };

  // ── My Persons (load/save/delete) ──
  const loadMyPersons = async () => {
    try {
      setLoadingMyPersons(true);
      const res = await axios.get(`${API_BASE}/api/v1/ai-video-generator/custom-persons`, {
        headers: { "X-Admin-Key": ADMIN_KEY },
      });
      setMyPersons(res.data.persons || []);
    } catch (err) {
      console.error("Failed to load my persons:", err);
    } finally {
      setLoadingMyPersons(false);
    }
  };

  const handleSavePerson = async (name, imageUrl, analysis) => {
    try {
      const params = new URLSearchParams({ name, image_url: imageUrl });
      if (analysis) params.append("analysis", JSON.stringify(analysis));
      const res = await axios.post(
        `${API_BASE}/api/v1/ai-video-generator/custom-persons?${params.toString()}`,
        null,
        { headers: { "X-Admin-Key": ADMIN_KEY } }
      );
      if (res.data.success) {
        await loadMyPersons();
        setShowSavePersonDialog(false);
        setSavePersonName("");
      }
    } catch (err) {
      setError(err.response?.data?.detail || "人物の保存に失敗しました");
    }
  };

  const handleDeletePerson = async (personId) => {
    try {
      await axios.delete(`${API_BASE}/api/v1/ai-video-generator/custom-persons/${personId}`, {
        headers: { "X-Admin-Key": ADMIN_KEY },
      });
      await loadMyPersons();
      if (selectedAvatarId === `myperson:${personId}`) {
        setSelectedAvatarId("");
        setPersonImageUrl("");
      }
    } catch (err) {
      setError("人物の削除に失敗しました");
    }
  };

  const handleSelectMyPerson = (person) => {
    setSelectedAvatarId(`myperson:${person.id}`);
    setPersonImageUrl(person.image_url);
    setCustomPersonPreview(person.image_url);
  };

  // ── Image Upload Handler ──
  const handleImageSelect = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("画像ファイルを選択してください");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      setError("画像サイズは20MB以下にしてください");
      return;
    }
    setImageFile({ file, preview: URL.createObjectURL(file) });
    setError(null);
    e.target.value = "";
  };

  // ── Person Photo Upload Handler (NEW) ──
  const handlePersonImageSelect = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("画像ファイルを選択してください");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setError("人物写真は10MB以下にしてください");
      return;
    }
    setPersonImageFile({ file, preview: URL.createObjectURL(file) });
    setError(null);
    e.target.value = "";
  };

  // ── Analyze Person Photo (NEW) ──
  const handleAnalyzePerson = async () => {
    if (!personImageFile && !personImageUrl) {
      setError("人物写真をアップロードするかURLを入力してください");
      return;
    }
    setIsAnalyzingPerson(true);
    setError(null);
    setPersonAnalysis(null);

    try {
      let res;
      if (personImageFile) {
        const formData = new FormData();
        formData.append("image", personImageFile.file);
        res = await axios.post(
          `${API_BASE}/api/v1/ai-video-generator/analyze-person`,
          formData,
          {
            headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "multipart/form-data" },
            timeout: 60000,
          }
        );
      } else {
        res = await axios.post(
          `${API_BASE}/api/v1/ai-video-generator/analyze-person?image_url=${encodeURIComponent(personImageUrl)}`,
          null,
          { headers: { "X-Admin-Key": ADMIN_KEY }, timeout: 60000 }
        );
      }

      if (res.data.success && res.data.analysis) {
        setPersonAnalysis(res.data.analysis);

        // Auto-upload person image to blob for use in video generation
        try {
          let uploadRes;
          if (personImageFile) {
            const uploadForm = new FormData();
            uploadForm.append("image", personImageFile.file);
            uploadRes = await axios.post(
              `${API_BASE}/api/v1/ai-video-generator/upload-person-photo`,
              uploadForm,
              { headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "multipart/form-data" } }
            );
          } else if (personImageUrl) {
            // For URL-based analysis, fetch the image and upload it
            const imgResp = await fetch(personImageUrl);
            const blob = await imgResp.blob();
            const uploadForm = new FormData();
            uploadForm.append("image", new File([blob], "person.jpg", { type: blob.type || "image/jpeg" }));
            uploadRes = await axios.post(
              `${API_BASE}/api/v1/ai-video-generator/upload-person-photo`,
              uploadForm,
              { headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "multipart/form-data" } }
            );
          }
          if (uploadRes?.data?.success) {
            // Set as custom person for video generation
            setPersonImageUrl(uploadRes.data.url);
            setCustomPersonPreview(personImageFile?.preview || personImageUrl);
            setSelectedAvatarId("custom_person");
            // Show save dialog to let user save to My Persons
            setShowSavePersonDialog(true);
            setSavePersonName("");
          }
        } catch (uploadErr) {
          console.warn("Auto-upload person image failed (non-fatal):", uploadErr);
          // Non-fatal: analysis still succeeded, user can manually upload in ライバー選択
        }
      } else {
        setError("人物分析に失敗しました");
      }
    } catch (err) {
      setError(err.response?.data?.detail || "人物分析に失敗しました");
    } finally {
      setIsAnalyzingPerson(false);
    }
  };

  // ── Analyze Product ──
  const handleAnalyze = async () => {
    setIsAnalyzing(true);
    setError(null);
    setAnalyzedProduct(null);

    try {
      let res;

      if (inputMode === "upload" && imageFile) {
        const formData = new FormData();
        formData.append("image", imageFile.file);
        res = await axios.post(
          `${API_BASE}/api/v1/ai-video-generator/analyze-product`,
          formData,
          {
            headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "multipart/form-data" },
          }
        );
      } else if (inputMode === "image_url" && imageUrl.trim()) {
        res = await axios.post(
          `${API_BASE}/api/v1/ai-video-generator/analyze-product?image_url=${encodeURIComponent(imageUrl.trim())}`,
          null,
          { headers: { "X-Admin-Key": ADMIN_KEY } }
        );
      } else if (inputMode === "page_url" && pageUrl.trim()) {
        res = await axios.post(
          `${API_BASE}/api/v1/ai-video-generator/analyze-product?page_url=${encodeURIComponent(pageUrl.trim())}`,
          null,
          { headers: { "X-Admin-Key": ADMIN_KEY } }
        );
      } else {
        setError("商品写真、画像URL、または商品ページURLを入力してください");
        setIsAnalyzing(false);
        return;
      }

      if (res.data.success && res.data.product) {
        const p = res.data.product;
        setAnalyzedProduct(p);
        setProductName(p.name || "");
        setProductDescription(p.description || "");
        setProductPrice(p.price || "");
        if (res.data.image_url && !res.data.image_url.startsWith("data:")) {
          setProductImageUrl(res.data.image_url);
        }
      } else {
        setError("商品情報を解析できませんでした。別の画像をお試しください。");
      }
    } catch (err) {
      setError(err.response?.data?.detail || "商品解析に失敗しました");
    } finally {
      setIsAnalyzing(false);
    }
  };

  // ── Custom Person Upload for ライバー選択 ──
  const handleCustomPersonUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    // Show preview immediately
    const previewUrl = URL.createObjectURL(file);
    setCustomPersonPreview(previewUrl);
    setCustomPersonUploading(true);
    setSelectedAvatarId("custom_person");
    try {
      const formData = new FormData();
      formData.append("image", file);
      const res = await axios.post(
        `${API_BASE}/api/v1/ai-video-generator/upload-person-photo`,
        formData,
        { headers: { "X-Admin-Key": ADMIN_KEY, "Content-Type": "multipart/form-data" } }
      );
      if (res.data.success) {
        setPersonImageUrl(res.data.url);
      } else {
        setError("人物写真のアップロードに失敗しました");
        setCustomPersonPreview(null);
        setSelectedAvatarId("");
      }
    } catch (err) {
      setError(err.response?.data?.detail || "人物写真のアップロードに失敗しました");
      setCustomPersonPreview(null);
      setSelectedAvatarId("");
    } finally {
      setCustomPersonUploading(false);
    }
  };

  const handleRemoveCustomPerson = () => {
    setCustomPersonPreview(null);
    setPersonImageUrl("");
    if (selectedAvatarId === "custom_person") {
      setSelectedAvatarId("");
    }
    if (customPersonInputRef.current) {
      customPersonInputRef.current.value = "";
    }
  };

  // ── Generate Video ──
  const handleGenerate = async () => {
    if (!productName.trim() && !analyzedProduct) {
      setError("まず商品を解析するか、商品名を入力してください");
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
        product_name: productName.trim() || analyzedProduct?.name || "商品",
        product_description: productDescription.trim() || analyzedProduct?.description || undefined,
        product_image_url: productImageUrl.trim() || undefined,
        product_price: productPrice.trim() || analyzedProduct?.price || undefined,
        benefits: benefits.trim() || analyzedProduct?.notes || undefined,
        target_audience: targetAudience.trim() || undefined,
        avatar_id: selectedAvatarId.startsWith("myperson:") ? "custom_person" : selectedAvatarId,
        voice_id: selectedVoiceId || undefined,
        tone,
        language,
        duration_seconds: durationSeconds,
        custom_script: useCustomScript ? customScript.trim() : undefined,
        // NEW: Product showcase
        showcase_mode: showcaseMode || undefined,
        showcase_description: showcaseDescription.trim() || undefined,
        // NEW: Person photo
        person_image_url: personImageUrl || undefined,
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

  // ── Delete Job (NEW) ──
  const handleDeleteJob = async (jobId) => {
    try {
      await axios.delete(`${API_BASE}/api/v1/ai-video-generator/jobs/${jobId}`, {
        headers: { "X-Admin-Key": ADMIN_KEY },
      });
      setJobHistory((prev) => prev.filter((j) => j.job_id !== jobId));
    } catch (err) {
      console.error("Failed to delete job:", err);
    }
  };

  const getStatusLabel = (status) => {
    const labels = {
      queued: "キューに追加中...",
      analyzing_product: "🔍 商品を解析中...",
      generating_script: "🧠 AI台本を生成中...",
      generating_audio: "🎙️ 音声を生成中...",
      generating_video: "🎬 動画を生成中...",
      compositing_showcase: "📷 商品展示を合成中...",
      completed: "✅ 完成！",
      failed: "❌ エラー",
    };
    return labels[status] || status;
  };

  const getProgressColor = (status) => {
    if (status === "completed") return "bg-green-500";
    if (status === "failed") return "bg-red-500";
    return "bg-emerald-500";
  };

  const hasInput = () => {
    if (inputMode === "upload") return !!imageFile;
    if (inputMode === "image_url") return !!imageUrl.trim();
    if (inputMode === "page_url") return !!pageUrl.trim();
    return false;
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
              <h1 className="text-2xl font-bold bg-gradient-to-r from-emerald-400 to-cyan-400 bg-clip-text text-transparent">
                AI Video Generator
              </h1>
              <p className="text-sm text-gray-400">
                商品写真を入れるだけ → ライバーが紹介する動画を自動生成
              </p>
            </div>
          </div>
          <span className="px-3 py-1 bg-emerald-500/20 text-emerald-300 rounded-full text-xs font-medium">
            Beta v2
          </span>
        </div>
      </header>

      {/* Tab Navigation */}
      <div className="max-w-7xl mx-auto px-6 pt-4">
        <div className="flex gap-1 bg-gray-900 rounded-lg p-1 w-fit">
          <button
            onClick={() => setActiveTab("generate")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition flex items-center gap-2 ${
              activeTab === "generate" ? "bg-emerald-600 text-white" : "text-gray-400 hover:text-white"
            }`}
          >
            <Zap className="w-4 h-4" />
            動画生成
          </button>
          <button
            onClick={() => setActiveTab("person")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition flex items-center gap-2 ${
              activeTab === "person" ? "bg-emerald-600 text-white" : "text-gray-400 hover:text-white"
            }`}
          >
            <User className="w-4 h-4" />
            人物分析
          </button>
          <button
            onClick={() => { setActiveTab("history"); loadJobHistory(); }}
            className={`px-4 py-2 rounded-md text-sm font-medium transition flex items-center gap-2 ${
              activeTab === "history" ? "bg-emerald-600 text-white" : "text-gray-400 hover:text-white"
            }`}
          >
            <RefreshCw className="w-4 h-4" />
            履歴
          </button>
        </div>
      </div>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-6 py-6">

        {/* ═══════════ TAB: Generate ═══════════ */}
        {activeTab === "generate" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">

            {/* ═══ Left Column: Input ═══ */}
            <div className="lg:col-span-2 space-y-6">

              {/* STEP 1: Product Input */}
              <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
                <h2 className="text-lg font-semibold mb-2 flex items-center gap-2">
                  <Camera className="w-5 h-5 text-emerald-400" />
                  商品を入力
                </h2>
                <p className="text-sm text-gray-400 mb-4">
                  写真・画像URL・商品ページURLのいずれかを入力 → AIが自動解析します
                </p>

                {/* Input Mode Tabs */}
                <div className="flex gap-1 mb-4 bg-gray-800 rounded-lg p-1">
                  <button
                    onClick={() => setInputMode("upload")}
                    className={`flex-1 py-2 px-3 rounded-md text-sm font-medium transition flex items-center justify-center gap-1.5 ${
                      inputMode === "upload" ? "bg-emerald-600 text-white" : "text-gray-400 hover:text-white"
                    }`}
                  >
                    <Upload className="w-3.5 h-3.5" />
                    写真アップ
                  </button>
                  <button
                    onClick={() => setInputMode("image_url")}
                    className={`flex-1 py-2 px-3 rounded-md text-sm font-medium transition flex items-center justify-center gap-1.5 ${
                      inputMode === "image_url" ? "bg-emerald-600 text-white" : "text-gray-400 hover:text-white"
                    }`}
                  >
                    <ImageIcon className="w-3.5 h-3.5" />
                    画像URL
                  </button>
                  <button
                    onClick={() => setInputMode("page_url")}
                    className={`flex-1 py-2 px-3 rounded-md text-sm font-medium transition flex items-center justify-center gap-1.5 ${
                      inputMode === "page_url" ? "bg-emerald-600 text-white" : "text-gray-400 hover:text-white"
                    }`}
                  >
                    <Link className="w-3.5 h-3.5" />
                    商品ページURL
                  </button>
                </div>

                {/* Input Area */}
                {inputMode === "upload" && (
                  <div>
                    <input ref={fileInputRef} type="file" accept="image/*" onChange={handleImageSelect} className="hidden" />
                    {imageFile ? (
                      <div className="relative">
                        <img src={imageFile.preview} alt="Product" className="w-full max-h-64 object-contain rounded-lg border border-gray-700 bg-gray-800" />
                        <button
                          onClick={() => { setImageFile(null); setAnalyzedProduct(null); }}
                          className="absolute top-2 right-2 p-1.5 bg-red-500/80 hover:bg-red-500 rounded-full text-white text-xs"
                        >✕</button>
                      </div>
                    ) : (
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        className="w-full py-12 border-2 border-dashed border-gray-700 hover:border-emerald-500/50 rounded-xl flex flex-col items-center gap-3 transition group"
                      >
                        <div className="w-14 h-14 rounded-full bg-gray-800 group-hover:bg-emerald-500/10 flex items-center justify-center transition">
                          <Camera className="w-7 h-7 text-gray-500 group-hover:text-emerald-400 transition" />
                        </div>
                        <div className="text-center">
                          <p className="text-sm text-gray-300">クリックして商品写真を選択</p>
                          <p className="text-xs text-gray-500 mt-1">JPG, PNG, WebP（20MBまで）</p>
                        </div>
                      </button>
                    )}
                  </div>
                )}

                {inputMode === "image_url" && (
                  <input
                    type="text" value={imageUrl} onChange={(e) => setImageUrl(e.target.value)}
                    placeholder="https://example.com/product-image.jpg"
                    className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 outline-none"
                  />
                )}

                {inputMode === "page_url" && (
                  <div>
                    <input
                      type="text" value={pageUrl} onChange={(e) => setPageUrl(e.target.value)}
                      placeholder="https://www.rakuten.co.jp/... or https://www.amazon.co.jp/..."
                      className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 outline-none"
                    />
                    <p className="text-xs text-gray-500 mt-1.5">楽天・Amazon・Yahoo!ショッピング・自社ECサイトなどの商品ページURL</p>
                  </div>
                )}

                {/* Analyze Button */}
                <button
                  onClick={handleAnalyze}
                  disabled={isAnalyzing || !hasInput()}
                  className={`mt-4 w-full py-3 rounded-lg font-medium text-sm flex items-center justify-center gap-2 transition ${
                    isAnalyzing || !hasInput() ? "bg-gray-700 text-gray-400 cursor-not-allowed" : "bg-emerald-600 hover:bg-emerald-500 text-white"
                  }`}
                >
                  {isAnalyzing ? (<><Loader2 className="w-4 h-4 animate-spin" />AIが商品を解析中...</>) : (<><Search className="w-4 h-4" />AIで商品情報を解析</>)}
                </button>

                {/* Analysis Result */}
                {analyzedProduct && (
                  <div className="mt-4 bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <CheckCircle className="w-4 h-4 text-emerald-400" />
                      <span className="text-sm font-medium text-emerald-300">商品情報を検出しました</span>
                    </div>
                    <div className="space-y-2">
                      <div>
                        <label className="text-xs text-gray-500">商品名</label>
                        <input type="text" value={productName} onChange={(e) => setProductName(e.target.value)}
                          className="w-full mt-0.5 px-3 py-2 bg-gray-800 border border-gray-700 rounded-md text-sm text-white focus:border-emerald-500 outline-none" />
                      </div>
                      <div>
                        <label className="text-xs text-gray-500">説明・特徴</label>
                        <textarea value={productDescription} onChange={(e) => setProductDescription(e.target.value)} rows={2}
                          className="w-full mt-0.5 px-3 py-2 bg-gray-800 border border-gray-700 rounded-md text-sm text-white focus:border-emerald-500 outline-none resize-none" />
                      </div>
                      {productPrice && (
                        <div>
                          <label className="text-xs text-gray-500">価格</label>
                          <input type="text" value={productPrice} onChange={(e) => setProductPrice(e.target.value)}
                            className="w-full mt-0.5 px-3 py-2 bg-gray-800 border border-gray-700 rounded-md text-sm text-white focus:border-emerald-500 outline-none" />
                        </div>
                      )}
                    </div>
                    <p className="text-xs text-gray-500 mt-2">※ 解析結果は編集できます</p>
                  </div>
                )}
              </div>

              {/* STEP 2: Voice & Language Selection (NEW - Prominent) */}
              <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
                <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                  <Volume2 className="w-5 h-5 text-purple-400" />
                  声・言語の選択
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Voice Selection */}
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5">音声（ボイス）</label>
                    {loadingVoices ? (
                      <div className="flex items-center gap-2 py-2">
                        <Loader2 className="w-4 h-4 animate-spin text-purple-400" />
                        <span className="text-sm text-gray-400">音声を読み込み中...</span>
                      </div>
                    ) : (
                      <select
                        value={selectedVoiceId}
                        onChange={(e) => setSelectedVoiceId(e.target.value)}
                        className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:border-purple-500 outline-none"
                      >
                        <option value="">デフォルト（自動選択）</option>
                        {voices.map((v) => (
                          <option key={v.voice_id} value={v.voice_id}>
                            {v.name} {v.language ? `(${v.language})` : ""} {v.gender ? `- ${v.gender}` : ""}
                          </option>
                        ))}
                      </select>
                    )}
                    {selectedVoiceId && voices.find(v => v.voice_id === selectedVoiceId)?.preview_url && (
                      <button
                        onClick={() => {
                          const audio = new Audio(voices.find(v => v.voice_id === selectedVoiceId).preview_url);
                          audio.play();
                        }}
                        className="mt-2 text-xs text-purple-400 hover:text-purple-300 flex items-center gap-1"
                      >
                        <Play className="w-3 h-3" /> プレビュー再生
                      </button>
                    )}
                  </div>

                  {/* Language Selection (Improved - more languages) */}
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5">出力言語</label>
                    <select
                      value={language}
                      onChange={(e) => setLanguage(e.target.value)}
                      className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:border-emerald-500 outline-none"
                    >
                      {languages.length > 0 ? (
                        languages.map((lang) => (
                          <option key={lang.code} value={lang.code}>
                            {lang.name} {lang.name_en ? `(${lang.name_en})` : ""}
                          </option>
                        ))
                      ) : (
                        <>
                          <option value="ja">🇯🇵 日本語</option>
                          <option value="en">🇺🇸 English</option>
                          <option value="zh">🇨🇳 中文</option>
                          <option value="ko">🇰🇷 한국어</option>
                        </>
                      )}
                    </select>
                    <p className="text-xs text-gray-500 mt-1">
                      <Globe className="w-3 h-3 inline mr-1" />
                      {languages.length}言語対応
                    </p>
                  </div>
                </div>
              </div>

              {/* Product Showcase Options (NEW) */}
              <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
                <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                  <Package className="w-5 h-5 text-orange-400" />
                  商品展示オプション
                </h2>
                <div className="space-y-4">
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5">展示モード</label>
                    <select
                      value={showcaseMode}
                      onChange={(e) => setShowcaseMode(e.target.value)}
                      className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:border-orange-500 outline-none"
                    >
                      <option value="">標準（ライバーのみ）</option>
                      <option value="overlay">オーバーレイ（商品画像を画面に表示）</option>
                      <option value="split">スプリット（画面分割：ライバー＋商品）</option>
                      <option value="fullscreen">フルスクリーン（商品メイン表示）</option>
                    </select>
                  </div>
                  {showcaseMode && (
                    <div>
                      <label className="block text-xs text-gray-400 mb-1.5">展示方式の詳細説明（任意）</label>
                      <textarea
                        value={showcaseDescription}
                        onChange={(e) => setShowcaseDescription(e.target.value)}
                        placeholder="例: 商品を右上に表示し、3秒ごとに角度を変えて回転させる。機能紹介時にズームイン。"
                        rows={3}
                        className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:border-orange-500 outline-none resize-none"
                      />
                      <p className="text-xs text-gray-500 mt-1">カメラ角度、回転、ズーム、表示位置などを自由に記述できます</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Advanced Options (Collapsed) */}
              <div className="bg-gray-900 rounded-xl border border-gray-800">
                <button
                  onClick={() => setShowAdvanced(!showAdvanced)}
                  className="w-full px-6 py-4 flex items-center justify-between text-left"
                >
                  <span className="text-sm font-medium text-gray-300 flex items-center gap-2">
                    <Sparkles className="w-4 h-4 text-yellow-400" />
                    詳細オプション
                  </span>
                  {showAdvanced ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
                </button>

                {showAdvanced && (
                  <div className="px-6 pb-6 space-y-4 border-t border-gray-800 pt-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                        <label className="block text-xs text-gray-400 mb-1">トーン</label>
                        <select value={tone} onChange={(e) => setTone(e.target.value)}
                          className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:border-emerald-500 outline-none">
                          <option value="energetic">🔥 元気・テンション高め</option>
                          <option value="professional_friendly">💼 プロフェッショナル</option>
                          <option value="calm">🌿 落ち着き・高級感</option>
                          <option value="sexy">✨ 魅力的・自信</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs text-gray-400 mb-1">動画の長さ</label>
                        <select value={durationSeconds} onChange={(e) => setDurationSeconds(Number(e.target.value))}
                          className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:border-emerald-500 outline-none">
                          <option value={30}>30秒（ショート）</option>
                          <option value={60}>60秒（スタンダード）</option>
                          <option value={90}>90秒（ロング）</option>
                          <option value={120}>120秒（フル）</option>
                        </select>
                      </div>
                    </div>

                    <div>
                      <label className="block text-xs text-gray-400 mb-1">特典・限定オファー</label>
                      <input type="text" value={benefits} onChange={(e) => setBenefits(e.target.value)}
                        placeholder="例: 今だけ送料無料、2本セットで20%OFF"
                        className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:border-emerald-500 outline-none" />
                    </div>

                    <div>
                      <label className="block text-xs text-gray-400 mb-1">ターゲット層</label>
                      <input type="text" value={targetAudience} onChange={(e) => setTargetAudience(e.target.value)}
                        placeholder="例: 30代女性、カラーリングの色落ちに悩む方"
                        className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:border-emerald-500 outline-none" />
                    </div>

                    {/* Custom Script */}
                    <div>
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={useCustomScript} onChange={(e) => setUseCustomScript(e.target.checked)}
                          className="w-4 h-4 rounded border-gray-600 text-emerald-500 focus:ring-emerald-500" />
                        <span className="text-sm text-gray-300">自分で台本を書く（AI生成をスキップ）</span>
                      </label>
                      {useCustomScript && (
                        <textarea value={customScript} onChange={(e) => setCustomScript(e.target.value)}
                          placeholder="ライバーに話してほしい台本を入力..."
                          rows={4}
                          className="mt-2 w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:border-emerald-500 outline-none resize-none" />
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* Generate Button */}
              <button
                onClick={handleGenerate}
                disabled={isGenerating || (!productName.trim() && !analyzedProduct) || !selectedAvatarId || customPersonUploading}
                className={`w-full py-4 rounded-xl font-bold text-lg flex items-center justify-center gap-3 transition-all ${
                  isGenerating || (!productName.trim() && !analyzedProduct) || !selectedAvatarId || customPersonUploading
                    ? "bg-gray-700 text-gray-400 cursor-not-allowed"
                    : "bg-gradient-to-r from-emerald-600 to-cyan-600 hover:from-emerald-500 hover:to-cyan-500 text-white shadow-lg shadow-emerald-500/25 hover:shadow-emerald-500/40"
                }`}
              >
                {isGenerating ? (<><Loader2 className="w-5 h-5 animate-spin" />生成中...</>) : (<><Zap className="w-5 h-5" />動画を生成する</>)}
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
                  <div className="mb-4">
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-gray-300">{getStatusLabel(jobStatus.status)}</span>
                      <span className="text-gray-400">{jobStatus.progress}%</span>
                    </div>
                    <div className="w-full h-3 bg-gray-800 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all duration-500 ${getProgressColor(jobStatus.status)}`}
                        style={{ width: `${jobStatus.progress}%` }} />
                    </div>
                  </div>

                  {jobStatus.script && (
                    <div className="mb-4">
                      <h4 className="text-sm font-medium text-gray-400 mb-2">生成された台本:</h4>
                      <div className="bg-gray-800 rounded-lg p-3 max-h-40 overflow-y-auto">
                        <p className="text-sm text-gray-200 whitespace-pre-wrap">{jobStatus.script}</p>
                      </div>
                    </div>
                  )}

                  {jobStatus.status === "completed" && jobStatus.video_url && (
                    <div className="mt-4">
                      <h4 className="text-sm font-medium text-green-400 mb-2 flex items-center gap-1">
                        <CheckCircle className="w-4 h-4" />動画が完成しました！
                      </h4>
                      <div className="relative rounded-lg overflow-hidden bg-black max-w-sm mx-auto">
                        <video src={jobStatus.video_url} controls className="w-full aspect-[9/16]" />
                      </div>
                      <div className="flex gap-3 mt-3 justify-center">
                        <a href={jobStatus.video_url} target="_blank" rel="noopener noreferrer"
                          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium flex items-center gap-2 transition">
                          <Download className="w-4 h-4" />ダウンロード
                        </a>
                        <button onClick={() => navigator.clipboard.writeText(jobStatus.video_url)}
                          className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium flex items-center gap-2 transition">
                          <Copy className="w-4 h-4" />URLコピー
                        </button>
                      </div>
                    </div>
                  )}

                  {jobStatus.status === "failed" && (
                    <div className="mt-4 bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                      <p className="text-red-300 text-sm">エラー: {jobStatus.error || "不明なエラー"}</p>
                      <p className="text-red-400/60 text-xs mt-1">ステップ: {jobStatus.error_step}</p>
                      {(jobStatus.error || "").toLowerCase().includes("insufficient credit") && 
                       avatars.find(a => a.avatar_id === selectedAvatarId)?.source === 'heygen' && (
                        <div className="mt-3 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
                          <p className="text-amber-300 text-sm font-medium">💡 Digital Twinの動画生成にはHeyGenのAPIクレジットが必要です</p>
                          <p className="text-amber-200/70 text-xs mt-1">現在クレジットが不足しています。以下からクレジットを追加してください。</p>
                          <a 
                            href="https://app.heygen.com/settings/billing" 
                            target="_blank" 
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 mt-2 px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 rounded-md text-amber-200 text-xs font-medium transition-colors"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
                            HeyGen クレジットを追加する
                          </a>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* ═══ Right Column: Avatar Selection ═══ */}
            <div className="space-y-6">
              {/* Avatar Selection */}
              <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
                <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                  <Mic className="w-5 h-5 text-emerald-400" />
                  ライバー選択
                </h2>

                {loadingAvatars ? (
                  <div className="space-y-3">
                    <div className="flex items-center gap-2 mb-2">
                      <Loader2 className="w-4 h-4 animate-spin text-emerald-400" />
                      <span className="text-gray-400 text-sm">ライバーを読み込み中...</span>
                    </div>
                    <div className="grid grid-cols-3 gap-2.5">
                      {[...Array(6)].map((_, i) => (
                        <div key={i} className="rounded-lg overflow-hidden border-2 border-gray-700">
                          <div className="w-full aspect-[9/16] bg-gray-800 animate-pulse" />
                        </div>
                      ))}
                    </div>
                  </div>
                ) : avatars.length === 0 ? (
                  <p className="text-gray-500 text-sm text-center py-4">利用可能なライバーがありません</p>
                ) : (
                  <>
                    {/* My Persons (我の人物) */}
                    {myPersons.length > 0 && (
                      <div className="mb-4">
                        <p className="text-xs text-cyan-400 font-medium mb-2 flex items-center gap-1">
                          <span className="w-2 h-2 rounded-full bg-cyan-400 inline-block"></span>
                          我の人物
                        </p>
                        <div className="grid grid-cols-3 gap-2.5 max-h-52 overflow-y-auto pr-1">
                          {myPersons.map((person) => (
                            <div key={person.id} className="relative group">
                              <button
                                onClick={() => handleSelectMyPerson(person)}
                                className={`relative rounded-lg overflow-hidden border-2 transition-all w-full ${
                                  selectedAvatarId === `myperson:${person.id}`
                                    ? "border-cyan-500 ring-2 ring-cyan-500/30 scale-[1.02]"
                                    : "border-gray-700 hover:border-gray-600 hover:scale-[1.01]"
                                }`}
                              >
                                <img
                                  src={person.image_url}
                                  alt={person.name}
                                  className="w-full aspect-[9/16] object-cover bg-gray-800"
                                  onError={(e) => { e.target.src = ''; e.target.className = 'w-full aspect-[9/16] bg-gray-800 flex items-center justify-center'; }}
                                />
                                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-1.5">
                                  <p className="text-[10px] font-medium truncate">{person.name}</p>
                                </div>
                                {selectedAvatarId === `myperson:${person.id}` && (
                                  <div className="absolute top-1.5 right-1.5 w-4 h-4 bg-cyan-500 rounded-full flex items-center justify-center">
                                    <CheckCircle className="w-2.5 h-2.5" />
                                  </div>
                                )}
                              </button>
                              <button
                                onClick={() => handleDeletePerson(person.id)}
                                className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-red-600 rounded-full items-center justify-center hover:bg-red-500 transition-colors hidden group-hover:flex z-10"
                                title="削除"
                              >
                                <Trash2 className="w-2.5 h-2.5" />
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* AitherHub Livers */}
                    {avatars.filter(a => a.source === 'aitherhub').length > 0 && (
                      <div className="mb-4">
                        <p className="text-xs text-emerald-400 font-medium mb-2 flex items-center gap-1">
                          <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block animate-pulse"></span>
                          AitherHub ライバー
                        </p>
                        <div className="grid grid-cols-3 gap-2.5 max-h-52 overflow-y-auto pr-1">
                          {avatars.filter(a => a.source === 'aitherhub').map((avatar) => (
                            <button key={avatar.avatar_id} onClick={() => { setSelectedAvatarId(avatar.avatar_id); setPersonImageUrl(""); }}
                              className={`relative rounded-lg overflow-hidden border-2 transition-all ${
                                selectedAvatarId === avatar.avatar_id
                                  ? "border-emerald-500 ring-2 ring-emerald-500/30 scale-[1.02]"
                                  : "border-gray-700 hover:border-gray-600 hover:scale-[1.01]"
                              }`}>
                              {/* Priority: thumbnail_image_url (JPG) > video with poster > fallback */}
                              {avatar.thumbnail_image_url ? (
                                <img 
                                  src={avatar.thumbnail_image_url} 
                                  alt={avatar.name} 
                                  className="w-full aspect-[9/16] object-cover bg-gray-800"
                                  onError={(e) => { e.target.style.display = 'none'; if(e.target.nextSibling) e.target.nextSibling.style.display = 'flex'; }}
                                />
                              ) : avatar.preview_image_url ? (
                                avatar.preview_image_url.includes('.mp4') || avatar.preview_image_url.includes('/clips/') ? (
                                  <video 
                                    src={`${avatar.preview_image_url}#t=0.5`}
                                    className="w-full aspect-[9/16] object-cover bg-gray-800" 
                                    preload="metadata"
                                    muted
                                    playsInline
                                    onLoadedData={(e) => { try { e.target.currentTime = 0.5; } catch {} }}
                                    onMouseEnter={(e) => { try { e.target.play(); } catch {} }}
                                    onMouseLeave={(e) => { try { e.target.pause(); e.target.currentTime = 0.5; } catch {} }}
                                    onError={(e) => { e.target.style.display = 'none'; if(e.target.nextSibling) e.target.nextSibling.style.display = 'flex'; }}
                                  />
                                ) : (
                                  <img src={avatar.preview_image_url} alt={avatar.name} className="w-full aspect-[9/16] object-cover" />
                                )
                              ) : null}
                              {/* Fallback: shown if img/video errors */}
                              <div className="w-full aspect-[9/16] bg-gray-800 items-center justify-center hidden">
                                <Mic className="w-6 h-6 text-gray-600" />
                              </div>
                              {!avatar.thumbnail_image_url && !avatar.preview_image_url && (
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

                    {/* Digital Twins */}
                    {avatars.filter(a => a.source !== 'aitherhub').length > 0 && (
                      <div>
                        <p className="text-xs text-gray-500 font-medium mb-2 flex items-center gap-1">
                          <span className="w-2 h-2 rounded-full bg-gray-500 inline-block"></span>
                          Digital Twin
                        </p>
                        <div className="grid grid-cols-3 gap-2.5 max-h-40 overflow-y-auto pr-1">
                          {avatars.filter(a => a.source !== 'aitherhub').map((avatar) => (
                            <button key={avatar.avatar_id} onClick={() => { setSelectedAvatarId(avatar.avatar_id); setPersonImageUrl(""); }}
                              className={`relative rounded-lg overflow-hidden border-2 transition-all ${
                                selectedAvatarId === avatar.avatar_id
                                  ? "border-emerald-500 ring-2 ring-emerald-500/30 scale-[1.02]"
                                  : "border-gray-700 hover:border-gray-600 hover:scale-[1.01]"
                              }`}>
                              {avatar.preview_image_url ? (
                                <img src={avatar.preview_image_url} alt={avatar.name} className="w-full aspect-square object-cover" />
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

                    {/* Custom Person Upload */}
                    <div className="mt-4 pt-4 border-t border-gray-700">
                      <p className="text-xs text-cyan-400 font-medium mb-2 flex items-center gap-1">
                        <Upload className="w-3 h-3" />
                        カスタム人物アップロード
                      </p>
                      <input
                        ref={customPersonInputRef}
                        type="file"
                        accept="image/*"
                        onChange={handleCustomPersonUpload}
                        className="hidden"
                      />
                      {customPersonPreview ? (
                        <div className="relative">
                          <div className={`relative rounded-lg overflow-hidden border-2 transition-all ${
                            selectedAvatarId === "custom_person"
                              ? "border-cyan-500 ring-2 ring-cyan-500/30"
                              : "border-gray-700"
                          }`}>
                            <img
                              src={customPersonPreview}
                              alt="カスタム人物"
                              className="w-full aspect-[9/16] object-cover bg-gray-800 max-w-[120px]"
                              onClick={() => setSelectedAvatarId("custom_person")}
                            />
                            {customPersonUploading && (
                              <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
                                <Loader2 className="w-6 h-6 animate-spin text-cyan-400" />
                              </div>
                            )}
                            {selectedAvatarId === "custom_person" && !customPersonUploading && (
                              <div className="absolute top-1.5 right-1.5 w-4 h-4 bg-cyan-500 rounded-full flex items-center justify-center">
                                <CheckCircle className="w-2.5 h-2.5" />
                              </div>
                            )}
                            <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-1.5">
                              <p className="text-[10px] font-medium truncate">カスタム</p>
                            </div>
                          </div>
                          <button
                            onClick={handleRemoveCustomPerson}
                            className="absolute -top-2 -right-2 w-5 h-5 bg-red-600 rounded-full flex items-center justify-center hover:bg-red-500 transition-colors"
                            title="削除"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => customPersonInputRef.current?.click()}
                          className="w-full py-3 px-4 rounded-lg border-2 border-dashed border-gray-600 hover:border-cyan-500/50 bg-gray-800/50 hover:bg-gray-800 transition-all flex items-center justify-center gap-2 text-gray-400 hover:text-cyan-400"
                        >
                          <Upload className="w-4 h-4" />
                          <span className="text-xs">人物写真をアップロード</span>
                        </button>
                      )}
                      <p className="text-[10px] text-gray-500 mt-1.5">※ 任意の人物写真をアップロードしてAI動画に使用できます</p>
                    </div>
                  </>
                )}
              </div>

              {/* How it works */}
              <div className="bg-gradient-to-br from-emerald-900/20 to-cyan-900/20 rounded-xl border border-emerald-500/20 p-6">
                <h3 className="text-sm font-semibold text-emerald-300 mb-3">使い方（3ステップ）</h3>
                <div className="space-y-2.5 text-xs text-gray-400">
                  <div className="flex items-start gap-2">
                    <span className="w-5 h-5 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-[10px] font-bold flex-shrink-0">1</span>
                    <span>商品写真をアップ or URLを貼る → AIが自動解析</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="w-5 h-5 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-[10px] font-bold flex-shrink-0">2</span>
                    <span>ライバー・声・言語を選択</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="w-5 h-5 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-[10px] font-bold flex-shrink-0">3</span>
                    <span>「動画を生成する」をクリック → 完成動画をダウンロード</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ═══════════ TAB: Person Analysis ═══════════ */}
        {activeTab === "person" && (
          <div className="max-w-2xl mx-auto space-y-6">
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
              <h2 className="text-lg font-semibold mb-2 flex items-center gap-2">
                <User className="w-5 h-5 text-purple-400" />
                人物写真分析
              </h2>
              <p className="text-sm text-gray-400 mb-4">
                人物写真をアップロードすると、AIが外見・表情・スタイルを分析し、最適な動画生成パラメータを提案します。
              </p>

              {/* Person Photo Upload */}
              <input ref={personFileInputRef} type="file" accept="image/*" onChange={handlePersonImageSelect} className="hidden" />
              
              {personImageFile ? (
                <div className="relative mb-4">
                  <img src={personImageFile.preview} alt="Person" className="w-full max-h-80 object-contain rounded-lg border border-gray-700 bg-gray-800" />
                  <button
                    onClick={() => { setPersonImageFile(null); setPersonAnalysis(null); }}
                    className="absolute top-2 right-2 p-1.5 bg-red-500/80 hover:bg-red-500 rounded-full text-white text-xs"
                  >✕</button>
                </div>
              ) : (
                <div className="mb-4">
                  <button
                    onClick={() => personFileInputRef.current?.click()}
                    className="w-full py-12 border-2 border-dashed border-gray-700 hover:border-purple-500/50 rounded-xl flex flex-col items-center gap-3 transition group"
                  >
                    <div className="w-14 h-14 rounded-full bg-gray-800 group-hover:bg-purple-500/10 flex items-center justify-center transition">
                      <User className="w-7 h-7 text-gray-500 group-hover:text-purple-400 transition" />
                    </div>
                    <div className="text-center">
                      <p className="text-sm text-gray-300">クリックして人物写真を選択</p>
                      <p className="text-xs text-gray-500 mt-1">JPG, PNG, WebP（10MBまで）</p>
                    </div>
                  </button>

                  <div className="mt-3">
                    <label className="text-xs text-gray-400 mb-1 block">または画像URLを入力:</label>
                    <input
                      type="text" value={personImageUrl} onChange={(e) => setPersonImageUrl(e.target.value)}
                      placeholder="https://example.com/person.jpg"
                      className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:border-purple-500 outline-none"
                    />
                  </div>
                </div>
              )}

              {/* Analyze Button */}
              <button
                onClick={handleAnalyzePerson}
                disabled={isAnalyzingPerson || (!personImageFile && !personImageUrl)}
                className={`w-full py-3 rounded-lg font-medium text-sm flex items-center justify-center gap-2 transition ${
                  isAnalyzingPerson || (!personImageFile && !personImageUrl) ? "bg-gray-700 text-gray-400 cursor-not-allowed" : "bg-purple-600 hover:bg-purple-500 text-white"
                }`}
              >
                {isAnalyzingPerson ? (<><Loader2 className="w-4 h-4 animate-spin" />AIが人物を分析中...</>) : (<><Eye className="w-4 h-4" />人物を分析する</>)}
              </button>

              {/* Analysis Result */}
              {personAnalysis && (
                <div className="mt-4 bg-purple-500/5 border border-purple-500/20 rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <CheckCircle className="w-4 h-4 text-purple-400" />
                    <span className="text-sm font-medium text-purple-300">分析結果</span>
                  </div>
                  <div className="space-y-3 text-sm">
                    {personAnalysis.appearance && (
                      <div>
                        <span className="text-xs text-gray-500">外見特徴</span>
                        <p className="text-gray-200">{personAnalysis.appearance}</p>
                      </div>
                    )}
                    {personAnalysis.expression && (
                      <div>
                        <span className="text-xs text-gray-500">表情・雰囲気</span>
                        <p className="text-gray-200">{personAnalysis.expression}</p>
                      </div>
                    )}
                    {personAnalysis.style && (
                      <div>
                        <span className="text-xs text-gray-500">スタイル</span>
                        <p className="text-gray-200">{personAnalysis.style}</p>
                      </div>
                    )}
                    {personAnalysis.age_range && (
                      <div>
                        <span className="text-xs text-gray-500">年齢層</span>
                        <p className="text-gray-200">{personAnalysis.age_range}</p>
                      </div>
                    )}
                    {personAnalysis.suggestions && (
                      <div className="mt-3 pt-3 border-t border-gray-700">
                        <span className="text-xs text-purple-400 font-medium">AIからの提案</span>
                        <div className="mt-1 space-y-1">
                          {personAnalysis.suggestions.tone && (
                            <p className="text-gray-300 text-xs">推奨トーン: <span className="text-purple-300">{personAnalysis.suggestions.tone}</span></p>
                          )}
                          {personAnalysis.suggestions.product_categories && (
                            <p className="text-gray-300 text-xs">適合商品: <span className="text-purple-300">{personAnalysis.suggestions.product_categories.join(", ")}</span></p>
                          )}
                          {personAnalysis.suggestions.script_style && (
                            <p className="text-gray-300 text-xs">台本スタイル: <span className="text-purple-300">{personAnalysis.suggestions.script_style}</span></p>
                          )}
                        </div>
                      </div>
                    )}
                    {personAnalysis.raw_analysis && (
                      <div>
                        <span className="text-xs text-gray-500">分析結果</span>
                        <p className="text-gray-200 whitespace-pre-wrap">{personAnalysis.raw_analysis}</p>
                      </div>
                    )}
                  </div>
                  {/* Auto-linked to video generation notice */}
                  {selectedAvatarId === "custom_person" && personImageUrl && (
                    <div className="mt-4 bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <CheckCircle className="w-4 h-4 text-emerald-400" />
                        <span className="text-sm text-emerald-300">この人物が動画生成のライバーとして設定されました</span>
                      </div>
                      <button
                        onClick={() => setActiveTab("generate")}
                        className="text-xs bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1 rounded-md transition-colors"
                      >
                        動画生成へ →
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ═══════════ TAB: History ═══════════ */}
        {activeTab === "history" && (
          <div className="max-w-4xl mx-auto">
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold flex items-center gap-2">
                  <RefreshCw className="w-5 h-5 text-blue-400" />
                  生成履歴
                </h2>
                <button onClick={loadJobHistory} disabled={historyLoading}
                  className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs text-gray-300 flex items-center gap-1 transition">
                  <RefreshCw className={`w-3 h-3 ${historyLoading ? "animate-spin" : ""}`} />
                  更新
                </button>
              </div>

              {historyLoading && jobHistory.length === 0 ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                </div>
              ) : jobHistory.length === 0 ? (
                <p className="text-gray-500 text-sm text-center py-12">まだ生成履歴がありません</p>
              ) : (
                <div className="space-y-3">
                  {jobHistory.map((job) => (
                    <div key={job.job_id} className="bg-gray-800 rounded-lg p-4 border border-gray-700 hover:border-gray-600 transition">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-3">
                          <p className="text-sm font-medium">{job.product_name || "無題"}</p>
                          <span className={`text-xs px-2 py-0.5 rounded-full ${
                            job.status === "completed" ? "bg-green-500/20 text-green-300" :
                            job.status === "failed" ? "bg-red-500/20 text-red-300" :
                            "bg-yellow-500/20 text-yellow-300"
                          }`}>
                            {job.status === "completed" ? "完成" : job.status === "failed" ? "失敗" : "処理中"}
                          </span>
                        </div>
                        <div className="flex items-center gap-2">
                          {job.language && (
                            <span className="text-xs text-gray-500">{job.language.toUpperCase()}</span>
                          )}
                          <button onClick={() => handleDeleteJob(job.job_id)}
                            className="p-1 hover:bg-red-500/20 rounded text-gray-500 hover:text-red-400 transition">
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>

                      <div className="flex items-center gap-4 text-xs text-gray-500">
                        {job.created_at && (
                          <span>{new Date(job.created_at).toLocaleString("ja-JP")}</span>
                        )}
                        {job.video_duration_sec && (
                          <span>{Math.round(job.video_duration_sec)}秒</span>
                        )}
                      </div>

                      {job.video_url && (
                        <div className="mt-3 flex items-center gap-3">
                          <a href={job.video_url} target="_blank" rel="noopener noreferrer"
                            className="text-xs text-emerald-400 hover:text-emerald-300 flex items-center gap-1">
                            <Play className="w-3 h-3" />動画を再生
                          </a>
                          <a href={job.video_url} target="_blank" rel="noopener noreferrer"
                            className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1">
                            <Download className="w-3 h-3" />ダウンロード
                          </a>
                          <button onClick={() => navigator.clipboard.writeText(job.video_url)}
                            className="text-xs text-gray-400 hover:text-gray-300 flex items-center gap-1">
                            <Copy className="w-3 h-3" />URLコピー
                          </button>
                        </div>
                      )}

                      {job.error && (
                        <p className="mt-2 text-xs text-red-400">エラー: {job.error}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </main>

      {/* Save Person Dialog */}
      {showSavePersonDialog && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 max-w-sm w-full shadow-2xl">
            <h3 className="text-lg font-semibold text-white mb-2 flex items-center gap-2">
              <UserPlus className="w-5 h-5 text-cyan-400" />
              人物を保存
            </h3>
            <p className="text-sm text-gray-400 mb-4">この人物を「我の人物」に保存して、次回から簡単に選択できます。</p>
            {customPersonPreview && (
              <div className="mb-4 flex justify-center">
                <img src={customPersonPreview} alt="人物" className="w-20 h-20 object-cover rounded-lg border border-gray-600" />
              </div>
            )}
            <input
              type="text"
              value={savePersonName}
              onChange={(e) => setSavePersonName(e.target.value)}
              placeholder="人物の名前を入力（例：田中さん）"
              className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded-lg text-white text-sm focus:outline-none focus:border-cyan-500 mb-4"
              autoFocus
            />
            <div className="flex gap-3">
              <button
                onClick={() => { setShowSavePersonDialog(false); setSavePersonName(""); }}
                className="flex-1 py-2 px-4 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm transition-colors"
              >
                スキップ
              </button>
              <button
                onClick={() => handleSavePerson(savePersonName || "カスタム人物", personImageUrl, personAnalysis)}
                className="flex-1 py-2 px-4 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium transition-colors"
              >
                保存する
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
