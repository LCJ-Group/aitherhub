import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  Loader2,
  Radio,
  UserCircle,
  Mic,
  Video,
  MessageSquare,
  Play,
  Square,
  Sparkles,
  Volume2,
  Settings,
  Send,
  X,
} from "lucide-react";
import digitalHumanService from "../base/services/digitalHumanService";

/**
 * DigitalHumanPage — Digital Human Livestream Management
 *
 * Features:
 *  1. Health dashboard (ElevenLabs, Tencent, GPU)
 *  2. Create livestream room (with script generation)
 *  3. Monitor active rooms
 *  4. Real-time takeover (interjection)
 *  5. Face swap stream management
 */
export default function DigitalHumanPage() {
  const navigate = useNavigate();

  // Health
  const [health, setHealth] = useState(null);
  const [healthLoading, setHealthLoading] = useState(false);

  // Voices
  const [voices, setVoices] = useState([]);
  const [selectedVoice, setSelectedVoice] = useState("");

  // Active rooms
  const [rooms, setRooms] = useState([]);
  const [roomsLoading, setRoomsLoading] = useState(false);

  // Create room form
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createForm, setCreateForm] = useState({
    video_id: "",
    product_focus: "",
    tone: "professional_friendly",
    language: "ja",
    use_hybrid_voice: true,
    cycle_times: 3,
  });
  const [creating, setCreating] = useState(false);

  // Script generation
  const [generatedScript, setGeneratedScript] = useState(null);
  const [generatingScript, setGeneratingScript] = useState(false);

  // Takeover
  const [takeoverRoom, setTakeoverRoom] = useState(null);
  const [takeoverText, setTakeoverText] = useState("");
  const [takeoverSending, setTakeoverSending] = useState(false);

  // Face swap stream
  const [faceSwapStatus, setFaceSwapStatus] = useState(null);

  // Error / success
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  const clearMessages = () => { setError(null); setSuccess(null); };

  // ─── Load health ───
  const loadHealth = useCallback(async () => {
    setHealthLoading(true);
    try {
      const data = await digitalHumanService.fullHealth();
      setHealth(data);
    } catch (e) {
      console.error("Health check failed:", e);
      setError(window.__t('digitalHumanPage_cda582', 'ヘルスチェックに失敗しました'));
    } finally {
      setHealthLoading(false);
    }
  }, []);

  // ─── Load voices ───
  const loadVoices = useCallback(async () => {
    try {
      const data = await digitalHumanService.getVoices();
      setVoices(data.voices || []);
      if (data.voices?.length > 0 && !selectedVoice) {
        setSelectedVoice(data.voices[0].voice_id);
      }
    } catch (e) {
      console.error("Failed to load voices:", e);
    }
  }, [selectedVoice]);

  // ─── Load rooms ───
  const loadRooms = useCallback(async () => {
    setRoomsLoading(true);
    try {
      const data = await digitalHumanService.listLiverooms();
      setRooms(data.liverooms || data.rooms || []);
    } catch (e) {
      console.error("Failed to load rooms:", e);
    } finally {
      setRoomsLoading(false);
    }
  }, []);

  // ─── Initial load ───
  useEffect(() => {
    loadHealth();
    loadVoices();
    loadRooms();
  }, [loadHealth, loadVoices, loadRooms]);

  // ─── Auto-refresh rooms ───
  useEffect(() => {
    const interval = setInterval(loadRooms, 15000);
    return () => clearInterval(interval);
  }, [loadRooms]);

  // ─── Generate script ───
  const handleGenerateScript = async () => {
    if (!createForm.video_id) {
      setError(window.__t('digitalHumanPage_5eff91', '動画IDを入力してください'));
      return;
    }
    setGeneratingScript(true);
    clearMessages();
    try {
      const data = await digitalHumanService.generateScript({
        video_id: createForm.video_id,
        product_focus: createForm.product_focus || undefined,
        tone: createForm.tone,
        language: createForm.language,
      });
      setGeneratedScript(data);
      setSuccess(window.__t('digitalHumanPage_b33d4d', '台本を生成しました'));
    } catch (e) {
      setError(e.response?.data?.detail || window.__t('digitalHumanPage_181728', '台本生成に失敗しました'));
    } finally {
      setGeneratingScript(false);
    }
  };

  // ─── Create room ───
  const handleCreateRoom = async () => {
    setCreating(true);
    clearMessages();
    try {
      const params = {
        ...createForm,
        video_id: createForm.video_id || undefined,
        product_focus: createForm.product_focus || undefined,
        elevenlabs_voice_id: selectedVoice || undefined,
        scripts: generatedScript?.scripts || undefined,
      };
      const data = await digitalHumanService.createLiveroom(params);
      setSuccess(`ライブルーム作成成功: ${data.liveroom_id || "OK"}`);
      setShowCreateForm(false);
      loadRooms();
    } catch (e) {
      setError(e.response?.data?.detail || window.__t('digitalHumanPage_c7b350', 'ライブルーム作成に失敗しました'));
    } finally {
      setCreating(false);
    }
  };

  // ─── Close room ───
  const handleCloseRoom = async (roomId) => {
    clearMessages();
    try {
      await digitalHumanService.closeLiveroom(roomId);
      setSuccess(`ルーム ${roomId} を閉じました`);
      loadRooms();
    } catch (e) {
      setError(e.response?.data?.detail || window.__t('digitalHumanPage_bdc030', 'ルームの閉鎖に失敗しました'));
    }
  };

  // ─── Takeover ───
  const handleTakeover = async () => {
    if (!takeoverRoom || !takeoverText.trim()) return;
    setTakeoverSending(true);
    clearMessages();
    try {
      await digitalHumanService.takeover(takeoverRoom, {
        content: takeoverText.trim(),
        language: "ja",
        use_hybrid_voice: true,
        elevenlabs_voice_id: selectedVoice || undefined,
      });
      setSuccess(window.__t('digitalHumanPage_8ae132', 'テイクオーバーを送信しました'));
      setTakeoverText("");
    } catch (e) {
      setError(e.response?.data?.detail || window.__t('digitalHumanPage_580fa7', 'テイクオーバーに失敗しました'));
    } finally {
      setTakeoverSending(false);
    }
  };

  // ─── Status badge ───
  const StatusBadge = ({ status, label }) => {
    const colors = {
      ok: "bg-green-100 text-green-700",
      error: "bg-red-100 text-red-700",
      unknown: "bg-gray-100 text-gray-500",
      running: "bg-blue-100 text-blue-700",
      idle: "bg-gray-100 text-gray-500",
    };
    return (
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${colors[status] || colors.unknown}`}>
        {status === "ok" && <CheckCircle className="w-3 h-3" />}
        {status === "error" && <AlertCircle className="w-3 h-3" />}
        {label || status}
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-3">
          <button onClick={() => navigate("/")} className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <UserCircle className="w-6 h-6 text-purple-500" />
          <h1 className="text-lg font-bold bg-gradient-to-r from-purple-600 to-blue-600 bg-clip-text text-transparent">
            Digital Human
          </h1>
          <span className="text-xs text-gray-400 ml-1">AI Livestream</span>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => { loadHealth(); loadRooms(); }}
              disabled={healthLoading}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <RefreshCw className={`w-4 h-4 ${healthLoading ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">
        {/* Messages */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-sm text-red-700">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{typeof error === 'string' ? error : JSON.stringify(error)}</span>
            <button onClick={() => setError(null)} className="ml-auto"><X className="w-4 h-4" /></button>
          </div>
        )}
        {success && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 flex items-center gap-2 text-sm text-green-700">
            <CheckCircle className="w-4 h-4 shrink-0" />
            <span>{success}</span>
            <button onClick={() => setSuccess(null)} className="ml-auto"><X className="w-4 h-4" /></button>
          </div>
        )}

        {/* ═══ Health Dashboard ═══ */}
        <div className="bg-white rounded-xl border shadow-sm p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
            <Settings className="w-4 h-4 text-gray-400" />
            システム状態
          </h2>
          {health ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {/* ElevenLabs */}
              <div className="border rounded-lg p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-gray-500 flex items-center gap-1">
                    <Volume2 className="w-3 h-3" /> ElevenLabs TTS
                  </span>
                  <StatusBadge status={health.elevenlabs?.status} />
                </div>
                <p className="text-xs text-gray-400">
                  {health.elevenlabs?.total_voices || 0} voices / {health.elevenlabs?.cloned_voices || 0} cloned
                </p>
              </div>
              {/* Tencent */}
              <div className="border rounded-lg p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-gray-500 flex items-center gap-1">
                    <UserCircle className="w-3 h-3" /> Tencent IVH
                  </span>
                  <StatusBadge status={health.tencent?.status} />
                </div>
                {health.tencent?.error && (
                  <p className="text-xs text-red-400 truncate">{health.tencent.error}</p>
                )}
              </div>
              {/* Capabilities */}
              <div className="border rounded-lg p-3 space-y-2">
                <span className="text-xs font-medium text-gray-500 flex items-center gap-1">
                  <Sparkles className="w-3 h-3" /> 機能
                </span>
                <div className="flex flex-wrap gap-1">
                  {health.capabilities && Object.entries(health.capabilities).map(([k, v]) => (
                    <span key={k} className={`text-[10px] px-1.5 py-0.5 rounded ${v ? "bg-green-50 text-green-600" : "bg-gray-50 text-gray-400"}`}>
                      {k.replace(/_/g, " ")}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center py-8 text-gray-400 text-sm">
              {healthLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : window.__t('common_loading', '読み込み中...')}
            </div>
          )}
        </div>

        {/* ═══ Active Rooms ═══ */}
        <div className="bg-white rounded-xl border shadow-sm p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
              <Radio className="w-4 h-4 text-red-400" />
              アクティブルーム
              {rooms.length > 0 && (
                <span className="bg-red-100 text-red-600 text-xs px-2 py-0.5 rounded-full">{rooms.length}</span>
              )}
            </h2>
            <button
              onClick={() => setShowCreateForm(!showCreateForm)}
              className="px-3 py-1.5 text-xs font-medium text-white bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg hover:opacity-90 transition-opacity"
            >
              + 新規ルーム
            </button>
          </div>

          {rooms.length === 0 ? (
            <div className="text-center py-8 text-gray-400 text-sm">
              {roomsLoading ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : window.__t('digitalHumanPage_a3c823', 'アクティブなルームはありません')}
            </div>
          ) : (
            <div className="space-y-3">
              {rooms.map((room) => (
                <div key={room.liveroom_id || room.id} className="border rounded-lg p-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Radio className="w-4 h-4 text-red-500 animate-pulse" />
                      <span className="text-sm font-medium">{room.liveroom_id || room.id}</span>
                      <StatusBadge status={room.status || "running"} />
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setTakeoverRoom(room.liveroom_id || room.id)}
                        className="px-2 py-1 text-xs bg-blue-50 text-blue-600 rounded hover:bg-blue-100 transition-colors flex items-center gap-1"
                      >
                        <MessageSquare className="w-3 h-3" /> テイクオーバー
                      </button>
                      <button
                        onClick={() => handleCloseRoom(room.liveroom_id || room.id)}
                        className="px-2 py-1 text-xs bg-red-50 text-red-600 rounded hover:bg-red-100 transition-colors flex items-center gap-1"
                      >
                        <Square className="w-3 h-3" /> 停止
                      </button>
                    </div>
                  </div>
                  {room.created_at && (
                    <p className="text-xs text-gray-400 mt-1">{window.__t('digitalHumanPage_5d03ed', '開始')}: {new Date(room.created_at).toLocaleString("ja-JP")}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ═══ Takeover Panel ═══ */}
        {takeoverRoom && (
          <div className="bg-white rounded-xl border shadow-sm p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
                <MessageSquare className="w-4 h-4 text-blue-400" />
                テイクオーバー — {takeoverRoom}
              </h2>
              <button onClick={() => setTakeoverRoom(null)} className="p-1 hover:bg-gray-100 rounded">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={takeoverText}
                onChange={(e) => setTakeoverText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleTakeover()}
                placeholder={window.__t('digitalHumanPage_0da8ab', 'ライブ配信に割り込むテキストを入力...')}
                className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300"
              />
              <button
                onClick={handleTakeover}
                disabled={takeoverSending || !takeoverText.trim()}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors flex items-center gap-1"
              >
                {takeoverSending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                送信
              </button>
            </div>
          </div>
        )}

        {/* ═══ Create Room Form ═══ */}
        {showCreateForm && (
          <div className="bg-white rounded-xl border shadow-sm p-5">
            <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
              <Play className="w-4 h-4 text-green-500" />
              新規ライブルーム作成
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Video ID */}
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">{window.__t('digitalHumanPage_13d768', '動画ID（分析データ連携）')}</label>
                <input
                  type="text"
                  value={createForm.video_id}
                  onChange={(e) => setCreateForm({ ...createForm, video_id: e.target.value })}
                  placeholder={window.__t('digitalHumanPage_869740', 'AitherHub動画ID')}
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300"
                />
              </div>
              {/* Product Focus */}
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">{window.__t('analyticsSection_de54', '商品名')}</label>
                <input
                  type="text"
                  value={createForm.product_focus}
                  onChange={(e) => setCreateForm({ ...createForm, product_focus: e.target.value })}
                  placeholder={window.__t('digitalHumanPage_e5639e', '強調する商品名')}
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300"
                />
              </div>
              {/* Tone */}
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">{window.__t('scriptGen_toneLabel', 'トーン')}</label>
                <select
                  value={createForm.tone}
                  onChange={(e) => setCreateForm({ ...createForm, tone: e.target.value })}
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300"
                >
                  <option value="professional_friendly">{window.__t('autoVideoPage_faaee7', 'プロフェッショナル')}</option>
                  <option value="energetic">{window.__t('autoVideoPage_3f558c', 'エネルギッシュ')}</option>
                  <option value="calm">{window.__t('autoVideoPage_561ecb', '落ち着いた')}</option>
                </select>
              </div>
              {/* Language */}
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">{window.__t('script_language', '言語')}</label>
                <select
                  value={createForm.language}
                  onChange={(e) => setCreateForm({ ...createForm, language: e.target.value })}
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300"
                >
                  <option value="ja">{window.__t('language_japanese', '日本語')}</option>
                  <option value="en">English</option>
                  <option value="zh">{window.__t('scriptGen_langZh', '中文')}</option>
                </select>
              </div>
              {/* Voice */}
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">{window.__t('digitalHumanPage_52fe83', '声')}</label>
                <select
                  value={selectedVoice}
                  onChange={(e) => setSelectedVoice(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300"
                >
                  {voices.map((v) => (
                    <option key={v.voice_id} value={v.voice_id}>
                      {v.name} {v.labels?.accent ? `(${v.labels.accent})` : ""}
                    </option>
                  ))}
                </select>
              </div>
              {/* Cycle times */}
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">{window.__t('digitalHumanPage_99ea28', 'ループ回数')}</label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={createForm.cycle_times}
                  onChange={(e) => setCreateForm({ ...createForm, cycle_times: parseInt(e.target.value) || 3 })}
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300"
                />
              </div>
              {/* Hybrid voice toggle */}
              <div className="flex items-center gap-2 col-span-full">
                <input
                  type="checkbox"
                  id="hybrid-voice"
                  checked={createForm.use_hybrid_voice}
                  onChange={(e) => setCreateForm({ ...createForm, use_hybrid_voice: e.target.checked })}
                  className="rounded border-gray-300"
                />
                <label htmlFor="hybrid-voice" className="text-xs text-gray-600">
                  ElevenLabsボイスクローンを使用（日本語対応）
                </label>
              </div>
            </div>

            {/* Script generation */}
            <div className="mt-4 pt-4 border-t">
              <div className="flex items-center gap-2 mb-2">
                <button
                  onClick={handleGenerateScript}
                  disabled={generatingScript || !createForm.video_id}
                  className="px-3 py-1.5 text-xs font-medium bg-purple-50 text-purple-600 rounded-lg hover:bg-purple-100 disabled:opacity-50 transition-colors flex items-center gap-1"
                >
                  {generatingScript ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                  台本をAI生成
                </button>
                {generatedScript && (
                  <span className="text-xs text-green-600 flex items-center gap-1">
                    <CheckCircle className="w-3 h-3" />
                    {generatedScript.scripts?.length || 0} セクション生成済み
                  </span>
                )}
              </div>
              {generatedScript?.scripts && (
                <div className="bg-gray-50 rounded-lg p-3 max-h-40 overflow-y-auto">
                  {generatedScript.scripts.map((s, i) => (
                    <p key={i} className="text-xs text-gray-600 mb-1">
                      <span className="font-medium text-gray-500">[{i + 1}]</span> {s.length > 100 ? s.slice(0, 100) + "..." : s}
                    </p>
                  ))}
                </div>
              )}
            </div>

            {/* Submit */}
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setShowCreateForm(false)}
                className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              >
                キャンセル
              </button>
              <button
                onClick={handleCreateRoom}
                disabled={creating}
                className="px-4 py-2 text-sm font-medium text-white bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1"
              >
                {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                ライブ開始
              </button>
            </div>
          </div>
        )}

        {/* ═══ Face Swap Stream ═══ */}
        <div className="bg-white rounded-xl border shadow-sm p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
            <Video className="w-4 h-4 text-green-400" />
            Face Swap ストリーム
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button
              onClick={async () => {
                try {
                  const data = await digitalHumanService.getFaceSwapStreamStatus();
                  setFaceSwapStatus(data);
                } catch (e) {
                  setError(window.__t('digitalHumanPage_ef721b', 'Face Swapステータス取得に失敗しました'));
                }
              }}
              className="border rounded-lg p-3 text-left hover:bg-gray-50 transition-colors"
            >
              <span className="text-xs font-medium text-gray-500 flex items-center gap-1">
                <RefreshCw className="w-3 h-3" /> ステータス確認
              </span>
              {faceSwapStatus && (
                <div className="mt-2 text-xs text-gray-600">
                  <p>{window.__t('digitalHumanPage_9aeabc', '状態:')} <StatusBadge status={faceSwapStatus.stream_status || faceSwapStatus.status || "unknown"} /></p>
                  {faceSwapStatus.session_id && <p className="mt-1">Session: {faceSwapStatus.session_id}</p>}
                </div>
              )}
            </button>
            <button
              onClick={async () => {
                try {
                  const data = await digitalHumanService.faceSwapHealth();
                  setFaceSwapStatus(data);
                  setSuccess("GPU Worker: OK");
                } catch (e) {
                  setError(window.__t('digitalHumanPage_7a8156', 'Face Swap GPU Worker接続失敗'));
                }
              }}
              className="border rounded-lg p-3 text-left hover:bg-gray-50 transition-colors"
            >
              <span className="text-xs font-medium text-gray-500 flex items-center gap-1">
                <Mic className="w-3 h-3" /> GPU Worker ヘルス
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
