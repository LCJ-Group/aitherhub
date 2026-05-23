import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Radio,
  Play,
  Square,
  Settings,
  Mic,
  MicOff,
  Volume2,
  Upload,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  Loader2,
  Users,
  MessageSquare,
  Zap,
  Monitor,
  Camera,
  Sliders,
  Send,
  Trash2,
} from "lucide-react";
import liverCloneService from "../base/services/liverCloneService";
import { useTranslation } from "react-i18next";

/**
 * Liver Clone Page — Real-time Face Swap + Voice Conversion Live Streaming
 *
 * Layout:
 *   Left:   Stream Preview + Status
 *   Right:  Configuration Panel (Face, Voice, Stream, Auto-pilot)
 *
 * Modes:
 *   Manual:  Person speaks → face+voice converted → streamed
 *   Auto:    AI generates script → TTS → lip-sync → streamed
 *   Hybrid:  Person speaks when active, AI fills silence automatically
 */
export default function LiverClonePage() {
  useTranslation();
  const navigate = useNavigate();

  // ── Session State ──
  const [sessionId, setSessionId] = useState(null);
  const [sessionStatus, setSessionStatus] = useState(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState(null);

  // ── Configuration ──
  const [sourceFaceUrl, setSourceFaceUrl] = useState("");
  const [sourceFaceFile, setSourceFaceFile] = useState(null);
  const [sourceFacePreview, setSourceFacePreview] = useState(null);
  const [inputRtmp, setInputRtmp] = useState("");
  const [outputRtmp, setOutputRtmp] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [quality, setQuality] = useState("high");
  const [language, setLanguage] = useState("en");
  const [resolution, setResolution] = useState("720p");
  const [fps, setFps] = useState(30);

  // Voice settings
  const [voiceStability, setVoiceStability] = useState(0.5);
  const [voiceSimilarity, setVoiceSimilarity] = useState(0.75);

  // VAD settings
  const [vadThreshold, setVadThreshold] = useState(0.3);
  const [silenceTimeout, setSilenceTimeout] = useState(5.0);

  // Persona (Auto-pilot)
  const [personaName, setPersonaName] = useState("");
  const [personaStyle, setPersonaStyle] = useState("");
  const [openingScript, setOpeningScript] = useState("");

  // Comments
  const [commentText, setCommentText] = useState("");
  const [commentHistory, setCommentHistory] = useState([]);

  // Manual speak
  const [speakText, setSpeakText] = useState("");

  // Health
  const [health, setHealth] = useState(null);

  // Tabs
  const [activeTab, setActiveTab] = useState("config"); // config, comments, autopilot, metrics

  const pollRef = useRef(null);
  const fileInputRef = useRef(null);

  // ── Load health on mount ──
  useEffect(() => {
    checkHealth();
    loadExistingSessions();
  }, []);

  // ── Poll session status ──
  useEffect(() => {
    if (!sessionId) return;
    const poll = async () => {
      try {
        const status = await liverCloneService.getSessionStatus(sessionId);
        setSessionStatus(status);
      } catch (err) {
        console.error("[LiverClone] Poll error:", err);
      }
    };
    poll();
    pollRef.current = setInterval(poll, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [sessionId]);

  // ── Functions ──
  const checkHealth = async () => {
    try {
      const h = await liverCloneService.healthCheck();
      setHealth(h);
    } catch (err) {
      setHealth({ status: "error" });
    }
  };

  const loadExistingSessions = async () => {
    try {
      const data = await liverCloneService.listSessions();
      if (data.sessions && data.sessions.length > 0) {
        const active = data.sessions.find(
          (s) => s.status === "STREAMING" || s.status === "CONFIGURING"
        );
        if (active) {
          setSessionId(active.session_id);
          setSessionStatus(active);
        }
      }
    } catch (err) {
      console.error("[LiverClone] Failed to load sessions:", err);
    }
  };

  const handleFaceUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setSourceFaceFile(file);
    const reader = new FileReader();
    reader.onload = (ev) => setSourceFacePreview(ev.target.result);
    reader.readAsDataURL(file);
  };

  const handleCreateSession = async () => {
    setIsCreating(true);
    setError(null);
    try {
      const config = {
        source_face_url: sourceFaceUrl,
        source_face_base64: sourceFacePreview
          ? sourceFacePreview.split(",")[1]
          : undefined,
        face_swap_quality: quality,
        input_rtmp: inputRtmp,
        output_rtmp: outputRtmp,
        voice_id: voiceId,
        voice_stability: voiceStability,
        voice_similarity: voiceSimilarity,
        mode,
        vad_threshold: vadThreshold,
        silence_timeout: silenceTimeout,
        persona_name: personaName,
        persona_style: personaStyle,
        language,
        opening_script: openingScript,
        resolution,
        fps,
      };
      const result = await liverCloneService.createSession(config);
      setSessionId(result.session_id);
      setSessionStatus(result);
    } catch (err) {
      const detail = err.response?.data?.detail || err.message;
      setError(`セッション作成に失敗: ${detail}`);
    } finally {
      setIsCreating(false);
    }
  };

  const handleStartSession = async () => {
    if (!sessionId) return;
    setIsStarting(true);
    setError(null);
    try {
      const result = await liverCloneService.startSession(sessionId);
      setSessionStatus(result);
    } catch (err) {
      const detail = err.response?.data?.detail || err.message;
      setError(`配信開始に失敗: ${detail}`);
    } finally {
      setIsStarting(false);
    }
  };

  const handleStopSession = async () => {
    if (!sessionId) return;
    try {
      await liverCloneService.stopSession(sessionId);
      setSessionStatus(null);
      setSessionId(null);
    } catch (err) {
      setError("停止に失敗しました");
    }
  };

  const handleDeleteSession = async () => {
    if (!sessionId) return;
    try {
      await liverCloneService.deleteSession(sessionId);
      setSessionId(null);
      setSessionStatus(null);
    } catch (err) {
      setError("削除に失敗しました");
    }
  };

  const handleSendComment = async () => {
    if (!sessionId || !commentText.trim()) return;
    try {
      const result = await liverCloneService.respondToComment(
        sessionId,
        commentText
      );
      setCommentHistory((prev) => [
        ...prev,
        {
          comment: commentText,
          response: result.response,
          time: new Date().toLocaleTimeString(),
        },
      ]);
      setCommentText("");
    } catch (err) {
      setError("コメント返答に失敗しました");
    }
  };

  const handleSpeak = async () => {
    if (!sessionId || !speakText.trim()) return;
    try {
      await liverCloneService.pushSpeakText(sessionId, speakText);
      setSpeakText("");
    } catch (err) {
      setError("テキスト送信に失敗しました");
    }
  };

  // ── Status helpers ──
  const isStreaming =
    sessionStatus?.status === "STREAMING" ||
    sessionStatus?.state === "STREAMING";
  const isConfiguring =
    sessionStatus?.status === "CONFIGURING" ||
    sessionStatus?.state === "CONFIGURING";

  const getStatusBadge = () => {
    if (isStreaming) {
      return (
        <span className="flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-red-900/50 text-red-400">
          <div className="w-2 h-2 rounded-full bg-red-400 animate-pulse" />
          LIVE
        </span>
      );
    }
    if (isConfiguring) {
      return (
        <span className="flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-yellow-900/50 text-yellow-400">
          <Settings className="w-3 h-3" />
          設定中
        </span>
      );
    }
    return (
      <span className="flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-gray-800 text-gray-400">
        待機中
      </span>
    );
  };

  // ── Render ──
  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      {/* Header */}
      <header className="border-b border-gray-800 bg-[#0d0d14]">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate("/")}
              className="p-2 hover:bg-gray-800 rounded-lg transition"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <h1 className="text-xl font-bold flex items-center gap-2">
                <Users className="w-6 h-6 text-cyan-400" />
                Liver Clone
              </h1>
              <p className="text-sm text-gray-400">
                リアルタイム顔変換 + 声変換 ライブ配信
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {getStatusBadge()}
            {health && (
              <div
                className={`flex items-center gap-1 text-xs px-2 py-1 rounded-full ${
                  health.status === "healthy"
                    ? "bg-green-900/50 text-green-400"
                    : "bg-red-900/50 text-red-400"
                }`}
              >
                <div
                  className={`w-2 h-2 rounded-full ${
                    health.status === "healthy"
                      ? "bg-green-400"
                      : "bg-red-400"
                  }`}
                />
                GPU {health.status === "healthy" ? "Ready" : "Offline"}
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 py-6">
        {error && (
          <div className="mb-4 p-3 bg-red-900/30 border border-red-800 rounded-lg flex items-center gap-2 text-red-300">
            <AlertCircle className="w-4 h-4" />
            {error}
            <button
              onClick={() => setError(null)}
              className="ml-auto text-red-400 hover:text-red-300"
            >
              ×
            </button>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* ── Left: Stream Status & Preview ── */}
          <div className="space-y-4">
            {/* Stream Preview Placeholder */}
            <div className="bg-[#12121a] rounded-xl border border-gray-800 overflow-hidden">
              <div className="aspect-[9/16] bg-black flex items-center justify-center relative">
                {isStreaming ? (
                  <div className="text-center">
                    <Radio className="w-12 h-12 text-red-400 animate-pulse mx-auto mb-2" />
                    <p className="text-sm text-gray-400">配信中...</p>
                    <p className="text-xs text-gray-500 mt-1">
                      プラットフォームで確認してください
                    </p>
                  </div>
                ) : sourceFacePreview ? (
                  <img
                    src={sourceFacePreview}
                    alt="Source face"
                    className="w-full h-full object-cover opacity-50"
                  />
                ) : (
                  <div className="text-center">
                    <Camera className="w-12 h-12 text-gray-600 mx-auto mb-2" />
                    <p className="text-sm text-gray-500">
                      顔写真をアップロード
                    </p>
                  </div>
                )}
                {isStreaming && (
                  <div className="absolute top-3 left-3 flex items-center gap-1 bg-red-600 px-2 py-0.5 rounded text-xs font-bold">
                    <div className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                    LIVE
                  </div>
                )}
              </div>
            </div>

            {/* Metrics */}
            {isStreaming && sessionStatus?.metrics && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-4">
                <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
                  <Monitor className="w-4 h-4 text-cyan-400" />
                  配信メトリクス
                </h3>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div className="bg-gray-900 rounded-lg p-2">
                    <p className="text-gray-500">FPS</p>
                    <p className="text-lg font-bold text-green-400">
                      {sessionStatus.metrics.fps || "--"}
                    </p>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-2">
                    <p className="text-gray-500">遅延</p>
                    <p className="text-lg font-bold text-yellow-400">
                      {sessionStatus.metrics.latency_ms || "--"}ms
                    </p>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-2">
                    <p className="text-gray-500">モード</p>
                    <p className="text-sm font-bold text-cyan-400">
                      {sessionStatus.metrics.current_mode || mode}
                    </p>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-2">
                    <p className="text-gray-500">発話数</p>
                    <p className="text-lg font-bold text-purple-400">
                      {sessionStatus.metrics.speak_count || 0}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Quick Speak */}
            {isStreaming && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-4">
                <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Volume2 className="w-4 h-4 text-purple-400" />
                  手動発話
                </h3>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={speakText}
                    onChange={(e) => setSpeakText(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSpeak()}
                    placeholder="テキストを入力..."
                    className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                  />
                  <button
                    onClick={handleSpeak}
                    disabled={!speakText.trim()}
                    className="px-3 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded-lg transition"
                  >
                    <Send className="w-4 h-4" />
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* ── Right: Configuration Panel ── */}
          <div className="lg:col-span-2 space-y-4">
            {/* Tab Navigation */}
            <div className="flex gap-1 bg-[#12121a] rounded-xl border border-gray-800 p-1">
              {[
                { id: "config", label: "設定", icon: Settings },
                { id: "comments", label: "コメント", icon: MessageSquare },
                { id: "autopilot", label: "Auto Pilot", icon: Zap },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-sm transition ${
                    activeTab === tab.id
                      ? "bg-gray-800 text-white"
                      : "text-gray-400 hover:text-gray-300"
                  }`}
                >
                  <tab.icon className="w-4 h-4" />
                  {tab.label}
                </button>
              ))}
            </div>

            {/* ── Config Tab ── */}
            {activeTab === "config" && (
              <div className="space-y-4">
                {/* Face Settings */}
                <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                  <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                    <Camera className="w-4 h-4 text-cyan-400" />
                    顔設定
                  </h3>
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        ソース顔画像
                      </label>
                      <div className="flex gap-3">
                        <div
                          onClick={() => fileInputRef.current?.click()}
                          className="w-20 h-20 border-2 border-dashed border-gray-700 rounded-lg flex items-center justify-center cursor-pointer hover:border-cyan-500 transition overflow-hidden"
                        >
                          {sourceFacePreview ? (
                            <img
                              src={sourceFacePreview}
                              alt="Face"
                              className="w-full h-full object-cover"
                            />
                          ) : (
                            <Upload className="w-6 h-6 text-gray-500" />
                          )}
                        </div>
                        <input
                          ref={fileInputRef}
                          type="file"
                          accept="image/*"
                          onChange={handleFaceUpload}
                          className="hidden"
                        />
                        <div className="flex-1">
                          <input
                            type="text"
                            value={sourceFaceUrl}
                            onChange={(e) => setSourceFaceUrl(e.target.value)}
                            placeholder="または画像URLを入力..."
                            className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-cyan-500"
                          />
                          <div className="mt-2 flex gap-2">
                            <select
                              value={quality}
                              onChange={(e) => setQuality(e.target.value)}
                              className="bg-gray-900 border border-gray-700 rounded-lg px-2 py-1 text-xs"
                            >
                              <option value="fast">Fast</option>
                              <option value="balanced">Balanced</option>
                              <option value="high">High</option>
                              <option value="ultra">Ultra</option>
                            </select>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Stream Settings */}
                <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                  <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                    <Radio className="w-4 h-4 text-red-400" />
                    配信設定
                  </h3>
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        入力RTMP URL（OBSから）
                      </label>
                      <input
                        type="text"
                        value={inputRtmp}
                        onChange={(e) => setInputRtmp(e.target.value)}
                        placeholder="rtmp://your-server/live/input-key"
                        className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-red-500"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        出力RTMP URL（配信先）
                      </label>
                      <input
                        type="text"
                        value={outputRtmp}
                        onChange={(e) => setOutputRtmp(e.target.value)}
                        placeholder="rtmp://live.shopee.sg/live/stream-key"
                        className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-red-500"
                      />
                    </div>
                    <div className="flex gap-3">
                      <div className="flex-1">
                        <label className="text-xs text-gray-400 mb-1 block">
                          解像度
                        </label>
                        <select
                          value={resolution}
                          onChange={(e) => setResolution(e.target.value)}
                          className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                        >
                          <option value="480p">480p</option>
                          <option value="720p">720p</option>
                          <option value="1080p">1080p</option>
                        </select>
                      </div>
                      <div className="flex-1">
                        <label className="text-xs text-gray-400 mb-1 block">
                          FPS
                        </label>
                        <select
                          value={fps}
                          onChange={(e) => setFps(Number(e.target.value))}
                          className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                        >
                          <option value={24}>24 fps</option>
                          <option value={30}>30 fps</option>
                          <option value={60}>60 fps</option>
                        </select>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Voice Settings */}
                <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                  <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                    <Mic className="w-4 h-4 text-purple-400" />
                    音声設定
                  </h3>
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        Voice ID（ElevenLabs）
                      </label>
                      <input
                        type="text"
                        value={voiceId}
                        onChange={(e) => setVoiceId(e.target.value)}
                        placeholder="ElevenLabs Voice ID"
                        className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs text-gray-400 mb-1 block">
                          安定性: {voiceStability}
                        </label>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={voiceStability}
                          onChange={(e) =>
                            setVoiceStability(Number(e.target.value))
                          }
                          className="w-full"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-400 mb-1 block">
                          類似度: {voiceSimilarity}
                        </label>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={voiceSimilarity}
                          onChange={(e) =>
                            setVoiceSimilarity(Number(e.target.value))
                          }
                          className="w-full"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        モード
                      </label>
                      <div className="flex gap-2">
                        {[
                          { id: "manual", label: "手動", desc: "人が喋る→変換" },
                          { id: "auto", label: "自動", desc: "AI自動配信" },
                          {
                            id: "hybrid",
                            label: "ハイブリッド",
                            desc: "喋る時は変換、黙ったらAI",
                          },
                        ].map((m) => (
                          <button
                            key={m.id}
                            onClick={() => setMode(m.id)}
                            className={`flex-1 p-2 rounded-lg border text-xs text-center transition ${
                              mode === m.id
                                ? "border-cyan-500 bg-cyan-900/20 text-cyan-300"
                                : "border-gray-700 text-gray-400 hover:border-gray-600"
                            }`}
                          >
                            <p className="font-semibold">{m.label}</p>
                            <p className="text-[10px] mt-0.5 opacity-70">
                              {m.desc}
                            </p>
                          </button>
                        ))}
                      </div>
                    </div>
                    {(mode === "hybrid" || mode === "auto") && (
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="text-xs text-gray-400 mb-1 block">
                            VAD閾値: {vadThreshold}
                          </label>
                          <input
                            type="range"
                            min="0.1"
                            max="0.9"
                            step="0.05"
                            value={vadThreshold}
                            onChange={(e) =>
                              setVadThreshold(Number(e.target.value))
                            }
                            className="w-full"
                          />
                        </div>
                        <div>
                          <label className="text-xs text-gray-400 mb-1 block">
                            無音タイムアウト: {silenceTimeout}s
                          </label>
                          <input
                            type="range"
                            min="2"
                            max="15"
                            step="0.5"
                            value={silenceTimeout}
                            onChange={(e) =>
                              setSilenceTimeout(Number(e.target.value))
                            }
                            className="w-full"
                          />
                        </div>
                      </div>
                    )}
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        言語
                      </label>
                      <select
                        value={language}
                        onChange={(e) => setLanguage(e.target.value)}
                        className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                      >
                        <option value="en">English</option>
                        <option value="ja">日本語</option>
                        <option value="zh">中文</option>
                        <option value="th">ภาษาไทย</option>
                        <option value="ms">Bahasa Melayu</option>
                      </select>
                    </div>
                  </div>
                </div>

                {/* Action Buttons */}
                <div className="flex gap-3">
                  {!sessionId ? (
                    <button
                      onClick={handleCreateSession}
                      disabled={isCreating}
                      className="flex-1 flex items-center justify-center gap-2 py-3 bg-cyan-600 hover:bg-cyan-700 disabled:bg-gray-700 rounded-xl font-semibold transition"
                    >
                      {isCreating ? (
                        <Loader2 className="w-5 h-5 animate-spin" />
                      ) : (
                        <Settings className="w-5 h-5" />
                      )}
                      セッション作成
                    </button>
                  ) : !isStreaming ? (
                    <>
                      <button
                        onClick={handleStartSession}
                        disabled={isStarting}
                        className="flex-1 flex items-center justify-center gap-2 py-3 bg-red-600 hover:bg-red-700 disabled:bg-gray-700 rounded-xl font-semibold transition"
                      >
                        {isStarting ? (
                          <Loader2 className="w-5 h-5 animate-spin" />
                        ) : (
                          <Play className="w-5 h-5" />
                        )}
                        配信開始
                      </button>
                      <button
                        onClick={handleDeleteSession}
                        className="px-4 py-3 bg-gray-800 hover:bg-gray-700 rounded-xl transition"
                      >
                        <Trash2 className="w-5 h-5 text-red-400" />
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={handleStopSession}
                      className="flex-1 flex items-center justify-center gap-2 py-3 bg-gray-800 hover:bg-gray-700 border border-red-600 rounded-xl font-semibold transition"
                    >
                      <Square className="w-5 h-5 text-red-400" />
                      配信停止
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* ── Comments Tab ── */}
            {activeTab === "comments" && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                  <MessageSquare className="w-4 h-4 text-green-400" />
                  コメント返答
                </h3>
                <div className="space-y-3">
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={commentText}
                      onChange={(e) => setCommentText(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && handleSendComment()}
                      placeholder="コメントを入力して返答を生成..."
                      className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-green-500"
                    />
                    <button
                      onClick={handleSendComment}
                      disabled={!commentText.trim() || !isStreaming}
                      className="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-gray-700 rounded-lg transition"
                    >
                      <Send className="w-4 h-4" />
                    </button>
                  </div>
                  {/* Comment History */}
                  <div className="space-y-2 max-h-96 overflow-y-auto">
                    {commentHistory.length === 0 ? (
                      <p className="text-sm text-gray-500 text-center py-8">
                        コメントがまだありません
                      </p>
                    ) : (
                      commentHistory.map((item, i) => (
                        <div
                          key={i}
                          className="bg-gray-900 rounded-lg p-3 text-sm"
                        >
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-gray-400 text-xs">
                              {item.time}
                            </span>
                            <span className="text-cyan-400">
                              {item.comment}
                            </span>
                          </div>
                          <p className="text-gray-300 pl-2 border-l-2 border-purple-500">
                            {item.response}
                          </p>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* ── Auto Pilot Tab ── */}
            {activeTab === "autopilot" && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                  <Zap className="w-4 h-4 text-yellow-400" />
                  Auto Pilot設定
                </h3>
                <div className="space-y-3">
                  <div>
                    <label className="text-xs text-gray-400 mb-1 block">
                      ペルソナ名
                    </label>
                    <input
                      type="text"
                      value={personaName}
                      onChange={(e) => setPersonaName(e.target.value)}
                      placeholder="例: KYOGOKU Ryu"
                      className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-yellow-500"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-gray-400 mb-1 block">
                      話し方・スタイル
                    </label>
                    <textarea
                      value={personaStyle}
                      onChange={(e) => setPersonaStyle(e.target.value)}
                      placeholder="例: Professional yet friendly, high energy, confident..."
                      rows={3}
                      className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-yellow-500 resize-none"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-gray-400 mb-1 block">
                      オープニングスクリプト
                    </label>
                    <textarea
                      value={openingScript}
                      onChange={(e) => setOpeningScript(e.target.value)}
                      placeholder="配信開始時に自動で話す内容..."
                      rows={3}
                      className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-yellow-500 resize-none"
                    />
                  </div>
                  <p className="text-xs text-gray-500">
                    ※ Auto Pilotはハイブリッドモードで人が黙っている時に自動で台本を生成して話します。
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
