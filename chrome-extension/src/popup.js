/**
 * AitherHub LIVE Connector - Popup Script v2.0.0
 * 
 * Session management, 2-screen connection status,
 * Start/Stop Tracking, real-time stats display.
 */

const API_BASE = 'https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net';

document.addEventListener('DOMContentLoaded', () => {
  // ============================================================
  // Elements
  // ============================================================

  // Login
  const loginSection = document.getElementById('loginSection');
  const dashboardSection = document.getElementById('dashboardSection');
  const emailInput = document.getElementById('email');
  const passwordInput = document.getElementById('password');
  const loginBtn = document.getElementById('loginBtn');
  const loginError = document.getElementById('loginError');

  // Dashboard
  const userAvatar = document.getElementById('userAvatar');
  const userName = document.getElementById('userName');
  const userEmail = document.getElementById('userEmail');
  const logoutBtn = document.getElementById('logoutBtn');
  const statusDot = document.getElementById('statusDot');
  const statusText = document.getElementById('statusText');

  // Connection cards
  const liveDot = document.getElementById('liveDot');
  const liveCardValue = document.getElementById('liveCardValue');
  const dashDot = document.getElementById('dashDot');
  const dashCardValue = document.getElementById('dashCardValue');

  // Session
  const sessionBadge = document.getElementById('sessionBadge');
  const sessionDuration = document.getElementById('sessionDuration');
  const sessionInfo = document.getElementById('sessionInfo');
  const trackingBtn = document.getElementById('trackingBtn');

  // Stats
  const statViewers = document.getElementById('statViewers');
  const statComments = document.getElementById('statComments');
  const statGMV = document.getElementById('statGMV');
  const statSales = document.getElementById('statSales');
  const statProducts = document.getElementById('statProducts');
  const statEvents = document.getElementById('statEvents');
  const lastSync = document.getElementById('lastSync');

  // ============================================================
  // Initialization
  // ============================================================
  chrome.storage.local.get(['accessToken', 'refreshToken', 'userEmail', 'userName'], (result) => {
    if (result.accessToken && result.userEmail) {
      showDashboard(result.userEmail, result.userName || '');
      refreshState();
    } else {
      showLogin();
    }
  });

  // ============================================================
  // Login Handler
  // ============================================================
  loginBtn.addEventListener('click', async () => {
    const email = emailInput.value.trim();
    const password = passwordInput.value.trim();

    if (!email) { showError('メールアドレスを入力してください'); return; }
    if (!password) { showError('パスワードを入力してください'); return; }

    loginBtn.disabled = true;
    loginBtn.innerHTML = '<span class="loading-spinner"></span>ログイン中...';
    hideError();

    try {
      const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `ログインに失敗しました (${response.status})`);
      }

      const data = await response.json();
      const accessToken = data.access_token;
      const refreshToken = data.refresh_token;

      if (!accessToken) throw new Error('トークンの取得に失敗しました');

      await chrome.storage.local.set({
        accessToken,
        refreshToken,
        apiToken: accessToken,
        apiBase: API_BASE,
        userEmail: email,
        userName: email.split('@')[0]
      });

      chrome.runtime.sendMessage({
        type: 'SET_CONFIG',
        apiBase: API_BASE,
        apiToken: accessToken
      });

      showDashboard(email, email.split('@')[0]);
      updateStatus('connected', 'ログイン成功');

    } catch (err) {
      console.error('[AitherHub] Login error:', err);
      showError(err.message);
    } finally {
      loginBtn.disabled = false;
      loginBtn.innerHTML = 'ログイン';
    }
  });

  passwordInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') loginBtn.click(); });
  emailInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') passwordInput.focus(); });

  // ============================================================
  // Logout Handler
  // ============================================================
  logoutBtn.addEventListener('click', async () => {
    // Stop tracking first if active
    chrome.runtime.sendMessage({ type: 'EXT_STOP_SESSION' });

    await chrome.storage.local.remove([
      'accessToken', 'refreshToken', 'apiToken', 'apiBase',
      'userEmail', 'userName', 'liveSessionId', 'extSessionId'
    ]);

    chrome.runtime.sendMessage({
      type: 'SET_CONFIG',
      apiBase: '',
      apiToken: ''
    });

    showLogin();
    updateStatus('disconnected', '未接続');
  });

  // ============================================================
  // Tracking Button
  // ============================================================
  trackingBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ type: 'EXT_GET_STATE' }, (state) => {
      if (state && state.hasSession && state.sessionStatus === 'active') {
        // Stop tracking
        chrome.runtime.sendMessage({ type: 'EXT_STOP_SESSION' }, () => {
          refreshState();
        });
      } else {
        // Start tracking
        chrome.runtime.sendMessage({ type: 'EXT_START_SESSION' }, (response) => {
          if (response && response.error) {
            sessionInfo.textContent = response.error;
          }
          refreshState();
        });
      }
    });
  });

  // ============================================================
  // State Refresh
  // ============================================================
  function refreshState() {
    chrome.runtime.sendMessage({ type: 'EXT_GET_STATE' }, (state) => {
      if (chrome.runtime.lastError || !state) {
        updateStatus('disconnected', '拡張機能エラー');
        return;
      }

      // Connection cards
      updateConnectionCard(liveDot, liveCardValue, state.liveConnected, 'LIVE');
      updateConnectionCard(dashDot, dashCardValue, state.dashboardConnected, 'Dashboard');

      // Session status
      const hasSession = state.hasSession;
      const sessionActive = state.sessionStatus === 'active';

      if (sessionActive) {
        sessionBadge.textContent = 'RECORDING';
        sessionBadge.className = 'session-badge active';
        updateStatus('recording', 'トラッキング中');

        trackingBtn.textContent = 'Stop Tracking';
        trackingBtn.className = 'btn-tracking stop';
        trackingBtn.disabled = false;

        // Duration
        if (state.sessionStartedAt) {
          const elapsed = Math.floor((Date.now() - new Date(state.sessionStartedAt).getTime()) / 1000);
          sessionDuration.textContent = formatDuration(elapsed);
        }

        sessionInfo.textContent = 'データ収集中...';

      } else if (state.liveConnected || state.dashboardConnected) {
        sessionBadge.textContent = 'READY';
        sessionBadge.className = 'session-badge waiting';
        updateStatus('connected', 'ログイン済み');

        trackingBtn.textContent = 'Start Tracking';
        trackingBtn.className = 'btn-tracking start';
        trackingBtn.disabled = false;

        const missing = [];
        if (!state.liveConnected) missing.push('LIVE配信画面');
        if (!state.dashboardConnected) missing.push('ダッシュボード');
        if (missing.length > 0) {
          sessionInfo.textContent = `${missing.join('と')}を開いてください（片方だけでも開始可能）`;
        } else {
          sessionInfo.textContent = '両画面接続済み。Start Trackingで開始';
        }

      } else {
        sessionBadge.textContent = 'NO SESSION';
        sessionBadge.className = 'session-badge idle';
        updateStatus('connected', 'ログイン済み');

        trackingBtn.textContent = 'Start Tracking';
        trackingBtn.className = 'btn-tracking start';
        trackingBtn.disabled = true;

        sessionInfo.textContent = 'LIVE配信画面またはダッシュボードを開いてください';
      }

      // Stats
      if (state.stats) {
        statViewers.textContent = state.stats.viewers != null ? formatNumber(state.stats.viewers) : '---';
        statComments.textContent = formatNumber(state.stats.comments || 0);
        statGMV.textContent = state.stats.gmv != null ? formatCurrency(state.stats.gmv) : '---';
        statSales.textContent = state.stats.sales != null ? formatNumber(state.stats.sales) : '---';
        statProducts.textContent = formatNumber(state.stats.products || 0);
        statEvents.textContent = formatNumber(state.stats.events || 0);
      }

      // Last sync
      if (state.lastSyncAt) {
        const ago = Math.floor((Date.now() - new Date(state.lastSyncAt).getTime()) / 1000);
        lastSync.textContent = `Last sync: ${ago}s ago`;
      }
    });
  }

  function updateConnectionCard(dot, valueEl, connected, label) {
    if (connected) {
      dot.className = 'dot on';
      valueEl.textContent = '接続中';
      valueEl.style.color = '#00e676';
    } else {
      dot.className = 'dot off';
      valueEl.textContent = '未接続';
      valueEl.style.color = '#888';
    }
  }

  // ============================================================
  // UI Helpers
  // ============================================================
  function showLogin() {
    loginSection.classList.remove('hidden');
    dashboardSection.classList.add('hidden');
  }

  function showDashboard(email, name) {
    loginSection.classList.add('hidden');
    dashboardSection.classList.remove('hidden');
    userEmail.textContent = email;
    userName.textContent = name || email.split('@')[0];
    userAvatar.textContent = (name || email).charAt(0).toUpperCase();
  }

  function showError(msg) {
    loginError.textContent = msg;
    loginError.style.display = 'block';
  }

  function hideError() {
    loginError.style.display = 'none';
  }

  function updateStatus(state, text) {
    statusDot.className = 'status-dot ' + state;
    statusText.textContent = text;
  }

  function formatDuration(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
  }

  function formatNumber(n) {
    if (n === null || n === undefined) return '---';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
  }

  function formatCurrency(n) {
    if (n === null || n === undefined) return '---';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return Math.round(n / 1000) + 'K';
    return String(Math.round(n));
  }

  // ============================================================
  // Auto-refresh
  // ============================================================
  setInterval(() => {
    if (!dashboardSection.classList.contains('hidden')) {
      refreshState();
    }
  }, 3000);
});
