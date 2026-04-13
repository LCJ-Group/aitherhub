import { useState, useEffect, useCallback } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

export default function AdminWidgetManager({ adminKey }) {
  const [clients, setClients] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingClient, setEditingClient] = useState(null);
  const [tagSnippet, setTagSnippet] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [selectedClientForClips, setSelectedClientForClips] = useState(null);
  const [clipAssignments, setClipAssignments] = useState([]);
  const [newClipId, setNewClipId] = useState("");
  const [newPagePattern, setNewPagePattern] = useState("");

  // Form state
  const [form, setForm] = useState({
    name: "",
    domain: "",
    theme_color: "#FF2D55",
    position: "bottom-right",
    cta_text: "購入する",
    cta_url_template: "",
    cart_selector: "",
  });

  const headers = { "X-Admin-Key": adminKey };

  // ── Fetch clients ──
  const fetchClients = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await axios.get(`${API_BASE}/api/v1/widget/admin/clients`, { headers });
      setClients(res.data.clients || []);
    } catch (err) {
      setError(`クライアント取得失敗: ${err.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  }, [adminKey]);

  useEffect(() => {
    fetchClients();
  }, [fetchClients]);

  // ── Fetch analytics ──
  const fetchAnalytics = useCallback(async () => {
    try {
      setAnalyticsLoading(true);
      const res = await axios.get(`${API_BASE}/api/v1/widget/admin/analytics`, { headers });
      setAnalytics(res.data);
    } catch (err) {
      console.error("Analytics fetch failed:", err);
    } finally {
      setAnalyticsLoading(false);
    }
  }, [adminKey]);

  useEffect(() => {
    fetchAnalytics();
  }, [fetchAnalytics]);

  // ── Create client ──
  const handleCreate = async () => {
    try {
      setError(null);
      await axios.post(`${API_BASE}/api/v1/widget/admin/clients`, form, { headers });
      setShowCreateForm(false);
      setForm({ name: "", domain: "", theme_color: "#FF2D55", position: "bottom-right", cta_text: "購入する", cta_url_template: "", cart_selector: "" });
      fetchClients();
    } catch (err) {
      setError(`作成失敗: ${err.response?.data?.detail || err.message}`);
    }
  };

  // ── Update client ──
  const handleUpdate = async () => {
    if (!editingClient) return;
    try {
      setError(null);
      await axios.put(`${API_BASE}/api/v1/widget/admin/clients/${editingClient}`, form, { headers });
      setEditingClient(null);
      setForm({ name: "", domain: "", theme_color: "#FF2D55", position: "bottom-right", cta_text: "購入する", cta_url_template: "", cart_selector: "" });
      fetchClients();
    } catch (err) {
      setError(`更新失敗: ${err.response?.data?.detail || err.message}`);
    }
  };

  // ── Get GTM tag ──
  const handleGetTag = async (clientId) => {
    try {
      const res = await axios.get(`${API_BASE}/api/v1/widget/admin/clients/${clientId}/tag`, { headers });
      setTagSnippet(res.data);
    } catch (err) {
      setError(`タグ取得失敗: ${err.response?.data?.detail || err.message}`);
    }
  };

  // ── Fetch clip assignments ──
  const fetchClipAssignments = async (clientId) => {
    try {
      const res = await axios.get(`${API_BASE}/api/v1/widget/config/${clientId}`);
      setClipAssignments(res.data.clips || []);
    } catch (err) {
      setClipAssignments([]);
    }
  };

  // ── Assign clip ──
  const handleAssignClip = async () => {
    if (!selectedClientForClips || !newClipId) return;
    try {
      setError(null);
      await axios.post(`${API_BASE}/api/v1/widget/admin/clients/${selectedClientForClips}/clips`, {
        clip_id: newClipId,
        page_url_pattern: newPagePattern || null,
      }, { headers });
      setNewClipId("");
      setNewPagePattern("");
      fetchClipAssignments(selectedClientForClips);
    } catch (err) {
      setError(`クリップ割当失敗: ${err.response?.data?.detail || err.message}`);
    }
  };

  // ── Edit button handler ──
  const startEdit = (client) => {
    setEditingClient(client.client_id);
    setForm({
      name: client.name,
      domain: client.domain,
      theme_color: client.theme_color || "#FF2D55",
      position: client.position || "bottom-right",
      cta_text: client.cta_text || "購入する",
      cta_url_template: client.cta_url_template || "",
      cart_selector: client.cart_selector || "",
    });
    setShowCreateForm(false);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2">
            🎯 ウィジェット管理
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            GTM経由で配信するフローティング動画プレイヤーの設定
          </p>
        </div>
        <button
          onClick={() => { setShowCreateForm(!showCreateForm); setEditingClient(null); setForm({ name: "", domain: "", theme_color: "#FF2D55", position: "bottom-right", cta_text: "購入する", cta_url_template: "", cart_selector: "" }); }}
          className="px-4 py-2 bg-pink-600 text-white rounded-lg hover:bg-pink-700 transition-colors text-sm font-medium"
        >
          + 新規クライアント
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Analytics Summary */}
      {analytics && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {analytics.summary?.map((item, i) => (
            <div key={i} className="bg-white rounded-lg border p-4">
              <div className="text-2xl font-bold text-gray-800">{item.count?.toLocaleString() || 0}</div>
              <div className="text-xs text-gray-500 mt-1">{item.event_type}</div>
            </div>
          ))}
        </div>
      )}

      {/* Create/Edit Form */}
      {(showCreateForm || editingClient) && (
        <div className="bg-white rounded-lg border p-6 space-y-4">
          <h3 className="font-semibold text-gray-700">
            {editingClient ? "クライアント編集" : "新規クライアント作成"}
          </h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">ブランド名 *</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg text-sm"
                placeholder="例: KYOGOKU Professional"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">ドメイン *</label>
              <input
                type="text"
                value={form.domain}
                onChange={(e) => setForm({ ...form, domain: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg text-sm"
                placeholder="例: kyogokupro.com"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">テーマカラー</label>
              <div className="flex gap-2 items-center">
                <input
                  type="color"
                  value={form.theme_color}
                  onChange={(e) => setForm({ ...form, theme_color: e.target.value })}
                  className="w-10 h-10 rounded border cursor-pointer"
                />
                <input
                  type="text"
                  value={form.theme_color}
                  onChange={(e) => setForm({ ...form, theme_color: e.target.value })}
                  className="flex-1 px-3 py-2 border rounded-lg text-sm"
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">表示位置</label>
              <select
                value={form.position}
                onChange={(e) => setForm({ ...form, position: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg text-sm"
              >
                <option value="bottom-right">右下</option>
                <option value="bottom-left">左下</option>
                <option value="top-right">右上</option>
                <option value="top-left">左上</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">CTAボタンテキスト</label>
              <input
                type="text"
                value={form.cta_text}
                onChange={(e) => setForm({ ...form, cta_text: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg text-sm"
                placeholder="例: 購入する"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">カートセレクタ (CSS)</label>
              <input
                type="text"
                value={form.cart_selector}
                onChange={(e) => setForm({ ...form, cart_selector: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg text-sm"
                placeholder="例: #add-to-cart, .btn-cart"
              />
            </div>
            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-600 mb-1">CTA URL テンプレート</label>
              <input
                type="text"
                value={form.cta_url_template}
                onChange={(e) => setForm({ ...form, cta_url_template: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg text-sm"
                placeholder="例: https://example.com/cart?product={product}"
              />
              <p className="text-xs text-gray-400 mt-1">{"{product}"} は商品名に置換されます</p>
            </div>
          </div>
          <div className="flex gap-2 pt-2">
            <button
              onClick={editingClient ? handleUpdate : handleCreate}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium"
            >
              {editingClient ? "更新" : "作成"}
            </button>
            <button
              onClick={() => { setShowCreateForm(false); setEditingClient(null); }}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 text-sm"
            >
              キャンセル
            </button>
          </div>
        </div>
      )}

      {/* GTM Tag Snippet Modal */}
      {tagSnippet && (
        <div className="bg-gray-900 rounded-lg p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-white flex items-center gap-2">
              📋 GTMタグ — {tagSnippet.client_name}
            </h3>
            <button
              onClick={() => setTagSnippet(null)}
              className="text-gray-400 hover:text-white"
            >
              ✕
            </button>
          </div>
          <div className="space-y-3">
            <div>
              <p className="text-xs text-gray-400 mb-1">GTM カスタムHTMLタグ（これをコピーして貼り付け）:</p>
              <div className="bg-black rounded-lg p-4 relative group">
                <pre className="text-green-400 text-sm font-mono whitespace-pre-wrap break-all">
                  {tagSnippet.gtm_custom_html}
                </pre>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(tagSnippet.gtm_custom_html);
                  }}
                  className="absolute top-2 right-2 px-3 py-1 bg-gray-700 text-gray-300 rounded text-xs hover:bg-gray-600 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  コピー
                </button>
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-400 mb-1">直接埋め込み用（HTML）:</p>
              <div className="bg-black rounded-lg p-4 relative group">
                <pre className="text-blue-400 text-sm font-mono whitespace-pre-wrap break-all">
                  {tagSnippet.direct_embed}
                </pre>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(tagSnippet.direct_embed);
                  }}
                  className="absolute top-2 right-2 px-3 py-1 bg-gray-700 text-gray-300 rounded text-xs hover:bg-gray-600 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  コピー
                </button>
              </div>
            </div>
            <div className="bg-yellow-900/30 rounded-lg p-3">
              <p className="text-yellow-300 text-xs">
                💡 GTMで「カスタムHTML」タグを新規作成し、上のコードを貼り付けて「すべてのページ」トリガーで公開してください。
                Facebookピクセルと同じ手順です。
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Client List */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-pink-500"></div>
        </div>
      ) : clients.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <p className="text-4xl mb-2">🎯</p>
          <p>ウィジェットクライアントがまだありません</p>
          <p className="text-sm mt-1">「新規クライアント」ボタンから追加してください</p>
        </div>
      ) : (
        <div className="space-y-3">
          {clients.map((client) => (
            <div key={client.client_id} className="bg-white rounded-lg border p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div
                    className="w-4 h-4 rounded-full"
                    style={{ backgroundColor: client.theme_color || "#FF2D55" }}
                  />
                  <div>
                    <h4 className="font-semibold text-gray-800">{client.name}</h4>
                    <p className="text-xs text-gray-500">{client.domain} — ID: {client.client_id}</p>
                  </div>
                  <span className={`px-2 py-0.5 rounded-full text-xs ${client.is_active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                    {client.is_active ? "有効" : "無効"}
                  </span>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      if (selectedClientForClips === client.client_id) {
                        setSelectedClientForClips(null);
                      } else {
                        setSelectedClientForClips(client.client_id);
                        fetchClipAssignments(client.client_id);
                      }
                    }}
                    className="px-3 py-1.5 bg-purple-100 text-purple-700 rounded-lg text-xs hover:bg-purple-200"
                  >
                    🎬 クリップ
                  </button>
                  <button
                    onClick={() => handleGetTag(client.client_id)}
                    className="px-3 py-1.5 bg-green-100 text-green-700 rounded-lg text-xs hover:bg-green-200"
                  >
                    📋 GTMタグ
                  </button>
                  <button
                    onClick={() => startEdit(client)}
                    className="px-3 py-1.5 bg-blue-100 text-blue-700 rounded-lg text-xs hover:bg-blue-200"
                  >
                    編集
                  </button>
                </div>
              </div>

              {/* Clip Assignment Panel */}
              {selectedClientForClips === client.client_id && (
                <div className="mt-4 pt-4 border-t space-y-3">
                  <h5 className="text-sm font-medium text-gray-600">割り当てクリップ</h5>
                  {clipAssignments.length > 0 ? (
                    <div className="space-y-2">
                      {clipAssignments.map((clip, i) => (
                        <div key={i} className="flex items-center gap-3 bg-gray-50 rounded-lg p-3">
                          {clip.thumbnail_url && (
                            <img src={clip.thumbnail_url} alt="" className="w-12 h-16 object-cover rounded" />
                          )}
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-gray-700 truncate">{clip.product_name || clip.liver_name || "Untitled"}</p>
                            <p className="text-xs text-gray-400">ID: {clip.clip_id}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-gray-400">クリップが割り当てられていません</p>
                  )}
                  <div className="flex gap-2 items-end">
                    <div className="flex-1">
                      <label className="block text-xs text-gray-500 mb-1">クリップID</label>
                      <input
                        type="text"
                        value={newClipId}
                        onChange={(e) => setNewClipId(e.target.value)}
                        className="w-full px-3 py-2 border rounded-lg text-sm"
                        placeholder="クリップDBからIDをコピー"
                      />
                    </div>
                    <div className="flex-1">
                      <label className="block text-xs text-gray-500 mb-1">ページURLパターン（任意）</label>
                      <input
                        type="text"
                        value={newPagePattern}
                        onChange={(e) => setNewPagePattern(e.target.value)}
                        className="w-full px-3 py-2 border rounded-lg text-sm"
                        placeholder="例: /products/*"
                      />
                    </div>
                    <button
                      onClick={handleAssignClip}
                      className="px-4 py-2 bg-purple-600 text-white rounded-lg text-sm hover:bg-purple-700 whitespace-nowrap"
                    >
                      割当
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Per-client analytics */}
      {analytics && analytics.per_client && analytics.per_client.length > 0 && (
        <div className="bg-white rounded-lg border p-4">
          <h3 className="font-semibold text-gray-700 mb-3">クライアント別イベント数</h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                <th className="text-left py-2 text-gray-500">Client ID</th>
                <th className="text-left py-2 text-gray-500">イベント種別</th>
                <th className="text-right py-2 text-gray-500">件数</th>
              </tr>
            </thead>
            <tbody>
              {analytics.per_client.map((row, i) => (
                <tr key={i} className="border-b border-gray-100">
                  <td className="py-2 font-mono text-xs">{row.client_id}</td>
                  <td className="py-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${
                      row.event_type === "conversion" ? "bg-green-100 text-green-700" :
                      row.event_type === "cta_click" ? "bg-pink-100 text-pink-700" :
                      row.event_type === "video_play" ? "bg-blue-100 text-blue-700" :
                      "bg-gray-100 text-gray-600"
                    }`}>
                      {row.event_type}
                    </span>
                  </td>
                  <td className="py-2 text-right font-medium">{row.count?.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
