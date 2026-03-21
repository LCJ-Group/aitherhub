/**
 * Persona Service — API client for Liver Clone (Persona) management
 */

const API_BASE = import.meta.env.VITE_API_URL || "";
const ADMIN_KEY = "aither:hub";

class PersonaService {
  async _request(method, path, body = null) {
    const opts = {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Key": ADMIN_KEY,
      },
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(`${API_BASE}/api/v1/personas${path}`, opts);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`API error ${res.status}: ${text}`);
    }
    return res.json();
  }

  // ── CRUD ──

  async listPersonas() {
    return this._request("GET", "");
  }

  async getPersona(id) {
    return this._request("GET", `/${id}`);
  }

  async createPersona(data) {
    return this._request("POST", "", data);
  }

  async updatePersona(id, data) {
    return this._request("PUT", `/${id}`, data);
  }

  async deletePersona(id) {
    return this._request("DELETE", `/${id}`);
  }

  // ── Video Tagging ──

  async tagVideos(personaId, videoIds) {
    return this._request("POST", `/${personaId}/tag-videos`, { video_ids: videoIds });
  }

  async untagVideos(personaId, videoIds) {
    return this._request("POST", `/${personaId}/untag-videos`, { video_ids: videoIds });
  }

  // ── Dataset & Training ──

  async getDatasetPreview(personaId) {
    return this._request("GET", `/${personaId}/dataset-preview`);
  }

  async startTraining(personaId, options = {}) {
    return this._request("POST", `/${personaId}/train`, options);
  }

  async getTrainingStatus(personaId) {
    return this._request("GET", `/${personaId}/training-status`);
  }
}

const personaService = new PersonaService();
export default personaService;
