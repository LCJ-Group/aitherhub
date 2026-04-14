import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { Room, RoomEvent, Track } from "livekit-client";

/**
 * OBS Output Page — Shared Session Mode (方式A)
 *
 * URL: /ai-live-creator/obs?bg=green
 *
 * This page does NOT create its own LiveAvatar session.
 * Instead, it receives LiveKit credentials from the main AiLiveCreatorPage
 * via postMessage and joins the SAME LiveKit room as a receive-only client.
 *
 * This ensures:
 *   - OBS sees the exact same avatar video + audio as the main page
 *   - Lip-sync works perfectly because it's the same LiveKit stream
 *   - No extra session costs (only one LiveAvatar session is active)
 *   - Future comment responses automatically appear in OBS too
 *
 * Flow:
 *   1. Main page creates LiveAvatar session → gets livekit_url + livekit_client_token
 *   2. Main page sends credentials via postMessage({ type: "obs-livekit-creds", ... })
 *   3. This page connects to the same LiveKit room using those credentials
 *   4. Video/audio tracks are received and displayed (receive-only, no publishing)
 *
 * URL Parameters:
 *   - bg: Background color (green|blue|black|transparent) default: green
 *   - avatar_id: (legacy, kept for URL compatibility)
 *   - voice_id: (legacy, kept for URL compatibility)
 *   - language: (legacy, kept for URL compatibility)
 *   - autostart: (legacy, ignored — session is started by main page)
 */
