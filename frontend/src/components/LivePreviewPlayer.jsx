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
} from "lucide-react";
import aiLiveCreatorService from "../base/services/aiLiveCreatorService";

/**
 * LivePreviewPlayer — TikTok Live-style 9:16 Preview Player
 *
 * Features:
 *   - 9:16 vertical video playback (TikTok Live format)
 *   - Auto-play video queue in sequence (uses blob URLs to bypass auth headers)
 *   - TikTok-style comment overlay
 *   - Product info overlay
 *   - Live status indicators (viewer count, likes, etc.)
 *   - Fullscreen toggle
 */
export default function LivePreviewPlayer({
  sessionId,
  engine,
  videoQueue = [],
  commentHistory = [],
  products = [],
  currentProduct,
  isLive = false,
  onVideoEnded,
  onRequestNextVideo,
}) {
  // ── Playback State ──
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [currentVideoIndex, setCurrentVideoIndex] = useState(-1);
  const [currentVideoUrl, setCurrentVideoUrl] = useState(null);
  const [isLoadingVideo, setIsLoadingVideo] = useState(false);
  const [videoError, setVideoError] = useState(null);
  const [playbackProgress, setPlaybackProgress] = useState(0);
  const [videoDuration, setVideoDuration] = useState(0);

  // ── Simulated Live Stats ──
  const [viewerCount, setViewerCount] = useState(0);
  const [likeCount, setLikeCount] = useState(0);
  const [showProductCard, setShowProductCard] = useState(true);

  // ── Comment Display ──
  const [visibleComments, setVisibleComments] = useState([]);
  const [floatingHearts, setFloatingHearts] = useState([]);

  // ── Track which job_ids we've already played / loaded ──
  const [loadedJobIds, setLoadedJobIds] = useState(new Set());

  // ── Refs ──
  const videoRef = useRef(null);
  const containerRef = useRef(null);
  const heartIdRef = useRef(0);
  const blobUrlRef = useRef(null); // Track current blob URL for cleanup

  // ── Completed videos from queue ──
  const completedVideos = videoQueue.filter((v) => v.status === "completed");

  // ══════════════════════════════════════════════
  // Play video using blob URL (bypasses auth header requirement)
  // ══════════════════════════════════════════════

  const playVideo = useCallback(
    async (queueItem, index) => {
      if (!queueItem?.job_id) return;
      setIsLoadingVideo(true);
      setVideoError(null);
      setCurrentVideoIndex(index);

      try {
        // Clean up previous blob URL
        if (blobUrlRef.current) {
          URL.revokeObjectURL(blobUrlRef.current);
          blobUrlRef.current = null;
        }

        // Download video as blob (this uses axios with auth headers)
        const enginePrefix = engine === "imtalker" ? "imtalker" : "musetalk";
        const blob = await aiLiveCreatorService.downloadVideo(
          queueItem.job_id,
          enginePrefix
        );

        // Create blob URL for video element
        const blobUrl = URL.createObjectURL(blob);
        blobUrlRef.current = blobUrl;

        setCurrentVideoUrl(blobUrl);
        setIsPlaying(true);
        setLoadedJobIds((prev) => new Set([...prev, queueItem.job_id]));
      } catch (err) {
        console.error("Failed to load video:", err);
        setVideoError(`Failed to load video: ${err.message || "Unknown error"}`);
        // Still mark as loaded to avoid infinite retry
        setLoadedJobIds((prev) => new Set([...prev, queueItem.job_id]));
      } finally {
        setIsLoadingVideo(false);
      }
    },
    [engine]
  );

  // Cleanup blob URL on unmount
  useEffect(() => {
    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
      }
    };
  }, []);

  // ── Auto-play first completed video when available ──
  useEffect(() => {
    if (completedVideos.length > 0 && currentVideoIndex === -1 && !currentVideoUrl && !isLoadingVideo) {
      playVideo(completedVideos[0], 0);
    }
  }, [completedVideos.length, currentVideoIndex, currentVideoUrl, isLoadingVideo, playVideo]);

  // ── Auto-play NEW completed videos as they arrive ──
  useEffect(() => {
    if (completedVideos.length === 0 || isLoadingVideo) return;

    // Find the first completed video that hasn't been loaded yet
    const newCompleted = completedVideos.find(
      (v) => v.job_id && !loadedJobIds.has(v.job_id)
    );

    if (newCompleted) {
      const newIndex = completedVideos.indexOf(newCompleted);
      // If nothing is currently playing, or current video has ended, play the new one
      if (!isPlaying && !isLoadingVideo) {
        playVideo(newCompleted, newIndex);
      }
      // If something is playing, we'll pick it up when current video ends
    }
  }, [completedVideos, loadedJobIds, isPlaying, isLoadingVideo, playVideo]);

  // ── Handle video ended → play next + request new content ──
  const handleVideoEnded = useCallback(() => {
    onVideoEnded?.();

    // Always request next video generation for infinite loop
    onRequestNextVideo?.();

    const nextIndex = currentVideoIndex + 1;
    if (nextIndex < completedVideos.length) {
      // Play next video in queue
      playVideo(completedVideos[nextIndex], nextIndex);
    } else if (completedVideos.length > 0) {
      // Loop back to first video while waiting for new content
      playVideo(completedVideos[0], 0);
      // Reset loaded job IDs so we can detect new videos
      setLoadedJobIds(new Set(completedVideos.map(v => v.job_id)));
    }
  }, [
    currentVideoIndex,
    completedVideos,
    playVideo,
    onVideoEnded,
    onRequestNextVideo,
  ]);

  // ── Skip to next video ──
  const handleSkipNext = () => {
    const nextIndex = currentVideoIndex + 1;
    if (nextIndex < completedVideos.length) {
      playVideo(completedVideos[nextIndex], nextIndex);
    } else if (completedVideos.length > 0) {
      playVideo(completedVideos[0], 0);
    }
  };

  // ── Toggle play/pause ──
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

  // ── Toggle mute ──
  const toggleMute = () => {
    if (!videoRef.current) return;
    videoRef.current.muted = !videoRef.current.muted;
    setIsMuted(videoRef.current.muted);
  };

  // ── Fullscreen toggle ──
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

  // ── Video time update ──
  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    const { currentTime, duration } = videoRef.current;
    if (duration > 0) {
      setPlaybackProgress((currentTime / duration) * 100);
      setVideoDuration(duration);
    }
  };

  // ── Simulated viewer/like counts ──
  useEffect(() => {
    if (!isLive && !isPlaying) return;
    const base = 128;
    setViewerCount(base + Math.floor(Math.random() * 50));
    const interval = setInterval(() => {
      setViewerCount((v) => Math.max(1, v + Math.floor(Math.random() * 5) - 2));
      setLikeCount((l) => l + Math.floor(Math.random() * 3));
    }, 3000);
    return () => clearInterval(interval);
  }, [isLive, isPlaying]);

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

  // ── Current queue item info ──
  const currentQueueItem =
    currentVideoIndex >= 0 ? completedVideos[currentVideoIndex] : null;
  const activeProduct =
    currentProduct ||
    (currentQueueItem?.product_name
      ? products.find((p) => p.name === currentQueueItem.product_name)
      : products[0]);

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
      {/* ── Video Layer ── */}
      {currentVideoUrl ? (
        <video
          ref={videoRef}
          src={currentVideoUrl}
          className="absolute inset-0 w-full h-full object-cover bg-black"
          autoPlay
          playsInline
          muted={isMuted}
          onEnded={handleVideoEnded}
          onTimeUpdate={handleTimeUpdate}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onError={(e) => {
            console.error("Video playback error:", e);
            setVideoError("Video playback error");
            // Try next video after error
            setTimeout(() => handleVideoEnded(), 1000);
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
            Waiting for video content...
          </p>
          {videoQueue.filter((v) => v.status === "processing").length > 0 && (
            <div className="flex items-center gap-2 bg-white/10 px-4 py-2 rounded-full">
              <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />
              <span className="text-blue-300 text-xs">
                Generating{" "}
                {videoQueue.filter((v) => v.status === "processing").length}{" "}
                video(s)...
              </span>
            </div>
          )}
          {completedVideos.length === 0 &&
            videoQueue.filter((v) => v.status === "processing").length === 0 && (
              <div className="text-center mt-4 px-8">
                <p className="text-gray-500 text-[11px] leading-relaxed">
                  Add products, generate scripts, and click "Video" to start
                  building your livestream content.
                </p>
              </div>
            )}
        </div>
      )}

      {/* ── Loading Overlay ── */}
      {isLoadingVideo && (
        <div className="absolute inset-0 bg-black/60 flex items-center justify-center z-10">
          <div className="text-center">
            <Loader2 className="w-8 h-8 text-white animate-spin mx-auto mb-2" />
            <p className="text-white text-xs">Loading video...</p>
          </div>
        </div>
      )}

      {/* ── Video Error Overlay ── */}
      {videoError && !isLoadingVideo && (
        <div className="absolute top-16 left-3 right-3 z-20">
          <div className="bg-red-900/60 backdrop-blur-sm text-red-200 text-[10px] px-3 py-1.5 rounded-lg">
            {videoError}
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
                isPlaying
                  ? "bg-red-500 text-white"
                  : "bg-gray-600/80 text-gray-300"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  isPlaying ? "bg-white animate-pulse" : "bg-gray-400"
                }`}
              />
              {isPlaying ? "LIVE" : "PREVIEW"}
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

        {/* Engine Badge */}
        <div className="mt-2 flex items-center gap-2">
          {engine === "imtalker" ? (
            <span className="text-[9px] bg-purple-500/30 text-purple-200 px-2 py-0.5 rounded-full flex items-center gap-1 backdrop-blur-sm">
              <Crown className="w-2.5 h-2.5" /> Premium
            </span>
          ) : (
            <span className="text-[9px] bg-blue-500/30 text-blue-200 px-2 py-0.5 rounded-full backdrop-blur-sm">
              Standard
            </span>
          )}
          {currentQueueItem && (
            <span className="text-[9px] bg-white/10 text-white/70 px-2 py-0.5 rounded-full backdrop-blur-sm">
              {currentQueueItem.type === "comment_reply"
                ? "Comment Reply"
                : "Product Intro"}
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

        {/* Skip Next */}
        {completedVideos.length > 1 && (
          <button
            onClick={handleSkipNext}
            className="flex flex-col items-center gap-0.5"
          >
            <div className="w-10 h-10 bg-black/30 backdrop-blur-sm rounded-full flex items-center justify-center hover:bg-white/20 transition-colors">
              <SkipForward className="w-5 h-5 text-white" />
            </div>
            <span className="text-[9px] text-white/80 font-medium">Next</span>
          </button>
        )}
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
                {item.commenter
                  ? item.commenter[0].toUpperCase()
                  : "?"}
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

      {/* ── Bottom: Playback Controls ── */}
      <div className="absolute bottom-0 left-0 right-0 z-20">
        {/* Progress Bar */}
        {currentVideoUrl && (
          <div className="h-0.5 bg-white/20 mx-3 mb-2 rounded-full overflow-hidden">
            <div
              className="h-full bg-white/80 rounded-full transition-all duration-300"
              style={{ width: `${playbackProgress}%` }}
            />
          </div>
        )}

        {/* Bottom Bar */}
        <div className="bg-gradient-to-t from-black/80 to-transparent px-3 pb-3 pt-6">
          <div className="flex items-center justify-between">
            {/* Play/Pause */}
            <div className="flex items-center gap-2">
              {currentVideoUrl && (
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
              {videoDuration > 0 && (
                <span className="text-[10px] text-white/60">
                  {Math.floor(
                    (playbackProgress / 100) * videoDuration
                  )}
                  s / {Math.floor(videoDuration)}s
                </span>
              )}
            </div>

            {/* Queue Info */}
            <div className="flex items-center gap-2">
              {completedVideos.length > 0 && (
                <span className="text-[9px] text-white/50 bg-white/10 px-2 py-0.5 rounded-full">
                  {currentVideoIndex + 1} / {completedVideos.length}
                </span>
              )}
              {videoQueue.filter((v) => v.status === "processing").length >
                0 && (
                <span className="text-[9px] text-blue-300 bg-blue-500/20 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <Loader2 className="w-2.5 h-2.5 animate-spin" />
                  {videoQueue.filter((v) => v.status === "processing").length}{" "}
                  generating
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── CSS for floating hearts ── */}
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
      `}</style>
    </div>
  );
}
