import { useState, useEffect, useRef, useCallback } from "react";
import { Room, RoomEvent, Track, DataPacket_Kind } from "livekit-client";
import {
  Loader2,
  Send,
  StopCircle,
  Wifi,
  WifiOff,
  Volume2,
  VolumeX,
  Radio,
  AlertCircle,
  Square,
  MessageSquare,
} from "lucide-react";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";

/**
 * LiveAvatar Streaming Component
 *
 * Provides real-time avatar streaming via LiveAvatar FULL Mode + LiveKit WebRTC.
 * User types text → sends via LiveKit data channel → avatar speaks it in real-time.
 *
 * Two-step session flow:
 *   1. Backend calls LiveAvatar /v1/sessions/token → session_token
 *   2. Backend calls LiveAvatar /v1/sessions/start → livekit_url + livekit_client_token
 *   3. Frontend connects to LiveKit room using livekit_url + livekit_client_token
 *   4. Text is sent via LiveKit data channel (topic: "agent-control")
 *
 * Props:
 *   - avatarId: Selected LiveAvatar avatar UUID (optional, defaults to Ann Therapist)
 *   - language: Language code (e.g., "ja")
 *   - personaPrompt: System prompt for the avatar persona
 *   - voiceId: Optional voice ID override (ElevenLabs)
 *   - sandbox: Use sandbox mode (free, 1-min sessions)
 *   - onStreamReady: Callback when stream is ready (receives MediaStream)
 *   - onError: Callback for errors
 *   - className: Additional CSS classes
 */