export default function OBSOutputPage() {
  const [searchParams] = useSearchParams();

  // URL params
  const bgColor = searchParams.get("bg") || "green";

  // State
  const [status, setStatus] = useState("waiting"); // waiting | connecting | connected | error
  const [error, setError] = useState(null);
  const [sessionDuration, setSessionDuration] = useState(0);
  const [isSpeaking, setIsSpeaking] = useState(false);

  // Refs
  const videoContainerRef = useRef(null);
  const roomRef = useRef(null);
  const sessionTimerRef = useRef(null);
  const audioElementsRef = useRef([]);
  const connectedRef = useRef(false); // prevent double-connect

  // Background color map
  const bgMap = {
    green: "#00FF00",
    blue: "#0000FF",
    black: "#000000",
    transparent: "transparent",
    gray: "#1a1a2e",
  };

  const backgroundColor = bgMap[bgColor] || bgMap.green;

  // ── Connect to LiveKit room using shared credentials ──
  const connectToRoom = useCallback(async (livekitUrl, livekitToken) => {
    if (connectedRef.current) {
      console.log("[OBS] Already connected, ignoring duplicate credentials");
      return;
    }
    connectedRef.current = true;

    setStatus("connecting");
    setError(null);

    try {
      console.log("[OBS] Connecting to shared LiveKit room:", livekitUrl);

      // Disconnect existing room if any
      if (roomRef.current) {
        try {
          roomRef.current.disconnect();
        } catch (e) {}
        roomRef.current = null;
      }

      // Clean up existing audio elements
      audioElementsRef.current.forEach((el) => {
        try { el.remove(); } catch (e) {}
      });
      audioElementsRef.current = [];

      // ╔══════════════════════════════════════════════════════════════════╗
      // ║ CRITICAL: DO NOT CHANGE adaptiveStream / dynacast to true!     ║
      // ║ adaptiveStream: true causes LiveAvatar video frames to STOP    ║
      // ║ rendering (frameRate drops to 0). This breaks lip-sync.        ║
      // ║ LiveAvatar official SDK uses default Room() (both false).      ║
      // ║ See: SKILL.md → LiveAvatar Lip-Sync Rules                     ║
      // ╚══════════════════════════════════════════════════════════════════╝
      const room = new Room({
        adaptiveStream: false,  // ⚠️ DO NOT CHANGE — breaks lip-sync
        dynacast: false,        // ⚠️ DO NOT CHANGE — breaks lip-sync
      });
      roomRef.current = room;

      // Handle video tracks
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
          console.log("[OBS] Video track attached");
        }
        if (track.kind === Track.Kind.Audio) {
          const element = track.attach();
          element.autoplay = true;
          element.volume = 1.0;
          document.body.appendChild(element);
          audioElementsRef.current.push(element);
          console.log("[OBS] Audio track attached");
        }
      });

      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((el) => el.remove());
      });

      // Handle data events from avatar (for speaking status)
      room.on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
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
            default:
              break;
          }
        } catch (e) {
          // ignore parse errors
        }
      });

      room.on(RoomEvent.Disconnected, () => {
        console.log("[OBS] Disconnected from LiveKit room");
        setStatus("waiting");
        connectedRef.current = false;
        if (videoContainerRef.current) {
          videoContainerRef.current.innerHTML = "";
        }
      });

      // Connect to the shared LiveKit room
      await room.connect(livekitUrl, livekitToken);
      setStatus("connected");
      console.log("[OBS] Successfully connected to shared LiveKit room");

      // Notify parent window that OBS is connected
      if (window.opener) {
        window.opener.postMessage({ type: "obs-stream-ready" }, "*");
      }
    } catch (err) {
      console.error("[OBS] Connection error:", err);
      setError(err.message);
      setStatus("error");
      connectedRef.current = false;
    }
  }, []);

  // ── Listen for postMessage from main page ──
  useEffect(() => {
    const handleMessage = (event) => {
      if (!event.data || typeof event.data !== "object") return;

      switch (event.data.type) {
        case "obs-livekit-creds":
          // Receive LiveKit credentials from main page
          console.log("[OBS] Received LiveKit credentials from main page");
          if (event.data.livekit_url && event.data.livekit_client_token) {
            connectToRoom(event.data.livekit_url, event.data.livekit_client_token);
          }
          break;
        case "obs-stop":
          // Stop and disconnect
          disconnectFromRoom();
          break;
        default:
          break;
      }
    };

    window.addEventListener("message", handleMessage);

    // Also notify main page that OBS is ready to receive credentials
    if (window.opener) {
      window.opener.postMessage({ type: "obs-ready" }, "*");
    }

    return () => window.removeEventListener("message", handleMessage);
  }, [connectToRoom]);

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
      if (status === "waiting") setSessionDuration(0);
    }
    return () => {
      if (sessionTimerRef.current) clearInterval(sessionTimerRef.current);
    };
  }, [status]);

  // ── Cleanup on unmount ──
  useEffect(() => {
    return () => {
      disconnectFromRoom();
    };
  }, []);

  // ── Disconnect from room ──
  const disconnectFromRoom = useCallback(() => {
    audioElementsRef.current.forEach((el) => {
      try { el.remove(); } catch (e) {}
    });
    audioElementsRef.current = [];

    if (roomRef.current) {
      roomRef.current.disconnect();
      roomRef.current = null;
    }

    setStatus("waiting");
    setIsSpeaking(false);
    connectedRef.current = false;

    if (videoContainerRef.current) {
      videoContainerRef.current.innerHTML = "";
    }
  }, []);

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

      {/* Minimal overlay — only shown when NOT connected */}
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
              Shared Session Mode — Waiting for main page
            </p>

            {status === "waiting" && (
              <div style={{ color: "#facc15" }}>
                <p style={{ fontSize: "14px" }}>Waiting for LiveKit credentials...</p>
                <p style={{ fontSize: "11px", color: "#888", marginTop: "8px" }}>
                  Start a LiveAvatar session on the main page, then open this OBS window.
                </p>
                <p style={{ fontSize: "11px", color: "#888", marginTop: "4px" }}>
                  Credentials will be sent automatically via postMessage.
                </p>
              </div>
            )}

            {status === "connecting" && (
              <div style={{ color: "#facc15" }}>
                <p style={{ fontSize: "14px" }}>Connecting to shared LiveKit room...</p>
                <p style={{ fontSize: "11px", color: "#888", marginTop: "4px" }}>
                  Joining the same session as the main page
                </p>
              </div>
            )}

            {status === "error" && (
              <div>
                <p style={{ color: "#ef4444", fontSize: "13px", marginBottom: "12px" }}>
                  {error}
                </p>
                <p style={{ fontSize: "11px", color: "#888" }}>
                  Please restart the session from the main page.
                </p>
              </div>
            )}

            <div style={{ marginTop: "20px", fontSize: "10px", color: "#666" }}>
              <p>Background: {bgColor}</p>
              <p>Mode: Shared Session (receive-only)</p>
            </div>
          </div>
        </div>
      )}

      {/* Minimal status indicator during streaming */}
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
