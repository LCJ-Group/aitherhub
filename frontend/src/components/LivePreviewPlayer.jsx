import { useState, useEffect, useRef, useCallback } from "react";
import {
  Play,
  Pause,
  SkipForward,
  Volume2,
  VolumeX,
  Maximize2,
  Minimize2,
  Radio,
  Eye,
  Heart,
  Gift,
  ShoppingBag,
  MessageSquare,
  Loader2,
  Sparkles,
  Crown,
  Mic,
  MicOff,
} from "lucide-react";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";
/**
 * LivePreviewPlayer — TikTok Live-style 9:16 Preview Player
 *
 * Architecture (Lip-Sync Video):
 *   - Portrait VIDEO loops continuously (muted) — idle/fallback visual
 *   - When AutoPilot generates a lip-synced video segment, it plays THAT video
 *     (with audio) instead of the loop + separate TTS audio
 *   - Falls back to loop + TTS audio if lip-sync video is not available
 *   - Supports AutoPilot mode: brain auto-generates scripts + TTS + lip-sync video
 *
 * Features:
 *   - 9:16 vertical video loop playback
 *   - Lip-synced video segment playback (replaces loop during speech)
 *   - Fallback: TTS audio overlay when lip-sync unavailable
 *   - AutoPilot: automatic script cycling with lip-sync
 *   - TikTok-style comment overlay
 *   - Product info overlay
 *   - Live status indicators
 *   - Fullscreen toggle
 */
