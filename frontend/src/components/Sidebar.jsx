import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";

import logo from "../assets/logo/logo.svg";
import searchMobile from "../assets/icons/searchmobile.png";
import searchSp from "../assets/icons/searchSp.png";
import library from "../assets/icons/Library.png";

import "../assets/css/sidebar.css";
import ForgotPasswordModal from "./modals/ForgotPasswordModal";
import AuthService from "../base/services/userService";
import backgroundUploadManager from "../base/services/backgroundUploadManager";
import UploadService from "../base/services/uploadService";
import VideoService from "../base/services/videoService";
import personaService from "../base/services/personaService";

import { ChevronDown, LogOut, Settings, User, Users, X, MoreHorizontal, Pencil, Trash2, Scissors, MessageSquareText, Radio, Video, Eye, Calendar, Sparkles, UserCircle, Clapperboard, Wand2, Brain, Check, FileText, Globe, Upload, AlertCircle, CheckCircle2, RotateCcw, FileUp } from "lucide-react";
import { useTranslation } from 'react-i18next';
import { changeLanguage } from '../i18n';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "./ui/dropdown-menu";

export default function Sidebar({ isOpen, onClose, user, onVideoSelect, onNewAnalysis, onShowFeedback, refreshKey, selectedVideo, showFeedback }) {
  const sidebarRef = useRef(null);
  const { t, i18n } = useTranslation();
  const [openForgotPassword, setOpenForgotPassword] = useState(false);
  const [videos, setVideos] = useState([]);
  const [loadingVideos, setLoadingVideos] = useState(false);
  const [videoListError, setVideoListError] = useState(null); // null | 'error' | 'auth'
  const [selectedVideoId, setSelectedVideoId] = useState(null);
  const [menuOpenVideoId, setMenuOpenVideoId] = useState(null);
  const [renamingVideoId, setRenamingVideoId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteConfirmVideoId, setDeleteConfirmVideoId] = useState(null);
  const menuRef = useRef(null);
  const [personas, setPersonas] = useState([]);
  const [videoPersonaTags, setVideoPersonaTags] = useState({}); // { videoId: [personaId, ...] }
  const [personaMenuVideoId, setPersonaMenuVideoId] = useState(null);
  const [taggingInProgress, setTaggingInProgress] = useState(false);

  // Background upload tasks state
  const [bgUploadTasks, setBgUploadTasks] = useState([]);
  const resumeFileInputRef = useRef(null);
  const [resumingTaskId, setResumingTaskId] = useState(null);

  useEffect(() => {
    const unsub = backgroundUploadManager.subscribe((tasks) => {
      setBgUploadTasks(tasks);
    });
    return unsub;
  }, []);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpenVideoId(null);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleDeleteVideo = async (videoId) => {
    try {
      await VideoService.deleteVideo(videoId);
      setVideos((prev) => prev.filter((v) => v.id !== videoId));
      setDeleteConfirmVideoId(null);
      setMenuOpenVideoId(null);
      if (selectedVideoId === videoId) {
        setSelectedVideoId(null);
        navigate('/');
        if (onVideoSelect) onVideoSelect(null);
        if (onNewAnalysis) onNewAnalysis();
      }
    } catch (error) {
      console.error("Failed to delete video:", error);
      alert(window.__t("sidebar_deleteVideoFailed"));
    }
  };

  const handleRenameVideo = async (videoId) => {
    const newName = renameValue.trim();
    if (!newName) return;
    try {
      await VideoService.renameVideo(videoId, newName);
      setVideos((prev) =>
        prev.map((v) =>
          v.id === videoId ? { ...v, original_filename: newName } : v
        )
      );
      setRenamingVideoId(null);
      setRenameValue("");
      setMenuOpenVideoId(null);
    } catch (error) {
      console.error("Failed to rename video:", error);
      alert(window.__t("sidebar_renameFailed"));
    }
  };

  // Sync selectedVideoId when video is selected from outside (e.g., after upload)
  useEffect(() => {
    if (selectedVideo?.id) {
      setSelectedVideoId(selectedVideo.id);
    }
  }, [selectedVideo?.id]);

  // ===== Fetch personas and their video tags =====
  useEffect(() => {
    const fetchPersonas = async () => {
      try {
        const data = await personaService.listPersonas();
        const list = data.personas || data || [];
        setPersonas(list);
        // Build videoId -> [personaId] map from list API (tagged_video_ids included)
        const tagMap = {};
        for (const p of list) {
          if (p.tagged_video_ids && p.tagged_video_ids.length > 0) {
            for (const vid of p.tagged_video_ids) {
              if (!tagMap[vid]) tagMap[vid] = [];
              if (!tagMap[vid].includes(p.id)) tagMap[vid].push(p.id);
            }
          }
        }
        setVideoPersonaTags(tagMap);
      } catch (e) {
        console.warn('[Sidebar] Failed to fetch personas:', e);
      }
    };
    fetchPersonas();
  }, []);

  const handleTogglePersonaTag = async (videoId, personaId) => {
    if (taggingInProgress) return;
    setTaggingInProgress(true);
    try {
      const currentTags = videoPersonaTags[videoId] || [];
      if (currentTags.includes(personaId)) {
        // Untag
        await personaService.untagVideos(personaId, [videoId]);
        setVideoPersonaTags(prev => {
          const updated = { ...prev };
          updated[videoId] = (updated[videoId] || []).filter(id => id !== personaId);
          if (updated[videoId].length === 0) delete updated[videoId];
          return updated;
        });
      } else {
        // Tag
        await personaService.tagVideos(personaId, [videoId]);
        setVideoPersonaTags(prev => {
          const updated = { ...prev };
          if (!updated[videoId]) updated[videoId] = [];
          updated[videoId] = [...updated[videoId], personaId];
          return updated;
        });
      }
    } catch (e) {
      console.error('[Sidebar] Failed to toggle persona tag:', e);
    } finally {
      setTaggingInProgress(false);
      setPersonaMenuVideoId(null);
      setMenuOpenVideoId(null);
    }
  };

  const getPersonaName = (personaId) => {
    const p = personas.find(p => p.id === personaId);
    return p ? p.name : '';
  };

  // ===== sidebar search (PC + SP) =====
  const [searchValue, setSearchValue] = useState("");
  const [showBackButton, setShowBackButton] = useState(false);

  // ===== user fallback =====
  // Use prop user if available, otherwise fall back to localStorage.
  // Additionally, verify that the stored token's user_id matches the user's id
  // to prevent mismatched token/user scenarios (e.g., after switching accounts).
  const effectiveUser = (() => {
    const u = user ?? (() => {
      try {
        const s = localStorage.getItem("user");
        return s ? JSON.parse(s) : { isLoggedIn: false };
      } catch {
        return { isLoggedIn: false };
      }
    })();

    // Safety check: if user is logged in, verify token matches user id
    if (u?.isLoggedIn && u?.id) {
      try {
        const token = localStorage.getItem('app_access_token');
        if (token) {
          const payload = JSON.parse(atob(token.split('.')[1]));
          const tokenUserId = payload?.sub;
          if (tokenUserId && String(tokenUserId) !== String(u.id)) {
            // Token belongs to a different user - treat as not logged in
            // This prevents 403 errors from mismatched token/userId
            console.warn('[Sidebar] Token user mismatch: token.sub=', tokenUserId, 'user.id=', u.id);
            return { isLoggedIn: false };
          }
        }
      } catch {
        // Ignore JWT parse errors
      }
    }
    return u;
  })();

  // ===== fetch videos =====
  const fetchVideosRef = useRef(null);
  const loadingTimeoutRef = useRef(null);

  const doFetchVideos = useCallback(async () => {
    const userId = effectiveUser?.id || effectiveUser?.email;
    if (!effectiveUser?.isLoggedIn || !userId) {
      setVideos([]);
      setSelectedVideoId(null);
      setLoadingVideos(false);
      return;
    }

    setLoadingVideos(true);
    setVideoListError(null);

    // Safety timeout: if API doesn't respond within 20s, show error instead of spinner
    if (loadingTimeoutRef.current) clearTimeout(loadingTimeoutRef.current);
    loadingTimeoutRef.current = setTimeout(() => {
      setLoadingVideos((prev) => {
        if (prev) {
          console.warn('[Sidebar] Video list loading timed out after 20s');
          setVideoListError('error');
          return false;
        }
        return prev;
      });
    }, 20000);

    try {
      const videoList = await VideoService.getVideosByUser(userId);
      // Deduplicate: keep only the latest video per original_filename
      const seen = new Map();
      const deduped = [];
      for (const v of (videoList || [])) {
        const key = v.original_filename || v.id;
        if (!seen.has(key)) {
          seen.set(key, true);
          deduped.push(v);
        }
      }
      setVideos(deduped);
      setVideoListError(null);
    } catch (error) {
      console.error("[Sidebar] Error fetching videos:", error);
      // Distinguish auth errors from general errors
      const status = error?.response?.status;
      if (status === 401 || status === 403) {
        setVideoListError('auth');
      } else {
        setVideoListError('error');
      }
      // Keep existing videos if we had some (don't flash empty on transient errors)
      if (videos.length === 0) {
        setVideos([]);
      }
    } finally {
      if (loadingTimeoutRef.current) {
        clearTimeout(loadingTimeoutRef.current);
        loadingTimeoutRef.current = null;
      }
      setLoadingVideos(false);
    }
  }, [effectiveUser?.isLoggedIn, effectiveUser?.id, effectiveUser?.email]);

  useEffect(() => {
    doFetchVideos();
    return () => {
      if (loadingTimeoutRef.current) {
        clearTimeout(loadingTimeoutRef.current);
        loadingTimeoutRef.current = null;
      }
    };
  }, [effectiveUser?.isLoggedIn, effectiveUser?.id, effectiveUser?.email, refreshKey]);

  // Auto-refresh sidebar when there are processing videos (QUEUED, STEP_*, UPLOADED)
  useEffect(() => {
    const hasProcessing = videos.some(v =>
      v.status && v.status !== 'DONE' && v.status !== 'ERROR'
    );
    const hasError = videos.some(v => v.status === 'ERROR');
    // Refresh every 15s if processing (for progress % updates), every 60s if only errors
    const interval = hasProcessing ? 15000 : hasError ? 60000 : null;
    if (!interval) return;
    const timer = setInterval(() => {
      doFetchVideos();
    }, interval);
    return () => clearInterval(timer);
  }, [videos, doFetchVideos]);

  const filteredVideos = useMemo(() => {
    const query = searchValue.trim().toLowerCase();
    if (!query) return videos;
    return (videos || []).filter((video) => {
      const name = (video?.original_filename ?? "").toString().toLowerCase();
      const id = (video?.id ?? "").toString().toLowerCase();
      return name.includes(query) || id.includes(query);
    });
  }, [videos, searchValue]);

  // Separate live captures from regular video analyses
  const liveVideos = useMemo(() => {
    return filteredVideos.filter(v => v.upload_type === 'live_capture');
  }, [filteredVideos]);

  const regularVideos = useMemo(() => {
    return filteredVideos.filter(v => v.upload_type !== 'live_capture');
  }, [filteredVideos]);

  // Fetch active extension sessions
  const [extensionSessions, setExtensionSessions] = useState([]);
  const cleanupDoneRef = useRef(false);
  useEffect(() => {
    if (!effectiveUser?.isLoggedIn) {
      setExtensionSessions([]);
      return;
    }
    const fetchExtSessions = async () => {
      try {
        // Auto-cleanup stale sessions on first fetch only
        if (!cleanupDoneRef.current) {
          await VideoService.cleanupStaleSessions();
          cleanupDoneRef.current = true;
        }
        const res = await VideoService.getActiveExtensionSessions();
        const data = res?.data || res || {};
        const sessions = data.sessions || [];
        // Filter out sessions that already have a matching live_capture video
        const liveVideoIds = new Set(liveVideos.map(v => v.id));
        const liveVideoAccounts = new Set(liveVideos.map(v => (v.original_filename || '').replace(/^tiktok_live_/, '').replace(/\.mp4$/, '')).filter(Boolean));
        const filtered = sessions.filter(s => {
          // Skip if video_id matches a live_capture video
          if (liveVideoIds.has(s.video_id)) return false;
          // Skip if account matches a live_capture video's account
          if (s.account && liveVideoAccounts.has(s.account)) return false;
          return true;
        });
        // Keep only the single most recent session (1 user = 1 live at a time)
        if (filtered.length <= 1) {
          setExtensionSessions(filtered);
        } else {
          filtered.sort((a, b) => (b.started_at || '').localeCompare(a.started_at || ''));
          setExtensionSessions([filtered[0]]);
        }
      } catch {
        setExtensionSessions([]);
      }
    };
    fetchExtSessions();
    // Refresh every 30 seconds
    const interval = setInterval(fetchExtSessions, 30000);
    return () => clearInterval(interval);
  }, [effectiveUser?.isLoggedIn, refreshKey, liveVideos]);

  // Combine live videos and extension sessions for display
  const hasLiveContent = liveVideos.length > 0 || extensionSessions.length > 0;

  const navigate = useNavigate();
  const location = useLocation();

  const handleVideoClick = (video) => {
    setSelectedVideoId(video.id);
    navigate(`/video/${video.id}`);
    if (onVideoSelect) {
      onVideoSelect(video);
    }
    if (onClose) {
      onClose();
    }
  };

  useEffect(() => {
    if (isOpen) {
      const timer = setTimeout(() => {
        setShowBackButton(true);
      }, 300); // đúng duration sidebar

      return () => clearTimeout(timer);
    } else {
      setShowBackButton(false);
    }
  }, [isOpen]);

  // ── Resume file selection handler for pending_resume tasks ──
  const handleResumeFileClick = (taskId) => {
    setResumingTaskId(taskId);
    if (resumeFileInputRef.current) {
      resumeFileInputRef.current.value = '';
      resumeFileInputRef.current.click();
    }
  };

  const handleResumeFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file || !resumingTaskId) {
      setResumingTaskId(null);
      return;
    }

    const task = bgUploadTasks.find(t => t.id === resumingTaskId);
    if (!task || task.status !== 'pending_resume') {
      setResumingTaskId(null);
      return;
    }

    // Get metadata from IndexedDB
    const metadata = await UploadService.getUploadMetadata(task.uploadId);
    if (!metadata) {
      console.error('[Sidebar] No metadata found for upload:', task.uploadId);
      setResumingTaskId(null);
      return;
    }

    // Validate file matches
    if (file.name !== metadata.fileName || file.size !== metadata.fileSize) {
      alert(
        (window.__t('fileMismatch') || 'ファイルが一致しません') +
        `\n期待: ${metadata.fileName} (${formatSize(metadata.fileSize)})` +
        `\n選択: ${file.name} (${formatSize(file.size)})`
      );
      setResumingTaskId(null);
      return;
    }

    // Cache file handle for uploadService
    UploadService.cacheFileHandle(task.uploadId, file);

    const userEmail = user?.email;
    const analysisLang = i18n.language === 'zh-TW' ? 'zh-TW' : i18n.language === 'en' ? 'en' : 'ja';

    // Calculate startFrom based on uploaded blocks
    const uploadedBlockIds = metadata.uploadedBlocks || [];
    const maxUploadedIndex = uploadedBlockIds.length > 0
      ? Math.max(...uploadedBlockIds.map(id => {
        try { return parseInt(atob(id), 10); } catch { return -1; }
      }))
      : -1;
    const startFrom = maxUploadedIndex + 1;

    backgroundUploadManager.resumePendingTask(
      resumingTaskId,
      file,
      async ({ onProgress, onVideoId }) => {
        await UploadService.uploadToAzure(
          file,
          metadata.uploadUrl,
          task.uploadId,
          (percentage) => onProgress(percentage),
          startFrom
        );

        const video_id = metadata.videoId;
        if (!video_id) throw new Error('Video ID not found in metadata.');
        onVideoId(video_id);

        const uploadMode = metadata.uploadMode || 'screen_recording';
        if (uploadMode === 'clean_video') {
          await UploadService.uploadCompleteWithType(
            userEmail, video_id, file.name, task.uploadId,
            'clean_video',
            metadata.excelProductBlobUrl || null,
            metadata.excelTrendBlobUrl || null,
            analysisLang
          );
        } else {
          await UploadService.uploadComplete(
            userEmail, video_id, file.name, task.uploadId, analysisLang
          );
        }

        await UploadService.clearUploadMetadata(task.uploadId);
        return video_id;
      },
      (videoId) => {
        console.log(`[Sidebar] Resume upload completed: ${videoId}`);
      },
      (err) => {
        console.error('[Sidebar] Resume upload failed:', err);
      }
    );

    setResumingTaskId(null);
  };

  const handleDismissPendingTask = async (taskId) => {
    const task = bgUploadTasks.find(t => t.id === taskId);
    if (!task) return;
    // Clear from backend too if user is logged in
    if (user?.id) {
      try { await UploadService.clearUserUploads(user.id); } catch (e) { console.warn(e); }
    }
    await backgroundUploadManager.dismissPendingTask(taskId, (uploadId) => UploadService.clearUploadMetadata(uploadId));
  };

  const formatSize = (bytes) => {
    if (!bytes) return '';
    if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)}GB`;
    return `${(bytes / (1024 * 1024)).toFixed(0)}MB`;
  };

  return (
    <>
      {/* Hidden file input for resume */}
      <input
        ref={resumeFileInputRef}
        type="file"
        accept="video/*"
        className="hidden"
        onChange={handleResumeFileChange}
      />
      {/* OVERLAY – mobile */}
      <div
        onClick={onClose}
        className={`fixed inset-0 bg-black/40 z-40 md:hidden transition-opacity
        ${isOpen ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"}`}
      />

      {/* SIDEBAR */}
      <aside
        ref={sidebarRef}
        className={`fixed md:static top-0 left-0 z-50 py-4 pl-4 pr-0
        w-full md:min-w-[260px] md:w-[320px] bg-white md:h-screen
        bottom-0
        flex flex-col 
        transition-transform duration-300 ease-in-out
        ${isOpen ? "translate-x-0" : "-translate-x-full"} md:translate-x-0
        md:overflow-y-auto md:scrollbar-custom`}
      >
        {/* ================= PC ================= */}
        <div className="block space-y-3 pr-4 ">
          <div className="flex items-center">
            <img src={logo} className="w-[37px] h-[35px]" />
            <span className="font-semibold text-[22px] ml-2 bg-[linear-gradient(180deg,rgba(69,0,255,1),rgba(155,0,255,1))] bg-clip-text text-transparent">
              Aitherhub
            </span>
          </div>
          <div className="mt-[28px]">
            <div
              onClick={() => {
                setSelectedVideoId(null);
                navigate('/');
                if (onVideoSelect) {
                  onVideoSelect(null);
                }
                if (onNewAnalysis) {
                  onNewAnalysis();
                }
              }}
              className={`flex items-center gap-2 p-2 px-4 border rounded-md cursor-pointer transition-all duration-200 ease-out ${!showFeedback && !selectedVideo
                ? "border-purple-300 bg-purple-50 text-purple-700"
                : "border-gray-200 hover:bg-gray-100"
                }`}
            >
              {/* <img src={write} className="w-[30px] h-[30px]" /> */}
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={!showFeedback && !selectedVideo ? "#7c3aed" : "#213547"} stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-pencil-icon lucide-pencil transition-colors duration-200 ease-out"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" /><path d="m15 5 4 4" /></svg>
              <span className={`text-sm transition-colors duration-200 ease-out ${!showFeedback && !selectedVideo ? "text-purple-700 font-medium" : "text-[#020817]"
                }`}>{window.__t('newAnalysis')}</span>
            </div>
            <div className="relative">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-search absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground"><path d="m21 21-4.34-4.34" /><circle cx="11" cy="11" r="8" /></svg>
              <input
                type="text"
                value={searchValue}
                onChange={(e) => setSearchValue(e.target.value)}
                placeholder={window.__t('searchChat')}
                className="flex h-10 w-full rounded-md mt-2 border px-8 py-2 text-base ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm bg-muted/50 border-border"
              />
              {searchValue.trim() && (
                <X className="w-4 h-4 text-gray-500 hover:text-gray-700 cursor-pointer absolute right-3 top-1/2 -translate-y-1/2" onClick={() => setSearchValue("")} />
              )}
            </div>
            <div
              role="button"
              tabIndex={0}
              onClick={() => {
                if (onShowFeedback) {
                  onShowFeedback();
                }
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  if (onShowFeedback) {
                    onShowFeedback();
                  }
                }
              }}
              className={`flex items-center gap-2 p-2 px-4 mt-2 rounded-md cursor-pointer transition-all duration-200 ease-out ${showFeedback
                ? "border-purple-300 bg-purple-50 text-purple-700"
                : "border-gray-200 hover:bg-gray-100"
                }`}
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={showFeedback ? "#7c3aed" : "#6b7280"} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-message-square-icon lucide-message-square transition-colors duration-200 ease-out"><path d="M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z" /></svg>
              <span className={`text-sm transition-colors duration-200 ease-out ${showFeedback ? "text-purple-700 font-medium" : "text-muted-foreground "
                }`}>{window.__t('feedback')}</span>
            </div>

            {/* ===== Tool Navigation ===== */}
            <div className="mt-3 pt-3 border-t border-gray-100 space-y-1">
              <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider px-2">Tools</span>
              <div
                onClick={() => { navigate('/auto-video'); onClose?.(); }}
                className={`flex items-center gap-2 p-2 px-4 rounded-md cursor-pointer transition-all duration-200 ease-out ${
                  location.pathname === '/auto-video'
                    ? 'border border-purple-300 bg-purple-50 text-purple-700'
                    : 'hover:bg-gray-100'
                }`}
              >
                <Sparkles className={`w-4 h-4 transition-colors duration-200 ease-out ${location.pathname === '/auto-video' ? 'text-purple-600' : 'text-gray-500'}`} />
                <span className={`text-sm transition-colors duration-200 ease-out ${location.pathname === '/auto-video' ? 'text-purple-700 font-medium' : 'text-muted-foreground'}`}>Auto Video</span>
              </div>
              <div
                onClick={() => { navigate('/face-swap'); onClose?.(); }}
                className={`flex items-center gap-2 p-2 px-4 rounded-md cursor-pointer transition-all duration-200 ease-out ${
                  location.pathname === '/face-swap'
                    ? 'border border-purple-300 bg-purple-50 text-purple-700'
                    : 'hover:bg-gray-100'
                }`}
              >
                <Clapperboard className={`w-4 h-4 transition-colors duration-200 ease-out ${location.pathname === '/face-swap' ? 'text-purple-600' : 'text-gray-500'}`} />
                <span className={`text-sm transition-colors duration-200 ease-out ${location.pathname === '/face-swap' ? 'text-purple-700 font-medium' : 'text-muted-foreground'}`}>Face Swap</span>
              </div>
              <div
                onClick={() => { navigate('/digital-human'); onClose?.(); }}
                className={`flex items-center gap-2 p-2 px-4 rounded-md cursor-pointer transition-all duration-200 ease-out ${
                  location.pathname === '/digital-human'
                    ? 'border border-purple-300 bg-purple-50 text-purple-700'
                    : 'hover:bg-gray-100'
                }`}
              >
                <UserCircle className={`w-4 h-4 transition-colors duration-200 ease-out ${location.pathname === '/digital-human' ? 'text-purple-600' : 'text-gray-500'}`} />
                <span className={`text-sm transition-colors duration-200 ease-out ${location.pathname === '/digital-human' ? 'text-purple-700 font-medium' : 'text-muted-foreground'}`}>Digital Human</span>
              </div>
              <div
                onClick={() => { navigate('/ai-live-creator'); onClose?.(); }}
                className={`flex items-center gap-2 p-2 px-4 rounded-md cursor-pointer transition-all duration-200 ease-out ${
                  location.pathname === '/ai-live-creator'
                    ? 'border border-purple-300 bg-purple-50 text-purple-700'
                    : 'hover:bg-gray-100'
                }`}
              >
                <Wand2 className={`w-4 h-4 transition-colors duration-200 ease-out ${location.pathname === '/ai-live-creator' ? 'text-purple-600' : 'text-gray-500'}`} />
                <span className={`text-sm transition-colors duration-200 ease-out ${location.pathname === '/ai-live-creator' ? 'text-purple-700 font-medium' : 'text-muted-foreground'}`}>AI Live Creator</span>
              </div>
              <div
                onClick={() => { navigate('/script-generator'); onClose?.(); }}
                className={`flex items-center gap-2 p-2 px-4 rounded-md cursor-pointer transition-all duration-200 ease-out ${
                  location.pathname === '/script-generator'
                    ? 'border border-orange-300 bg-orange-50 text-orange-700'
                    : 'hover:bg-gray-100'
                }`}
              >
                <FileText className={`w-4 h-4 transition-colors duration-200 ease-out ${location.pathname === '/script-generator' ? 'text-orange-600' : 'text-gray-500'}`} />
                <span className={`text-sm transition-colors duration-200 ease-out ${location.pathname === '/script-generator' ? 'text-orange-700 font-medium' : 'text-muted-foreground'}`}>{window.__t("sidebar_scriptGenerator")}</span>
              </div>
              <div
                onClick={() => { navigate('/personas'); onClose?.(); }}
                className={`flex items-center gap-2 p-2 px-4 rounded-md cursor-pointer transition-all duration-200 ease-out ${
                  location.pathname === '/personas'
                    ? 'border border-pink-300 bg-pink-50 text-pink-700'
                    : 'hover:bg-gray-100'
                }`}
              >
                <Users className={`w-4 h-4 transition-colors duration-200 ease-out ${location.pathname === '/personas' ? 'text-pink-600' : 'text-gray-500'}`} />
                <span className={`text-sm transition-colors duration-200 ease-out ${location.pathname === '/personas' ? 'text-pink-700 font-medium' : 'text-muted-foreground'}`}>Liver Clone</span>
              </div>

            </div>
          </div>
        </div>

        {/* ================= SP ================= */}
        <div className="hidden mt-[22px] px-4 shrink-0">
          {/* <div className="flex justify-between items-center ml-[50px] mb-[20px] gap-2">
            <div className="relative w-full max-w-[270px]">
              <div className="relative p-px rounded-[5px] bg-linear-to-b from-[#4500FF] via-[#6A00FF] to-[#9B00FF]">
                <img src={searchSp} className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  placeholder={window.__t("searchChat")}
                  value={searchValue}
                  onChange={(e) => setSearchValue(e.target.value)}
                  className="
                    w-full h-[40px] rounded-[5px] bg-white text-black pl-[35px] pr-3 outline-none

                    placeholder:text-[#9B00FF]
                    placeholder:font-bold
                    placeholder:text-[14px]

                    placeholder:transition-opacity
                    placeholder:duration-100
                    focus:placeholder:opacity-50
                  "
                />

              </div>
            </div>

            <img src={searchMobile} onClick={() => { setSelectedVideoId(null); if (onVideoSelect) onVideoSelect(null); if (onNewAnalysis) onNewAnalysis(); }} className="w-[32px] cursor-pointer" />
          </div> */}

          <div className="bg-[linear-gradient(180deg,rgba(69,0,255,1),rgba(155,0,255,1))]">
            <div className="bg-white">
              <div className="flex items-center mb-5 mt-1">
                <img src={logo} className="w-10 h-10 ml-2" />
                <span className="ml-2 font-semibold text-[24px] bg-[linear-gradient(180deg,rgba(69,0,255,1),rgba(155,0,255,1))] bg-clip-text text-transparent">
                  Aitherhub
                </span>
              </div>

              <div className="flex items-center">
                <img src={library} className="w-[29px] h-[22px] ml-2" />
                <span className="ml-4 font-semibold text-[24px] bg-[linear-gradient(180deg,rgba(69,0,255,1),rgba(155,0,255,1))] bg-clip-text text-transparent">
                  {window.__t("sidebar_library")}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* ================= COMMON ================= */}
        <div className="mt-6 md:space-y-3 flex flex-col flex-1 min-h-0 pl-4 pr-0 md:px-0">

          {effectiveUser?.isLoggedIn && (
            <>
              <div className="flex-1 min-h-0 flex flex-col">
                {loadingVideos && videos.length === 0 && !videoListError ? (
                  <div className="flex items-center justify-center py-4">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-gray-400"></div>
                  </div>
                ) : filteredVideos.length > 0 ? (
                  <div className="flex flex-col items-start gap-1 flex-1 min-h-0 overflow-y-auto scrollbar-custom">
                    {loadingVideos && (
                      <div className="w-full flex justify-center py-1">
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-400"></div>
                      </div>
                    )}

                    {/* ── Live Analysis Section ── */}
                    {hasLiveContent && (
                      <>
                        <div className="flex items-center gap-2 w-full px-1 pt-1 pb-0.5 shrink-0">
                          <Radio className="w-3.5 h-3.5 text-red-500" />
                          <span className="text-xs font-semibold text-gray-500">{window.__t("sidebar_liveAnalysis")}</span>
                          <span className="ml-auto inline-flex items-center gap-1 bg-red-500 text-white text-[9px] font-bold px-1.5 py-0.5 rounded-full leading-none">
                            <span className="relative flex h-1.5 w-1.5"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-75"></span><span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-white"></span></span>
                            LIVE
                          </span>
                        </div>
                        {liveVideos.map((video) => (
                          <div className={`group relative w-full min-h-10 flex items-center gap-2 font-semibold cursor-pointer text-black p-2 rounded-lg text-left transition-all duration-200 ease-out ${selectedVideoId === video.id
                            ? "bg-red-50 text-red-700 border border-red-200"
                            : "hover:text-gray-400 hover:bg-red-50/50"
                            }`} key={video.id}
                            onClick={() => { if (renamingVideoId !== video.id && deleteConfirmVideoId !== video.id) handleVideoClick(video); }}>
                            <Radio className="min-w-[16px] w-4 h-4 text-red-400" />

                            {renamingVideoId === video.id ? (
                              <input autoFocus value={renameValue} onChange={(e) => setRenameValue(e.target.value)}
                                onKeyDown={(e) => { if (e.key === "Enter") handleRenameVideo(video.id); if (e.key === "Escape") { setRenamingVideoId(null); setRenameValue(""); } }}
                                onBlur={() => handleRenameVideo(video.id)} onClick={(e) => e.stopPropagation()}
                                className="text-sm font-medium text-gray-700 bg-white border border-red-300 rounded px-1 py-0.5 outline-none focus:ring-2 focus:ring-red-400 w-full" />
                            ) : deleteConfirmVideoId === video.id ? (
                              <div className="flex items-center gap-2 w-full" onClick={(e) => e.stopPropagation()}>
                                <span className="text-xs text-red-500">{window.__t("sidebar_deleteConfirm")}</span>
                                <button onClick={() => handleDeleteVideo(video.id)} className="text-xs bg-red-500 text-white px-2 py-0.5 rounded hover:bg-red-600">{window.__t("common_delete")}</button>
                                <button onClick={() => setDeleteConfirmVideoId(null)} className="text-xs bg-gray-200 text-gray-700 px-2 py-0.5 rounded hover:bg-gray-300">{window.__t("sidebar_cancel")}</button>
                              </div>
                            ) : (
                              <>
                                <div className="flex flex-col flex-1 min-w-0">
                                  <div className="flex items-center gap-1.5">
                                    <span className="text-sm font-medium text-[#6b7280] block truncate">
                                      {video.original_filename || `${window.__t('videoTitleFallback')} ${video.id}`}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                                    {video.status === 'capturing' && (
                                      <button
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          navigate(`/live/${video.id}`);
                                          if (onClose) onClose();
                                        }}
                                        className="inline-flex items-center gap-1 text-[10px] font-semibold text-white bg-red-500 hover:bg-red-600 px-2 py-0.5 rounded-full transition-colors animate-pulse"
                                      >
                                        <Eye className="w-3 h-3" />
                                        {window.__t("sidebar_watchLive")}
                                      </button>
                                    )}
                                    {video.stream_duration != null && video.stream_duration > 0 && (
                                      <span className="inline-flex items-center gap-0.5 text-[10px] text-blue-500">
                                        <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                                        {(() => { const h = Math.floor(video.stream_duration / 3600); const m = Math.floor((video.stream_duration % 3600) / 60); return h > 0 ? `${h}h${m.toString().padStart(2,'0')}m` : `${m}m`; })()}
                                      </span>
                                    )}
                                    {video.total_gmv != null && video.total_gmv > 0 && (
                                      <span className="inline-flex items-center gap-0.5 text-[10px] text-orange-600">
                                        <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                                        {video.total_gmv >= 10000 ? `¥${(video.total_gmv / 10000).toFixed(1)}${window.__t('tenThousand', '万')}` : `¥${Math.round(video.total_gmv).toLocaleString()}`}
                                      </span>
                                    )}
                                  </div>
                                </div>
                                <div className="relative" ref={menuOpenVideoId === video.id ? menuRef : null}>
                                  <button onClick={(e) => { e.stopPropagation(); setMenuOpenVideoId(menuOpenVideoId === video.id ? null : video.id); }}
                                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-red-100">
                                    <MoreHorizontal className="w-4 h-4 text-gray-500" />
                                  </button>
                                  {menuOpenVideoId === video.id && (
                                    <div className="absolute right-0 top-8 z-50 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[140px]">
                                      <button onClick={(e) => { e.stopPropagation(); setRenamingVideoId(video.id); setRenameValue(video.original_filename || ""); setMenuOpenVideoId(null); }}
                                        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-100">
                                        <Pencil className="w-3.5 h-3.5" /> {window.__t("sidebar_rename")}
                                      </button>
                                      <button onClick={(e) => { e.stopPropagation(); setDeleteConfirmVideoId(video.id); setMenuOpenVideoId(null); }}
                                        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-500 hover:bg-red-50">
                                        <Trash2 className="w-3.5 h-3.5" /> {window.__t("common_delete")}
                                      </button>
                                    </div>
                                  )}
                                </div>
                              </>
                            )}
                          </div>
                        ))}
                        {/* Extension sessions */}
                        {extensionSessions.map((session) => (
                          <div className={`group relative w-full min-h-10 flex items-center gap-2 font-semibold cursor-pointer text-black p-2 rounded-lg text-left transition-all duration-200 ease-out ${
                            selectedVideoId === session.video_id
                              ? "bg-red-50 text-red-700 border border-red-200"
                              : "hover:text-gray-400 hover:bg-red-50/50"
                          }`} key={session.video_id}
                            onClick={() => {
                              setSelectedVideoId(session.video_id);
                              navigate(`/live/${session.video_id}`);
                              if (onClose) onClose();
                            }}>
                            <Radio className="min-w-[16px] w-4 h-4 text-red-400" />
                            <div className="flex flex-col flex-1 min-w-0">
                              <div className="flex items-center gap-1.5">
                                <span className="text-sm font-medium text-[#6b7280] block truncate">
                                  {session.account ? `@${session.account}` : `Extension ${session.video_id}`}
                                </span>
                              </div>
                              <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    navigate(`/live/${session.video_id}`);
                                    if (onClose) onClose();
                                  }}
                                  className="inline-flex items-center gap-1 text-[10px] font-semibold text-white bg-red-500 hover:bg-red-600 px-2 py-0.5 rounded-full transition-colors animate-pulse"
                                >
                                  <Eye className="w-3 h-3" />
                                  {window.__t("sidebar_watchLive")}
                                </button>
                                <span className="inline-flex items-center gap-0.5 text-[10px] text-green-600">
                                  <span className="relative flex h-1.5 w-1.5"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span><span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-green-500"></span></span>
                                  {window.__t("sidebar_chromeExtension")}
                                </span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </>
                    )}

                    {/* ── Separator between sections ── */}
                    {hasLiveContent && regularVideos.length > 0 && (
                      <div className="w-full border-t border-gray-200 my-1"></div>
                    )}

                    {/* ── Background Upload Progress ── */}
                    {bgUploadTasks.length > 0 && (
                      <>
                        <div className="flex items-center gap-2 w-full px-1 pt-2 pb-1 shrink-0">
                          <Upload className="w-3.5 h-3.5 text-blue-400" />
                          <span className="text-xs font-semibold text-blue-500">{window.__t('sidebar_uploading') || 'アップロード'}</span>
                          <span className="text-[10px] text-blue-400 bg-blue-50 px-1.5 py-0.5 rounded-full font-medium">{bgUploadTasks.length}</span>
                        </div>
                        {bgUploadTasks.map((task) => (
                          <div key={task.id} className={`w-full px-2.5 py-2.5 rounded-lg mb-1 ${
                            task.status === 'pending_resume'
                              ? 'border border-amber-200 bg-amber-50/50'
                              : 'border border-blue-100 bg-blue-50/50'
                          }`}>
                            <div className="flex items-center gap-2 min-w-0">
                              {(task.status === 'uploading' || task.status === 'retrying') && (
                                <svg className="w-3.5 h-3.5 flex-shrink-0 animate-spin text-blue-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                              )}
                              {task.status === 'done' && <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0 text-green-500" />}
                              {task.status === 'error' && <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 text-red-500" />}
                              {task.status === 'pending_resume' && <FileUp className="w-3.5 h-3.5 flex-shrink-0 text-amber-500" />}
                              <span className="text-[12px] font-medium text-gray-700 truncate flex-1" title={task.fileName}>
                                {task.fileName}
                              </span>
                            </div>
                            {task.status === 'pending_resume' && (
                              <div className="mt-1.5">
                                <div className="w-full h-1.5 bg-amber-100 rounded-full overflow-hidden">
                                  <div
                                    className="h-full bg-gradient-to-r from-amber-300 to-amber-500 rounded-full"
                                    style={{ width: `${task.progress}%` }}
                                  />
                                </div>
                                <div className="flex justify-between items-center mt-1">
                                  <span className="text-[10px] text-amber-600 font-medium">
                                    {window.__t('sidebar_interrupted') || '中断されました'} ({Math.round(task.progress)}%)
                                  </span>
                                  <span className="text-[10px] text-gray-400">
                                    {task.fileSize ? (task.fileSize / (1024*1024) >= 1024 ? `${(task.fileSize / (1024*1024*1024)).toFixed(1)}GB` : `${(task.fileSize / (1024*1024)).toFixed(0)}MB`) : ''}
                                  </span>
                                </div>
                                <div className="mt-1.5 flex items-center gap-1.5">
                                  <button
                                    className="text-[10px] px-2 py-1 rounded bg-amber-500 text-white hover:bg-amber-600 transition-colors flex items-center gap-1"
                                    onClick={(e) => { e.stopPropagation(); handleResumeFileClick(task.id); }}
                                    title={window.__t('sidebar_resumeUpload') || 'ファイルを再選択して再開'}
                                  >
                                    <RotateCcw className="w-3 h-3" />
                                    {window.__t('sidebar_resume') || '再開'}
                                  </button>
                                  <button
                                    className="text-[10px] px-2 py-1 rounded bg-gray-200 text-gray-600 hover:bg-gray-300 transition-colors"
                                    onClick={(e) => { e.stopPropagation(); handleDismissPendingTask(task.id); }}
                                  >
                                    {window.__t('sidebar_dismiss') || '破棄'}
                                  </button>
                                </div>
                              </div>
                            )}
                            {task.status === 'retrying' && (
                              <div className="mt-1 flex items-center gap-1.5">
                                <span className="text-[10px] text-amber-600 truncate flex-1">
                                  {window.__t('sidebar_retrying') || `リトライ中... (${task.retryCount}/3)`}
                                </span>
                              </div>
                            )}
                            {task.status === 'uploading' && (
                              <div className="mt-1.5">
                                <div className="w-full h-1.5 bg-blue-100 rounded-full overflow-hidden">
                                  <div
                                    className="h-full bg-gradient-to-r from-blue-400 to-blue-600 rounded-full transition-all duration-300 ease-out"
                                    style={{ width: `${task.progress}%` }}
                                  />
                                </div>
                                <div className="flex justify-between items-center mt-1">
                                  <span className="text-[10px] text-blue-500 font-medium">{Math.round(task.progress)}%</span>
                                  <span className="text-[10px] text-gray-400">
                                    {task.fileSize ? (task.fileSize / (1024*1024) >= 1024 ? `${(task.fileSize / (1024*1024*1024)).toFixed(1)}GB` : `${(task.fileSize / (1024*1024)).toFixed(0)}MB`) : ''}
                                  </span>
                                </div>
                              </div>
                            )}
                            {task.status === 'done' && (
                              <span className="text-[10px] text-green-600 mt-1 block">{window.__t('sidebar_uploadDone') || '解析開始済み'}</span>
                            )}
                            {task.status === 'error' && (
                              <div className="mt-1 flex items-center gap-1.5">
                                <span className="text-[10px] text-red-500 truncate flex-1" title={task.error}>{task.error || 'エラー'}</span>
                                <button
                                  className="text-[9px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-600 hover:bg-blue-200 transition-colors flex-shrink-0"
                                  onClick={(e) => { e.stopPropagation(); backgroundUploadManager.retryTask(task.id); }}
                                  title="Retry"
                                >
                                  ↻
                                </button>
                                <button
                                  className="text-[9px] px-1.5 py-0.5 rounded bg-red-100 text-red-600 hover:bg-red-200 transition-colors flex-shrink-0"
                                  onClick={(e) => { e.stopPropagation(); backgroundUploadManager.removeTask(task.id); }}
                                >
                                  ✕
                                </button>
                              </div>
                            )}
                          </div>
                        ))}
                        {regularVideos.length > 0 && (
                          <div className="w-full border-t border-gray-200 my-1"></div>
                        )}
                      </>
                    )}

                    {/* ── Video Analysis Section ── */}
                    {regularVideos.length > 0 && (
                      <>
                        <div className="flex items-center gap-2 w-full px-1 pt-2 pb-1 shrink-0">
                          <Video className="w-3.5 h-3.5 text-gray-400" />
                          <span className="text-xs font-semibold text-gray-500">{window.__t('analysisHistory')}</span>
                        </div>
                        {regularVideos.map((video) => (
                          <div className={`group relative w-full flex items-start gap-2.5 cursor-pointer px-2.5 py-3 rounded-lg text-left transition-all duration-200 ease-out border border-transparent ${selectedVideoId === video.id
                            ? "bg-purple-50 border-purple-200"
                            : "hover:bg-gray-50"
                            }`} key={video.id}
                            onClick={() => { if (renamingVideoId !== video.id && deleteConfirmVideoId !== video.id) handleVideoClick(video); }}>
                            <svg xmlns="http://www.w3.org/2000/svg" className="min-w-[16px] mt-0.5 flex-shrink-0" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5" /><rect x="2" y="6" width="14" height="12" rx="2" /></svg>

                            {renamingVideoId === video.id ? (
                              <input autoFocus value={renameValue} onChange={(e) => setRenameValue(e.target.value)}
                                onKeyDown={(e) => { if (e.key === "Enter") handleRenameVideo(video.id); if (e.key === "Escape") { setRenamingVideoId(null); setRenameValue(""); } }}
                                onBlur={() => handleRenameVideo(video.id)} onClick={(e) => e.stopPropagation()}
                                className="text-sm font-medium text-gray-700 bg-white border border-purple-300 rounded px-1 py-0.5 outline-none focus:ring-2 focus:ring-purple-400 w-full" />
                            ) : deleteConfirmVideoId === video.id ? (
                              <div className="flex items-center gap-2 w-full" onClick={(e) => e.stopPropagation()}>
                                <span className="text-xs text-red-500">{window.__t("sidebar_deleteConfirm")}</span>
                                <button onClick={() => handleDeleteVideo(video.id)} className="text-xs bg-red-500 text-white px-2 py-0.5 rounded hover:bg-red-600">{window.__t("common_delete")}</button>
                                <button onClick={() => setDeleteConfirmVideoId(null)} className="text-xs bg-gray-200 text-gray-700 px-2 py-0.5 rounded hover:bg-gray-300">{window.__t("sidebar_cancel")}</button>
                              </div>
                            ) : (
                              <>
                                <div className="flex flex-col flex-1 min-w-0 gap-1.5">
                                  <span className="text-[13px] font-medium text-gray-700 leading-snug truncate" title={video.original_filename}>
                                    {video.original_filename || `${window.__t('videoTitleFallback')} ${video.id}`}
                                  </span>
                                  {/* Status badge for non-DONE videos */}
                                  {video.status && video.status !== 'DONE' && (
                                    <div className="flex items-center gap-1.5">
                                      {video.status === 'ERROR' && video.stream_duration ? (
                                        <span className="inline-flex items-center gap-1 text-[11px] text-amber-500 font-medium leading-normal">
                                          <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                                          {window.__t("sidebar_partialIncomplete")}
                                        </span>
                                      ) : video.status === 'ERROR' ? (
                                        <span className="inline-flex items-center gap-1 text-[11px] text-red-500 font-medium leading-normal">
                                          <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                                          {window.__t("common_error")}
                                        </span>
                                      ) : (
                                        <span className="inline-flex items-center gap-1 text-[11px] text-amber-600 font-medium leading-normal">
                                          <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 flex-shrink-0 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                                          {video.status === 'QUEUED' ? [window.__t('sidebar_queued', 'キュー待ち')] : video.status === 'UPLOADED' ? [window.__t('sidebar_uploaded', 'アップロード済み')] : video.status.startsWith('STEP_') ? `解析中 ${(() => { const m = { STEP_COMPRESS_1080P: 1, STEP_0_EXTRACT_FRAMES: 5, STEP_1_DETECT_PHASES: 10, STEP_2_EXTRACT_METRICS: 20, STEP_3_TRANSCRIBE_AUDIO: 55, STEP_4_IMAGE_CAPTION: 70, STEP_5_BUILD_PHASE_UNITS: 80, STEP_6_BUILD_PHASE_DESCRIPTION: 85, STEP_7_GROUPING: 90, STEP_8_UPDATE_BEST_PHASE: 92, STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES: 94, STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP: 95, STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS: 96, STEP_12_UPDATE_VIDEO_STRUCTURE_BEST: 97, STEP_12_5_PRODUCT_DETECTION: 98, STEP_13_BUILD_REPORTS: 98, STEP_14_FINALIZE: 99, STEP_14_SPLIT_VIDEO: 99 }; return m[video.status] || 0; })()}%` : video.status}
                                        </span>
                                      )}
                                      {video.updated_at && (() => {
                                        const updated = new Date(video.updated_at);
                                        const now = new Date();
                                        const diffMin = Math.floor((now - updated) / 60000);
                                        if (diffMin < 1) return null;
                                        const diffH = Math.floor(diffMin / 60);
                                        const diffD = Math.floor(diffH / 24);
                                        const elapsed = diffD > 0 ? `${diffD}${window.__t('common_days', '日')}${diffH % 24}${window.__t('script_duration', '時間')}` : diffH > 0 ? `${diffH}${window.__t('script_duration', '時間')}${diffMin % 60}${window.__t('common_minutes', '分')}` : `${diffMin}${window.__t('common_minutes', '分')}`;
                                        return (
                                          <span className={`text-[10px] leading-normal ${diffMin > 30 ? 'text-red-400' : 'text-gray-400'}`}>
                                            ({elapsed}経過)
                                          </span>
                                        );
                                      })()}
                                    </div>
                                  )}
                                  {video.top_products && video.top_products.length > 0 && (
                                    <div className="flex items-center gap-1.5">
                                      <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 text-emerald-500 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
                                      <span className="text-[11px] text-emerald-600 truncate leading-normal" title={video.top_products.join(' / ')}>
                                        {video.top_products[0] ? (video.top_products[0].length > 20 ? video.top_products[0].slice(0, 20) + '...' : video.top_products[0]) : ''}
                                      </span>
                                    </div>
                                  )}
                                  <div className="flex items-center gap-3 flex-wrap">
                                    {video.created_at && (
                                      <span className="inline-flex items-center gap-1 text-[11px] text-gray-400 leading-normal">
                                        <Calendar className="w-3 h-3 flex-shrink-0" />
                                        {(() => {
                                          const d = new Date(video.created_at);
                                          const now = new Date();
                                          const diffMs = now - d;
                                          const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
                                          if (diffDays === 0) return window.__t('sidebar_today', '今日');
                                          if (diffDays === 1) return window.__t('sidebar_yesterday', '昨日');
                                          if (diffDays < 7) return `${diffDays}${window.__t('sidebar_daysAgo', '日前')}`;
                                          return `${(d.getMonth() + 1)}/${d.getDate()}`;
                                        })()}
                                      </span>
                                    )}
                                    {video.total_gmv != null && video.total_gmv > 0 && (
                                      <span className="inline-flex items-center gap-1 text-[11px] text-orange-600 font-medium leading-normal">
                                        <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                                        {video.total_gmv >= 10000 ? `¥${(video.total_gmv / 10000).toFixed(1)}${window.__t('tenThousand', '万')}` : `¥${Math.round(video.total_gmv).toLocaleString()}`}
                                      </span>
                                    )}
                                    {video.stream_duration != null && video.stream_duration > 0 && (
                                      <span className="inline-flex items-center gap-1 text-[11px] text-blue-500 leading-normal">
                                        <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                                        {(() => { const h = Math.floor(video.stream_duration / 3600); const m = Math.floor((video.stream_duration % 3600) / 60); return h > 0 ? `${h}h${m.toString().padStart(2,'0')}m` : `${m}m`; })()}
                                      </span>
                                    )}
                                    {video.completed_clip_count > 0 && (
                                      <span className="inline-flex items-center gap-1 text-[11px] text-purple-600 leading-normal">
                                        <Scissors className="w-3 h-3 flex-shrink-0" />
                                        {video.completed_clip_count}
                                      </span>
                                    )}
                                    {video.memo_count > 0 && (
                                      <span className="inline-flex items-center gap-1 text-[11px] text-green-600 leading-normal">
                                        <MessageSquareText className="w-3 h-3 flex-shrink-0" />
                                        {video.memo_count}
                                      </span>
                                    )}
                                    {/* Persona tag badges */}
                                    {videoPersonaTags[video.id] && videoPersonaTags[video.id].map(pid => (
                                      <span key={pid} className="inline-flex items-center gap-1 text-[10px] text-fuchsia-600 bg-fuchsia-50 px-1.5 py-0.5 rounded-full font-medium leading-normal">
                                        <Brain className="w-2.5 h-2.5 flex-shrink-0" />
                                        {getPersonaName(pid)}
                                      </span>
                                    ))}
                                  </div>
                                </div>
                                <div className="relative" ref={menuOpenVideoId === video.id ? menuRef : null}>
                                  <button onClick={(e) => { e.stopPropagation(); setMenuOpenVideoId(menuOpenVideoId === video.id ? null : video.id); setPersonaMenuVideoId(null); }}
                                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-gray-200">
                                    <MoreHorizontal className="w-4 h-4 text-gray-500" />
                                  </button>
                                  {menuOpenVideoId === video.id && (
                                    <div className="absolute right-0 top-8 z-50 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[180px]">
                                      {/* Persona tagging */}
                                      {personas.length > 0 && (
                                        <>
                                          <div className="px-3 py-1.5 text-[10px] font-semibold text-gray-400 uppercase tracking-wider">{window.__t("sidebar_liverAssignment")}</div>
                                          {personas.map(p => {
                                            const isTagged = (videoPersonaTags[video.id] || []).includes(p.id);
                                            return (
                                              <button key={p.id} onClick={(e) => { e.stopPropagation(); handleTogglePersonaTag(video.id, p.id); }}
                                                className={`w-full flex items-center gap-2 px-3 py-2 text-sm transition-colors ${
                                                  isTagged ? 'text-fuchsia-600 bg-fuchsia-50 hover:bg-fuchsia-100' : 'text-gray-700 hover:bg-gray-100'
                                                }`}
                                                disabled={taggingInProgress}>
                                                {isTagged ? <Check className="w-3.5 h-3.5" /> : <Brain className="w-3.5 h-3.5 text-gray-400" />}
                                                {p.name}
                                                {taggingInProgress && <span className="ml-auto text-[10px] text-gray-400">...</span>}
                                              </button>
                                            );
                                          })}
                                          <div className="border-t border-gray-100 my-1"></div>
                                        </>
                                      )}
                                      <button onClick={(e) => { e.stopPropagation(); setRenamingVideoId(video.id); setRenameValue(video.original_filename || ""); setMenuOpenVideoId(null); }}
                                        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-100">
                                        <Pencil className="w-3.5 h-3.5" /> {window.__t("sidebar_rename")}
                                      </button>
                                      <button onClick={(e) => { e.stopPropagation(); setDeleteConfirmVideoId(video.id); setMenuOpenVideoId(null); }}
                                        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-500 hover:bg-red-50">
                                        <Trash2 className="w-3.5 h-3.5" /> {window.__t("common_delete")}
                                      </button>
                                    </div>
                                  )}
                                </div>
                              </>
                            )}
                          </div>
                        ))}
                      </>
                    )}
                  </div>
                ) : videoListError === 'auth' ? (
                  /* Auth error state */
                  <div className="flex flex-col items-center gap-2 py-6 px-4">
                    <div className="w-10 h-10 rounded-full bg-amber-50 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-amber-500">
                        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                      </svg>
                    </div>
                    <span className="text-xs text-amber-600 font-medium text-center">
                      {window.__t("videoListAuthError") || "Please check your login status"}
                    </span>
                  </div>
                ) : videoListError === 'error' ? (
                  /* API error state */
                  <div className="flex flex-col items-center gap-2 py-6 px-4">
                    <div className="w-10 h-10 rounded-full bg-red-50 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-red-400">
                        <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
                      </svg>
                    </div>
                    <span className="text-xs text-red-500 font-medium text-center">
                      {window.__t("videoListError") || "Failed to load video list"}
                    </span>
                    <button
                      onClick={() => doFetchVideos()}
                      className="px-3 py-1.5 text-xs font-medium text-white bg-red-500 hover:bg-red-600 rounded-lg transition-colors"
                    >
                      {window.__t("videoListRetry") || "Retry"}
                    </button>
                  </div>
                ) : (
                  /* Genuinely empty state */
                  <div className="flex flex-col items-center gap-3 py-8 px-4">
                    <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-gray-300">
                        <polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
                      </svg>
                    </div>
                    <span className="text-xs text-gray-400 text-center">
                      {videos.length > 0 && searchValue.trim()
                        ? (window.__t("noSearchResults") || "No results")
                        : (window.__t("noVideos") || "No videos yet")}
                    </span>
                    {!searchValue.trim() && (
                      <button
                        onClick={onNewAnalysis}
                        className="px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors"
                      >
                        {window.__t("newAnalysis") || "New analysis"}
                      </button>
                    )}
                  </div>
                )}
              </div>

              {/* ===== Email pill (SP) ===== */}
              <div className="ml-[7px] mb-[25px] mt-auto md:hidden shrink-0">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      className="w-[225px] h-[45px] rounded-[50px] border border-[#B5B5B5] flex items-center justify-center shadow cursor-pointer transition-colors hover:bg-gray-100 active:bg-gray-100"
                    >
                      <span className="font-bold text-sm max-w-[165px] truncate inline-block align-middle text-gray-700">
                        {effectiveUser.email}
                      </span>
                      <ChevronDown className="ml-1 w-4 h-4 text-gray-500" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-[210px]">
                    <DropdownMenuLabel>{window.__t("myAccount")}</DropdownMenuLabel>
                    <DropdownMenuSeparator />

                    <DropdownMenuItem
                      onSelect={() => {
                        onClose?.();
                      }}
                    >
                      <User className="w-4 h-4" />
                      {window.__t("myAccount")}
                    </DropdownMenuItem>

                    <DropdownMenuItem
                      onSelect={() => {
                        setOpenForgotPassword(true);
                        // Close sidebar after opening modal to avoid unmount/blur race on mobile
                        setTimeout(() => onClose?.(), 0);
                      }}
                    >
                      <Settings className="w-4 h-4" />
                      {t("changePassword")}
                    </DropdownMenuItem>

                    <DropdownMenuSub>
                      <DropdownMenuSubTrigger>
                        <Globe className="w-4 h-4" />
                        {t("language_settings")}
                      </DropdownMenuSubTrigger>
                      <DropdownMenuSubContent>
                        <DropdownMenuItem
                          onSelect={() => changeLanguage('ja')}
                          className={i18n.language === 'ja' ? 'bg-accent' : ''}
                        >
                          {i18n.language === 'ja' && <Check className="w-3 h-3 mr-1" />}
                          🇯🇵 日本語
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => changeLanguage('zh-TW')}
                          className={i18n.language === 'zh-TW' ? 'bg-accent' : ''}
                        >
                          {i18n.language === 'zh-TW' && <Check className="w-3 h-3 mr-1" />}
                          🇹🇼 繁體中文
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => changeLanguage('en')}
                          className={i18n.language === 'en' ? 'bg-accent' : ''}
                        >
                          {i18n.language === 'en' && <Check className="w-3 h-3 mr-1" />}
                          🇺🇸 English
                        </DropdownMenuItem>
                      </DropdownMenuSubContent>
                    </DropdownMenuSub>

                    <DropdownMenuSeparator />

                    <DropdownMenuItem
                      className="text-red-500 focus:text-red-600"
                      onSelect={() => {
                        onClose?.();
                        AuthService.logout();
                        window.location.reload();
                      }}
                    >
                      <LogOut className="w-4 h-4" />
                      {window.__t("signOut")}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </>
          )}
        </div>
      </aside>
      <button
        onClick={onClose}
        style={{ fontSize: "24px", borderRadius: "50%" }}
        className={`md:hidden ml-[-10px] fixed top-[16px] right-[16px] z-70 w-[32px] h-[32px] flex items-center justify-center font-bold bg-white rounded-full shadow-lg transition-all duration-200 ease-out ${showBackButton ? "opacity-100 translate-x-0 pointer-events-auto" : "opacity-0 translate-x-2 pointer-events-none"}`}
      >
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-6">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" stroke="#4500FF" />
        </svg>

      </button>

      {/* ===== MODAL (must be outside dropdown content to avoid unmount when menu closes) ===== */}
      <ForgotPasswordModal open={openForgotPassword} onOpenChange={setOpenForgotPassword} />

    </>
  );
}
