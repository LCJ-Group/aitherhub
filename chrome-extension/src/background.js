/**
 * AitherHub LIVE Connector - Background Service Worker v2.0.0
 * 
 * Central message hub for the 2-screen (LIVE + Dashboard) extension.
 * 
 * Handles:
 * - Session management via SessionManager
 * - Event routing from content scripts to EventBuffer
 * - Tab lifecycle monitoring
 * - API communication via ExtApiClient
 * - Legacy LIVE_DATA support (backward compatible)
 * - AI analysis requests
 */

// ══════════════════════════════════════════════════════════════
// Import modules (loaded via importScripts for MV3 service worker)
// ══════════════════════════════════════════════════════════════
try {
  importScripts(
    'storage.js',
    'api_client.js',
    'event_buffer.js',
    'session_manager.js'
  );
} catch (e) {
  console.error('[AitherHub BG] Failed to import modules:', e);
}

// ══════════════════════════════════════════════════════════════
// Legacy State (backward compatible with v1.x content.js)
// ══════════════════════════════════════════════════════════════
const DEFAULT_API_BASE = 'https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net';
const SEND_INTERVAL_MS = 5000;

let apiBase = DEFAULT_API_BASE;
let apiToken = '';
let refreshToken = '';
let isConnected = false;
let lastSendTime = 0;
let pendingData = null;
let sessionStartTime = null;

let stats = {
  dataSent: 0,
  comments: 0,
  products: 0,
  uptime: 0,
  aiAnalyses: 0
};

// ══════════════════════════════════════════════════════════════
// Initialization
// ══════════════════════════════════════════════════════════════

// Load settings from storage on startup
chrome.storage.local.get(['apiBase', 'accessToken', 'apiToken', 'refreshToken'], (result) => {
  if (result.apiBase) apiBase = result.apiBase;
  apiToken = result.accessToken || result.apiToken || '';
  refreshToken = result.refreshToken || '';
  console.log('[AitherHub BG] Loaded config, hasToken:', !!apiToken);
});

// Initialize new modules
(async () => {
  try {
    await ExtApiClient.init();
    await SessionManager.init();
    console.log('[AitherHub BG] v2.0 modules initialized');
  } catch (e) {
    console.error('[AitherHub BG] Module init error:', e);
  }
})();

// Listen for storage changes
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local') {
    if (changes.accessToken) {
      apiToken = changes.accessToken.newValue || '';
    }
    if (changes.apiToken) {
      apiToken = changes.apiToken.newValue || apiToken;
    }
    if (changes.apiBase) {
      apiBase = changes.apiBase.newValue || DEFAULT_API_BASE;
    }
    if (changes.refreshToken) {
      refreshToken = changes.refreshToken.newValue || '';
    }
  }
});

// ══════════════════════════════════════════════════════════════
// Tab Lifecycle Monitoring
// ══════════════════════════════════════════════════════════════

chrome.tabs.onRemoved.addListener((tabId) => {
  SessionManager.onTabClosed(tabId);
});

// ══════════════════════════════════════════════════════════════
// Message Handler
// ══════════════════════════════════════════════════════════════

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab ? sender.tab.id : null;

  switch (message.type) {
    // ── New v2.0 Messages ──

    case 'EXT_START_SESSION':
      handleExtStartSession(message.data, tabId)
        .then(r => sendResponse(r))
        .catch(e => sendResponse({ error: e.message }));
      return true;

    case 'EXT_END_SESSION':
      handleExtEndSession()
        .then(r => sendResponse(r))
        .catch(e => sendResponse({ error: e.message }));
      return true;

    case 'EXT_BIND_TAB':
      handleExtBindTab(message.data, tabId)
        .then(r => sendResponse(r))
        .catch(e => sendResponse({ error: e.message }));
      return true;

    case 'EXT_EVENTS':
      handleExtEvents(message.events, message.source_type, tabId);
      sendResponse({ status: 'buffered', count: (message.events || []).length });
      break;

    case 'EXT_PRODUCT_SNAPSHOT':
      handleExtProductSnapshot(message.data);
      sendResponse({ status: 'buffered' });
      break;

    case 'EXT_TREND_SNAPSHOT':
      handleExtTrendSnapshot(message.data);
      sendResponse({ status: 'buffered' });
      break;

    case 'EXT_MANUAL_MARKER':
      handleExtManualMarker(message.data)
        .then(r => sendResponse(r))
        .catch(e => sendResponse({ error: e.message }));
      return true;

    case 'EXT_TAB_PING':
      SessionManager.tabPing(message.tab_type, tabId);
      sendResponse({ status: 'ok' });
      break;

    case 'EXT_GET_STATE':
      sendResponse(getExtState());
      break;

    case 'EXT_GET_SUMMARY':
      handleExtGetSummary()
        .then(r => sendResponse(r))
        .catch(e => sendResponse({ error: e.message }));
      return true;

    // ── Legacy v1.x Messages (backward compatible) ──

    case 'LIVE_DATA':
      handleLiveData(message.data, sender.tab);
      sendResponse({ status: 'received' });
      break;

    case 'LIVE_STARTED':
      handleLiveStarted(message.data, sender.tab);
      sendResponse({ status: 'ok' });
      break;

    case 'LIVE_ENDED':
      handleLiveEnded(message.data);
      sendResponse({ status: 'ok' });
      break;

    case 'AI_ANALYZE':
      handleAiAnalyze(message.data)
        .then(result => sendResponse(result))
        .catch(err => sendResponse({
          suggestions: [{
            type: 'info',
            text: 'AI分析に接続できませんでした: ' + err.message
          }]
        }));
      return true;

    case 'GET_STATUS':
      sendResponse({
        isConnected,
        apiBase,
        hasToken: !!apiToken,
        lastSendTime,
        stats: {
          ...stats,
          uptime: sessionStartTime ? Math.floor((Date.now() - sessionStartTime) / 1000) : 0
        },
        // v2.0 state
        extSession: SessionManager.getState(),
      });
      break;

    case 'SET_CONFIG':
      if (message.apiBase !== undefined) apiBase = message.apiBase || DEFAULT_API_BASE;
      if (message.apiToken !== undefined) apiToken = message.apiToken || '';
      chrome.storage.local.set({
        apiBase: apiBase,
        apiToken: apiToken,
        accessToken: apiToken
      });
      sendResponse({ status: 'saved' });
      break;

    case 'BRIDGE_TOKEN_SYNC':
      if (message.accessToken) {
        apiToken = message.accessToken;
        refreshToken = message.refreshToken || refreshToken;
        chrome.storage.local.set({
          accessToken: apiToken,
          apiToken: apiToken,
          refreshToken: refreshToken
        });
        sendResponse({ status: 'saved' });
      } else {
        sendResponse({ status: 'no_token' });
      }
      break;

    default:
      sendResponse({ status: 'unknown_type' });
  }
  return true;
});