export default function LiveAvatarStreaming({
  avatarId = "",
  language = "ja",
  personaPrompt = "",
  voiceId = "",
  sandbox = false,
  hideVideo = false,
  onStreamReady,
  onDisconnect,
  onError,
  className = "",
}) {
  // ── State ──
  const [sessionId, setSessionId] = useState(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [speakText, setSpeakText] = useState("");
  const [speakHistory, setSpeakHistory] = useState([]);
  const [error, setError] = useState(null);
  const [sessionDuration, setSessionDuration] = useState(0);
  const [maxDuration, setMaxDuration] = useState(1200);
  const [connectionStatus, setConnectionStatus] = useState("disconnected"); // disconnected, connecting, connected, reconnecting

  // ── Refs ──
  const videoRef = useRef(null);
  const roomRef = useRef(null);
  const wsRef = useRef(null); // WebSocket for command events
  const sessionTimerRef = useRef(null);
  const textInputRef = useRef(null);
  const sessionIdRef = useRef(null);
  const audioElementsRef = useRef([]); // Track audio elements for cleanup

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
        setSessionDuration((prev) => prev + 1);
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
  }, [isConnected]);

  // Generate UUID-style event ID (matching LiveAvatar SDK format)
  const generateEventId = () => {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  };

  // ── Send event via WebSocket (preferred) or LiveKit data channel (fallback) ──
  // Matches official @heygen/liveavatar-web-sdk sendCommandEvent behavior:
  //   1. Try WebSocket first (if connected)
  //   2. Fall back to LiveKit data channel
  const sendEvent = useCallback((eventType, extraData = {}) => {
    const ws = wsRef.current;
    const room = roomRef.current;
    const eventId = generateEventId();

    // Build the LiveKit data channel format
    const livekitEvent = {
      event_id: eventId,
      event_type: eventType,
      ...extraData,
    };

    // Try WebSocket first (like official SDK)
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        // Map event types to WebSocket format
        let wsMessage;
        if (eventType === "avatar.speak_text") {
          // For speak_text, send via BOTH WebSocket and LiveKit data channel
          // WebSocket doesn't handle speak_text in SDK, so use LiveKit
          // But first try WebSocket with agent.speak format
          wsMessage = null; // speak_text is not handled via WS in SDK
        } else if (eventType === "avatar.interrupt") {
          wsMessage = { type: "agent.interrupt", event_id: eventId };
        } else if (eventType === "avatar.start_listening") {
          wsMessage = { type: "agent.start_listening", event_id: eventId };
        } else if (eventType === "avatar.stop_listening") {
          wsMessage = { type: "agent.stop_listening", event_id: eventId };
        }

        if (wsMessage) {
          ws.send(JSON.stringify(wsMessage));
          console.log("[LiveAvatar] Sent via WebSocket:", wsMessage.type, JSON.stringify(wsMessage));
          return eventId;
        }
      } catch (err) {
        console.warn("[LiveAvatar] WebSocket send failed, falling back to LiveKit:", err);
      }
    }

    // Fall back to LiveKit data channel
    if (!room || !room.localParticipant) {
      console.warn("[LiveAvatar] Cannot send event: not connected. Room state:", room?.state);
      return;
    }

    if (room.state !== "connected") {
      console.warn("[LiveAvatar] Room not in connected state:", room.state);
      return;
    }

    const encoder = new TextEncoder();
    const data = encoder.encode(JSON.stringify(livekitEvent));

    try {
      // Log participant permissions for debugging
      const perms = room.localParticipant.permissions;
      console.log("[LiveAvatar] Participant permissions:", JSON.stringify(perms));
      console.log("[LiveAvatar] Room state:", room.state, "Participant identity:", room.localParticipant.identity);

      room.localParticipant.publishData(data, {
        reliable: true,
        topic: "agent-control",
      });
      console.log("[LiveAvatar] Sent via LiveKit data channel:", eventType, JSON.stringify(livekitEvent));
    } catch (err) {
      console.error("[LiveAvatar] Failed to publish data:", err);
      console.error("[LiveAvatar] Error details:", err.message, err.stack);
    }
    return eventId;
  }, []);

  // ── Handle incoming events from avatar ──
  const handleDataReceived = useCallback((payload, participant, kind, topic) => {
    if (topic !== "agent-response") return;

    try {
      const decoder = new TextDecoder();
      const event = JSON.parse(decoder.decode(payload));
      console.log("[LiveAvatar] Received event:", event.event_type, event);

      switch (event.event_type) {
        case "avatar.speak_started":
          setIsSpeaking(true);
          break;
        case "avatar.speak_ended":
          setIsSpeaking(false);
          break;
        case "user.speak_started":
          setIsListening(true);
          break;
        case "user.speak_ended":
          setIsListening(false);
          break;
        case "avatar.transcription":
          if (event.text) {
            setSpeakHistory((prev) => [
              { text: event.text, timestamp: new Date().toLocaleTimeString(), type: "avatar" },
              ...prev,
            ].slice(0, 50));
          }
          break;
        case "user.transcription":
          if (event.text) {
            setSpeakHistory((prev) => [
              { text: event.text, timestamp: new Date().toLocaleTimeString(), type: "user" },
              ...prev,
            ].slice(0, 50));
          }
          break;
        case "session.stopped":
          console.log("[LiveAvatar] Session stopped:", event.end_reason);
          stopSession();
          break;
        default:
          break;
      }
    } catch (e) {
      console.warn("[LiveAvatar] Failed to parse event:", e);
    }
  }, []);

  // ── Start Session (2-step flow) ──
  const startSession = useCallback(async () => {
    setIsConnecting(true);
    setConnectionStatus("connecting");
    setError(null);

    try {
      // 1. Call backend → creates session token → starts session → returns LiveKit credentials
      console.log("[LiveAvatar] Requesting session from backend...");
      const result = await aiLiveCreatorService.liveAvatarStreamingStart({
        avatar_id: avatarId || "",
        language: language,
        persona_prompt: personaPrompt,
        voice_id: voiceId || null,
        sandbox: sandbox,
      });

      if (!result.success) {
        throw new Error(result.error || "Failed to start LiveAvatar session");
      }

      const { session_id, livekit_url, livekit_client_token, ws_url, max_session_duration } = result;

      if (!livekit_url || !livekit_client_token) {
        throw new Error("Backend did not return LiveKit credentials");
      }

      setSessionId(session_id);
      sessionIdRef.current = session_id;
      setMaxDuration(max_session_duration || 1200);

      console.log(`[LiveAvatar] Session created: ${session_id}, connecting to LiveKit: ${livekit_url}`);
      console.log(`[LiveAvatar] WebSocket URL: ${ws_url || 'not provided'}`);

      // 2. Connect to LiveKit room using livekit_url + livekit_client_token
      const room = new Room({
        adaptiveStream: true,
        dynacast: true,
      });
      roomRef.current = room;

      // Handle track subscriptions (video + audio from avatar)
      room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        console.log("[LiveAvatar] Track subscribed:", track.kind, track.source);
        if (track.kind === Track.Kind.Video) {
          const element = track.attach();
          element.style.width = "100%";
          element.style.height = "100%";
          element.style.objectFit = "cover";
          element.style.borderRadius = "inherit";
          element.autoplay = true;
          element.playsInline = true;

          if (videoRef.current) {
            videoRef.current.innerHTML = "";
            videoRef.current.appendChild(element);
          }

          // Pass MediaStream to parent for preview player
          if (onStreamReady) {
            // track.mediaStream may be null in LiveKit SDK v2;
            // create MediaStream explicitly from the underlying mediaStreamTrack
            const stream = track.mediaStream
              || (track.mediaStreamTrack ? new MediaStream([track.mediaStreamTrack]) : null);
            if (stream) {
              console.log('[LiveAvatar] Passing MediaStream to parent (tracks:', stream.getTracks().length, ')');
              onStreamReady(stream);
            } else {
              console.warn('[LiveAvatar] No MediaStream available from video track');
            }
          }
        }
        if (track.kind === Track.Kind.Audio) {
          const element = track.attach();
          element.autoplay = true;
          element.volume = 1.0;
          document.body.appendChild(element);
          audioElementsRef.current.push(element);
        }
      });

      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((el) => el.remove());
      });

      // Handle data channel events (avatar responses)
      room.on(RoomEvent.DataReceived, handleDataReceived);

      room.on(RoomEvent.Disconnected, () => {
        console.log("[LiveAvatar] Disconnected from room");
        setIsConnected(false);
        setConnectionStatus("disconnected");
        setSessionId(null);
        sessionIdRef.current = null;
        if (onDisconnect) onDisconnect();
      });

      room.on(RoomEvent.Reconnecting, () => {
        console.log("[LiveAvatar] Reconnecting...");
        setConnectionStatus("reconnecting");
      });

      room.on(RoomEvent.Reconnected, () => {
        console.log("[LiveAvatar] Reconnected");
        setConnectionStatus("connected");
      });

      // Connect to LiveKit room with the actual URL from LiveAvatar API
      await room.connect(livekit_url, livekit_client_token);
      console.log("[LiveAvatar] Connected to LiveKit room successfully");

      // Log participant permissions for debugging
      const perms = room.localParticipant.permissions;
      console.log("[LiveAvatar] Participant permissions:", JSON.stringify(perms));
      console.log("[LiveAvatar] Participant identity:", room.localParticipant.identity);
      console.log("[LiveAvatar] Participant SID:", room.localParticipant.sid);

      // 3. Connect to WebSocket if ws_url is provided (like official SDK)
      if (ws_url) {
        try {
          console.log("[LiveAvatar] Connecting to WebSocket:", ws_url);
          const ws = new WebSocket(ws_url);
          wsRef.current = ws;

          ws.onopen = () => {
            console.log("[LiveAvatar] WebSocket connected successfully");
          };

          ws.onmessage = (event) => {
            try {
              const data = JSON.parse(event.data);
              console.log("[LiveAvatar] WebSocket message:", data.type, data);

              // Handle WebSocket events (same as SDK)
              if (data.type === "agent.speak_started") {
                setIsSpeaking(true);
              } else if (data.type === "agent.speak_ended") {
                setIsSpeaking(false);
              } else if (data.type === "session.stopped") {
                console.log("[LiveAvatar] Session stopped via WebSocket");
                stopSession();
              }
            } catch (e) {
              console.warn("[LiveAvatar] Failed to parse WebSocket message:", e);
            }
          };

          ws.onerror = (err) => {
            console.warn("[LiveAvatar] WebSocket error:", err);
          };

          ws.onclose = (event) => {
            console.warn("[LiveAvatar] WebSocket closed:", event.code, event.reason);
            wsRef.current = null;
          };
        } catch (wsErr) {
          console.warn("[LiveAvatar] Failed to connect WebSocket (non-fatal):", wsErr);
        }
      }

      setIsConnected(true);
      setIsConnecting(false);
      setConnectionStatus("connected");

    } catch (err) {
      console.error("[LiveAvatar] Error starting session:", err);
      setError(err.message || "Failed to start LiveAvatar session");
      setIsConnecting(false);
      setConnectionStatus("disconnected");
      if (onError) onError(err);
    }
  }, [avatarId, language, personaPrompt, voiceId, sandbox, onStreamReady, onError, handleDataReceived]);

  // ── Stop Session ──
  const stopSession = useCallback(async () => {
    try {
      // Clean up audio elements
      audioElementsRef.current.forEach((el) => {
        try { el.remove(); } catch (e) {}
      });
      audioElementsRef.current = [];

      // Clean up WebSocket
      if (wsRef.current) {
        try {
          wsRef.current.onopen = null;
          wsRef.current.onmessage = null;
          wsRef.current.onerror = null;
          wsRef.current.onclose = null;
          if (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING) {
            wsRef.current.close();
          }
        } catch (e) {}
        wsRef.current = null;
      }

      if (roomRef.current) {
        roomRef.current.disconnect();
        roomRef.current = null;
      }

      const sid = sessionIdRef.current || sessionId;
      if (sid) {
        try {
          await aiLiveCreatorService.liveAvatarStreamingStop({ session_id: sid });
        } catch (e) {
          console.warn("[LiveAvatar] Error stopping session on backend:", e);
        }
      }
    } catch (e) {
      console.warn("[LiveAvatar] Error during cleanup:", e);
    }

    setIsConnected(false);
    setSessionId(null);
    sessionIdRef.current = null;
    setIsSpeaking(false);
    setIsListening(false);
    setConnectionStatus("disconnected");

    if (videoRef.current) {
      videoRef.current.innerHTML = "";
    }

    // Notify parent that stream is disconnected
    if (onDisconnect) onDisconnect();
  }, [sessionId, onDisconnect]);

  // ── Speak Text (direct TTS) ──
  const handleSpeak = useCallback(() => {
    if (!speakText.trim() || !isConnected) return;

    const text = speakText.trim();

    // Send via LiveKit data channel
    sendEvent("avatar.speak_text", { text });

    // Add to history
    setSpeakHistory((prev) => [
      { text, timestamp: new Date().toLocaleTimeString(), type: "sent" },
      ...prev,
    ].slice(0, 50));

    setSpeakText("");
    if (textInputRef.current) {
      textInputRef.current.focus();
    }
  }, [speakText, isConnected, sendEvent]);

  // ── Interrupt ──
  const handleInterrupt = useCallback(() => {
    if (!isConnected) return;
    sendEvent("avatar.interrupt");
    setIsSpeaking(false);
  }, [isConnected, sendEvent]);

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

  // ── Remaining time warning ──
  const remainingSeconds = maxDuration - sessionDuration;
  const isNearEnd = remainingSeconds > 0 && remainingSeconds <= 120;

  // ── Connection status badge ──
  const statusConfig = {
    disconnected: { color: "gray", label: "未接続" },
    connecting: { color: "yellow", label: "接続中..." },
    connected: { color: "green", label: "LIVE" },
    reconnecting: { color: "orange", label: "再接続中..." },
  };

  const status = statusConfig[connectionStatus] || statusConfig.disconnected;

  return (
    <div className={`flex flex-col gap-3 ${className}`}>
      {/* ── Video Display (hidden when hideVideo=true, video still renders for stream capture) ── */}
      <div className={`relative bg-gray-900 rounded-xl overflow-hidden ${hideVideo ? 'hidden' : ''}`} style={{ aspectRatio: "9/16", maxHeight: "500px" }}>
        <div ref={videoRef} className="w-full h-full" />

        {/* Overlay: Not connected */}
        {!isConnected && !isConnecting && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-900/80">
            <Radio className="w-12 h-12 text-gray-600 mb-3" />
            <p className="text-sm text-gray-400 mb-1">LiveAvatar Streaming</p>
            <p className="text-xs text-gray-500">接続ボタンを押してストリーミング開始</p>
            {sandbox && (
              <p className="text-[10px] text-yellow-500 mt-2 bg-yellow-500/10 px-2 py-1 rounded">
                サンドボックスモード（テスト用・1分制限）
              </p>
            )}
          </div>
        )}

        {/* Overlay: Connecting */}
        {isConnecting && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-900/80">
            <Loader2 className="w-10 h-10 text-green-400 animate-spin mb-3" />
            <p className="text-sm text-green-300">接続中...</p>
            <p className="text-xs text-gray-400 mt-1">LiveKit WebRTC セッション確立中</p>
          </div>
        )}

        {/* Status bar */}
        {isConnected && (
          <div className="absolute top-2 left-2 right-2 flex items-center justify-between">
            <div className="flex items-center gap-1.5 bg-green-500/20 backdrop-blur-sm px-2 py-1 rounded-full border border-green-500/30">
              <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
              <span className="text-[10px] text-green-300 font-medium">{status.label}</span>
            </div>
            <div className="flex items-center gap-1.5 bg-black/40 backdrop-blur-sm px-2 py-1 rounded-full">
              <span className={`text-[10px] font-mono ${isNearEnd ? 'text-red-400' : 'text-gray-300'}`}>
                {formatTime(sessionDuration)} / {formatTime(maxDuration)}
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

        {/* Listening indicator */}
        {isConnected && isListening && !isSpeaking && (
          <div className="absolute bottom-2 left-2 right-2 flex justify-center">
            <div className="flex items-center gap-1.5 bg-purple-500/20 backdrop-blur-sm px-3 py-1.5 rounded-full border border-purple-500/30">
              <Radio className="w-3.5 h-3.5 text-purple-400 animate-pulse" />
              <span className="text-[10px] text-purple-300">Listening...</span>
            </div>
          </div>
        )}

        {/* Near-end warning */}
        {isConnected && isNearEnd && (
          <div className="absolute bottom-10 left-2 right-2 flex justify-center">
            <div className="flex items-center gap-1.5 bg-red-500/20 backdrop-blur-sm px-3 py-1.5 rounded-full border border-red-500/30">
              <AlertCircle className="w-3.5 h-3.5 text-red-400" />
              <span className="text-[10px] text-red-300">残り {formatTime(remainingSeconds)}</span>
            </div>
          </div>
        )}
      </div>

      {/* ── Connection Controls ── */}
      <div className="flex gap-2">
        {!isConnected ? (
          <button
            onClick={startSession}
            disabled={isConnecting}
            className={`flex-1 py-2.5 px-4 rounded-lg font-medium text-xs flex items-center justify-center gap-2 transition-all ${
              !isConnecting
                ? "bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-600 hover:to-emerald-700 text-white shadow-lg shadow-green-500/20"
                : "bg-gray-700/50 text-gray-500 cursor-not-allowed"
            }`}
          >
            {isConnecting ? (
              <><Loader2 className="w-3.5 h-3.5 animate-spin" />接続中...</>
            ) : (
              <><Wifi className="w-3.5 h-3.5" />ストリーミング開始</>
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
              placeholder="テキストを入力してEnterで送信..."
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
                title="そのまま話す (Enter)"
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
                title="中断"
              >
                <Square className="w-3 h-3" />
              </button>
            </div>
          </div>
          <p className="text-[9px] text-gray-500 mt-1.5">
            Enter: テキスト送信 → アバターが話す | ■: 中断
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
          <h5 className="text-[10px] font-medium text-gray-400 mb-2">発話履歴</h5>
          <div className="space-y-1.5">
            {speakHistory.map((item, i) => (
              <div key={i} className="flex items-start gap-2">
                <span className="text-[9px] text-gray-500 font-mono shrink-0 mt-0.5">{item.timestamp}</span>
                <span className={`text-[9px] shrink-0 mt-0.5 px-1 rounded ${
                  item.type === "sent" ? "bg-green-500/20 text-green-400" :
                  item.type === "avatar" ? "bg-blue-500/20 text-blue-400" :
                  "bg-purple-500/20 text-purple-400"
                }`}>
                  {item.type === "sent" ? "送信" : item.type === "avatar" ? "AI" : "You"}
                </span>
                <p className="text-[11px] text-gray-300">{item.text}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
