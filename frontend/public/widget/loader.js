/**
 * AitherHub Widget Loader v1.0
 * 
 * GTM経由で配信される軽量エントリーポイント。
 * 先方のECサイトに1行のタグを追加するだけで、
 * フローティング動画プレイヤー + 3つの悪魔的ハックを展開する。
 *
 * Usage (GTM Custom HTML):
 *   <script src="https://www.aitherhub.com/widget/loader.js" data-client-id="YOUR_ID" async></script>
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
    // Always refresh in both storages
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
    // Fallback to fetch
    try {
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        keepalive: true,
      }).catch(function () { });
    } catch (e) { }
  }

  // ── Hack 1: DOM Auto-Parse — Scrape page metadata ──
  function scrapePageContext() {
    var getMeta = function (name) {
      var el = document.querySelector('meta[property="' + name + '"]') ||
        document.querySelector('meta[name="' + name + '"]');
      return el ? el.getAttribute("content") : null;
    };

    var h1 = document.querySelector("h1");
    var canonical = document.querySelector('link[rel="canonical"]');

    // Try to find product price (common patterns)
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

  // ── Check if this is a conversion/thank-you page ──
  function checkConversionPage() {
    var url = window.location.href.toLowerCase();
    var title = document.title.toLowerCase();
    var isCV = url.indexOf("thank") !== -1 || url.indexOf("complete") !== -1 ||
      url.indexOf("success") !== -1 || url.indexOf("order-confirm") !== -1 ||
      title.indexOf("ありがとう") !== -1 || title.indexOf("注文完了") !== -1 ||
      title.indexOf("thank") !== -1 || title.indexOf("購入完了") !== -1;

    if (isCV) {
      var storedSid = localStorage.getItem(SESSION_KEY) || sessionStorage.getItem(SESSION_KEY);
      var storedTs = localStorage.getItem(TIMESTAMP_KEY) || sessionStorage.getItem(TIMESTAMP_KEY);
      trackEvent("conversion", {
        stored_session_id: storedSid,
        stored_timestamp: storedTs,
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

  // ── Build Floating Widget UI ──
  function buildWidget(shadow, config) {
    var clips = config.clips || [];
    if (clips.length === 0) return; // No clips assigned, don't show widget

    var themeColor = config.theme_color || "#FF2D55";
    var position = config.position || "bottom-right";
    var ctaText = config.cta_text || "購入する";

    // ── Styles ──
    var style = document.createElement("style");
    style.textContent = '\
      * { box-sizing: border-box; margin: 0; padding: 0; }\
      \
      .ath-fab {\
        position: fixed;\
        ' + (position.indexOf("right") !== -1 ? "right: 20px;" : "left: 20px;") + '\
        ' + (position.indexOf("top") !== -1 ? "top: 20px;" : "bottom: 20px;") + '\
        width: 64px;\
        height: 64px;\
        border-radius: 50%;\
        background: ' + themeColor + ';\
        cursor: pointer;\
        pointer-events: auto;\
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.3s;\
        animation: ath-pulse 2s infinite;\
        overflow: hidden;\
      }\
      .ath-fab:hover {\
        transform: scale(1.1);\
        box-shadow: 0 6px 30px rgba(0,0,0,0.4);\
      }\
      .ath-fab img {\
        width: 100%;\
        height: 100%;\
        object-fit: cover;\
        border-radius: 50%;\
      }\
      .ath-fab-icon {\
        width: 28px;\
        height: 28px;\
        fill: white;\
      }\
      .ath-fab .ath-badge {\
        position: absolute;\
        top: -2px;\
        right: -2px;\
        width: 20px;\
        height: 20px;\
        background: #FF3B30;\
        border-radius: 50%;\
        color: white;\
        font-size: 11px;\
        font-weight: bold;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        font-family: -apple-system, BlinkMacSystemFont, sans-serif;\
      }\
      \
      @keyframes ath-pulse {\
        0%, 100% { box-shadow: 0 4px 20px rgba(0,0,0,0.3); }\
        50% { box-shadow: 0 4px 20px rgba(0,0,0,0.3), 0 0 0 8px ' + themeColor + '33; }\
      }\
      \
      .ath-overlay {\
        position: fixed;\
        top: 0; left: 0; right: 0; bottom: 0;\
        background: rgba(0,0,0,0.95);\
        pointer-events: auto;\
        display: none;\
        flex-direction: column;\
        align-items: center;\
        justify-content: center;\
        opacity: 0;\
        transition: opacity 0.3s;\
      }\
      .ath-overlay.active {\
        display: flex;\
        opacity: 1;\
      }\
      \
      .ath-close {\
        position: absolute;\
        top: 16px;\
        right: 16px;\
        width: 40px;\
        height: 40px;\
        border-radius: 50%;\
        background: rgba(255,255,255,0.15);\
        border: none;\
        cursor: pointer;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        color: white;\
        font-size: 20px;\
        font-family: -apple-system, BlinkMacSystemFont, sans-serif;\
        transition: background 0.2s;\
        z-index: 10;\
      }\
      .ath-close:hover { background: rgba(255,255,255,0.25); }\
      \
      .ath-player-container {\
        width: 100%;\
        max-width: 400px;\
        height: 90vh;\
        max-height: 800px;\
        position: relative;\
        border-radius: 16px;\
        overflow: hidden;\
        background: #000;\
      }\
      \
      .ath-video {\
        width: 100%;\
        height: 100%;\
        object-fit: cover;\
      }\
      \
      .ath-video-info {\
        position: absolute;\
        bottom: 80px;\
        left: 16px;\
        right: 16px;\
        color: white;\
        font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;\
        text-shadow: 0 1px 4px rgba(0,0,0,0.6);\
        pointer-events: none;\
      }\
      .ath-video-info h3 {\
        font-size: 16px;\
        font-weight: 700;\
        margin-bottom: 4px;\
        line-height: 1.3;\
      }\
      .ath-video-info p {\
        font-size: 13px;\
        opacity: 0.85;\
        line-height: 1.4;\
        display: -webkit-box;\
        -webkit-line-clamp: 2;\
        -webkit-box-orient: vertical;\
        overflow: hidden;\
      }\
      \
      .ath-cta {\
        position: absolute;\
        bottom: 16px;\
        left: 16px;\
        right: 16px;\
        height: 52px;\
        border-radius: 26px;\
        background: ' + themeColor + ';\
        color: white;\
        border: none;\
        cursor: pointer;\
        font-size: 16px;\
        font-weight: 700;\
        font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        gap: 8px;\
        transition: transform 0.2s, opacity 0.2s;\
        pointer-events: auto;\
        box-shadow: 0 4px 16px rgba(0,0,0,0.3);\
      }\
      .ath-cta:hover { transform: scale(1.03); }\
      .ath-cta:active { transform: scale(0.97); }\
      .ath-cta svg {\
        width: 20px;\
        height: 20px;\
        fill: white;\
      }\
      \
      .ath-nav {\
        position: absolute;\
        top: 50%;\
        transform: translateY(-50%);\
        width: 36px;\
        height: 36px;\
        border-radius: 50%;\
        background: rgba(255,255,255,0.2);\
        border: none;\
        cursor: pointer;\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        color: white;\
        font-size: 18px;\
        font-family: -apple-system, BlinkMacSystemFont, sans-serif;\
        transition: background 0.2s;\
        pointer-events: auto;\
      }\
      .ath-nav:hover { background: rgba(255,255,255,0.35); }\
      .ath-nav-prev { left: -48px; }\
      .ath-nav-next { right: -48px; }\
      \
      .ath-progress {\
        position: absolute;\
        top: 0;\
        left: 0;\
        height: 3px;\
        background: ' + themeColor + ';\
        transition: width 0.1s linear;\
        border-radius: 0 2px 2px 0;\
      }\
      \
      .ath-swipe-hint {\
        position: absolute;\
        top: 50%;\
        left: 50%;\
        transform: translate(-50%, -50%);\
        color: white;\
        font-size: 14px;\
        font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;\
        opacity: 0;\
        transition: opacity 0.5s;\
        pointer-events: none;\
        text-align: center;\
      }\
      \
      @media (max-width: 480px) {\
        .ath-player-container {\
          max-width: 100%;\
          height: 100vh;\
          max-height: none;\
          border-radius: 0;\
        }\
        .ath-nav { display: none; }\
      }\
    ';
    shadow.appendChild(style);

    // ── FAB (Floating Action Button) ──
    var fab = document.createElement("div");
    fab.className = "ath-fab";

    // Use first clip thumbnail as FAB image, or play icon
    if (clips[0] && clips[0].thumbnail_url) {
      var fabImg = document.createElement("img");
      fabImg.src = clips[0].thumbnail_url;
      fabImg.alt = "Watch video";
      fab.appendChild(fabImg);
    } else {
      fab.innerHTML = '<svg class="ath-fab-icon" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
    }

    // Badge with clip count
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

    var closeBtn = document.createElement("button");
    closeBtn.className = "ath-close";
    closeBtn.innerHTML = "&#10005;";
    overlay.appendChild(closeBtn);

    var playerContainer = document.createElement("div");
    playerContainer.className = "ath-player-container";

    // Progress bar
    var progressBar = document.createElement("div");
    progressBar.className = "ath-progress";
    playerContainer.appendChild(progressBar);

    // Video element
    var video = document.createElement("video");
    video.className = "ath-video";
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    video.setAttribute("preload", "metadata");
    video.muted = false;
    playerContainer.appendChild(video);

    // Video info overlay
    var videoInfo = document.createElement("div");
    videoInfo.className = "ath-video-info";
    var infoTitle = document.createElement("h3");
    var infoDesc = document.createElement("p");
    videoInfo.appendChild(infoTitle);
    videoInfo.appendChild(infoDesc);
    playerContainer.appendChild(videoInfo);

    // ── Hack 2: CTA Button (In-Video Action) ──
    var ctaBtn = document.createElement("button");
    ctaBtn.className = "ath-cta";
    ctaBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zM1 2v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42c-.14 0-.25-.11-.25-.25l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49c.08-.14.12-.31.12-.48 0-.55-.45-1-1-1H5.21l-.94-2H1z"/></svg>' +
      '<span>' + ctaText + '</span>';
    playerContainer.appendChild(ctaBtn);

    // Navigation buttons
    if (clips.length > 1) {
      var prevBtn = document.createElement("button");
      prevBtn.className = "ath-nav ath-nav-prev";
      prevBtn.innerHTML = "&#8249;";
      playerContainer.appendChild(prevBtn);

      var nextBtn = document.createElement("button");
      nextBtn.className = "ath-nav ath-nav-next";
      nextBtn.innerHTML = "&#8250;";
      playerContainer.appendChild(nextBtn);
    }

    overlay.appendChild(playerContainer);
    shadow.appendChild(overlay);

    // ── State ──
    var currentIndex = 0;
    var isOpen = false;

    function loadClip(index) {
      if (index < 0 || index >= clips.length) return;
      currentIndex = index;
      var clip = clips[index];

      video.src = clip.clip_url || "";
      video.load();
      video.play().catch(function () { });

      infoTitle.textContent = clip.product_name || clip.liver_name || "";
      infoDesc.textContent = clip.transcript_text ? clip.transcript_text.substring(0, 100) : "";

      progressBar.style.width = "0%";

      trackEvent("video_play", { clip_id: clip.clip_id, clip_index: index });
    }

    // ── Video progress ──
    video.addEventListener("timeupdate", function () {
      if (video.duration) {
        progressBar.style.width = (video.currentTime / video.duration * 100) + "%";
      }
    });

    video.addEventListener("ended", function () {
      trackEvent("video_complete", { clip_id: clips[currentIndex].clip_id });
      // Auto-advance to next clip
      if (currentIndex < clips.length - 1) {
        loadClip(currentIndex + 1);
      }
    });

    // ── Open/Close ──
    fab.addEventListener("click", function () {
      isOpen = true;
      overlay.classList.add("active");
      fab.style.display = "none";
      loadClip(0);
      trackEvent("widget_open");
    });

    closeBtn.addEventListener("click", function () {
      isOpen = false;
      overlay.classList.remove("active");
      fab.style.display = "flex";
      video.pause();
      video.src = "";
    });

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) {
        closeBtn.click();
      }
    });

    // ── Navigation ──
    if (clips.length > 1) {
      prevBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (currentIndex > 0) loadClip(currentIndex - 1);
      });
      nextBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (currentIndex < clips.length - 1) loadClip(currentIndex + 1);
      });
    }

    // ── Swipe support (mobile) ──
    var touchStartY = 0;
    playerContainer.addEventListener("touchstart", function (e) {
      touchStartY = e.touches[0].clientY;
    }, { passive: true });

    playerContainer.addEventListener("touchend", function (e) {
      var deltaY = touchStartY - e.changedTouches[0].clientY;
      if (Math.abs(deltaY) > 60) {
        if (deltaY > 0 && currentIndex < clips.length - 1) {
          loadClip(currentIndex + 1);
        } else if (deltaY < 0 && currentIndex > 0) {
          loadClip(currentIndex - 1);
        }
      }
    }, { passive: true });

    // ── Hack 2: CTA Click Handler ──
    ctaBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var clip = clips[currentIndex];

      trackEvent("cta_click", {
        clip_id: clip.clip_id,
        product_name: clip.product_name,
        video_time: video.currentTime,
      });

      // Strategy 1: Try DOM manipulation (add to cart) if cart_selector is set
      if (config.cart_selector) {
        try {
          var cartBtn = document.querySelector(config.cart_selector);
          if (cartBtn) {
            cartBtn.click();
            // Show feedback
            ctaBtn.innerHTML = '<span>✓ カートに追加しました</span>';
            setTimeout(function () {
              ctaBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zM1 2v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42c-.14 0-.25-.11-.25-.25l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49c.08-.14.12-.31.12-.48 0-.55-.45-1-1-1H5.21l-.94-2H1z"/></svg><span>' + ctaText + '</span>';
            }, 2000);
            return;
          }
        } catch (err) {
          console.warn("[AitherHub] Cart selector failed:", err);
        }
      }

      // Strategy 2: Use postMessage for cross-frame communication
      if (window.parent !== window) {
        window.parent.postMessage({
          type: "aitherhub:cta_click",
          clipId: clip.clip_id,
          productName: clip.product_name,
        }, "*");
      }

      // Strategy 3: Navigate to product URL
      var targetUrl = config.cta_url_template
        ? config.cta_url_template.replace("{product}", encodeURIComponent(clip.product_name || ""))
        : (clip.product_url || window.location.href);

      if (targetUrl && targetUrl !== window.location.href) {
        window.location.href = targetUrl;
      }
    });

    // ── Keyboard navigation ──
    document.addEventListener("keydown", function (e) {
      if (!isOpen) return;
      if (e.key === "Escape") closeBtn.click();
      if (e.key === "ArrowUp" && currentIndex > 0) loadClip(currentIndex - 1);
      if (e.key === "ArrowDown" && currentIndex < clips.length - 1) loadClip(currentIndex + 1);
    });
  }

  // ── Initialize ──
  function init() {
    // Hack 1: Scrape page context
    var pageContext = scrapePageContext();

    // Hack 3: Track page view
    trackEvent("page_view", {
      title: document.title,
      referrer: document.referrer,
    });

    // Check for conversion page
    checkConversionPage();

    // Load config and build widget
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
