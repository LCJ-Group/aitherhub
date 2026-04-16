/**
 * LanguageContext - Provides reactive language switching across the entire app.
 * 
 * When the language changes via i18n.changeLanguage(), this context triggers
 * a re-render of all components that use window.__t(), ensuring the UI updates
 * immediately without requiring each component to import useTranslation.
 */
import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import i18n from '../i18n';

const LanguageContext = createContext({
  language: 'ja',
  changeLanguage: () => {},
});

export function LanguageProvider({ children }) {
  const [language, setLanguage] = useState(i18n.language || 'ja');

  useEffect(() => {
    const handleLanguageChanged = (lng) => {
      setLanguage(lng);
      // Update window.__t to always use the latest i18n.t
      if (typeof window !== 'undefined') {
        window.__t = i18n.t.bind(i18n);
      }
    };

    i18n.on('languageChanged', handleLanguageChanged);
    return () => {
      i18n.off('languageChanged', handleLanguageChanged);
    };
  }, []);

  const handleChangeLanguage = useCallback((lng) => {
    i18n.changeLanguage(lng);
  }, []);

  return (
    <LanguageContext.Provider value={{ language, changeLanguage: handleChangeLanguage }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  return useContext(LanguageContext);
}

export default LanguageContext;
