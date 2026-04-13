import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { Room, RoomEvent, Track } from "livekit-client";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";

/**
 * OBS Output Page — Minimal page for OBS Browser Source capture
 *
 * URL: /ai-live-creator/obs?avatar_id=xxx&voice_id=yyy&bg=green
 *
 * Features:
 *   - No UI chrome — only the avatar video fills the entire viewport
 *   - Configurable background color for chroma key (green, blue, black, transparent)
 *   - Auto-connects to LiveAvatar session on load
 *   - Supports URL parameters for avatar/voice selection
 *   - Designed to be added as OBS "Browser Source" (1080x1920 recommended)
 *
 * URL Parameters:
 *   - avatar_id: LiveAvatar avatar UUID
 *   - voice_id: ElevenLabs voice UUID
 *   - bg: Background color (green|blue|black|transparent) default: green
 *   - autostart: Auto-start session (true|false) default: false
 *   - language: Language code (ja|en|zh) default: ja
 */
export default function OBSOutputPage() {
  const [searchParams] = useSearchParams();

  // URL params
  const avatarId = searchParams.get("avatar_id") || "";
  const voiceId = searchParams.get("voice_id") || "";
  const bgColor = searchParams.get("bg") || "green";
  const autostart = searchParams.get("autostart") === "true";
  const language = searchParams.get("language") || "ja";

  // State
  const [status, setStatus] = useState("idle"); // idle | connecting | connected | error
  const [error, setError] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [sessionDuration, setSessionDuration] = useState(0);
  const [isSpeaking, setIsSpeaking] = useState(false);

  // Refs
  const videoContainerRef = useRef(null);
  const roomRef = useRef(null);
  const sessionIdRef = useRef(null);
  const sessionTimerRef = useRef(null);
  const audioElementsRef = useRef([]);

  // Background color map
  const bgMap = {
    green: "#00FF00",
    blue: "#0000FF",
    black: "#000000",
    transparent: "transparent",
    gray: "#1a1a2e",
  };

  const backgroundColor = bgMap[bgColor] || bgMap.green;

  // ── Auto-start on load ──
  useEffect(() => {
    if (autostart) {
      startSession();
    }
    return () => {
      stopSession();
    };
  }, []);

  // ── Session duration timer ──
  useEffect(() => {
    if (status === "connected") {
      sessionTimerRef.current = setInterval(() => {
        setSessionDuration((prev) => prev + 1);
      }, 1000);
    } else {
      if (sessionTimerRef.current) {
        clearInterval(sessionTimerRef.current);
        sessionTimerRef.current = null;
      }
      if (status === "idle") setSessionDuration(0);
    }
    return () => {
      if (sessionTimerRef.current) clearInterval(sessionTimerRef.current);
    };
  }, [status]);

  // ── Listen for parent window messages (for control from main page) ──
  useEffect(() => {
    const handleMessage = (event) => {
      if (!event.data || typeof event.data !== "object") return;

      switch (event.data.type) {
        case "obs-speak":
          if (event.data.text) {
            sendSpeakEvent(event.data.text);
          }
          break;
        case "obs-interrupt":
          sendInterruptEvent();
          break;
        case "obs-stop":
          stopSession();
          break;
        case "obs-start":
          startSession();
          break;
        default:
          break;
      }
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  // ── Generate event ID ──
  const generateEventId = () =>
    `evt_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

  // ── Send speak event via LiveKit data channel ──
  const sendSpeakEvent = useCallback((text) => {
    const room = roomRef.current;
    if (!room || !room.localParticipant) return;

    const event = {
      event_id: generateEventId(),
      event_type: "avatar.speak_text",
      session_id: sessionIdRef.current || "",
      text,
    };

    const encoder = new TextEncoder();
    const data = encoder.encode(JSON.stringify(event));
    room.localParticipant.publishData(data, {
      reliable: true,
      topic: "agent-control",
    });
  }, []);

  // ── Send interrupt event ──
  const sendInterruptEvent = useCallback(() => {
    const room = roomRef.current;
    if (!room || !room.localParticipant) return;

    const event = {
      event_id: generateEventId(),
      event_type: "avatar.interrupt",
      session_id: sessionIdRef.current || "",
    };

    const encoder = new TextEncoder();
    const data = encoder.encode(JSON.stringify(event));
    room.localParticipant.publishData(data, {
      reliable: true,
      topic: "agent-control",
    });
  }, []);

  // ── Handle data received from avatar ──
  const handleDataReceived = useCallback((payload, participant, kind, topic) => {
    if (topic !== "agent-response") return;
    try {
      const decoder = new TextDecoder();
      const event = JSON.parse(decoder.decode(payload));
      switch (event.event_type) {
        case "avatar.speak_started":
          setIsSpeaking(true);
          break;
        case "avatar.speak_ended":
          setIsSpeaking(false);
          break;
        case "session.stopped":
          stopSession();
          break;
        default:
          break;
      }
    } catch (e) {
      // ignore parse errors
    }
  }, []);

  // ── Start LiveAvatar Session ──
  const startSession = useCallback(async () => {
    if (status === "connecting" || status === "connected") return;

    setStatus("connecting");
    setError(null);

    try {
      const result = await aiLiveCreatorService.liveAvatarStreamingStart({
        avatar_id: avatarId,
        language,
        voice_id: voiceId || null,
        sandbox: false,
      });

      if (!result.success) {
        throw new Error(result.error || "Failed to start session");
      }

      const { session_id, livekit_url, livekit_client_token } = result;
      if (!livekit_url || !livekit_client_token) {
        throw new Error("No LiveKit credentials returned");
      }

      setSessionId(session_id);
      sessionIdRef.current = session_id;

      // Connect to LiveKit
      const room = new Room({ adaptiveStream: true, dynacast: true });
      roomRef.current = room;

      room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        if (track.kind === Track.Kind.Video) {
          const element = track.attach();
          element.style.width = "100%";
          element.style.height = "100%";
          element.style.objectFit = "cover";
          element.autoplay = true;
          element.playsInline = true;

          if (videoContainerRef.current) {
            videoContainerRef.current.innerHTML = "";
            videoContainerRef.current.appendChild(element);
          }

          // Notify parent window that stream is ready
          if (window.opener) {
            window.opener.postMessage({ type: "obs-stream-ready" }, "*");
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

      room.on(RoomEvent.DataReceived, handleDataReceived);

      room.on(RoomEvent.Disconnected, () => {
        setStatus("idle");
        setSessionId(null);
        sessionIdRef.current = null;
      });

      await room.connect(livekit_url, livekit_client_token);
      setStatus("connected");
    } catch (err) {
      console.error("[OBS Output] Error:", err);
      setError(err.message);
      setStatus("error");
    }
  }, [avatarId, voiceId, language, status, handleDataReceived]);

  // ── Stop Session ──
  const stopSession = useCallback(async () => {
    audioElementsRef.current.forEach((el) => {
      try { el.remove(); } catch (e) {}
    });
    audioElementsRef.current = [];

    if (roomRef.current) {
      roomRef.current.disconnect();
      roomRef.current = null;
    }

    const sid = sessionIdRef.current || sessionId;
    if (sid) {
      try {
        await aiLiveCreatorService.liveAvatarStreamingStop({ session_id: sid });
      } catch (e) {}
    }

    setStatus("idle");
    setSessionId(null);
    sessionIdRef.current = null;
    setIsSpeaking(false);

    if (videoContainerRef.current) {
      videoContainerRef.current.innerHTML = "";
    }
  }, [sessionId]);

  // ── Format time ──
  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        backgroundColor,
        overflow: "hidden",
        position: "relative",
        margin: 0,
        padding: 0,
      }}
    >
      {/* Video container — fills entire viewport */}
      <div
        ref={videoContainerRef}
        style={{
          width: "100%",
          height: "100%",
          position: "absolute",
          top: 0,
          left: 0,
        }}
      />

      {/* Minimal overlay — only shown when NOT connected (hidden during streaming for clean OBS capture) */}
      {status !== "connected" && (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "rgba(0,0,0,0.8)",
            color: "white",
            fontFamily: "system-ui, sans-serif",
            zIndex: 10,
          }}
        >
          <div style={{ textAlign: "center", maxWidth: "400px", padding: "20px" }}>
            <h2 style={{ fontSize: "24px", marginBottom: "8px", fontWeight: "bold" }}>
              AitherHub OBS Output
            </h2>
            <p style={{ fontSize: "12px", color: "#888", marginBottom: "24px" }}>
              LiveAvatar Streaming for OBS Browser Source
            </p>

            {status === "idle" && (
              <button
                onClick={startSession}
                style={{
                  padding: "12px 32px",
                  fontSize: "14px",
                  fontWeight: "bold",
                  backgroundColor: "#22c55e",
                  color: "white",
                  border: "none",
                  borderRadius: "8px",
                  cursor: "pointer",
                  marginBottom: "12px",
                }}
              >
                Start Streaming
              </button>
            )}

            {status === "connecting" && (
              <div style={{ color: "#facc15" }}>
                <p style={{ fontSize: "14px" }}>Connecting...</p>
                <p style={{ fontSize: "11px", color: "#888", marginTop: "4px" }}>
                  Establishing LiveKit WebRTC session
                </p>
              </div>
            )}

            {status === "error" && (
              <div>
                <p style={{ color: "#ef4444", fontSize: "13px", marginBottom: "12px" }}>
                  {error}
                </p>
                <button
                  onClick={startSession}
                  style={{
                    padding: "10px 24px",
                    fontSize: "13px",
                    backgroundColor: "#22c55e",
                    color: "white",
                    border: "none",
                    borderRadius: "8px",
                    cursor: "pointer",
                  }}
                >
                  Retry
                </button>
              </div>
            )}

            <div style={{ marginTop: "20px", fontSize: "10px", color: "#666" }}>
              <p>Avatar: {avatarId || "Default"}</p>
              <p>Background: {bgColor}</p>
              <p>Language: {language}</p>
              {autostart && <p>Auto-start: enabled</p>}
            </div>
          </div>
        </div>
      )}

      {/* Minimal status indicator during streaming (small, unobtrusive) */}
      {status === "connected" && (
        <div
          style={{
            position: "absolute",
            top: "8px",
            right: "8px",
            zIndex: 20,
            display: "flex",
            alignItems: "center",
            gap: "6px",
            backgroundColor: "rgba(0,0,0,0.5)",
            padding: "4px 10px",
            borderRadius: "12px",
            fontSize: "10px",
            color: "#4ade80",
            fontFamily: "monospace",
          }}
        >
          <span
            style={{
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              backgroundColor: "#4ade80",
              animation: "pulse 2s infinite",
            }}
          />
          LIVE {formatTime(sessionDuration)}
          {isSpeaking && (
            <span style={{ color: "#38bdf8", marginLeft: "4px" }}>Speaking</span>
          )}
        </div>
      )}

      {/* CSS animation for pulse */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { overflow: hidden; }
      `}</style>
    </div>
  );
}
