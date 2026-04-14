import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { Room, RoomEvent, Track } from "livekit-client";
import axios from "axios";

const ADMIN_KEY = "aither:hub";
const QUEUE_POLL_INTERVAL = 1500; // 1.5 seconds

/**
 * OBS Output Page — Independent Session + SpeakText Queue Relay
 *
 * URL: /ai-live-creator/obs?bg=green&language=ja
 *
 * Architecture:
 *   1. OBS creates its OWN LiveAvatar session (independent from main page)
 *   2. OBS polls the backend speak-text queue every 1.5 seconds
 *   3. When main page sends speakText, it also pushes to the backend queue
 *   4. OBS receives the text from the queue and sends it to its own LiveKit room
 *   5. Both avatars speak the same text simultaneously
 *
 * IMPORTANT: All functions use refs directly (no useCallback chains) to avoid
 * stale closure issues. setInterval callbacks always read the latest roomRef.current.
 */
export default function OBSOutputPage() {
  const [searchParams] = useSearchParams();

  // URL params
  const bgColor = searchParams.get("bg") || "green";
  const language = searchParams.get("language") || "ja";
  const avatarIdParam = searchParams.get("avatar_id") || "";

  // State
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);
  const [sessionDuration, setSessionDuration] = useState(0);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [debugInfo, setDebugInfo] = useState("");

  // Refs — used by interval callbacks to avoid stale closures
  const videoContainerRef = useRef(null);
  const roomRef = useRef(null);
  const sessionIdRef = useRef(null);
  const sessionTimerRef = useRef(null);
  const audioElementsRef = useRef([]);
  const queuePollRef = useRef(null);
  const lastQueueIdRef = useRef("0");
  const mountedRef = useRef(true);
  const wsRef = useRef(null);
  const pollCountRef = useRef(0);
  const speakCountRef = useRef(0);

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

  // ── Helper: Send event to LiveKit data channel ──
  // This is a plain function, NOT useCallback. It reads roomRef.current directly.
  function sendToLiveKit(text) {
    const room = roomRef.current;
    if (!room || !room.localParticipant || room.state !== "connected") {
      console.warn("[OBS] sendToLiveKit: room not connected. state=", room?.state);
      return false;
    }

    const eventId = `obs_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    const livekitEvent = {
      event_id: eventId,
      event_type: "avatar.speak_text",
      text: text,
    };

    const encoder = new TextEncoder();
    const data = encoder.encode(JSON.stringify(livekitEvent));

    try {
      room.localParticipant.publishData(data, {
        reliable: true,
        topic: "agent-control",
      });
      speakCountRef.current += 1;
      console.log(`[OBS] ✅ Sent speakText to LiveKit (${speakCountRef.current}):`, text.substring(0, 60));
      return true;
    } catch (err) {
      console.error("[OBS] ❌ Failed to publishData:", err);
      return false;
    }
  }

  // ── Helper: Poll speak queue once ──
  // Plain async function, reads refs directly.
  async function pollQueueOnce() {
    const room = roomRef.current;
    if (!room || room.state !== "connected") {
      return;
    }

    try {
      const resp = await axios.get(
        `${baseURL}/api/v1/digital-human/liveavatar/speak-queue/poll`,
        {
          params: { after_id: lastQueueIdRef.current },
          headers: { "X-Admin-Key": ADMIN_KEY },
          timeout: 5000,
        }
      );

      pollCountRef.current += 1;

      if (resp.data?.success && resp.data.items?.length > 0) {
        for (const item of resp.data.items) {
          console.log(`[OBS] 📨 Queue item #${item.id}: "${item.text.substring(0, 60)}"`);
          const sent = sendToLiveKit(item.text);
          console.log(`[OBS] → LiveKit send result: ${sent ? "SUCCESS" : "FAILED"}`);
        }
        // Update last seen ID
        const maxId = resp.data.items[resp.data.items.length - 1].id;
        lastQueueIdRef.current = maxId;
      }

      // Update debug info
      if (pollCountRef.current % 5 === 0) {
        setDebugInfo(`Polls: ${pollCountRef.current} | Speaks: ${speakCountRef.current} | LastID: ${lastQueueIdRef.current}`);
      }
    } catch (err) {
      console.debug("[OBS] Queue poll error:", err.message);
    }
  }

  // ── Start queue polling ──
  function startQueuePolling() {
    if (queuePollRef.current) {
      clearInterval(queuePollRef.current);
    }
    console.log(`[OBS] 🔄 Starting queue polling (every ${QUEUE_POLL_INTERVAL}ms)`);
    // Use arrow function in setInterval so it always calls the latest pollQueueOnce
    queuePollRef.current = setInterval(() => {
      pollQueueOnce();
    }, QUEUE_POLL_INTERVAL);
  }

  function stopQueuePolling() {
    if (queuePollRef.current) {
      clearInterval(queuePollRef.current);
      queuePollRef.current = null;
      console.log("[OBS] Queue polling stopped");
    }
  }

  // ── Connect to LiveKit room ──
  async function connectToRoom(livekitUrl, livekitToken) {
    setStatus("connecting");

    try {
      // Clean up existing room
      if (roomRef.current) {
        try { roomRef.current.disconnect(); } catch (e) {}
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
        adaptiveStream: false,
        dynacast: false,
      });

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
          console.log("[OBS] ✅ Video track attached");
        }
        if (track.kind === Track.Kind.Audio) {
          const element = track.attach();
          element.autoplay = true;
          element.volume = 1.0;
          document.body.appendChild(element);
          audioElementsRef.current.push(element);
          console.log("[OBS] ✅ Audio track attached");
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
          console.log("[OBS] 📩 DataReceived:", event.event_type);
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
        console.log("[OBS] ⚠️ Disconnected from LiveKit room");
        stopQueuePolling();
        if (mountedRef.current) {
          setStatus("error");
          setError("Disconnected from LiveKit room");
        }
      });

      // Connect to room
      await room.connect(livekitUrl, livekitToken);

      // IMPORTANT: Set roomRef AFTER successful connection
      roomRef.current = room;
      setStatus("connected");
      console.log("[OBS] ✅ Connected to LiveKit room");
      console.log("[OBS] Room state:", room.state);
      console.log("[OBS] Local participant:", room.localParticipant?.identity);
      console.log("[OBS] Permissions:", JSON.stringify(room.localParticipant?.permissions));

      // Start queue polling AFTER room is connected and ref is set
      startQueuePolling();

    } catch (err) {
      console.error("[OBS] ❌ LiveKit connection error:", err);
      setError(err.message);
      setStatus("error");
    }
  }

  // ── Create own LiveAvatar session ──
  async function createOwnSession() {
    setStatus("creating");
    setError(null);

    try {
      console.log("[OBS] 🚀 Creating own LiveAvatar session...");
      const resp = await axios.post(
        `${baseURL}/api/v1/digital-human/liveavatar/streaming/start`,
        {
          avatar_id: avatarIdParam || "",
          language: language,
          persona_prompt: "",
          voice_id: null,
          sandbox: false,
        },
        { headers: { "X-Admin-Key": ADMIN_KEY }, timeout: 60000 }
      );

      if (!resp.data?.success) {
        throw new Error(resp.data?.error || "Failed to create session");
      }

      const { session_id, livekit_url, livekit_client_token, ws_url } = resp.data;
      if (!livekit_url || !livekit_client_token) {
        throw new Error("Backend did not return LiveKit credentials");
      }

      sessionIdRef.current = session_id;
      console.log(`[OBS] ✅ Session created: ${session_id}`);

      // Connect WebSocket if available
      if (ws_url) {
        try {
          const ws = new WebSocket(ws_url);
          ws.onopen = () => console.log("[OBS] WebSocket connected");
          ws.onclose = () => console.log("[OBS] WebSocket closed");
          ws.onerror = (e) => console.warn("[OBS] WebSocket error:", e);
          wsRef.current = ws;
        } catch (e) {
          console.warn("[OBS] WebSocket connect failed:", e);
        }
      }

      // Connect to LiveKit room
      await connectToRoom(livekit_url, livekit_client_token);
    } catch (err) {
      console.error("[OBS] ❌ Session creation error:", err);
      setError(err.response?.data?.message || err.message || "Session creation failed");
      setStatus("error");
    }
  }

  // ── Stop session ──
  async function stopSession() {
    stopQueuePolling();

    if (wsRef.current) {
      try { wsRef.current.close(); } catch (e) {}
      wsRef.current = null;
    }

    audioElementsRef.current.forEach((el) => {
      try { el.remove(); } catch (e) {}
    });
    audioElementsRef.current = [];

    if (roomRef.current) {
      try { roomRef.current.disconnect(); } catch (e) {}
      roomRef.current = null;
    }

    if (sessionIdRef.current) {
      try {
        await axios.post(
          `${baseURL}/api/v1/digital-human/liveavatar/streaming/stop`,
          { session_id: sessionIdRef.current },
          { headers: { "X-Admin-Key": ADMIN_KEY }, timeout: 10000 }
        );
      } catch (e) {
        console.warn("[OBS] Failed to stop session on backend:", e);
      }
      sessionIdRef.current = null;
    }

    if (videoContainerRef.current) {
      videoContainerRef.current.innerHTML = "";
    }

    setStatus("idle");
    setIsSpeaking(false);
    setSessionDuration(0);
    pollCountRef.current = 0;
    speakCountRef.current = 0;
  }

  // ── Auto-start session on mount ──
  useEffect(() => {
    mountedRef.current = true;
    const timer = setTimeout(() => {
      if (mountedRef.current) {
        createOwnSession();
      }
    }, 1000);

    return () => {
      mountedRef.current = false;
      clearTimeout(timer);
      stopQueuePolling();
      if (roomRef.current) {
        try { roomRef.current.disconnect(); } catch (e) {}
      }
      audioElementsRef.current.forEach((el) => {
        try { el.remove(); } catch (e) {}
      });
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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

      {/* Overlay — shown when NOT connected */}
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
            backgroundColor: "rgba(0,0,0,0.85)",
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
              Independent Session + SpeakText Queue Relay
            </p>

            {status === "idle" && (
              <div style={{ color: "#facc15" }}>
                <p style={{ fontSize: "14px" }}>Initializing...</p>
              </div>
            )}

            {status === "creating" && (
              <div style={{ color: "#facc15" }}>
                <p style={{ fontSize: "14px" }}>Creating LiveAvatar session...</p>
                <p style={{ fontSize: "11px", color: "#888", marginTop: "8px" }}>
                  This may take 10-20 seconds
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
                    }}
                  />
                  <span style={{ fontSize: "11px", color: "#facc15", marginLeft: "6px" }}>
                    Creating session...
                  </span>
                </div>
              </div>
            )}

            {status === "connecting" && (
              <div style={{ color: "#38bdf8" }}>
                <p style={{ fontSize: "14px" }}>Connecting to LiveKit room...</p>
              </div>
            )}

            {status === "error" && (
              <div>
                <p style={{ color: "#ef4444", fontSize: "13px", marginBottom: "12px" }}>
                  {error}
                </p>
                <button
                  onClick={() => {
                    stopSession().then(() => createOwnSession());
                  }}
                  style={{
                    padding: "8px 20px",
                    backgroundColor: "#22c55e",
                    color: "white",
                    border: "none",
                    borderRadius: "8px",
                    cursor: "pointer",
                    fontSize: "13px",
                    fontWeight: "bold",
                  }}
                >
                  Retry
                </button>
              </div>
            )}

            <div style={{ marginTop: "20px", fontSize: "10px", color: "#666" }}>
              <p>Background: {bgColor}</p>
              <p>Language: {language}</p>
              <p>Mode: Independent Session + Queue Relay</p>
            </div>
          </div>
        </div>
      )}

      {/* Status indicator during streaming */}
      {status === "connected" && (
        <div
          style={{
            position: "absolute",
            top: "8px",
            right: "8px",
            zIndex: 20,
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            gap: "4px",
            fontFamily: "monospace",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              backgroundColor: "rgba(0,0,0,0.5)",
              padding: "4px 10px",
              borderRadius: "12px",
              fontSize: "10px",
              color: "#4ade80",
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
          {debugInfo && (
            <div
              style={{
                backgroundColor: "rgba(0,0,0,0.5)",
                padding: "2px 8px",
                borderRadius: "8px",
                fontSize: "8px",
                color: "#888",
              }}
            >
              {debugInfo}
            </div>
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
