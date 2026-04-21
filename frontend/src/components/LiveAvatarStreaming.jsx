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
import axios from "axios";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";
import { useTranslation } from 'react-i18next';

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
  autoLiveMode = false,
  onStreamReady,
  onDisconnect,
  onError,
  onTextSent,
  onSessionCreated,
  className = "",
}) {
  useTranslation(); // triggers re-render on language change
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
  const autoLivePollRef = useRef(null); // Auto Live speak queue polling
  const lastQueueIdRef = useRef("0"); // Last processed queue item ID
  const autoLiveSpeakingRef = useRef(false); // Whether avatar is currently speaking (for queue pacing)
  const autoLiveQueueRef = useRef([]); // Local buffer of pending texts from queue
  const processedIdsRef = useRef(new Set()); // Dedup processed queue items
  const processedTextsRef = useRef(new Set()); // Dedup by text content
  const speakStartTimeRef = useRef(null); // Track when speaking started for timeout
  const autoLiveModeRef = useRef(false); // Ref to track autoLiveMode for closures
  const pollAndSendQueueRef = useRef(null); // Ref to latest pollAndSendQueue for closures

  // ── Sync autoLiveMode to ref for use in closures (handleDataReceived) ──
  useEffect(() => {
    autoLiveModeRef.current = autoLiveMode;
  }, [autoLiveMode]);

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
          // Reset auto-live speaking flag so next queue item can be sent
          autoLiveSpeakingRef.current = false;
          speakStartTimeRef.current = null;
          // Immediately try to send next item from queue (don't wait for next poll)
          // Use refs to avoid stale closure (handleDataReceived has [] deps)
          if (autoLiveModeRef.current && pollAndSendQueueRef.current) {
            setTimeout(() => pollAndSendQueueRef.current(), 100);
          }
          break;
        case "user.speak_started":
          setIsListening(true);
          break;
        case "user.speak_ended":
          setIsListening(false);
          break;
        case "avatar.transcription":
          // In autoLiveMode, the "Auto" tag is already added when we send the text.
          // avatar.transcription would add a duplicate "AI" entry. Skip it.
          // Use ref to avoid stale closure (handleDataReceived has [] deps)
          if (event.text && !autoLiveModeRef.current) {
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

      // Notify parent with LiveKit credentials so OBS can join the same room
      if (onSessionCreated) {
        onSessionCreated({ session_id, livekit_url, livekit_client_token });
      }

      // 2. Connect to LiveKit room using livekit_url + livekit_client_token
      // ╔══════════════════════════════════════════════════════════════════╗
      // ║ CRITICAL: DO NOT CHANGE adaptiveStream / dynacast to true!     ║
      // ║ adaptiveStream: true causes LiveAvatar video frames to STOP    ║
      // ║ rendering (frameRate drops to 0) because LiveKit pauses the    ║
      // ║ video track when the element is not visible or not properly    ║
      // ║ attached to the DOM. This breaks lip-sync completely.          ║
      // ║ LiveAvatar official SDK uses default Room() (both false).      ║
      // ║ See: SKILL.md → LiveAvatar Lip-Sync Rules                     ║
      // ╚══════════════════════════════════════════════════════════════════╝
      const room = new Room({
        adaptiveStream: false,  // DO NOT CHANGE — breaks lip-sync
        dynacast: false,        // DO NOT CHANGE — breaks lip-sync
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
            // Use the MediaStream from the attached element (most reliable)
            // track.attach() creates a proper MediaStream internally
            const stream = element.srcObject
              || track.mediaStream
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
                // Reset auto-live speaking flag so next queue item can be sent
                autoLiveSpeakingRef.current = false;
                speakStartTimeRef.current = null;
                // Immediately try to send next item from queue
                // Use refs to avoid stale closure (WebSocket handler is created in startSession)
                if (autoLiveModeRef.current && pollAndSendQueueRef.current) {
                  setTimeout(() => pollAndSendQueueRef.current(), 100);
                }
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

  // ══════════════════════════════════════════════════════════
  // Auto Live: Speak Queue Polling + Auto Send
  // ══════════════════════════════════════════════════════════

  // Poll speak queue from backend and send to avatar
  const pollAndSendQueue = useCallback(async () => {
    if (!isConnected) return;

    const baseURL = import.meta.env.VITE_API_BASE_URL;
    try {
      const resp = await axios.get(
        `${baseURL}/api/v1/digital-human/liveavatar/speak-queue/poll`,
        {
          params: { after_id: lastQueueIdRef.current },
          headers: { "X-Admin-Key": "aither:hub" },
          timeout: 5000,
        }
      );

      if (resp.data?.success && resp.data.items?.length > 0) {
        for (const item of resp.data.items) {
          const itemId = String(item.id);
          if (processedIdsRef.current.has(itemId)) continue;
          processedIdsRef.current.add(itemId);

          // Keep set bounded
          if (processedIdsRef.current.size > 200) {
            const arr = Array.from(processedIdsRef.current);
            processedIdsRef.current = new Set(arr.slice(-100));
          }

          // Add to local queue
          autoLiveQueueRef.current.push(item);

          // Update last seen ID
          if (parseInt(itemId) > parseInt(lastQueueIdRef.current || "0")) {
            lastQueueIdRef.current = itemId;
          }
        }
      }
    } catch (err) {
      console.debug("[LiveAvatar AutoLive] Queue poll error:", err.message);
    }

    // Timeout protection: if speaking for more than 45 seconds, force reset
    // (Increased from 15s to handle longer speech segments)
    if (autoLiveSpeakingRef.current && speakStartTimeRef.current) {
      const elapsed = Date.now() - speakStartTimeRef.current;
      if (elapsed > 45000) {
        console.warn("[LiveAvatar AutoLive] Speak timeout (45s), forcing reset");
        autoLiveSpeakingRef.current = false;
        speakStartTimeRef.current = null;
      }
    }

    // Process next item from local queue if not currently speaking
    // Use a loop to skip duplicates and find the next valid item
    while (!autoLiveSpeakingRef.current && autoLiveQueueRef.current.length > 0) {
      const nextItem = autoLiveQueueRef.current.shift();
      if (!nextItem?.text) continue;

      // Content-based dedup: use full text hash to avoid false positives
      // (Previously used only first 30 chars which caused GPT-generated texts
      //  with similar openings to be incorrectly skipped)
      const textKey = nextItem.text.trim();
      if (processedTextsRef.current.has(textKey)) {
        console.warn(`[LiveAvatar AutoLive] Skipping exact duplicate text: "${textKey.substring(0, 50)}..."`);
        continue; // Try next item instead of returning
      }
      processedTextsRef.current.add(textKey);
      if (processedTextsRef.current.size > 100) {
        const arr = Array.from(processedTextsRef.current);
        processedTextsRef.current = new Set(arr.slice(-50));
      }

      console.log(`[LiveAvatar AutoLive] Sending text: "${nextItem.text.substring(0, 60)}..."`);
      autoLiveSpeakingRef.current = true;
      speakStartTimeRef.current = Date.now();
      sendEvent("avatar.speak_text", { text: nextItem.text });

      // Also push to OBS queue and notify parent (fire-and-forget, don't block)
      if (onTextSent) {
        try { onTextSent(nextItem.text); } catch (e) {}
      }

      // Add to speak history
      setSpeakHistory((prev) => [
        { text: nextItem.text, timestamp: new Date().toLocaleTimeString(), type: "auto" },
        ...prev,
      ].slice(0, 50));

      // Mark as consumed so backend knows to keep generating
      try {
        aiLiveCreatorService.autoLiveMarkConsumed(sessionId, 1);
      } catch (e) {
        console.debug("[LiveAvatar AutoLive] mark-consumed error:", e);
      }

      break; // Successfully sent one item, wait for speak_ended before next
    }
  }, [isConnected, sendEvent, onTextSent, sessionId, autoLiveMode]);

  // Keep ref in sync so handleDataReceived closure can call latest version
  useEffect(() => {
    pollAndSendQueueRef.current = pollAndSendQueue;
  }, [pollAndSendQueue]);

  // Start/stop auto live polling based on autoLiveMode prop
  useEffect(() => {
    if (autoLiveMode && isConnected) {
      console.log("[LiveAvatar AutoLive] Starting speak queue polling");
      // Reset state for fresh auto-live session
      autoLiveQueueRef.current = [];
      autoLiveSpeakingRef.current = false;
      processedTextsRef.current = new Set(); // Clear dedup cache for new session
      speakStartTimeRef.current = null;

      // Poll every 1 second for faster response
      autoLivePollRef.current = setInterval(() => {
        pollAndSendQueue();
      }, 1000);

      return () => {
        if (autoLivePollRef.current) {
          clearInterval(autoLivePollRef.current);
          autoLivePollRef.current = null;
        }
      };
    } else {
      // Stop polling
      if (autoLivePollRef.current) {
        clearInterval(autoLivePollRef.current);
        autoLivePollRef.current = null;
        console.log("[LiveAvatar AutoLive] Stopped speak queue polling");
      }
    }
  }, [autoLiveMode, isConnected, pollAndSendQueue]);

  // Listen for avatar.speak_ended to pace auto-live queue processing
  // (Update autoLiveSpeakingRef when avatar finishes speaking)
  useEffect(() => {
    // This is handled in handleDataReceived via avatar.speak_ended event
    // We just need to reset the flag when speaking ends
  }, []);

  // ── Speak Text (direct TTS) ──
  const handleSpeak = useCallback(() => {
    if (!speakText.trim() || !isConnected) return;

    const text = speakText.trim();

    // Send via LiveKit data channel
    sendEvent("avatar.speak_text", { text });

    // Notify parent (used to forward to OBS window)
    if (onTextSent) onTextSent(text);

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
    disconnected: { color: "gray", label: window.__t('auto_345', '未接続') },
    connecting: { color: "yellow", label: window.__t('auto_342', '接続中...') },
    connected: { color: "green", label: "LIVE" },
    reconnecting: { color: "orange", label: window.__t('auto_331', '再接続中...') },
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
            <p className="text-xs text-gray-500">{window.__t('auto_341', '接続ボタンを押してストリーミング開始')}</p>
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
            <p className="text-sm text-green-300">{window.__t('auto_342', '接続中...')}</p>
            <p className="text-xs text-gray-400 mt-1">{window.__t('auto_301', 'LiveKit WebRTC セッション確立中')}</p>
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
                title={window.__t('auto_307', 'そのまま話す (Enter)')}
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
          <h5 className="text-[10px] font-medium text-gray-400 mb-2">{window.__t('auto_349', '発話履歴')}</h5>
          <div className="space-y-1.5">
            {speakHistory.map((item, i) => (
              <div key={i} className="flex items-start gap-2">
                <span className="text-[9px] text-gray-500 font-mono shrink-0 mt-0.5">{item.timestamp}</span>
                <span className={`text-[9px] shrink-0 mt-0.5 px-1 rounded ${
                  item.type === "sent" ? "bg-green-500/20 text-green-400" :
                  item.type === "auto" ? "bg-amber-500/20 text-amber-400" :
                  item.type === "avatar" ? "bg-blue-500/20 text-blue-400" :
                  "bg-purple-500/20 text-purple-400"
                }`}>
                  {item.type === "sent" ? "\u9001\u4fe1" : item.type === "auto" ? "Auto" : item.type === "avatar" ? "AI" : "You"}
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
