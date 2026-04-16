import { useState, useEffect, useRef, useCallback } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import {
  Loader2,
  Send,
  StopCircle,
  Wifi,
  WifiOff,
  Mic,
  MicOff,
  Volume2,
  VolumeX,
  Radio,
  AlertCircle,
  Square,
} from "lucide-react";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";

/**
 * HeyGen Streaming Avatar Component
 *
 * Provides real-time avatar streaming via HeyGen's Streaming API + LiveKit WebRTC.
 * User types text → avatar speaks it in real-time.
 *
 * Props:
 *   - avatarId: Selected HeyGen avatar ID
 *   - voiceId: Selected voice ID (optional)
 *   - language: Language code (e.g., "ja")
 *   - onStreamReady: Callback when stream is ready (receives MediaStream)
 *   - onError: Callback for errors
 *   - className: Additional CSS classes
 */
export default function HeyGenStreamingAvatar({
  avatarId,
  voiceId = "",
  language = "ja",
  onStreamReady,
  onError,
  className = "",
}) {
  // ── State ──
  const [sessionId, setSessionId] = useState(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [speakText, setSpeakText] = useState("");
  const [speakHistory, setSpeakHistory] = useState([]);
  const [error, setError] = useState(null);
  const [sessionDuration, setSessionDuration] = useState(0);
  const [sessionLimit, setSessionLimit] = useState(600);

  // ── Refs ──
  const videoRef = useRef(null);
  const roomRef = useRef(null);
  const sessionTimerRef = useRef(null);
  const textInputRef = useRef(null);

  // ── Cleanup on unmount ──
  useEffect(() => {
    return () => {
      stopSession();
    };
  }, []);

  // ── Session duration timer ──
  useEffect(() => {
    if (isConnected) {
      sessionTimerRef.current = setInterval(() => {
        setSessionDuration((prev) => {
          const next = prev + 1;
          // Auto-stop 30s before limit
          if (next >= sessionLimit - 30) {
            console.warn("[Streaming] Approaching session limit, auto-stopping...");
            stopSession();
          }
          return next;
        });
      }, 1000);
    } else {
      if (sessionTimerRef.current) {
        clearInterval(sessionTimerRef.current);
        sessionTimerRef.current = null;
      }
      setSessionDuration(0);
    }
    return () => {
      if (sessionTimerRef.current) {
        clearInterval(sessionTimerRef.current);
      }
    };
  }, [isConnected, sessionLimit]);

  // ── Start Session ──
  const startSession = useCallback(async () => {
    if (!avatarId) {
      setError(window.__t('auto_311', 'アバターを選択してください'));
      return;
    }

    setIsConnecting(true);
    setError(null);

    try {
      // 1. Create streaming session via backend
      const result = await aiLiveCreatorService.heygenStreamingStart({
        avatar_id: avatarId,
        voice_id: voiceId,
        quality: "medium",
        language: language,
      });

      if (!result.success) {
        throw new Error(result.error || "Failed to start streaming session");
      }

      const { session_id, access_token, url, session_duration_limit } = result;
      setSessionId(session_id);
      setSessionLimit(session_duration_limit || 600);

      // 2. Connect to LiveKit room
      const room = new Room({
        adaptiveStream: true,
        dynacast: true,
      });
      roomRef.current = room;

      // Handle track subscriptions
      room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        console.log("[Streaming] Track subscribed:", track.kind, track.source);
        if (track.kind === Track.Kind.Video) {
          const element = track.attach();
          element.style.width = "100%";
          element.style.height = "100%";
          element.style.objectFit = "cover";
          element.style.borderRadius = "inherit";
          element.autoplay = true;
          element.playsInline = true;

          if (videoRef.current) {
            // Clear previous content
            videoRef.current.innerHTML = "";
            videoRef.current.appendChild(element);
          }

          // Notify parent about stream
          if (onStreamReady && track.mediaStream) {
            onStreamReady(track.mediaStream);
          }
        }
        if (track.kind === Track.Kind.Audio) {
          const element = track.attach();
          element.autoplay = true;
          element.volume = 1.0;
          // Append audio element to body (hidden)
          document.body.appendChild(element);
        }
      });

      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((el) => el.remove());
      });

      room.on(RoomEvent.Disconnected, () => {
        console.log("[Streaming] Disconnected from room");
        setIsConnected(false);
        setSessionId(null);
      });

      room.on(RoomEvent.Reconnecting, () => {
        console.log("[Streaming] Reconnecting...");
      });

      room.on(RoomEvent.Reconnected, () => {
        console.log("[Streaming] Reconnected");
      });

      // Connect to the room
      await room.connect(url, access_token);
      console.log("[Streaming] Connected to LiveKit room");

      setIsConnected(true);
      setIsConnecting(false);

    } catch (err) {
      console.error("[Streaming] Error starting session:", err);
      setError(err.message || "Failed to start streaming session");
      setIsConnecting(false);
      if (onError) onError(err);
    }
  }, [avatarId, voiceId, language, onStreamReady, onError]);

  // ── Stop Session ──
  const stopSession = useCallback(async () => {
    try {
      // Disconnect LiveKit room
      if (roomRef.current) {
        roomRef.current.disconnect();
        roomRef.current = null;
      }

      // Stop session on backend
      if (sessionId) {
        try {
          await aiLiveCreatorService.heygenStreamingStop({ session_id: sessionId });
        } catch (e) {
          console.warn("[Streaming] Error stopping session on backend:", e);
        }
      }
    } catch (e) {
      console.warn("[Streaming] Error during cleanup:", e);
    }

    setIsConnected(false);
    setSessionId(null);
    setIsSpeaking(false);

    // Clear video
    if (videoRef.current) {
      videoRef.current.innerHTML = "";
    }
  }, [sessionId]);

  // ── Speak ──
  const handleSpeak = useCallback(async () => {
    if (!sessionId || !speakText.trim()) return;

    setIsSpeaking(true);
    try {
      await aiLiveCreatorService.heygenStreamingSpeak({
        session_id: sessionId,
        text: speakText.trim(),
        task_type: "repeat",
      });

      // Add to history
      setSpeakHistory((prev) => [
        { text: speakText.trim(), timestamp: new Date().toLocaleTimeString() },
        ...prev,
      ].slice(0, 50));

      setSpeakText("");
      // Focus back on input
      if (textInputRef.current) {
        textInputRef.current.focus();
      }
    } catch (err) {
      console.error("[Streaming] Error speaking:", err);
      setError(err.message || "Failed to send text");
    } finally {
      // Speaking animation continues until avatar finishes
      setTimeout(() => setIsSpeaking(false), 1000);
    }
  }, [sessionId, speakText]);

  // ── Interrupt ──
  const handleInterrupt = useCallback(async () => {
    if (!sessionId) return;
    try {
      await aiLiveCreatorService.heygenStreamingInterrupt({ session_id: sessionId });
      setIsSpeaking(false);
    } catch (err) {
      console.error("[Streaming] Error interrupting:", err);
    }
  }, [sessionId]);

  // ── Key handler ──
  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSpeak();
    }
  };

  // ── Format time ──
  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  return (
    <div className={`flex flex-col gap-3 ${className}`}>
      {/* ── Video Display ── */}
      <div className="relative bg-gray-900 rounded-xl overflow-hidden" style={{ aspectRatio: "9/16", maxHeight: "500px" }}>
        <div ref={videoRef} className="w-full h-full" />

        {/* Overlay: Not connected */}
        {!isConnected && !isConnecting && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-900/80">
            <Radio className="w-12 h-12 text-gray-600 mb-3" />
            <p className="text-sm text-gray-400 mb-1">Realtime Streaming</p>
            <p className="text-xs text-gray-500">{window.__t('auto_312', 'アバターを選択して接続開始')}</p>
          </div>
        )}

        {/* Overlay: Connecting */}
        {isConnecting && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-900/80">
            <Loader2 className="w-10 h-10 text-green-400 animate-spin mb-3" />
            <p className="text-sm text-green-300">{window.__t('auto_342', '接続中...')}</p>
            <p className="text-xs text-gray-400 mt-1">{window.__t('auto_301', 'LiveKit WebRTC セッション確立中')}</p>
          </div>
        )}

        {/* Status bar */}
        {isConnected && (
          <div className="absolute top-2 left-2 right-2 flex items-center justify-between">
            <div className="flex items-center gap-1.5 bg-green-500/20 backdrop-blur-sm px-2 py-1 rounded-full border border-green-500/30">
              <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
              <span className="text-[10px] text-green-300 font-medium">LIVE</span>
            </div>
            <div className="flex items-center gap-1.5 bg-black/40 backdrop-blur-sm px-2 py-1 rounded-full">
              <span className="text-[10px] text-gray-300 font-mono">
                {formatTime(sessionDuration)} / {formatTime(sessionLimit)}
              </span>
            </div>
          </div>
        )}

        {/* Speaking indicator */}
        {isConnected && isSpeaking && (
          <div className="absolute bottom-2 left-2 right-2 flex justify-center">
            <div className="flex items-center gap-1.5 bg-blue-500/20 backdrop-blur-sm px-3 py-1.5 rounded-full border border-blue-500/30">
              <Volume2 className="w-3.5 h-3.5 text-blue-400 animate-pulse" />
              <span className="text-[10px] text-blue-300">Speaking...</span>
            </div>
          </div>
        )}
      </div>

      {/* ── Connection Controls ── */}
      <div className="flex gap-2">
        {!isConnected ? (
          <button
            onClick={startSession}
            disabled={isConnecting || !avatarId}
            className={`flex-1 py-2.5 px-4 rounded-lg font-medium text-xs flex items-center justify-center gap-2 transition-all ${
              !isConnecting && avatarId
                ? "bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-600 hover:to-emerald-700 text-white shadow-lg shadow-green-500/20"
                : "bg-gray-700/50 text-gray-500 cursor-not-allowed"
            }`}
          >
            {isConnecting ? (
              <><Loader2 className="w-3.5 h-3.5 animate-spin" />{window.__t('auto_342', '接続中...')}</>
            ) : (
              <><Wifi className="w-3.5 h-3.5" />{window.__t('auto_314', 'ストリーミング開始')}</>
            )}
          </button>
        ) : (
          <button
            onClick={stopSession}
            className="flex-1 py-2.5 px-4 rounded-lg font-medium text-xs flex items-center justify-center gap-2 transition-all bg-gradient-to-r from-red-500 to-rose-600 hover:from-red-600 hover:to-rose-700 text-white shadow-lg shadow-red-500/20"
          >
            <StopCircle className="w-3.5 h-3.5" />ストリーミング停止
          </button>
        )}
      </div>

      {/* ── Text Input (only when connected) ── */}
      {isConnected && (
        <div className="bg-gray-800/50 rounded-xl border border-gray-700/30 p-3">
          <div className="flex gap-2">
            <textarea
              ref={textInputRef}
              value={speakText}
              onChange={(e) => setSpeakText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={window.__t('auto_317', 'テキストを入力してEnterで送信...')}
              rows={2}
              className="flex-1 px-3 py-2 bg-gray-900/50 border border-gray-700/30 rounded-lg text-sm text-gray-200 outline-none focus:border-green-500/50 resize-none placeholder-gray-500"
            />
            <div className="flex flex-col gap-1">
              <button
                onClick={handleSpeak}
                disabled={!speakText.trim() || isSpeaking}
                className={`px-3 py-2 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all ${
                  speakText.trim() && !isSpeaking
                    ? "bg-green-500 hover:bg-green-600 text-white"
                    : "bg-gray-700/50 text-gray-500 cursor-not-allowed"
                }`}
                title={window.__t('auto_352', '送信 (Enter)')}
              >
                {isSpeaking ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Send className="w-3.5 h-3.5" />
                )}
              </button>
              <button
                onClick={handleInterrupt}
                className="px-3 py-2 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all bg-orange-500/20 hover:bg-orange-500/30 text-orange-400 border border-orange-500/30"
                title={window.__t('auto_327', '中断')}
              >
                <Square className="w-3 h-3" />
              </button>
            </div>
          </div>
          <p className="text-[9px] text-gray-500 mt-1.5">
            Enter: 送信 | Shift+Enter: 改行 | 中断ボタン: 現在の発話を停止
          </p>
        </div>
      )}

      {/* ── Error ── */}
      {error && (
        <div className="p-2.5 bg-red-900/30 border border-red-700/50 rounded-lg flex items-start gap-2">
          <AlertCircle className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />
          <p className="text-[11px] text-red-300 flex-1">{error}</p>
          <button onClick={() => setError(null)} className="text-red-500 hover:text-red-400">
            <span className="text-xs">x</span>
          </button>
        </div>
      )}

      {/* ── Speak History ── */}
      {speakHistory.length > 0 && (
        <div className="bg-gray-800/50 rounded-xl border border-gray-700/30 p-3 max-h-40 overflow-y-auto" style={{ scrollbarWidth: "thin" }}>
          <h5 className="text-[10px] font-medium text-gray-400 mb-2">{window.__t('auto_349', '発話履歴')}</h5>
          <div className="space-y-1.5">
            {speakHistory.map((item, i) => (
              <div key={i} className="flex items-start gap-2">
                <span className="text-[9px] text-gray-500 font-mono shrink-0 mt-0.5">{item.timestamp}</span>
                <p className="text-[11px] text-gray-300">{item.text}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
