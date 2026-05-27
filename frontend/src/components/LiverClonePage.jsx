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
  ShoppingBag,
  Image,
  Sparkles,
} from "lucide-react";
import liverCloneService from "../base/services/liverCloneService";
import { useTranslation } from "react-i18next";

// ── IndexedDB helpers for large binary data (product/face images) ──
const IDB_NAME = "liverClone_imageStore";
const IDB_VERSION = 1;
const IDB_STORE = "images";

function openImageDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, IDB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(IDB_STORE)) {
        db.createObjectStore(IDB_STORE, { keyPath: "id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function saveImageToDB(id, imageBase64, imagePreview) {
  try {
    const db = await openImageDB();
    const tx = db.transaction(IDB_STORE, "readwrite");
    tx.objectStore(IDB_STORE).put({ id, image_base64: imageBase64, image_preview: imagePreview });
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej; });
  } catch (e) {
    console.warn("[IDB] Failed to save image:", e);
  }
}

async function loadImagesFromDB(ids) {
  try {
    const db = await openImageDB();
    const tx = db.transaction(IDB_STORE, "readonly");
    const store = tx.objectStore(IDB_STORE);
    const results = {};
    await Promise.all(ids.map(id => new Promise((res) => {
      const req = store.get(id);
      req.onsuccess = () => { if (req.result) results[id] = req.result; res(); };
      req.onerror = () => res();
    })));
    return results;
  } catch (e) {
    console.warn("[IDB] Failed to load images:", e);
    return {};
  }
}

async function deleteImageFromDB(id) {
  try {
    const db = await openImageDB();
    const tx = db.transaction(IDB_STORE, "readwrite");
    tx.objectStore(IDB_STORE).delete(id);
  } catch (e) {
    console.warn("[IDB] Failed to delete image:", e);
  }
}

