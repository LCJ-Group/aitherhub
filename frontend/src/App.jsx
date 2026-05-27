import MainLayout from './layouts/MainLayout'
import AdminDashboard from './components/AdminDashboard'
import LivePage from './components/LivePage'
import FaceSwapPage from './components/FaceSwapPage'
import AutoVideoPage from './components/AutoVideoPage'
import DigitalHumanPage from './components/DigitalHumanPage'
import AiLiveCreatorPage from './components/AiLiveCreatorPage'
import OBSOutputPage from './components/OBSOutputPage'
import PersonaPage from './components/PersonaPage'
import LiverClonePage from './components/LiverClonePage'
import MagicCutPage from './components/MagicCutPage'
import ScriptGeneratorPage from './components/ScriptGeneratorPage'
import PrivacyPolicy from './components/PrivacyPolicy'
import AuthPage from './pages/authPages/AuthPage'
import BrandPortal from './components/brand/BrandPortal'
import ShareVideoPage from './components/ShareVideoPage'
import LandingPage from './pages/landing/LandingPage'
import { Toaster } from "./components/ui/toaster";
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { LanguageProvider } from './contexts/LanguageContext';
import SectionErrorBoundary from './components/SectionErrorBoundary';

/**
 * HomeRouter: ログイン状態で表示を分岐
 * - ログイン済み → MainLayout（ダッシュボード）
 * - 未ログイン → LandingPage
 */
function HomeRouter() {
  try {
    const userData = localStorage.getItem('user');
    if (userData) {
      const user = JSON.parse(userData);
      if (user && user.access_token) {
        return <MainLayout />;
      }
    }
  } catch (e) {
    // parse error → 未ログイン扱い
  }
  return <LandingPage />;
}

function App() {
  return (
    <LanguageProvider>
      <BrowserRouter>
        <SectionErrorBoundary sectionName="アプリケーション">
          <Routes>
            <Route path="/" element={<HomeRouter />} />
            <Route path="/lp" element={<LandingPage />} />
            <Route path="/video/:videoId" element={<MainLayout />} />
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="/live" element={<LivePage />} />
            <Route path="/live/:sessionId" element={<LivePage />} />
            <Route path="/privacy-policy" element={<PrivacyPolicy />} />
            <Route path="/face-swap" element={<FaceSwapPage />} />
            <Route path="/auto-video" element={<AutoVideoPage />} />
            <Route path="/digital-human" element={<DigitalHumanPage />} />
            <Route path="/ai-live-creator" element={<AiLiveCreatorPage />} />
            <Route path="/ai-live-creator/obs" element={<OBSOutputPage />} />
            <Route path="/personas" element={<PersonaPage />} />
            <Route path="/liver-clone" element={<LiverClonePage />} />
            <Route path="/magic-cut" element={<MagicCutPage />} />
            <Route path="/script-generator" element={<ScriptGeneratorPage />} />
            <Route path="/login" element={<AuthPage mode="login" />} />
            <Route path="/register" element={<AuthPage mode="register" />} />
            <Route path="/forgot-password" element={<AuthPage mode="forgot-password" />} />
            <Route path="/brand" element={<BrandPortal />} />
            <Route path="/v/:clipId" element={<ShareVideoPage />} />
          </Routes>
        </SectionErrorBoundary>
        <Toaster />
      </BrowserRouter>
    </LanguageProvider>
  )
}
export default App
