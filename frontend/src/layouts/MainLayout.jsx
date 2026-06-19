import Sidebar from "../components/Sidebar";
import MainContent from '../components/MainContent';
import OnboardingModal from '../components/OnboardingModal';
import { useState, useCallback, useMemo, useEffect } from "react";
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import BaseApiService from '../base/api/BaseApiService';

const API_BASE = import.meta.env.VITE_API_BASE_URL;

const getUserFromStorage = () => {
  try {
    const stored = localStorage.getItem("user");
    return stored ? JSON.parse(stored) : { isLoggedIn: false };
  } catch {
    return { isLoggedIn: false };
  }
};

export default function MainLayout() {
  const { videoId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [openSidebar, setOpenSidebar] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);

  // Extract editor params from URL (for feedback card → editor navigation)
  const editorParams = useMemo(() => {
    if (searchParams.get('open_editor') === '1') {
      const rawPhase = searchParams.get('phase');
      // phase can be numeric ("5") or string ("moment_strong_5") — preserve as-is if non-numeric
      let phaseIndex = null;
      if (rawPhase) {
        const num = Number(rawPhase);
        phaseIndex = isNaN(num) ? rawPhase : num;
      }
      return {
        phase_index: phaseIndex,
        time_start: searchParams.get('t_start') ? Number(searchParams.get('t_start')) : null,
        time_end: searchParams.get('t_end') ? Number(searchParams.get('t_end')) : null,
      };
    }
    return null;
  }, [searchParams]);
  const [selectedVideoId, setSelectedVideoId] = useState(videoId || null);
  const [user, setUser] = useState(getUserFromStorage);
  const [refreshKey, setRefreshKey] = useState(0);
  const [showFeedback, setShowFeedback] = useState(false);

  // Check if onboarding is needed (only once on mount)
  useEffect(() => {
    const checkOnboarding = async () => {
      try {
        // Skip if already dismissed (persisted across sessions)
        if (localStorage.getItem('onboarding_completed')) return;
        // Also check legacy sessionStorage flag
        if (sessionStorage.getItem('onboarding_dismissed')) {
          // Migrate to localStorage for persistence
          localStorage.setItem('onboarding_completed', '1');
          return;
        }
        // Skip if not logged in (no token = no profile to check)
        const token = localStorage.getItem('app_access_token');
        if (!token) return;
        const api = new BaseApiService(API_BASE);
        const profile = await api.get('/api/v1/profile/me', { noCache: true });
        if (profile && !profile.onboarding_completed) {
          setShowOnboarding(true);
        } else if (profile && profile.onboarding_completed) {
          // Sync local flag so we never check again
          localStorage.setItem('onboarding_completed', '1');
        }
      } catch (err) {
        // If API fails (401, network), don't show onboarding
        console.debug('[MainLayout] onboarding check skipped:', err?.message);
      }
    };
    checkOnboarding();
  }, []);

  const handleOnboardingComplete = useCallback(() => {
    setShowOnboarding(false);
    // Persist across sessions so popup never reappears
    localStorage.setItem('onboarding_completed', '1');
    sessionStorage.setItem('onboarding_dismissed', '1'); // legacy compat
  }, []);

  // Sync selectedVideoId when URL param changes
  useEffect(() => {
    if (videoId) {
      setSelectedVideoId(videoId);
      setShowFeedback(false);
    } else {
      setSelectedVideoId(null);
    }
  }, [videoId]);
  useEffect(() => {
    let scrollY;
    if (openSidebar) {
      scrollY = window.scrollY;

      Object.assign(document.body.style, {
        position: "fixed",
        top: `-${scrollY}px`,
        left: "0",
        right: "0",
        width: "100%",
        overflow: "hidden",
      });

      return () => {
        Object.assign(document.body.style, {
          position: "",
          top: "",
          left: "",
          right: "",
          width: "",
          overflow: "",
        });
        window.scrollTo(0, scrollY);
      };
    }
  }, [openSidebar]);

  const handleVideoSelect = useCallback((video) => {
    setShowFeedback(false);
    if (video?.id) {
      setSelectedVideoId(video.id);
      navigate(`/video/${video.id}`);
    }
    setOpenSidebar(false);
  }, [navigate]);

  const handleUserChange = useCallback((newUser) => {
    setUser(newUser);
    if (!newUser?.isLoggedIn) {
      setSelectedVideoId(null);
    }
  }, []);

  const handleCloseSidebar = useCallback(() => {
    setOpenSidebar(false);
  }, []);

  const handleOpenSidebar = useCallback(() => {
    setOpenSidebar(true);
  }, []);

  const handleNewAnalysis = useCallback(() => {
    setShowFeedback(false);
    setSelectedVideoId(null);
    navigate('/');
    setOpenSidebar(false);
  }, [navigate]);
  const handleShowFeedback = useCallback(() => {
    setShowFeedback(true);
    setSelectedVideoId(null);
    setOpenSidebar(false);
  }, []);

  const handleCloseFeedback = useCallback(() => {
    setShowFeedback(false);
  }, []);

  const handleUploadSuccess = useCallback((videoId) => {
    setRefreshKey(prev => prev + 1);
    if (videoId) {
      setShowFeedback(false);
      setSelectedVideoId(videoId);
      navigate(`/video/${videoId}`);
    }
  }, [navigate]);

  const sidebarProps = useMemo(() => ({
    isOpen: openSidebar,
    onClose: handleCloseSidebar,
    user,
    onVideoSelect: handleVideoSelect,
    onNewAnalysis: handleNewAnalysis,
    onShowFeedback: handleShowFeedback,
    onCloseFeedback: handleCloseFeedback,
    refreshKey,
    showFeedback,
    selectedVideo: selectedVideoId ? { id: selectedVideoId } : null,
  }), [openSidebar, handleCloseSidebar, user, handleVideoSelect, handleNewAnalysis, handleShowFeedback, handleCloseFeedback, refreshKey, selectedVideoId, showFeedback]);

  const mainContentProps = useMemo(() => ({
    onOpenSidebar: handleOpenSidebar,
    user,
    setUser: handleUserChange,
    onUploadSuccess: handleUploadSuccess,
    selectedVideoId,
    showFeedback,
    onCloseFeedback: handleCloseFeedback,
    editorParams,
  }), [handleOpenSidebar, user, handleUserChange, handleUploadSuccess, selectedVideoId, showFeedback, handleCloseFeedback, editorParams]);

  return (
    <div className="min-h-screen bg-gray-100 flex">
      <div className="w-full max-w-[1600px] mx-auto flex">

        <aside className="hidden xl:block w-[320px] max-w-[320px] min-w-[320px] bg-white text-black">
          <Sidebar {...sidebarProps} />
        </aside>

        <div className="xl:hidden">
          <Sidebar {...sidebarProps} />
        </div>

        <main className="w-full md:flex-1 bg-white text-gray-900">
          <MainContent {...mainContentProps}>
          </MainContent>
        </main>
      </div>

      {/* Onboarding Modal */}
      <OnboardingModal
        isOpen={showOnboarding}
        onComplete={handleOnboardingComplete}
      />
    </div>
  );
}
