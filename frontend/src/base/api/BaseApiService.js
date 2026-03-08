import axios from "axios";
import TokenManager from "../utils/tokenManager";
import AuthService from "../services/userService";
import { generateRequestId } from "../utils/runtimeErrorLogger";

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

export default class BaseApiService {
  constructor(baseURL) {
    this.client = axios.create({
      baseURL,
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
        });
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
            const newToken = await tryRefreshToken();
            isRefreshing = false;
            if (newToken) {
              processQueue(null, newToken);
              token = newToken;
            } else {
              processQueue(new Error('Token refresh failed'), null);
              token = null;
            }
          } else {
            // Another refresh is in progress – wait for it
            token = await new Promise((resolve, reject) => {
              failedQueue.push({ resolve, reject });
            }).catch(() => null);
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

          // If already refreshing, queue this request
          if (isRefreshing) {
            return new Promise((resolve, reject) => {
              failedQueue.push({ resolve, reject });
            }).then(token => {
              originalRequest.headers.Authorization = "Bearer " + token;
              return this.client(originalRequest);
            }).catch(err => {
              return Promise.reject(err);
            });
          }

          originalRequest._retry = true;
          isRefreshing = true;

          try {
            const newToken = await tryRefreshToken();
            isRefreshing = false;

            if (newToken) {
              processQueue(null, newToken);
              originalRequest.headers.Authorization = "Bearer " + newToken;
              return this.client(originalRequest);
            } else {
              throw new Error('No valid refresh token available');
            }
          } catch (refreshError) {
            isRefreshing = false;
            processQueue(refreshError, null);
            handleAutoLogout();
            return Promise.reject(refreshError);
          }
        }

        return Promise.reject(error);
      }
    );
  }

  async post(url, data, config = {}) {
    const res = await this.client.post(url, data, config);
    return res.data;
  }

  async get(url, config = {}) {
    const res = await this.client.get(url, config);
    return res.data;
  }

  async delete(url, config = {}) {
    const res = await this.client.delete(url, config);
    return res.data;
  }

  async put(url, data, config = {}) {
    const res = await this.client.put(url, data, config);
    return res.data;
  }

  async patch(url, data, config = {}) {
    const res = await this.client.patch(url, data, config);
    return res.data;
  }
}
