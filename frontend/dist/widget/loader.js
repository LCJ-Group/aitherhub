/**
 * AitherHub Widget Loader v3.3 — TikTok-Style Fullscreen Feed + Product Card + Subtitles
 *
 * GTM経由で配信される軽量エントリーポイント。
 * 先方のECサイトに1行のタグを追加するだけで、
 * TikTok風フルスクリーン縦型動画フィード + 商品カード + CTAボタンを展開する。
 *
 * Usage (GTM Custom HTML):
 *   <script src="https://www.aitherhub.com/widget/loader.js" data-client-id="YOUR_ID" async></script>
 *
 * Features:
 *   - Floating bubble icon (bottom-right) with pulse animation
 *   - Tap → fullscreen TikTok-style vertical video feed overlay
 *   - Swipe up/down (touch) or scroll/arrow keys to navigate videos
 *   - Right-side action buttons (like, share, mute)
 *   - Bottom product info card with image, name, price
 *   - Dual CTA: "カートに入れる" + "購入する" buttons
 *   - UTM parameter tracking for GA4 conversion attribution
 *   - SaaS: brand name/logo/theme color from API config
 *   - Hack 1: DOM auto-parse (page scraping)
 *   - Hack 2: In-video CTA action
 *   - Hack 3: Shadow Tracking (localStorage session)
 *
 * v2.3 Changes:
 *   - Product card with image, name, price overlay on fullscreen video
 *   - Dual CTA buttons: "カートに入れる" + "購入する"
 *   - UTM parameters (utm_source=aitherhub, utm_medium=widget, utm_campaign=video_cta)
 *   - product_click / add_to_cart / purchase_click tracking events
 *   - Graceful fallback: no product info → single CTA button (v2.2 behavior)
 *
 * v2.4 Changes:
 *   - Real-time subtitle display synced to video playback
 *   - Priority: clip.captions (precise JSON) > clip.transcript_text (estimated timing)
 *   - TikTok-style subtitle overlay with blur background
 *   - Auto-hide when no caption available
 *
 * v2.5 Changes:
 *   - Product name truncated to 1 line with ellipsis in product card
 *   - CTA button shows only action text (e.g. "購入する"), no product name
 *   - Info title avoids duplicating product name when product card is visible
 *   - Info description limited to 1 line
 *
 * v2.6 Changes:
 *   - "Tap to unmute" hint overlay on first open (auto-hides after 4s)
 *   - Mute button pulse animation when muted to draw attention
 *   - localStorage remembers user sound preference for next visit
 *   - Returning users with sound ON get auto-unmute attempt
 *
 * v2.10 – OGP product preview: fetch real product info (image, title, description, price) from product page via server-side OGP API
 * v2.9 – Product detail panel: tap product card or CTA → slide-up detail panel
 *         with large product image, full name, price, description, and action buttons.
 *         product_detail_view tracking event added to conversion funnel.
 *         Design balance: compact product card (48px img, 12px name, 36px CTA buttons).
 *
 * v2.8 – 2-tier CTA system: product_url only → "商品を見る" single button;
 *         product_url + product_cart_url → dual "カートに入れる" + "購入する" buttons;
 *         no URLs → CTA hidden. Tracking: product_click / add_to_cart / purchase_click.
 * v2.7 – Changes:
 *   - CTA buttons hidden when clip has no product_url/product_cart_url (no dead links)
 *   - Video counter no longer shows total count (prevents "I've seen enough" effect)
 */