async function cleanupOrphanedImages(validIds) {
  try {
    const db = await openImageDB();
    const tx = db.transaction(IDB_STORE, "readwrite");
    const store = tx.objectStore(IDB_STORE);
    const allKeys = await new Promise((res) => {
      const req = store.getAllKeys();
      req.onsuccess = () => res(req.result);
      req.onerror = () => res([]);
    });
    const validSet = new Set(validIds);
    for (const key of allKeys) {
      if (!validSet.has(key)) store.delete(key);
    }
  } catch (e) {
    console.warn("[IDB] Cleanup failed:", e);
  }
}

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

  // Real-time STS (Speech-to-Speech) voice conversion
  const [stsEnabled, setStsEnabled] = useState(() => localStorage.getItem("liverClone_stsEnabled") === "true");
  const [stsActive, setStsActive] = useState(false); // Currently processing STS
  const stsRecorderRef = useRef(null); // MediaRecorder for STS chunks
  const stsIntervalRef = useRef(null); // Interval for STS chunk sending
  const stsQueueRef = useRef([]); // Queue of audio chunks to process
  const stsProcessingRef = useRef(false); // Lock to prevent concurrent STS calls
  const stsAudioContextRef = useRef(null); // Dedicated AudioContext for STS playback

  // Voice ID management
  const [savedVoiceIds, setSavedVoiceIds] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("liverClone_savedVoiceIds") || "[]");
    } catch { return []; }
  });
  const [voiceIdName, setVoiceIdName] = useState("");
  const [voiceValidation, setVoiceValidation] = useState(null); // { valid, name, error, loading }

  // Recording (9:16 vertical video)
  const [isRecording, setIsRecording] = useState(false);
  const [recordedChunks, setRecordedChunks] = useState([]);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const mediaRecorderRef = useRef(null);
  const recordingCanvasRef = useRef(null);
  const recordingTimerRef = useRef(null);
  const recordedFramesRef = useRef([]);

    // Tabs
  const [activeTab, setActiveTab] = useState("config"); // config, comments, autopilot, products, metrics
  const pollRef = useRef(null);
  const fileInputRef = useRef(null);
  // Saved Faces (localStorage metadata + IndexedDB images)
  const [savedFaces, setSavedFaces] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("liverClone_savedFaces") || "[]");
    } catch { return []; }
  });
  const [faceSaveName, setFaceSaveName] = useState("");

  // Product Introduction (localStorage metadata + IndexedDB images)
  const [products, setProducts] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("liverClone_savedProducts") || "[]");
    } catch { return []; }
  });
  const [productGenerating, setProductGenerating] = useState(false);
  const [activeProductIdx, setActiveProductIdx] = useState(null); // Currently displayed product PIP
  const productFileInputRef = useRef(null);
  const productsInitializedRef = useRef(false); // Prevent double-init

  // ── Load health on mount + restore images from IndexedDB ──
  useEffect(() => {
    checkHealth();
    loadExistingSessions();

    // Restore product images from IndexedDB
    (async () => {
      try {
        const saved = JSON.parse(localStorage.getItem("liverClone_savedProducts") || "[]");
        if (saved.length > 0) {
          const ids = saved.map(p => p.id).filter(Boolean);
          if (ids.length > 0) {
            const images = await loadImagesFromDB(ids);
            const restored = saved.map(p => {
              const img = images[p.id];
              if (img) {
                return { ...p, image_base64: img.image_base64 || "", image_preview: img.image_preview || "" };
              }
              return p;
            });
            productsInitializedRef.current = true;
            setProducts(restored);
          }
        }
      } catch (e) {
        console.warn("[Products] Failed to restore images from IndexedDB:", e);
      }
    })();

    // Restore saved face images from IndexedDB
    (async () => {
      try {
        const savedFaceMeta = JSON.parse(localStorage.getItem("liverClone_savedFaces") || "[]");
        if (savedFaceMeta.length === 0) return;
        const faceIds = savedFaceMeta.map(f => `face_${f.id}`);
        const faceImages = await loadImagesFromDB(faceIds);
        const restoredFaces = savedFaceMeta.map(f => {
          const img = faceImages[`face_${f.id}`];
          return {
            ...f,
            preview: img?.image_preview || f.preview || "",
          };
        });
        setSavedFaces(restoredFaces);
      } catch (e) {
        console.warn("[Faces] Failed to restore images from IndexedDB:", e);
      }
    })();
  }, []);

  // ── Persist voice/config settings to localStorage ──
  useEffect(() => {
    localStorage.setItem("liverClone_voiceId", voiceId);
    setVoiceValidation(null); // Reset validation when voice ID changes
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
  useEffect(() => {
    localStorage.setItem("liverClone_stsEnabled", stsEnabled ? "true" : "false");
  }, [stsEnabled]);
  useEffect(() => {
    // Persist products: metadata to localStorage, images to IndexedDB
    // Exclude image_base64 and image_preview from localStorage to avoid quota exceeded
    const toSave = products.map(p => {
      const { image_base64, image_preview, speaking, identifying, ...rest } = p;
      return { ...rest, speaking: false, identifying: false };
    });
    try {
      localStorage.setItem("liverClone_savedProducts", JSON.stringify(toSave));
    } catch (e) {
      console.warn("[Products] localStorage save failed (clearing old data):", e);
      // If still fails, clear old products data
      try {
        localStorage.removeItem("liverClone_savedProducts");
        localStorage.setItem("liverClone_savedProducts", JSON.stringify(toSave));
      } catch (e2) {
        console.error("[Products] Cannot save to localStorage:", e2);
      }
    }
    // Save images to IndexedDB (fire-and-forget)
    products.forEach(p => {
      if (p.image_base64 || p.image_preview) {
        saveImageToDB(p.id, p.image_base64 || "", p.image_preview || "");
      }
    });
    // Cleanup orphaned images
    const validIds = products.map(p => p.id).filter(Boolean);
    cleanupOrphanedImages(validIds);
  }, [products]);

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
      // Get webcam in 9:16 portrait mode for live commerce
      // Request 1080x1920 (portrait) - browser will use closest available
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1080 }, height: { ideal: 1920 }, facingMode: "user" },
        audio: true,
      });
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }

      // Audio passthrough: play microphone audio through speakers
      // When STS is enabled, mute passthrough and use voice conversion instead
      try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const source = audioCtx.createMediaStreamSource(stream);
        const gainNode = audioCtx.createGain();
        // Mute passthrough when STS is enabled (converted audio will play instead)
        gainNode.gain.value = stsEnabled ? 0.0 : 1.0;
        source.connect(gainNode);
        gainNode.connect(audioCtx.destination);
        audioContextRef.current = audioCtx;
        audioStreamRef.current = stream;
        audioGainRef.current = gainNode;
        console.log(`[Preview] Audio passthrough ${stsEnabled ? 'MUTED (STS active)' : 'enabled'}`);
      } catch (audioErr) {
        console.warn("[Preview] Audio passthrough failed (non-critical):", audioErr);
      }

      // Start real-time STS voice conversion if enabled
      if (stsEnabled && voiceId) {
        startSTSConversion(stream);
      }

      // Get WebSocket URL from backend
      const { ws_url } = await liverCloneService.getPreviewWsUrl();
      
      // Connect WebSocket
      const ws = new WebSocket(ws_url);
      ws.binaryType = "arraybuffer";
      previewWsRef.current = ws;

      ws.onopen = async () => {
        console.log("[Preview] WebSocket connected");
        setPreviewActive(true);
        // Re-upload source face to GPU Worker to ensure embedding is set
        // (GPU Worker may have restarted, or source was set before WS connection)
        if (sourceFacePreview) {
          const base64 = sourceFacePreview.split(",")[1];
          try {
            await liverCloneService.previewSetSource(base64);
            console.log("[Preview] Source face re-uploaded to GPU Worker");
          } catch (err) {
            console.warn("[Preview] Failed to re-upload source face:", err);
          }
        } else if (sourceFaceUrl) {
          try {
            await liverCloneService.previewSetSource(null, sourceFaceUrl);
            console.log("[Preview] Source face URL sent to GPU Worker");
          } catch (err) {
            console.warn("[Preview] Failed to send source face URL:", err);
          }
        }
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
    // 9:16 portrait for live commerce (Shopee/TikTok/YouTube Shorts)
    // GPU Worker processes at this resolution directly - no resize needed
    const SEND_W = 360;
    const SEND_H = 640;
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

      // Draw video frame in 9:16 portrait - crop center if camera is landscape
      const vw = video.videoWidth;
      const vh = video.videoHeight;
      const targetRatio = SEND_W / SEND_H; // 9/16 = 0.5625
      const videoRatio = vw / vh;

      let sx = 0, sy = 0, sw = vw, sh = vh;
      if (videoRatio > targetRatio) {
        // Camera is wider than 9:16 - crop sides (center crop)
        sw = Math.floor(vh * targetRatio);
        sx = Math.floor((vw - sw) / 2);
      } else if (videoRatio < targetRatio) {
        // Camera is taller than 9:16 - crop top/bottom
        sh = Math.floor(vw / targetRatio);
        sy = Math.floor((vh - sh) / 2);
      }
      ctx.drawImage(video, sx, sy, sw, sh, 0, 0, SEND_W, SEND_H);

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
    // Stop STS voice conversion
    stopSTSConversion();
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

  // ── Real-time STS (Speech-to-Speech) Voice Conversion ──
  /**
   * Start real-time STS voice conversion.
   * Records microphone audio in chunks (3 seconds each),
   * sends to backend for ElevenLabs STS conversion,
   * and plays the converted audio with lip-sync.
   */
  const startSTSConversion = (stream) => {
    if (!voiceId) {
      console.warn("[STS] No voice ID set, cannot start STS");
      return;
    }
    console.log("[STS] Starting real-time voice conversion (v2 - low latency)");
    setStsActive(true);
    stsQueueRef.current = [];
    stsProcessingRef.current = false;

    // Create a shared AudioContext for playback (reuse to avoid overhead)
    if (!stsAudioContextRef.current || stsAudioContextRef.current.state === 'closed') {
      stsAudioContextRef.current = new (window.AudioContext || window.webkitAudioContext)();
    }

    // Set up VAD using AnalyserNode to detect silence
    const audioStream = new MediaStream(stream.getAudioTracks());
    const vadCtx = new (window.AudioContext || window.webkitAudioContext)();
    const vadSource = vadCtx.createMediaStreamSource(audioStream);
    const analyser = vadCtx.createAnalyser();
    analyser.fftSize = 512;
    vadSource.connect(analyser);

    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : 'audio/mp4';

    const recorder = new MediaRecorder(audioStream, {
      mimeType,
      audioBitsPerSecond: 64000,
    });

    let chunks = [];

    // Check if audio has voice activity (not just noise)
    // Uses both average energy AND peak energy to avoid false positives
    const hasVoiceActivity = () => {
      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(dataArray);
      // Calculate average energy
      let sum = 0;
      let peak = 0;
      for (let i = 0; i < dataArray.length; i++) {
        sum += dataArray[i];
        if (dataArray[i] > peak) peak = dataArray[i];
      }
      const avg = sum / dataArray.length;
      // Require BOTH: average > 25 AND peak > 80 (real speech has high peaks)
      const isVoice = avg > 25 && peak > 80;
      if (!isVoice && avg > 10) {
        console.log(`[STS] VAD: avg=${avg.toFixed(1)}, peak=${peak} → skipped (below threshold)`);
      }
      return isVoice;
    };

    // Track if STS is currently playing (to avoid recording during playback)
    let stsPlaying = false;

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) {
        chunks.push(e.data);
      }
    };

    recorder.onstop = () => {
      if (chunks.length > 0) {
        const blob = new Blob(chunks, { type: mimeType });
        chunks = [];

        // Skip if no voice activity detected
        if (!hasVoiceActivity()) {
          console.log("[STS] Skipping silent chunk");
          return;
        }

        // Allow recording during playback but limit queue to prevent lag
        // (Don't skip during playback - this causes gaps in continuous speech)
        if (stsQueueRef.current.length > 2) {
          console.log(`[STS] Queue overflow (${stsQueueRef.current.length}), keeping latest only`);
          stsQueueRef.current = [stsQueueRef.current[stsQueueRef.current.length - 1]];
        }

        const reader = new FileReader();
        reader.onloadend = () => {
          const base64 = reader.result.split(',')[1];
          if (base64 && base64.length > 800) {
            stsQueueRef.current.push(base64);
            processSTSQueue();
          }
        };
        reader.readAsDataURL(blob);
      }
    };

    stsRecorderRef.current = recorder;
    // Attach playing state helpers AFTER assigning recorder to ref
    stsRecorderRef.current._isPlaying = () => stsPlaying;
    stsRecorderRef.current._setPlaying = (v) => { stsPlaying = v; };

    // Record in 2.5-second chunks (longer = better quality, less overhead)
    const CHUNK_INTERVAL = 2500;
    recorder.start();

    stsIntervalRef.current = setInterval(() => {
      if (recorder.state === 'recording') {
        recorder.stop();
        setTimeout(() => {
          if (stsRecorderRef.current && stsEnabled) {
            try {
              recorder.start();
            } catch (e) {
              console.warn("[STS] Failed to restart recorder:", e);
            }
          }
        }, 30);
      }
    }, CHUNK_INTERVAL);

    // Store VAD context for cleanup
    stsRecorderRef.current._vadCtx = vadCtx;
    console.log(`[STS] Recording started (${CHUNK_INTERVAL}ms chunks, VAD enabled, ${mimeType})`);
  };

  /**
   * Process the STS queue - send audio chunks to backend for conversion.
   * Uses pipeline approach: sends next chunk while previous is playing.
   * This eliminates gaps between chunks for smoother continuous speech.
   */
  const processSTSQueue = async () => {
    if (stsProcessingRef.current) return;
    if (stsQueueRef.current.length === 0) return;

    stsProcessingRef.current = true;

    // Take the first chunk in queue (FIFO order for natural speech flow)
    const audioBase64 = stsQueueRef.current.shift();

    try {
      const sizeKB = (audioBase64.length * 0.75 / 1024).toFixed(1);
      console.log(`[STS] Sending chunk (${sizeKB}KB, queue: ${stsQueueRef.current.length} remaining)`);
      const result = await liverCloneService.previewSTS(audioBase64, voiceId, {
        voice_stability: voiceStability,
        voice_similarity: voiceSimilarity,
      });

      if (result.status === "ok" && result.audio_base64) {
        // Mark as playing for lip-sync purposes
        if (stsRecorderRef.current?._setPlaying) stsRecorderRef.current._setPlaying(true);
        await playSTSAudio(result.audio_base64);
      } else if (result.status === "skipped") {
        console.log(`[STS] Skipped: ${result.reason}`);
      }
    } catch (err) {
      console.error("[STS] Conversion failed:", err.response?.data?.detail || err.message);
    }

    stsProcessingRef.current = false;

    // Process next chunk if available (pipeline: immediately start next)
    if (stsQueueRef.current.length > 0) {
      processSTSQueue();
    }
  };

  /**
   * Play STS-converted audio with lip-sync. Reuses shared AudioContext.
   * Does NOT block recording - allows pipeline processing for smoother output.
   */
  const playSTSAudio = (audioBase64) => {
    return new Promise(async (resolve) => {
      try {
        let ctx = stsAudioContextRef.current;
        if (!ctx || ctx.state === 'closed') {
          ctx = new (window.AudioContext || window.webkitAudioContext)();
          stsAudioContextRef.current = ctx;
        }
        if (ctx.state === "suspended") await ctx.resume();

        // Decode base64 to ArrayBuffer
        const audioData = atob(audioBase64);
        const audioArray = new Uint8Array(audioData.length);
        for (let i = 0; i < audioData.length; i++) {
          audioArray[i] = audioData.charCodeAt(i);
        }

        let audioBuffer;
        try {
          audioBuffer = await ctx.decodeAudioData(audioArray.buffer.slice(0));
        } catch (decodeErr) {
          console.error("[STS] decodeAudioData failed:", decodeErr);
          // Fallback: play via Audio element
          const blob = new Blob([audioArray], { type: 'audio/mpeg' });
          const url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          audio.onended = () => {
            URL.revokeObjectURL(url);
            if (stsRecorderRef.current?._setPlaying) stsRecorderRef.current._setPlaying(false);
            if (previewWsRef.current?.readyState === WebSocket.OPEN) {
              previewWsRef.current.send(JSON.stringify({ type: "mouth_open", value: 0 }));
            }
            resolve();
          };
          audio.onerror = () => {
            URL.revokeObjectURL(url);
            if (stsRecorderRef.current?._setPlaying) stsRecorderRef.current._setPlaying(false);
            resolve();
          };
          // Simple lip-sync for Audio fallback: toggle mouth during playback
          const fallbackLip = setInterval(() => {
            if (previewWsRef.current?.readyState === WebSocket.OPEN) {
              previewWsRef.current.send(JSON.stringify({ type: "mouth_open", value: 0.5 }));
            }
          }, 100);
          audio.addEventListener('ended', () => clearInterval(fallbackLip), { once: true });
          await audio.play().catch(() => {
            if (stsRecorderRef.current?._setPlaying) stsRecorderRef.current._setPlaying(false);
            resolve();
          });
          return;
        }

        // Compute envelope for lip-sync (30 samples/sec for smoother animation)
        const channelData = audioBuffer.getChannelData(0);
        const sampleRate = audioBuffer.sampleRate;
        const LIP_FPS = 30; // Higher analysis rate for smoother mouth movement
        const chunkSize = Math.floor(sampleRate / LIP_FPS);
        const numChunks = Math.ceil(channelData.length / chunkSize);
        const rawEnvelope = new Float32Array(numChunks);
        let maxRms = 0;
        for (let c = 0; c < numChunks; c++) {
          let sum = 0;
          const start = c * chunkSize;
          const end = Math.min(start + chunkSize, channelData.length);
          for (let i = start; i < end; i++) sum += channelData[i] * channelData[i];
          const rms = Math.sqrt(sum / (end - start));
          rawEnvelope[c] = rms;
          if (rms > maxRms) maxRms = rms;
        }
        // Normalize
        const normFactor = maxRms > 0.001 ? (1.0 / maxRms) : 8.0;
        for (let c = 0; c < numChunks; c++) {
          rawEnvelope[c] = Math.pow(Math.min(1.0, rawEnvelope[c] * normFactor), 0.6);
        }
        // Apply temporal smoothing (EMA) to prevent jittery mouth movement
        const envelope = new Float32Array(numChunks);
        const SMOOTH_UP = 0.6;   // Fast attack (mouth opens quickly)
        const SMOOTH_DOWN = 0.3; // Slow release (mouth closes gradually)
        envelope[0] = rawEnvelope[0];
        for (let c = 1; c < numChunks; c++) {
          const alpha = rawEnvelope[c] > envelope[c - 1] ? SMOOTH_UP : SMOOTH_DOWN;
          envelope[c] = envelope[c - 1] + alpha * (rawEnvelope[c] - envelope[c - 1]);
        }

        // Play audio
        const sourceNode = ctx.createBufferSource();
        sourceNode.buffer = audioBuffer;
        sourceNode.connect(ctx.destination);

        const startTime = ctx.currentTime;
        let lipActive = true;
        let prevMouthOpen = 0;
        const lipLoop = () => {
          if (!lipActive) return;
          const elapsed = ctx.currentTime - startTime;
          const idx = Math.floor(elapsed * LIP_FPS);
          if (idx >= numChunks) return;
          // Interpolate between frames for even smoother movement
          const frac = (elapsed * LIP_FPS) - idx;
          const nextIdx = Math.min(idx + 1, numChunks - 1);
          let mouthOpen = envelope[idx] * (1 - frac) + envelope[nextIdx] * frac;
          // Additional runtime smoothing to compensate for WebSocket latency
          mouthOpen = prevMouthOpen + 0.5 * (mouthOpen - prevMouthOpen);
          prevMouthOpen = mouthOpen;
          if (previewWsRef.current?.readyState === WebSocket.OPEN) {
            previewWsRef.current.send(JSON.stringify({ type: "mouth_open", value: mouthOpen }));
          }
        };
        // Send at 30Hz for smooth animation (matches LIP_FPS)
        const lipInterval = setInterval(lipLoop, 33);

        sourceNode.onended = () => {
          lipActive = false;
          clearInterval(lipInterval);
          // Gradually close mouth over 150ms instead of instant snap
          let closeVal = prevMouthOpen;
          const closeInterval = setInterval(() => {
            closeVal *= 0.5;
            if (closeVal < 0.02) {
              clearInterval(closeInterval);
              closeVal = 0;
            }
            if (previewWsRef.current?.readyState === WebSocket.OPEN) {
              previewWsRef.current.send(JSON.stringify({ type: "mouth_open", value: closeVal }));
            }
          }, 33);
          setTimeout(() => clearInterval(closeInterval), 200);
          if (stsRecorderRef.current?._setPlaying) stsRecorderRef.current._setPlaying(false);
          console.log(`[STS] Playback done (${audioBuffer.duration.toFixed(1)}s)`);
          resolve();
        };

        sourceNode.start(0);
      } catch (err) {
        console.error("[STS] playSTSAudio error:", err);
        if (stsRecorderRef.current?._setPlaying) stsRecorderRef.current._setPlaying(false);
        resolve();
      }
    });
  };

  /**
   * Stop STS voice conversion.
   */
  const stopSTSConversion = () => {
    if (stsIntervalRef.current) {
      clearInterval(stsIntervalRef.current);
      stsIntervalRef.current = null;
    }
    if (stsRecorderRef.current) {
      try {
        if (stsRecorderRef.current._vadCtx) {
          stsRecorderRef.current._vadCtx.close().catch(() => {});
        }
        if (stsRecorderRef.current.state !== 'inactive') {
          stsRecorderRef.current.stop();
        }
      } catch (e) {}
      stsRecorderRef.current = null;
    }
    if (stsAudioContextRef.current && stsAudioContextRef.current.state !== 'closed') {
      stsAudioContextRef.current.close().catch(() => {});
      stsAudioContextRef.current = null;
    }
    stsQueueRef.current = [];
    stsProcessingRef.current = false;
    setStsActive(false);
    console.log("[STS] Voice conversion stopped");
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

    console.log("[TTS] speakWithTTS called, previewWs:", previewWsRef.current?.readyState, "previewActive:", previewActive);
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

        // Pre-compute RMS envelope for lip-sync (30Hz for smooth animation)
        const channelData = audioBuffer.getChannelData(0);
        const sampleRate = audioBuffer.sampleRate;
        const LIP_FPS = 30; // 30Hz analysis for smoother mouth movement
        const chunkSize = Math.floor(sampleRate / LIP_FPS);
        const numChunks = Math.ceil(channelData.length / chunkSize);
        const rawEnvelope = new Float32Array(numChunks);
        let maxRms = 0;
        for (let c = 0; c < numChunks; c++) {
          let sum = 0;
          const start = c * chunkSize;
          const end = Math.min(start + chunkSize, channelData.length);
          for (let i = start; i < end; i++) {
            sum += channelData[i] * channelData[i];
          }
          const rms = Math.sqrt(sum / (end - start));
          rawEnvelope[c] = rms;
          if (rms > maxRms) maxRms = rms;
        }
        // Adaptive normalization
        const normFactor = maxRms > 0.001 ? (1.0 / maxRms) : 8.0;
        for (let c = 0; c < numChunks; c++) {
          rawEnvelope[c] = Math.pow(Math.min(1.0, rawEnvelope[c] * normFactor), 0.6);
        }
        // Apply temporal smoothing (EMA) - fast attack, slow release for natural speech
        const envelope = new Float32Array(numChunks);
        const SMOOTH_UP = 0.6;   // Fast attack (mouth opens quickly)
        const SMOOTH_DOWN = 0.3; // Slow release (mouth closes gradually)
        envelope[0] = rawEnvelope[0];
        for (let c = 1; c < numChunks; c++) {
          const alpha = rawEnvelope[c] > envelope[c - 1] ? SMOOTH_UP : SMOOTH_DOWN;
          envelope[c] = envelope[c - 1] + alpha * (rawEnvelope[c] - envelope[c - 1]);
        }
        console.log("[TTS] Envelope computed:", numChunks, "chunks @", LIP_FPS, "Hz, maxRms:", maxRms.toFixed(4));

        // Play audio using AudioBufferSourceNode
        const sourceNode = ttsCtx.createBufferSource();
        sourceNode.buffer = audioBuffer;
        sourceNode.connect(ttsCtx.destination);

        // Ensure AudioContext is running before starting playback
        if (ttsCtx.state === "suspended") {
          await ttsCtx.resume();
          console.log("[TTS] AudioContext re-resumed before playback");
        }

        // Start playback FIRST, then start lip-sync with correct timing
        sourceNode.start(0);
        ttsAudioRef.current = sourceNode;
        console.log("[TTS] Audio playback started via AudioBufferSourceNode");

        // Start lip-sync AFTER sourceNode.start so currentTime is advancing
        const startTime = ttsCtx.currentTime;
        let lipSyncActive = true;
        let lipSyncSendCount = 0;
        let prevMouthOpen = 0;
        const lipSyncLoop = () => {
          if (!lipSyncActive) return;
          const elapsed = ttsCtx.currentTime - startTime;
          const idx = Math.floor(elapsed * LIP_FPS);
          if (idx >= numChunks) return;
          // Interpolate between envelope frames for sub-frame smoothness
          const frac = (elapsed * LIP_FPS) - idx;
          const nextIdx = Math.min(idx + 1, numChunks - 1);
          let mouthOpen = envelope[idx] * (1 - frac) + envelope[nextIdx] * frac;
          // Additional runtime smoothing to compensate for WebSocket latency
          mouthOpen = prevMouthOpen + 0.5 * (mouthOpen - prevMouthOpen);
          prevMouthOpen = mouthOpen;

          if (previewWsRef.current?.readyState === WebSocket.OPEN) {
            previewWsRef.current.send(JSON.stringify({ type: "mouth_open", value: mouthOpen }));
            lipSyncSendCount++;
            if (lipSyncSendCount % 15 === 1) {
              console.log(`[LipSync] mouth_open=${mouthOpen.toFixed(3)} chunk=${idx}/${numChunks} elapsed=${elapsed.toFixed(2)}s`);
            }
          } else {
            if (lipSyncSendCount === 0) {
              console.warn("[LipSync] WebSocket not open, cannot send mouth_open. previewActive:", previewActive);
            }
          }
        };
        lipSyncIntervalRef.current = setInterval(lipSyncLoop, 33); // 30Hz
        console.log(`[LipSync] Started lip-sync loop (30Hz, ${numChunks} chunks, duration=${audioBuffer.duration.toFixed(2)}s)`);

        const cleanup = () => {
          lipSyncActive = false;
          if (lipSyncIntervalRef.current) {
            clearInterval(lipSyncIntervalRef.current);
            lipSyncIntervalRef.current = null;
          }
          // Gradually close mouth over 150ms instead of instant snap
          let closeVal = prevMouthOpen;
          const closeInterval = setInterval(() => {
            closeVal *= 0.5;
            if (closeVal < 0.02) {
              clearInterval(closeInterval);
              closeVal = 0;
            }
            if (previewWsRef.current?.readyState === WebSocket.OPEN) {
              previewWsRef.current.send(JSON.stringify({ type: "mouth_open", value: closeVal }));
            }
          }, 33);
          setTimeout(() => clearInterval(closeInterval), 200);
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

      } catch (err) {
        console.error("[TTS] playTTSAudio error:", err);
        resolve();
      }
    });
  };

  // ── Product Introduction Functions ──
  const handleProductImageUpload = (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    files.forEach((file, fileIdx) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const base64 = reader.result.split(',')[1];
        const newProduct = {
          id: Date.now() + fileIdx,
          image_base64: base64,
          image_preview: reader.result,
          name: "",
          info: "",
          script: "",
          speaking: false,
          identifying: true, // Auto-identify in progress
        };
        setProducts(prev => {
          const updated = [...prev, newProduct];
          // Auto-identify this product
          autoIdentifyProduct(updated.length - 1, base64);
          return updated;
        });
      };
      reader.readAsDataURL(file);
    });
    e.target.value = ""; // Reset input
  };

  // Auto-identify product from image using GPT-4 Vision
  const autoIdentifyProduct = async (productIdx, imageBase64) => {
    try {
      const result = await liverCloneService.generateProductIntro({
        image_base64: imageBase64,
        product_name: "",
        product_info: "",
        language: language,
        style: "identify", // Special mode: just identify, don't generate script
        max_length: 50,
      });
      if (result.status === "ok" && result.script) {
        // Parse the identification result (format: "商品名: xxx\n特徴: yyy")
        const lines = result.script.split('\n');
        let name = "";
        let info = "";
        for (const line of lines) {
          if (line.includes('商品名') || line.includes('Product')) {
            name = line.replace(/^.*[:：]\s*/, '').trim();
          } else if (line.includes('特徴') || line.includes('Feature') || line.includes('情報')) {
            info = line.replace(/^.*[:：]\s*/, '').trim();
          } else if (!name && line.trim()) {
            name = line.trim();
          } else if (name && !info && line.trim()) {
            info = line.trim();
          }
        }
        if (!name && result.script.length < 100) name = result.script.trim();
        setProducts(prev => prev.map((p, i) =>
          i === productIdx ? { ...p, name: name || p.name, info: info || p.info, identifying: false } : p
        ));
      } else {
        setProducts(prev => prev.map((p, i) =>
          i === productIdx ? { ...p, identifying: false } : p
        ));
      }
    } catch (err) {
      console.error("[Product] Auto-identify failed:", err);
      setProducts(prev => prev.map((p, i) =>
        i === productIdx ? { ...p, identifying: false } : p
      ));
    }
  };

  const generateProductScript = async (productIdx) => {
    const product = products[productIdx];
    if (!product) return;
    setProductGenerating(true);
    try {
      const result = await liverCloneService.generateProductIntro({
        image_base64: product.image_base64,
        product_name: product.name,
        product_info: product.info,
        language: language,
        style: "enthusiastic",
        max_length: 150,
      });
      if (result.status === "ok" && result.script) {
        setProducts(prev => prev.map((p, i) =>
          i === productIdx ? { ...p, script: result.script } : p
        ));
      }
    } catch (err) {
      console.error("[Product] Script generation failed:", err);
      setError("商品スクリプト生成に失敗しました: " + (err.response?.data?.detail || err.message));
    } finally {
      setProductGenerating(false);
    }
  };

  const speakProductScript = async (productIdx) => {
    const product = products[productIdx];
    if (!product?.script) return;
    // Show product PIP
    setActiveProductIdx(productIdx);
    setProducts(prev => prev.map((p, i) =>
      i === productIdx ? { ...p, speaking: true } : p
    ));
    // Speak the script via TTS
    await speakWithTTS(product.script);
    // After speaking, keep PIP visible for 2 more seconds
    setTimeout(() => {
      setProducts(prev => prev.map((p, i) =>
        i === productIdx ? { ...p, speaking: false } : p
      ));
      setActiveProductIdx(null);
    }, 2000);
  };

  const removeProduct = (productIdx) => {
    const removedProduct = products[productIdx];
    if (removedProduct?.id) {
      deleteImageFromDB(removedProduct.id);
    }
    setProducts(prev => prev.filter((_, i) => i !== productIdx));
    if (activeProductIdx === productIdx) setActiveProductIdx(null);
  };

  // ── Auto Product Detection (Camera-based) ──
  const [autoProductDetect, setAutoProductDetect] = useState(false);
  const autoDetectIntervalRef = useRef(null);
  const lastDetectedProductRef = useRef(""); // Prevent re-detecting same product
  const autoDetectCooldownRef = useRef(false); // Cooldown after detection

  const startAutoProductDetection = () => {
    if (autoDetectIntervalRef.current) return;
    setAutoProductDetect(true);
    console.log("[AutoDetect] Started auto product detection (every 5s)");
    
    autoDetectIntervalRef.current = setInterval(async () => {
      // Skip if cooldown active, speaking, or no preview
      if (autoDetectCooldownRef.current || isSpeaking) return;
      if (!videoRef.current || !videoRef.current.videoWidth) return;
      if (!previewActive) return;

      try {
        // Capture current camera frame
        const canvas = document.createElement('canvas');
        const video = videoRef.current;
        canvas.width = 320; // Low res for fast API call
        canvas.height = 240;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, 0, 320, 240);
        const frameBase64 = canvas.toDataURL('image/jpeg', 0.6).split(',')[1];

        // Send to AI for product detection
        const result = await liverCloneService.generateProductIntro({
          image_base64: frameBase64,
          product_name: "",
          product_info: "",
          language: language,
          style: "identify",
          max_length: 50,
        });

        if (result.status === "ok" && result.script) {
          // Parse identification
          const lines = result.script.split('\n');
          let name = "";
          for (const line of lines) {
            if (line.includes('商品名') || line.includes('Product')) {
              name = line.replace(/^.*[:：]\s*/, '').trim();
              break;
            } else if (!name && line.trim() && line.trim().length < 50) {
              name = line.trim();
            }
          }

          // Check if it's a new product (not the same as last detected)
          if (name && name !== lastDetectedProductRef.current && 
              !name.includes('人物') && !name.includes('顔') && !name.includes('背景') &&
              !name.includes('person') && !name.includes('face')) {
            console.log(`[AutoDetect] New product detected: ${name}`);
            lastDetectedProductRef.current = name;
            
            // Set cooldown (30s) to prevent rapid re-detection
            autoDetectCooldownRef.current = true;
            setTimeout(() => { autoDetectCooldownRef.current = false; }, 30000);

            // Auto-generate script and speak
            const scriptResult = await liverCloneService.generateProductIntro({
              image_base64: frameBase64,
              product_name: name,
              product_info: result.script,
              language: language,
              style: "enthusiastic",
              max_length: 150,
            });

            if (scriptResult.status === "ok" && scriptResult.script) {
              // Add to products list and auto-speak
              const newProduct = {
                id: Date.now(),
                image_base64: frameBase64,
                image_preview: `data:image/jpeg;base64,${frameBase64}`,
                name: name,
                info: result.script,
                script: scriptResult.script,
                speaking: false,
                autoDetected: true,
              };
              setProducts(prev => {
                const updated = [...prev, newProduct];
                // Auto-speak the new product
                const newIdx = updated.length - 1;
                setTimeout(() => speakProductScript(newIdx), 500);
                return updated;
              });
            }
          }
        }
      } catch (err) {
        console.warn("[AutoDetect] Detection cycle failed:", err.message);
      }
    }, 5000); // Check every 5 seconds
  };

  const stopAutoProductDetection = () => {
    if (autoDetectIntervalRef.current) {
      clearInterval(autoDetectIntervalRef.current);
      autoDetectIntervalRef.current = null;
    }
    setAutoProductDetect(false);
    autoDetectCooldownRef.current = false;
    console.log("[AutoDetect] Stopped auto product detection");
  };

  // Clean up auto-detect on unmount or preview stop
  useEffect(() => {
    if (!previewActive && autoDetectIntervalRef.current) {
      stopAutoProductDetection();
    }
  }, [previewActive]);

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
          {/* ── Left: Stream Preview (9:16 portrait) ── */}
          <div className="lg:col-span-2 space-y-4">
            {/* Stream Preview */}
            <div className="bg-[#12121a] rounded-xl border border-gray-800 overflow-hidden">
              <div
                className="aspect-[9/16] max-h-[calc(100vh-200px)] bg-black flex items-center justify-center relative cursor-pointer"
                onClick={() => {
                  // Allow clicking the preview area to upload face photo when idle
                  if (!previewActive && !isStreaming && !sourceFacePreview) {
                    fileInputRef.current?.click();
                  }
                }}
              >
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
                  <div
                    className="w-full h-full relative group"
                    onClick={(e) => {
                      e.stopPropagation();
                      fileInputRef.current?.click();
                    }}
                  >
                    <img
                      src={sourceFacePreview}
                      alt="Source face"
                      className="w-full h-full object-cover opacity-50 group-hover:opacity-30 transition"
                    />
                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition">
                      <div className="text-center">
                        <Camera className="w-8 h-8 text-cyan-400 mx-auto mb-1" />
                        <p className="text-xs text-cyan-400">クリックで変更</p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="text-center hover:scale-105 transition">
                    <Camera className="w-12 h-12 text-gray-600 mx-auto mb-2 group-hover:text-cyan-400" />
                    <p className="text-sm text-gray-500">
                      顔写真をアップロード
                    </p>
                    <p className="text-xs text-gray-600 mt-1">
                      クリックまたは右の⬆ボタン
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
                      {"9:16 Portrait"}
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
          <div className="lg:col-span-3 space-y-4 max-h-[calc(100vh-140px)] overflow-y-auto">
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
                    {/* Save Face */}
                    {sourceFacePreview && (
                      <div className="flex gap-2 items-center">
                        <input
                          type="text"
                          value={faceSaveName}
                          onChange={(e) => setFaceSaveName(e.target.value)}
                          placeholder="名前をつけて保存..."
                          className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-cyan-500"
                        />
                        <button
                          onClick={async () => {
                            if (!faceSaveName.trim() || !sourceFacePreview) return;
                            const faceId = Date.now();
                            const newFace = {
                              id: faceId,
                              name: faceSaveName.trim(),
                              preview: sourceFacePreview,
                              url: sourceFaceUrl || "",
                            };
                            const updated = [...savedFaces, newFace];
                            setSavedFaces(updated);
                            // Save metadata (without preview) to localStorage, image to IndexedDB
                            const metaOnly = updated.map(f => ({ id: f.id, name: f.name, url: f.url || "" }));
                            try {
                              localStorage.setItem("liverClone_savedFaces", JSON.stringify(metaOnly));
                            } catch (e) {
                              console.warn("[Faces] localStorage save failed:", e);
                            }
                            await saveImageToDB(`face_${faceId}`, "", sourceFacePreview);
                            setFaceSaveName("");
                          }}
                          className="px-3 py-1.5 bg-cyan-600 hover:bg-cyan-500 rounded-lg text-xs font-medium transition"
                        >
                          保存
                        </button>
                      </div>
                    )}
                    {/* Saved Faces List */}
                    {savedFaces.length > 0 && (
                      <div className="space-y-1">
                        <label className="text-xs text-gray-400 block">保存済み顔</label>
                        <div className="grid grid-cols-3 gap-2">
                          {savedFaces.map((face) => (
                            <div
                              key={face.id}
                              className="relative group cursor-pointer border border-gray-700 rounded-lg overflow-hidden hover:border-cyan-500 transition"
                              onClick={() => {
                                setSourceFacePreview(face.preview);
                                setSourceFaceUrl(face.url || "");
                                // Upload to GPU Worker
                                const base64 = face.preview.split(",")[1];
                                uploadSourceFace(base64);
                              }}
                            >
                              <img src={face.preview} alt={face.name} className="w-full h-16 object-cover" />
                              <div className="absolute bottom-0 left-0 right-0 bg-black/70 px-1 py-0.5 text-[10px] truncate">
                                {face.name}
                              </div>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  const updated = savedFaces.filter((f) => f.id !== face.id);
                                  setSavedFaces(updated);
                                  const metaOnly = updated.map(f => ({ id: f.id, name: f.name, url: f.url || "" }));
                                  try {
                                    localStorage.setItem("liverClone_savedFaces", JSON.stringify(metaOnly));
                                  } catch (e2) { console.warn("[Faces] localStorage save failed:", e2); }
                                  deleteImageFromDB(`face_${face.id}`);
                                }}
                                className="absolute top-0.5 right-0.5 w-4 h-4 bg-red-600 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition"
                              >
                                <Trash2 className="w-2.5 h-2.5" />
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
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
                          className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono mb-2 focus:outline-none focus:border-purple-500"
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
                          className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:border-purple-500"
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
                            onClick={async () => {
                              if (!voiceIdName.trim() || !voiceId.trim()) return;
                              // Validate Voice ID before saving
                              setVoiceValidation({ loading: true });
                              try {
                                const result = await liverCloneService.validateVoiceId(voiceId.trim());
                                if (result.valid) {
                                  const exists = savedVoiceIds.some(v => v.id === voiceId);
                                  if (exists) {
                                    setSavedVoiceIds(prev => prev.map(v => v.id === voiceId ? { ...v, name: voiceIdName.trim() } : v));
                                  } else {
                                    setSavedVoiceIds(prev => [...prev, { id: voiceId.trim(), name: voiceIdName.trim() }]);
                                  }
                                  setVoiceIdName("");
                                  setVoiceValidation({ valid: true, name: result.name });
                                } else {
                                  setVoiceValidation({ valid: false, error: result.error || "Voice ID not found" });
                                }
                              } catch (err) {
                                setVoiceValidation({ valid: false, error: "Validation failed: " + (err.response?.data?.detail || err.message) });
                              }
                            }}
                            disabled={!voiceIdName.trim() || voiceValidation?.loading}
                            className="px-3 py-1 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded-lg text-xs transition"
                          >
                            {voiceValidation?.loading ? "検証中..." : "保存"}
                          </button>
                        </div>
                      )}
                      {/* Voice ID validation result */}
                      {voiceValidation && !voiceValidation.loading && (
                        <div className={`mt-2 px-3 py-2 rounded-lg text-xs flex items-center gap-2 ${
                          voiceValidation.valid
                            ? 'bg-green-900/30 border border-green-700 text-green-300'
                            : 'bg-red-900/30 border border-red-700 text-red-300'
                        }`}>
                          {voiceValidation.valid ? (
                            <><CheckCircle className="w-3 h-3" /> Voice ID確認済み: {voiceValidation.name}</>
                          ) : (
                            <><AlertCircle className="w-3 h-3" /> {voiceValidation.error}</>
                          )}
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
                    {/* Real-time STS Voice Conversion Toggle */}
                    <div className="flex items-center justify-between bg-gray-900/50 rounded-lg px-3 py-2">
                      <div>
                        <p className="text-xs font-semibold text-cyan-300">リアルタイム音声変換</p>
                        <p className="text-[10px] text-gray-500">マイク音声をAI声に変換（プレビュー時）</p>
                      </div>
                      <button
                        onClick={() => {
                          const next = !stsEnabled;
                          setStsEnabled(next);
                          // If preview is active, toggle STS immediately
                          if (previewActive && audioStreamRef.current) {
                            if (next && voiceId) {
                              // Mute passthrough and start STS
                              if (audioGainRef.current) audioGainRef.current.gain.value = 0.0;
                              startSTSConversion(audioStreamRef.current);
                            } else {
                              // Restore passthrough and stop STS
                              if (audioGainRef.current) audioGainRef.current.gain.value = 1.0;
                              stopSTSConversion();
                            }
                          }
                        }}
                        className={`relative w-10 h-5 rounded-full transition-colors ${
                          stsEnabled ? 'bg-cyan-600' : 'bg-gray-700'
                        }`}
                      >
                        <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                          stsEnabled ? 'translate-x-5' : 'translate-x-0'
                        }`} />
                      </button>
                    </div>
                    {stsActive && (
                      <div className="flex items-center gap-2 px-3 py-1.5 bg-cyan-900/20 border border-cyan-800 rounded-lg">
                        <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                        <span className="text-[10px] text-cyan-300">音声変換中...</span>
                      </div>
                    )}
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

                {/* ── Product Introduction Section (inside config tab) ── */}
                <div className="bg-[#12121a] rounded-xl border border-gray-800 p-5">
                  <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                    <ShoppingBag className="w-4 h-4 text-green-400" />
                    商品紹介
                  </h3>
                  <div className="space-y-4">
                    {/* Auto Product Detection Toggle */}
                    {previewActive && (
                      <div className="flex items-center justify-between p-3 bg-gray-900 rounded-lg border border-gray-700">
                        <div>
                          <p className="text-sm font-medium text-green-400">自動商品検出</p>
                          <p className="text-xs text-gray-500">カメラに商品を映すと自動で紹介</p>
                        </div>
                        <button
                          onClick={() => autoProductDetect ? stopAutoProductDetection() : startAutoProductDetection()}
                          className={`relative w-11 h-6 rounded-full transition-colors ${
                            autoProductDetect ? 'bg-green-500' : 'bg-gray-600'
                          }`}
                        >
                          <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                            autoProductDetect ? 'translate-x-5' : ''
                          }`} />
                        </button>
                      </div>
                    )}
                    {autoProductDetect && (
                      <div className="flex items-center gap-2 px-3 py-2 bg-green-900/30 rounded-lg border border-green-700/50">
                        <span className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
                        <span className="text-xs text-green-300">商品を検出中...</span>
                      </div>
                    )}
                    {/* Upload button */}
                    <div>
                      <input
                        ref={productFileInputRef}
                        type="file"
                        accept="image/*"
                        multiple
                        onChange={handleProductImageUpload}
                        className="hidden"
                      />
                      <button
                        onClick={() => productFileInputRef.current?.click()}
                        className="w-full flex items-center justify-center gap-2 py-3 px-4 border-2 border-dashed border-gray-600 rounded-lg text-gray-400 hover:border-green-500 hover:text-green-400 transition"
                      >
                        <Upload className="w-4 h-4" />
                        商品画像をアップロード
                      </button>
                    </div>

                    {/* Product list */}
                    {products.map((product, idx) => (
                      <div key={product.id} className="bg-gray-900 rounded-lg p-3 border border-gray-700">
                        <div className="flex gap-3">
                          <div className="w-16 h-16 rounded-lg overflow-hidden flex-shrink-0 bg-gray-800">
                            <img
                              src={product.image_preview}
                              alt="商品"
                              className="w-full h-full object-cover"
                            />
                          </div>
                          <div className="flex-1 min-w-0">
                            {product.identifying ? (
                              <div className="flex items-center gap-2 py-2">
                                <Loader2 className="w-3 h-3 animate-spin text-green-400" />
                                <span className="text-xs text-green-400">AIが商品を識別中...</span>
                              </div>
                            ) : (
                              <>
                                <input
                                  type="text"
                                  value={product.name}
                                  onChange={(e) => setProducts(prev => prev.map((p, i) =>
                                    i === idx ? { ...p, name: e.target.value } : p
                                  ))}
                                  placeholder="商品名（任意）"
                                  className="w-full bg-transparent border-b border-gray-700 text-sm py-1 focus:outline-none focus:border-green-500 mb-1"
                                />
                                <input
                                  type="text"
                                  value={product.info}
                                  onChange={(e) => setProducts(prev => prev.map((p, i) =>
                                    i === idx ? { ...p, info: e.target.value } : p
                                  ))}
                                  placeholder="商品情報（任意：価格、特徴など）"
                                  className="w-full bg-transparent border-b border-gray-700 text-xs text-gray-400 py-1 focus:outline-none focus:border-green-500"
                                />
                              </>
                            )}
                          </div>
                          <button
                            onClick={() => removeProduct(idx)}
                            className="text-gray-500 hover:text-red-400 p-1"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>

                        {/* Script area */}
                        {product.script ? (
                          <div className="mt-3">
                            <textarea
                              value={product.script}
                              onChange={(e) => setProducts(prev => prev.map((p, i) =>
                                i === idx ? { ...p, script: e.target.value } : p
                              ))}
                              rows={3}
                              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-green-500 resize-none"
                            />
                            <div className="flex gap-2 mt-2">
                              <button
                                onClick={() => generateProductScript(idx)}
                                disabled={productGenerating}
                                className="flex-1 flex items-center justify-center gap-1 py-1.5 px-3 bg-gray-700 hover:bg-gray-600 rounded-lg text-xs transition disabled:opacity-50"
                              >
                                <Sparkles className="w-3 h-3" />
                                再生成
                              </button>
                              <button
                                onClick={() => speakProductScript(idx)}
                                disabled={isSpeaking || !previewActive}
                                className="flex-1 flex items-center justify-center gap-1 py-1.5 px-3 bg-green-600 hover:bg-green-500 rounded-lg text-xs transition disabled:opacity-50"
                              >
                                <Volume2 className="w-3 h-3" />
                                {product.speaking ? "読み上げ中..." : "読み上げ"}
                              </button>
                            </div>
                          </div>
                        ) : (
                          <button
                            onClick={() => generateProductScript(idx)}
                            disabled={productGenerating}
                            className="mt-3 w-full flex items-center justify-center gap-2 py-2 px-3 bg-green-600/20 hover:bg-green-600/30 border border-green-600/50 rounded-lg text-xs text-green-400 transition disabled:opacity-50"
                          >
                            {productGenerating ? (
                              <><Loader2 className="w-3 h-3 animate-spin" /> スクリプト生成中...</>
                            ) : (
                              <><Sparkles className="w-3 h-3" /> AIスクリプトを生成</>
                            )}
                          </button>
                        )}
                      </div>
                    ))}

                    {products.length === 0 && (
                      <p className="text-xs text-gray-500 text-center py-2">
                        商品画像をアップロードすると、AIが自動で商品紹介スクリプトを生成します。
                      </p>
                    )}
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

      {/* Product PIP overlay */}
      {activeProductIdx !== null && products[activeProductIdx] && (
        <div className="fixed bottom-24 right-6 z-50 animate-fade-in">
          <div className="bg-black/80 backdrop-blur-sm rounded-xl border border-green-500/50 p-2 shadow-2xl">
            <img
              src={products[activeProductIdx].image_preview}
              alt="商品"
              className="w-32 h-32 object-cover rounded-lg"
            />
            {products[activeProductIdx].name && (
              <p className="text-xs text-center text-white mt-1 font-medium truncate max-w-[128px]">
                {products[activeProductIdx].name}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
