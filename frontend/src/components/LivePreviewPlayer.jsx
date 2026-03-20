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
 * NEW Architecture (Real-time TTS):
 *   - Portrait VIDEO loops continuously (muted) — just for visual appearance
 *   - TTS AUDIO plays on top via separate <audio> element
 *   - No GPU video generation needed per script
 *   - Supports AutoPilot mode: brain auto-generates scripts + TTS
 *
 * Features:
 *   - 9:16 vertical video loop playback
 *   - Real-time TTS audio overlay
 *   - AutoPilot: automatic script cycling with TTS
 *   - TikTok-style comment overlay
 *   - Product info overlay
 *   - Live status indicators
 *   - Fullscreen toggle
 */
export default function LivePreviewPlayer({
  sessionId,
  engine,
  portraitVideoUrl,
  videoQueue = [],
  commentHistory = [],
  products = [],
  currentProduct,
  isLive = false,
  autoPilotActive = false,
  onVideoEnded,
  onRequestNextVideo,
  onAutoPilotStateChange,
  onSpeakingChange,
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
  const videoRef = useRef(null);
  const audioRef = useRef(null);
  const containerRef = useRef(null);
  const heartIdRef = useRef(0);
  const autoPilotTimerRef = useRef(null);
  const isSpeakingRef = useRef(false);
  const autoPilotActiveRef = useRef(false);

  // Keep refs in sync
  useEffect(() => {
    isSpeakingRef.current = isSpeaking;
  }, [isSpeaking]);
  useEffect(() => {
    autoPilotActiveRef.current = autoPilotActive;
  }, [autoPilotActive]);

  // ══════════════════════════════════════════════
  // Video Loop — Portrait video plays continuously
  // ══════════════════════════════════════════════

  // Start video loop when portrait URL is available
  useEffect(() => {
    if (portraitVideoUrl && videoRef.current) {
      videoRef.current.src = portraitVideoUrl;
      videoRef.current.load();
    }
  }, [portraitVideoUrl]);

  const handleVideoCanPlay = () => {
    setVideoReady(true);
    if (videoRef.current && (isLive || autoPilotActive)) {
      videoRef.current.play().catch(() => {});
      setIsPlaying(true);
    }
  };

  const handleVideoLoop = () => {
    // Video ended — loop it
    if (videoRef.current) {
      videoRef.current.currentTime = 0;
      videoRef.current.play().catch(() => {});
    }
  };

  // ══════════════════════════════════════════════
  // TTS Audio Playback
  // ══════════════════════════════════════════════

  /**
   * Play TTS audio from a URL. Shows subtitle text while speaking.
   */
  const playTTSAudio = useCallback((audioUrl, text, scriptType) => {
    if (!audioRef.current) return;

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
  // AutoPilot — Brain auto-generates scripts + TTS
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
        language: "zh",
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
      });

      // Play the TTS audio
      if (result.audio_url) {
        playTTSAudio(result.audio_url, result.text, result.script_type);
      } else {
        setAudioLoading(false);
        // No audio — try again
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
    playTTSAudio,
    onAutoPilotStateChange,
  ]);

  // Start/stop autopilot
  useEffect(() => {
    if (autoPilotActive && sessionId && !isSpeaking && !audioLoading) {
      // Start video loop
      if (videoRef.current && portraitVideoUrl) {
        videoRef.current.play().catch(() => {});
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
          language: "zh",
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
    window.__livePlayerSpeak = speakText;
    return () => {
      delete window.__livePlayerSpeak;
    };
  }, [speakText]);

  // ══════════════════════════════════════════════
  // Controls
  // ══════════════════════════════════════════════

  const togglePlayPause = () => {
    if (!videoRef.current) return;
    if (videoRef.current.paused) {
      videoRef.current.play();
      setIsPlaying(true);
    } else {
      videoRef.current.pause();
      setIsPlaying(false);
    }
  };

  const toggleMute = () => {
    if (audioRef.current) {
      audioRef.current.muted = !audioRef.current.muted;
    }
    setIsMuted((prev) => !prev);
  };

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen?.();
      setIsFullscreen(true);
    } else {
      document.exitFullscreen?.();
      setIsFullscreen(false);
    }
  };

  // ── Simulated viewer/like counts ──
  useEffect(() => {
    if (!isLive && !isPlaying && !autoPilotActive) return;
    const base = 128;
    setViewerCount(base + Math.floor(Math.random() * 50));
    const interval = setInterval(() => {
      setViewerCount((v) => Math.max(1, v + Math.floor(Math.random() * 5) - 2));
      setLikeCount((l) => l + Math.floor(Math.random() * 3));
    }, 3000);
    return () => clearInterval(interval);
  }, [isLive, isPlaying, autoPilotActive]);

  // ── Comment overlay animation ──
  useEffect(() => {
    if (commentHistory.length === 0) return;
    const recent = commentHistory.slice(0, 8).map((c, i) => ({
      ...c,
      id: `comment-${Date.now()}-${i}`,
      fadeIn: i < 3,
    }));
    setVisibleComments(recent);
  }, [commentHistory]);

  // ── Floating hearts ──
  const addFloatingHeart = () => {
    const id = ++heartIdRef.current;
    const heart = {
      id,
      x: 85 + Math.random() * 10,
      delay: Math.random() * 0.5,
    };
    setFloatingHearts((prev) => [...prev, heart]);
    setTimeout(() => {
      setFloatingHearts((prev) => prev.filter((h) => h.id !== id));
    }, 2500);
    setLikeCount((l) => l + 1);
  };

  // ── Current product ──
  const activeProduct =
    currentProduct ||
    (autoPilotActive && products[autoPilotProductIndex]) ||
    products[0];

  // ══════════════════════════════════════════════
  // Render
  // ══════════════════════════════════════════════

  return (
    <div
      ref={containerRef}
      className={`relative bg-black rounded-2xl overflow-hidden shadow-2xl ${
        isFullscreen ? "fixed inset-0 z-50 rounded-none" : ""
      }`}
      style={{
        aspectRatio: isFullscreen ? undefined : "9/16",
        maxHeight: isFullscreen ? "100vh" : "720px",
        width: isFullscreen ? "100%" : undefined,
      }}
    >
      {/* ── Hidden Audio Element for TTS ── */}
      <audio
        ref={audioRef}
        onEnded={handleAudioEnded}
        muted={isMuted}
        style={{ display: "none" }}
      />

      {/* ── Video Layer (Looping Portrait) ── */}
      {portraitVideoUrl ? (
        <video
          ref={videoRef}
          className="absolute inset-0 w-full h-full object-cover bg-black"
          autoPlay
          playsInline
          muted
          loop
          onCanPlay={handleVideoCanPlay}
          onEnded={handleVideoLoop}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onError={(e) => {
            console.error("Video loop error:", e);
          }}
        />
      ) : (
        /* ── Idle / Waiting Screen ── */
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
            Upload a portrait video to start
          </p>
          <p className="text-gray-500 text-[11px] leading-relaxed text-center px-8">
            Upload a 9:16 digital human video, add products, then start the AI autopilot
            to begin your live stream.
          </p>
        </div>
      )}

      {/* ── Speaking Indicator ── */}
      {(isSpeaking || audioLoading) && (
        <div className="absolute top-14 left-1/2 -translate-x-1/2 z-30">
          <div className="flex items-center gap-2 bg-black/60 backdrop-blur-sm px-3 py-1.5 rounded-full">
            {audioLoading ? (
              <>
                <Loader2 className="w-3.5 h-3.5 text-cyan-400 animate-spin" />
                <span className="text-[10px] text-cyan-300">Generating speech...</span>
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
                autoPilotActive || isPlaying
                  ? "bg-red-500 text-white"
                  : "bg-gray-600/80 text-gray-300"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  autoPilotActive || isPlaying ? "bg-white animate-pulse" : "bg-gray-400"
                }`}
              />
              {autoPilotActive ? "LIVE" : isPlaying ? "PREVIEW" : "OFFLINE"}
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
              {portraitVideoUrl && (
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
              {isSpeaking && (
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
