import './App.css'
import MainLayout from './layouts/MainLayout'
import AdminDashboard from './components/AdminDashboard'
import LivePage from './components/LivePage'
import FaceSwapPage from './components/FaceSwapPage'
import AutoVideoPage from './components/AutoVideoPage'
import DigitalHumanPage from './components/DigitalHumanPage'
import AiLiveCreatorPage from './components/AiLiveCreatorPage'
import OBSOutputPage from './components/OBSOutputPage'
import PersonaPage from './components/PersonaPage'
import ScriptGeneratorPage from './components/ScriptGeneratorPage'
import PrivacyPolicy from './components/PrivacyPolicy'
import AuthPage from './pages/authPages/AuthPage'
import BrandPortal from './components/brand/BrandPortal'
import { Toaster } from "./components/ui/toaster";
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { LanguageProvider } from './contexts/LanguageContext';

function App() {
  return (
    <LanguageProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<MainLayout />} />
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
          <Route path="/script-generator" element={<ScriptGeneratorPage />} />
          <Route path="/login" element={<AuthPage mode="login" />} />
          <Route path="/register" element={<AuthPage mode="register" />} />
          <Route path="/forgot-password" element={<AuthPage mode="forgot-password" />} />
          <Route path="/brand" element={<BrandPortal />} />
        </Routes>
        <Toaster />
      </BrowserRouter>
    </LanguageProvider>
  )
}
export default App
