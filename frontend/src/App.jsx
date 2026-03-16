import './App.css'
import MainLayout from './layouts/MainLayout'
import AdminDashboard from './components/AdminDashboard'
import LivePage from './components/LivePage'
import FaceSwapPage from './components/FaceSwapPage'
import AutoVideoPage from './components/AutoVideoPage'
import DigitalHumanPage from './components/DigitalHumanPage'
import PrivacyPolicy from './components/PrivacyPolicy'
import { Toaster } from "./components/ui/toaster";
import { BrowserRouter, Routes, Route } from 'react-router-dom';

function App() {
  return (
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
      </Routes>
      <Toaster />
    </BrowserRouter>
  )
}
export default App
