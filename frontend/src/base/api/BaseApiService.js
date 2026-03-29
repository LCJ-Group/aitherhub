import axios from "axios";
import TokenManager from "../utils/tokenManager";
import AuthService from "../services/userService";
import { generateRequestId } from "../utils/runtimeErrorLogger";

/**
 * Default request timeout in milliseconds.
 * Prevents requests from hanging indefinitely (e.g. Azure cold-start).
 */
const DEFAULT_TIMEOUT_MS = 30000; // 30 seconds

/**
 * Maximum time (ms) a queued request will wait for a token refresh to complete.
 * Prevents deadlocks when the refresh itself hangs.
 */
const REFRESH_QUEUE_TIMEOUT_MS = 15000; // 15 seconds

/**
 * Endpoints that do NOT require authentication (no Bearer token needed).
 * These are the only endpoints where we skip the Authorization header.
 */
const PUBLIC_AUTH_ENDPOINTS = [
  '/auth/login',
  '/auth/register',
  '/auth/refresh',
  '/auth/forgot',
  '/auth/reset',
];

/**
 * Check if a URL is a public auth endpoint (login, register, refresh, etc.)
 * These endpoints do NOT need a Bearer token.
 */
function isPublicAuthEndpoint(url) {
  if (!url) return false;
  const u = String(url);
  return PUBLIC_AUTH_ENDPOINTS.some(ep => u.includes(ep));
}

/**
 * In-memory + sessionStorage cache for GET requests.
 * Uses stale-while-revalidate strategy: returns cached data immediately,
 * then revalidates in the background.
 */
const API_CACHE = {
  _mem: new Map(),
  _TTL: 5 * 60 * 1000, // 5 minutes default TTL
  _STALE_TTL: 30 * 60 * 1000, // 30 minutes stale TTL (still usable while revalidating)

  _key(url) {
    return `api_cache:${url}`;
  },

  get(url) {
    const key = this._key(url);
    // Try memory first
    const mem = this._mem.get(key);
    if (mem) return mem;
    // Try sessionStorage
    try {
      const raw = sessionStorage.getItem(key);
      if (raw) {
        const parsed = JSON.parse(raw);
        this._mem.set(key, parsed); // promote to memory
        return parsed;
      }
    } catch (e) { /* ignore */ }
    return null;
  },

  set(url, data) {
    const key = this._key(url);
    const entry = { data, ts: Date.now() };
    this._mem.set(key, entry);
    try {
      sessionStorage.setItem(key, JSON.stringify(entry));
    } catch (e) {
      // sessionStorage full – evict oldest entries
      this._evict();
      try { sessionStorage.setItem(key, JSON.stringify(entry)); } catch (e2) { /* give up */ }
    }
  },

  isFresh(url) {
    const entry = this.get(url);
    return entry && (Date.now() - entry.ts) < this._TTL;
  },

  isStale(url) {
    const entry = this.get(url);
    return entry && (Date.now() - entry.ts) >= this._TTL && (Date.now() - entry.ts) < this._STALE_TTL;
  },

  invalidate(urlPattern) {
    // Invalidate all cache entries matching a pattern
    const keysToDelete = [];
    this._mem.forEach((_, key) => {
      if (key.includes(urlPattern)) keysToDelete.push(key);
    });
    keysToDelete.forEach(k => {
      this._mem.delete(k);
      try { sessionStorage.removeItem(k); } catch (e) { /* ignore */ }
    });
  },

  _evict() {
    // Remove oldest 20% of entries from sessionStorage
    try {
      const entries = [];
      for (let i = 0; i < sessionStorage.length; i++) {
        const key = sessionStorage.key(i);
        if (key?.startsWith('api_cache:')) {
          try {
            const val = JSON.parse(sessionStorage.getItem(key));
            entries.push({ key, ts: val.ts || 0 });
          } catch (e) { entries.push({ key, ts: 0 }); }
        }
      }
      entries.sort((a, b) => a.ts - b.ts);
      const removeCount = Math.max(1, Math.floor(entries.length * 0.2));
      for (let i = 0; i < removeCount; i++) {
        sessionStorage.removeItem(entries[i].key);
        this._mem.delete(entries[i].key.replace('api_cache:', ''));
      }
    } catch (e) { /* ignore */ }
  },
};

// Endpoints that should NOT be cached (mutations, auth, real-time data)
const NO_CACHE_PATTERNS = [
  '/auth/',
  '/upload',
  '/process',
  '/generate',
  '/export',
];

function shouldCache(url) {
  if (!url) return false;
  return !NO_CACHE_PATTERNS.some(p => url.includes(p));
}

