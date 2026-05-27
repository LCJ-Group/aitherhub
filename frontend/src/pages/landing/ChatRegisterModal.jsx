import { useState, useRef, useEffect, useCallback } from 'react';
import AuthService from '../../base/services/userService';
import UploadService from '../../base/services/uploadService';

/* ─────────────────────────────────────────────
   Chat-style Registration Modal
   Appears inline after upload completion on LP.
   Steps: email → password → confirm → register → upload-complete → redirect
   ───────────────────────────────────────────── */

const STEPS = ['email', 'password', 'confirm', 'registering', 'done'];

// Chat bubble component
function ChatBubble({ from, children, animate }) {
  const isBot = from === 'bot';
  return (
    <div style={{
      display: 'flex',
      justifyContent: isBot ? 'flex-start' : 'flex-end',
      marginBottom: '12px',
      animation: animate ? 'chatFadeIn 0.3s ease' : 'none',
    }}>
      <div style={{
        maxWidth: '85%',
        padding: '10px 16px',
        borderRadius: isBot ? '4px 16px 16px 16px' : '16px 4px 16px 16px',
        background: isBot
          ? 'rgba(99, 102, 241, 0.12)'
          : 'rgba(255, 255, 255, 0.08)',
        border: isBot
          ? '1px solid rgba(99, 102, 241, 0.25)'
          : '1px solid rgba(255, 255, 255, 0.12)',
        color: '#e2e8f0',
        fontSize: '14px',
        lineHeight: '1.6',
      }}>
        {children}
      </div>
    </div>
  );
}

