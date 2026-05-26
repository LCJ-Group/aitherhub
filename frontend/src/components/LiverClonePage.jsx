import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Radio,
  Play,
  Square,
  Settings,
  Mic,
  MicOff,
  Volume2,
  Upload,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  Loader2,
  Users,
  MessageSquare,
  Zap,
  Monitor,
  Camera,
  Sliders,
  Send,
  Trash2,
  Download,
  Circle,
  StopCircle,
} from "lucide-react";
import liverCloneService from "../base/services/liverCloneService";
import { useTranslation } from "react-i18next";

/**
 * Liver Clone Page — Real-time Face Swap + Voice Conversion Live Streaming
 *
 * Layout:
 *   Left:   Stream Preview + Status
 *   Right:  Configuration Panel (Face, Voice, Stream, Auto-pilot)
 *
 * Modes:
 *   Manual:  Person speaks → face+voice converted → streamed
 *   Auto:    AI generates script → TTS → lip-sync → streamed
 *   Hybrid:  Person speaks when active, AI fills silence automatically
 */
export default function LiverClonePage() {
  useTranslation();
  const navigate = useNavigate();

  // ── Session State ──
  const [sessionId, setSessionId] = useState(null);
  const [sessionStatus, setSessionStatus] = useState(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState(null);

  // ── Configuration ──
  const [sourceFaceUrl, setSourceFaceUrl] = useState("");
  const [sourceFaceFile, setSourceFaceFile] = useState(null);
  const [sourceFacePreview, setSourceFacePreview] = useState(null);
  const [inputRtmp, setInputRtmp] = useState("");
  const [outputRtmp, setOutputRtmp] = useState("");
  const [voiceId, setVoiceId] = useState(() => localStorage.getItem("liverClone_voiceId") || "");
  const [mode, setMode] = useState(() => localStorage.getItem("liverClone_mode") || "hybrid");
  const [quality, setQuality] = useState(() => localStorage.getItem("liverClone_quality") || "high");
  const [language, setLanguage] = useState(() => localStorage.getItem("liverClone_language") || "en");
  const [resolution, setResolution] = useState(() => localStorage.getItem("liverClone_resolution") || "720p");
  const [fps, setFps] = useState(() => Number(localStorage.getItem("liverClone_fps")) || 30);

  // Voice settings
  const [voiceStability, setVoiceStability] = useState(() => Number(localStorage.getItem("liverClone_voiceStability")) || 0.5);
  const [voiceSimilarity, setVoiceSimilarity] = useState(() => Number(localStorage.getItem("liverClone_voiceSimilarity")) || 0.75);

  // VAD settings
  const [vadThreshold, setVadThreshold] = useState(() => Number(localStorage.getItem("liverClone_vadThreshold")) || 0.3);
  const [silenceTimeout, setSilenceTimeout] = useState(() => Number(localStorage.getItem("liverClone_silenceTimeout")) || 5.0);

  // Persona (Auto-pilot)
  const [personaName, setPersonaName] = useState("");
  const [personaStyle, setPersonaStyle] = useState("");
  const [openingScript, setOpeningScript] = useState("");

  // Comments
  const [commentText, setCommentText] = useState("");
  const [commentHistory, setCommentHistory] = useState([]);

  // Manual speak
  const [speakText, setSpeakText] = useState("");

  // Health
  const [health, setHealth] = useState(null);

  // Preview
  const [previewActive, setPreviewActive] = useState(false);
  const [previewFrame, setPreviewFrame] = useState(null);
  const [previewFps, setPreviewFps] = useState(0);
  const [previewLatency, setPreviewLatency] = useState(0);
  const [previewError, setPreviewError] = useState(null);
  const [isSourceUploaded, setIsSourceUploaded] = useState(false);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const previewWsRef = useRef(null);
  const previewIntervalRef = useRef(null);
  const frameCountRef = useRef(0);
  const lastFpsTimeRef = useRef(Date.now());

  // Audio passthrough refs
  const audioContextRef = useRef(null);
  const audioStreamRef = useRef(null);
  const audioGainRef = useRef(null);

  // TTS & Lip-sync refs
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [speakQueue, setSpeakQueue] = useState([]);
  const ttsAudioRef = useRef(null);
  const ttsAnalyserRef = useRef(null);
  const lipSyncIntervalRef = useRef(null);

  // Voice ID management
  const [savedVoiceIds, setSavedVoiceIds] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("liverClone_savedVoiceIds") || "[]");
    } catch { return []; }
  });
  const [voiceIdName, setVoiceIdName] = useState("");

  // Recording (9:16 vertical video)
  const [isRecording, setIsRecording] = useState(false);
  const [recordedChunks, setRecordedChunks] = useState([]);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const mediaRecorderRef = useRef(null);
  const recordingCanvasRef = useRef(null);
  const recordingTimerRef = useRef(null);
  const recordedFramesRef = useRef([]);

  // Tabs
  const [activeTab, setActiveTab] = useState("config"); // config, comments, autopilot, metrics

  const pollRef = useRef(null);
  const fileInputRef = useRef(null);

  // ── Load health on mount ──
  useEffect(() => {
    checkHealth();
    loadExistingSessions();
  }, []);

  // ── Persist voice/config settings to localStorage ──
  useEffect(() => {
    localStorage.setItem("liverClone_voiceId", voiceId);
  }, [voiceId]);
  useEffect(() => {
    localStorage.setItem("liverClone_mode", mode);
  }, [mode]);
  useEffect(() => {
    localStorage.setItem("liverClone_quality", quality);
  }, [quality]);
  useEffect(() => {
    localStorage.setItem("liverClone_language", language);
  }, [language]);
  useEffect(() => {
    localStorage.setItem("liverClone_resolution", resolution);
  }, [resolution]);
  useEffect(() => {
    localStorage.setItem("liverClone_fps", String(fps));
  }, [fps]);
  useEffect(() => {
    localStorage.setItem("liverClone_voiceStability", String(voiceStability));
  }, [voiceStability]);
  useEffect(() => {
    localStorage.setItem("liverClone_voiceSimilarity", String(voiceSimilarity));
  }, [voiceSimilarity]);
  useEffect(() => {
    localStorage.setItem("liverClone_vadThreshold", String(vadThreshold));
  }, [vadThreshold]);
  useEffect(() => {
    localStorage.setItem("liverClone_silenceTimeout", String(silenceTimeout));
  }, [silenceTimeout]);
  useEffect(() => {
    localStorage.setItem("liverClone_savedVoiceIds", JSON.stringify(savedVoiceIds));
  }, [savedVoiceIds]);

  // ── Poll session status ──
  useEffect(() => {
    if (!sessionId) return;
    const poll = async () => {
      try {
        const status = await liverCloneService.getSessionStatus(sessionId);
        setSessionStatus(status);
      } catch (err) {
        console.error("[LiverClone] Poll error:", err);
      }
    };
    poll();
    pollRef.current = setInterval(poll, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [sessionId]);

  // ── Functions ──
  const checkHealth = async () => {
    try {
      const h = await liverCloneService.healthCheck();
      setHealth(h);
    } catch (err) {
      setHealth({ status: "error" });
    }
  };

  const loadExistingSessions = async () => {
    try {
      const data = await liverCloneService.listSessions();
      if (data.sessions && data.sessions.length > 0) {
        const active = data.sessions.find(
          (s) => s.status === "STREAMING" || s.status === "CONFIGURING"
        );
        if (active) {
          setSessionId(active.session_id);
          setSessionStatus(active);
        }
      }
    } catch (err) {
      console.error("[LiverClone] Failed to load sessions:", err);
    }
  };

  const handleFaceUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setSourceFaceFile(file);
    const reader = new FileReader();
    reader.onload = (ev) => {
      setSourceFacePreview(ev.target.result);
      // Auto-upload source face to GPU Worker for preview
      const base64 = ev.target.result.split(",")[1];
      uploadSourceFace(base64);
    };
    reader.readAsDataURL(file);
  };

  const uploadSourceFace = async (base64) => {
    try {
      await liverCloneService.previewSetSource(base64);
      setIsSourceUploaded(true);
      setPreviewError(null);
    } catch (err) {
      console.error("[Preview] Failed to upload source face:", err);
      setPreviewError("ソース顔のアップロードに失敗しました");
      setIsSourceUploaded(false);
    }
  };

  // ── Preview Functions ──
  // Helper: get resolution dimensions from setting
  const getResolutionDims = () => {
    switch (resolution) {
      case "480p": return { w: 854, h: 480 };
      case "1080p": return { w: 1920, h: 1080 };
      case "720p":
      default: return { w: 1280, h: 720 };
    }
  };

  // Helper: get frame interval from FPS setting
  const getFrameInterval = () => {
    // RTX 5090 can handle higher FPS, but face swap takes ~80-150ms per frame
    // So max practical FPS is ~12-15 for face swap processing
    // We send at target rate, GPU Worker will skip if overloaded
    const targetFps = Math.min(fps, 20); // Cap at 20fps for face swap
    return Math.floor(1000 / targetFps);
  };

  const startPreview = async () => {
    try {
      setPreviewError(null);
      // Get webcam at 1080p - GPU Worker downscales internally for processing
      // but upscales result back to 1080p for high-quality output
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1920 }, height: { ideal: 1080 }, facingMode: "user" },
        audio: true,
      });
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }

      // Audio passthrough: play microphone audio through speakers
      // This lets the user hear their own voice during preview
      try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const source = audioCtx.createMediaStreamSource(stream);
        const gainNode = audioCtx.createGain();
        gainNode.gain.value = 1.0; // Full volume passthrough
        source.connect(gainNode);
        gainNode.connect(audioCtx.destination);
        audioContextRef.current = audioCtx;
        audioStreamRef.current = stream;
        audioGainRef.current = gainNode;
        console.log("[Preview] Audio passthrough enabled");
      } catch (audioErr) {
        console.warn("[Preview] Audio passthrough failed (non-critical):", audioErr);
      }

      // Get WebSocket URL from backend
      const { ws_url } = await liverCloneService.getPreviewWsUrl();
      
      // Connect WebSocket
      const ws = new WebSocket(ws_url);
      ws.binaryType = "arraybuffer";
      previewWsRef.current = ws;

      ws.onopen = () => {
        console.log("[Preview] WebSocket connected");
        setPreviewActive(true);
        // Start sending frames
        startSendingFrames();
      };

      ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          // Binary frame - display it
          const blob = new Blob([event.data], { type: "image/jpeg" });
          const url = URL.createObjectURL(blob);
          setPreviewFrame((prev) => {
            if (prev) URL.revokeObjectURL(prev);
            return url;
          });
          // FPS counting
          frameCountRef.current++;
          const now = Date.now();
          if (now - lastFpsTimeRef.current >= 1000) {
            setPreviewFps(frameCountRef.current);
            frameCountRef.current = 0;
            lastFpsTimeRef.current = now;
          }
        } else {
          // JSON message (error)
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === "error") {
              setPreviewError(msg.message);
            }
          } catch (e) {}
        }
      };

      ws.onerror = (err) => {
        console.error("[Preview] WebSocket error:", err);
        setPreviewError("WebSocket接続エラー");
      };

      ws.onclose = () => {
        console.log("[Preview] WebSocket closed");
        setPreviewActive(false);
      };
    } catch (err) {
      console.error("[Preview] Start failed:", err);
      setPreviewError(err.message || "プレビューの開始に失敗しました");
    }
  };

  const startSendingFrames = () => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas || !video) return;

    const ctx = canvas.getContext("2d");
    // Optimized: 640x360 for minimal latency while maintaining face detail
    // GPU Worker processes at this resolution directly - no resize needed
    const SEND_W = 640;
    const SEND_H = 360;
    canvas.width = SEND_W;
    canvas.height = SEND_H;

    // Pipeline-parallel flow control:
    // - No waitingForResponse (removed - was limiting to ~3 FPS)
    // - GPU Worker already keeps only the latest frame and discards old ones
    // - We use bufferedAmount only to prevent memory buildup
    // - Target: send at ~20 FPS, GPU processes latest frame only
    let animFrameId = null;
    let lastSendTime = 0;
    const MIN_SEND_INTERVAL = 50; // 50ms = max 20 FPS send rate

    const sendLoop = () => {
      animFrameId = requestAnimationFrame(sendLoop);

      if (!previewWsRef.current || previewWsRef.current.readyState !== WebSocket.OPEN) return;
      if (!video.videoWidth) return;

      // Rate limiting: don't send faster than 20 FPS
      const now = performance.now();
      if (now - lastSendTime < MIN_SEND_INTERVAL) return;

      // Backpressure: skip if WebSocket buffer is building up (64KB threshold)
      // GPU Worker discards old frames anyway, so this just prevents memory issues
      if (previewWsRef.current.bufferedAmount > 65536) return;

      // Draw video frame at optimized resolution
      ctx.drawImage(video, 0, 0, SEND_W, SEND_H);

      // Low JPEG quality for speed - face swap quality is determined by GPU Worker output
      canvas.toBlob(
        (blob) => {
          if (blob && previewWsRef.current?.readyState === WebSocket.OPEN) {
            previewWsRef.current.send(blob);
            lastSendTime = performance.now();
          }
        },
        "image/jpeg",
        0.6 // 60% quality - sufficient for face detection, minimal transfer size (~15-25KB)
      );
    };

    animFrameId = requestAnimationFrame(sendLoop);
    // Store cleanup reference
    previewIntervalRef.current = { cancel: () => cancelAnimationFrame(animFrameId) };
  };

  const stopPreview = () => {
    // Stop sending frames
    if (previewIntervalRef.current) {
      if (previewIntervalRef.current.cancel) {
        previewIntervalRef.current.cancel();
      } else {
        clearInterval(previewIntervalRef.current);
      }
      previewIntervalRef.current = null;
    }
    // Close WebSocket
    if (previewWsRef.current) {
      previewWsRef.current.close();
      previewWsRef.current = null;
    }
    // Stop audio passthrough
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }
    audioStreamRef.current = null;
    audioGainRef.current = null;
    // Stop webcam (this also stops audio tracks)
    if (videoRef.current?.srcObject) {
      videoRef.current.srcObject.getTracks().forEach((t) => t.stop());
      videoRef.current.srcObject = null;
    }
    // Cleanup
    setPreviewActive(false);
    if (previewFrame) {
      URL.revokeObjectURL(previewFrame);
      setPreviewFrame(null);
    }
    setPreviewFps(0);
    setPreviewLatency(0);
  };

  // ── Recording Functions (9:16 vertical video) ──
  const startRecording = () => {
    if (!previewActive) return;
    
    // Create a canvas for 9:16 recording (1080x1920)
    const recCanvas = document.createElement('canvas');
    recCanvas.width = 1080;
    recCanvas.height = 1920;
    recordingCanvasRef.current = recCanvas;
    recordedFramesRef.current = [];
    
    // Use canvas stream for MediaRecorder
    const canvasStream = recCanvas.captureStream(30);
    
    // Add audio track from microphone if available
    const combinedStream = new MediaStream();
    canvasStream.getVideoTracks().forEach(t => combinedStream.addTrack(t));
    if (audioStreamRef.current) {
      const audioTracks = audioStreamRef.current.getAudioTracks();
      audioTracks.forEach(t => combinedStream.addTrack(t));
    }
    
    const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp9,opus')
      ? 'video/webm;codecs=vp9,opus'
      : MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
        ? 'video/webm;codecs=vp9'
        : 'video/webm';
    
    const recorder = new MediaRecorder(combinedStream, {
      mimeType,
      videoBitsPerSecond: 8000000, // 8Mbps for high quality
    });
    
    const chunks = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = () => {
      setRecordedChunks(chunks);
    };
    
    recorder.start(100); // Collect data every 100ms
    mediaRecorderRef.current = recorder;
    setIsRecording(true);
    setRecordingDuration(0);
    
    // Timer for duration display
    recordingTimerRef.current = setInterval(() => {
      setRecordingDuration(prev => prev + 1);
    }, 1000);
    
    // Draw frames to recording canvas at 30fps
    // Use a persistent reference to the canvas context for performance
    const ctx = recCanvas.getContext('2d');
    let lastDrawnFrame = null;
    
    const drawFrame = () => {
      if (!mediaRecorderRef.current || mediaRecorderRef.current.state !== 'recording') return;
      
      // Find the preview image element (blob URL from WebSocket)
      const previewImg = document.querySelector('[data-preview-frame]');
      if (previewImg && previewImg.complete && previewImg.naturalWidth > 0) {
        try {
          // Draw in 9:16 aspect ratio (center crop from 16:9 source)
          const srcW = previewImg.naturalWidth;
          const srcH = previewImg.naturalHeight;
          // Calculate crop area for 9:16 from center
          const targetAspect = 9 / 16;
          const srcAspect = srcW / srcH;
          let cropX = 0, cropY = 0, cropW = srcW, cropH = srcH;
          if (srcAspect > targetAspect) {
            // Source is wider - crop sides
            cropW = srcH * targetAspect;
            cropX = (srcW - cropW) / 2;
          } else {
            // Source is taller - crop top/bottom
            cropH = srcW / targetAspect;
            cropY = (srcH - cropH) / 2;
          }
          ctx.drawImage(previewImg, cropX, cropY, cropW, cropH, 0, 0, 1080, 1920);
          lastDrawnFrame = true;
        } catch (drawErr) {
          // Canvas tainted or image not ready - draw black frame
          if (!lastDrawnFrame) {
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, 1080, 1920);
          }
          console.warn('[Recording] Draw error:', drawErr.message);
        }
      } else if (!lastDrawnFrame) {
        // No preview image available yet - black frame
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, 1080, 1920);
      }
      requestAnimationFrame(drawFrame);
    };
    requestAnimationFrame(drawFrame);
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    setIsRecording(false);
  };

  const downloadRecording = () => {
    if (recordedChunks.length === 0) return;
    // Use the same mimeType that was used during recording for proper playback
    const mimeType = mediaRecorderRef.current?.mimeType || 'video/webm';
    const blob = new Blob(recordedChunks, { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `face_swap_${new Date().toISOString().slice(0,19).replace(/[:-]/g,'')}_9x16.webm`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const formatDuration = (seconds) => {
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = (seconds % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopPreview();
      if (recordingTimerRef.current) clearInterval(recordingTimerRef.current);
    };
  }, []);

  const handleCreateSession = async () => {
    setIsCreating(true);
    setError(null);
    try {
      const config = {
        source_face_url: sourceFaceUrl,
        source_face_base64: sourceFacePreview
          ? sourceFacePreview.split(",")[1]
          : undefined,
        face_swap_quality: quality,
        input_rtmp: inputRtmp,
        output_rtmp: outputRtmp,
        voice_id: voiceId,
        voice_stability: voiceStability,
        voice_similarity: voiceSimilarity,
        mode,
        vad_threshold: vadThreshold,
        silence_timeout: silenceTimeout,
        persona_name: personaName,
        persona_style: personaStyle,
        language,
        opening_script: openingScript,
        resolution,
        fps,
      };
      const result = await liverCloneService.createSession(config);
      setSessionId(result.session_id);
      setSessionStatus(result);
    } catch (err) {
      const detail = err.response?.data?.detail || err.message;
      setError(`セッション作成に失敗: ${detail}`);
    } finally {
      setIsCreating(false);
    }
  };

  const handleStartSession = async () => {
    if (!sessionId) return;
    setIsStarting(true);
    setError(null);
    try {
      const result = await liverCloneService.startSession(sessionId);
      setSessionStatus(result);
    } catch (err) {
      const detail = err.response?.data?.detail || err.message;
      setError(`配信開始に失敗: ${detail}`);
    } finally {
      setIsStarting(false);
    }
  };

  const handleStopSession = async () => {
    if (!sessionId) return;
    try {
      await liverCloneService.stopSession(sessionId);
      setSessionStatus(null);
      setSessionId(null);
    } catch (err) {
      setError("停止に失敗しました");
    }
  };

  const handleDeleteSession = async () => {
    if (!sessionId) return;
    try {
      await liverCloneService.deleteSession(sessionId);
      setSessionId(null);
      setSessionStatus(null);
    } catch (err) {
      setError("削除に失敗しました");
    }
  };

  const handleSendComment = async () => {
    if (!commentText.trim()) return;
    const text = commentText.trim();
    setCommentText("");

    // If streaming with session, use session-based comment response
    if (sessionId && isStreaming) {
      try {
        const result = await liverCloneService.respondToComment(sessionId, text);
        setCommentHistory((prev) => [
          ...prev,
          {
            comment: text,
            response: result.response,
            time: new Date().toLocaleTimeString(),
          },
        ]);
      } catch (err) {
        setError("コメント返答に失敗しました");
      }
      return;
    }

    // Preview mode: directly speak the comment text via TTS
    setCommentHistory((prev) => [
      ...prev,
      {
        comment: text,
        response: "🗣️ 読み上げ中...",
        time: new Date().toLocaleTimeString(),
      },
    ]);
    await speakWithTTS(text);
    // Update the last comment entry to show completion
    setCommentHistory((prev) => {
      const updated = [...prev];
      if (updated.length > 0) {
        updated[updated.length - 1].response = "✅ 読み上げ完了";
      }
      return updated;
    });
  };

  const handleSpeak = async () => {
    if (!speakText.trim()) return;
    const text = speakText.trim();
    setSpeakText("");

    // If streaming with session, use session-based speak
    if (sessionId && isStreaming) {
      try {
        await liverCloneService.pushSpeakText(sessionId, text);
      } catch (err) {
        setError("テキスト送信に失敗しました");
      }
      return;
    }

    // Preview mode: use preview/speak API + browser playback + lip-sync
    await speakWithTTS(text);
  };

  /**
   * Generate TTS audio via backend and play it in browser.
   * During playback, detect volume levels and send mouth_open to GPU Worker.
   */
  const speakWithTTS = async (text) => {
    if (!voiceId) {
      setError("Voice IDが設定されていません");
      return;
    }
    if (isSpeaking) {
      // Queue the text if already speaking
      setSpeakQueue((prev) => [...prev, text]);
      return;
    }

    setIsSpeaking(true);
    try {
      // Call backend TTS API
      const result = await liverCloneService.previewSpeak(text, voiceId, {
        voice_stability: voiceStability,
        voice_similarity: voiceSimilarity,
        language: language,
      });

      if (result.status === "ok" && result.audio_base64) {
        await playTTSAudio(result.audio_base64, result.audio_format || "mp3");
      }
    } catch (err) {
      console.error("[TTS] Speak failed:", err);
      setError("音声生成に失敗しました: " + (err.response?.data?.detail || err.message));
    } finally {
      setIsSpeaking(false);
      // Process queue
      setSpeakQueue((prev) => {
        if (prev.length > 0) {
          const [next, ...rest] = prev;
          setTimeout(() => speakWithTTS(next), 100);
          return rest;
        }
        return prev;
      });
    }
  };

  /**
   * Play TTS audio and send lip-sync data to GPU Worker via WebSocket.
   * Uses a SEPARATE AudioContext from the audio passthrough to avoid conflicts.
   * MediaElementSource can only be connected once per audio element, so we create
   * a fresh Audio element and a dedicated TTS AudioContext each time.
   */
  const playTTSAudio = (audioBase64, format) => {
    return new Promise(async (resolve) => {
      try {
        // Create a DEDICATED AudioContext for TTS playback (separate from passthrough)
        const ttsCtx = new (window.AudioContext || window.webkitAudioContext)();
        console.log("[TTS] Created dedicated AudioContext, state:", ttsCtx.state);

        // MUST await resume before proceeding
        if (ttsCtx.state === "suspended") {
          await ttsCtx.resume();
          console.log("[TTS] AudioContext resumed");
        }

        // Decode base64 to ArrayBuffer
        const audioData = atob(audioBase64);
        const audioArray = new Uint8Array(audioData.length);
        for (let i = 0; i < audioData.length; i++) {
          audioArray[i] = audioData.charCodeAt(i);
        }

        // Decode audio data into AudioBuffer (bypasses CORS/MediaElement issues)
        let audioBuffer;
        try {
          audioBuffer = await ttsCtx.decodeAudioData(audioArray.buffer.slice(0));
          console.log("[TTS] Audio decoded:", audioBuffer.duration.toFixed(2), "s");
        } catch (decodeErr) {
          console.error("[TTS] decodeAudioData failed:", decodeErr);
          // Fallback: use Audio element without lip-sync
          const blob = new Blob([audioArray], { type: `audio/${format}` });
          const audioUrl = URL.createObjectURL(blob);
          const audio = new Audio(audioUrl);
          audio.onended = () => { URL.revokeObjectURL(audioUrl); resolve(); };
          audio.onerror = () => { URL.revokeObjectURL(audioUrl); resolve(); };
          await audio.play().catch(() => {});
          return;
        }

        // Pre-compute RMS envelope for lip-sync (analyze the entire buffer upfront)
        const channelData = audioBuffer.getChannelData(0);
        const sampleRate = audioBuffer.sampleRate;
        const chunkSize = Math.floor(sampleRate / 20); // 20Hz analysis = 50ms chunks
        const numChunks = Math.ceil(channelData.length / chunkSize);
        const envelope = new Float32Array(numChunks);
        for (let c = 0; c < numChunks; c++) {
          let sum = 0;
          const start = c * chunkSize;
          const end = Math.min(start + chunkSize, channelData.length);
          for (let i = start; i < end; i++) {
            sum += channelData[i] * channelData[i];
          }
          const rms = Math.sqrt(sum / (end - start));
          // Normalize: typical speech RMS is 0.01-0.15, map to 0-1
          envelope[c] = Math.min(1.0, rms * 8.0);
        }
        console.log("[TTS] Envelope computed:", numChunks, "chunks, max:", Math.max(...envelope).toFixed(3));

        // Play audio using AudioBufferSourceNode (guaranteed to work with AudioContext)
        const sourceNode = ttsCtx.createBufferSource();
        sourceNode.buffer = audioBuffer;
        sourceNode.connect(ttsCtx.destination);

        // Start lip-sync: send pre-computed envelope values at 20Hz
        const startTime = ttsCtx.currentTime;
        let lipSyncActive = true;
        const lipSyncLoop = () => {
          if (!lipSyncActive) return;
          const elapsed = ttsCtx.currentTime - startTime;
          const chunkIndex = Math.floor(elapsed * 20); // 20Hz
          if (chunkIndex >= numChunks) {
            // Audio should be done
            return;
          }
          const mouthOpen = envelope[chunkIndex];

          // Send mouth_open to GPU Worker via WebSocket
          if (previewWsRef.current?.readyState === WebSocket.OPEN) {
            previewWsRef.current.send(JSON.stringify({ type: "mouth_open", value: mouthOpen }));
          }
        };
        lipSyncIntervalRef.current = setInterval(lipSyncLoop, 50); // 20Hz

        const cleanup = () => {
          lipSyncActive = false;
          if (lipSyncIntervalRef.current) {
            clearInterval(lipSyncIntervalRef.current);
            lipSyncIntervalRef.current = null;
          }
          // Send mouth closed
          if (previewWsRef.current?.readyState === WebSocket.OPEN) {
            previewWsRef.current.send(JSON.stringify({ type: "mouth_open", value: 0 }));
          }
          ttsAudioRef.current = null;
          ttsAnalyserRef.current = null;
          ttsCtx.close().catch(() => {});
          console.log("[TTS] Playback complete, lip-sync stopped");
          resolve();
        };

        sourceNode.onended = () => {
          console.log("[TTS] Audio playback ended");
          cleanup();
        };

        sourceNode.start(0);
        ttsAudioRef.current = sourceNode;
        console.log("[TTS] Audio playback started via AudioBufferSourceNode");
      } catch (err) {
        console.error("[TTS] playTTSAudio error:", err);
        resolve();
      }
    });
  };

  // ── Status helpers ──
  const isStreaming =
    sessionStatus?.status === "STREAMING" ||
    sessionStatus?.state === "STREAMING";
  const isConfiguring =
    sessionStatus?.status === "CONFIGURING" ||
    sessionStatus?.state === "CONFIGURING";

  const getStatusBadge = () => {
    if (isStreaming) {
      return (
        <span className="flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-red-900/50 text-red-400">
          <div className="w-2 h-2 rounded-full bg-red-400 animate-pulse" />
          LIVE
        </span>
      );
    }
    if (isConfiguring) {
      return (
        <span className="flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-yellow-900/50 text-yellow-400">
          <Settings className="w-3 h-3" />
          設定中
        </span>
      );
    }
    return (
      <span className="flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-gray-800 text-gray-400">
        待機中
      </span>
    );
  };

  // ── Render ──
  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      {/* Header */}
      <header className="border-b border-gray-800 bg-[#0d0d14]">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate("/")}
              className="p-2 hover:bg-gray-800 rounded-lg transition"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <h1 className="text-xl font-bold flex items-center gap-2">
                <Users className="w-6 h-6 text-cyan-400" />
                Liver Clone
              </h1>
              <p className="text-sm text-gray-400">
                リアルタイム顔変換 + 声変換 ライブ配信
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {getStatusBadge()}
            {health && (
              <div
                className={`flex items-center gap-1 text-xs px-2 py-1 rounded-full ${
                  health.status === "healthy" || health.status === "ok" || health.face_swap_worker === "ok"
                    ? "bg-green-900/50 text-green-400"
                    : "bg-red-900/50 text-red-400"
                }`}
              >
                <div
                  className={`w-2 h-2 rounded-full ${
                    health.status === "healthy" || health.status === "ok" || health.face_swap_worker === "ok"
                      ? "bg-green-400"
                      : "bg-red-400"
                  }`}
                />
                GPU {health.status === "healthy" || health.status === "ok" || health.face_swap_worker === "ok" ? "Ready" : "Offline"}
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 py-6">
        {error && (
          <div className="mb-4 p-3 bg-red-900/30 border border-red-800 rounded-lg flex items-center gap-2 text-red-300">
            <AlertCircle className="w-4 h-4" />
            {error}
            <button
              onClick={() => setError(null)}
              className="ml-auto text-red-400 hover:text-red-300"
            >
              ×
            </button>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          {/* ── Left: Stream Preview (larger) ── */}
          <div className="lg:col-span-3 space-y-4">
            {/* Stream Preview */}
            <div className="bg-[#12121a] rounded-xl border border-gray-800 overflow-hidden">
              <div className="aspect-video bg-black flex items-center justify-center relative">
                {/* Hidden video element for webcam */}
                <video ref={videoRef} className="hidden" muted playsInline />
                <canvas ref={canvasRef} className="hidden" />
                
                {previewActive && previewFrame ? (
                  <img
                    src={previewFrame}
                    alt="Preview"
                    data-preview-frame="true"
                    className="w-full h-full object-contain"
                  />
                ) : isStreaming ? (
                  <div className="text-center">
                    <Radio className="w-12 h-12 text-red-400 animate-pulse mx-auto mb-2" />
                    <p className="text-sm text-gray-400">配信中...</p>
                    <p className="text-xs text-gray-500 mt-1">
                      プラットフォームで確認してください
                    </p>
                  </div>
                ) : previewActive ? (
                  <div className="text-center">
                    <Loader2 className="w-8 h-8 text-cyan-400 animate-spin mx-auto mb-2" />
                    <p className="text-sm text-gray-400">プレビュー接続中...</p>
                  </div>
                ) : sourceFacePreview ? (
                  <img
                    src={sourceFacePreview}
                    alt="Source face"
                    className="w-full h-full object-cover opacity-50"
                  />
                ) : (
                  <div className="text-center">
                    <Camera className="w-12 h-12 text-gray-600 mx-auto mb-2" />
                    <p className="text-sm text-gray-500">
                      顔写真をアップロード
                    </p>
                  </div>
                )}
                {isStreaming && (
                  <div className="absolute top-3 left-3 flex items-center gap-1 bg-red-600 px-2 py-0.5 rounded text-xs font-bold">
                    <div className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                    LIVE
                  </div>
                )}
                {previewActive && (
                  <div className="absolute top-3 left-3 flex items-center gap-1 bg-cyan-600 px-2 py-0.5 rounded text-xs font-bold">
                    <div className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                    PREVIEW
                  </div>
                )}
                {/* Preview stats overlay */}
                {previewActive && (
                  <div className="absolute bottom-3 left-3 right-3 flex justify-between text-xs">
                    <span className={`bg-black/70 px-2 py-1 rounded ${previewFps >= 15 ? 'text-green-400' : previewFps >= 8 ? 'text-yellow-400' : 'text-red-400'}`}>
                      {previewFps} FPS
                    </span>
                    <span className="bg-black/70 px-2 py-1 rounded text-cyan-400">
                      {"HD 1080p"}
                    </span>
                    <span className="bg-black/70 px-2 py-1 rounded text-yellow-400">
                      GPU: RTX 5090
                    </span>
                  </div>
                )}
              </div>
              {/* Preview Controls */}
              <div className="p-3 border-t border-gray-800 flex gap-2">
                {!previewActive ? (
                  <button
                    onClick={startPreview}
                    disabled={!isSourceUploaded || isStreaming}
                    className="flex-1 flex items-center justify-center gap-2 py-2 bg-cyan-600 hover:bg-cyan-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition"
                  >
                    <Camera className="w-4 h-4" />
                    プレビュー開始
                  </button>
                ) : (
                  <>
                    <button
                      onClick={stopPreview}
                      className="flex-1 flex items-center justify-center gap-2 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition"
                    >
                      <Square className="w-4 h-4 text-red-400" />
                      停止
                    </button>
                    {!isRecording ? (
                      <button
                        onClick={startRecording}
                        className="flex items-center justify-center gap-2 py-2 px-4 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium transition"
                        title="9:16縦動画で録画開始"
                      >
                        <Circle className="w-4 h-4 fill-current" />
                        録画
                      </button>
                    ) : (
                      <button
                        onClick={stopRecording}
                        className="flex items-center justify-center gap-2 py-2 px-4 bg-red-700 hover:bg-red-800 rounded-lg text-sm font-medium transition animate-pulse"
                      >
                        <StopCircle className="w-4 h-4" />
                        {formatDuration(recordingDuration)}
                      </button>
                    )}
                  </>
                )}
                {recordedChunks.length > 0 && !isRecording && (
                  <button
                    onClick={downloadRecording}
                    className="flex items-center justify-center gap-2 py-2 px-4 bg-green-600 hover:bg-green-700 rounded-lg text-sm font-medium transition"
                    title="9:16縦動画をダウンロード"
                  >
                    <Download className="w-4 h-4" />
                    DL
                  </button>
                )}
              </div>
              {/* Preview error */}
              {previewError && (
                <div className="p-2 bg-red-900/30 border-t border-red-800 text-xs text-red-300 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" />
                  {previewError}
                </div>
              )}
            </div>

            {/* Metrics */}
            {isStreaming && sessionStatus?.metrics && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-4">
                <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
                  <Monitor className="w-4 h-4 text-cyan-400" />
                  配信メトリクス
                </h3>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div className="bg-gray-900 rounded-lg p-2">
                    <p className="text-gray-500">FPS</p>
                    <p className="text-lg font-bold text-green-400">
                      {sessionStatus.metrics.fps || "--"}
                    </p>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-2">
                    <p className="text-gray-500">遅延</p>
                    <p className="text-lg font-bold text-yellow-400">
                      {sessionStatus.metrics.latency_ms || "--"}ms
                    </p>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-2">
                    <p className="text-gray-500">モード</p>
                    <p className="text-sm font-bold text-cyan-400">
                      {sessionStatus.metrics.current_mode || mode}
                    </p>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-2">
                    <p className="text-gray-500">発話数</p>
                    <p className="text-lg font-bold text-purple-400">
                      {sessionStatus.metrics.speak_count || 0}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Quick Speak - available in both streaming and preview modes */}
            {(isStreaming || previewActive) && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-4">
                <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Volume2 className="w-4 h-4 text-purple-400" />
                  {isSpeaking ? "🗣️ 発話中..." : "手動発話"}
                </h3>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={speakText}
                    onChange={(e) => setSpeakText(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSpeak()}
                    placeholder="テキストを入力して読み上げ..."
                    className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                  />
                  <button
                    onClick={handleSpeak}
                    disabled={!speakText.trim() || isSpeaking}
                    className="px-3 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded-lg transition"
                  >
                    {isSpeaking ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* ── Right: Configuration Panel ── */}
          <div className="lg:col-span-2 space-y-4 max-h-[calc(100vh-140px)] overflow-y-auto">
            {/* Tab Navigation */}
            <div className="flex gap-1 bg-[#12121a] rounded-xl border border-gray-800 p-1">
              {[
                { id: "config", label: "設定", icon: Settings },
                { id: "comments", label: "コメント", icon: MessageSquare },
                { id: "autopilot", label: "Auto Pilot", icon: Zap },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-sm transition ${
                    activeTab === tab.id
                      ? "bg-gray-800 text-white"
                      : "text-gray-400 hover:text-gray-300"
                  }`}
                >
                  <tab.icon className="w-4 h-4" />
                  {tab.label}
                </button>
              ))}
            </div>

            {/* ── Config Tab ── */}
            {activeTab === "config" && (
              <div className="space-y-4">
                {/* Face Settings */}
                <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                  <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                    <Camera className="w-4 h-4 text-cyan-400" />
                    顔設定
                  </h3>
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        ソース顔画像
                      </label>
                      <div className="flex gap-3">
                        <div
                          onClick={() => fileInputRef.current?.click()}
                          className="w-20 h-20 border-2 border-dashed border-gray-700 rounded-lg flex items-center justify-center cursor-pointer hover:border-cyan-500 transition overflow-hidden"
                        >
                          {sourceFacePreview ? (
                            <img
                              src={sourceFacePreview}
                              alt="Face"
                              className="w-full h-full object-cover"
                            />
                          ) : (
                            <Upload className="w-6 h-6 text-gray-500" />
                          )}
                        </div>
                        <input
                          ref={fileInputRef}
                          type="file"
                          accept="image/*"
                          onChange={handleFaceUpload}
                          className="hidden"
                        />
                        <div className="flex-1">
                          <input
                            type="text"
                            value={sourceFaceUrl}
                            onChange={(e) => setSourceFaceUrl(e.target.value)}
                            placeholder="または画像URLを入力..."
                            className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-cyan-500"
                          />
                          <div className="mt-2 flex gap-2">
                            <select
                              value={quality}
                              onChange={(e) => setQuality(e.target.value)}
                              className="bg-gray-900 border border-gray-700 rounded-lg px-2 py-1 text-xs"
                            >
                              <option value="fast">Fast</option>
                              <option value="balanced">Balanced</option>
                              <option value="high">High</option>
                              <option value="ultra">Ultra</option>
                            </select>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Stream Settings */}
                <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                  <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                    <Radio className="w-4 h-4 text-red-400" />
                    配信設定
                  </h3>
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        入力RTMP URL（OBSから）
                      </label>
                      <input
                        type="text"
                        value={inputRtmp}
                        onChange={(e) => setInputRtmp(e.target.value)}
                        placeholder="rtmp://your-server/live/input-key"
                        className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-red-500"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        出力RTMP URL（配信先）
                      </label>
                      <input
                        type="text"
                        value={outputRtmp}
                        onChange={(e) => setOutputRtmp(e.target.value)}
                        placeholder="rtmp://live.shopee.sg/live/stream-key"
                        className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-red-500"
                      />
                    </div>
                    <div className="flex gap-3">
                      <div className="flex-1">
                        <label className="text-xs text-gray-400 mb-1 block">
                          解像度
                        </label>
                        <select
                          value={resolution}
                          onChange={(e) => setResolution(e.target.value)}
                          className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                        >
                          <option value="480p">480p</option>
                          <option value="720p">720p</option>
                          <option value="1080p">1080p</option>
                        </select>
                      </div>
                      <div className="flex-1">
                        <label className="text-xs text-gray-400 mb-1 block">
                          FPS
                        </label>
                        <select
                          value={fps}
                          onChange={(e) => setFps(Number(e.target.value))}
                          className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                        >
                          <option value={24}>24 fps</option>
                          <option value={30}>30 fps</option>
                          <option value={60}>60 fps</option>
                        </select>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Voice Settings */}
                <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                  <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                    <Mic className="w-4 h-4 text-purple-400" />
                    音声設定
                  </h3>
                  <div className="space-y-3">
                    {/* Voice ID with save/load management */}
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        Voice ID（ElevenLabs）
                      </label>
                      {/* Saved Voice IDs dropdown */}
                      {savedVoiceIds.length > 0 && (
                        <select
                          value={voiceId}
                          onChange={(e) => setVoiceId(e.target.value)}
                          className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm mb-2 focus:outline-none focus:border-purple-500"
                        >
                          <option value="">-- 保存済みVoice IDを選択 --</option>
                          {savedVoiceIds.map((item, idx) => (
                            <option key={idx} value={item.id}>
                              {item.name} ({item.id.slice(0, 8)}...)
                            </option>
                          ))}
                        </select>
                      )}
                      {/* Voice ID input + save */}
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={voiceId}
                          onChange={(e) => setVoiceId(e.target.value)}
                          placeholder="ElevenLabs Voice ID"
                          className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
                        />
                      </div>
                      {/* Save new Voice ID */}
                      {voiceId && (
                        <div className="flex gap-2 mt-2">
                          <input
                            type="text"
                            value={voiceIdName}
                            onChange={(e) => setVoiceIdName(e.target.value)}
                            placeholder="名前を付けて保存..."
                            className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-purple-500"
                          />
                          <button
                            onClick={() => {
                              if (!voiceIdName.trim() || !voiceId.trim()) return;
                              const exists = savedVoiceIds.some(v => v.id === voiceId);
                              if (exists) {
                                setSavedVoiceIds(prev => prev.map(v => v.id === voiceId ? { ...v, name: voiceIdName.trim() } : v));
                              } else {
                                setSavedVoiceIds(prev => [...prev, { id: voiceId.trim(), name: voiceIdName.trim() }]);
                              }
                              setVoiceIdName("");
                            }}
                            disabled={!voiceIdName.trim()}
                            className="px-3 py-1 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded-lg text-xs transition"
                          >
                            保存
                          </button>
                        </div>
                      )}
                      {/* Saved Voice IDs list with delete */}
                      {savedVoiceIds.length > 0 && (
                        <div className="mt-2 space-y-1">
                          {savedVoiceIds.map((item, idx) => (
                            <div key={idx} className="flex items-center justify-between bg-gray-900/50 rounded px-2 py-1 text-xs">
                              <span
                                className={`cursor-pointer hover:text-purple-300 ${voiceId === item.id ? 'text-purple-400 font-semibold' : 'text-gray-300'}`}
                                onClick={() => setVoiceId(item.id)}
                              >
                                {item.name}
                              </span>
                              <button
                                onClick={() => setSavedVoiceIds(prev => prev.filter((_, i) => i !== idx))}
                                className="text-red-400 hover:text-red-300 ml-2"
                                title="削除"
                              >
                                <Trash2 className="w-3 h-3" />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs text-gray-400 mb-1 block">
                          安定性: {voiceStability}
                        </label>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={voiceStability}
                          onChange={(e) =>
                            setVoiceStability(Number(e.target.value))
                          }
                          className="w-full"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-400 mb-1 block">
                          類似度: {voiceSimilarity}
                        </label>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={voiceSimilarity}
                          onChange={(e) =>
                            setVoiceSimilarity(Number(e.target.value))
                          }
                          className="w-full"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        モード
                      </label>
                      <div className="flex gap-2">
                        {[
                          { id: "manual", label: "手動", desc: "人が喋る→変換" },
                          { id: "auto", label: "自動", desc: "AI自動配信" },
                          {
                            id: "hybrid",
                            label: "ハイブリッド",
                            desc: "喋る時は変換、黙ったらAI",
                          },
                        ].map((m) => (
                          <button
                            key={m.id}
                            onClick={() => setMode(m.id)}
                            className={`flex-1 p-2 rounded-lg border text-xs text-center transition ${
                              mode === m.id
                                ? "border-cyan-500 bg-cyan-900/20 text-cyan-300"
                                : "border-gray-700 text-gray-400 hover:border-gray-600"
                            }`}
                          >
                            <p className="font-semibold">{m.label}</p>
                            <p className="text-[10px] mt-0.5 opacity-70">
                              {m.desc}
                            </p>
                          </button>
                        ))}
                      </div>
                    </div>
                    {(mode === "hybrid" || mode === "auto") && (
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="text-xs text-gray-400 mb-1 block">
                            VAD閾値: {vadThreshold}
                          </label>
                          <input
                            type="range"
                            min="0.1"
                            max="0.9"
                            step="0.05"
                            value={vadThreshold}
                            onChange={(e) =>
                              setVadThreshold(Number(e.target.value))
                            }
                            className="w-full"
                          />
                        </div>
                        <div>
                          <label className="text-xs text-gray-400 mb-1 block">
                            無音タイムアウト: {silenceTimeout}s
                          </label>
                          <input
                            type="range"
                            min="2"
                            max="15"
                            step="0.5"
                            value={silenceTimeout}
                            onChange={(e) =>
                              setSilenceTimeout(Number(e.target.value))
                            }
                            className="w-full"
                          />
                        </div>
                      </div>
                    )}
                    <div>
                      <label className="text-xs text-gray-400 mb-1 block">
                        言語
                      </label>
                      <select
                        value={language}
                        onChange={(e) => setLanguage(e.target.value)}
                        className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                      >
                        <option value="en">English</option>
                        <option value="ja">日本語</option>
                        <option value="zh">中文</option>
                        <option value="th">ภาษาไทย</option>
                        <option value="ms">Bahasa Melayu</option>
                      </select>
                    </div>
                  </div>
                </div>

                {/* Action Buttons */}
                <div className="flex gap-3">
                  {!sessionId ? (
                    <button
                      onClick={handleCreateSession}
                      disabled={isCreating}
                      className="flex-1 flex items-center justify-center gap-2 py-3 bg-cyan-600 hover:bg-cyan-700 disabled:bg-gray-700 rounded-xl font-semibold transition"
                    >
                      {isCreating ? (
                        <Loader2 className="w-5 h-5 animate-spin" />
                      ) : (
                        <Settings className="w-5 h-5" />
                      )}
                      セッション作成
                    </button>
                  ) : !isStreaming ? (
                    <>
                      <button
                        onClick={handleStartSession}
                        disabled={isStarting}
                        className="flex-1 flex items-center justify-center gap-2 py-3 bg-red-600 hover:bg-red-700 disabled:bg-gray-700 rounded-xl font-semibold transition"
                      >
                        {isStarting ? (
                          <Loader2 className="w-5 h-5 animate-spin" />
                        ) : (
                          <Play className="w-5 h-5" />
                        )}
                        配信開始
                      </button>
                      <button
                        onClick={handleDeleteSession}
                        className="px-4 py-3 bg-gray-800 hover:bg-gray-700 rounded-xl transition"
                      >
                        <Trash2 className="w-5 h-5 text-red-400" />
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={handleStopSession}
                      className="flex-1 flex items-center justify-center gap-2 py-3 bg-gray-800 hover:bg-gray-700 border border-red-600 rounded-xl font-semibold transition"
                    >
                      <Square className="w-5 h-5 text-red-400" />
                      配信停止
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* ── Comments Tab ── */}
            {activeTab === "comments" && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                  <MessageSquare className="w-4 h-4 text-green-400" />
                  コメント返答
                </h3>
                <div className="space-y-3">
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={commentText}
                      onChange={(e) => setCommentText(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && handleSendComment()}
                      placeholder="コメントを入力して返答を生成..."
                      className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-green-500"
                    />
                    <button
                      onClick={handleSendComment}
                      disabled={!commentText.trim() || (!isStreaming && !previewActive)}
                      className="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-gray-700 rounded-lg transition"
                    >
                      <Send className="w-4 h-4" />
                    </button>
                  </div>
                  {/* Comment History */}
                  <div className="space-y-2 max-h-96 overflow-y-auto">
                    {commentHistory.length === 0 ? (
                      <p className="text-sm text-gray-500 text-center py-8">
                        コメントがまだありません
                      </p>
                    ) : (
                      commentHistory.map((item, i) => (
                        <div
                          key={i}
                          className="bg-gray-900 rounded-lg p-3 text-sm"
                        >
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-gray-400 text-xs">
                              {item.time}
                            </span>
                            <span className="text-cyan-400">
                              {item.comment}
                            </span>
                          </div>
                          <p className="text-gray-300 pl-2 border-l-2 border-purple-500">
                            {item.response}
                          </p>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* ── Auto Pilot Tab ── */}
            {activeTab === "autopilot" && (
              <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                  <Zap className="w-4 h-4 text-yellow-400" />
                  Auto Pilot設定
                </h3>
                <div className="space-y-3">
                  <div>
                    <label className="text-xs text-gray-400 mb-1 block">
                      ペルソナ名
                    </label>
                    <input
                      type="text"
                      value={personaName}
                      onChange={(e) => setPersonaName(e.target.value)}
                      placeholder="例: KYOGOKU Ryu"
                      className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-yellow-500"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-gray-400 mb-1 block">
                      話し方・スタイル
                    </label>
                    <textarea
                      value={personaStyle}
                      onChange={(e) => setPersonaStyle(e.target.value)}
                      placeholder="例: Professional yet friendly, high energy, confident..."
                      rows={3}
                      className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-yellow-500 resize-none"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-gray-400 mb-1 block">
                      オープニングスクリプト
                    </label>
                    <textarea
                      value={openingScript}
                      onChange={(e) => setOpeningScript(e.target.value)}
                      placeholder="配信開始時に自動で話す内容..."
                      rows={3}
                      className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-yellow-500 resize-none"
                    />
                  </div>
                  <p className="text-xs text-gray-500">
                    ※ Auto Pilotはハイブリッドモードで人が黙っている時に自動で台本を生成して話します。
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
