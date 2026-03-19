import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  Upload,
  Play,
  Download,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  Clock,
  ArrowLeft,
  Loader2,
  ImageIcon,
  Mic,
  Video,
  Settings,
  Sparkles,
  X,
  Volume2,
  Sliders,
} from "lucide-react";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";

/**
 * AI Live Creator Page
 *
 * Generate lip-synced videos using MuseTalk.
 * User provides a portrait image + audio → AI generates a video
 * where the portrait appears to speak the audio.
 */
export default function AiLiveCreatorPage() {
  const navigate = useNavigate();

  // ── Form State ──
  const [portraitFile, setPortraitFile] = useState(null);
  const [portraitPreview, setPortraitPreview] = useState(null);
  const [portraitUrl, setPortraitUrl] = useState("");
  const [portraitUploadProgress, setPortraitUploadProgress] = useState(0);
  const [isUploadingPortrait, setIsUploadingPortrait] = useState(false);

  const [audioFile, setAudioFile] = useState(null);
  const [audioName, setAudioName] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [audioUploadProgress, setAudioUploadProgress] = useState(0);
  const [isUploadingAudio, setIsUploadingAudio] = useState(false);

  // Advanced settings
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [bboxShift, setBboxShift] = useState(0);
  const [extraMargin, setExtraMargin] = useState(10);
  const [batchSize, setBatchSize] = useState(16);
  const [outputFps, setOutputFps] = useState(25);

  // Job state
  const [currentJobId, setCurrentJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Health
  const [health, setHealth] = useState(null);

  // Job history (local)
  const [jobHistory, setJobHistory] = useState([]);

  // Refs
  const portraitInputRef = useRef(null);
  const audioInputRef = useRef(null);
  const pollRef = useRef(null);

  // ── Load health on mount ──
  useEffect(() => {
    checkHealth();
    // Load job history from localStorage
    try {
      const saved = localStorage.getItem("aiLiveCreator_jobs");
      if (saved) setJobHistory(JSON.parse(saved));
    } catch {}
  }, []);

  // ── Poll job status ──
  useEffect(() => {
    if (!currentJobId) return;

    const poll = async () => {
      try {
        const status = await aiLiveCreatorService.getStatus(currentJobId);
        setJobStatus(status);

        if (
          status.status === "completed" ||
          status.status === "error" ||
          status.status === "failed"
        ) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          // Update job history
          updateJobHistory(currentJobId, status);
        }
      } catch (err) {
        console.error("Poll error:", err);
      }
    };

    poll(); // immediate
    pollRef.current = setInterval(poll, 3000);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [currentJobId]);

  // ── Helpers ──
  const checkHealth = async () => {
    try {
      const h = await aiLiveCreatorService.healthCheck();
      setHealth(h);
    } catch {
      setHealth({ status: "error", error: "Cannot reach API" });
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

    // Validate image
    if (!file.type.startsWith("image/")) {
      setError("Please select an image file (JPEG, PNG)");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      setError("Image must be less than 20MB");
      return;
    }

    setPortraitFile(file);
    setPortraitPreview(URL.createObjectURL(file));
    setError(null);

    // Upload
    setIsUploadingPortrait(true);
    setPortraitUploadProgress(0);
    try {
      const url = await aiLiveCreatorService.uploadFile(
        file,
        "portrait",
        setPortraitUploadProgress
      );
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

    // Validate audio
    const validTypes = [
      "audio/wav",
      "audio/mpeg",
      "audio/mp3",
      "audio/x-wav",
      "audio/wave",
      "audio/mp4",
      "audio/aac",
    ];
    if (!validTypes.includes(file.type) && !file.name.match(/\.(wav|mp3|m4a|aac)$/i)) {
      setError("Please select an audio file (WAV, MP3, M4A)");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setError("Audio must be less than 50MB");
      return;
    }

    setAudioFile(file);
    setAudioName(file.name);
    setError(null);

    // Upload
    setIsUploadingAudio(true);
    setAudioUploadProgress(0);
    try {
      const url = await aiLiveCreatorService.uploadFile(
        file,
        "audio",
        setAudioUploadProgress
      );
      setAudioUrl(url);
    } catch (err) {
      setError(`Audio upload failed: ${err.message}`);
      setAudioFile(null);
      setAudioName("");
    } finally {
      setIsUploadingAudio(false);
    }
  };

  // ── Generate ──
  const handleGenerate = async () => {
    if (!portraitUrl || !audioUrl) {
      setError("Please upload both a portrait image and an audio file");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    setJobStatus(null);

    try {
      const result = await aiLiveCreatorService.generate({
        portrait_url: portraitUrl,
        audio_url: audioUrl,
        bbox_shift: bboxShift,
        extra_margin: extraMargin,
        batch_size: batchSize,
        output_fps: outputFps,
      });

      if (!result.success) {
        setError(result.error || "Generation failed");
        return;
      }

      setCurrentJobId(result.job_id);
      setJobStatus({ status: result.status || "queued", progress: 0 });

      // Add to history
      const newJob = {
        job_id: result.job_id,
        status: "queued",
        progress: 0,
        created_at: new Date().toISOString(),
        portrait_preview: portraitPreview,
      };
      setJobHistory((prev) => {
        const updated = [newJob, ...prev].slice(0, 20);
        localStorage.setItem("aiLiveCreator_jobs", JSON.stringify(updated));
        return updated;
      });
    } catch (err) {
      setError(
        err.response?.data?.error ||
          err.response?.data?.detail ||
          err.message ||
          "Generation failed"
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  // ── Download ──
  const handleDownload = async (jobId) => {
    try {
      const blob = await aiLiveCreatorService.downloadVideo(jobId || currentJobId);
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
    setAudioFile(null);
    setAudioName("");
    setAudioUrl("");
    setCurrentJobId(null);
    setJobStatus(null);
    setError(null);
    setIsSubmitting(false);
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  // ── Status helpers ──
  const getStatusColor = (status) => {
    switch (status) {
      case "completed":
        return "text-green-600";
      case "processing":
        return "text-blue-600";
      case "queued":
        return "text-yellow-600";
      case "error":
      case "failed":
        return "text-red-600";
      default:
        return "text-gray-600";
    }
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case "completed":
        return <CheckCircle className="w-5 h-5 text-green-600" />;
      case "processing":
        return <Loader2 className="w-5 h-5 text-blue-600 animate-spin" />;
      case "queued":
        return <Clock className="w-5 h-5 text-yellow-600" />;
      case "error":
      case "failed":
        return <AlertCircle className="w-5 h-5 text-red-600" />;
      default:
        return <Clock className="w-5 h-5 text-gray-400" />;
    }
  };

  const getStatusLabel = (status) => {
    switch (status) {
      case "completed":
        return "Complete";
      case "processing":
        return "Generating...";
      case "queued":
        return "Queued";
      case "error":
      case "failed":
        return "Error";
      default:
        return status || "Unknown";
    }
  };

  const isReady = portraitUrl && audioUrl && !isUploadingPortrait && !isUploadingAudio;
  const isProcessing =
    jobStatus &&
    (jobStatus.status === "queued" || jobStatus.status === "processing");

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate("/")}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <ArrowLeft className="w-5 h-5 text-gray-600" />
            </button>
            <div>
              <h1 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-purple-600" />
                AI Live Creator
              </h1>
              <p className="text-xs text-gray-500">
                Portrait + Audio → AI Lip-Sync Video
              </p>
            </div>
          </div>

          {/* Health indicator */}
          <div className="flex items-center gap-2">
            <div
              className={`w-2 h-2 rounded-full ${
                health?.status === "ok"
                  ? "bg-green-500"
                  : health?.status === "not_configured"
                  ? "bg-yellow-500"
                  : "bg-red-500"
              }`}
            />
            <span className="text-xs text-gray-500">
              {health?.status === "ok"
                ? "GPU Ready"
                : health?.status === "not_configured"
                ? "GPU Not Configured"
                : "GPU Offline"}
            </span>
            <button
              onClick={checkHealth}
              className="p-1 hover:bg-gray-100 rounded transition-colors"
              title="Refresh status"
            >
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
            <div className="flex-1">
              <p className="text-sm text-red-700">{error}</p>
            </div>
            <button onClick={() => setError(null)}>
              <X className="w-4 h-4 text-red-400" />
            </button>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* ── Left Column: Inputs ── */}
          <div className="lg:col-span-2 space-y-4">
            {/* Portrait Upload */}
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <h2 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                <ImageIcon className="w-4 h-4 text-purple-600" />
                Portrait Image
              </h2>
              <p className="text-xs text-gray-500 mb-3">
                Upload a front-facing photo. The AI will animate this face to match the audio.
              </p>

              {portraitPreview ? (
                <div className="relative">
                  <img
                    src={portraitPreview}
                    alt="Portrait"
                    className="w-full max-h-64 object-contain rounded-lg bg-gray-50"
                  />
                  {isUploadingPortrait && (
                    <div className="absolute inset-0 bg-black/30 rounded-lg flex items-center justify-center">
                      <div className="text-center">
                        <Loader2 className="w-8 h-8 text-white animate-spin mx-auto mb-2" />
                        <p className="text-white text-sm font-medium">
                          Uploading... {portraitUploadProgress}%
                        </p>
                      </div>
                    </div>
                  )}
                  {!isUploadingPortrait && portraitUrl && (
                    <div className="absolute top-2 right-2 bg-green-500 text-white px-2 py-1 rounded-full text-xs flex items-center gap-1">
                      <CheckCircle className="w-3 h-3" /> Uploaded
                    </div>
                  )}
                  <button
                    onClick={() => {
                      setPortraitFile(null);
                      setPortraitPreview(null);
                      setPortraitUrl("");
                    }}
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
                  <p className="text-sm text-gray-500">
                    Click to upload portrait image
                  </p>
                  <p className="text-xs text-gray-400 mt-1">
                    JPEG, PNG (max 20MB)
                  </p>
                </div>
              )}
              <input
                ref={portraitInputRef}
                type="file"
                accept="image/jpeg,image/png,image/jpg"
                onChange={handlePortraitSelect}
                className="hidden"
              />
            </div>

            {/* Audio Upload */}
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <h2 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                <Mic className="w-4 h-4 text-purple-600" />
                Audio File
              </h2>
              <p className="text-xs text-gray-500 mb-3">
                Upload the audio that the portrait will lip-sync to. WAV format recommended.
              </p>

              {audioFile ? (
                <div className="relative bg-gray-50 rounded-lg p-4">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
                      <Volume2 className="w-5 h-5 text-purple-600" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-800 truncate">
                        {audioName}
                      </p>
                      <p className="text-xs text-gray-500">
                        {(audioFile.size / 1024 / 1024).toFixed(1)} MB
                      </p>
                    </div>
                    {isUploadingAudio ? (
                      <div className="flex items-center gap-2">
                        <Loader2 className="w-4 h-4 text-purple-600 animate-spin" />
                        <span className="text-xs text-purple-600">
                          {audioUploadProgress}%
                        </span>
                      </div>
                    ) : audioUrl ? (
                      <CheckCircle className="w-5 h-5 text-green-500" />
                    ) : null}
                  </div>
                  <button
                    onClick={() => {
                      setAudioFile(null);
                      setAudioName("");
                      setAudioUrl("");
                    }}
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
                  <p className="text-sm text-gray-500">
                    Click to upload audio file
                  </p>
                  <p className="text-xs text-gray-400 mt-1">
                    WAV, MP3, M4A (max 50MB)
                  </p>
                </div>
              )}
              <input
                ref={audioInputRef}
                type="file"
                accept="audio/wav,audio/mpeg,audio/mp3,.wav,.mp3,.m4a"
                onChange={handleAudioSelect}
                className="hidden"
              />
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
                </span>
                <span className="text-gray-400 text-xs">
                  {showAdvanced ? "Hide" : "Show"}
                </span>
              </button>

              {showAdvanced && (
                <div className="px-5 pb-5 space-y-4 border-t border-gray-100 pt-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="text-xs font-medium text-gray-600 block mb-1">
                        Bbox Shift
                      </label>
                      <input
                        type="number"
                        value={bboxShift}
                        onChange={(e) => setBboxShift(Number(e.target.value))}
                        min={-50}
                        max={50}
                        className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none"
                      />
                      <p className="text-[10px] text-gray-400 mt-1">
                        Face detection vertical shift (-50 to 50)
                      </p>
                    </div>
                    <div>
                      <label className="text-xs font-medium text-gray-600 block mb-1">
                        Extra Margin
                      </label>
                      <input
                        type="number"
                        value={extraMargin}
                        onChange={(e) => setExtraMargin(Number(e.target.value))}
                        min={0}
                        max={50}
                        className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none"
                      />
                      <p className="text-[10px] text-gray-400 mt-1">
                        Extra margin below face (0-50)
                      </p>
                    </div>
                    <div>
                      <label className="text-xs font-medium text-gray-600 block mb-1">
                        Batch Size
                      </label>
                      <input
                        type="number"
                        value={batchSize}
                        onChange={(e) => setBatchSize(Number(e.target.value))}
                        min={1}
                        max={64}
                        className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none"
                      />
                      <p className="text-[10px] text-gray-400 mt-1">
                        Higher = faster, more VRAM (1-64)
                      </p>
                    </div>
                    <div>
                      <label className="text-xs font-medium text-gray-600 block mb-1">
                        Output FPS
                      </label>
                      <input
                        type="number"
                        value={outputFps}
                        onChange={(e) => setOutputFps(Number(e.target.value))}
                        min={15}
                        max={60}
                        className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none"
                      />
                      <p className="text-[10px] text-gray-400 mt-1">
                        Video frame rate (15-60)
                      </p>
                    </div>
                  </div>
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
                    ? "bg-purple-600 hover:bg-purple-700 text-white shadow-md hover:shadow-lg"
                    : "bg-gray-100 text-gray-400 cursor-not-allowed"
                }`}
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Submitting...
                  </>
                ) : isProcessing ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Processing...
                  </>
                ) : (
                  <>
                    <Sparkles className="w-4 h-4" />
                    Generate Video
                  </>
                )}
              </button>

              {/* Checklist */}
              <div className="mt-4 space-y-2">
                <div className="flex items-center gap-2 text-xs">
                  {portraitUrl ? (
                    <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                  ) : (
                    <div className="w-3.5 h-3.5 rounded-full border-2 border-gray-300" />
                  )}
                  <span
                    className={
                      portraitUrl ? "text-green-700" : "text-gray-500"
                    }
                  >
                    Portrait image uploaded
                  </span>
                </div>
                <div className="flex items-center gap-2 text-xs">
                  {audioUrl ? (
                    <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                  ) : (
                    <div className="w-3.5 h-3.5 rounded-full border-2 border-gray-300" />
                  )}
                  <span
                    className={audioUrl ? "text-green-700" : "text-gray-500"}
                  >
                    Audio file uploaded
                  </span>
                </div>
                <div className="flex items-center gap-2 text-xs">
                  {health?.status === "ok" ? (
                    <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                  ) : (
                    <AlertCircle className="w-3.5 h-3.5 text-yellow-500" />
                  )}
                  <span
                    className={
                      health?.status === "ok"
                        ? "text-green-700"
                        : "text-yellow-700"
                    }
                  >
                    GPU Worker{" "}
                    {health?.status === "ok" ? "online" : "offline"}
                  </span>
                </div>
              </div>
            </div>

            {/* Job Status */}
            {jobStatus && (
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                  {getStatusIcon(jobStatus.status)}
                  Job Status
                </h3>

                <div className="space-y-3">
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-500">ID</span>
                    <span className="text-gray-700 font-mono text-[10px]">
                      {currentJobId}
                    </span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-500">Status</span>
                    <span
                      className={`font-medium ${getStatusColor(
                        jobStatus.status
                      )}`}
                    >
                      {getStatusLabel(jobStatus.status)}
                    </span>
                  </div>

                  {/* Progress bar */}
                  {(jobStatus.status === "processing" ||
                    jobStatus.status === "queued") && (
                    <div>
                      <div className="flex justify-between text-xs mb-1">
                        <span className="text-gray-500">Progress</span>
                        <span className="text-gray-700">
                          {jobStatus.progress || 0}%
                        </span>
                      </div>
                      <div className="w-full bg-gray-100 rounded-full h-2">
                        <div
                          className="bg-purple-600 h-2 rounded-full transition-all duration-500"
                          style={{
                            width: `${jobStatus.progress || 0}%`,
                          }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Error message */}
                  {jobStatus.error && (
                    <p className="text-xs text-red-600 bg-red-50 p-2 rounded">
                      {jobStatus.error}
                    </p>
                  )}

                  {/* Download button */}
                  {jobStatus.status === "completed" && (
                    <button
                      onClick={() => handleDownload(currentJobId)}
                      className="w-full py-2.5 px-4 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium flex items-center justify-center gap-2 transition-colors"
                    >
                      <Download className="w-4 h-4" />
                      Download Video
                    </button>
                  )}

                  {/* New generation button */}
                  {(jobStatus.status === "completed" ||
                    jobStatus.status === "error" ||
                    jobStatus.status === "failed") && (
                    <button
                      onClick={handleReset}
                      className="w-full py-2 px-4 border border-gray-200 text-gray-600 hover:bg-gray-50 rounded-lg text-sm flex items-center justify-center gap-2 transition-colors"
                    >
                      <RefreshCw className="w-4 h-4" />
                      New Generation
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* GPU Info */}
            {health?.status === "ok" && (
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                  <Settings className="w-4 h-4 text-gray-500" />
                  GPU Worker
                </h3>
                <div className="space-y-2 text-xs">
                  {health.gpu_name && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">GPU</span>
                      <span className="text-gray-700">{health.gpu_name}</span>
                    </div>
                  )}
                  {health.gpu_memory_used_mb != null && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">VRAM</span>
                      <span className="text-gray-700">
                        {(health.gpu_memory_used_mb / 1024).toFixed(1)} /{" "}
                        {(health.gpu_memory_total_mb / 1024).toFixed(1)} GB
                      </span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-gray-500">MuseTalk</span>
                    <span
                      className={
                        health.musetalk_loaded
                          ? "text-green-600"
                          : "text-yellow-600"
                      }
                    >
                      {health.musetalk_loaded ? "Loaded" : "Not loaded (lazy)"}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Job History */}
            {jobHistory.length > 0 && (
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
                  <Clock className="w-4 h-4 text-gray-500" />
                  Recent Jobs
                </h3>
                <div className="space-y-2 max-h-60 overflow-y-auto">
                  {jobHistory.map((job) => (
                    <div
                      key={job.job_id}
                      className="flex items-center justify-between p-2 bg-gray-50 rounded-lg"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        {getStatusIcon(job.status)}
                        <div className="min-w-0">
                          <p className="text-[10px] font-mono text-gray-600 truncate">
                            {job.job_id}
                          </p>
                          <p className="text-[10px] text-gray-400">
                            {new Date(job.created_at).toLocaleString("ja-JP")}
                          </p>
                        </div>
                      </div>
                      {job.status === "completed" && (
                        <button
                          onClick={() => handleDownload(job.job_id)}
                          className="p-1.5 hover:bg-gray-200 rounded transition-colors shrink-0"
                          title="Download"
                        >
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
