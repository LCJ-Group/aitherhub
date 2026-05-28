import { useState, useEffect, useRef } from 'react';
import BaseApiService from '../base/api/BaseApiService';

const API_BASE = import.meta.env.VITE_API_BASE_URL;

/**
 * OnboardingModal - チャット形式のカウンセリングシート
 * 登録直後に表示され、ユーザータイプ・課題・TikTokアカウントを聞く
 */
export default function OnboardingModal({ isOpen, onComplete }) {
  const [step, setStep] = useState(0); // 0: type, 1: challenge, 2: tiktok, 3: done
  const [messages, setMessages] = useState([]);
  const [userType, setUserType] = useState(null);
  const [mainChallenge, setMainChallenge] = useState(null);
  const [tiktokAccount, setTiktokAccount] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const chatEndRef = useRef(null);

  // Initial message
  useEffect(() => {
    if (isOpen && messages.length === 0) {
      setMessages([
        {
          from: 'bot',
          text: 'ようこそ！🎉\nあなたに最適な解析をお届けするために、1つだけ教えてください。',
          id: 1,
        },
        {
          from: 'bot',
          text: 'あなたはどちらに近いですか？',
          id: 2,
          options: [
            { value: 'liver', label: '🎙️ 自分で配信して商品を売っている', sublabel: 'ライブコマーサー' },
            { value: 'brand', label: '🏢 ライバーに依頼して商品を売っている', sublabel: 'ブランド・EC事業者' },
            { value: 'both', label: '🔄 両方やっている', sublabel: '' },
          ],
        },
      ]);
    }
  }, [isOpen]);

  // Auto-scroll
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleTypeSelect = (type) => {
    const labels = {
      liver: '🎙️ 自分で配信して商品を売っている',
      brand: '🏢 ライバーに依頼して商品を売っている',
      both: '🔄 両方やっている',
    };
    setUserType(type);
    setMessages(prev => [
      ...prev,
      { from: 'user', text: labels[type], id: Date.now() },
    ]);

    // Show challenge question based on type
    setTimeout(() => {
      const challengeOptions = type === 'brand'
        ? [
            { value: 'find_liver', label: '🔍 良いライバーを見つけたい' },
            { value: 'roi', label: '📊 ROIを可視化したい' },
            { value: 'sales', label: '📈 売上を伸ばしたい' },
          ]
        : [
            { value: 'sales', label: '💰 売上' },
            { value: 'viewers', label: '👀 視聴者数' },
            { value: 'retention', label: '⏱️ 滞在時間' },
            { value: 'cvr', label: '🎯 CVR（購入率）' },
          ];

      setMessages(prev => [
        ...prev,
        {
          from: 'bot',
          text: type === 'brand' ? '一番の課題は何ですか？' : '一番伸ばしたいのは？',
          id: Date.now(),
          options: challengeOptions,
        },
      ]);
      setStep(1);
    }, 400);
  };

  const handleChallengeSelect = (challenge) => {
    const allOptions = {
      find_liver: '🔍 良いライバーを見つけたい',
      roi: '📊 ROIを可視化したい',
      sales_brand: '📈 売上を伸ばしたい',
      sales: '💰 売上',
      viewers: '👀 視聴者数',
      retention: '⏱️ 滞在時間',
      cvr: '🎯 CVR（購入率）',
    };
    setMainChallenge(challenge);
    setMessages(prev => [
      ...prev,
      { from: 'user', text: allOptions[challenge] || challenge, id: Date.now() },
    ]);

    setTimeout(() => {
      setMessages(prev => [
        ...prev,
        {
          from: 'bot',
          text: 'TikTokのアカウント名を教えてください（任意）',
          id: Date.now(),
          inputField: true,
        },
      ]);
      setStep(2);
    }, 400);
  };

  const handleTiktokSubmit = async (skip = false) => {
    if (!skip && tiktokAccount.trim()) {
      setMessages(prev => [
        ...prev,
        { from: 'user', text: `@${tiktokAccount.trim()}`, id: Date.now() },
      ]);
    } else {
      setMessages(prev => [
        ...prev,
        { from: 'user', text: 'スキップ', id: Date.now() },
      ]);
    }

    setStep(3);
    setIsSubmitting(true);

    // Save to backend
    try {
      const api = new BaseApiService(API_BASE);
      await api.post('/api/v1/profile/onboarding', {
        user_type: userType,
        main_challenge: mainChallenge,
        tiktok_account: skip ? null : tiktokAccount.trim() || null,
      });
    } catch (err) {
      console.error('[Onboarding] Failed to save profile:', err);
      // Don't block the user - continue anyway
    }

    setTimeout(() => {
      setMessages(prev => [
        ...prev,
        {
          from: 'bot',
          text: '✅ ありがとうございます！\nあなたに最適な解析を準備します。',
          id: Date.now(),
        },
      ]);
      setIsSubmitting(false);
      // Auto-close after brief delay
      setTimeout(() => {
        onComplete?.();
      }, 1000);
    }, 500);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 max-h-[80vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center">
            <span className="text-white text-sm font-bold">A</span>
          </div>
          <div>
            <p className="font-semibold text-gray-900 text-sm">Aitherhub</p>
            <p className="text-xs text-gray-500">カウンセリング</p>
          </div>
        </div>

        {/* Chat area */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
          {messages.map((msg) => (
            <div key={msg.id}>
              {msg.from === 'bot' ? (
                <div className="flex gap-2">
                  <div className="w-6 h-6 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex-shrink-0 flex items-center justify-center mt-0.5">
                    <span className="text-white text-[10px] font-bold">A</span>
                  </div>
                  <div className="flex-1">
                    <div className="bg-gray-100 rounded-2xl rounded-tl-sm px-4 py-2.5 text-sm text-gray-800 whitespace-pre-line inline-block max-w-[85%]">
                      {msg.text}
                    </div>
                    {/* Options */}
                    {msg.options && step === (msg.id === 2 ? 0 : 1) && (
                      <div className="mt-2 space-y-2">
                        {msg.options.map((opt) => (
                          <button
                            key={opt.value}
                            onClick={() => {
                              if (step === 0) handleTypeSelect(opt.value);
                              else if (step === 1) handleChallengeSelect(opt.value);
                            }}
                            className="block w-full text-left px-4 py-3 bg-white border border-gray-200 rounded-xl hover:border-purple-400 hover:bg-purple-50 transition-all text-sm group"
                          >
                            <span className="font-medium text-gray-800 group-hover:text-purple-700">{opt.label}</span>
                            {opt.sublabel && (
                              <span className="block text-xs text-gray-500 mt-0.5">{opt.sublabel}</span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                    {/* Input field for TikTok */}
                    {msg.inputField && step === 2 && (
                      <div className="mt-3 space-y-2">
                        <div className="flex gap-2">
                          <div className="flex-1 relative">
                            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">@</span>
                            <input
                              type="text"
                              value={tiktokAccount}
                              onChange={(e) => setTiktokAccount(e.target.value)}
                              placeholder="アカウント名"
                              className="w-full pl-7 pr-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:border-purple-400"
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' && tiktokAccount.trim()) {
                                  handleTiktokSubmit(false);
                                }
                              }}
                            />
                          </div>
                          <button
                            onClick={() => handleTiktokSubmit(false)}
                            disabled={!tiktokAccount.trim()}
                            className="px-4 py-2.5 bg-purple-600 text-white rounded-xl text-sm font-medium disabled:opacity-40 hover:bg-purple-700 transition-colors"
                          >
                            送信
                          </button>
                        </div>
                        <button
                          onClick={() => handleTiktokSubmit(true)}
                          className="text-xs text-gray-500 hover:text-gray-700 underline"
                        >
                          あとで入力する
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex justify-end">
                  <div className="bg-purple-600 text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm max-w-[85%]">
                    {msg.text}
                  </div>
                </div>
              )}
            </div>
          ))}
          {isSubmitting && (
            <div className="flex gap-2">
              <div className="w-6 h-6 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex-shrink-0 flex items-center justify-center">
                <span className="text-white text-[10px] font-bold">A</span>
              </div>
              <div className="bg-gray-100 rounded-2xl rounded-tl-sm px-4 py-3 inline-block">
                <div className="flex gap-1">
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
                </div>
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>
      </div>
    </div>
  );
}
