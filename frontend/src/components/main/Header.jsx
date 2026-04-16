import { useEffect, useState } from "react";
import MenuIcon from "../../assets/icons/menu.png";
import LoginModal from "../modals/LoginModal";
import RegisterModal from "../modals/RegisterModal";
import ForgotPasswordModal from "../modals/ForgotPasswordModal";
import LCJLinkingModal from "../modals/LCJLinkingModal";
import AuthService from "../../base/services/userService";
import { BasicButton } from "../buttons";
import { Button } from "../ui/Button";
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
} from "../ui/dropdown-menu";
import { ChevronDown, User, Settings, LogOut, Link2, Globe, Check } from "lucide-react";
import { useTranslation } from 'react-i18next';
import { changeLanguage } from '../../i18n';

export default function Header({
  onOpenSidebar,
  user: propUser,
  setUser: setPropUser,
}) {
  const { t, i18n } = useTranslation();
  const [openLogin, setOpenLogin] = useState(false);
  const [openRegister, setOpenRegister] = useState(false);
  const [localUser, setLocalUser] = useState(null);
  const [openLCJLinking, setOpenLCJLinking] = useState(false);

  useEffect(() => {
    if (propUser) return;
    const storedUser = localStorage.getItem("user");
    if (storedUser) {
      setLocalUser(JSON.parse(storedUser));
    }
  }, [propUser]);

  // Check URL query parameter to auto-open login modal
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('showLogin') === 'true') {
      setOpenLogin(true);
      // Remove query parameter from URL
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  // Listen for custom event to open login modal
  useEffect(() => {
    const handleOpenLoginModal = () => {
      // First, ensure user state is updated to reflect logout
      // Clear user from localStorage (already done by AuthService.logout)
      // Update local user state
      setLocalUser({ isLoggedIn: false });
      if (setPropUser) {
        setPropUser({ isLoggedIn: false });
      }
      // Then open login modal
      setOpenLogin(true);
    };
    window.addEventListener('openLoginModal', handleOpenLoginModal);
    return () => {
      window.removeEventListener('openLoginModal', handleOpenLoginModal);
    };
  }, [setPropUser]);


  const user = propUser ?? localUser;
  const setUser = setPropUser ?? setLocalUser;
  const [openForgotPassword, setOpenForgotPassword] = useState(false);

  const handleLoginOpenChange = (nextOpen) => {
    setOpenLogin(nextOpen);
    if (!nextOpen) {
      const storedUser = localStorage.getItem("user");
      if (storedUser) setUser(JSON.parse(storedUser));
    }
  };

  const handleRegisterOpenChange = (nextOpen) => {
    setOpenRegister(nextOpen);
    if (!nextOpen) {
      const storedUser = localStorage.getItem("user");
      if (storedUser) setUser(JSON.parse(storedUser));
    }
  };

  const handleForgotPasswordOpenChange = (nextOpen) => {
    setOpenForgotPassword(nextOpen);
    if (!nextOpen) {
      const storedUser = localStorage.getItem("user");
      if (storedUser) setUser(JSON.parse(storedUser));
    }
  };

  return (
    <>
      <header className=" px-6 py-4 md:px-5 flex items-center justify-between">
        <div className="flex items-center gap-[16px]">
          <img
            src={MenuIcon}
            alt="Menu"
            onClick={onOpenSidebar}
            className="block md:hidden w-[19px] h-[16px]"
          />

        </div>

        {/* RIGHT SIDE */}
        <div className="flex items-center gap-2 justify-center h-[35px] rounded-[50px]">
          {user?.isLoggedIn ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-gray-700 hover:text-gray-900 hover:bg-gray-100 gap-1 max-w-[200px]"
                >
                  <span className="truncate">{user.email}</span>
                  <ChevronDown className="w-3.5 h-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>{window.__t('myAccount')}</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem>
                  <User className="w-4 h-4" />
                  {window.__t('myAccount')}
                </DropdownMenuItem>
                <ForgotPasswordModal
                  open={openForgotPassword}
                  onOpenChange={handleForgotPasswordOpenChange}
                  trigger={
                    <DropdownMenuItem>
                      <Settings className="w-4 h-4" />
                      {window.__t("changePassword")}
                    </DropdownMenuItem>
                  }
                />
                <DropdownMenuItem
                  onClick={(e) => {
                    e.preventDefault();
                    setOpenLCJLinking(true);
                  }}
                >
                  <Link2 className="w-4 h-4" />
                  {window.__t("lcjLinking")}
                </DropdownMenuItem>
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>
                    <Globe className="w-4 h-4" />
                    {window.__t("language_settings")}
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
                  onClick={() => {
                    AuthService.logout();
                    setUser({ isLoggedIn: false });
                    window.location.reload();
                  }}
                >
                  <LogOut className="w-4 h-4" />
                  {window.__t('logout')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <>
              <LoginModal
                open={openLogin}
                onOpenChange={handleLoginOpenChange}
                onSwitchToRegister={() => {
                  setOpenLogin(false);
                  setOpenRegister(true);
                }}
                trigger={
                  <BasicButton variant="secondary">
                    {window.__t("login")}
                  </BasicButton>
                }
              />
              <RegisterModal
                open={openRegister}
                onOpenChange={handleRegisterOpenChange}
                onSwitchToLogin={() => {
                  setOpenRegister(false);
                  setOpenLogin(true);
                }}
                trigger={
                  <BasicButton variant="primary">
                    {window.__t("register")}
                  </BasicButton>
                }
              />
            </>
          )}
        </div>
      </header>

      <ForgotPasswordModal
        open={openForgotPassword}
        onOpenChange={handleForgotPasswordOpenChange}
      />

      <LCJLinkingModal
        open={openLCJLinking}
        onOpenChange={setOpenLCJLinking}
      />
    </>
  );
}
