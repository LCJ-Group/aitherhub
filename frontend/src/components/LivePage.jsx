import { useParams, useNavigate } from 'react-router-dom';
import { useState, useEffect } from 'react';
import LiveDashboard from './LiveDashboard';
import VideoService from '../base/services/videoService';
import { useTranslation } from 'react-i18next';

/**
 * LivePage - Standalone page for LiveDashboard at /live/:sessionId
 * 
 * Supports multiple connection modes:
 * 1. Direct video_id (live_capture session) - SSE connects to this video_id
 * 2. Extension video_id (ext_xxx) - SSE connects to extension's video_id
 * 3. No sessionId - auto-discovers active extension session
 * 
 * The LiveDashboard receives data from both the live_capture SSE stream
 * AND bridged extension data (metrics, comments, products, traffic).
 */
export default function LivePage() {
  useTranslation(); // triggers re-render on language change
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [dashboardData, setDashboardData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!sessionId) {
      // No session ID - try to find an active session
      VideoService.getActiveExtensionSessions()
        .then((res) => {
          const data = res?.data || res || {};
          const sessions = data.sessions || [];
          if (sessions.length > 0) {
            const session = sessions[0];
            // Redirect to the specific session
            navigate(`/live/${session.video_id}`, { replace: true });
          } else {
            setError(window.__t('auto_310', 'アクティブなライブセッションが見つかりません'));
            setLoading(false);
          }
        })
        .catch(() => {
          setError(window.__t('auto_316', 'セッション情報の取得に失敗しました'));
          setLoading(false);
        });
      return;
    }

    // We have a sessionId - resolve it
    const videoId = sessionId;

    // Try getLiveStatus first
    VideoService.getLiveStatus(videoId)
      .then((res) => {
        const data = res?.data || res || {};
        if (data.is_live) {
          const username = data.account || data.stream_info?.account || data.stream_info?.username || 'unknown';
          setDashboardData({
            videoId,
            extensionVideoId: data.extension_video_id || undefined,
            liveUrl: '',
            username,
            title: data.stream_info?.title || '',
          });
        } else {
          // Not live via getLiveStatus, try extension sessions
          return tryExtensionSessions(videoId);
        }
      })
      .catch(() => {
        // getLiveStatus failed - still try to connect
        // The SSE endpoint will work as long as extension data is being bridged
        return tryExtensionSessions(videoId);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [sessionId, navigate]);

  const tryExtensionSessions = (videoId) => {
    return VideoService.getActiveExtensionSessions()
      .then((res) => {
        const data = res?.data || res || {};
        const sessions = data.sessions || [];
        
        // First: check if any extension session is bridged to this videoId
        const bridgedSession = sessions.find(s => s.bridged_to === videoId);
        if (bridgedSession) {
          const username = bridgedSession.account || 'unknown';
          setDashboardData({
            videoId,
            liveUrl: '',
            username,
            title: '',
          });
          return;
        }
        
        // Second: check if this videoId matches an extension session directly
        const match = sessions.find(s => s.video_id === videoId);
        if (match) {
          const username = match.account || 'unknown';
          setDashboardData({
            videoId,
            liveUrl: '',
            username,
            title: '',
          });
          return;
        }
        
        // Third: if videoId is a live_capture UUID, still connect to it
        // and also pass the extension session's video_id for dual-SSE
        if (!videoId.startsWith('ext_') && sessions.length > 0) {
          const extSession = sessions[0];
          const username = extSession.account || 'unknown';
          setDashboardData({
            videoId,
            extensionVideoId: extSession.video_id,
            liveUrl: '',
            username,
            title: '',
          });
          return;
        }
        
        // Fourth: just connect anyway - data may arrive later
        if (!videoId.startsWith('ext_')) {
          setDashboardData({
            videoId,
            liveUrl: '',
            username: 'unknown',
            title: '',
          });
        } else {
          setError(window.__t('auto_324', 'ライブセッションが見つかりません'));
        }
      })
      .catch(() => {
        // Even if extension session lookup fails, try connecting
        if (!videoId.startsWith('ext_')) {
          setDashboardData({
            videoId,
            liveUrl: '',
            username: 'unknown',
            title: '',
          });
        } else {
          setError(window.__t('auto_316', 'セッション情報の取得に失敗しました'));
        }
      });
  };

  const handleClose = () => {
    navigate('/');
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black/95 z-50 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 rounded-full border-4 border-t-[#FF0050] border-r-[#00F2EA] border-b-[#FF0050] border-l-[#00F2EA] animate-spin"></div>
          <p className="text-white text-sm">{window.__t('auto_325', 'ライブセッションに接続中...')}</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="fixed inset-0 bg-black/95 z-50 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4 text-center px-8">
          <div className="w-16 h-16 rounded-full bg-gray-800 flex items-center justify-center">
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#FF0050" strokeWidth="2">
              <circle cx="12" cy="12" r="10"/>
              <line x1="15" y1="9" x2="9" y2="15"/>
              <line x1="9" y1="9" x2="15" y2="15"/>
            </svg>
          </div>
          <p className="text-white text-sm">{error}</p>
          <button
            onClick={() => navigate('/')}
            className="mt-4 px-6 py-2 bg-gradient-to-r from-[#FF0050] to-[#00F2EA] text-white rounded-full text-sm font-semibold hover:opacity-90 transition-opacity"
          >
            トップページに戻る
          </button>
        </div>
      </div>
    );
  }

  if (!dashboardData) {
    return null;
  }

  return (
    <LiveDashboard
      videoId={dashboardData.videoId}
      extensionVideoId={dashboardData.extensionVideoId}
      liveUrl={dashboardData.liveUrl}
      username={dashboardData.username}
      title={dashboardData.title}
      onClose={handleClose}
    />
  );
}