// ══════════════════════════════════════════════════════════════
// v2.0 Handlers
// ══════════════════════════════════════════════════════════════

async function handleExtStartSession(data, tabId) {
  const result = await SessionManager.startSession({
    platform: data.platform || 'tiktok_live',
    creator_id: data.creator_id,
    live_url: data.live_url,
    dashboard_url: data.dashboard_url,
    live_tab_id: data.live_tab_id,
    dashboard_tab_id: data.dashboard_tab_id,
    live_title: data.live_title,
    room_id: data.room_id,
  });

  updateBadge('REC', '#FF1744');
  return result;
}

async function handleExtEndSession() {
  const result = await SessionManager.endSession();
  updateBadge('', '');
  return result;
}

async function handleExtBindTab(data, tabId) {
  return SessionManager.bindTab(
    data.tab_type,
    data.tab_id || tabId,
    data.url
  );
}

function handleExtEvents(events, sourceType, tabId) {
  if (!events || !Array.isArray(events)) return;

  for (const evt of events) {
    EventBuffer.addEvent({
      ...evt,
      source_type: evt.source_type || sourceType || 'live_dom',
    });
  }
}

function handleExtProductSnapshot(data) {
  if (data && data.products) {
    EventBuffer.setProductSnapshot(data.products);
  }
}

function handleExtTrendSnapshot(data) {
  if (data && data.trends) {
    EventBuffer.setTrendSnapshot(data.trends);
  }
}

async function handleExtManualMarker(data) {
  const sessionId = SessionManager.getSessionId();
  if (!sessionId) return { error: 'No active session' };

  // Add to event buffer for immediate recording
  EventBuffer.addEvent({
    event_type: 'manual_marker_added',
    source_type: 'manual',
    text_value: data.label || 'manual_mark',
    video_sec: data.video_sec,
    confidence_score: 1.0,
    payload: { label: data.label, video_sec: data.video_sec },
  });

  // Also send directly to API for immediate persistence
  try {
    return await ExtApiClient.addMarker(sessionId, data.label, data.video_sec);
  } catch (err) {
    console.warn('[AitherHub BG] Direct marker send failed, buffered instead:', err.message);
    return { status: 'buffered' };
  }
}

async function handleExtGetSummary() {
  const sessionId = SessionManager.getSessionId();
  if (!sessionId) return { error: 'No active session' };
  return ExtApiClient.getSessionSummary(sessionId);
}

function getExtState() {
  return {
    session: SessionManager.getState(),
    hasSession: SessionManager.hasSession(),
    isActive: SessionManager.isActive(),
    bufferStats: EventBuffer.getStats(),
  };
}

// ══════════════════════════════════════════════════════════════
// Legacy v1.x Handlers (backward compatible)
// ══════════════════════════════════════════════════════════════

