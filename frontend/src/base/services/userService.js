import BaseApiService from '../api/BaseApiService';
import { URL_CONSTANTS } from '../api/endpoints/constant';
import TokenManager from '../utils/tokenManager';

class AuthService extends BaseApiService {
  constructor() {
    super(import.meta.env.VITE_API_BASE_URL);
  }

  async login(email, password) {
    // CRITICAL: Clear ALL previous auth state before login.
    // This prevents stale tokens from a previous user session from
    // interfering with the new login (e.g., axios interceptor using
    // old token for refresh, or cached user data being served).
    TokenManager.clearTokens();

    const resp = await this.post(URL_CONSTANTS.LOGIN, {
      email,
      password,
    });

    // store tokens if returned
    if (resp?.access_token) {
      try {
        TokenManager.setToken(resp.access_token);
        if (resp.refresh_token) TokenManager.setRefreshToken(resp.refresh_token);
      } catch (e) {
        console.warn('Failed to store tokens', e);
      }
    }

    return resp;
  }

  async register(email, password) {
    // Clear previous auth state before registration too
    TokenManager.clearTokens();

    const resp = await this.post(URL_CONSTANTS.REGISTER, { email, password });
    
    // store tokens if returned
    if (resp?.access_token) {
      try {
        TokenManager.setToken(resp.access_token);
        if (resp.refresh_token) TokenManager.setRefreshToken(resp.refresh_token);
      } catch (e) {
        console.warn('Failed to store tokens', e);
      }
    }
    
    return resp;
  }

  async getCurrentUser() {
    return await this.get(URL_CONSTANTS.ME);
  }

  logout() {
    TokenManager.clearTokens();
    // Note: clearTokens() already removes 'user' from localStorage
    // and clears sessionStorage API cache
  }

  async changePassword(currentPassword, newPassword, confirmPassword) {
    try {
      const response = await this.post(URL_CONSTANTS.CHANGE_PASSWORD, {
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword,
      });
      return response;
    } catch (error) {
      console.error("Error in changePassword:", error);
      throw error;
    }
  }

  async forgotPassword(email) {
    try {
      const response = await this.post(URL_CONSTANTS.FORGOT_PASSWORD, {
        email,
      });
      return response;
    } catch (error) {
      console.error("Error in forgotPassword:", error);
      throw error;
    }
  }

  async resetPassword(email, token, password, password_confirmation) {
    try {
      const response = await this.post(URL_CONSTANTS.RESET_PASSWORD, {
        email,
        token,
        password,
        password_confirmation,
      });
      return response;
    } catch (error) {
      console.error("Error in resetPassword:", error);
      throw error;
    }
  }
}

export default new AuthService();
