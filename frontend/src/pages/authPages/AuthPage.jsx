import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import Login from "./Login";
import Register from "./Register";
import ForgotPassword from "./ForgotPassword";
import { Toaster } from "../../components/ui/toaster";
import UploadService from "../../base/services/uploadService";

/**
 * Standalone auth page wrapper.
 * Renders Login, Register, or ForgotPassword as a full page
 * instead of a modal overlay.
 *
 * Routes:
 *   /login           → Login form
 *   /register        → Register form
 *   /forgot-password → Password reset form
 */
export default function AuthPage({ mode = "login" }) {
  const navigate = useNavigate();
  const [currentMode, setCurrentMode] = useState(mode);

  // Sync with prop when route changes
  useEffect(() => {
    setCurrentMode(mode);
  }, [mode]);

  // After successful login, check for pending video from LP upload and complete it
  const handleLoginSuccess = async () => {
    // Check for pending video uploaded from LP (before registration)
    try {
      const pendingRaw = localStorage.getItem('aitherhub_pending_video');
      if (pendingRaw) {
        const pending = JSON.parse(pendingRaw);
        if (pending?.video_id && pending?.upload_id && pending?.filename) {
          // Get user email from localStorage
          const storedUser = localStorage.getItem('user');
          const userEmail = storedUser ? JSON.parse(storedUser)?.email : pending.guestEmail;
          
          // Call upload-complete API (requires auth token which is now set)
          console.log('[AuthPage] Completing pending LP upload:', pending.video_id);
          await UploadService.uploadComplete(
            userEmail || pending.guestEmail,
            pending.video_id,
            pending.filename,
            pending.upload_id,
            'ja',
            null, // brand_client_id
            pending.guestEmail // source_email: original blob path email
          );
          console.log('[AuthPage] Pending upload completed successfully');
          
          // Clear pending video from localStorage
          localStorage.removeItem('aitherhub_pending_video');
          
          // Redirect to home (dashboard) - the video will appear in the list
          navigate('/');
          return;
        }
      }
    } catch (err) {
      console.error('[AuthPage] Failed to complete pending upload:', err);
      // Still clear the pending video to avoid infinite retry
      localStorage.removeItem('aitherhub_pending_video');
    }

    // Normal login flow
    try {
      const storedUser = localStorage.getItem("user");
      if (storedUser) {
        const parsedUser = JSON.parse(storedUser);
        if (parsedUser?.isLoggedIn) {
          // Check for post-login redirect
          const redirectTo = sessionStorage.getItem("postLoginRedirect");
          if (redirectTo) {
            sessionStorage.removeItem("postLoginRedirect");
            navigate(redirectTo);
          } else {
            navigate("/");
          }
          return;
        }
      }
    } catch {
      // ignore
    }
    navigate("/");
  };

  // After registration, auto-login is done (token is set), so handle pending video too
  const handleRegisterSuccess = async () => {
    // Check for pending video uploaded from LP
    try {
      const pendingRaw = localStorage.getItem('aitherhub_pending_video');
      if (pendingRaw) {
        const pending = JSON.parse(pendingRaw);
        if (pending?.video_id && pending?.upload_id && pending?.filename) {
          const storedUser = localStorage.getItem('user');
          const userEmail = storedUser ? JSON.parse(storedUser)?.email : pending.guestEmail;
          
          console.log('[AuthPage] Completing pending LP upload after registration:', pending.video_id);
          await UploadService.uploadComplete(
            userEmail || pending.guestEmail,
            pending.video_id,
            pending.filename,
            pending.upload_id,
            'ja',
            null, // brand_client_id
            pending.guestEmail // source_email: original blob path email
          );
          console.log('[AuthPage] Pending upload completed after registration');
          localStorage.removeItem('aitherhub_pending_video');
          
          // Redirect to dashboard directly (skip login form switch)
          navigate('/');
          return;
        }
      }
    } catch (err) {
      console.error('[AuthPage] Failed to complete pending upload after registration:', err);
      localStorage.removeItem('aitherhub_pending_video');
    }

    // Normal register flow - switch to login
    setCurrentMode("login");
  };

  const handleForgotPasswordSuccess = () => {
    setCurrentMode("login");
  };

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center">
      <div className="w-full max-w-[428px] mx-auto bg-white rounded-[10px] shadow-lg p-6 my-8">
        {currentMode === "login" && (
          <Login
            onSuccess={handleLoginSuccess}
            onSwitchToRegister={() => setCurrentMode("register")}
          />
        )}
        {currentMode === "register" && (
          <Register
            onSuccess={handleRegisterSuccess}
            onSwitchToLogin={() => setCurrentMode("login")}
          />
        )}
        {currentMode === "forgot-password" && (
          <ForgotPassword
            onSuccess={handleForgotPasswordSuccess}
          />
        )}

        {/* Navigation links between auth pages */}
        <div className="mt-4 text-center text-sm text-gray-500">
          {currentMode !== "login" && (
            <button
              onClick={() => setCurrentMode("login")}
              className="text-[#4500FF] hover:underline mr-4"
            >
              {window.__t ? window.__t("login") : "ログイン"}
            </button>
          )}
          {currentMode !== "register" && (
            <button
              onClick={() => setCurrentMode("register")}
              className="text-[#4500FF] hover:underline mr-4"
            >
              {window.__t ? window.__t("registerHere") : "新規登録"}
            </button>
          )}
          {currentMode === "login" && (
            <button
              onClick={() => setCurrentMode("forgot-password")}
              className="text-[#4500FF] hover:underline"
            >
              {window.__t ? window.__t("resetPassword") : "パスワードを再設定する"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