(function () {
  "use strict";
  console.log("[AitherHub] IIFE START v3.3");

  // ── Prevent double-loading ──
  if (window.__AITHERHUB_WIDGET_LOADED) { console.log("[AitherHub] SKIPPED: already loaded"); return; }
  window.__AITHERHUB_WIDGET_LOADED = true;
  console.log("[AitherHub] First load, proceeding...");

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

  // ── UTM helper ──
  function addUtmParams(url, clipId, action) {
    if (!url) return url;
    var sep = url.indexOf("?") === -1 ? "?" : "&";
    return url + sep +
      "utm_source=aitherhub&utm_medium=widget&utm_campaign=video_cta" +
      "&utm_content=" + encodeURIComponent(clipId || "") +
      "&utm_term=" + encodeURIComponent(action || "click");
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
  function loadConfig(callback, attempt) {
    attempt = attempt || 1;
    var MAX_RETRIES = 3;
    var RETRY_DELAYS = [3000, 6000, 12000]; // 3s, 6s, 12s
    fetch(API_BASE + "/widget/config/" + CLIENT_ID)
      .then(function (res) {
        if (!res.ok) throw new Error("Config HTTP " + res.status);
        return res.json();
      })
      .then(callback)
      .catch(function (err) {
        if (attempt < MAX_RETRIES) {
          console.warn("[AitherHub] Config load attempt " + attempt + " failed (" + err.message + "), retrying in " + (RETRY_DELAYS[attempt - 1] / 1000) + "s...");
          setTimeout(function () { loadConfig(callback, attempt + 1); }, RETRY_DELAYS[attempt - 1]);
        } else {
          console.warn("[AitherHub] Failed to load config after " + MAX_RETRIES + " attempts:", err.message);
        }
      });
  }

  // ── Create Shadow DOM container ──
  function createWidgetContainer() {
    var host = document.createElement("div");
    host.id = "aitherhub-widget-host";
    host.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;top:0;left:0;width:100%;height:100%;";
    document.body.appendChild(host);
    var shadow = host.attachShadow({ mode: "open" });
    // Load Google Fonts via <link> instead of @import (more reliable in Shadow DOM)
    var fontLink = document.createElement("link");
    fontLink.rel = "stylesheet";
    fontLink.href = "https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700;900&display=swap";
    shadow.appendChild(fontLink);
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
    bag: '<svg viewBox="0 0 24 24" fill="white"><path d="M18 6h-2c0-2.21-1.79-4-4-4S8 3.79 8 6H6c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-6-2c1.1 0 2 .9 2 2h-4c0-1.1.9-2 2-2zm6 16H6V8h2v2c0 .55.45 1 1 1s1-.45 1-1V8h4v2c0 .55.45 1 1 1s1-.45 1-1V8h2v12z"/></svg>',
    chevronUp: '<svg viewBox="0 0 24 24" fill="white"><path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg>',
    chevronDown: '<svg viewBox="0 0 24 24" fill="white"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>',
  };

  // ── Build TikTok-Style Feed Widget ──
  function buildWidget(shadow, config) {
    var rawClips = config.clips || [];
    // Filter out clips without a valid clip_url (safety net)
    var clips = [];
    for (var ci = 0; ci < rawClips.length; ci++) {
      if (rawClips[ci].clip_url) clips.push(rawClips[ci]);
    }
    console.log("[AitherHub] buildWidget: rawClips=", rawClips.length, "filtered clips=", clips.length);
    if (clips.length === 0) { console.warn("[AitherHub] No clips with clip_url, aborting widget"); return; }

    var themeColor = config.theme_color || "#FF2D55";
    var position = config.position || "bottom-right";
    var ctaText = config.cta_text || "購入する";
    var brandName = config.name || "";

    // ── CSS ──
    var style = document.createElement("style");
    style.textContent = '\
      /* Font loaded via <link> in createWidgetContainer() */\
      * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }\
      \
      .ath-fab {\
        position: fixed;\
        ' + (position.indexOf("right") !== -1 ? "right: 16px;" : "left: 16px;") + '\
        ' + (position.indexOf("top") !== -1 ? "top: 16px;" : "bottom: 16px;") + '\
        width: 60px;\
        height: 60px;\
        border-radius: 50%;\
        background: ' + themeColor + ';\
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
      .ath-fab video { width: 100%; height: 100%; object-fit: cover; border-radius: 50%; pointer-events: none; }\
      .ath-fab-play-overlay {\
        position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);\
        width: 20px; height: 20px; opacity: 0.9; pointer-events: none;\
        filter: drop-shadow(0 1px 3px rgba(0,0,0,0.5));\
      }\
      .ath-badge {\
        position: absolute; top: -4px; right: -4px;\
        background: #FF3B30; color: white; font-size: 10px; font-weight: 700;\
        min-width: 18px; height: 18px; border-radius: 9px;\
        display: flex; align-items: center; justify-content: center;\
        padding: 0 4px; border: 2px solid white;\
      }\
      @keyframes ath-pulse {\
        0%, 100% { box-shadow: 0 4px 24px rgba(0,0,0,0.35); }\
        50% { box-shadow: 0 4px 24px rgba(0,0,0,0.35), 0 0 0 8px ' + themeColor + '33; }\
      }\
      \
      /* ── Fullscreen Overlay ── */\
      .ath-overlay {\
        position: fixed;\
        top: 0; left: 0; right: 0; bottom: 0;\
        background: #000;\
        z-index: 2147483647;\
        pointer-events: auto;\
        display: none;\
        font-family: "Noto Sans JP", -apple-system, BlinkMacSystemFont, sans-serif;\
        -webkit-font-smoothing: antialiased;\
        user-select: none;\
        -webkit-user-select: none;\
      }\
      .ath-overlay.active { display: block; }\
      \
      /* ── Header ── */\
      .ath-header {\
        position: absolute;\
        top: 0; left: 0; right: 0;\
        height: 56px;\
        display: flex;\
        align-items: center;\
        justify-content: space-between;\
        padding: 0 16px;\
        z-index: 20;\
        background: linear-gradient(to bottom, rgba(0,0,0,0.5), transparent);\
      }\
      .ath-brand {\
        display: flex;\
        align-items: center;\
        gap: 8px;\
        color: white;\
        font-size: 14px;\
        font-weight: 700;\
      }\
      .ath-brand-logo { width: 28px; height: 28px; border-radius: 50%; object-fit: cover; }\
      .ath-close-btn {\
        width: 36px; height: 36px;\
        background: rgba(255,255,255,0.15);\
        border: none; border-radius: 50%;\
        cursor: pointer;\
        display: flex; align-items: center; justify-content: center;\
        backdrop-filter: blur(8px);\
        -webkit-backdrop-filter: blur(8px);\
        transition: background 0.2s;\
      }\
      .ath-close-btn:hover { background: rgba(255,255,255,0.25); }\
      .ath-close-btn svg { width: 20px; height: 20px; }\
      \
      /* ── Feed ── */\
      .ath-feed {\
        position: absolute;\
        top: 0; left: 0; right: 0; bottom: 0;\
        overflow: hidden;\
        z-index: 5;\
      }\
      .ath-slide {\
        position: absolute;\
        top: 0; left: 0;\
        width: 100%; height: 100%;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        will-change: transform;\
      }\
      .ath-slide-inner {\
        position: relative;\
        width: 100%; height: 100%;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        background: #000;\
      }\
      .ath-video {\
        width: 100%; height: 100%;\
        object-fit: contain;\
        background: #000;\
      }\
      \
      /* ── Play/Pause indicator ── */\
      .ath-play-indicator {\
        position: absolute;\
        top: 50%; left: 50%;\
        transform: translate(-50%, -50%) scale(0.5);\
        width: 64px; height: 64px;\
        background: rgba(0,0,0,0.5);\
        border-radius: 50%;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        opacity: 0;\
        transition: opacity 0.2s, transform 0.2s;\
        z-index: 15;\
        pointer-events: none;\
      }\
      .ath-play-indicator svg { width: 32px; height: 32px; }\
      .ath-play-indicator.show { opacity: 1; transform: translate(-50%, -50%) scale(1); }\
      \
      /* ── Right-side actions ── */\
      .ath-actions {\
        position: absolute;\
        right: 12px;\
        bottom: 260px;\
        display: flex;\
        flex-direction: column;\
        gap: 16px;\
        z-index: 20;\
        pointer-events: auto;\
      }\
      .ath-action-btn {\
        display: flex;\
        flex-direction: column;\
        align-items: center;\
        gap: 2px;\
        background: none;\
        border: none;\
        cursor: pointer;\
        color: white;\
        filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));\
      }\
      .ath-action-icon {\
        width: 36px; height: 36px;\
        display: flex; align-items: center; justify-content: center;\
        transition: transform 0.2s;\
      }\
      .ath-action-icon svg { width: 28px; height: 28px; }\
      .ath-action-icon.liked { animation: ath-like-pop 0.4s ease; }\
      @keyframes ath-like-pop {\
        0% { transform: scale(1); }\
        50% { transform: scale(1.4); }\
        100% { transform: scale(1); }\
      }\
      .ath-action-label {\
        font-size: 10px;\
        color: rgba(255,255,255,0.8);\
      }\
      \
      /* ── Video counter ── */\
      .ath-counter {\
        position: absolute;\
        top: 60px; right: 16px;\
        color: rgba(255,255,255,0.7);\
        font-size: 12px;\
        font-weight: 500;\
        z-index: 20;\
        pointer-events: none;\
      }\
      \
      /* ── Bottom area (product card + CTA) ── */\
      .ath-bottom {\
        position: absolute;\
        bottom: 0; left: 0; right: 0;\
        padding: 0 12px 20px;\
        z-index: 20;\
        pointer-events: auto;\
        background: linear-gradient(to top, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0.4) 60%, transparent 100%);\
      }\
      \
      /* ── Product Card ── */\
      .ath-product-card {\
        display: flex;\
        align-items: center;\
        gap: 8px;\
        background: rgba(255,255,255,0.12);\
        backdrop-filter: blur(12px);\
        -webkit-backdrop-filter: blur(12px);\
        border-radius: 10px;\
        padding: 8px;\
        margin-bottom: 8px;\
        border: 1px solid rgba(255,255,255,0.15);\
        cursor: pointer;\
        transition: background 0.2s;\
      }\
      .ath-product-card:active { background: rgba(255,255,255,0.2); }\
      .ath-product-img {\
        width: 48px; height: 48px;\
        border-radius: 8px;\
        object-fit: cover;\
        flex-shrink: 0;\
        background: rgba(255,255,255,0.1);\
      }\
      .ath-product-info {\
        flex: 1;\
        min-width: 0;\
      }\
      .ath-product-name {\
        color: white;\
        font-size: 12px;\
        font-weight: 600;\
        line-height: 1.3;\
        white-space: nowrap;\
        overflow: hidden;\
        text-overflow: ellipsis;\
        max-width: 180px;\
      }\
      .ath-product-price {\
        color: ' + themeColor + ';\
        font-size: 14px;\
        font-weight: 900;\
        margin-top: 2px;\
      }\
      \
      /* ── CTA Buttons ── */\
      .ath-cta-wrap {\
        display: flex;\
        gap: 8px;\
        margin-bottom: 8px;\
      }\
      .ath-cta {\
        flex: 1;\
        height: 36px;\
        border: none;\
        border-radius: 18px;\
        font-size: 13px;\
        font-weight: 700;\
        cursor: pointer;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        gap: 6px;\
        transition: transform 0.15s, opacity 0.15s;\
        pointer-events: auto;\
      }\
      .ath-cta:active { transform: scale(0.96); }\
      .ath-cta svg { width: 16px; height: 16px; flex-shrink: 0; }\
      .ath-cta span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\
      .ath-cta-cart {\
        background: rgba(255,255,255,0.2);\
        color: white;\
        border: 1px solid rgba(255,255,255,0.3);\
        backdrop-filter: blur(8px);\
        -webkit-backdrop-filter: blur(8px);\
      }\
      .ath-cta-cart:hover { background: rgba(255,255,255,0.3); }\
      .ath-cta-buy {\
        background: ' + themeColor + ';\
        color: white;\
        box-shadow: 0 4px 16px ' + themeColor + '66;\
      }\
      .ath-cta-buy:hover { opacity: 0.9; }\
      .ath-cta-single {\
        background: ' + themeColor + ';\
        color: white;\
        box-shadow: 0 4px 16px ' + themeColor + '66;\
      }\
      .ath-cta-single:hover { opacity: 0.9; }\
      \
      /* ── Info (fallback when no product) ── */\
      .ath-info {\
        padding: 4px 0;\
      }\
      .ath-info-title {\
        color: white;\
        font-size: 14px;\
        font-weight: 700;\
        text-shadow: 0 1px 4px rgba(0,0,0,0.5);\
        white-space: nowrap;\
        overflow: hidden;\
        text-overflow: ellipsis;\
      }\
      .ath-info-desc {\
        color: rgba(255,255,255,0.7);\
        font-size: 12px;\
        margin-top: 4px;\
        display: -webkit-box;\
        -webkit-line-clamp: 1;\
        -webkit-box-orient: vertical;\
        overflow: hidden;\
        text-shadow: 0 1px 3px rgba(0,0,0,0.4);\
      }\
      \
      /* ── Progress bar ── */\
      .ath-progress-wrap {\
        position: absolute;\
        bottom: 0; left: 0; right: 0;\
        height: 3px;\
        background: rgba(255,255,255,0.2);\
        z-index: 25;\
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
      /* ── Speed indicator ── */\
      .ath-speed-indicator {\
        position: absolute;\
        top: 70px; left: 50%;\
        transform: translateX(-50%);\
        background: rgba(0,0,0,0.6);\
        color: white;\
        padding: 6px 14px;\
        border-radius: 20px;\
        font-size: 13px;\
        font-weight: 700;\
        z-index: 30;\
        pointer-events: none;\
        display: none;\
      }\
      .ath-speed-indicator.show { display: flex; align-items: center; gap: 6px; }\
      \
      /* ── Sound hint overlay ── */\
      .ath-sound-hint {\
        position: absolute;\
        top: 0; left: 0; right: 0; bottom: 0;\
        z-index: 35;\
        display: flex;\
        flex-direction: column;\
        align-items: center;\
        justify-content: center;\
        background: rgba(0,0,0,0.4);\
        pointer-events: auto;\
        cursor: pointer;\
        transition: opacity 0.3s ease;\
      }\
      .ath-sound-hint.hidden { opacity: 0; pointer-events: none; }\
      .ath-sound-hint-icon {\
        width: 64px; height: 64px;\
        background: rgba(255,255,255,0.2);\
        border-radius: 50%;\
        display: flex; align-items: center; justify-content: center;\
        backdrop-filter: blur(8px);\
        -webkit-backdrop-filter: blur(8px);\
        animation: ath-sound-pulse 1.5s ease-in-out infinite;\
      }\
      .ath-sound-hint-icon svg { width: 32px; height: 32px; }\
      .ath-sound-hint-text {\
        color: white;\
        font-size: 14px;\
        font-weight: 600;\
        margin-top: 12px;\
        text-shadow: 0 1px 4px rgba(0,0,0,0.5);\
      }\
      @keyframes ath-sound-pulse {\
        0%, 100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(255,255,255,0.3); }\
        50% { transform: scale(1.08); box-shadow: 0 0 0 12px rgba(255,255,255,0); }\
      }\
      \
      /* ── Mute button pulse when muted ── */\
      .ath-action-btn.ath-mute-pulse .ath-action-icon {\
        animation: ath-mute-glow 2s ease-in-out infinite;\
      }\
      @keyframes ath-mute-glow {\
        0%, 100% { transform: scale(1); filter: drop-shadow(0 0 0 transparent); }\
        50% { transform: scale(1.15); filter: drop-shadow(0 0 6px rgba(255,255,255,0.6)); }\
      }\
      \
      /* ── Swipe hint ── */\
      .ath-swipe-hint {\
        position: absolute;\
        bottom: 100px; left: 50%;\
        transform: translateX(-50%);\
        color: rgba(255,255,255,0.7);\
        font-size: 13px;\
        z-index: 30;\
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
      /* ── Subtitle overlay ── */\
      .ath-subtitle {\
        position: absolute;\
        bottom: 260px;\
        left: 12px;\
        right: 60px;\
        z-index: 18;\
        pointer-events: none;\
        text-align: center;\
        transition: opacity 0.25s ease;\
      }\
      .ath-subtitle-text {\
        display: inline-block;\
        max-width: 100%;\
        padding: 6px 14px;\
        border-radius: 8px;\
        background: rgba(0,0,0,0.55);\
        backdrop-filter: blur(6px);\
        -webkit-backdrop-filter: blur(6px);\
        color: #fff;\
        font-size: 15px;\
        font-weight: 600;\
        line-height: 1.5;\
        text-shadow: 0 1px 3px rgba(0,0,0,0.6);\
        word-break: break-word;\
        white-space: pre-wrap;\
      }\
      .ath-subtitle.hidden { opacity: 0; }\
      .ath-subtitle.visible { opacity: 1; }\
      \
      /* ── Sound confirm popup ── */\
      .ath-sound-confirm {\
        position: absolute;\
        top: 0; left: 0; right: 0; bottom: 0;\
        z-index: 60;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        background: rgba(0,0,0,0.6);\
        backdrop-filter: blur(4px);\
        -webkit-backdrop-filter: blur(4px);\
        opacity: 0;\
        pointer-events: none;\
        transition: opacity 0.25s ease;\
      }\
      .ath-sound-confirm.visible { opacity: 1; pointer-events: auto; }\
      .ath-sound-confirm-box {\
        background: rgba(30,30,30,0.95);\
        border-radius: 16px;\
        padding: 24px 28px;\
        text-align: center;\
        max-width: 280px;\
        width: 80%;\
        box-shadow: 0 8px 32px rgba(0,0,0,0.4);\
        transform: scale(0.9);\
        transition: transform 0.25s ease;\
      }\
      .ath-sound-confirm.visible .ath-sound-confirm-box { transform: scale(1); }\
      .ath-sound-confirm-icon {\
        width: 56px; height: 56px;\
        margin: 0 auto 16px;\
        background: rgba(255,255,255,0.1);\
        border-radius: 50%;\
        display: flex; align-items: center; justify-content: center;\
      }\
      .ath-sound-confirm-icon svg { width: 28px; height: 28px; }\
      .ath-sound-confirm-title {\
        color: white;\
        font-size: 16px;\
        font-weight: 700;\
        margin-bottom: 8px;\
      }\
      .ath-sound-confirm-desc {\
        color: rgba(255,255,255,0.6);\
        font-size: 12px;\
        margin-bottom: 20px;\
        line-height: 1.4;\
      }\
      .ath-sound-confirm-btns {\
        display: flex;\
        gap: 10px;\
      }\
      .ath-sound-confirm-btn {\
        flex: 1;\
        padding: 12px 0;\
        border-radius: 10px;\
        border: none;\
        font-size: 14px;\
        font-weight: 600;\
        cursor: pointer;\
        transition: transform 0.15s ease, opacity 0.15s ease;\
      }\
      .ath-sound-confirm-btn:active { transform: scale(0.95); }\
      .ath-sound-confirm-btn.cancel {\
        background: rgba(255,255,255,0.15);\
        color: rgba(255,255,255,0.8);\
      }\
      .ath-sound-confirm-btn.confirm {\
        background: #FF2D55;\
        color: white;\
      }\
      \
      /* ── Loading Spinner ── */\
      .ath-loading-spinner {\
        position: absolute;\
        top: 0; left: 0; right: 0; bottom: 0;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        z-index: 5;\
        pointer-events: none;\
      }\
      .ath-spinner-ring {\
        width: 40px; height: 40px;\
        border: 3px solid rgba(255,255,255,0.2);\
        border-top-color: white;\
        border-radius: 50%;\
        animation: ath-spin 0.8s linear infinite;\
      }\
      @keyframes ath-spin {\
        to { transform: rotate(360deg); }\
      }\
      \
      /* ── Product Detail Panel ── */\
      .ath-detail-overlay {\
        position: absolute;\
        top: 0; left: 0; right: 0; bottom: 0;\
        background: rgba(0,0,0,0.6);\
        z-index: 40;\
        display: none;\
        pointer-events: auto;\
      }\
      .ath-detail-overlay.active { display: block; }\
      .ath-detail-panel {\
        position: absolute;\
        bottom: 0; left: 0; right: 0;\
        max-height: 75vh;\
        background: #1a1a1a;\
        border-radius: 16px 16px 0 0;\
        padding: 0;\
        transform: translateY(100%);\
        transition: transform 0.35s cubic-bezier(0.25, 0.46, 0.45, 0.94);\
        overflow-y: auto;\
        -webkit-overflow-scrolling: touch;\
      }\
      .ath-detail-overlay.active .ath-detail-panel { transform: translateY(0); }\
      .ath-detail-handle {\
        width: 36px; height: 4px;\
        background: rgba(255,255,255,0.3);\
        border-radius: 2px;\
        margin: 12px auto 8px;\
      }\
      .ath-detail-close {\
        position: absolute;\
        top: 12px; right: 12px;\
        width: 32px; height: 32px;\
        background: rgba(255,255,255,0.15);\
        border: none; border-radius: 50%;\
        cursor: pointer;\
        display: flex; align-items: center; justify-content: center;\
        z-index: 2;\
      }\
      .ath-detail-close svg { width: 18px; height: 18px; }\
      .ath-detail-close:active { background: rgba(255,255,255,0.25); }\
      .ath-detail-img {\
        width: 100%;\
        max-height: 280px;\
        object-fit: contain;\
        background: #111;\
        display: block;\
      }\
      .ath-detail-loading {\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        padding: 40px 0;\
        color: rgba(255,255,255,0.5);\
        font-size: 13px;\
        gap: 8px;\
      }\
      .ath-detail-loading.hidden { display: none; }\
      .ath-detail-spinner {\
        width: 20px; height: 20px;\
        border: 2px solid rgba(255,255,255,0.2);\
        border-top-color: ' + themeColor + ';\
        border-radius: 50%;\
        animation: ath-spin 0.8s linear infinite;\
      }\
      @keyframes ath-spin { to { transform: rotate(360deg); } }\
      .ath-detail-site {\
        display: flex;\
        align-items: center;\
        gap: 6px;\
        margin-bottom: 10px;\
        color: rgba(255,255,255,0.5);\
        font-size: 11px;\
      }\
      .ath-detail-favicon {\
        width: 14px; height: 14px;\
        border-radius: 2px;\
      }\
      .ath-detail-body {\
        padding: 16px;\
      }\
      .ath-detail-name {\
        color: white;\
        font-size: 16px;\
        font-weight: 700;\
        line-height: 1.4;\
        margin-bottom: 6px;\
      }\
      .ath-detail-price {\
        color: ' + themeColor + ';\
        font-size: 20px;\
        font-weight: 900;\
        margin-bottom: 12px;\
      }\
      .ath-detail-desc {\
        color: rgba(255,255,255,0.6);\
        font-size: 13px;\
        line-height: 1.6;\
        margin-bottom: 16px;\
      }\
      .ath-detail-actions {\
        display: flex;\
        gap: 10px;\
      }\
      .ath-detail-btn {\
        flex: 1;\
        height: 44px;\
        border: none;\
        border-radius: 22px;\
        font-size: 14px;\
        font-weight: 700;\
        cursor: pointer;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        gap: 6px;\
        transition: transform 0.15s, opacity 0.15s;\
      }\
      .ath-detail-btn:active { transform: scale(0.96); }\
      .ath-detail-btn svg { width: 18px; height: 18px; flex-shrink: 0; }\
      .ath-detail-btn-primary {\
        background: ' + themeColor + ';\
        color: white;\
        box-shadow: 0 4px 16px ' + themeColor + '66;\
      }\
      .ath-detail-btn-primary:hover { opacity: 0.9; }\
      .ath-detail-btn-secondary {\
        background: rgba(255,255,255,0.15);\
        color: white;\
        border: 1px solid rgba(255,255,255,0.2);\
      }\
      .ath-detail-btn-secondary:hover { background: rgba(255,255,255,0.25); }\
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
    var SOUND_PREF_KEY = "ath_sound_on";
    var currentIndex = 0;
    var isOpen = false;
    var userPreferSound = false;
    try { userPreferSound = localStorage.getItem(SOUND_PREF_KEY) === "1"; } catch (e) { }
    var isMuted = true; // Always start muted (autoplay policy), unmute after user interaction
    var soundHintDismissed = false;
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
    var _consecutiveSkips = 0; // Track consecutive auto-skips to prevent chain reactions
    var MAX_CONSECUTIVE_SKIPS = 2; // Stop auto-skipping after this many consecutive failures
    var _isShareLinkOpen = false; // When true, disable auto-skip entirely (user opened via share link)

    // ── Video Depth Tracking (AI Learning) ──
    var depthSent = {};          // { "clipId_25": true, ... }
    var clipWatchStart = {};     // { clipId: Date.now() }
    var clipLoopCount = {};      // { clipId: 0 }
    var DEPTH_THRESHOLDS = [25, 50, 75, 100];

    function resetDepthTracking(clipId) {
      DEPTH_THRESHOLDS.forEach(function (t) { delete depthSent[clipId + "_" + t]; });
      clipWatchStart[clipId] = Date.now();
      clipLoopCount[clipId] = 0;
    }

    function checkVideoDepth(video, clipId) {
      if (!video || !video.duration || video.duration < 1) return;
      var pct = (video.currentTime / video.duration) * 100;
      DEPTH_THRESHOLDS.forEach(function (t) {
        var key = clipId + "_" + t;
        if (pct >= t && !depthSent[key]) {
          depthSent[key] = true;
          var watchSec = clipWatchStart[clipId] ? (Date.now() - clipWatchStart[clipId]) / 1000 : 0;
          trackEvent("video_progress", {
            clip_id: clipId,
            progress_pct: t,
            watch_duration_sec: Math.round(watchSec * 10) / 10,
            total_duration_sec: Math.round(video.duration * 10) / 10,
            loop_count: clipLoopCount[clipId] || 0
          });
        }
      });
      // Detect loop restart (video loops back near start after being near end)
      if (pct < 5 && depthSent[clipId + "_100"]) {
        clipLoopCount[clipId] = (clipLoopCount[clipId] || 0) + 1;
        // Track replay event on each loop
        trackEvent("video_replay", {
          clip_id: clipId,
          loop_count: clipLoopCount[clipId],
          total_watch_sec: clipWatchStart[clipId] ? Math.round((Date.now() - clipWatchStart[clipId]) / 100) / 10 : 0
        });
        // Reset depth for next loop (except 100% which stays to detect future loops)
        [25, 50, 75].forEach(function (t) { delete depthSent[clipId + "_" + t]; });
      }
    }

    // ── Adaptive Quality: detect network speed and choose 720p or 1080p ──
    var useHD = false; // default: 720p for fast loading
    function detectNetworkQuality() {
      // Method 1: Navigator.connection API (Chrome/Android)
      var conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (conn) {
        // WiFi or fast connection → HD
        if (conn.type === "wifi" || conn.type === "ethernet") { useHD = true; return; }
        // effectiveType: 4g with good downlink → HD
        if (conn.effectiveType === "4g" && (conn.downlink || 0) >= 5) { useHD = true; return; }
        // 3g or slower → stay 720p
        useHD = false;
        return;
      }
      // Method 2: Measure actual download speed with first clip thumbnail
      var testUrl = (clips[0] && clips[0].thumbnail_url) || "";
      if (!testUrl) return;
      var startTime = performance.now();
      var img = new Image();
      img.onload = function () {
        var elapsed = (performance.now() - startTime) / 1000; // seconds
        // Thumbnail is typically 20-50KB; if loads in < 200ms → fast connection
        if (elapsed < 0.3) {
          useHD = true;
          upgradeToHD();
        }
      };
      img.src = testUrl + (testUrl.indexOf("?") > -1 ? "&" : "?") + "_t=" + Date.now();
    }
    detectNetworkQuality();

    // Get the best URL for a clip based on quality setting
    function getClipUrl(clip) {
      if (useHD && clip.clip_url_hd) {
        // Only use HD if it's a processed file (contains 'widget_' in path)
        // Raw unprocessed originals may be too large or use unsupported codecs (H.265)
        var hdPath = clip.clip_url_hd.split('?')[0]; // strip SAS token for check
        if (hdPath.indexOf('widget_') > -1 || hdPath.indexOf('/clips/') === -1) {
          return clip.clip_url_hd;
        }
        // HD URL is raw original — fall back to optimized 720p
        console.log('[AitherHub] HD URL is raw original, falling back to 720p for clip ' + clip.clip_id);
      }
      return clip.clip_url || "";
    }

    // Upgrade all loaded videos to HD if network is fast
    function upgradeToHD() {
      if (!useHD) return;
      clips.forEach(function (clip, idx) {
        var v = videoElements[idx];
        if (!v) return;
        var hdUrl = clip.clip_url_hd;
        if (!hdUrl || hdUrl === clip.clip_url) return;
        // Only upgrade if not currently playing or if it's a future video
        if (idx !== currentIndex) {
          var currentSrc = v.src || v.getAttribute("data-src") || "";
          if (currentSrc && currentSrc.indexOf("widget_") > -1) {
            // Currently has 720p, upgrade to HD
            if (v.src && v.src !== "" && v.src !== window.location.href) {
              v.src = hdUrl;
            } else {
              v.setAttribute("data-src", hdUrl);
            }
          }
        }
      });
    }

    // Listen for network changes
    var conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (conn) {
      conn.addEventListener("change", function () {
        var wasHD = useHD;
        detectNetworkQuality();
        if (useHD && !wasHD) upgradeToHD();
      });
    }

    // ── FAB (Floating Action Button) with Video Preview ──
    var fab = document.createElement("div");
    fab.className = "ath-fab";
    // Use thumbnail for FAB if available, otherwise use lightweight video
    if (clips[0] && clips[0].thumbnail_url) {
      var fabImg = document.createElement("img");
      fabImg.src = clips[0].thumbnail_url;
      fabImg.alt = "Watch video";
      fabImg.style.cssText = "width:100%;height:100%;object-fit:cover;border-radius:50%;";
      fab.appendChild(fabImg);
    } else if (clips[0] && (clips[0].clip_url || clips[0].widget_url)) {
      fabVideo = document.createElement("video");
      fabVideo.setAttribute("playsinline", "");
      fabVideo.setAttribute("webkit-playsinline", "");
      fabVideo.setAttribute("preload", "metadata");
      fabVideo.setAttribute("loop", "");
      fabVideo.setAttribute("autoplay", "");
      fabVideo.muted = true;
      fabVideo.src = getClipUrl(clips[0]);
      fab.appendChild(fabVideo);
      fabVideo.addEventListener("loadeddata", function () {
        fabVideo.play().catch(function () { });
      });
      try { fabVideo.play().catch(function () { }); } catch (e) { }
      var fabPlayOnTouch = function () {
        if (fabVideo && fabVideo.paused) {
          fabVideo.play().catch(function () { });
        }
        document.removeEventListener("touchstart", fabPlayOnTouch);
        document.removeEventListener("click", fabPlayOnTouch);
      };
      document.addEventListener("touchstart", fabPlayOnTouch, { once: true, passive: true });
      document.addEventListener("click", fabPlayOnTouch, { once: true });
    } else {
      fab.innerHTML = '<div class="ath-fab-icon">' + ICONS.play + '</div>';
    }
    // Small play icon overlay
    var fabPlayOverlay = document.createElement("div");
    fabPlayOverlay.className = "ath-fab-play-overlay";
    fabPlayOverlay.innerHTML = ICONS.play;
    fab.appendChild(fabPlayOverlay);
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
      // Only preload first video; rest load on demand for fast initial playback
      video.setAttribute("preload", index === 0 ? "auto" : "none");
      video.setAttribute("loop", "");
      video.muted = true;
      // Set poster for instant visual feedback
      if (clip.thumbnail_url) {
        video.setAttribute("poster", clip.thumbnail_url);
      }
      // Only set src for first 2 videos; rest are lazy-loaded
      // Use getClipUrl() for adaptive quality (720p/1080p based on network)
      if (index <= 1) {
        video.src = getClipUrl(clip);
      } else {
        video.setAttribute("data-src", getClipUrl(clip));
      }
      // Error handler for debugging video load failures
      video.addEventListener("error", function() {
        var err = video.error;
        console.warn("[AitherHub] Video error idx=" + index + " code=" + (err ? err.code : "?") + " msg=" + (err ? err.message : "unknown"));
      });
      // Stalled handler: retry load if video stalls
      video.addEventListener("stalled", function() {
        console.warn("[AitherHub] Video stalled idx=" + index + ", retrying load");
        setTimeout(function() { try { video.load(); } catch(e){} }, 1000);
      });
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
    speedIndicator.innerHTML = '&#9889; 2x 速度';
    overlay.appendChild(speedIndicator);

    // Right-side action buttons
    var actions = document.createElement("div");
    actions.className = "ath-actions";

    // Like button
    var likeBtn = document.createElement("button");
    likeBtn.className = "ath-action-btn";
    likeBtn.innerHTML = '<div class="ath-action-icon">' + ICONS.heart + '</div><span class="ath-action-label">いいね</span>';
    actions.appendChild(likeBtn);

    // Share button
    var shareBtn = document.createElement("button");
    shareBtn.className = "ath-action-btn";
    shareBtn.innerHTML = '<div class="ath-action-icon">' + ICONS.share + '</div><span class="ath-action-label">シェア</span>';
    actions.appendChild(shareBtn);

    // Mute button
    var muteBtn = document.createElement("button");
    muteBtn.className = "ath-action-btn";
    muteBtn.innerHTML = '<div class="ath-action-icon">' + ICONS.volumeOff + '</div><span class="ath-action-label">音声</span>';
    actions.appendChild(muteBtn);

    overlay.appendChild(actions);

    // Video counter
    var counter = document.createElement("div");
    counter.className = "ath-counter";
    overlay.appendChild(counter);

    // Bottom area (product card + CTA)
    var bottom = document.createElement("div");
    bottom.className = "ath-bottom";

    // Product card (will be shown/hidden per clip)
    var productCard = document.createElement("div");
    productCard.className = "ath-product-card";
    productCard.style.display = "none";
    var productImg = document.createElement("img");
    productImg.className = "ath-product-img";
    productCard.appendChild(productImg);
    var productInfo = document.createElement("div");
    productInfo.className = "ath-product-info";
    var productName = document.createElement("div");
    productName.className = "ath-product-name";
    var productPrice = document.createElement("div");
    productPrice.className = "ath-product-price";
    productInfo.appendChild(productName);
    productInfo.appendChild(productPrice);
    productCard.appendChild(productInfo);
    bottom.appendChild(productCard);

    // CTA buttons wrap
    var ctaWrap = document.createElement("div");
    ctaWrap.className = "ath-cta-wrap";

    // Cart button (shown when product_cart_url or product_url exists)
    var cartBtn = document.createElement("button");
    cartBtn.className = "ath-cta ath-cta-cart";
    cartBtn.innerHTML = ICONS.cart + '<span>カートに入れる</span>';

    // Buy button (shown when product_url exists)
    var buyBtn = document.createElement("button");
    buyBtn.className = "ath-cta ath-cta-buy";
    buyBtn.innerHTML = ICONS.bag + '<span>' + ctaText + '</span>';

    // Single CTA (fallback when no product info)
    var singleCta = document.createElement("button");
    singleCta.className = "ath-cta ath-cta-single";
    singleCta.innerHTML = ICONS.cart + '<span>' + ctaText + '</span>';

    ctaWrap.appendChild(cartBtn);
    ctaWrap.appendChild(buyBtn);
    ctaWrap.appendChild(singleCta);
    bottom.appendChild(ctaWrap);

    // Info area (title + description, always shown)
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
    swipeHint.innerHTML = ICONS.chevronUp + '上にスワイプ';
    swipeHint.style.display = "none";
    overlay.appendChild(swipeHint);

    // Sound hint overlay ("tap to unmute")
    var soundHint = document.createElement("div");
    soundHint.className = "ath-sound-hint hidden";
    var soundHintIcon = document.createElement("div");
    soundHintIcon.className = "ath-sound-hint-icon";
    soundHintIcon.innerHTML = ICONS.volumeOff;
    soundHint.appendChild(soundHintIcon);
    var soundHintText = document.createElement("div");
    soundHintText.className = "ath-sound-hint-text";
    soundHintText.textContent = "\u30BF\u30C3\u30D7\u3067\u97F3\u58F0ON";
    soundHint.appendChild(soundHintText);
    overlay.appendChild(soundHint);

    // Sound hint click → show confirm popup
    soundHint.addEventListener("click", function (e) {
      e.stopPropagation();
      soundHintDismissed = true;
      hideSoundHint();
      soundConfirm.className = "ath-sound-confirm visible";
    });

    function showSoundHint() {
      soundHint.className = "ath-sound-hint";
      // Auto-hide after 4 seconds if not tapped
      setTimeout(function () {
        if (!soundHintDismissed) hideSoundHint();
      }, 4000);
    }

    function hideSoundHint() {
      soundHint.className = "ath-sound-hint hidden";
    }

    // Subtitle overlay
    var subtitleEl = document.createElement("div");
    subtitleEl.className = "ath-subtitle hidden";
    var subtitleTextEl = document.createElement("span");
    subtitleTextEl.className = "ath-subtitle-text";
    subtitleEl.appendChild(subtitleTextEl);
    overlay.appendChild(subtitleEl);

    // Powered by
    var powered = document.createElement("div");
    powered.className = "ath-powered";
    powered.textContent = "Powered by AitherHub";
    overlay.appendChild(powered);

    // ── Product Detail Panel (slide-up overlay) ──
    var detailOverlay = document.createElement("div");
    detailOverlay.className = "ath-detail-overlay";
    var detailPanel = document.createElement("div");
    detailPanel.className = "ath-detail-panel";

    var detailHandle = document.createElement("div");
    detailHandle.className = "ath-detail-handle";
    detailPanel.appendChild(detailHandle);

    var detailCloseBtn = document.createElement("button");
    detailCloseBtn.className = "ath-detail-close";
    detailCloseBtn.innerHTML = ICONS.close;
    detailPanel.appendChild(detailCloseBtn);

    var detailImg = document.createElement("img");
    detailImg.className = "ath-detail-img";
    detailPanel.appendChild(detailImg);

    // Loading indicator
    var detailLoading = document.createElement("div");
    detailLoading.className = "ath-detail-loading hidden";
    detailLoading.innerHTML = '<div class="ath-detail-spinner"></div><span>\u8AAD\u307F\u8FBC\u307F\u4E2D...</span>';
    detailPanel.appendChild(detailLoading);

    var detailBody = document.createElement("div");
    detailBody.className = "ath-detail-body";

    // Site info (favicon + site name)
    var detailSite = document.createElement("div");
    detailSite.className = "ath-detail-site";
    var detailFavicon = document.createElement("img");
    detailFavicon.className = "ath-detail-favicon";
    var detailSiteName = document.createElement("span");
    detailSite.appendChild(detailFavicon);
    detailSite.appendChild(detailSiteName);
    detailBody.appendChild(detailSite);

    var detailName = document.createElement("div");
    detailName.className = "ath-detail-name";
    detailBody.appendChild(detailName);

    var detailPrice = document.createElement("div");
    detailPrice.className = "ath-detail-price";
    detailBody.appendChild(detailPrice);

    var detailDesc = document.createElement("div");
    detailDesc.className = "ath-detail-desc";
    detailBody.appendChild(detailDesc);

    var detailActions = document.createElement("div");
    detailActions.className = "ath-detail-actions";

    var detailCartBtn = document.createElement("button");
    detailCartBtn.className = "ath-detail-btn ath-detail-btn-secondary";
    detailCartBtn.innerHTML = ICONS.cart + '<span>\u30AB\u30FC\u30C8\u306B\u5165\u308C\u308B</span>';

    var detailBuyBtn = document.createElement("button");
    detailBuyBtn.className = "ath-detail-btn ath-detail-btn-primary";
    detailBuyBtn.innerHTML = ICONS.bag + '<span>\u5546\u54C1\u30DA\u30FC\u30B8\u3078</span>';

    detailActions.appendChild(detailCartBtn);
    detailActions.appendChild(detailBuyBtn);
    detailBody.appendChild(detailActions);
    detailPanel.appendChild(detailBody);
    detailOverlay.appendChild(detailPanel);
    overlay.appendChild(detailOverlay);

    var isDetailOpen = false;

    // OGP preview cache (keyed by product_url)
    var ogpCache = {};

    function openProductDetail(clip) {
      if (!clip) return;

      // Pause video
      var video = videoElements[currentIndex];
      if (video && !video.paused) {
        video.pause();
      }

      // Show panel immediately with loading state
      detailOverlay.classList.add("active");
      isDetailOpen = true;

      // Show loading, hide content initially
      detailLoading.classList.remove("hidden");
      detailBody.style.display = "none";
      detailImg.style.display = "none";

      // Track product_detail_view event
      trackEvent("product_detail_view", {
        clip_id: clip.clip_id,
        product_name: clip.product_name,
        product_price: clip.product_price,
        video_time: video ? video.currentTime : 0,
      });

      // Show/hide cart button based on product_cart_url
      detailCartBtn.style.display = clip.product_cart_url ? "flex" : "none";
      // Show/hide buy button based on product_url
      detailBuyBtn.style.display = clip.product_url ? "flex" : "none";

      // If we have a product_url, fetch OGP data
      var productUrl = clip.product_url;
      if (productUrl) {
        // Check cache first
        if (ogpCache[productUrl]) {
          populateDetailFromOGP(ogpCache[productUrl], clip);
          return;
        }
        // Fetch OGP data from API
        fetch(API_BASE + "/widget/product-preview?url=" + encodeURIComponent(productUrl))
          .then(function (resp) { return resp.json(); })
          .then(function (ogp) {
            if (ogp && ogp.success) {
              ogpCache[productUrl] = ogp;
              // Only update if panel is still open for this clip
              if (isDetailOpen && clips[currentIndex] === clip) {
                populateDetailFromOGP(ogp, clip);
              }
            } else {
              // Fallback to clip data
              populateDetailFallback(clip);
            }
          })
          .catch(function () {
            // Fallback to clip data on error
            populateDetailFallback(clip);
          });
      } else {
        // No product_url, use clip data directly
        populateDetailFallback(clip);
      }
    }

    function populateDetailFromOGP(ogp, clip) {
      detailLoading.classList.add("hidden");
      detailBody.style.display = "block";

      // Site info
      if (ogp.site_name || ogp.favicon) {
        detailSite.style.display = "flex";
        detailSiteName.textContent = ogp.site_name || "";
        if (ogp.favicon) {
          detailFavicon.src = ogp.favicon;
          detailFavicon.style.display = "block";
          detailFavicon.onerror = function () { detailFavicon.style.display = "none"; };
        } else {
          detailFavicon.style.display = "none";
        }
      } else {
        detailSite.style.display = "none";
      }

      // Title: prefer OGP, fallback to clip
      detailName.textContent = ogp.title || clip.product_name || "";

      // Price: prefer OGP, fallback to clip
      var price = ogp.price || clip.product_price || "";
      detailPrice.textContent = price;
      detailPrice.style.display = price ? "block" : "none";

      // Description from OGP
      var desc = ogp.description || "";
      detailDesc.textContent = desc;
      detailDesc.style.display = desc ? "block" : "none";

      // Image from OGP
      if (ogp.image) {
        detailImg.src = ogp.image;
        detailImg.style.display = "block";
        detailImg.onerror = function () {
          // Fallback to clip image or hide
          if (clip.product_image_url) {
            detailImg.src = clip.product_image_url;
          } else {
            detailImg.style.display = "none";
          }
        };
      } else if (clip.product_image_url) {
        detailImg.src = clip.product_image_url;
        detailImg.style.display = "block";
      } else {
        detailImg.style.display = "none";
      }
    }

    function populateDetailFallback(clip) {
      detailLoading.classList.add("hidden");
      detailBody.style.display = "block";

      // Hide site info in fallback mode
      detailSite.style.display = "none";

      detailName.textContent = clip.product_name || "";
      detailPrice.textContent = clip.product_price || "";
      detailPrice.style.display = clip.product_price ? "block" : "none";

      // Use transcript_text as description fallback
      detailDesc.textContent = clip.transcript_text || "";
      detailDesc.style.display = clip.transcript_text ? "block" : "none";

      if (clip.product_image_url) {
        detailImg.src = clip.product_image_url;
        detailImg.style.display = "block";
      } else {
        detailImg.style.display = "none";
      }
    }

    function closeProductDetail() {
      detailOverlay.classList.remove("active");
      isDetailOpen = false;
      // Resume video
      var video = videoElements[currentIndex];
      if (video && video.paused) {
        video.play().catch(function () { });
      }
    }

    // Close detail panel handlers
    detailCloseBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      closeProductDetail();
    });
    detailOverlay.addEventListener("click", function (e) {
      // Close when clicking outside the panel
      if (e.target === detailOverlay) {
        closeProductDetail();
      }
    });

    // Detail panel: Buy button → external navigation
    detailBuyBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];
      if (!clip || !clip.product_url) return;
      trackEvent("product_click", {
        clip_id: clip.clip_id,
        product_name: clip.product_name,
        product_price: clip.product_price,
        source: "detail_panel",
      });
      var targetUrl = addUtmParams(clip.product_url, clip.clip_id, "detail_buy");
      window.open(targetUrl, "_blank");
    });

    // Detail panel: Cart button → add to cart or navigate
    detailCartBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];
      if (!clip) return;
      trackEvent("add_to_cart", {
        clip_id: clip.clip_id,
        product_name: clip.product_name,
        product_price: clip.product_price,
        source: "detail_panel",
      });
      // Strategy 1: DOM manipulation
      if (config.cart_selector) {
        try {
          var domCartBtn = document.querySelector(config.cart_selector);
          if (domCartBtn) {
            domCartBtn.click();
            detailCartBtn.innerHTML = '<span>&#10003; \u30AB\u30FC\u30C8\u306B\u8FFD\u52A0\u3057\u307E\u3057\u305F</span>';
            setTimeout(function () {
              detailCartBtn.innerHTML = ICONS.cart + '<span>\u30AB\u30FC\u30C8\u306B\u5165\u308C\u308B</span>';
            }, 2000);
            return;
          }
        } catch (err) { }
      }
      // Strategy 2: Navigate to cart URL
      var cartUrl = clip.product_cart_url || clip.product_url;
      if (cartUrl) {
        var targetUrl = addUtmParams(cartUrl, clip.clip_id, "detail_cart");
        window.open(targetUrl, "_blank");
      }
    });

    shadow.appendChild(overlay);

    // ── Subtitle engine ──
    // Builds a timed captions array from either:
    //   1. clip.captions (JSON array with {start, end, text}) — precise
    //   2. clip.transcript_text (plain string) — estimated timing
    function buildCaptions(clip) {
      // Priority 1: structured captions from backend
      if (clip.captions && Array.isArray(clip.captions) && clip.captions.length > 0) {
        var rawCaps = clip.captions;
        var dur = clip.duration_sec || 60;
        // Auto-detect: are captions in absolute (source video) or local (0-based) time?
        // If max start time > clip duration, they're absolute and need offset subtraction
        var maxStart = 0;
        for (var mi = 0; mi < rawCaps.length; mi++) {
          if ((rawCaps[mi].start || 0) > maxStart) maxStart = rawCaps[mi].start || 0;
        }
        var offset = 0;
        if (maxStart > dur * 1.5) {
          // Absolute timestamps — extract offset from clip_url filename (clip_START_END.mp4)
          var urlMatch = (clip.clip_url || "").match(/clip_(\d+)_(\d+)/);
          if (urlMatch) {
            offset = parseInt(urlMatch[1], 10);
          } else {
            // Fallback: use first caption's start as offset
            offset = rawCaps[0].start || 0;
          }
        }
        return rawCaps.map(function (c) {
          var s = (c.start || 0) - offset;
          var e = (c.end || (c.start + 3)) - offset;
          if (s < 0) s = 0;
          if (e < 0) e = 0;
          return { start: s, end: e, text: c.text || "" };
        }).filter(function (c) { return c.text.trim(); });
      }
      // Priority 2: parse transcript_text into estimated segments
      var text = clip.transcript_text;
      if (!text || !text.trim()) return [];
      var dur = clip.duration_sec || 60;
      // Split by sentence-ending punctuation
      var raw = text.replace(/([。！？!?.]+)/g, "$1\n").split("\n").filter(function (s) { return s.trim(); });
      // If too few splits, chunk by ~20 chars
      if (raw.length <= 1 && text.length > 30) {
        raw = [];
        var chunk = "";
        for (var ci = 0; ci < text.length; ci++) {
          chunk += text[ci];
          if (chunk.length >= 20 && /[、。！？!?,. \u3000]/.test(text[ci])) {
            raw.push(chunk.trim());
            chunk = "";
          }
        }
        if (chunk.trim()) raw.push(chunk.trim());
      }
      if (raw.length === 0) return [{ start: 0, end: dur, text: text.substring(0, 60) }];
      // Distribute timing proportionally by character count
      var totalChars = 0;
      for (var ri = 0; ri < raw.length; ri++) totalChars += raw[ri].length;
      var result = [];
      var t = 0;
      for (var ri2 = 0; ri2 < raw.length; ri2++) {
        var segDur = (raw[ri2].length / totalChars) * dur;
        if (segDur < 1) segDur = 1;
        result.push({ start: t, end: t + segDur, text: raw[ri2] });
        t += segDur;
      }
      return result;
    }

    // Cache built captions per clip index
    var captionsCache = {};
    function getCaptions(idx) {
      if (captionsCache[idx] === undefined) {
        captionsCache[idx] = buildCaptions(clips[idx]);
      }
      return captionsCache[idx];
    }

    // Find current caption for a given time
    function findCaption(captions, time) {
      var MIN_DISPLAY = 2;
      for (var i = 0; i < captions.length; i++) {
        var c = captions[i];
        var end = Math.max(c.end, c.start + MIN_DISPLAY);
        // Don't overlap with next
        if (i + 1 < captions.length) {
          end = Math.min(end, captions[i + 1].start);
        }
        if (time >= c.start && time < end) return c;
      }
      return null;
    }

    // Update subtitle display
    var lastSubtitleText = "";
    function updateSubtitle() {
      var video = videoElements[currentIndex];
      if (!video) { subtitleEl.className = "ath-subtitle hidden"; return; }
      var captions = getCaptions(currentIndex);
      if (!captions.length) { subtitleEl.className = "ath-subtitle hidden"; return; }
      var cap = findCaption(captions, video.currentTime);
      if (cap && cap.text) {
        if (cap.text !== lastSubtitleText) {
          subtitleTextEl.textContent = cap.text;
          lastSubtitleText = cap.text;
        }
        subtitleEl.className = "ath-subtitle visible";
      } else {
        subtitleEl.className = "ath-subtitle hidden";
        lastSubtitleText = "";
      }
    }

    // ── Helper: Check if clip has product info ──
    function hasProductInfo(clip) {
      return clip.product_url || clip.product_cart_url || clip.product_name || clip.product_price;
    }

    // ── Helper: Update product card and CTA buttons for current clip ──
    function updateProductUI(clip) {
      var hasProd = hasProductInfo(clip);

      if (hasProd) {
        // Show product card if we have name or price
        if (clip.product_name || clip.product_price) {
          productCard.style.display = "flex";
          productName.textContent = clip.product_name || "";
          productPrice.textContent = clip.product_price || "";
          if (clip.product_image_url) {
            productImg.src = clip.product_image_url;
            productImg.style.display = "block";
          } else {
            productImg.style.display = "none";
          }
        } else {
          productCard.style.display = "none";
        }

        // Show CTA buttons based on available URLs (2-tier system)
        var hasCartUrl = !!clip.product_cart_url;
        var hasBuyUrl = !!clip.product_url;

        if (!hasCartUrl && !hasBuyUrl) {
          // No URLs at all → hide all CTA buttons
          cartBtn.style.display = "none";
          buyBtn.style.display = "none";
          singleCta.style.display = "none";
        } else if (hasCartUrl && hasBuyUrl) {
          // Tier 2: Both cart + buy URLs → show dual buttons
          cartBtn.style.display = "flex";
          buyBtn.style.display = "flex";
          singleCta.style.display = "none";
          cartBtn.innerHTML = ICONS.cart + '<span>カートに入れる</span>';
          buyBtn.innerHTML = ICONS.bag + '<span>' + ctaText + '</span>';
        } else if (hasBuyUrl) {
          // Tier 1: Only product_url → show single "商品を見る" button
          cartBtn.style.display = "none";
          buyBtn.style.display = "none";
          singleCta.style.display = "flex";
          singleCta.innerHTML = ICONS.bag + '<span>商品を見る</span>';
        } else {
          // Only cart URL (rare) → show single cart button
          cartBtn.style.display = "flex";
          buyBtn.style.display = "none";
          singleCta.style.display = "none";
          cartBtn.innerHTML = ICONS.cart + '<span>カートに入れる</span>';
        }

        // Track product view
        trackEvent("product_view", {
          clip_id: clip.clip_id,
          product_name: clip.product_name,
          product_price: clip.product_price,
        });
      } else {
        // No product info → hide product card AND all CTA buttons
        productCard.style.display = "none";
        cartBtn.style.display = "none";
        buyBtn.style.display = "none";
        singleCta.style.display = "none";
      }
    }

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
    // ── Helper: Lazy-load video src if not yet set ──
    function ensureVideoSrc(idx) {
      var v = videoElements[idx];
      if (!v) return;
      var clip = clips[idx];
      if (!v.src || v.src === "" || v.src === window.location.href) {
        var dataSrc = v.getAttribute("data-src");
        if (dataSrc) {
          // Use adaptive quality URL
          v.src = clip ? getClipUrl(clip) : dataSrc;
          v.removeAttribute("data-src");
        }
      } else if (useHD && clip && clip.clip_url_hd) {
        // If HD mode activated and current src is 720p, upgrade
        var currentSrc = v.src || "";
        if (currentSrc.indexOf("widget_") > -1 && clip.clip_url_hd.indexOf("widget_") === -1) {
          v.src = clip.clip_url_hd;
        }
      }
    }

    // ── Helper: Preload adjacent videos for seamless swiping ──
    function preloadAdjacent(idx) {
      var next = (idx + 1) % clips.length;
      var next2 = (idx + 2) % clips.length;
      var prev = ((idx - 1) + clips.length) % clips.length;
      // Load src for next 2 and previous
      ensureVideoSrc(next);
      ensureVideoSrc(next2);
      ensureVideoSrc(prev);
      // Set preload to auto for next 2 videos so they buffer ahead
      var nextV = videoElements[next];
      if (nextV) { nextV.setAttribute("preload", "auto"); nextV.load(); }
      var next2V = videoElements[next2];
      if (next2V) { next2V.setAttribute("preload", "auto"); next2V.load(); }
      var prevV = videoElements[prev];
      if (prevV) { prevV.setAttribute("preload", "metadata"); prevV.load(); }
    }

    function playCurrentVideo() {
      var video = videoElements[currentIndex];
      if (!video) return;

      // Show loading spinner
      var slide = video.closest(".ath-slide") || video.parentElement.parentElement;
      var spinner = slide.querySelector(".ath-loading-spinner");
      if (!spinner) {
        spinner = document.createElement("div");
        spinner.className = "ath-loading-spinner";
        spinner.innerHTML = '<div class="ath-spinner-ring"></div>';
        (video.parentElement || slide).appendChild(spinner);
      }
      spinner.style.display = "flex";

      // Ensure current video has src loaded
      ensureVideoSrc(currentIndex);

      // Pause all others
      Object.keys(videoElements).forEach(function (key) {
        if (parseInt(key) !== currentIndex) {
          videoElements[key].pause();
          videoElements[key].currentTime = 0;
        }
      });

      video.currentTime = 0;

      // Hide spinner once enough data is buffered
      var hideSpinner = function () {
        if (spinner) spinner.style.display = "none";
        video.removeEventListener("canplay", hideSpinner);
        video.removeEventListener("playing", hideSpinner);
      };
      video.addEventListener("canplay", hideSpinner);
      video.addEventListener("playing", hideSpinner);
      // Safety timeout: hide spinner after 8s regardless
      setTimeout(function () { if (spinner) spinner.style.display = "none"; }, 8000);

      // ── Robust mobile playback with retry + broken video detection ──
      var playAttempt = 0;
      var MAX_PLAY_RETRIES = 3;
      var _skipChecked = false;

      // Detect broken videos (videoWidth===0 after play) — only auto-skip if under consecutive limit
      function checkVideoHealth() {
        if (_skipChecked) return;
        _skipChecked = true;
        setTimeout(function () {
          if (video.videoWidth === 0 && video.videoHeight === 0 && !video.paused && video.currentTime > 0) {
            console.warn("[AitherHub] Broken video detected (videoWidth=0) at clip " + currentIndex + " (consecutiveSkips=" + _consecutiveSkips + ")");
            if (!_isShareLinkOpen && clips.length > 1 && _consecutiveSkips < MAX_CONSECUTIVE_SKIPS) {
              _consecutiveSkips++;
              goToIndex(currentIndex + 1);
            } else {
              if (_isShareLinkOpen) console.log("[AitherHub] Share link open: auto-skip disabled");
              else console.warn("[AitherHub] Stopping auto-skip: reached max consecutive skips");
              if (spinner) spinner.style.display = "none";
            }
          } else if (video.videoWidth > 0) {
            // Video is playing correctly — reset consecutive skip counter
            _consecutiveSkips = 0;
            _isShareLinkOpen = false; // Clear share link flag on successful play
          }
        }, 3000); // Check 3 seconds after play starts
      }

      function attemptPlay() {
        playAttempt++;
        video.muted = true;
        video.setAttribute("playsinline", "");
        video.setAttribute("webkit-playsinline", "");
        if (video.readyState < 2) { try { video.load(); } catch(e){} }
        var playPromise = video.play();
        if (playPromise !== undefined) {
          playPromise.then(function () {
            console.log("[AitherHub] Play OK attempt=" + playAttempt);
            if (!isMuted) { video.muted = false; }
            // Reset consecutive skip counter on successful play start
            // (checkVideoHealth will confirm actual video rendering)
            preloadAdjacent(currentIndex);
            checkVideoHealth();
          }).catch(function (err) {
            console.warn("[AitherHub] Play fail attempt=" + playAttempt + ": " + err.message);
            if (playAttempt < MAX_PLAY_RETRIES) {
              setTimeout(function () {
                try { video.load(); } catch(e){}
                setTimeout(attemptPlay, 300);
              }, 500 * playAttempt);
            } else {
              console.warn("[AitherHub] All play attempts failed at clip " + currentIndex + " (consecutiveSkips=" + _consecutiveSkips + ")");
              if (spinner) spinner.style.display = "none";
              // Only auto-skip if under consecutive limit and NOT opened via share link
              if (!_isShareLinkOpen && clips.length > 1 && _consecutiveSkips < MAX_CONSECUTIVE_SKIPS) {
                _consecutiveSkips++;
                goToIndex(currentIndex + 1);
              } else {
                if (_isShareLinkOpen) console.log("[AitherHub] Share link open: auto-skip disabled");
                else console.warn("[AitherHub] Stopping auto-skip: reached max consecutive skips");
                preloadAdjacent(currentIndex);
              }
            }
          });
        } else {
          if (!isMuted) video.muted = false;
          preloadAdjacent(currentIndex);
          checkVideoHealth();
        }
      }
      attemptPlay();

      // Update UI
      var clip = clips[currentIndex];
      // If product card is already showing product_name, don't repeat it in info title
      var showingProductCard = hasProductInfo(clip) && (clip.product_name || clip.product_price);
      infoTitle.textContent = showingProductCard ? (clip.liver_name || brandName) : (clip.product_name || clip.liver_name || brandName);
      infoDesc.textContent = clip.transcript_text ? clip.transcript_text.substring(0, 80) + (clip.transcript_text.length > 80 ? "..." : "") : "";
      counter.textContent = (currentIndex + 1);
      progressBar.style.width = "0%";

      // Update product card and CTA buttons
      updateProductUI(clip);

      // Reset subtitle for new clip
      lastSubtitleText = "";
      updateSubtitle();

      // Track
      trackEvent("video_play", { clip_id: clip.clip_id, clip_index: currentIndex });
      // Reset depth tracking for this clip
      resetDepthTracking(clip.clip_id);

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
      if (isDetailOpen) closeProductDetail();
      currentIndex = ((newIndex % clips.length) + clips.length) % clips.length;
      updateSlidePositions(true);
      playCurrentVideo();
      // Update URL bar with current clip for sharing
      if (isOpen && clips[currentIndex]) {
        updateUrlBar(clips[currentIndex].clip_id);
      }
    }

    function goNext() { _isShareLinkOpen = false; goToIndex(currentIndex + 1); }
    function goPrev() { _isShareLinkOpen = false; goToIndex(currentIndex - 1); }

    // ── Helper: Update mute button ──
    function updateMuteButton() {
      var iconEl = muteBtn.querySelector(".ath-action-icon");
      iconEl.innerHTML = isMuted ? ICONS.volumeOff : ICONS.volumeOn;
      // Pulse animation: show when muted, hide when unmuted
      if (isMuted) {
        muteBtn.classList.add("ath-mute-pulse");
      } else {
        muteBtn.classList.remove("ath-mute-pulse");
        // Remember user preference for sound ON
        try { localStorage.setItem(SOUND_PREF_KEY, "1"); } catch (e) { }
        userPreferSound = true;
        // Hide sound hint if visible
        hideSoundHint();
        soundHintDismissed = true;
      }
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
        if (index === currentIndex) {
          onTimeUpdate();
          updateSubtitle();
          // AI Learning: track video depth
          var c = clips[currentIndex];
          if (c) checkVideoDepth(videoElements[currentIndex], c.clip_id);
        }
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

      // Sound UX: set isMuted BEFORE playCurrentVideo so it uses correct state
      if (userPreferSound) {
        isMuted = false;
      } else {
        isMuted = true;
      }

      playCurrentVideo();
      // Update URL bar with first clip for sharing
      if (clips[currentIndex]) {
        updateUrlBar(clips[currentIndex].clip_id);
      }
      trackEvent("widget_open");

      // Update UI after play started
      updateMuteButton();
      if (!userPreferSound) {
        // First-time user: show "tap to unmute" hint
        soundHintDismissed = false;
        showSoundHint();
      }
    });

    closeBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      closeOverlay();
    });

    function closeOverlay() {
      isOpen = false;
      if (isDetailOpen) closeProductDetail();
      overlay.classList.remove("active");
      fab.style.display = "flex";
      // Restore original URL (remove ?ath_clip= parameter)
      restoreUrl();
      // Resume FAB video
      if (fabVideo) { try { fabVideo.play().catch(function () { }); } catch (e) { } }
      // Unlock body scroll
      document.body.style.overflow = "";
      document.documentElement.style.overflow = "";
      // Hide subtitle & sound hint
      subtitleEl.className = "ath-subtitle hidden";
      lastSubtitleText = "";
      hideSoundHint();
      // Pause all videos
      Object.keys(videoElements).forEach(function (key) {
        videoElements[key].pause();
      });
    }

    // ── Touch Swipe (TikTok-style) ──
    feed.addEventListener("touchstart", function (e) {
      if (isDetailOpen) return;
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
      if (isDetailOpen) return;
      // Ignore if clicking on buttons
      if (e.target.closest && (e.target.closest(".ath-action-btn") || e.target.closest(".ath-cta") || e.target.closest(".ath-close-btn") || e.target.closest(".ath-product-card") || e.target.closest(".ath-detail-overlay"))) return;

      var video = videoElements[currentIndex];
      if (!video) return;

      var feedRect = feed.getBoundingClientRect();
      var clickX = e.clientX - feedRect.left;
      var isLeftHalf = clickX < feedRect.width / 2;

      if (isLeftHalf && isMuted) {
        // First tap on left: show sound confirm popup
        soundConfirm.className = "ath-sound-confirm visible";
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
    // ── URL Share Link System ──
    // Build a share URL on the current EC site with ?ath_clip= parameter
    function buildShareUrl(clipId) {
      var base = window.location.origin + window.location.pathname;
      var params = new URLSearchParams(window.location.search);
      params.set("ath_clip", clipId);
      return base + "?" + params.toString();
    }

    // Update browser URL bar when navigating between clips (no page reload)
    function updateUrlBar(clipId) {
      if (!window.history || !window.history.replaceState) return;
      try {
        var newUrl = buildShareUrl(clipId);
        window.history.replaceState({ ath_clip: clipId }, document.title, newUrl);
      } catch(e) {}
    }

    // Restore original URL when widget is closed
    var _originalUrl = window.location.href;
    function restoreUrl() {
      if (!window.history || !window.history.replaceState) return;
      try {
        window.history.replaceState({}, document.title, _originalUrl);
      } catch(e) {}
    }

    shareBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];
      // Build share URL on the SAME EC site (not aitherhub.com)
      var shareUrl = buildShareUrl(clip.clip_id);
      var shareTitle = clip.product_name || brandName;
      var shareText = shareTitle + (clip.product_price ? " " + clip.product_price : "") + "\n" + shareUrl;
      if (navigator.share) {
        navigator.share({ title: shareTitle, text: shareText, url: shareUrl }).catch(function () { });
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(shareUrl).then(function() {
          var label = shareBtn.querySelector(".ath-action-label");
          label.textContent = "\u30B3\u30D4\u30FC!";
          setTimeout(function () { label.textContent = "\u30B7\u30A7\u30A2"; }, 2000);
        }).catch(function() {});
      }
      trackEvent("share", { clip_id: clip.clip_id, share_url: shareUrl });
    });

    // ── Sound confirm popup DOM ──
    var soundConfirm = document.createElement("div");
    soundConfirm.className = "ath-sound-confirm";
    soundConfirm.innerHTML = '<div class="ath-sound-confirm-box">' +
      '<div class="ath-sound-confirm-icon">' + ICONS.volumeOn + '</div>' +
      '<div class="ath-sound-confirm-title">\u97F3\u58F0\u3092ON\u306B\u3057\u307E\u3059\u304B\uFF1F</div>' +
      '<div class="ath-sound-confirm-desc">\u52D5\u753B\u306E\u97F3\u58F0\u304C\u518D\u751F\u3055\u308C\u307E\u3059</div>' +
      '<div class="ath-sound-confirm-btns">' +
        '<button class="ath-sound-confirm-btn cancel">\u3044\u3044\u3048</button>' +
        '<button class="ath-sound-confirm-btn confirm">\u97F3\u58F0ON</button>' +
      '</div>' +
    '</div>';
    overlay.appendChild(soundConfirm);

    // Popup backdrop click → close
    soundConfirm.addEventListener("click", function (e) {
      if (e.target === soundConfirm) {
        soundConfirm.className = "ath-sound-confirm";
      }
    });

    // Cancel button
    soundConfirm.querySelector(".ath-sound-confirm-btn.cancel").addEventListener("click", function (e) {
      e.stopPropagation();
      soundConfirm.className = "ath-sound-confirm";
    });

    // Confirm button → unmute
    soundConfirm.querySelector(".ath-sound-confirm-btn.confirm").addEventListener("click", function (e) {
      e.stopPropagation();
      isMuted = false;
      var video = videoElements[currentIndex];
      if (video) video.muted = false;
      updateMuteButton();
      soundConfirm.className = "ath-sound-confirm";
      try { localStorage.setItem(SOUND_PREF_KEY, "1"); } catch (e) { }
      userPreferSound = true;
    });

    // ── Action: Mute/Unmute ──
    muteBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (isMuted) {
        // Show confirmation popup before unmuting
        soundConfirm.className = "ath-sound-confirm visible";
      } else {
        // Muting doesn't need confirmation
        isMuted = true;
        var video = videoElements[currentIndex];
        if (video) video.muted = true;
        updateMuteButton();
        try { localStorage.setItem(SOUND_PREF_KEY, "0"); } catch (e) { }
        userPreferSound = false;
      }
    });

    // ── Product card click → navigate to product page ──
    productCard.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];
      if (!hasProductInfo(clip)) return;
      openProductDetail(clip);
    });

    // ── CTA: Cart button ──
    cartBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];
      trackEvent("add_to_cart", {
        clip_id: clip.clip_id,
        product_name: clip.product_name,
        product_price: clip.product_price,
        video_time: videoElements[currentIndex] ? videoElements[currentIndex].currentTime : 0,
      });

      // Strategy 1: DOM manipulation (add to cart via CSS selector)
      if (config.cart_selector) {
        try {
          var domCartBtn = document.querySelector(config.cart_selector);
          if (domCartBtn) {
            domCartBtn.click();
            cartBtn.innerHTML = '<span>&#10003; カートに追加しました</span>';
            setTimeout(function () {
              cartBtn.innerHTML = ICONS.cart + '<span>カートに入れる</span>';
            }, 2000);
            return;
          }
        } catch (err) { }
      }

      // Strategy 2: Navigate to cart URL
      var cartUrl = clip.product_cart_url || clip.product_url;
      if (cartUrl) {
        var targetUrl = addUtmParams(cartUrl, clip.clip_id, "add_to_cart");
        window.open(targetUrl, "_blank");
      }
    });

    // ── CTA: Buy button ──
    buyBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];
      trackEvent("purchase_click", {
        clip_id: clip.clip_id,
        product_name: clip.product_name,
        product_price: clip.product_price,
        video_time: videoElements[currentIndex] ? videoElements[currentIndex].currentTime : 0,
      });

      var targetUrl = clip.product_url;
      if (targetUrl) {
        targetUrl = addUtmParams(targetUrl, clip.clip_id, "purchase");
        window.open(targetUrl, "_blank");
      } else if (config.cta_url_template) {
        targetUrl = config.cta_url_template.replace("{product}", encodeURIComponent(clip.product_name || ""));
        targetUrl = addUtmParams(targetUrl, clip.clip_id, "purchase");
        window.location.href = targetUrl;
      }
    });

    // ── CTA: Single button ("商品を見る" — Tier 1: product_url only) ──
    singleCta.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];
      if (!hasProductInfo(clip)) return;
      openProductDetail(clip);
    });

    // ── Auto-open from share link: detect ?ath_clip=CLIP_ID in URL ──
    (function checkShareLink() {
      try {
        var params = new URLSearchParams(window.location.search);
        var sharedClipId = params.get("ath_clip");
        if (!sharedClipId) return;

        // Find the clip index matching the shared clip ID
        var targetIndex = -1;
        for (var si = 0; si < clips.length; si++) {
          if (clips[si].clip_id === sharedClipId) {
            targetIndex = si;
            break;
          }
        }
        if (targetIndex === -1) return; // Clip not found in this client's list

        // Auto-open the widget at the target clip
        _isShareLinkOpen = true; // Disable auto-skip for share link opens
        setTimeout(function () {
          isOpen = true;
          overlay.classList.add("active");
          fab.style.display = "none";
          if (fabVideo) { try { fabVideo.pause(); } catch (e) { } }
          document.body.style.overflow = "hidden";
          document.documentElement.style.overflow = "hidden";
          currentIndex = targetIndex;
          updateSlidePositions(false);
          isMuted = true;
          playCurrentVideo();
          updateMuteButton();
          soundHintDismissed = false;
          showSoundHint();
          // URL already has ?ath_clip= so no need to update
          trackEvent("share_open", { clip_id: sharedClipId });
          // Reset share link flag after first manual swipe
          // (auto-skip will be re-enabled when user swipes)
        }, 500); // Small delay to ensure DOM is ready

        // Keep the URL as-is so the user sees the same URL they can share
        // URL will be restored when widget is closed via restoreUrl()
      } catch (e) { /* Silently fail if URLSearchParams not supported */ }
    })();
  }

  // ── Debug helper: console-only logging (no DOM panel in production) ──
  function _dbg(msg) {
    console.log("[AitherHub] " + msg);
  }

  // ── Initialize ──
  function init() {
    _dbg("init() CLIENT_ID=" + CLIENT_ID);
    scrapePageContext();
    trackEvent("page_view", { title: document.title, referrer: document.referrer });
    checkConversionPage();
    _dbg("calling loadConfig...");
    loadConfig(function (config) {
      _dbg("config loaded, clips=" + (config.clips || []).length);
      var shadow = createWidgetContainer();
      _dbg("shadow created");
      try {
        buildWidget(shadow, config);
        _dbg("buildWidget OK");
      } catch (e) {
        _dbg("buildWidget ERROR: " + e.message);
      }
    });
  }

  // ── Wait for DOM ready ──
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