export default function ChatRegisterModal({ isOpen, onClose, onSuccess, pendingVideo }) {
  const [currentStep, setCurrentStep] = useState('email');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [messages, setMessages] = useState([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const inputRef = useRef(null);
  const chatEndRef = useRef(null);

  // Initialize chat on open
  useEffect(() => {
    if (isOpen) {
      setMessages([
        { from: 'bot', text: 'アップロード完了！🎉 AI解析を開始するために、アカウントを作成しましょう。', id: 1 },
        { from: 'bot', text: 'まず、メールアドレスを入力してください。', id: 2 },
      ]);
      setCurrentStep('email');
      setEmail('');
      setPassword('');
      setConfirmPassword('');
      setError('');
      setIsProcessing(false);
    }
  }, [isOpen]);

  // Auto-scroll to bottom
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input
  useEffect(() => {
    if (isOpen && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [isOpen, currentStep]);

  // Email validation
  const validateEmail = (email) => {
    const emailRegex = /^[^\s@]+@[^.]+(\.[^\s@]+)+$/;
    return emailRegex.test(email);
  };

  // Handle submit for current step
  const handleSubmit = useCallback(async () => {
    setError('');

    if (currentStep === 'email') {
      if (!email.trim()) {
        setError('メールアドレスを入力してください');
        return;
      }
      if (!validateEmail(email.trim())) {
        setError('メールアドレスの形式が正しくありません');
        return;
      }
      // Add user message and bot response
      setMessages(prev => [
        ...prev,
        { from: 'user', text: email, id: Date.now() },
        { from: 'bot', text: '次に、パスワードを設定してください（8文字以上）。', id: Date.now() + 1 },
      ]);
      setCurrentStep('password');
    }
    else if (currentStep === 'password') {
      if (!password) {
        setError('パスワードを入力してください');
        return;
      }
      if (password.length < 8) {
        setError('パスワードは8文字以上で入力してください');
        return;
      }
      setMessages(prev => [
        ...prev,
        { from: 'user', text: '••••••••', id: Date.now() },
        { from: 'bot', text: '確認のため、もう一度パスワードを入力してください。', id: Date.now() + 1 },
      ]);
      setCurrentStep('confirm');
    }
    else if (currentStep === 'confirm') {
      if (!confirmPassword) {
        setError('パスワードを再入力してください');
        return;
      }
      if (password !== confirmPassword) {
        setError('パスワードが一致しません');
        return;
      }
      setMessages(prev => [
        ...prev,
        { from: 'user', text: '••••••••', id: Date.now() },
        { from: 'bot', text: 'アカウントを作成しています...', id: Date.now() + 1 },
      ]);
      setCurrentStep('registering');
      setIsProcessing(true);

      // Perform registration
      try {
        await AuthService.register(email.trim(), password);
        const userInfo = await AuthService.getCurrentUser();
        const userData = {
          isLoggedIn: true,
          email: userInfo?.email || email.trim(),
        };
        localStorage.setItem('user', JSON.stringify(userData));

        // Now call upload-complete if we have pending video
        if (pendingVideo?.video_id && pendingVideo?.upload_id) {
          setMessages(prev => [
            ...prev,
            { from: 'bot', text: '登録完了！AI解析を開始しています...🚀', id: Date.now() },
          ]);

          try {
            await UploadService.uploadComplete(
              email.trim(),
              pendingVideo.video_id,
              pendingVideo.filename,
              pendingVideo.upload_id,
              'ja'
            );
            localStorage.removeItem('aitherhub_pending_video');
          } catch (uploadErr) {
            console.error('[ChatRegister] upload-complete failed:', uploadErr);
            // Still proceed - the video was uploaded, just the analysis trigger failed
            localStorage.removeItem('aitherhub_pending_video');
          }
        }

        setMessages(prev => [
          ...prev,
          { from: 'bot', text: '✅ 準備完了！ダッシュボードに移動します。', id: Date.now() },
        ]);
        setCurrentStep('done');
        setIsProcessing(false);

        // Redirect after a brief delay
        setTimeout(() => {
          if (onSuccess) onSuccess();
        }, 1200);

      } catch (err) {
        setIsProcessing(false);
        let detail = err?.response?.data?.detail || err?.message || '';
        if (Array.isArray(detail) && detail.length > 0) {
          detail = detail[0]?.msg || detail[0]?.message || JSON.stringify(detail);
        }
        // Map common errors
        let errorMsg = detail;
        if (detail.toLowerCase().includes('already exists') || detail.toLowerCase().includes('duplicate')) {
          errorMsg = 'このメールアドレスは既に登録されています。ログインしてください。';
        }
        setMessages(prev => [
          ...prev,
          { from: 'bot', text: `❌ ${errorMsg || '登録に失敗しました。もう一度お試しください。'}`, id: Date.now() },
        ]);
        setCurrentStep('email');
        setEmail('');
        setPassword('');
        setConfirmPassword('');
      }
    }
  }, [currentStep, email, password, confirmPassword, pendingVideo, onSuccess]);

  // Get placeholder text based on step
  const getPlaceholder = () => {
    switch (currentStep) {
      case 'email': return 'メールアドレスを入力...';
      case 'password': return 'パスワードを入力（8文字以上）...';
      case 'confirm': return 'パスワードを再入力...';
      default: return '';
    }
  };

  // Get input type
  const getInputType = () => {
    if (currentStep === 'password' || currentStep === 'confirm') return 'password';
    return 'email';
  };

  // Get current value
  const getCurrentValue = () => {
    switch (currentStep) {
      case 'email': return email;
      case 'password': return password;
      case 'confirm': return confirmPassword;
      default: return '';
    }
  };

  // Set current value
  const setCurrentValue = (val) => {
    switch (currentStep) {
      case 'email': setEmail(val); break;
      case 'password': setPassword(val); break;
      case 'confirm': setConfirmPassword(val); break;
    }
  };

  if (!isOpen) return null;

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      zIndex: 9999,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'rgba(0, 0, 0, 0.7)',
      backdropFilter: 'blur(8px)',
      animation: 'chatFadeIn 0.2s ease',
    }} onClick={(e) => { if (e.target === e.currentTarget && !isProcessing) onClose(); }}>
      <div style={{
        width: '100%',
        maxWidth: '440px',
        maxHeight: '80vh',
        margin: '16px',
        borderRadius: '20px',
        background: 'linear-gradient(160deg, rgba(30, 20, 50, 0.98) 0%, rgba(15, 10, 35, 0.99) 100%)',
        border: '1px solid rgba(139, 92, 246, 0.3)',
        boxShadow: '0 20px 80px rgba(99, 102, 241, 0.2)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          padding: '16px 20px',
          borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{
              width: '32px',
              height: '32px',
              borderRadius: '50%',
              background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '14px',
            }}>
              🤖
            </div>
            <div>
              <p style={{ color: '#e2e8f0', fontSize: '14px', fontWeight: '600', margin: 0 }}>
                AitherHub
              </p>
              <p style={{ color: '#64748b', fontSize: '11px', margin: 0 }}>
                30秒で登録完了
              </p>
            </div>
          </div>
          {!isProcessing && (
            <button
              onClick={onClose}
              style={{
                background: 'none',
                border: 'none',
                color: '#64748b',
                fontSize: '20px',
                cursor: 'pointer',
                padding: '4px 8px',
                borderRadius: '6px',
              }}
            >
              ✕
            </button>
          )}
        </div>

        {/* Chat messages */}
        <div style={{
          flex: 1,
          overflowY: 'auto',
          padding: '20px 16px',
          minHeight: '200px',
          maxHeight: '400px',
        }}>
          {messages.map((msg) => (
            <ChatBubble key={msg.id} from={msg.from} animate>
              {msg.text}
            </ChatBubble>
          ))}
          {isProcessing && currentStep === 'registering' && (
            <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: '12px' }}>
              <div style={{
                padding: '10px 16px',
                borderRadius: '4px 16px 16px 16px',
                background: 'rgba(99, 102, 241, 0.12)',
                border: '1px solid rgba(99, 102, 241, 0.25)',
              }}>
                <div style={{ display: 'flex', gap: '4px' }}>
                  <span style={{ animation: 'dotBounce 1.2s infinite 0s', color: '#a78bfa' }}>●</span>
                  <span style={{ animation: 'dotBounce 1.2s infinite 0.2s', color: '#a78bfa' }}>●</span>
                  <span style={{ animation: 'dotBounce 1.2s infinite 0.4s', color: '#a78bfa' }}>●</span>
                </div>
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Input area */}
        {(currentStep === 'email' || currentStep === 'password' || currentStep === 'confirm') && (
          <div style={{
            padding: '12px 16px',
            borderTop: '1px solid rgba(255, 255, 255, 0.06)',
            background: 'rgba(0, 0, 0, 0.2)',
          }}>
            {error && (
              <p style={{ color: '#ef4444', fontSize: '12px', marginBottom: '8px', paddingLeft: '4px' }}>
                {error}
              </p>
            )}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              background: 'rgba(255, 255, 255, 0.06)',
              borderRadius: '12px',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              padding: '4px 4px 4px 16px',
            }}>
              <input
                ref={inputRef}
                type={getInputType()}
                placeholder={getPlaceholder()}
                value={getCurrentValue()}
                onChange={(e) => { setCurrentValue(e.target.value); setError(''); }}
                onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
                style={{
                  flex: 1,
                  background: 'transparent',
                  border: 'none',
                  outline: 'none',
                  color: '#e2e8f0',
                  fontSize: '14px',
                  padding: '10px 0',
                }}
              />
              <button
                onClick={handleSubmit}
                style={{
                  background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                  border: 'none',
                  color: '#fff',
                  width: '36px',
                  height: '36px',
                  borderRadius: '10px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  fontSize: '16px',
                  transition: 'transform 0.15s',
                  flexShrink: 0,
                }}
              >
                →
              </button>
            </div>
            {currentStep === 'email' && (
              <p style={{ color: '#475569', fontSize: '11px', marginTop: '8px', textAlign: 'center' }}>
                クレジットカード不要 ・ 30秒で完了
              </p>
            )}
          </div>
        )}

        {/* Done state */}
        {currentStep === 'done' && (
          <div style={{
            padding: '16px',
            borderTop: '1px solid rgba(255, 255, 255, 0.06)',
            textAlign: 'center',
          }}>
            <div style={{
              width: '40px',
              height: '40px',
              margin: '0 auto 8px',
              borderRadius: '50%',
              background: 'linear-gradient(135deg, #10b981, #059669)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '18px',
              color: '#fff',
              animation: 'popIn 0.4s ease',
            }}>
              ✓
            </div>
            <p style={{ color: '#10b981', fontSize: '13px', fontWeight: '600' }}>
              ダッシュボードに移動中...
            </p>
          </div>
        )}
      </div>

      {/* CSS Animations */}
      <style>{`
        @keyframes chatFadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes dotBounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-4px); opacity: 1; }
        }
        @keyframes popIn {
          from { transform: scale(0.5); opacity: 0; }
          to { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
