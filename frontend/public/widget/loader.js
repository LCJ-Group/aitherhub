/**
 * AitherHub Widget Loader v2.1 — TikTok-Style Fullscreen Feed
 *
 * GTM経由で配信される軽量エントリーポイント。
 * 先方のECサイトに1行のタグを追加するだけで、
 * TikTok風フルスクリーン縦型動画フィード + 3つの悪魔的ハックを展開する。
 *
 * Usage (GTM Custom HTML):
 *   <script src="https://www.aitherhub.com/widget/loader.js" data-client-id="YOUR_ID" async></script>
 *
 * Features:
 *   - Floating bubble with auto-playing video preview (muted)
 *   - Tap → fullscreen TikTok-style vertical video feed overlay
 *   - Swipe up/down (touch) or scroll/arrow keys to navigate videos
 *   - Right-side action buttons (like, share, mute)
 *   - Bottom product info + CTA "購入する" button
 *   - SaaS: brand name/logo/theme color from API config
 *   - Hack 1: DOM auto-parse (page scraping)
 *   - Hack 2: In-video CTA action
 *   - Hack 3: Shadow Tracking (localStorage session)
 *
 * v2.1 Changes:
 *   - FAB bubble now shows auto-playing video preview instead of static icon
 *   - Client-side filtering of clips without valid clip_url
 *   - Improved empty state handling
 */