export default function LivePreviewPlayer({
  sessionId,
  engine,
  portraitVideoUrl,
  avatarPreviewUrl,
  videoQueue = [],
  commentHistory = [],
  products = [],
  currentProduct,
  isLive = false,
  autoPilotActive = false,
  voiceId,
  language = "ja",
  onVideoEnded,
  onRequestNextVideo,
  onAutoPilotStateChange,
  onSpeakingChange,
  liveAvatarStream = null,
  liveAvatarConnected = false,
}) {
  // ── Video Loop State ──
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [videoReady, setVideoReady] = useState(false);

  // ── TTS Audio State ──
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [currentSpeechText, setCurrentSpeechText] = useState("");
  const [currentScriptType, setCurrentScriptType] = useState("");
  const [audioLoading, setAudioLoading] = useState(false);
  const [speechQueue, setSpeechQueue] = useState([]);

  // ── Lip-Sync Video State ──
  const [lipSyncPlaying, setLipSyncPlaying] = useState(false);

  // ── AutoPilot State ──
  const [autoPilotState, setAutoPilotState] = useState("idle");
  const [autoPilotProductIndex, setAutoPilotProductIndex] = useState(0);
  const [autoPilotScriptType, setAutoPilotScriptType] = useState("introduction");
  const [totalSpeaks, setTotalSpeaks] = useState(0);

  // ── Simulated Live Stats ──
  const [viewerCount, setViewerCount] = useState(0);
  const [likeCount, setLikeCount] = useState(0);
  const [showProductCard, setShowProductCard] = useState(true);

  // ── Comment Display ──
  const [visibleComments, setVisibleComments] = useState([]);
  const [floatingHearts, setFloatingHearts] = useState([]);

  // ── Subtitle Display ──
  const [subtitleText, setSubtitleText] = useState("");
  const [showSubtitle, setShowSubtitle] = useState(false);

  // ── Refs ──
  const loopVideoRef = useRef(null);     // Background loop video (muted)
  const lipSyncVideoRef = useRef(null);  // Lip-sync segment video (with audio)
  const liveAvatarVideoRef = useRef(null); // LiveAvatar WebRTC stream
  const audioRef = useRef(null);          // Fallback TTS audio
  const containerRef = useRef(null);
  const heartIdRef = useRef(0);
  const autoPilotTimerRef = useRef(null);
  const isSpeakingRef = useRef(false);
  const autoPilotActiveRef = useRef(false);

  // Backward-compat alias
  const videoRef = loopVideoRef;

  // Keep refs in sync
  useEffect(() => {
    isSpeakingRef.current = isSpeaking;
  }, [isSpeaking]);
  useEffect(() => {
    autoPilotActiveRef.current = autoPilotActive;
  }, [autoPilotActive]);

  // ══════════════════════════════════════════════
  // LiveAvatar Stream — Attach WebRTC MediaStream to video element
  // ══════════════════════════════════════════════
  useEffect(() => {
    if (liveAvatarVideoRef.current && liveAvatarStream) {
      liveAvatarVideoRef.current.srcObject = liveAvatarStream;
      liveAvatarVideoRef.current.play().catch(() => {});
    }
    if (liveAvatarVideoRef.current && !liveAvatarStream) {
      liveAvatarVideoRef.current.srcObject = null;
    }
  }, [liveAvatarStream]);

  // ══════════════════════════════════════════════
  // Video Loop — Portrait video plays continuously (background)
  // ══════════════════════════════════════════════

  // Start video loop when portrait URL is available
  useEffect(() => {
    if (portraitVideoUrl && loopVideoRef.current) {
      loopVideoRef.current.src = portraitVideoUrl;
      loopVideoRef.current.load();
    }
  }, [portraitVideoUrl]);

  const handleVideoCanPlay = () => {
    setVideoReady(true);
    if (loopVideoRef.current && (isLive || autoPilotActive)) {
      loopVideoRef.current.play().catch(() => {});
      setIsPlaying(true);
    }
  };

  const handleVideoLoop = () => {
    // Video ended — loop it
    if (loopVideoRef.current) {
      loopVideoRef.current.currentTime = 0;
      loopVideoRef.current.play().catch(() => {});
    }
  };

  // ══════════════════════════════════════════════
  // Lip-Sync Video Playback
  // ══════════════════════════════════════════════

  /**
   * Play a lip-synced video segment. Hides the loop video and shows the
   * lip-sync video with audio. When it ends, switches back to loop.
   */
  const playLipSyncVideo = useCallback((videoUrl, text, scriptType) => {
    if (!lipSyncVideoRef.current) return;

    console.log("[LipSync] Playing lip-sync video:", videoUrl?.substring(0, 80));

    setAudioLoading(true);
    setCurrentSpeechText(text || "");
    setCurrentScriptType(scriptType || "");

    lipSyncVideoRef.current.src = videoUrl;
    lipSyncVideoRef.current.load();

    lipSyncVideoRef.current.oncanplaythrough = () => {
      setAudioLoading(false);
      setIsSpeaking(true);
      isSpeakingRef.current = true;
      setLipSyncPlaying(true);
      setShowSubtitle(true);
      setSubtitleText(text || "");
      onSpeakingChange?.(true);

      // Pause loop video while lip-sync plays
      if (loopVideoRef.current) {
        loopVideoRef.current.pause();
      }

      lipSyncVideoRef.current.play().catch((err) => {
        console.error("[LipSync] Video play error:", err);
        setIsSpeaking(false);
        isSpeakingRef.current = false;
        setLipSyncPlaying(false);
        onSpeakingChange?.(false);
        // Resume loop
        if (loopVideoRef.current) {
          loopVideoRef.current.play().catch(() => {});
        }
      });
    };

    lipSyncVideoRef.current.onerror = (e) => {
      console.error("[LipSync] Video load error:", e);
      setAudioLoading(false);
      setIsSpeaking(false);
      isSpeakingRef.current = false;
      setLipSyncPlaying(false);
      onSpeakingChange?.(false);
      // Resume loop
      if (loopVideoRef.current) {
        loopVideoRef.current.play().catch(() => {});
      }
      // If autopilot, request next after error
      if (autoPilotActiveRef.current) {
        setTimeout(() => requestAutoPilotNext(), 2000);
      }
    };
  }, [onSpeakingChange]);

  const handleLipSyncEnded = useCallback(() => {
    console.log("[LipSync] Segment ended, returning to loop");
    setIsSpeaking(false);
    isSpeakingRef.current = false;
    setLipSyncPlaying(false);
    setShowSubtitle(false);
    setSubtitleText("");
    onSpeakingChange?.(false);

    // Resume loop video
    if (loopVideoRef.current && portraitVideoUrl) {
      loopVideoRef.current.play().catch(() => {});
    }

    // If autopilot is active, request next segment after a brief pause
    if (autoPilotActiveRef.current) {
      setTimeout(() => requestAutoPilotNext(), 1500);
    }
  }, [onSpeakingChange, portraitVideoUrl]);

  // ══════════════════════════════════════════════
  // TTS Audio Playback (Fallback when no lip-sync video)
  // ══════════════════════════════════════════════

  /**
   * Play TTS audio from a URL. Shows subtitle text while speaking.
   * Used as fallback when lip-sync video is not available.
   */
  const playTTSAudio = useCallback((audioUrl, text, scriptType) => {
    if (!audioRef.current) return;

    console.log("[TTS Fallback] Playing audio:", audioUrl?.substring(0, 80));

    setAudioLoading(true);
    setCurrentSpeechText(text || "");
    setCurrentScriptType(scriptType || "");

    audioRef.current.src = audioUrl;
    audioRef.current.load();

    audioRef.current.oncanplaythrough = () => {
      setAudioLoading(false);
      setIsSpeaking(true);
      isSpeakingRef.current = true;
      setShowSubtitle(true);
      setSubtitleText(text || "");
      onSpeakingChange?.(true);
      audioRef.current.play().catch((err) => {
        console.error("TTS audio play error:", err);
        setIsSpeaking(false);
        isSpeakingRef.current = false;
        onSpeakingChange?.(false);
      });
    };

    audioRef.current.onerror = (e) => {
      console.error("TTS audio load error:", e);
      setAudioLoading(false);
      setIsSpeaking(false);
      isSpeakingRef.current = false;
      onSpeakingChange?.(false);
      // If autopilot, request next after error
      if (autoPilotActiveRef.current) {
        setTimeout(() => requestAutoPilotNext(), 2000);
      }
    };
  }, [onSpeakingChange]);

  const handleAudioEnded = useCallback(() => {
    setIsSpeaking(false);
    isSpeakingRef.current = false;
    setShowSubtitle(false);
    setSubtitleText("");
    onSpeakingChange?.(false);

    // If autopilot is active, request next segment after a brief pause
    if (autoPilotActiveRef.current) {
      setTimeout(() => requestAutoPilotNext(), 1500);
    }
  }, [onSpeakingChange]);

  // ══════════════════════════════════════════════
  // AutoPilot — Brain auto-generates scripts + TTS + Lip-Sync
  // ══════════════════════════════════════════════

  const requestAutoPilotNext = useCallback(async () => {
    if (!sessionId || !autoPilotActiveRef.current || isSpeakingRef.current) return;

    try {
      setAudioLoading(true);

      // Collect pending comments
      const pendingComments = commentHistory
        .filter((c) => !c.replied)
        .slice(0, 3)
        .map((c) => ({
          text: c.comment,
          name: c.commenter,
        }));

      const result = await aiLiveCreatorService.getAutoPilotNext(sessionId, {
        current_state: autoPilotState,
        current_product_index: autoPilotProductIndex,
        current_script_type: autoPilotScriptType,
        pending_comments: pendingComments.length > 0 ? pendingComments : null,
        language: language || "ja",
        voice_id: voiceId || null,
      });

      if (!result.success) {
        console.error("AutoPilot next error:", result.error);
        setAudioLoading(false);
        // Retry after delay
        if (autoPilotActiveRef.current) {
          setTimeout(() => requestAutoPilotNext(), 5000);
        }
        return;
      }

      // Update autopilot state
      if (result.next_state) setAutoPilotState(result.next_state);
      if (result.product_index !== undefined && result.product_index !== null) {
        setAutoPilotProductIndex(result.product_index);
      }
      if (result.script_type) setAutoPilotScriptType(result.script_type);
      setTotalSpeaks((prev) => prev + 1);

      onAutoPilotStateChange?.({
        state: result.next_state,
        productIndex: result.product_index,
        scriptType: result.script_type,
        action: result.action,
        text: result.text,
        productName: result.product_name,
        videoUrl: result.video_url,
      });

      // Priority: Play lip-sync video if available, otherwise fallback to TTS audio
      if (result.video_url) {
        console.log("[AutoPilot] Lip-sync video available, playing video segment");
        playLipSyncVideo(result.video_url, result.text, result.script_type);
      } else if (result.audio_url) {
        console.log("[AutoPilot] No lip-sync video, falling back to TTS audio");
        playTTSAudio(result.audio_url, result.text, result.script_type);
      } else {
        setAudioLoading(false);
        // No audio or video — try again
        if (autoPilotActiveRef.current) {
          setTimeout(() => requestAutoPilotNext(), 3000);
        }
      }
    } catch (err) {
      console.error("AutoPilot next request failed:", err);
      setAudioLoading(false);
      if (autoPilotActiveRef.current) {
        setTimeout(() => requestAutoPilotNext(), 5000);
      }
    }
  }, [
    sessionId,
    autoPilotState,
    autoPilotProductIndex,
    autoPilotScriptType,
    commentHistory,
    playLipSyncVideo,
    playTTSAudio,
    onAutoPilotStateChange,
  ]);

  // Start/stop autopilot
  useEffect(() => {
    if (autoPilotActive && sessionId && !isSpeaking && !audioLoading) {
      // Start video loop (if portrait video available)
      if (loopVideoRef.current && portraitVideoUrl) {
        loopVideoRef.current.play().catch(() => {});
        setIsPlaying(true);
      }
      // For HeyGen avatar mode: mark as playing even without video loop
      if (avatarPreviewUrl && !portraitVideoUrl) {
        setIsPlaying(true);
      }
      // Request first segment
      requestAutoPilotNext();
    }

    if (!autoPilotActive) {
      // Stop autopilot
      if (autoPilotTimerRef.current) {
        clearTimeout(autoPilotTimerRef.current);
        autoPilotTimerRef.current = null;
      }
      // Stop lip-sync video if playing
      if (lipSyncVideoRef.current) {
        lipSyncVideoRef.current.pause();
        lipSyncVideoRef.current.src = "";
      }
      setLipSyncPlaying(false);
    }
  }, [autoPilotActive, sessionId]);

  // ══════════════════════════════════════════════
  // Manual TTS Speak (from external trigger)
  // ══════════════════════════════════════════════

  // Expose speak function via callback
  const speakText = useCallback(
    async (text, scriptType = "script", productName = null) => {
      if (!sessionId || !text) return;

      try {
        setAudioLoading(true);
        const result = await aiLiveCreatorService.speak(sessionId, {
          text,
          speak_type: scriptType,
          product_name: productName,
          language: language || "ja",
        });

        if (result.success && result.audio_url) {
          playTTSAudio(result.audio_url, text, scriptType);
        } else {
          setAudioLoading(false);
          console.error("Speak failed:", result.error);
        }
      } catch (err) {
        setAudioLoading(false);
        console.error("Speak request failed:", err);
      }
    },
    [sessionId, playTTSAudio]
  );

  // Attach speakText to window for external access
  useEffect(() => {
    if (sessionId) {
      window.__aitherhub_speak = speakText;
    }
    return () => {
      delete window.__aitherhub_speak;
    };
  }, [sessionId, speakText]);

  // Expose playVideo function for Quick Generate integration
  useEffect(() => {
    window.__aitherhub_playVideo = (videoUrl, text, scriptType) => {
      if (videoUrl) {
        playLipSyncVideo(videoUrl, text || "", scriptType || "generated");
      }
    };
    return () => {
      delete window.__aitherhub_playVideo;
    };
  }, [playLipSyncVideo]);

  // ══════════════════════════════════════════════
  // Simulated Live Stats
  // ══════════════════════════════════════════════

  useEffect(() => {
    if (!autoPilotActive && !isLive) return;
    // Simulate viewer count changes
    setViewerCount(Math.floor(100 + Math.random() * 150));
    const interval = setInterval(() => {
      setViewerCount((prev) => {
        const delta = Math.floor(Math.random() * 20) - 8;
        return Math.max(50, prev + delta);
      });
    }, 8000);
    return () => clearInterval(interval);
  }, [autoPilotActive, isLive]);

  // Update visible comments
  useEffect(() => {
    setVisibleComments(commentHistory.slice(0, 5));
  }, [commentHistory]);

  // Active product
  const activeProduct =
    currentProduct || products[autoPilotProductIndex] || products[0] || null;

  // ══════════════════════════════════════════════
  // UI Controls
  // ══════════════════════════════════════════════

  const togglePlayPause = () => {
    if (!loopVideoRef.current) return;
    if (loopVideoRef.current.paused) {
      loopVideoRef.current.play();
      setIsPlaying(true);
    } else {
      loopVideoRef.current.pause();
      setIsPlaying(false);
    }
  };

  const toggleMute = () => {
    setIsMuted((prev) => !prev);
  };

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (isFullscreen) {
      document.exitFullscreen?.();
    } else {
      containerRef.current.requestFullscreen?.();
    }
    setIsFullscreen((prev) => !prev);
  };

  const addFloatingHeart = () => {
    setLikeCount((prev) => prev + 1);
    const id = heartIdRef.current++;
    const x = 5 + Math.random() * 15;
    const delay = Math.random() * 0.3;
    setFloatingHearts((prev) => [...prev, { id, x, delay }]);
    setTimeout(() => {
      setFloatingHearts((prev) => prev.filter((h) => h.id !== id));
    }, 2500);
  };

  // ══════════════════════════════════════════════
  // Render
  // ══════════════════════════════════════════════

  return (
    <div
      ref={containerRef}
      className="relative bg-black rounded-2xl overflow-hidden shadow-2xl"
      style={{
        aspectRatio: "9/16",
        maxHeight: isFullscreen ? "100vh" : "720px",
        width: isFullscreen ? "100%" : undefined,
      }}
    >
      {/* ── Hidden Audio Element for TTS Fallback ── */}
      <audio
        ref={audioRef}
        onEnded={handleAudioEnded}
        muted={isMuted}
        style={{ display: "none" }}
      />

      {/* ── LiveAvatar WebRTC Stream Layer (Realtime mode) ── */}
      {engine === "realtime" && (
        <>
          <video
            ref={liveAvatarVideoRef}
            className="absolute inset-0 w-full h-full object-cover bg-black"
            style={{ zIndex: liveAvatarConnected ? 10 : -1, opacity: liveAvatarConnected ? 1 : 0, transition: "opacity 0.3s ease" }}
            autoPlay
            playsInline
            muted={false}
          />
          {!liveAvatarConnected && (
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-gradient-to-b from-gray-900 via-gray-800 to-black" style={{ zIndex: 1 }}>
              <div className="relative mb-6">
                <div className="w-24 h-24 rounded-full bg-gradient-to-r from-green-500 to-emerald-500 flex items-center justify-center">
                  <Radio className="w-10 h-10 text-white" />
                </div>
                <div className="absolute -bottom-1 -right-1 w-8 h-8 bg-gradient-to-r from-pink-500 to-red-500 rounded-full flex items-center justify-center border-2 border-black">
                  <Sparkles className="w-4 h-4 text-white" />
                </div>
              </div>
              <h3 className="text-white text-lg font-bold mb-1">AI Live Creator</h3>
              <p className="text-gray-400 text-xs mb-2">LiveAvatar Realtime</p>
              <p className="text-gray-500 text-[11px] text-center px-8">
                右側の「ストリーミング開始」を押してアバターを起動
              </p>
            </div>
          )}
        </>
      )}

      {/* ── Background Video Layer (Looping Portrait — always present) ── */}
      {engine !== "realtime" && portraitVideoUrl ? (
        <video
          ref={loopVideoRef}
          className="absolute inset-0 w-full h-full object-cover bg-black"
          style={{
            opacity: lipSyncPlaying ? 0 : 1,
            transition: "opacity 0.3s ease",
          }}
          autoPlay
          playsInline
          muted
          loop
          onCanPlay={handleVideoCanPlay}
          onEnded={handleVideoLoop}
          onPlay={() => setIsPlaying(true)}
          onPause={() => {
            if (!lipSyncPlaying) setIsPlaying(false);
          }}
          onError={(e) => {
            console.error("Video loop error:", e);
          }}
        />
      ) : engine !== "realtime" && avatarPreviewUrl ? (
        /* ── HeyGen Avatar Preview (static image background) ── */
        <div
          className="absolute inset-0 bg-black"
          style={{
            opacity: lipSyncPlaying ? 0 : 1,
            transition: "opacity 0.3s ease",
          }}
        >
          <img
            src={avatarPreviewUrl}
            alt="Digital Twin Avatar"
            className="w-full h-full object-cover"
          />
          {/* Generating overlay when autopilot is active and loading */}
          {autoPilotActive && audioLoading && (
            <div className="absolute inset-0 bg-black/40 flex flex-col items-center justify-center">
              <Loader2 className="w-10 h-10 text-amber-400 animate-spin mb-3" />
              <p className="text-amber-300 text-sm font-medium">Generating lip-sync video...</p>
              <p className="text-gray-400 text-[10px] mt-1">HeyGen Digital Twin is speaking</p>
            </div>
          )}
        </div>
      ) : engine !== "realtime" ? (
        /* ── Idle / Waiting Screen (non-realtime modes only) ── */
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-gradient-to-b from-gray-900 via-gray-800 to-black">
          <div className="relative mb-6">
            <div className="w-24 h-24 rounded-full bg-gradient-to-r from-indigo-500 to-purple-500 flex items-center justify-center">
              <Radio className="w-10 h-10 text-white" />
            </div>
            <div className="absolute -bottom-1 -right-1 w-8 h-8 bg-gradient-to-r from-pink-500 to-red-500 rounded-full flex items-center justify-center border-2 border-black">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
          </div>
          <h3 className="text-white text-lg font-bold mb-1">AI Live Creator</h3>
          <p className="text-gray-400 text-xs mb-4">
            {engine === "heygen" ? "Select a Digital Twin avatar" : "Upload a portrait video to start"}
          </p>
          <p className="text-gray-500 text-[11px] leading-relaxed text-center px-8">
            {engine === "heygen"
              ? "Select a Digital Twin avatar, add products, then start the AI autopilot to begin your live stream."
              : "Upload a 9:16 digital human video, add products, then start the AI autopilot to begin your live stream."
            }
          </p>
        </div>
      ) : null}

      {/* ── Lip-Sync Video Layer (overlays loop during speech) ── */}
      <video
        ref={lipSyncVideoRef}
        className="absolute inset-0 w-full h-full object-cover bg-black"
        style={{
          opacity: lipSyncPlaying ? 1 : 0,
          transition: "opacity 0.3s ease",
          pointerEvents: lipSyncPlaying ? "auto" : "none",
          zIndex: lipSyncPlaying ? 5 : -1,
        }}
        playsInline
        muted={isMuted}
        onEnded={handleLipSyncEnded}
        onError={(e) => {
          console.error("[LipSync] Video error:", e);
          handleLipSyncEnded();
        }}
      />

      {/* ── Speaking Indicator ── */}
      {(isSpeaking || audioLoading) && (
        <div className="absolute top-14 left-1/2 -translate-x-1/2 z-30">
          <div className="flex items-center gap-2 bg-black/60 backdrop-blur-sm px-3 py-1.5 rounded-full">
            {audioLoading ? (
              <>
                <Loader2 className="w-3.5 h-3.5 text-cyan-400 animate-spin" />
                <span className="text-[10px] text-cyan-300">
                  {lipSyncPlaying ? "Loading video..." : "Generating speech..."}
                </span>
              </>
            ) : (
              <>
                <div className="flex items-center gap-0.5">
                  {[1, 2, 3, 4, 5].map((i) => (
                    <div
                      key={i}
                      className="w-0.5 bg-cyan-400 rounded-full"
                      style={{
                        height: `${6 + Math.random() * 10}px`,
                        animation: `pulse-bar 0.6s ease-in-out ${i * 0.1}s infinite alternate`,
                      }}
                    />
                  ))}
                </div>
                <span className="text-[10px] text-cyan-300">Speaking</span>
              </>
            )}
          </div>
        </div>
      )}

      {/* ── Lip-Sync / Video Mode Badge ── */}
      {lipSyncPlaying && (
        <div className="absolute top-8 right-3 z-30">
          <span className="text-[8px] bg-green-500/40 text-green-200 px-2 py-0.5 rounded-full backdrop-blur-sm flex items-center gap-1">
            <Crown className="w-2.5 h-2.5" /> Video
          </span>
        </div>
      )}

      {/* ── Subtitle Overlay ── */}
      {showSubtitle && subtitleText && (
        <div className="absolute bottom-44 left-3 right-3 z-25">
          <div className="bg-black/70 backdrop-blur-sm rounded-lg px-3 py-2 text-center">
            <p className="text-white text-[12px] leading-relaxed line-clamp-3">
              {subtitleText}
            </p>
          </div>
        </div>
      )}

      {/* ── Top Bar: Live Status ── */}
      <div className="absolute top-0 left-0 right-0 p-3 z-20 bg-gradient-to-b from-black/60 to-transparent">
        <div className="flex items-center justify-between">
          {/* Live Badge + Viewer Count */}
          <div className="flex items-center gap-2">
            <div
              className={`flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold ${
                autoPilotActive || isPlaying || avatarPreviewUrl || liveAvatarConnected
                  ? "bg-red-500 text-white"
                  : "bg-gray-600/80 text-gray-300"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  autoPilotActive || isPlaying || liveAvatarConnected ? "bg-white animate-pulse" : "bg-gray-400"
                }`}
              />
              {autoPilotActive ? "LIVE" : liveAvatarConnected ? "LIVE" : (isPlaying || avatarPreviewUrl) ? "PREVIEW" : "OFFLINE"}
            </div>
            <div className="flex items-center gap-1 bg-black/40 px-2 py-1 rounded-full">
              <Eye className="w-3 h-3 text-white/70" />
              <span className="text-[10px] text-white/90 font-medium">
                {viewerCount.toLocaleString()}
              </span>
            </div>
          </div>

          {/* Controls */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={toggleMute}
              className="p-1.5 bg-black/40 hover:bg-black/60 rounded-full transition-colors"
            >
              {isMuted ? (
                <VolumeX className="w-3.5 h-3.5 text-white/80" />
              ) : (
                <Volume2 className="w-3.5 h-3.5 text-white/80" />
              )}
            </button>
            <button
              onClick={toggleFullscreen}
              className="p-1.5 bg-black/40 hover:bg-black/60 rounded-full transition-colors"
            >
              {isFullscreen ? (
                <Minimize2 className="w-3.5 h-3.5 text-white/80" />
              ) : (
                <Maximize2 className="w-3.5 h-3.5 text-white/80" />
              )}
            </button>
          </div>
        </div>

        {/* Status Badges */}
        <div className="mt-2 flex items-center gap-2 flex-wrap">
          {autoPilotActive && (
            <span className="text-[9px] bg-green-500/30 text-green-200 px-2 py-0.5 rounded-full flex items-center gap-1 backdrop-blur-sm">
              <Sparkles className="w-2.5 h-2.5" /> AutoPilot
            </span>
          )}
          {isSpeaking && (
            <span className="text-[9px] bg-cyan-500/30 text-cyan-200 px-2 py-0.5 rounded-full flex items-center gap-1 backdrop-blur-sm">
              <Mic className="w-2.5 h-2.5" /> {currentScriptType || "Speaking"}
            </span>
          )}
          {lipSyncPlaying && (
            <span className="text-[9px] bg-purple-500/30 text-purple-200 px-2 py-0.5 rounded-full flex items-center gap-1 backdrop-blur-sm">
              <Crown className="w-2.5 h-2.5" /> Lip-Sync
            </span>
          )}
          {totalSpeaks > 0 && (
            <span className="text-[9px] bg-white/10 text-white/70 px-2 py-0.5 rounded-full backdrop-blur-sm">
              {totalSpeaks} segments
            </span>
          )}
        </div>
      </div>

      {/* ── Right Side: Action Buttons (TikTok-style) ── */}
      <div className="absolute right-2 bottom-40 flex flex-col items-center gap-4 z-20">
        {/* Like Button */}
        <button
          onClick={addFloatingHeart}
          className="flex flex-col items-center gap-0.5"
        >
          <div className="w-10 h-10 bg-black/30 backdrop-blur-sm rounded-full flex items-center justify-center hover:bg-red-500/40 transition-colors">
            <Heart className="w-5 h-5 text-white" />
          </div>
          <span className="text-[9px] text-white/80 font-medium">
            {likeCount > 0 ? likeCount.toLocaleString() : "Like"}
          </span>
        </button>

        {/* Comment Count */}
        <div className="flex flex-col items-center gap-0.5">
          <div className="w-10 h-10 bg-black/30 backdrop-blur-sm rounded-full flex items-center justify-center">
            <MessageSquare className="w-5 h-5 text-white" />
          </div>
          <span className="text-[9px] text-white/80 font-medium">
            {commentHistory.length}
          </span>
        </div>

        {/* Gift */}
        <div className="flex flex-col items-center gap-0.5">
          <div className="w-10 h-10 bg-black/30 backdrop-blur-sm rounded-full flex items-center justify-center">
            <Gift className="w-5 h-5 text-white" />
          </div>
          <span className="text-[9px] text-white/80 font-medium">Gift</span>
        </div>

        {/* Speaking Status */}
        <div className="flex flex-col items-center gap-0.5">
          <div
            className={`w-10 h-10 backdrop-blur-sm rounded-full flex items-center justify-center ${
              isSpeaking
                ? "bg-cyan-500/40 ring-2 ring-cyan-400/50"
                : "bg-black/30"
            }`}
          >
            {isSpeaking ? (
              <Mic className="w-5 h-5 text-cyan-300" />
            ) : (
              <MicOff className="w-5 h-5 text-white/50" />
            )}
          </div>
          <span className="text-[9px] text-white/80 font-medium">
            {isSpeaking ? "Live" : "Muted"}
          </span>
        </div>
      </div>

      {/* ── Floating Hearts Animation ── */}
      {floatingHearts.map((heart) => (
        <div
          key={heart.id}
          className="absolute z-30 pointer-events-none"
          style={{
            right: `${heart.x}%`,
            bottom: "35%",
            animation: `float-heart 2s ease-out ${heart.delay}s forwards`,
          }}
        >
          <Heart
            className="w-6 h-6 text-red-500 fill-red-500"
            style={{ opacity: 0.9 }}
          />
        </div>
      ))}

      {/* ── Comment Overlay (TikTok-style) ── */}
      <div className="absolute left-3 bottom-36 right-16 z-20 space-y-1.5 max-h-40 overflow-hidden">
        {visibleComments.map((item, idx) => (
          <div
            key={item.id || idx}
            className={`flex items-start gap-1.5 transition-all duration-500 ${
              idx === 0 ? "opacity-100" : idx < 3 ? "opacity-80" : "opacity-50"
            }`}
          >
            <div className="flex-shrink-0 w-6 h-6 rounded-full bg-gradient-to-r from-indigo-500 to-purple-500 flex items-center justify-center">
              <span className="text-[8px] text-white font-bold">
                {item.commenter ? item.commenter[0].toUpperCase() : "?"}
              </span>
            </div>
            <div className="bg-black/30 backdrop-blur-sm rounded-lg px-2 py-1 max-w-[85%]">
              <span className="text-[9px] text-yellow-300 font-medium mr-1">
                {item.commenter || "Viewer"}
              </span>
              <span className="text-[10px] text-white/90">{item.comment}</span>
              {item.reply && (
                <div className="mt-0.5 pt-0.5 border-t border-white/10">
                  <span className="text-[9px] text-cyan-300 font-medium mr-1">
                    AI Host
                  </span>
                  <span className="text-[10px] text-white/80 line-clamp-2">
                    {item.reply}
                  </span>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* ── Product Card Overlay ── */}
      {activeProduct && showProductCard && (
        <div className="absolute left-3 bottom-20 right-16 z-20">
          <div className="bg-black/40 backdrop-blur-md rounded-xl p-2.5 border border-white/10">
            <div className="flex items-center gap-2">
              {activeProduct.image_url ? (
                <img
                  src={activeProduct.image_url}
                  alt={activeProduct.name}
                  className="w-12 h-12 rounded-lg object-cover border border-white/20"
                  onError={(e) => {
                    e.target.style.display = "none";
                  }}
                />
              ) : (
                <div className="w-12 h-12 rounded-lg bg-white/10 flex items-center justify-center">
                  <ShoppingBag className="w-5 h-5 text-white/50" />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <p className="text-[11px] text-white font-medium truncate">
                  {activeProduct.name}
                </p>
                {activeProduct.price && (
                  <p className="text-[12px] text-orange-400 font-bold">
                    {activeProduct.price}
                  </p>
                )}
                {activeProduct.source === "tiktok_shop" && (
                  <span className="text-[8px] bg-pink-500/30 text-pink-200 px-1.5 py-0.5 rounded-full">
                    TikTok Shop
                  </span>
                )}
              </div>
              <button className="bg-orange-500 hover:bg-orange-600 text-white text-[10px] font-bold px-3 py-1.5 rounded-full transition-colors flex items-center gap-1">
                <ShoppingBag className="w-3 h-3" />
                Buy
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Bottom Bar ── */}
      <div className="absolute bottom-0 left-0 right-0 z-20">
        <div className="bg-gradient-to-t from-black/80 to-transparent px-3 pb-3 pt-6">
          <div className="flex items-center justify-between">
            {/* Play/Pause */}
            <div className="flex items-center gap-2">
              {(portraitVideoUrl || avatarPreviewUrl) && (
                <button
                  onClick={togglePlayPause}
                  className="p-1.5 bg-white/10 hover:bg-white/20 rounded-full transition-colors"
                >
                  {isPlaying ? (
                    <Pause className="w-4 h-4 text-white" />
                  ) : (
                    <Play className="w-4 h-4 text-white" />
                  )}
                </button>
              )}
              {autoPilotActive && (
                <span className="text-[10px] text-green-400">
                  AutoPilot Active
                </span>
              )}
            </div>

            {/* Status Info */}
            <div className="flex items-center gap-2">
              {products.length > 0 && (
                <span className="text-[9px] text-white/50 bg-white/10 px-2 py-0.5 rounded-full">
                  Product {autoPilotProductIndex + 1}/{products.length}
                </span>
              )}
              {lipSyncPlaying && (
                <span className="text-[9px] text-purple-300 bg-purple-500/20 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <Crown className="w-2.5 h-2.5" />
                  Lip-Sync
                </span>
              )}
              {isSpeaking && !lipSyncPlaying && (
                <span className="text-[9px] text-cyan-300 bg-cyan-500/20 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <Mic className="w-2.5 h-2.5" />
                  Speaking
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── CSS Animations ── */}
      <style>{`
        @keyframes float-heart {
          0% {
            transform: translateY(0) scale(1);
            opacity: 1;
          }
          50% {
            transform: translateY(-80px) scale(1.2) translateX(15px);
            opacity: 0.8;
          }
          100% {
            transform: translateY(-160px) scale(0.6) translateX(-10px);
            opacity: 0;
          }
        }
        @keyframes pulse-bar {
          0% { height: 4px; }
          100% { height: 14px; }
        }
      `}</style>
    </div>
  );
}
