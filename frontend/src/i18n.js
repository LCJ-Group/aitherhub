import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import ja from './locales/ja.json';
import en from './locales/en.json';
import zhTW from './locales/zh-TW.json';

// Determine initial language:
// 1. Check localStorage for user preference
// 2. Fall back to 'ja'
const STORAGE_KEY = 'aitherhub_language';

function getInitialLanguage() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && ['ja', 'en', 'zh-TW'].includes(saved)) {
      return saved;
    }
  } catch (e) { /* ignore */ }
  return 'ja';
}

i18n.use(initReactI18next).init({
  resources: {
    ja: { translation: ja },
    en: { translation: en },
    'zh-TW': { translation: zhTW },
  },
  lng: getInitialLanguage(),
  fallbackLng: 'ja',
  interpolation: { escapeValue: false },
});

// Save language preference when it changes
i18n.on('languageChanged', (lng) => {
  try {
    localStorage.setItem(STORAGE_KEY, lng);
  } catch (e) { /* ignore */ }
});

/**
 * Change the UI language and persist to localStorage (and optionally to backend).
 * @param {'ja' | 'en' | 'zh-TW'} lng
 */
export function changeLanguage(lng) {
  i18n.changeLanguage(lng);
}

// convenience: export bound `t` and expose a global helper for quick usage
export const t = i18n.t.bind(i18n);
try {
  // attach to window for components that prefer calling without imports
  if (typeof window !== 'undefined') {
    window.__t = t;
    window.__changeLanguage = changeLanguage;
    window.__i18n = i18n;
  }
} catch (e) {}

export default i18n;
