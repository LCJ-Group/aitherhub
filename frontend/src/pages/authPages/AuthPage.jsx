import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import Login from "./Login";
import Register from "./Register";
import ForgotPassword from "./ForgotPassword";
import { Toaster } from "../../components/ui/toaster";

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

  // After successful login, save user and redirect to home
  const handleLoginSuccess = () => {
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

  const handleRegisterSuccess = () => {
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
