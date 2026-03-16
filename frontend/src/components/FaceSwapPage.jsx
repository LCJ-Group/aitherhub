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
  Volume2,
  VolumeX,
} from "lucide-react";
import faceSwapService from "../base/services/faceSwapService";

/**
 * FaceSwapPage - Video face swap + voice conversion pipeline UI
 *
 * Allows staff to:
 *  1. Upload a video (or provide a URL)
 *  2. Select voice conversion settings
 *  3. Start the face swap + voice conversion pipeline
 *  4. Monitor progress in real-time
 *  5. Download the completed video
 */
export default function FaceSwapPage() {
  const navigate = useNavigate();

  // ── State ──
  const [videoUrl, setVideoUrl] = useState("");
  const [videoFile, setVideoFile] = useState(null);
  const [videoPreview, setVideoPreview] = useState(null);
  const [quality, setQuality] = useState("high");
  const [faceEnhancer, setFaceEnhancer] = useState(true);
  const [enableVoice, setEnableVoice] = useState(true);
  const [removeNoise, setRemoveNoise] = useState(false);
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [voices, setVoices] = useState([]);
  const [voicesLoading, setVoicesLoading] = useState(false);

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

  // Settings panel
  const [showSettings, setShowSettings] = useState(false);

  const fileInputRef = useRef(null);
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
        const status = await faceSwapService.getJobStatus(currentJobId);
        setJobStatus(status);

        if (status.status === "completed" || status.status === "error") {
          clearInterval(pollRef.current);
          pollRef.current = null;
          loadJobs(); // Refresh job list
        }
      } catch (err) {
        console.error("Poll error:", err);
      }
    };

    poll(); // Initial fetch
    pollRef.current = setInterval(poll, 3000);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [currentJobId]);

  // ── Handlers ──

  const loadVoices = async () => {
    setVoicesLoading(true);
    try {
      const data = await faceSwapService.listVoices();
      setVoices(data.voices || []);
      if (data.voices?.length > 0 && !selectedVoiceId) {
        setSelectedVoiceId(data.voices[0].voice_id);
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
      const data = await faceSwapService.listJobs(10);
      setJobs(data.jobs || []);
    } catch (err) {
      console.error("Failed to load jobs:", err);
    } finally {
      setJobsLoading(false);
    }
  };

  const checkHealth = async () => {
    try {
      const data = await faceSwapService.healthCheck();
      setHealth(data);
    } catch (err) {
      console.error("Health check failed:", err);
    }
  };

  const handleFileSelect = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setVideoFile(file);
    setVideoUrl("");
    setError(null);

    // Create preview URL
    const url = URL.createObjectURL(file);
    setVideoPreview(url);
  };

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith("video/")) {
      setVideoFile(file);
      setVideoUrl("");
      setError(null);
      setVideoPreview(URL.createObjectURL(file));
    }
  }, []);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleSubmit = async () => {
    setError(null);
    setIsSubmitting(true);

    try {
      let url = videoUrl;

      // If file was selected, upload to Azure Blob Storage first
      if (videoFile && !videoUrl) {
        try {
          url = await faceSwapService.uploadVideo(videoFile);
        } catch (uploadErr) {
          const msg = uploadErr.response?.data?.detail || uploadErr.message || "アップロードに失敗しました";
          setError(`動画アップロードエラー: ${msg}`);
          setIsSubmitting(false);
          return;
        }
      }

      if (!url) {
        setError("動画URLを入力してください");
        setIsSubmitting(false);
        return;
      }

      const result = await faceSwapService.startJob({
        video_url: url,
        voice_id: enableVoice ? selectedVoiceId : undefined,
        quality,
        face_enhancer: faceEnhancer,
        enable_voice_conversion: enableVoice,
        remove_background_noise: removeNoise,
      });

      setCurrentJobId(result.job_id);
      setJobStatus({
        job_id: result.job_id,
        status: "pending",
        step: "ジョブを開始しています...",
        progress: 0,
      });
    } catch (err) {
      const detail = err.response?.data?.detail || err.message;
      setError(`ジョブの開始に失敗しました: ${detail}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteJob = async (jobId) => {
    try {
      await faceSwapService.deleteJob(jobId);
      setJobs((prev) => prev.filter((j) => j.job_id !== jobId));
      if (currentJobId === jobId) {
        setCurrentJobId(null);
        setJobStatus(null);
      }
    } catch (err) {
      console.error("Failed to delete job:", err);
    }
  };

  const handleDownload = (jobId) => {
    const url = faceSwapService.getDownloadUrl(jobId);
    window.open(url, "_blank");
  };

  const resetForm = () => {
    setVideoFile(null);
    setVideoUrl("");
    setVideoPreview(null);
    setCurrentJobId(null);
    setJobStatus(null);
    setError(null);
  };

  // ── Status helpers ──

  const getStatusColor = (status) => {
    switch (status) {
      case "completed":
        return "text-green-400";
      case "error":
        return "text-red-400";
      case "pending":
        return "text-yellow-400";
      default:
        return "text-blue-400";
    }
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case "completed":
        return <CheckCircle className="w-5 h-5 text-green-400" />;
      case "error":
        return <AlertCircle className="w-5 h-5 text-red-400" />;
      case "pending":
        return <Clock className="w-5 h-5 text-yellow-400" />;
      default:
        return <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />;
    }
  };

  const getStatusLabel = (status) => {
    const labels = {
      pending: "待機中",
      uploading: "ダウンロード中",
      extracting_audio: "音声抽出中",
      face_swapping: "顔変更中",
      voice_converting: "音声変換中",
      merging: "合成中",
      uploading_result: "結果準備中",
      completed: "完了",
      error: "エラー",
    };
    return labels[status] || status;
  };

  // ── Render ──

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      {/* Header */}
      <header className="border-b border-gray-800 bg-[#0d0d14]">
        <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate("/")}
              className="p-2 hover:bg-gray-800 rounded-lg transition"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <h1 className="text-xl font-bold flex items-center gap-2">
                <Video className="w-6 h-6 text-purple-400" />
                Face Swap Studio
              </h1>
              <p className="text-sm text-gray-400">
                動画の顔変更 + 音声変換パイプライン
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {health && (
              <div
                className={`flex items-center gap-1 text-xs px-2 py-1 rounded-full ${
                  health.gpu_worker?.status === "ok"
                    ? "bg-green-900/50 text-green-400"
                    : "bg-red-900/50 text-red-400"
                }`}
              >
                <div
                  className={`w-2 h-2 rounded-full ${
                    health.gpu_worker?.status === "ok"
                      ? "bg-green-400"
                      : "bg-red-400"
                  }`}
                />
                GPU{" "}
                {health.gpu_worker?.status === "ok"
                  ? "オンライン"
                  : "オフライン"}
              </div>
            )}
            <button
              onClick={() => setShowSettings(!showSettings)}
              className="p-2 hover:bg-gray-800 rounded-lg transition"
            >
              <Settings className="w-5 h-5 text-gray-400" />
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-4 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* ── Left Column: Input & Controls ── */}
          <div className="lg:col-span-2 space-y-6">
            {/* Video Input */}
            <div className="bg-[#12121a] rounded-xl border border-gray-800 p-6">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <Upload className="w-5 h-5 text-purple-400" />
                動画入力
              </h2>

              {/* Drop zone */}
              <div
                onDrop={handleDrop}
                onDragOver={handleDragOver}
                onClick={() => fileInputRef.current?.click()}
                className="border-2 border-dashed border-gray-700 rounded-xl p-8 text-center cursor-pointer hover:border-purple-500 hover:bg-purple-900/10 transition-all"
              >
                {videoPreview ? (
                  <video
                    src={videoPreview}
                    className="max-h-48 mx-auto rounded-lg"
                    controls
                    muted
                  />
                ) : (
                  <>
                    <Upload className="w-12 h-12 text-gray-500 mx-auto mb-3" />
                    <p className="text-gray-400">
                      動画ファイルをドラッグ&ドロップ
                    </p>
                    <p className="text-sm text-gray-500 mt-1">
                      または クリックして選択
                    </p>
                    <p className="text-xs text-gray-600 mt-2">
                      MP4, MOV, AVI (最大500MB)
                    </p>
                  </>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="video/*"
                  onChange={handleFileSelect}
                  className="hidden"
                />
              </div>

              {/* URL input */}
              <div className="mt-4">
                <label className="block text-sm text-gray-400 mb-1">
                  または動画URLを入力
                </label>
                <input
                  type="url"
                  value={videoUrl}
                  onChange={(e) => {
                    setVideoUrl(e.target.value);
                    setVideoFile(null);
                    setVideoPreview(null);
                  }}
                  placeholder="https://storage.blob.core.windows.net/..."
                  className="w-full bg-[#1a1a24] border border-gray-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-purple-500 transition"
                />
              </div>
            </div>

            {/* Settings */}
            <div className="bg-[#12121a] rounded-xl border border-gray-800 p-6">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <Settings className="w-5 h-5 text-purple-400" />
                処理設定
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Quality */}
                <div>
                  <label className="block text-sm text-gray-400 mb-1">
                    顔変更品質
                  </label>
                  <select
                    value={quality}
                    onChange={(e) => setQuality(e.target.value)}
                    className="w-full bg-[#1a1a24] border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                  >
                    <option value="fast">高速 (低品質)</option>
                    <option value="balanced">バランス</option>
                    <option value="high">高品質 (推奨)</option>
                  </select>
                </div>

                {/* Face Enhancer */}
                <div className="flex items-center justify-between">
                  <div>
                    <label className="block text-sm text-gray-400">
                      顔補正 (GFPGAN)
                    </label>
                    <p className="text-xs text-gray-500">
                      顔の品質を向上させます
                    </p>
                  </div>
                  <button
                    onClick={() => setFaceEnhancer(!faceEnhancer)}
                    className={`relative w-12 h-6 rounded-full transition ${
                      faceEnhancer ? "bg-purple-600" : "bg-gray-700"
                    }`}
                  >
                    <div
                      className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                        faceEnhancer ? "translate-x-6" : "translate-x-0.5"
                      }`}
                    />
                  </button>
                </div>

                {/* Voice Conversion */}
                <div className="flex items-center justify-between">
                  <div>
                    <label className="block text-sm text-gray-400 flex items-center gap-1">
                      <Mic className="w-4 h-4" />
                      音声変換 (ElevenLabs)
                    </label>
                    <p className="text-xs text-gray-500">
                      声をインフルエンサーの声に変換
                    </p>
                  </div>
                  <button
                    onClick={() => setEnableVoice(!enableVoice)}
                    className={`relative w-12 h-6 rounded-full transition ${
                      enableVoice ? "bg-purple-600" : "bg-gray-700"
                    }`}
                  >
                    <div
                      className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                        enableVoice ? "translate-x-6" : "translate-x-0.5"
                      }`}
                    />
                  </button>
                </div>

                {/* Remove Noise */}
                {enableVoice && (
                  <div className="flex items-center justify-between">
                    <div>
                      <label className="block text-sm text-gray-400">
                        背景ノイズ除去
                      </label>
                      <p className="text-xs text-gray-500">
                        音声のノイズを除去します
                      </p>
                    </div>
                    <button
                      onClick={() => setRemoveNoise(!removeNoise)}
                      className={`relative w-12 h-6 rounded-full transition ${
                        removeNoise ? "bg-purple-600" : "bg-gray-700"
                      }`}
                    >
                      <div
                        className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                          removeNoise ? "translate-x-6" : "translate-x-0.5"
                        }`}
                      />
                    </button>
                  </div>
                )}
              </div>

              {/* Voice Selection */}
              {enableVoice && (
                <div className="mt-4">
                  <label className="block text-sm text-gray-400 mb-1 flex items-center gap-1">
                    <Volume2 className="w-4 h-4" />
                    変換先の声
                  </label>
                  {voicesLoading ? (
                    <div className="flex items-center gap-2 text-sm text-gray-500">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      声を読み込み中...
                    </div>
                  ) : (
                    <select
                      value={selectedVoiceId}
                      onChange={(e) => setSelectedVoiceId(e.target.value)}
                      className="w-full bg-[#1a1a24] border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                    >
                      {voices.map((v) => (
                        <option key={v.voice_id} value={v.voice_id}>
                          {v.name}{" "}
                          {v.category === "cloned"
                            ? "(クローン)"
                            : `(${v.category || "プリセット"})`}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}
            </div>

            {/* Submit Button */}
            <button
              onClick={handleSubmit}
              disabled={isSubmitting || (!videoUrl && !videoFile)}
              className="w-full py-3 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 disabled:from-gray-700 disabled:to-gray-700 disabled:cursor-not-allowed rounded-xl font-semibold text-lg transition-all flex items-center justify-center gap-2"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  処理を開始中...
                </>
              ) : (
                <>
                  <Play className="w-5 h-5" />
                  顔変更 + 音声変換を開始
                </>
              )}
            </button>

            {/* Error */}
            {error && (
              <div className="bg-red-900/30 border border-red-800 rounded-xl p-4 flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-red-400 mt-0.5 flex-shrink-0" />
                <div>
                  <p className="text-red-300 text-sm">{error}</p>
                </div>
              </div>
            )}

            {/* Job Progress */}
            {jobStatus && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-semibold flex items-center gap-2">
                    {getStatusIcon(jobStatus.status)}
                    処理状況
                  </h2>
                  <span
                    className={`text-sm font-medium ${getStatusColor(
                      jobStatus.status
                    )}`}
                  >
                    {getStatusLabel(jobStatus.status)}
                  </span>
                </div>

                {/* Progress Bar */}
                <div className="mb-4">
                  <div className="flex justify-between text-sm text-gray-400 mb-1">
                    <span>{jobStatus.step}</span>
                    <span>{jobStatus.progress}%</span>
                  </div>
                  <div className="w-full bg-gray-800 rounded-full h-3 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        jobStatus.status === "error"
                          ? "bg-red-500"
                          : jobStatus.status === "completed"
                          ? "bg-green-500"
                          : "bg-gradient-to-r from-purple-500 to-blue-500"
                      }`}
                      style={{ width: `${jobStatus.progress}%` }}
                    />
                  </div>
                </div>

                {/* Pipeline Steps */}
                <div className="grid grid-cols-6 gap-1 mb-4">
                  {[
                    { key: "uploading", label: "DL" },
                    { key: "extracting_audio", label: "音声" },
                    { key: "face_swapping", label: "顔" },
                    { key: "voice_converting", label: "声" },
                    { key: "merging", label: "合成" },
                    { key: "completed", label: "完了" },
                  ].map((step, i) => {
                    const statusOrder = [
                      "pending",
                      "uploading",
                      "extracting_audio",
                      "face_swapping",
                      "voice_converting",
                      "merging",
                      "uploading_result",
                      "completed",
                    ];
                    const currentIdx = statusOrder.indexOf(jobStatus.status);
                    const stepIdx = statusOrder.indexOf(step.key);
                    const isActive = currentIdx >= stepIdx;
                    const isCurrent =
                      jobStatus.status === step.key ||
                      (step.key === "completed" &&
                        jobStatus.status === "uploading_result");

                    return (
                      <div
                        key={step.key}
                        className={`text-center py-1 rounded text-xs font-medium ${
                          isCurrent
                            ? "bg-purple-600 text-white"
                            : isActive
                            ? "bg-purple-900/50 text-purple-300"
                            : "bg-gray-800 text-gray-500"
                        }`}
                      >
                        {step.label}
                      </div>
                    );
                  })}
                </div>

                {/* Elapsed time */}
                {jobStatus.elapsed_sec > 0 && (
                  <p className="text-xs text-gray-500">
                    経過時間: {Math.round(jobStatus.elapsed_sec)}秒
                    {jobStatus.duration_sec > 0 &&
                      ` / 動画長: ${Math.round(jobStatus.duration_sec)}秒`}
                  </p>
                )}

                {/* Error detail */}
                {jobStatus.status === "error" && jobStatus.error && (
                  <div className="mt-3 bg-red-900/20 rounded-lg p-3">
                    <p className="text-sm text-red-300">{jobStatus.error}</p>
                  </div>
                )}

                {/* Download button */}
                {jobStatus.status === "completed" && (
                  <div className="mt-4 flex gap-3">
                    <button
                      onClick={() => handleDownload(currentJobId)}
                      className="flex-1 py-2.5 bg-green-600 hover:bg-green-500 rounded-lg font-medium flex items-center justify-center gap-2 transition"
                    >
                      <Download className="w-5 h-5" />
                      完成動画をダウンロード
                    </button>
                    <button
                      onClick={resetForm}
                      className="px-4 py-2.5 bg-gray-700 hover:bg-gray-600 rounded-lg font-medium flex items-center justify-center gap-2 transition"
                    >
                      <RefreshCw className="w-4 h-4" />
                      新規
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── Right Column: Job History ── */}
          <div className="space-y-6">
            {/* Job History */}
            <div className="bg-[#12121a] rounded-xl border border-gray-800 p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-sm flex items-center gap-2">
                  <Clock className="w-4 h-4 text-gray-400" />
                  処理履歴
                </h3>
                <button
                  onClick={loadJobs}
                  className="p-1 hover:bg-gray-800 rounded transition"
                  title="更新"
                >
                  <RefreshCw
                    className={`w-4 h-4 text-gray-400 ${
                      jobsLoading ? "animate-spin" : ""
                    }`}
                  />
                </button>
              </div>

              {jobs.length === 0 ? (
                <p className="text-sm text-gray-500 text-center py-4">
                  まだ処理履歴がありません
                </p>
              ) : (
                <div className="space-y-2">
                  {jobs.map((job) => (
                    <div
                      key={job.job_id}
                      className={`p-3 rounded-lg border transition cursor-pointer ${
                        currentJobId === job.job_id
                          ? "border-purple-500 bg-purple-900/20"
                          : "border-gray-800 hover:border-gray-700 bg-[#0d0d14]"
                      }`}
                      onClick={() => setCurrentJobId(job.job_id)}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          {getStatusIcon(job.status)}
                          <span className="text-sm font-medium">
                            {job.job_id.slice(0, 16)}...
                          </span>
                        </div>
                        <div className="flex items-center gap-1">
                          {job.status === "completed" && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDownload(job.job_id);
                              }}
                              className="p-1 hover:bg-gray-700 rounded"
                              title="ダウンロード"
                            >
                              <Download className="w-3.5 h-3.5 text-green-400" />
                            </button>
                          )}
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteJob(job.job_id);
                            }}
                            className="p-1 hover:bg-gray-700 rounded"
                            title="削除"
                          >
                            <Trash2 className="w-3.5 h-3.5 text-gray-500 hover:text-red-400" />
                          </button>
                        </div>
                      </div>
                      <div className="mt-1 flex items-center justify-between">
                        <span
                          className={`text-xs ${getStatusColor(job.status)}`}
                        >
                          {getStatusLabel(job.status)}
                        </span>
                        <span className="text-xs text-gray-500">
                          {job.progress}%
                        </span>
                      </div>
                      {/* Mini progress bar */}
                      <div className="mt-1 w-full bg-gray-800 rounded-full h-1 overflow-hidden">
                        <div
                          className={`h-full rounded-full ${
                            job.status === "completed"
                              ? "bg-green-500"
                              : job.status === "error"
                              ? "bg-red-500"
                              : "bg-purple-500"
                          }`}
                          style={{ width: `${job.progress}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Pipeline Info */}
            <div className="bg-[#12121a] rounded-xl border border-gray-800 p-4">
              <h3 className="font-semibold text-sm mb-3">パイプライン概要</h3>
              <div className="space-y-3 text-xs">
                <div className="flex items-start gap-2">
                  <div className="w-6 h-6 rounded-full bg-purple-900/50 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <span className="text-purple-400 font-bold">1</span>
                  </div>
                  <div>
                    <p className="text-gray-300 font-medium">動画アップロード</p>
                    <p className="text-gray-500">スタッフが撮影した動画</p>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <div className="w-6 h-6 rounded-full bg-purple-900/50 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <span className="text-purple-400 font-bold">2</span>
                  </div>
                  <div>
                    <p className="text-gray-300 font-medium">顔変更 (FaceFusion)</p>
                    <p className="text-gray-500">
                      GPU処理で顔をインフルエンサーに変更
                    </p>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <div className="w-6 h-6 rounded-full bg-purple-900/50 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <span className="text-purple-400 font-bold">3</span>
                  </div>
                  <div>
                    <p className="text-gray-300 font-medium">
                      音声変換 (ElevenLabs)
                    </p>
                    <p className="text-gray-500">
                      声をインフルエンサーの声に変換
                    </p>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <div className="w-6 h-6 rounded-full bg-green-900/50 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <span className="text-green-400 font-bold">4</span>
                  </div>
                  <div>
                    <p className="text-gray-300 font-medium">合成 & ダウンロード</p>
                    <p className="text-gray-500">
                      映像+音声を合成して完成動画を出力
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Health Status */}
            {health && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-4">
                <h3 className="font-semibold text-sm mb-3">システム状態</h3>
                <div className="space-y-2 text-xs">
                  <div className="flex justify-between">
                    <span className="text-gray-400">GPU Worker</span>
                    <span
                      className={
                        health.gpu_worker?.status === "ok"
                          ? "text-green-400"
                          : "text-red-400"
                      }
                    >
                      {health.gpu_worker?.status === "ok"
                        ? "オンライン"
                        : health.gpu_worker?.status || "不明"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">ElevenLabs</span>
                    <span
                      className={
                        health.elevenlabs?.status === "ok"
                          ? "text-green-400"
                          : "text-yellow-400"
                      }
                    >
                      {health.elevenlabs?.status === "ok"
                        ? "接続済み"
                        : health.elevenlabs?.status || "不明"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">アクティブジョブ</span>
                    <span className="text-gray-300">
                      {health.active_jobs || 0}
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
