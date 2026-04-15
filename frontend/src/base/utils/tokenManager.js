const TOKEN_KEY = 'app_access_token';
const REFRESH_KEY = 'app_refresh_token';

// Legacy keys from older versions of the app
const LEGACY_KEYS = [
  'aitherhub_token',
  'aitherhub_refresh_token',
  'token',
  'refresh_token',
  'access_token',
];

function setToken(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
    return true;
  } catch (e) {
    console.error('Failed to store token', e);
    return false;
  }
}

function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

function setRefreshToken(token) {
  try {
    localStorage.setItem(REFRESH_KEY, token);
    return true;
  } catch (e) {
    console.error('Failed to store refresh token', e);
    return false;
  }
}

function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY);
}

/**
 * Clear all auth-related data from localStorage and sessionStorage.
 * This includes current tokens, legacy token keys, user data, and API cache.
 */
function clearTokens() {
  // Clear current token keys
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);

  // Clear legacy token keys from older app versions
  LEGACY_KEYS.forEach(key => {
    try { localStorage.removeItem(key); } catch (e) { /* ignore */ }
  });

  // Clear user data
  localStorage.removeItem('user');

  // Clear API response cache from sessionStorage to prevent stale data
  // after user switch
  _clearApiCache();
}

/**
 * Clear all api_cache: entries from sessionStorage.
 * This prevents stale cached responses from a previous user session
 * being served after login/logout.
 */
function _clearApiCache() {
  try {
    const keysToRemove = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i);
      if (key && key.startsWith('api_cache:')) {
        keysToRemove.push(key);
      }
    }
    keysToRemove.forEach(key => sessionStorage.removeItem(key));
  } catch (e) {
    // sessionStorage might not be available in some contexts
  }
}

function _parseJwt(token) {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length < 2) return null;
  try {
    const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const decoded = atob(payload);
    return JSON.parse(decoded);
  } catch (e) {
    return null;
  }
}

function isTokenExpired(token) {
  const payload = _parseJwt(token);
  if (!payload) return true;
  if (!payload.exp) return false; // not JWT or no exp
  const exp = payload.exp; // exp is unix timestamp in seconds
  return Date.now() / 1000 > exp;
}

function isValidJWT(token) {
  return !!_parseJwt(token);
}

/**
 * Get the user ID (sub claim) from the current access token.
 * Returns null if no token or invalid.
 */
function getTokenUserId() {
  const token = getToken();
  const payload = _parseJwt(token);
  return payload?.sub || null;
}

export default {
  setToken,
  getToken,
  setRefreshToken,
  getRefreshToken,
  clearTokens,
  isTokenExpired,
  isValidJWT,
  getTokenUserId,
};