(function () {
  "use strict";

  // ── Prevent double-loading ──
  if (window.__AITHERHUB_WIDGET_LOADED) return;
  window.__AITHERHUB_WIDGET_LOADED = true;

  // ── Configuration ──
  var SCRIPT_TAG = document.currentScript || (function () {
    var scripts = document.getElementsByTagName("script");
    for (var i = scripts.length - 1; i >= 0; i--) {
      if (scripts[i].src && scripts[i].src.indexOf("loader.js") !== -1) return scripts[i];
    }
    return null;
  })();

  var CLIENT_ID = SCRIPT_TAG ? SCRIPT_TAG.getAttribute("data-client-id") : null;
  if (!CLIENT_ID) {
    console.warn("[AitherHub] Missing data-client-id attribute");
    return;
  }

  var API_BASE = "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1";
  var SESSION_KEY = "lcj_sid";
  var TIMESTAMP_KEY = "lcj_ts";

  // ── Hack 3: Shadow Tracking — Session ID Management ──
  function getOrCreateSessionId() {
    var sid = localStorage.getItem(SESSION_KEY) || sessionStorage.getItem(SESSION_KEY);
    if (!sid) {
      sid = "lcj_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 9);
    }
    try { localStorage.setItem(SESSION_KEY, sid); } catch (e) { }
    try { sessionStorage.setItem(SESSION_KEY, sid); } catch (e) { }
    try { localStorage.setItem(TIMESTAMP_KEY, new Date().toISOString()); } catch (e) { }
    try { sessionStorage.setItem(TIMESTAMP_KEY, new Date().toISOString()); } catch (e) { }
    return sid;
  }

  var SESSION_ID = getOrCreateSessionId();

  // ── Utility: Send data to API (fire-and-forget) ──
  function sendBeacon(endpoint, data) {
    data.client_id = CLIENT_ID;
    data.session_id = SESSION_ID;
    var url = API_BASE + endpoint;
    var body = JSON.stringify(data);
    if (navigator.sendBeacon) {
      try {
        navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
        return;
      } catch (e) { }
    }
    try {
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        keepalive: true,
      }).catch(function () { });
    } catch (e) { }
  }

  // ── Hack 1: DOM Auto-Parse ──
  function scrapePageContext() {
    var getMeta = function (name) {
      var el = document.querySelector('meta[property="' + name + '"]') ||
        document.querySelector('meta[name="' + name + '"]');
      return el ? el.getAttribute("content") : null;
    };
    var h1 = document.querySelector("h1");
    var canonical = document.querySelector('link[rel="canonical"]');
    var priceEl = document.querySelector('[class*="price"]:not([class*="old"]):not([class*="was"])') ||
      document.querySelector('[id*="price"]') ||
      document.querySelector('.product-price') ||
      document.querySelector('[itemprop="price"]');
    var context = {
      page_url: window.location.href,
      canonical_url: canonical ? canonical.getAttribute("href") : window.location.href,
      title: document.title,
      og_title: getMeta("og:title"),
      og_image: getMeta("og:image"),
      h1_text: h1 ? h1.textContent.trim().substring(0, 200) : null,
      product_price: priceEl ? priceEl.textContent.trim().substring(0, 50) : null,
      meta_description: getMeta("description") || getMeta("og:description"),
    };
    sendBeacon("/widget/page-context", context);
    return context;
  }

  // ── Hack 3: Track event ──
  function trackEvent(eventType, extraData) {
    sendBeacon("/widget/track", {
      event_type: eventType,
      page_url: window.location.href,
      extra_data: extraData || null,
    });
  }

  // ── Conversion detection ──
  function checkConversionPage() {
    var url = window.location.href.toLowerCase();
    var title = document.title.toLowerCase();
    var isCV = url.indexOf("thank") !== -1 || url.indexOf("complete") !== -1 ||
      url.indexOf("success") !== -1 || url.indexOf("order-confirm") !== -1 ||
      title.indexOf("ありがとう") !== -1 || title.indexOf("注文完了") !== -1 ||
      title.indexOf("thank") !== -1 || title.indexOf("購入完了") !== -1;
    if (isCV) {
      trackEvent("conversion", {
        stored_session_id: localStorage.getItem(SESSION_KEY) || sessionStorage.getItem(SESSION_KEY),
        stored_timestamp: localStorage.getItem(TIMESTAMP_KEY) || sessionStorage.getItem(TIMESTAMP_KEY),
        referrer: document.referrer,
      });
    }
  }

  // ── Load widget config from API ──
  function loadConfig(callback) {
    fetch(API_BASE + "/widget/config/" + CLIENT_ID)
      .then(function (res) {
        if (!res.ok) throw new Error("Config not found");
        return res.json();
      })
      .then(callback)
      .catch(function (err) {
        console.warn("[AitherHub] Failed to load config:", err.message);
      });
  }

  // ── Create Shadow DOM container ──
  function createWidgetContainer() {
    var host = document.createElement("div");
    host.id = "aitherhub-widget-host";
    host.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;top:0;left:0;width:100%;height:100%;";
    document.body.appendChild(host);
    var shadow = host.attachShadow({ mode: "closed" });
    return shadow;
  }

  // ── SVG Icons ──
  var ICONS = {
    play: '<svg viewBox="0 0 24 24" fill="white"><path d="M8 5v14l11-7z"/></svg>',
    pause: '<svg viewBox="0 0 24 24" fill="white"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="white"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>',
    heart: '<svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>',
    heartFill: '<svg viewBox="0 0 24 24" fill="#FF2D55"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>',
    share: '<svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>',
    volumeOn: '<svg viewBox="0 0 24 24" fill="white"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>',
    volumeOff: '<svg viewBox="0 0 24 24" fill="white"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>',
    cart: '<svg viewBox="0 0 24 24" fill="white"><path d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zM1 2v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42c-.14 0-.25-.11-.25-.25l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49c.08-.14.12-.31.12-.48 0-.55-.45-1-1-1H5.21l-.94-2H1zm16 16c-1.1 0-1.99.9-1.99 2s.89 2 1.99 2 2-.9 2-2-.9-2-2-2z"/></svg>',
    chevronUp: '<svg viewBox="0 0 24 24" fill="white"><path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg>',
    chevronDown: '<svg viewBox="0 0 24 24" fill="white"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>',
  };

  // ── Build TikTok-Style Feed Widget ──
  function buildWidget(shadow, config) {
    var rawClips = config.clips || [];

    // Filter out clips without a valid clip_url (safety net)
    var clips = [];
    for (var i = 0; i < rawClips.length; i++) {
      if (rawClips[i].clip_url) clips.push(rawClips[i]);
    }
    if (clips.length === 0) return;

    var themeColor = config.theme_color || "#FF2D55";
    var position = config.position || "bottom-right";
    var ctaText = config.cta_text || "\u8CFC\u5165\u3059\u308B";
    var brandName = config.name || "";

    // ── CSS ──
    var style = document.createElement("style");
    style.textContent = '\
      @import url("https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700;900&display=swap");\
      * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }\
      \
      .ath-fab {\
        position: fixed;\
        ' + (position.indexOf("right") !== -1 ? "right: 16px;" : "left: 16px;") + '\
        ' + (position.indexOf("top") !== -1 ? "top: 16px;" : "bottom: 16px;") + '\
        width: 68px;\
        height: 68px;\
        border-radius: 50%;\
        background: #000;\
        cursor: pointer;\
        pointer-events: auto;\
        box-shadow: 0 4px 24px rgba(0,0,0,0.35);\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.3s;\
        animation: ath-pulse 2s infinite;\
        overflow: hidden;\
        z-index: 2147483647;\
        border: 3px solid rgba(255,255,255,0.9);\
      }\
      .ath-fab:hover { transform: scale(1.1); box-shadow: 0 6px 32px rgba(0,0,0,0.45); }\
      .ath-fab:active { transform: scale(0.95); }\
      .ath-fab img { width: 100%; height: 100%; object-fit: cover; border-radius: 50%; }\
      .ath-fab-icon { width: 28px; height: 28px; }\
      .ath-fab-video {\
        width: 100%; height: 100%;\
        object-fit: cover;\
        border-radius: 50%;\
        pointer-events: none;\
      }\
      .ath-fab-play-overlay {\
        position: absolute;\
        top: 50%; left: 50%;\
        transform: translate(-50%, -50%);\
        width: 24px; height: 24px;\
        opacity: 0.9;\
        pointer-events: none;\
        filter: drop-shadow(0 1px 3px rgba(0,0,0,0.5));\
      }\
      .ath-fab .ath-badge {\
        position: absolute; top: -4px; right: -4px;\
        min-width: 22px; height: 22px; padding: 0 6px;\
        background: #FF3B30; border-radius: 11px;\
        color: white; font-size: 11px; font-weight: 700;\
        display: flex; align-items: center; justify-content: center;\
        font-family: "Noto Sans JP", -apple-system, sans-serif;\
        border: 2px solid white;\
        z-index: 2;\
      }\
      @keyframes ath-pulse {\
        0%, 100% { box-shadow: 0 4px 24px rgba(0,0,0,0.35); }\
        50% { box-shadow: 0 4px 24px rgba(0,0,0,0.35), 0 0 0 10px ' + themeColor + '30; }\
      }\
      \
      /* ── Fullscreen Overlay ── */\
      .ath-overlay {\
        position: fixed;\
        top: 0; left: 0; right: 0; bottom: 0;\
        background: #000;\
        pointer-events: auto;\
        display: none;\
        z-index: 2147483647;\
        overflow: hidden;\
        font-family: "Noto Sans JP", -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;\
      }\
      .ath-overlay.active { display: block; }\
      \
      /* ── Feed Container ── */\
      .ath-feed {\
        position: absolute;\
        top: 0; left: 0; right: 0; bottom: 0;\
        overflow: hidden;\
      }\
      .ath-slide {\
        position: absolute;\
        top: 0; left: 0;\
        width: 100%; height: 100%;\
        will-change: transform;\
      }\
      .ath-slide-inner {\
        width: 100%; height: 100%;\
        position: relative;\
        background: #000;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
      }\
      \
      /* ── Video ── */\
      .ath-video {\
        width: 100%; height: 100%;\
        object-fit: contain;\
        background: #000;\
      }\
      \
      /* ── Header ── */\
      .ath-header {\
        position: absolute;\
        top: 0; left: 0; right: 0;\
        padding: 12px 16px;\
        padding-top: max(env(safe-area-inset-top, 12px), 12px);\
        display: flex;\
        align-items: center;\
        justify-content: space-between;\
        z-index: 20;\
        background: linear-gradient(to bottom, rgba(0,0,0,0.6) 0%, transparent 100%);\
        pointer-events: none;\
      }\
      .ath-header > * { pointer-events: auto; }\
      .ath-brand {\
        display: flex;\
        align-items: center;\
        gap: 8px;\
        color: white;\
        font-weight: 700;\
        font-size: 16px;\
        text-shadow: 0 1px 4px rgba(0,0,0,0.5);\
      }\
      .ath-brand-logo {\
        width: 32px; height: 32px;\
        border-radius: 50%;\
        object-fit: cover;\
        border: 2px solid rgba(255,255,255,0.3);\
      }\
      .ath-close-btn {\
        width: 40px; height: 40px;\
        border-radius: 50%;\
        background: rgba(255,255,255,0.15);\
        backdrop-filter: blur(8px);\
        -webkit-backdrop-filter: blur(8px);\
        border: none;\
        cursor: pointer;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        transition: background 0.2s;\
      }\
      .ath-close-btn:hover { background: rgba(255,255,255,0.25); }\
      .ath-close-btn svg { width: 22px; height: 22px; }\
      \
      /* ── Right-side Actions ── */\
      .ath-actions {\
        position: absolute;\
        right: 12px;\
        bottom: 180px;\
        display: flex;\
        flex-direction: column;\
        gap: 20px;\
        z-index: 15;\
        pointer-events: auto;\
      }\
      .ath-action-btn {\
        display: flex;\
        flex-direction: column;\
        align-items: center;\
        gap: 4px;\
        background: none;\
        border: none;\
        cursor: pointer;\
        color: white;\
        padding: 0;\
      }\
      .ath-action-icon {\
        width: 44px; height: 44px;\
        border-radius: 50%;\
        background: rgba(255,255,255,0.12);\
        backdrop-filter: blur(8px);\
        -webkit-backdrop-filter: blur(8px);\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        transition: transform 0.2s, background 0.2s;\
      }\
      .ath-action-icon:active { transform: scale(0.9); }\
      .ath-action-icon svg { width: 24px; height: 24px; }\
      .ath-action-icon.liked { background: rgba(255,45,85,0.3); }\
      .ath-action-label {\
        font-size: 10px;\
        font-weight: 500;\
        text-shadow: 0 1px 3px rgba(0,0,0,0.5);\
      }\
      \
      /* ── Video Counter ── */\
      .ath-counter {\
        position: absolute;\
        top: 60px; left: 50%;\
        transform: translateX(-50%);\
        color: rgba(255,255,255,0.7);\
        font-size: 13px;\
        font-weight: 500;\
        z-index: 15;\
        pointer-events: none;\
        text-shadow: 0 1px 3px rgba(0,0,0,0.5);\
      }\
      \
      /* ── Bottom Info + CTA ── */\
      .ath-bottom {\
        position: absolute;\
        bottom: 0; left: 0; right: 0;\
        padding: 16px;\
        padding-bottom: max(env(safe-area-inset-bottom, 16px), 16px);\
        background: linear-gradient(to top, rgba(0,0,0,0.8) 0%, transparent 100%);\
        z-index: 15;\
        pointer-events: none;\
      }\
      .ath-bottom > * { pointer-events: auto; }\
      .ath-cta-wrap { margin-bottom: 12px; }\
      .ath-cta {\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        gap: 8px;\
        width: 100%;\
        padding: 14px 20px;\
        border-radius: 12px;\
        background: ' + themeColor + ';\
        color: white;\
        font-size: 16px;\
        font-weight: 700;\
        border: none;\
        cursor: pointer;\
        transition: transform 0.2s, opacity 0.2s;\
        font-family: "Noto Sans JP", -apple-system, sans-serif;\
        box-shadow: 0 4px 16px ' + themeColor + '60;\
      }\
      .ath-cta:hover { opacity: 0.9; }\
      .ath-cta:active { transform: scale(0.98); }\
      .ath-cta svg { width: 20px; height: 20px; flex-shrink: 0; }\
      .ath-info { color: white; }\
      .ath-info-title {\
        font-size: 15px;\
        font-weight: 700;\
        margin-bottom: 4px;\
        text-shadow: 0 1px 4px rgba(0,0,0,0.5);\
        display: -webkit-box;\
        -webkit-line-clamp: 1;\
        -webkit-box-orient: vertical;\
        overflow: hidden;\
      }\
      .ath-info-desc {\
        font-size: 13px;\
        color: rgba(255,255,255,0.8);\
        line-height: 1.4;\
        display: -webkit-box;\
        -webkit-line-clamp: 2;\
        -webkit-box-orient: vertical;\
        overflow: hidden;\
        text-shadow: 0 1px 3px rgba(0,0,0,0.5);\
      }\
      \
      /* ── Progress Bar ── */\
      .ath-progress-wrap {\
        position: absolute;\
        bottom: 0; left: 0; right: 0;\
        height: 3px;\
        background: rgba(255,255,255,0.2);\
        z-index: 20;\
        cursor: pointer;\
        pointer-events: auto;\
      }\
      .ath-progress-bar {\
        height: 100%;\
        background: ' + themeColor + ';\
        width: 0%;\
        transition: width 0.1s linear;\
      }\
      \
      /* ── Play/Pause Indicator ── */\
      .ath-play-indicator {\
        position: absolute;\
        top: 50%; left: 50%;\
        transform: translate(-50%, -50%) scale(0);\
        width: 64px; height: 64px;\
        background: rgba(0,0,0,0.5);\
        border-radius: 50%;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        opacity: 0;\
        transition: transform 0.2s, opacity 0.2s;\
        pointer-events: none;\
        z-index: 10;\
      }\
      .ath-play-indicator.show {\
        transform: translate(-50%, -50%) scale(1);\
        opacity: 1;\
      }\
      .ath-play-indicator svg { width: 32px; height: 32px; }\
      \
      /* ── Speed Indicator ── */\
      .ath-speed-indicator {\
        position: absolute;\
        top: 50%; left: 50%;\
        transform: translate(-50%, -50%);\
        background: rgba(0,0,0,0.7);\
        color: white;\
        padding: 8px 16px;\
        border-radius: 20px;\
        font-size: 14px;\
        font-weight: 700;\
        z-index: 25;\
        opacity: 0;\
        transition: opacity 0.2s;\
        pointer-events: none;\
      }\
      .ath-speed-indicator.show { opacity: 1; }\
      \
      /* ── Swipe Hint ── */\
      .ath-swipe-hint {\
        position: absolute;\
        bottom: 200px; left: 50%;\
        transform: translateX(-50%);\
        color: rgba(255,255,255,0.8);\
        font-size: 13px;\
        font-weight: 500;\
        z-index: 15;\
        pointer-events: none;\
        animation: ath-hint-bounce 2s ease-in-out infinite;\
        text-align: center;\
      }\
      .ath-swipe-hint svg { width: 20px; height: 20px; margin: 0 auto 4px; display: block; opacity: 0.7; }\
      @keyframes ath-hint-bounce {\
        0%, 100% { transform: translateX(-50%) translateY(0); opacity: 0.7; }\
        50% { transform: translateX(-50%) translateY(-8px); opacity: 1; }\
      }\
      \
      /* ── Powered by ── */\
      .ath-powered {\
        position: absolute;\
        bottom: 6px; right: 12px;\
        font-size: 9px;\
        color: rgba(255,255,255,0.3);\
        z-index: 15;\
        pointer-events: none;\
      }\
    ';
    shadow.appendChild(style);

    // ── State ──
    var currentIndex = 0;
    var isOpen = false;
    var isMuted = true;
    var isLiked = {};
    var dragStartY = 0;
    var dragOffset = 0;
    var isDragging = false;
    var velocity = 0;
    var lastY = 0;
    var videoElements = {};
    var longPressTimer = null;
    var isSpeedUp = false;
    var hintShown = false;
    var fabVideo = null;

    // ── FAB (Floating Action Button) with Video Preview ──
    var fab = document.createElement("div");
    fab.className = "ath-fab";

    // Create video element for FAB bubble preview
    if (clips[0] && clips[0].clip_url) {
      fabVideo = document.createElement("video");
      fabVideo.className = "ath-fab-video";
      fabVideo.setAttribute("playsinline", "");
      fabVideo.setAttribute("webkit-playsinline", "");
      fabVideo.setAttribute("preload", "auto");
      fabVideo.setAttribute("loop", "");
      fabVideo.muted = true;
      fabVideo.src = clips[0].clip_url;
      fab.appendChild(fabVideo);

      // Small play icon overlay on the video bubble
      var fabPlayOverlay = document.createElement("div");
      fabPlayOverlay.className = "ath-fab-play-overlay";
      fabPlayOverlay.innerHTML = ICONS.play;
      fab.appendChild(fabPlayOverlay);

      // Auto-play the FAB video when it's ready
      fabVideo.addEventListener("loadeddata", function () {
        fabVideo.play().catch(function () { });
      });
      // Also try to play immediately (in case loadeddata already fired)
      try { fabVideo.play().catch(function () { }); } catch (e) { }
    } else if (clips[0] && clips[0].thumbnail_url) {
      var fabImg = document.createElement("img");
      fabImg.src = clips[0].thumbnail_url;
      fabImg.alt = "Watch video";
      fab.appendChild(fabImg);
    } else {
      fab.innerHTML = '<div class="ath-fab-icon">' + ICONS.play + '</div>';
    }

    if (clips.length > 1) {
      var badge = document.createElement("span");
      badge.className = "ath-badge";
      badge.textContent = clips.length;
      fab.appendChild(badge);
    }
    shadow.appendChild(fab);

    // ── Fullscreen Overlay ──
    var overlay = document.createElement("div");
    overlay.className = "ath-overlay";

    // Header
    var header = document.createElement("div");
    header.className = "ath-header";
    var brandEl = document.createElement("div");
    brandEl.className = "ath-brand";
    if (config.logo_url) {
      var logo = document.createElement("img");
      logo.className = "ath-brand-logo";
      logo.src = config.logo_url;
      logo.alt = brandName;
      brandEl.appendChild(logo);
    }
    var brandText = document.createElement("span");
    brandText.textContent = brandName;
    brandEl.appendChild(brandText);
    header.appendChild(brandEl);

    var closeBtn = document.createElement("button");
    closeBtn.className = "ath-close-btn";
    closeBtn.innerHTML = ICONS.close;
    header.appendChild(closeBtn);
    overlay.appendChild(header);

    // Feed container
    var feed = document.createElement("div");
    feed.className = "ath-feed";

    // Create slides for each clip
    clips.forEach(function (clip, index) {
      var slide = document.createElement("div");
      slide.className = "ath-slide";
      slide.setAttribute("data-index", index);

      var inner = document.createElement("div");
      inner.className = "ath-slide-inner";

      var video = document.createElement("video");
      video.className = "ath-video";
      video.setAttribute("playsinline", "");
      video.setAttribute("webkit-playsinline", "");
      video.setAttribute("preload", index <= 2 ? "auto" : "metadata");
      video.setAttribute("loop", "");
      video.muted = true;
      video.src = clip.clip_url;
      inner.appendChild(video);
      videoElements[index] = video;

      // Play/pause indicator
      var playIndicator = document.createElement("div");
      playIndicator.className = "ath-play-indicator";
      playIndicator.innerHTML = ICONS.play;
      inner.appendChild(playIndicator);

      slide.appendChild(inner);
      feed.appendChild(slide);
    });

    overlay.appendChild(feed);

    // Speed indicator
    var speedIndicator = document.createElement("div");
    speedIndicator.className = "ath-speed-indicator";
    speedIndicator.innerHTML = '&#9889; 2x \u901F\u5EA6';
    overlay.appendChild(speedIndicator);

    // Right-side action buttons
    var actions = document.createElement("div");
    actions.className = "ath-actions";

    // Like button
    var likeBtn = document.createElement("button");
    likeBtn.className = "ath-action-btn";
    likeBtn.innerHTML = '<div class="ath-action-icon">' + ICONS.heart + '</div><span class="ath-action-label">\u3044\u3044\u306D</span>';
    actions.appendChild(likeBtn);

    // Share button
    var shareBtn = document.createElement("button");
    shareBtn.className = "ath-action-btn";
    shareBtn.innerHTML = '<div class="ath-action-icon">' + ICONS.share + '</div><span class="ath-action-label">\u30B7\u30A7\u30A2</span>';
    actions.appendChild(shareBtn);

    // Mute button
    var muteBtn = document.createElement("button");
    muteBtn.className = "ath-action-btn";
    muteBtn.innerHTML = '<div class="ath-action-icon">' + ICONS.volumeOff + '</div><span class="ath-action-label">\u97F3\u58F0</span>';
    actions.appendChild(muteBtn);

    overlay.appendChild(actions);

    // Video counter
    var counter = document.createElement("div");
    counter.className = "ath-counter";
    overlay.appendChild(counter);

    // Bottom info area
    var bottom = document.createElement("div");
    bottom.className = "ath-bottom";

    var ctaWrap = document.createElement("div");
    ctaWrap.className = "ath-cta-wrap";
    var ctaBtn = document.createElement("button");
    ctaBtn.className = "ath-cta";
    ctaBtn.innerHTML = ICONS.cart + '<span>' + ctaText + '</span>';
    ctaWrap.appendChild(ctaBtn);
    bottom.appendChild(ctaWrap);

    var info = document.createElement("div");
    info.className = "ath-info";
    var infoTitle = document.createElement("div");
    infoTitle.className = "ath-info-title";
    var infoDesc = document.createElement("div");
    infoDesc.className = "ath-info-desc";
    info.appendChild(infoTitle);
    info.appendChild(infoDesc);
    bottom.appendChild(info);
    overlay.appendChild(bottom);

    // Progress bar
    var progressWrap = document.createElement("div");
    progressWrap.className = "ath-progress-wrap";
    var progressBar = document.createElement("div");
    progressBar.className = "ath-progress-bar";
    progressWrap.appendChild(progressBar);
    overlay.appendChild(progressWrap);

    // Swipe hint (shown once)
    var swipeHint = document.createElement("div");
    swipeHint.className = "ath-swipe-hint";
    swipeHint.innerHTML = ICONS.chevronUp + '\u4E0A\u306B\u30B9\u30EF\u30A4\u30D7';
    swipeHint.style.display = "none";
    overlay.appendChild(swipeHint);

    // Powered by
    var powered = document.createElement("div");
    powered.className = "ath-powered";
    powered.textContent = "Powered by AitherHub";
    overlay.appendChild(powered);

    shadow.appendChild(overlay);

    // ── Helper: Update slide positions ──
    function updateSlidePositions(animate) {
      var slides = feed.querySelectorAll(".ath-slide");
      for (var i = 0; i < slides.length; i++) {
        var idx = parseInt(slides[i].getAttribute("data-index"));
        var diff = idx - currentIndex;
        // Wrap for infinite loop
        if (clips.length > 2) {
          if (diff > clips.length / 2) diff -= clips.length;
          if (diff < -clips.length / 2) diff += clips.length;
        }
        // Only render nearby slides
        if (Math.abs(diff) > 2) {
          slides[i].style.display = "none";
          continue;
        }
        slides[i].style.display = "block";
        var translateY = diff * 100;
        var dragPx = isDragging ? dragOffset : 0;
        slides[i].style.transform = "translateY(calc(" + translateY + "% + " + dragPx + "px))";
        slides[i].style.transition = animate && !isDragging ? "transform 0.35s cubic-bezier(0.25, 0.46, 0.45, 0.94)" : "none";
      }
    }

    // ── Helper: Play current video ──
    function playCurrentVideo() {
      var video = videoElements[currentIndex];
      if (!video) return;

      // Pause all others
      Object.keys(videoElements).forEach(function (key) {
        if (parseInt(key) !== currentIndex) {
          videoElements[key].pause();
          videoElements[key].currentTime = 0;
        }
      });

      video.currentTime = 0;
      video.muted = isMuted;
      var playPromise = video.play();
      if (playPromise !== undefined) {
        playPromise.catch(function () {
          // If blocked, try muted
          video.muted = true;
          isMuted = true;
          updateMuteButton();
          video.play().catch(function () { });
        });
      }

      // Update UI
      var clip = clips[currentIndex];
      infoTitle.textContent = clip.product_name || clip.liver_name || brandName;
      infoDesc.textContent = clip.transcript_text ? clip.transcript_text.substring(0, 120) + (clip.transcript_text.length > 120 ? "..." : "") : "";
      counter.textContent = (currentIndex + 1) + " / " + clips.length;
      progressBar.style.width = "0%";

      // CTA text with product name
      if (clip.product_name) {
        ctaBtn.innerHTML = ICONS.cart + '<span>' + ctaText + ' \u00B7 ' + clip.product_name + '</span>';
      } else {
        ctaBtn.innerHTML = ICONS.cart + '<span>' + ctaText + '</span>';
      }

      // Track
      trackEvent("video_play", { clip_id: clip.clip_id, clip_index: currentIndex });

      // Show swipe hint on first video
      if (!hintShown && clips.length > 1) {
        hintShown = true;
        swipeHint.style.display = "block";
        setTimeout(function () { swipeHint.style.display = "none"; }, 3000);
      }
    }

    // ── Helper: Navigate ──
    function goToIndex(newIndex) {
      if (clips.length <= 1) return;
      currentIndex = ((newIndex % clips.length) + clips.length) % clips.length;
      updateSlidePositions(true);
      playCurrentVideo();
    }

    function goNext() { goToIndex(currentIndex + 1); }
    function goPrev() { goToIndex(currentIndex - 1); }

    // ── Helper: Update mute button ──
    function updateMuteButton() {
      var iconEl = muteBtn.querySelector(".ath-action-icon");
      iconEl.innerHTML = isMuted ? ICONS.volumeOff : ICONS.volumeOn;
    }

    // ── Video progress ──
    function onTimeUpdate() {
      var video = videoElements[currentIndex];
      if (video && video.duration) {
        progressBar.style.width = (video.currentTime / video.duration * 100) + "%";
      }
    }
    // Attach timeupdate to all videos
    clips.forEach(function (clip, index) {
      videoElements[index].addEventListener("timeupdate", function () {
        if (index === currentIndex) onTimeUpdate();
      });
    });

    // ── Progress bar seek ──
    progressWrap.addEventListener("click", function (e) {
      var video = videoElements[currentIndex];
      if (!video || !video.duration) return;
      var rect = progressWrap.getBoundingClientRect();
      var ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      video.currentTime = ratio * video.duration;
    });

    // ── Open/Close ──
    fab.addEventListener("click", function () {
      isOpen = true;
      overlay.classList.add("active");
      fab.style.display = "none";
      // Pause FAB video when overlay opens
      if (fabVideo) { try { fabVideo.pause(); } catch (e) { } }
      // Lock body scroll
      document.body.style.overflow = "hidden";
      document.documentElement.style.overflow = "hidden";
      currentIndex = 0;
      updateSlidePositions(false);
      playCurrentVideo();
      trackEvent("widget_open");
    });

    closeBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      closeOverlay();
    });

    function closeOverlay() {
      isOpen = false;
      overlay.classList.remove("active");
      fab.style.display = "flex";
      // Resume FAB video when overlay closes
      if (fabVideo) { try { fabVideo.play().catch(function () { }); } catch (e) { } }
      // Unlock body scroll
      document.body.style.overflow = "";
      document.documentElement.style.overflow = "";
      // Pause all videos
      Object.keys(videoElements).forEach(function (key) {
        videoElements[key].pause();
      });
    }

    // ── Touch Swipe (TikTok-style) ──
    feed.addEventListener("touchstart", function (e) {
      dragStartY = e.touches[0].clientY;
      lastY = dragStartY;
      isDragging = true;
      velocity = 0;
      dragOffset = 0;
    }, { passive: true });

    feed.addEventListener("touchmove", function (e) {
      if (!isDragging) return;
      e.preventDefault();
      var currentY = e.touches[0].clientY;
      dragOffset = currentY - dragStartY;
      velocity = currentY - lastY;
      lastY = currentY;
      updateSlidePositions(false);
    }, { passive: false });

    feed.addEventListener("touchend", function (e) {
      if (!isDragging) return;
      isDragging = false;
      var endY = e.changedTouches[0].clientY;
      var deltaY = dragStartY - endY;
      var screenH = window.innerHeight;
      var swipeRatio = Math.abs(deltaY) / screenH;

      // Snap if dragged > 15% or fast velocity
      if (swipeRatio > 0.15 || Math.abs(velocity) > 5) {
        if (deltaY > 0) {
          goNext();
        } else {
          goPrev();
        }
      } else {
        // Snap back
        updateSlidePositions(true);
      }
      dragOffset = 0;
    }, { passive: true });

    // ── Tap to play/pause (left half) & long-press 2x speed (right half) ──
    feed.addEventListener("click", function (e) {
      if (isSpeedUp) return;
      // Ignore if clicking on buttons
      if (e.target.closest && (e.target.closest(".ath-action-btn") || e.target.closest(".ath-cta") || e.target.closest(".ath-close-btn"))) return;

      var video = videoElements[currentIndex];
      if (!video) return;

      var feedRect = feed.getBoundingClientRect();
      var clickX = e.clientX - feedRect.left;
      var isLeftHalf = clickX < feedRect.width / 2;

      if (isLeftHalf && isMuted) {
        // First tap on left: unmute
        isMuted = false;
        video.muted = false;
        updateMuteButton();
        showPlayIndicator(ICONS.volumeOn);
        return;
      }

      // Toggle play/pause
      if (video.paused) {
        video.play().catch(function () { });
        showPlayIndicator(ICONS.play);
      } else {
        video.pause();
        showPlayIndicator(ICONS.pause);
      }
    });

    // Long press for 2x speed
    feed.addEventListener("touchstart", function (e) {
      var feedRect = feed.getBoundingClientRect();
      var touchX = e.touches[0].clientX - feedRect.left;
      if (touchX > feedRect.width / 2) {
        longPressTimer = setTimeout(function () {
          var video = videoElements[currentIndex];
          if (video) {
            video.playbackRate = 2.0;
            isSpeedUp = true;
            speedIndicator.classList.add("show");
          }
        }, 300);
      }
    }, { passive: true });

    feed.addEventListener("touchend", function () {
      if (longPressTimer) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
      }
      if (isSpeedUp) {
        var video = videoElements[currentIndex];
        if (video) video.playbackRate = 1.0;
        isSpeedUp = false;
        speedIndicator.classList.remove("show");
      }
    }, { passive: true });

    // ── Play indicator animation ──
    function showPlayIndicator(iconHtml) {
      var slide = feed.querySelector('.ath-slide[data-index="' + currentIndex + '"]');
      if (!slide) return;
      var indicator = slide.querySelector(".ath-play-indicator");
      if (!indicator) return;
      indicator.innerHTML = iconHtml;
      indicator.classList.add("show");
      setTimeout(function () { indicator.classList.remove("show"); }, 600);
    }

    // ── Mouse wheel scroll ──
    var lastScrollTime = 0;
    overlay.addEventListener("wheel", function (e) {
      var now = Date.now();
      if (now - lastScrollTime < 500) return;
      if (e.deltaY > 50) { goNext(); lastScrollTime = now; }
      else if (e.deltaY < -50) { goPrev(); lastScrollTime = now; }
    });

    // ── Keyboard navigation ──
    document.addEventListener("keydown", function (e) {
      if (!isOpen) return;
      if (e.key === "Escape") closeOverlay();
      if (e.key === "ArrowUp" || e.key === "k") goPrev();
      if (e.key === "ArrowDown" || e.key === "j") goNext();
    });

    // ── Action: Like ──
    likeBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var clipId = clips[currentIndex].clip_id;
      isLiked[clipId] = !isLiked[clipId];
      var iconEl = likeBtn.querySelector(".ath-action-icon");
      if (isLiked[clipId]) {
        iconEl.innerHTML = ICONS.heartFill;
        iconEl.classList.add("liked");
      } else {
        iconEl.innerHTML = ICONS.heart;
        iconEl.classList.remove("liked");
      }
      trackEvent("like", { clip_id: clipId, liked: isLiked[clipId] });
    });

    // ── Action: Share ──
    shareBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];
      var shareUrl = window.location.href;
      var shareTitle = clip.product_name || brandName;
      if (navigator.share) {
        navigator.share({ title: shareTitle, url: shareUrl }).catch(function () { });
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(shareUrl);
        // Visual feedback
        var label = shareBtn.querySelector(".ath-action-label");
        label.textContent = "\u30B3\u30D4\u30FC!";
        setTimeout(function () { label.textContent = "\u30B7\u30A7\u30A2"; }, 2000);
      }
      trackEvent("share", { clip_id: clip.clip_id });
    });

    // ── Action: Mute/Unmute ──
    muteBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      isMuted = !isMuted;
      var video = videoElements[currentIndex];
      if (video) video.muted = isMuted;
      updateMuteButton();
    });

    // ── Hack 2: CTA Click ──
    ctaBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];
      trackEvent("cta_click", {
        clip_id: clip.clip_id,
        product_name: clip.product_name,
        video_time: videoElements[currentIndex] ? videoElements[currentIndex].currentTime : 0,
      });

      // Strategy 1: DOM manipulation (add to cart)
      if (config.cart_selector) {
        try {
          var cartBtn = document.querySelector(config.cart_selector);
          if (cartBtn) {
            cartBtn.click();
            ctaBtn.innerHTML = '<span>&#10003; \u30AB\u30FC\u30C8\u306B\u8FFD\u52A0\u3057\u307E\u3057\u305F</span>';
            setTimeout(function () {
              var clip2 = clips[currentIndex];
              ctaBtn.innerHTML = ICONS.cart + '<span>' + ctaText + (clip2.product_name ? ' \u00B7 ' + clip2.product_name : '') + '</span>';
            }, 2000);
            return;
          }
        } catch (err) { }
      }

      // Strategy 2: Navigate to product URL
      var targetUrl = config.cta_url_template
        ? config.cta_url_template.replace("{product}", encodeURIComponent(clip.product_name || ""))
        : (clip.product_url || window.location.href);
      if (targetUrl && targetUrl !== window.location.href) {
        window.location.href = targetUrl;
      }
    });
  }

  // ── Initialize ──
  function init() {
    scrapePageContext();
    trackEvent("page_view", { title: document.title, referrer: document.referrer });
    checkConversionPage();
    loadConfig(function (config) {
      var shadow = createWidgetContainer();
      buildWidget(shadow, config);
    });
  }

  // ── Wait for DOM ready ──
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