async function handleLiveStarted(data, tab) {
  console.log('[AitherHub BG] Live session started (legacy):', data);

  const existing = await chrome.storage.local.get(['liveSessionId', 'liveSessionAccount', 'liveSessionSource']);
  if (existing.liveSessionId &&
      existing.liveSessionAccount === data.account &&
      isConnected) {
    return;
  }

  sessionStartTime = Date.now();
  stats = { dataSent: 0, comments: 0, products: 0, uptime: 0, aiAnalyses: 0 };

  const payload = {
    event: 'live_started',
    source: data.source,
    room_id: data.roomId,
    account: data.account,
    region: data.region,
    timestamp: new Date().toISOString()
  };

  try {
    const response = await sendToAPI('/api/v1/live/extension/session/start', payload);
    if (response && response.session_id) {
      chrome.storage.local.set({
        liveSessionId: response.session_id,
        liveSessionAccount: data.account || '',
        liveSessionSource: data.source || ''
      });
      isConnected = true;
      updateBadge('ON', '#00C853');
    }
  } catch (err) {
    console.error('[AitherHub BG] Failed to start session:', err);
    updateBadge('ERR', '#FF1744');
  }
}

async function handleLiveEnded(data) {
  const sessionId = (await chrome.storage.local.get('liveSessionId')).liveSessionId;
  if (sessionId) {
    try {
      await sendToAPI('/api/v1/live/extension/session/end', {
        session_id: sessionId,
        timestamp: new Date().toISOString()
      });
    } catch (err) {
      console.error('[AitherHub BG] Failed to end session:', err);
    }
  }

  isConnected = false;
  sessionStartTime = null;
  chrome.storage.local.remove(['liveSessionId', 'liveSessionAccount', 'liveSessionSource']);
  updateBadge('', '');
}

async function handleLiveData(data, tab) {
  const now = Date.now();

  if (pendingData && (now - lastSendTime) < SEND_INTERVAL_MS) {
    pendingData = mergeData(pendingData, data);
    return;
  }

  const sessionId = (await chrome.storage.local.get('liveSessionId')).liveSessionId;

  const payload = {
    session_id: sessionId,
    source: data.source,
    timestamp: new Date().toISOString(),
    metrics: data.metrics || {},
    comments: data.comments || [],
    products: data.products || [],
    activities: data.activities || [],
    traffic_sources: data.trafficSources || [],
    suggestions: data.suggestions || []
  };

  try {
    await sendToAPI('/api/v1/live/extension/data', payload);
    lastSendTime = now;
    pendingData = null;
    stats.dataSent++;
    stats.comments += (data.comments || []).length;
    stats.products = Math.max(stats.products, (data.products || []).length);
    updateBadge('ON', '#00C853');
  } catch (err) {
    console.error('[AitherHub BG] Failed to send data:', err);
    pendingData = data;
    if (err.message && err.message.includes('401')) {
      await tryRefreshToken();
    }
    updateBadge('ERR', '#FF1744');
  }
}

async function handleAiAnalyze(snapshot) {
  stats.aiAnalyses++;
  try {
    const result = await sendToAPI('/api/v1/live/ai/analyze', snapshot);
    return result;
  } catch (err) {
    if (err.message && err.message.includes('401')) {
      const refreshed = await tryRefreshToken();
      if (refreshed) {
        return await sendToAPI('/api/v1/live/ai/analyze', snapshot);
      }
    }
    throw err;
  }
}

function mergeData(existing, incoming) {
  return {
    ...incoming,
    metrics: { ...(existing.metrics || {}), ...(incoming.metrics || {}) },
    comments: [...(existing.comments || []), ...(incoming.comments || [])],
    activities: [...(existing.activities || []), ...(incoming.activities || [])],
    products: incoming.products || existing.products,
    trafficSources: incoming.trafficSources || existing.trafficSources
  };
}

async function sendToAPI(endpoint, payload) {
  const url = `${apiBase}${endpoint}`;
  const headers = { 'Content-Type': 'application/json' };
  if (apiToken) headers['Authorization'] = `Bearer ${apiToken}`;

  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function tryRefreshToken() {
  if (!refreshToken) return false;
  try {
    const response = await fetch(`${apiBase}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken })
    });
    if (!response.ok) {
      apiToken = '';
      refreshToken = '';
      await chrome.storage.local.remove(['accessToken', 'apiToken', 'refreshToken']);
      return false;
    }
    const data = await response.json();
    apiToken = data.access_token;
    refreshToken = data.refresh_token || refreshToken;
    await chrome.storage.local.set({
      accessToken: apiToken,
      apiToken: apiToken,
      refreshToken: refreshToken
    });
    return true;
  } catch (err) {
    return false;
  }
}

// ══════════════════════════════════════════════════════════════
// Badge & Alarms
// ══════════════════════════════════════════════════════════════

function updateBadge(text, color) {
  chrome.action.setBadgeText({ text });
  if (color) chrome.action.setBadgeBackgroundColor({ color });
}

// Periodic flush for legacy data
chrome.alarms.create('flushData', { periodInMinutes: 0.1 });

// Periodic flush for v2.0 event buffer
chrome.alarms.create('flushEventBuffer', { periodInMinutes: 0.083 }); // ~5 seconds

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === 'flushData' && pendingData) {
    const data = pendingData;
    pendingData = null;
    await handleLiveData(data, null);
  }

  if (alarm.name === 'flushEventBuffer') {
    if (SessionManager.hasSession() && EventBuffer._isRunning) {
      await EventBuffer.flush();
    }
  }
});