export default class BaseApiService {
  constructor(baseURL) {
    this.client = axios.create({
      baseURL,
      timeout: DEFAULT_TIMEOUT_MS,
      headers: {
        "Content-Type": "application/json",
      },
    });

    let isRefreshing = false;
    let failedQueue = [];

    const processQueue = (error, token = null) => {
      failedQueue.forEach(prom => {
        if (error) {
          prom.reject(error);
        } else {
          prom.resolve(token);
        }
      });
      failedQueue = [];
    };

    const handleAutoLogout = () => {
      // First, perform logout (clear tokens and user data)
      AuthService.logout();
      // Then dispatch event to open login modal
      // Use setTimeout to ensure logout completes before opening modal
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent('openLoginModal'));
      }, 0);
    };

    /**
     * Wait for an in-progress token refresh with a timeout.
     * Returns the new token on success, or null on timeout / failure.
     */
    const waitForRefresh = () => {
      return new Promise((resolve) => {
        const timer = setTimeout(() => {
          // Remove ourselves from the queue so processQueue won't call stale resolve/reject
          const idx = failedQueue.findIndex(p => p._timer === timer);
          if (idx !== -1) failedQueue.splice(idx, 1);
          console.warn('[BaseApiService] Timed out waiting for token refresh');
          resolve(null);
        }, REFRESH_QUEUE_TIMEOUT_MS);

        const entry = {
          _timer: timer,
          resolve: (token) => { clearTimeout(timer); resolve(token); },
          reject: () => { clearTimeout(timer); resolve(null); },
        };
        failedQueue.push(entry);
      });
    };

    /**
     * Attempt to refresh the access token using the refresh token.
     * Returns the new access token on success, or null on failure.
     */
    const tryRefreshToken = async () => {
      const refreshToken = TokenManager.getRefreshToken();
      if (!refreshToken || TokenManager.isTokenExpired(refreshToken)) {
        return null;
      }
      try {
        const response = await axios.post(baseURL + "/api/v1/auth/refresh", {
          refresh_token: refreshToken,
        }, { timeout: 15000 });
        const { token, refreshToken: newRefreshToken } = response.data;
        const tokenStored = TokenManager.setToken(token);
        if (newRefreshToken) {
          TokenManager.setRefreshToken(newRefreshToken);
        }
        return tokenStored ? token : null;
      } catch (e) {
        console.warn('[BaseApiService] Token refresh failed:', e.message);
        return null;
      }
    };

    this.client.interceptors.request.use(
      async (config) => {
        // Attach X-Request-Id for log correlation (frontend ↔ backend)
        const requestId = generateRequestId();
        config.headers['X-Request-Id'] = requestId;
        // Store on config for downstream error logging
        config._requestId = requestId;

        const requestUrl = config.url || '';

        // Public auth endpoints (login, register, refresh) don't need a token
        if (isPublicAuthEndpoint(requestUrl)) {
          return config;
        }

        let token = TokenManager.getToken();

        if (token && TokenManager.isTokenExpired(token)) {
          // Access token expired – try to refresh proactively
          console.info('[BaseApiService] Access token expired, attempting proactive refresh...');
          if (!isRefreshing) {
            isRefreshing = true;
            try {
              const newToken = await tryRefreshToken();
              if (newToken) {
                processQueue(null, newToken);
                token = newToken;
              } else {
                processQueue(new Error('Token refresh failed'), null);
                token = null;
              }
            } finally {
              isRefreshing = false;
            }
          } else {
            // Another refresh is in progress – wait with timeout
            token = await waitForRefresh();
          }
        }

        if (token) {
          config.headers.Authorization = "Bearer " + token;
        }
        return config;
      },
      (error) => {
        return Promise.reject(error);
      }
    );

    this.client.interceptors.response.use(
      (response) => response,
      async (error) => {
        const originalRequest = error.config;
        const requestUrl = originalRequest?.url || '';
        const isPublicAuth = isPublicAuthEndpoint(requestUrl);
        const status = error.response?.status;

        // Handle 401 Unauthorized or 403 "Not authenticated"
        const isAuthError = status === 401 || status === 403;

        if (isAuthError && !originalRequest._retry) {
          // Don't retry or auto-refresh for public auth endpoints (login/register)
          if (isPublicAuth) {
            return Promise.reject(error);
          }

          // If already refreshing, queue this request (with timeout)
          if (isRefreshing) {
            const token = await waitForRefresh();
            if (token) {
              originalRequest.headers.Authorization = "Bearer " + token;
              originalRequest._retry = true;
              return this.client(originalRequest);
            }
            // Refresh failed or timed out – propagate original error
            return Promise.reject(error);
          }

          originalRequest._retry = true;
          isRefreshing = true;

          try {
            const newToken = await tryRefreshToken();

            if (newToken) {
              processQueue(null, newToken);
              originalRequest.headers.Authorization = "Bearer " + newToken;
              return this.client(originalRequest);
            } else {
              throw new Error('No valid refresh token available');
            }
          } catch (refreshError) {
            processQueue(refreshError, null);
            handleAutoLogout();
            return Promise.reject(refreshError);
          } finally {
            isRefreshing = false;
          }
        }

        return Promise.reject(error);
      }
    );
  }

  async post(url, data, config = {}) {
    const res = await this.client.post(url, data, config);
    // Invalidate related GET caches on mutation
    const basePath = url.split('?')[0];
    API_CACHE.invalidate(basePath.split('/').slice(0, -1).join('/'));
    return res.data;
  }

  async get(url, config = {}) {
    const fullUrl = url;
    const useCache = shouldCache(fullUrl) && !config.noCache;

    if (useCache) {
      const cached = API_CACHE.get(fullUrl);
      if (cached) {
        if (API_CACHE.isFresh(fullUrl)) {
          // Fresh cache – return immediately, no network request
          return cached.data;
        }
        if (API_CACHE.isStale(fullUrl)) {
          // Stale cache – return immediately, revalidate in background
          this.client.get(url, config).then(res => {
            API_CACHE.set(fullUrl, res.data);
          }).catch(() => { /* background revalidation failed, keep stale */ });
          return cached.data;
        }
      }
    }

    // No cache or expired – fetch from network
    const res = await this.client.get(url, config);
    if (useCache) {
      API_CACHE.set(fullUrl, res.data);
    }
    return res.data;
  }

  async delete(url, config = {}) {
    const res = await this.client.delete(url, config);
    API_CACHE.invalidate(url.split('?')[0]);
    return res.data;
  }

  async put(url, data, config = {}) {
    const res = await this.client.put(url, data, config);
    API_CACHE.invalidate(url.split('?')[0]);
    return res.data;
  }

  async patch(url, data, config = {}) {
    const res = await this.client.patch(url, data, config);
    API_CACHE.invalidate(url.split('?')[0]);
    return res.data;
  }
}
