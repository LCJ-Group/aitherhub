import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { Room, RoomEvent, Track } from "livekit-client";
import axios from "axios";

const ADMIN_KEY = "aither:hub";

/**
 * OBS Output Page — Shared Session Mode (方式A + API Polling)
 *
 * URL: /ai-live-creator/obs?bg=green
 *
 * This page does NOT create its own LiveAvatar session.
 * It retrieves LiveKit credentials from the backend's active session API
 * and joins the SAME LiveKit room as a receive-only client.
 *
 * Two credential sources (whichever arrives first):
 *   1. API Polling: GET /api/v1/digital-human/liveavatar/session/active (every 3s)
 *   2. postMessage: From parent window (when opened via Pop-out button)
 *
 * This ensures:
 *   - OBS Browser Source (direct URL) works via API polling
 *   - Pop-out window works via postMessage (faster)
 *   - Lip-sync works perfectly because it's the same LiveKit stream
 *   - No extra session costs (only one LiveAvatar session is active)
 *
 * URL Parameters:
 *   - bg: Background color (green|blue|black|transparent) default: green
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
  const [credSource, setCredSource] = useState(""); // "api" | "postMessage"

  // Refs
  const videoContainerRef = useRef(null);
  const roomRef = useRef(null);
  const sessionTimerRef = useRef(null);
  const audioElementsRef = useRef([]);
  const connectedRef = useRef(false); // prevent double-connect
  const pollingRef = useRef(null);
  const currentSessionIdRef = useRef(null);

  const baseURL = import.meta.env.VITE_API_BASE_URL;

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
  const connectToRoom = useCallback(async (livekitUrl, livekitToken, sessionId, source) => {
    // Prevent double-connect to the same session
    if (connectedRef.current && currentSessionIdRef.current === sessionId) {
      console.log("[OBS] Already connected to this session, ignoring");
      return;
    }

    // If connected to a different session, disconnect first
    if (connectedRef.current) {
      console.log("[OBS] Switching to new session, disconnecting old one");
      disconnectFromRoom();
    }

    connectedRef.current = true;
    currentSessionIdRef.current = sessionId;

    setStatus("connecting");
    setError(null);
    setCredSource(source || "unknown");

    try {
      console.log(`[OBS] Connecting to shared LiveKit room (source: ${source}):`, livekitUrl);

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
        currentSessionIdRef.current = null;
        if (videoContainerRef.current) {
          videoContainerRef.current.innerHTML = "";
        }
      });

      // Connect to the shared LiveKit room
      await room.connect(livekitUrl, livekitToken);
      setStatus("connected");
      console.log("[OBS] Successfully connected to shared LiveKit room");

      // Stop polling once connected
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }

      // Notify parent window that OBS is connected (for Pop-out mode)
      if (window.opener) {
        window.opener.postMessage({ type: "obs-stream-ready" }, "*");
      }
    } catch (err) {
      console.error("[OBS] Connection error:", err);
      setError(err.message);
      setStatus("error");
      connectedRef.current = false;
      currentSessionIdRef.current = null;

      // Resume polling on error so we can retry
      startPolling();
    }
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
    currentSessionIdRef.current = null;

    if (videoContainerRef.current) {
      videoContainerRef.current.innerHTML = "";
    }
  }, []);

  // ── API Polling for active session ──
  const pollActiveSession = useCallback(async () => {
    if (connectedRef.current) return; // Already connected, skip

    try {
      const resp = await axios.get(
        `${baseURL}/api/v1/digital-human/liveavatar/session/active`,
        { headers: { "X-Admin-Key": ADMIN_KEY }, timeout: 5000 }
      );

      if (resp.data?.active && resp.data.livekit_url && resp.data.livekit_client_token) {
        console.log("[OBS] Found active session via API:", resp.data.session_id);
        connectToRoom(
          resp.data.livekit_url,
          resp.data.livekit_client_token,
          resp.data.session_id,
          "api"
        );
      }
    } catch (err) {
      // Silently ignore polling errors (backend may be temporarily unavailable)
      console.debug("[OBS] Polling error:", err.message);
    }
  }, [baseURL, connectToRoom]);

  const startPolling = useCallback(() => {
    if (pollingRef.current) return; // Already polling
    console.log("[OBS] Starting API polling for active session (every 3s)");
    // Poll immediately, then every 3 seconds
    pollActiveSession();
    pollingRef.current = setInterval(pollActiveSession, 3000);
  }, [pollActiveSession]);

  // ── Listen for postMessage from main page + Start API polling ──
  useEffect(() => {
    const handleMessage = (event) => {
      if (!event.data || typeof event.data !== "object") return;

      switch (event.data.type) {
        case "obs-livekit-creds":
          // Receive LiveKit credentials from main page (Pop-out mode)
          console.log("[OBS] Received LiveKit credentials via postMessage");
          if (event.data.livekit_url && event.data.livekit_client_token) {
            connectToRoom(
              event.data.livekit_url,
              event.data.livekit_client_token,
              event.data.session_id || "unknown",
              "postMessage"
            );
          }
          break;
        case "obs-stop":
          // Stop and disconnect
          disconnectFromRoom();
          startPolling(); // Resume polling after disconnect
          break;
        default:
          break;
      }
    };

    window.addEventListener("message", handleMessage);

    // Notify parent window that OBS is ready (for Pop-out mode)
    if (window.opener) {
      window.opener.postMessage({ type: "obs-ready" }, "*");
    }

    // Start API polling (for OBS Browser Source direct URL mode)
    startPolling();

    return () => {
      window.removeEventListener("message", handleMessage);
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [connectToRoom, disconnectFromRoom, startPolling]);

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
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
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
              Shared Session Mode — API Polling + postMessage
            </p>

            {status === "waiting" && (
              <div style={{ color: "#facc15" }}>
                <p style={{ fontSize: "14px" }}>Waiting for active LiveAvatar session...</p>
                <p style={{ fontSize: "11px", color: "#888", marginTop: "8px" }}>
                  Start a LiveAvatar session on the main page.
                </p>
                <p style={{ fontSize: "11px", color: "#888", marginTop: "4px" }}>
                  This page polls the backend every 3 seconds for active sessions.
                </p>
                <div style={{ marginTop: "12px" }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: "8px",
                      height: "8px",
                      borderRadius: "50%",
                      backgroundColor: "#facc15",
                      animation: "pulse 1.5s infinite",
                      marginRight: "6px",
                    }}
                  />
                  <span style={{ fontSize: "11px", color: "#facc15" }}>Polling...</span>
                </div>
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
                  Retrying automatically via API polling...
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
          {credSource && (
            <span style={{ color: "#888", marginLeft: "4px" }}>({credSource})</span>
          )}
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
