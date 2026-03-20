import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  Upload,
  Download,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  Clock,
  ArrowLeft,
  Loader2,
  ImageIcon,
  Mic,
  Settings,
  Sparkles,
  X,
  Volume2,
  Sliders,
  Type,
  FileAudio,
  ChevronDown,
  Zap,
  Crown,
} from "lucide-react";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";
import LiveStreamPanel from "./LiveStreamPanel";

/**
 * AI Live Creator Page
 *
 * Engine modes:
 *   Standard (MuseTalk): Lip-sync only — fast and stable
 *   Premium (IMTalker):  Full facial animation — head movement, expressions, blinks
 *
 * Input modes:
 *   Text Mode: Text → ElevenLabs TTS → Engine → Video
 *   Audio Mode: Upload audio → Engine → Video
 */
export default function AiLiveCreatorPage() {
  const navigate = useNavigate();

  // ── Engine Mode ──
  const [engine, setEngine] = useState("imtalker"); // "musetalk" | "imtalker"

  // ── Input Mode ──
  const [inputMode, setInputMode] = useState("text"); // "text" | "audio"

  // ── Portrait ──
  const [portraitFile, setPortraitFile] = useState(null);
  const [portraitPreview, setPortraitPreview] = useState(null);
  const [portraitUrl, setPortraitUrl] = useState("");
  const [portraitUploadProgress, setPortraitUploadProgress] = useState(0);
  const [isUploadingPortrait, setIsUploadingPortrait] = useState(false);

  // ── Text Mode ──
  const [scriptText, setScriptText] = useState("");
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [voices, setVoices] = useState([]);
  const [loadingVoices, setLoadingVoices] = useState(false);
  const [languageCode, setLanguageCode] = useState("ja");

  // ── Audio Mode ──
  const [audioFile, setAudioFile] = useState(null);
  const [audioName, setAudioName] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [audioUploadProgress, setAudioUploadProgress] = useState(0);
  const [isUploadingAudio, setIsUploadingAudio] = useState(false);

  // ── Advanced Settings (shared) ──
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [outputFps, setOutputFps] = useState(25);
  // MuseTalk-specific
  const [bboxShift, setBboxShift] = useState(0);
  const [extraMargin, setExtraMargin] = useState(10);
  const [batchSize, setBatchSize] = useState(16);
  // IMTalker-specific
  const [aCfgScale, setACfgScale] = useState(2.0);
  const [nfe, setNfe] = useState(10);
  const [crop, setCrop] = useState(true);

  // ── Job State ──
  const [currentJobId, setCurrentJobId] = useState(null);
  const [currentEngine, setCurrentEngine] = useState(null); // engine used for current job
  const [jobStatus, setJobStatus] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [ttsInfo, setTtsInfo] = useState(null);

  // ── Health ──
  const [health, setHealth] = useState(null);

  // ── Job History ──
  const [jobHistory, setJobHistory] = useState([]);

  // ── Live Session ──
  const [liveSessionId, setLiveSessionId] = useState(null);

  // ── Refs ──
  const portraitInputRef = useRef(null);
  const audioInputRef = useRef(null);
  const pollRef = useRef(null);

  // ── Load on mount ──
  useEffect(() => {
    checkHealth();
    loadVoices();
    try {
      const saved = localStorage.getItem("aiLiveCreator_jobs");
      if (saved) setJobHistory(JSON.parse(saved));
    } catch {}
  }, []);

  // ── Poll job status ──
  useEffect(() => {
    if (!currentJobId || !currentEngine) return;

    const poll = async () => {
      try {
        const status = await aiLiveCreatorService.getStatus(currentJobId, currentEngine);
        setJobStatus(status);
        if (["completed", "error", "failed"].includes(status.status)) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          updateJobHistory(currentJobId, status);
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
  }, [currentJobId, currentEngine]);

  // ── Helpers ──
  const checkHealth = async () => {
    try {
      const h = await aiLiveCreatorService.healthCheck();
      setHealth(h);
    } catch {
      setHealth({ status: "error", error: "Cannot reach API" });
    }
  };

  const loadVoices = async () => {
    setLoadingVoices(true);
    try {
      const res = await aiLiveCreatorService.listVoices();
      if (res.success && res.voices) {
        setVoices(res.voices);
        const cloned = res.voices.find((v) => v.is_cloned);
        if (cloned) setSelectedVoiceId(cloned.voice_id);
        else if (res.voices.length > 0) setSelectedVoiceId(res.voices[0].voice_id);
      }
    } catch (err) {
      console.error("Failed to load voices:", err);
    } finally {
      setLoadingVoices(false);
    }
  };

  const updateJobHistory = (jobId, status) => {
    setJobHistory((prev) => {
      const updated = prev.map((j) =>
        j.job_id === jobId ? { ...j, ...status } : j
      );
      localStorage.setItem("aiLiveCreator_jobs", JSON.stringify(updated));
      return updated;
    });
  };

  // ── Portrait Upload ──
  const handlePortraitSelect = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("画像ファイルを選択してください (JPEG, PNG)");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      setError("画像は20MB以下にしてください");
      return;
    }

    setPortraitFile(file);
    setPortraitPreview(URL.createObjectURL(file));
    setError(null);

    setIsUploadingPortrait(true);
    setPortraitUploadProgress(0);
    try {
      const url = await aiLiveCreatorService.uploadFile(file, "portrait", setPortraitUploadProgress);
      setPortraitUrl(url);
    } catch (err) {
      setError(`Portrait upload failed: ${err.message}`);
      setPortraitFile(null);
      setPortraitPreview(null);
    } finally {
      setIsUploadingPortrait(false);
    }
  };

  // ── Audio Upload ──
  const handleAudioSelect = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const validExts = /\.(wav|mp3|m4a|aac)$/i;
    if (!file.type.startsWith("audio/") && !file.name.match(validExts)) {
      setError("音声ファイルを選択してください (WAV, MP3, M4A)");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setError("音声は50MB以下にしてください");
      return;
    }

    setAudioFile(file);
    setAudioName(file.name);
    setError(null);

    setIsUploadingAudio(true);
    setAudioUploadProgress(0);
    try {
      const url = await aiLiveCreatorService.uploadFile(file, "audio", setAudioUploadProgress);
      setAudioUrl(url);
    } catch (err) {
      setError(`Audio upload failed: ${err.message}`);
      setAudioFile(null);
      setAudioName("");
    } finally {
      setIsUploadingAudio(false);
    }
  };

  // ── Generate (Text Mode) ──
  const handleGenerateFromText = async () => {
    if (!portraitUrl) { setError("肖像画をアップロードしてください"); return; }
    if (!scriptText.trim()) { setError("テキストを入力してください"); return; }

    setIsSubmitting(true);
    setError(null);
    setJobStatus(null);
    setTtsInfo(null);

    try {
      let result;
      if (engine === "imtalker") {
        result = await aiLiveCreatorService.generatePremiumFromText({
          portrait_url: portraitUrl,
          text: scriptText.trim(),
          voice_id: selectedVoiceId || undefined,
          language_code: languageCode,
          a_cfg_scale: aCfgScale,
          nfe: nfe,
          crop: crop,
          output_fps: outputFps,
        });
      } else {
        result = await aiLiveCreatorService.generateFromText({
          portrait_url: portraitUrl,
          text: scriptText.trim(),
          voice_id: selectedVoiceId || undefined,
          language_code: languageCode,
          bbox_shift: bboxShift,
          extra_margin: extraMargin,
          batch_size: batchSize,
          output_fps: outputFps,
        });
      }

      if (!result.success) {
        setError(result.error || "Generation failed");
        return;
      }

      setCurrentJobId(result.job_id);
      setCurrentEngine(engine);
      setJobStatus({ status: result.status || "queued", progress: 0 });
      if (result.tts_duration_ms) {
        setTtsInfo({ duration_ms: result.tts_duration_ms, audio_url: result.audio_url });
      }

      const newJob = {
        job_id: result.job_id,
        status: "queued",
        progress: 0,
        created_at: new Date().toISOString(),
        mode: "text",
        engine: engine,
        text_preview: scriptText.substring(0, 50),
      };
      setJobHistory((prev) => {
        const updated = [newJob, ...prev].slice(0, 20);
        localStorage.setItem("aiLiveCreator_jobs", JSON.stringify(updated));
        return updated;
      });
    } catch (err) {
      setError(err.response?.data?.error || err.response?.data?.detail || err.message || "Generation failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  // ── Generate (Audio Mode) ──
  const handleGenerateFromAudio = async () => {
    if (!portraitUrl || !audioUrl) {
      setError("肖像画と音声ファイルをアップロードしてください");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    setJobStatus(null);

    try {
      let result;
      if (engine === "imtalker") {
        result = await aiLiveCreatorService.generatePremium({
          portrait_url: portraitUrl,
          audio_url: audioUrl,
          a_cfg_scale: aCfgScale,
          nfe: nfe,
          crop: crop,
          output_fps: outputFps,
        });
      } else {
        result = await aiLiveCreatorService.generate({
          portrait_url: portraitUrl,
          audio_url: audioUrl,
          bbox_shift: bboxShift,
          extra_margin: extraMargin,
          batch_size: batchSize,
          output_fps: outputFps,
        });
      }

      if (!result.success) {
        setError(result.error || "Generation failed");
        return;
      }

      setCurrentJobId(result.job_id);
      setCurrentEngine(engine);
      setJobStatus({ status: result.status || "queued", progress: 0 });

      const newJob = {
        job_id: result.job_id,
        status: "queued",
        progress: 0,
        created_at: new Date().toISOString(),
        mode: "audio",
        engine: engine,
      };
      setJobHistory((prev) => {
        const updated = [newJob, ...prev].slice(0, 20);
        localStorage.setItem("aiLiveCreator_jobs", JSON.stringify(updated));
        return updated;
      });
    } catch (err) {
      setError(err.response?.data?.error || err.response?.data?.detail || err.message || "Generation failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleGenerate = () => {
    if (inputMode === "text") handleGenerateFromText();
    else handleGenerateFromAudio();
  };

  // ── Download ──
  const handleDownload = async (jobId, eng) => {
    try {
      const downloadEngine = eng || currentEngine || "musetalk";
      const blob = await aiLiveCreatorService.downloadVideo(jobId || currentJobId, downloadEngine);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `ai-live-creator-${jobId || currentJobId}.mp4`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(`Download failed: ${err.message}`);
    }
  };

  // ── Reset ──
  const handleReset = () => {
    setPortraitFile(null);
    setPortraitPreview(null);
    setPortraitUrl("");
    setScriptText("");
    setAudioFile(null);
    setAudioName("");
    setAudioUrl("");
    setCurrentJobId(null);
    setCurrentEngine(null);
    setJobStatus(null);
    setTtsInfo(null);
    setError(null);
    setIsSubmitting(false);
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  // ── Status helpers ──
  const getStatusColor = (s) => {
    const map = { completed: "text-green-600", processing: "text-blue-600", queued: "text-yellow-600", error: "text-red-600", failed: "text-red-600" };
    return map[s] || "text-gray-600";
  };
  const getStatusIcon = (s) => {
    const map = {
      completed: <CheckCircle className="w-5 h-5 text-green-600" />,
      processing: <Loader2 className="w-5 h-5 text-blue-600 animate-spin" />,
      queued: <Clock className="w-5 h-5 text-yellow-600" />,
      error: <AlertCircle className="w-5 h-5 text-red-600" />,
      failed: <AlertCircle className="w-5 h-5 text-red-600" />,
    };
    return map[s] || <Clock className="w-5 h-5 text-gray-400" />;
  };
  const getStatusLabel = (s) => {
    const map = { completed: "Complete", processing: "Generating...", queued: "Queued", error: "Error", failed: "Error", tts_generating: "Generating voice..." };
    return map[s] || s || "Unknown";
  };

  const isReadyText = portraitUrl && scriptText.trim() && !isUploadingPortrait;
  const isReadyAudio = portraitUrl && audioUrl && !isUploadingPortrait && !isUploadingAudio;
  const isReady = inputMode === "text" ? isReadyText : isReadyAudio;
  const isProcessing = jobStatus && ["queued", "processing", "tts_generating"].includes(jobStatus.status);

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={() => navigate("/")} className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
              <ArrowLeft className="w-5 h-5 text-gray-600" />
            </button>
            <div>
              <h1 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-purple-600" />
                AI Live Creator
              </h1>
              <p className="text-xs text-gray-500">Portrait + Text/Audio → AI Animated Video</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${health?.status === "ok" ? "bg-green-500" : health?.status === "not_configured" ? "bg-yellow-500" : "bg-red-500"}`} />
            <span className="text-xs text-gray-500">{health?.status === "ok" ? "GPU Ready" : health?.status === "not_configured" ? "GPU Not Configured" : "GPU Offline"}</span>
            <button onClick={checkHealth} className="p-1 hover:bg-gray-100 rounded transition-colors" title="Refresh">
              <RefreshCw className="w-3 h-3 text-gray-400" />
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
        {/* Error Banner */}
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2">
            <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 shrink-0" />
            <p className="text-sm text-red-700 flex-1">{error}</p>
            <button onClick={() => setError(null)}><X className="w-4 h-4 text-red-400" /></button>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* ── Left Column: Inputs ── */}
          <div className="lg:col-span-2 space-y-4">

            {/* Engine Selector */}
            <div className="bg-white rounded-xl border border-gray-200 p-4">
              <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Animation Engine</h2>
              <div className="grid grid-cols-2 gap-3">
                <button
                  onClick={() => setEngine("musetalk")}
                  className={`relative p-4 rounded-xl border-2 transition-all text-left ${
                    engine === "musetalk"
                      ? "border-blue-500 bg-blue-50/50 shadow-sm"
                      : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-2">
                    <Zap className={`w-5 h-5 ${engine === "musetalk" ? "text-blue-600" : "text-gray-400"}`} />
                    <span className={`text-sm font-bold ${engine === "musetalk" ? "text-blue-700" : "text-gray-700"}`}>Standard</span>
                  </div>
                  <p className="text-[11px] text-gray-500 leading-relaxed">リップシンクのみ。高速・安定。口元だけが動きます。</p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    <span className="text-[9px] bg-blue-100 text-blue-600 px-1.5 py-0.5 rounded-full">Lip-sync</span>
                    <span className="text-[9px] bg-green-100 text-green-600 px-1.5 py-0.5 rounded-full">Fast</span>
                  </div>
                  {engine === "musetalk" && (
                    <div className="absolute top-2 right-2">
                      <CheckCircle className="w-5 h-5 text-blue-500" />
                    </div>
                  )}
                </button>

                <button
                  onClick={() => setEngine("imtalker")}
                  className={`relative p-4 rounded-xl border-2 transition-all text-left ${
                    engine === "imtalker"
                      ? "border-purple-500 bg-purple-50/50 shadow-sm"
                      : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-2">
                    <Crown className={`w-5 h-5 ${engine === "imtalker" ? "text-purple-600" : "text-gray-400"}`} />
                    <span className={`text-sm font-bold ${engine === "imtalker" ? "text-purple-700" : "text-gray-700"}`}>Premium</span>
                    <span className="text-[9px] bg-gradient-to-r from-purple-500 to-pink-500 text-white px-1.5 py-0.5 rounded-full font-medium">NEW</span>
                  </div>
                  <p className="text-[11px] text-gray-500 leading-relaxed">フル表情アニメーション。頭の動き・まばたき・表情変化。</p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    <span className="text-[9px] bg-purple-100 text-purple-600 px-1.5 py-0.5 rounded-full">Head motion</span>
                    <span className="text-[9px] bg-purple-100 text-purple-600 px-1.5 py-0.5 rounded-full">Expressions</span>
                    <span className="text-[9px] bg-purple-100 text-purple-600 px-1.5 py-0.5 rounded-full">Blinks</span>
                    <span className="text-[9px] bg-purple-100 text-purple-600 px-1.5 py-0.5 rounded-full">Lip-sync</span>
                  </div>
                  {engine === "imtalker" && (
                    <div className="absolute top-2 right-2">
                      <CheckCircle className="w-5 h-5 text-purple-500" />
                    </div>
                  )}
                </button>
              </div>
            </div>

            {/* Portrait Upload */}
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <h2 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                <ImageIcon className="w-4 h-4 text-purple-600" />
                Portrait Image
              </h2>
              <p className="text-xs text-gray-500 mb-3">
                正面を向いた写真をアップロードしてください。AIがこの顔を音声に合わせてアニメーションします。
              </p>

              {portraitPreview ? (
                <div className="relative">
                  <img src={portraitPreview} alt="Portrait" className="w-full max-h-64 object-contain rounded-lg bg-gray-50" />
                  {isUploadingPortrait && (
                    <div className="absolute inset-0 bg-black/30 rounded-lg flex items-center justify-center">
                      <div className="text-center">
                        <Loader2 className="w-8 h-8 text-white animate-spin mx-auto mb-2" />
                        <p className="text-white text-sm font-medium">Uploading... {portraitUploadProgress}%</p>
                      </div>
                    </div>
                  )}
                  {!isUploadingPortrait && portraitUrl && (
                    <div className="absolute top-2 right-2 bg-green-500 text-white px-2 py-1 rounded-full text-xs flex items-center gap-1">
                      <CheckCircle className="w-3 h-3" /> Uploaded
                    </div>
                  )}
                  <button
                    onClick={() => { setPortraitFile(null); setPortraitPreview(null); setPortraitUrl(""); }}
                    className="absolute top-2 left-2 bg-white/80 hover:bg-white p-1.5 rounded-full transition-colors"
                  >
                    <X className="w-4 h-4 text-gray-600" />
                  </button>
                </div>
              ) : (
                <div
                  onClick={() => portraitInputRef.current?.click()}
                  className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center cursor-pointer hover:border-purple-400 hover:bg-purple-50/30 transition-all"
                >
                  <ImageIcon className="w-10 h-10 text-gray-300 mx-auto mb-2" />
                  <p className="text-sm text-gray-500">クリックして肖像画をアップロード</p>
                  <p className="text-xs text-gray-400 mt-1">JPEG, PNG (max 20MB)</p>
                </div>
              )}
              <input ref={portraitInputRef} type="file" accept="image/jpeg,image/png,image/jpg" onChange={handlePortraitSelect} className="hidden" />
            </div>

            {/* Input Mode Tabs */}
            <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <div className="flex border-b border-gray-200">
                <button
                  onClick={() => setInputMode("text")}
                  className={`flex-1 py-3 px-4 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${
                    inputMode === "text"
                      ? "text-purple-700 bg-purple-50 border-b-2 border-purple-600"
                      : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                  }`}
                >
                  <Type className="w-4 h-4" />
                  テキスト入力
                  <span className="text-[10px] bg-purple-100 text-purple-600 px-1.5 py-0.5 rounded-full">AI音声</span>
                </button>
                <button
                  onClick={() => setInputMode("audio")}
                  className={`flex-1 py-3 px-4 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${
                    inputMode === "audio"
                      ? "text-purple-700 bg-purple-50 border-b-2 border-purple-600"
                      : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                  }`}
                >
                  <FileAudio className="w-4 h-4" />
                  音声アップロード
                </button>
              </div>

              <div className="p-5">
                {inputMode === "text" ? (
                  <div className="space-y-4">
                    <div>
                      <label className="text-xs font-medium text-gray-600 block mb-1.5">台本テキスト</label>
                      <textarea
                        value={scriptText}
                        onChange={(e) => setScriptText(e.target.value)}
                        placeholder="ここにテキストを入力してください。AIが自動的に音声を生成し、肖像画がこのテキストを話す動画を作成します。&#10;&#10;例: こんにちは、皆さん！今日は新商品をご紹介します。"
                        rows={6}
                        maxLength={5000}
                        className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none resize-none"
                      />
                      <div className="flex justify-between mt-1">
                        <p className="text-[10px] text-gray-400">ElevenLabs AIが自動的に音声を生成します</p>
                        <p className="text-[10px] text-gray-400">{scriptText.length}/5000</p>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="text-xs font-medium text-gray-600 block mb-1.5">音声 (Voice)</label>
                        {loadingVoices ? (
                          <div className="flex items-center gap-2 text-xs text-gray-400 py-2">
                            <Loader2 className="w-3 h-3 animate-spin" /> Loading voices...
                          </div>
                        ) : (
                          <select
                            value={selectedVoiceId}
                            onChange={(e) => setSelectedVoiceId(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none bg-white"
                          >
                            <option value="">Default Voice</option>
                            {voices.map((v) => (
                              <option key={v.voice_id} value={v.voice_id}>
                                {v.name} {v.is_cloned ? "(Cloned)" : `(${v.category})`}
                              </option>
                            ))}
                          </select>
                        )}
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-600 block mb-1.5">言語 (Language)</label>
                        <select
                          value={languageCode}
                          onChange={(e) => setLanguageCode(e.target.value)}
                          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none bg-white"
                        >
                          <option value="ja">日本語</option>
                          <option value="en">English</option>
                          <option value="zh">中文</option>
                          <option value="ko">한국어</option>
                        </select>
                      </div>
                    </div>

                    {ttsInfo && (
                      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs">
                        <p className="text-blue-700 font-medium">AI音声生成完了</p>
                        <p className="text-blue-600 mt-1">音声長: {(ttsInfo.duration_ms / 1000).toFixed(1)}秒</p>
                      </div>
                    )}
                  </div>
                ) : (
                  <div>
                    <p className="text-xs text-gray-500 mb-3">
                      肖像画がリップシンクする音声ファイルをアップロードしてください。WAV形式推奨。
                    </p>
                    {audioFile ? (
                      <div className="relative bg-gray-50 rounded-lg p-4">
                        <div className="flex items-center gap-3">
                          <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
                            <Volume2 className="w-5 h-5 text-purple-600" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-gray-800 truncate">{audioName}</p>
                            <p className="text-xs text-gray-500">{(audioFile.size / 1024 / 1024).toFixed(1)} MB</p>
                          </div>
                          {isUploadingAudio ? (
                            <div className="flex items-center gap-2">
                              <Loader2 className="w-4 h-4 text-purple-600 animate-spin" />
                              <span className="text-xs text-purple-600">{audioUploadProgress}%</span>
                            </div>
                          ) : audioUrl ? (
                            <CheckCircle className="w-5 h-5 text-green-500" />
                          ) : null}
                        </div>
                        <button
                          onClick={() => { setAudioFile(null); setAudioName(""); setAudioUrl(""); }}
                          className="absolute top-2 right-2 p-1 hover:bg-gray-200 rounded-full transition-colors"
                        >
                          <X className="w-4 h-4 text-gray-400" />
                        </button>
                      </div>
                    ) : (
                      <div
                        onClick={() => audioInputRef.current?.click()}
                        className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center cursor-pointer hover:border-purple-400 hover:bg-purple-50/30 transition-all"
                      >
                        <Mic className="w-10 h-10 text-gray-300 mx-auto mb-2" />
                        <p className="text-sm text-gray-500">クリックして音声ファイルをアップロード</p>
                        <p className="text-xs text-gray-400 mt-1">WAV, MP3, M4A (max 50MB)</p>
                      </div>
                    )}
                    <input ref={audioInputRef} type="file" accept="audio/wav,audio/mpeg,audio/mp3,.wav,.mp3,.m4a" onChange={handleAudioSelect} className="hidden" />
                  </div>
                )}
              </div>
            </div>

            {/* Advanced Settings */}
            <div className="bg-white rounded-xl border border-gray-200">
              <button
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="w-full p-4 flex items-center justify-between text-sm font-medium text-gray-700 hover:bg-gray-50 rounded-xl transition-colors"
              >
                <span className="flex items-center gap-2">
                  <Sliders className="w-4 h-4 text-gray-500" />
                  Advanced Settings
                  <span className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded-full">
                    {engine === "imtalker" ? "IMTalker" : "MuseTalk"}
                  </span>
                </span>
                <span className="text-gray-400 text-xs">{showAdvanced ? "Hide" : "Show"}</span>
              </button>
              {showAdvanced && (
                <div className="px-5 pb-5 space-y-4 border-t border-gray-100 pt-4">
                  {engine === "imtalker" ? (
                    /* IMTalker Settings */
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="text-xs font-medium text-gray-600 block mb-1">Audio CFG Scale</label>
                        <input type="number" value={aCfgScale} onChange={(e) => setACfgScale(Number(e.target.value))} min={0.5} max={5.0} step={0.1}
                          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none" />
                        <p className="text-[10px] text-gray-400 mt-1">表現力の強さ (0.5-5.0, 推奨: 2.0)</p>
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-600 block mb-1">NFE Steps</label>
                        <input type="number" value={nfe} onChange={(e) => setNfe(Number(e.target.value))} min={5} max={30}
                          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none" />
                        <p className="text-[10px] text-gray-400 mt-1">品質ステップ数 (5-30, 推奨: 10)</p>
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-600 block mb-1">Output FPS</label>
                        <input type="number" value={outputFps} onChange={(e) => setOutputFps(Number(e.target.value))} min={15} max={60}
                          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none" />
                        <p className="text-[10px] text-gray-400 mt-1">動画フレームレート (15-60)</p>
                      </div>
                      <div className="flex items-center gap-3 pt-4">
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input type="checkbox" checked={crop} onChange={(e) => setCrop(e.target.checked)} className="sr-only peer" />
                          <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-purple-600"></div>
                        </label>
                        <div>
                          <p className="text-xs font-medium text-gray-600">Auto Crop</p>
                          <p className="text-[10px] text-gray-400">顔領域を自動クロップ</p>
                        </div>
                      </div>
                    </div>
                  ) : (
                    /* MuseTalk Settings */
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="text-xs font-medium text-gray-600 block mb-1">Bbox Shift</label>
                        <input type="number" value={bboxShift} onChange={(e) => setBboxShift(Number(e.target.value))} min={-50} max={50}
                          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none" />
                        <p className="text-[10px] text-gray-400 mt-1">顔検出の垂直シフト (-50 to 50)</p>
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-600 block mb-1">Extra Margin</label>
                        <input type="number" value={extraMargin} onChange={(e) => setExtraMargin(Number(e.target.value))} min={0} max={50}
                          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none" />
                        <p className="text-[10px] text-gray-400 mt-1">顔の下の余白 (0-50)</p>
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-600 block mb-1">Batch Size</label>
                        <input type="number" value={batchSize} onChange={(e) => setBatchSize(Number(e.target.value))} min={1} max={64}
                          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none" />
                        <p className="text-[10px] text-gray-400 mt-1">大きい = 速い、VRAM多い (1-64)</p>
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-600 block mb-1">Output FPS</label>
                        <input type="number" value={outputFps} onChange={(e) => setOutputFps(Number(e.target.value))} min={15} max={60}
                          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none" />
                        <p className="text-[10px] text-gray-400 mt-1">動画フレームレート (15-60)</p>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* ── Right Column: Generate & Status ── */}
          <div className="space-y-4">
            {/* Generate Button */}
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <button
                onClick={handleGenerate}
                disabled={!isReady || isSubmitting || isProcessing}
                className={`w-full py-3 px-4 rounded-lg font-medium text-sm flex items-center justify-center gap-2 transition-all ${
                  isReady && !isSubmitting && !isProcessing
                    ? engine === "imtalker"
                      ? "bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-700 hover:to-pink-700 text-white shadow-md hover:shadow-lg"
                      : "bg-blue-600 hover:bg-blue-700 text-white shadow-md hover:shadow-lg"
                    : "bg-gray-100 text-gray-400 cursor-not-allowed"
                }`}
              >
                {isSubmitting ? (
                  <><Loader2 className="w-4 h-4 animate-spin" />Submitting...</>
                ) : isProcessing ? (
                  <><Loader2 className="w-4 h-4 animate-spin" />Processing...</>
                ) : engine === "imtalker" ? (
                  <><Crown className="w-4 h-4" />Generate Premium Video</>
                ) : (
                  <><Sparkles className="w-4 h-4" />Generate Video</>
                )}
              </button>

              {/* Checklist */}
              <div className="mt-4 space-y-2">
                <div className="flex items-center gap-2 text-xs">
                  {portraitUrl ? <CheckCircle className="w-3.5 h-3.5 text-green-500" /> : <div className="w-3.5 h-3.5 rounded-full border-2 border-gray-300" />}
                  <span className={portraitUrl ? "text-green-700" : "text-gray-500"}>肖像画アップロード済み</span>
                </div>
                {inputMode === "text" ? (
                  <div className="flex items-center gap-2 text-xs">
                    {scriptText.trim() ? <CheckCircle className="w-3.5 h-3.5 text-green-500" /> : <div className="w-3.5 h-3.5 rounded-full border-2 border-gray-300" />}
                    <span className={scriptText.trim() ? "text-green-700" : "text-gray-500"}>テキスト入力済み</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-xs">
                    {audioUrl ? <CheckCircle className="w-3.5 h-3.5 text-green-500" /> : <div className="w-3.5 h-3.5 rounded-full border-2 border-gray-300" />}
                    <span className={audioUrl ? "text-green-700" : "text-gray-500"}>音声アップロード済み</span>
                  </div>
                )}
                <div className="flex items-center gap-2 text-xs">
                  {health?.status === "ok" ? <CheckCircle className="w-3.5 h-3.5 text-green-500" /> : <AlertCircle className="w-3.5 h-3.5 text-yellow-500" />}
                  <span className={health?.status === "ok" ? "text-green-700" : "text-yellow-700"}>
                    GPU Worker {health?.status === "ok" ? "online" : "offline"}
                  </span>
                </div>
              </div>

              {/* Pipeline info */}
              <div className="mt-4 pt-3 border-t border-gray-100">
                <p className="text-[10px] text-gray-400 leading-relaxed">
                  {inputMode === "text" ? (
                    engine === "imtalker"
                      ? "Pipeline: テキスト → ElevenLabs TTS → IMTalker (フル表情アニメーション)"
                      : "Pipeline: テキスト → ElevenLabs TTS → MuseTalk (リップシンク)"
                  ) : (
                    engine === "imtalker"
                      ? "Pipeline: 音声 → IMTalker (フル表情アニメーション)"
                      : "Pipeline: 音声 → MuseTalk (リップシンク)"
                  )}
                </p>
              </div>
            </div>

            {/* Job Status */}
            {jobStatus && (
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                  {getStatusIcon(jobStatus.status)}
                  Job Status
                  {currentEngine === "imtalker" && (
                    <span className="text-[9px] bg-purple-100 text-purple-600 px-1.5 py-0.5 rounded-full">Premium</span>
                  )}
                </h3>
                <div className="space-y-3">
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-500">ID</span>
                    <span className="text-gray-700 font-mono text-[10px]">{currentJobId}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-500">Engine</span>
                    <span className="text-gray-700">{currentEngine === "imtalker" ? "Premium (IMTalker)" : "Standard (MuseTalk)"}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-500">Status</span>
                    <span className={`font-medium ${getStatusColor(jobStatus.status)}`}>{getStatusLabel(jobStatus.status)}</span>
                  </div>
                  {(jobStatus.status === "processing" || jobStatus.status === "queued") && (
                    <div>
                      <div className="flex justify-between text-xs mb-1">
                        <span className="text-gray-500">Progress</span>
                        <span className="text-gray-700">{jobStatus.progress || 0}%</span>
                      </div>
                      <div className="w-full bg-gray-100 rounded-full h-2">
                        <div
                          className={`h-2 rounded-full transition-all duration-500 ${
                            currentEngine === "imtalker" ? "bg-gradient-to-r from-purple-500 to-pink-500" : "bg-blue-600"
                          }`}
                          style={{ width: `${jobStatus.progress || 0}%` }}
                        />
                      </div>
                    </div>
                  )}
                  {jobStatus.error && <p className="text-xs text-red-600 bg-red-50 p-2 rounded">{jobStatus.error}</p>}
                  {jobStatus.status === "completed" && (
                    <button onClick={() => handleDownload(currentJobId, currentEngine)}
                      className="w-full py-2.5 px-4 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium flex items-center justify-center gap-2 transition-colors">
                      <Download className="w-4 h-4" /> Download Video
                    </button>
                  )}
                  {["completed", "error", "failed"].includes(jobStatus.status) && (
                    <button onClick={handleReset}
                      className="w-full py-2 px-4 border border-gray-200 text-gray-600 hover:bg-gray-50 rounded-lg text-sm flex items-center justify-center gap-2 transition-colors">
                      <RefreshCw className="w-4 h-4" /> New Generation
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* ── Livestream Brain Panel ── */}
            <LiveStreamPanel
              sessionId={liveSessionId}
              setSessionId={setLiveSessionId}
              portraitUrl={portraitUrl}
              engine={engine}
              voiceId={selectedVoiceId}
              language={languageCode}
              onVideoGenerated={(jobId) => {
                setCurrentJobId(jobId);
                setCurrentEngine(engine);
              }}
            />

            {/* GPU Info */}
            {health?.status === "ok" && (
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                  <Settings className="w-4 h-4 text-gray-500" /> GPU Worker
                </h3>
                <div className="space-y-2 text-xs">
                  {health.gpu_name && <div className="flex justify-between"><span className="text-gray-500">GPU</span><span className="text-gray-700">{health.gpu_name}</span></div>}
                  {health.gpu_memory_used_mb != null && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">VRAM</span>
                      <span className="text-gray-700">{(health.gpu_memory_used_mb / 1024).toFixed(1)} / {(health.gpu_memory_total_mb / 1024).toFixed(1)} GB</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Job History */}
            {jobHistory.length > 0 && (
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                  <Clock className="w-4 h-4 text-gray-500" /> Recent Jobs
                </h3>
                <div className="space-y-2 max-h-60 overflow-y-auto">
                  {jobHistory.map((job) => (
                    <div key={job.job_id} className="flex items-center justify-between p-2 bg-gray-50 rounded-lg">
                      <div className="flex items-center gap-2 min-w-0">
                        {getStatusIcon(job.status)}
                        <div className="min-w-0">
                          <p className="text-[10px] font-mono text-gray-600 truncate">{job.job_id}</p>
                          <p className="text-[10px] text-gray-400">
                            {job.engine === "imtalker" ? "Premium" : "Standard"} / {job.mode === "text" ? "Text" : "Audio"} — {new Date(job.created_at).toLocaleString("ja-JP")}
                          </p>
                        </div>
                      </div>
                      {job.status === "completed" && (
                        <button onClick={() => handleDownload(job.job_id, job.engine)} className="p-1.5 hover:bg-gray-200 rounded transition-colors shrink-0" title="Download">
                          <Download className="w-3.5 h-3.5 text-gray-500" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
