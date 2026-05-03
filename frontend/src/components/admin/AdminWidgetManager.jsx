import { useState, useEffect, useCallback, useRef } from "react";
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
  // Product info fields for clip assignment
  const [showProductFields, setShowProductFields] = useState(false);
  // Editing existing clip product info
  const [editingClipId, setEditingClipId] = useState(null);
  const [editProductForm, setEditProductForm] = useState({});
  // Video preview modal
  const [previewClip, setPreviewClip] = useState(null);
  // Brand login info
  const [brandLoginInfo, setBrandLoginInfo] = useState({});
  // Clip search/picker modal
  const [showClipPicker, setShowClipPicker] = useState(false);
  const [clipSearchQuery, setClipSearchQuery] = useState("");
  const [clipSearchResults, setClipSearchResults] = useState([]);
  const [clipSearchTotal, setClipSearchTotal] = useState(0);
  const [clipSearchLoading, setClipSearchLoading] = useState(false);
  const [clipSearchOffset, setClipSearchOffset] = useState(0);

  // Form state
  const [form, setForm] = useState({
    name: "",
    domain: "",
    theme_color: "#FF2D55",
    position: "bottom-right",
    cta_text: "購入する",
    cta_url_template: "",
    cart_selector: "",
    brand_keywords: "",
    fab_type: "circle",
    fab_shape: "round",
    fab_size: "medium",
    fab_image_url: "",
    fab_banner_width: 300,
    fab_banner_height: 80,
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

  useEffect(() => { fetchClients(); }, [fetchClients]);

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

  useEffect(() => { fetchAnalytics(); }, [fetchAnalytics]);

  // ── Create client ──
  const handleCreate = async () => {
    try {
      setError(null);
      await axios.post(`${API_BASE}/api/v1/widget/admin/clients`, form, { headers });
      setShowCreateForm(false);
      setForm({ name: "", domain: "", theme_color: "#FF2D55", position: "bottom-right", cta_text: "購入する", cta_url_template: "", cart_selector: "", brand_keywords: "", fab_type: "circle", fab_shape: "round", fab_size: "medium", fab_image_url: "", fab_banner_width: 300, fab_banner_height: 80 });
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
      setForm({ name: "", domain: "", theme_color: "#FF2D55", position: "bottom-right", cta_text: "購入する", cta_url_template: "", cart_selector: "", brand_keywords: "", fab_type: "circle", fab_shape: "round", fab_size: "medium", fab_image_url: "", fab_banner_width: 300, fab_banner_height: 80 });
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

  // ── Reset brand password & show login info ──
  const handleResetPassword = async (clientId) => {
    if (!confirm("このブランドのパスワードをリセットしますか？")) return;
    try {
      const res = await axios.post(`${API_BASE}/api/v1/widget/admin/clients/${clientId}/reset-password`, {}, { headers });
      setBrandLoginInfo(prev => ({ ...prev, [clientId]: { password: res.data.new_password, url: res.data.brand_portal_url, showPassword: true } }));
    } catch (err) {
      setError(`パスワードリセット失敗: ${err.response?.data?.detail || err.message}`);
    }
  };

  const handleSetPassword = async (clientId, newPassword) => {
    try {
      const res = await axios.post(`${API_BASE}/api/v1/widget/admin/clients/${clientId}/reset-password`, { password: newPassword }, { headers });
      setBrandLoginInfo(prev => ({ ...prev, [clientId]: { password: res.data.new_password, editing: false } }));
    } catch (err) {
      setError(`パスワード設定失敗: ${err.response?.data?.detail || err.message}`);
    }
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
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

  // ── Search clips for picker ──
  const searchClips = async (query = "", offset = 0) => {
    try {
      setClipSearchLoading(true);
      const res = await axios.get(`${API_BASE}/api/v1/widget/admin/clips/search`, {
        headers,
        params: { q: query, limit: 20, offset },
      });
      if (offset === 0) {
        setClipSearchResults(res.data.clips || []);
      } else {
        setClipSearchResults(prev => [...prev, ...(res.data.clips || [])]);
      }
      setClipSearchTotal(res.data.total || 0);
      setClipSearchOffset(offset);
    } catch (err) {
      console.error("Clip search failed:", err);
    } finally {
      setClipSearchLoading(false);
    }
  };

  // ── Assign clip from picker (with optional product info) ──
  const handleAssignClipFromPicker = async (clip, productInfo = {}) => {
    if (!selectedClientForClips) return;
    try {
      setError(null);
      await axios.post(`${API_BASE}/api/v1/widget/admin/clients/${selectedClientForClips}/clips`, {
        clip_id: clip.id,
        page_url_pattern: null,
        product_name: productInfo.product_name || clip.product_name || null,
        product_price: productInfo.product_price || null,
        product_image_url: productInfo.product_image_url || null,
        product_url: productInfo.product_url || null,
        product_cart_url: productInfo.product_cart_url || null,
      }, { headers });
      fetchClipAssignments(selectedClientForClips);
      // Don't close picker — allow adding more
    } catch (err) {
      setError(`クリップ割当失敗: ${err.response?.data?.detail || err.message}`);
    }
  };

  // ── Update clip product info ──
  const handleUpdateClipProduct = async (clipId) => {
    if (!selectedClientForClips) return;
    try {
      setError(null);
      await axios.post(`${API_BASE}/api/v1/widget/admin/clients/${selectedClientForClips}/clips`, {
        clip_id: clipId,
        page_url_pattern: editProductForm.page_url_pattern || null,
        product_name: editProductForm.product_name || null,
        product_price: editProductForm.product_price || null,
        product_image_url: editProductForm.product_image_url || null,
        product_url: editProductForm.product_url || null,
        product_cart_url: editProductForm.product_cart_url || null,
      }, { headers });
      setEditingClipId(null);
      setEditProductForm({});
      fetchClipAssignments(selectedClientForClips);
    } catch (err) {
      setError(`商品情報更新失敗: ${err.response?.data?.detail || err.message}`);
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
      brand_keywords: client.brand_keywords || "",
      fab_type: client.fab_type || "circle",
      fab_shape: client.fab_shape || "round",
      fab_size: client.fab_size || "medium",
      fab_image_url: client.fab_image_url || "",
      fab_banner_width: client.fab_banner_width || 300,
      fab_banner_height: client.fab_banner_height || 80,
    });
    setShowCreateForm(false);
  };

  // ── Start editing clip product info ──
  const startEditClipProduct = (clip) => {
    setEditingClipId(clip.clip_id);
    setEditProductForm({
      page_url_pattern: clip.page_url_pattern || "",
      product_name: clip.product_name || "",
      product_price: clip.product_price || "",
      product_image_url: clip.product_image_url || "",
      product_url: clip.product_url || "",
      product_cart_url: clip.product_cart_url || "",
    });
  };

  // ── Unassign (delete) clip from client ──
  const handleUnassignClip = async (clipId) => {
    if (!selectedClientForClips) return;
    if (!confirm("このクリップの割り当てを解除しますか？")) return;
    try {
      setError(null);
      await axios.delete(`${API_BASE}/api/v1/widget/admin/clients/${selectedClientForClips}/clips/${clipId}`, { headers });
      fetchClipAssignments(selectedClientForClips);
    } catch (err) {
      setError(`クリップ削除失敗: ${err.response?.data?.detail || err.message}`);
    }
  };

  // ── Reassign clip to different client (brand) ──
  const handleReassignClip = async (clipId, toClientId) => {
    if (!selectedClientForClips || !toClientId) return;
    if (toClientId === selectedClientForClips) return;
    try {
      setError(null);
      await axios.post(`${API_BASE}/api/v1/widget/admin/clips/${clipId}/reassign`, {
        from_client_id: selectedClientForClips,
        to_client_id: toClientId,
      }, { headers });
      fetchClipAssignments(selectedClientForClips);
    } catch (err) {
      setError(`ブランド移動失敗: ${err.response?.data?.detail || err.message}`);
    }
  };

  // ── Open clip picker ──
  const openClipPicker = () => {
    setShowClipPicker(true);
    setClipSearchQuery("");
    setClipSearchResults([]);
    setClipSearchOffset(0);
    searchClips("", 0);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2">
            ウィジェット管理
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            GTM経由で配信するフローティング動画プレイヤーの設定
          </p>
        </div>
        <button
          onClick={() => { setShowCreateForm(!showCreateForm); setEditingClient(null); setForm({ name: "", domain: "", theme_color: "#FF2D55", position: "bottom-right", cta_text: "購入する", cta_url_template: "", cart_selector: "", brand_keywords: "", fab_type: "circle", fab_shape: "round", fab_size: "medium", fab_image_url: "", fab_banner_width: 300, fab_banner_height: 80 }); }}
          className="px-4 py-2 bg-pink-600 text-white rounded-lg hover:bg-pink-700 transition-colors text-sm font-medium"
        >
          + 新規クライアント
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
          {error}
          <button onClick={() => setError(null)} className="ml-2 text-red-400 hover:text-red-600">✕</button>
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
              <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm" placeholder="例: KYOGOKU Professional" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">ドメイン *</label>
              <input type="text" value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm" placeholder="例: kyogokupro.com" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">テーマカラー</label>
              <div className="flex gap-2 items-center">
                <input type="color" value={form.theme_color} onChange={(e) => setForm({ ...form, theme_color: e.target.value })} className="w-10 h-10 rounded border cursor-pointer" />
                <input type="text" value={form.theme_color} onChange={(e) => setForm({ ...form, theme_color: e.target.value })} className="flex-1 px-3 py-2 border rounded-lg text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">表示位置</label>
              <select value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm">
                <option value="bottom-right">右下</option>
                <option value="bottom-left">左下</option>
                <option value="top-right">右上</option>
                <option value="top-left">左上</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">CTAボタンテキスト</label>
              <input type="text" value={form.cta_text} onChange={(e) => setForm({ ...form, cta_text: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm" placeholder="例: 購入する" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">カートセレクタ (CSS)</label>
              <input type="text" value={form.cart_selector} onChange={(e) => setForm({ ...form, cart_selector: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm" placeholder="例: #add-to-cart, .btn-cart" />
            </div>
            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-600 mb-1">CTA URL テンプレート</label>
              <input type="text" value={form.cta_url_template} onChange={(e) => setForm({ ...form, cta_url_template: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm" placeholder="例: https://example.com/cart?product={product}" />
              <p className="text-xs text-gray-400 mt-1">{"{"}product{"}"}  は商品名に置換されます</p>
            </div>
            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-600 mb-1">ブランドキーワード（おすすめクリップ用）</label>
              <input type="text" value={form.brand_keywords} onChange={(e) => setForm({ ...form, brand_keywords: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm" placeholder="例: KYOGOKU, 京極, ケラチン, シャンプー" />
              <p className="text-xs text-gray-400 mt-1">カンマ区切りで複数入力可。ブランドポータルの「おすすめ」タブで自動マッチングに使用されます</p>
            </div>

            {/* ── FABカスタマイズセクション ── */}
            <div className="col-span-2 border-t pt-4 mt-2">
              <h4 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-2">
                <span className="w-5 h-5 bg-pink-100 rounded flex items-center justify-center text-pink-600 text-xs">F</span>
                FAB（フローティングボタン）設定
              </h4>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">FABタイプ</label>
                  <select value={form.fab_type} onChange={(e) => setForm({ ...form, fab_type: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm">
                    <option value="circle">丸ボタン</option>
                    <option value="banner">バナー</option>
                    <option value="hidden">非表示</option>
                  </select>
                </div>
                {form.fab_type === "circle" && (
                  <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1">形状</label>
                    <select value={form.fab_shape} onChange={(e) => setForm({ ...form, fab_shape: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm">
                      <option value="round">丸</option>
                      <option value="square">四角</option>
                    </select>
                  </div>
                )}
                {form.fab_type === "circle" && (
                  <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1">サイズ</label>
                    <select value={form.fab_size} onChange={(e) => setForm({ ...form, fab_size: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm">
                      <option value="small">小 (48px)</option>
                      <option value="medium">中 (60px)</option>
                      <option value="large">大 (80px)</option>
                    </select>
                  </div>
                )}
                {form.fab_type === "banner" && (
                  <>
                    <div>
                      <label className="block text-xs font-medium text-gray-500 mb-1">バナー幅 (px)</label>
                      <input type="number" value={form.fab_banner_width} onChange={(e) => setForm({ ...form, fab_banner_width: parseInt(e.target.value) || 300 })} className="w-full px-3 py-2 border rounded-lg text-sm" min="100" max="600" />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-500 mb-1">バナー高さ (px)</label>
                      <input type="number" value={form.fab_banner_height} onChange={(e) => setForm({ ...form, fab_banner_height: parseInt(e.target.value) || 80 })} className="w-full px-3 py-2 border rounded-lg text-sm" min="40" max="200" />
                    </div>
                  </>
                )}
              </div>
              {form.fab_type !== "hidden" && (
                <div className="mt-3">
                  <label className="block text-xs font-medium text-gray-500 mb-1">カスタム画像 URL（任意）</label>
                  <input type="text" value={form.fab_image_url} onChange={(e) => setForm({ ...form, fab_image_url: e.target.value })} className="w-full px-3 py-2 border rounded-lg text-sm" placeholder="https://example.com/banner.png" />
                  <p className="text-xs text-gray-400 mt-1">設定するとサムネイル/動画の代わりにこの画像をFABに表示します</p>
                  {form.fab_image_url && (
                    <div className="mt-2 p-2 bg-gray-50 rounded-lg inline-block">
                      <img src={form.fab_image_url} alt="FAB preview" className="max-h-16 rounded" onError={(e) => { e.target.style.display = 'none'; }} />
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
          <div className="flex gap-2 pt-2">
            <button onClick={editingClient ? handleUpdate : handleCreate} className="px-4 py-2 bg-pink-600 text-white rounded-lg hover:bg-pink-700 text-sm font-medium">
              {editingClient ? "更新" : "作成"}
            </button>
            <button onClick={() => { setShowCreateForm(false); setEditingClient(null); }} className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 text-sm">
              キャンセル
            </button>
          </div>
        </div>
      )}

      {/* GTM Tag Snippet Modal */}
      {tagSnippet && (
        <div className="bg-gray-900 rounded-lg p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-white flex items-center gap-2">GTMタグ — {tagSnippet.client_name}</h3>
            <button onClick={() => setTagSnippet(null)} className="text-gray-400 hover:text-white">✕</button>
          </div>
          <div className="space-y-3">
            <div>
              <p className="text-xs text-gray-400 mb-1">GTM カスタムHTMLタグ（これをコピーして貼り付け）:</p>
              <div className="bg-black rounded-lg p-4 relative group">
                <pre className="text-green-400 text-sm font-mono whitespace-pre-wrap break-all">{tagSnippet.gtm_custom_html}</pre>
                <button onClick={() => navigator.clipboard.writeText(tagSnippet.gtm_custom_html)} className="absolute top-2 right-2 px-3 py-1 bg-gray-700 text-gray-300 rounded text-xs hover:bg-gray-600 opacity-0 group-hover:opacity-100 transition-opacity">コピー</button>
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-400 mb-1">直接埋め込み用（HTML）:</p>
              <div className="bg-black rounded-lg p-4 relative group">
                <pre className="text-blue-400 text-sm font-mono whitespace-pre-wrap break-all">{tagSnippet.direct_embed}</pre>
                <button onClick={() => navigator.clipboard.writeText(tagSnippet.direct_embed)} className="absolute top-2 right-2 px-3 py-1 bg-gray-700 text-gray-300 rounded text-xs hover:bg-gray-600 opacity-0 group-hover:opacity-100 transition-opacity">コピー</button>
              </div>
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
          {[...clients].sort((a, b) => (b.clip_count || 0) - (a.clip_count || 0)).map((client) => (
            <div key={client.client_id} className="bg-white rounded-lg border p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  {client.logo_url ? (
                    <img src={client.logo_url} alt={client.name} className="w-8 h-8 rounded-full object-cover border border-gray-200" onError={(e) => { e.target.style.display = 'none'; e.target.nextElementSibling.style.display = 'flex'; }} />
                  ) : null}
                  <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold" style={{ backgroundColor: client.theme_color || "#FF2D55", display: client.logo_url ? 'none' : 'flex' }}>
                    {client.name?.charAt(0)}
                  </div>
                  <div>
                    <h4 className="font-semibold text-gray-800">{client.name}</h4>
                    <p className="text-xs text-gray-500">{client.domain} — ID: {client.client_id}</p>
                  </div>
                  <span className={`px-2 py-0.5 rounded-full text-xs ${client.is_active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                    {client.is_active ? "有効" : "無効"}
                  </span>
                  {client.clip_count > 0 && (
                    <span className="px-2 py-0.5 rounded-full text-xs bg-purple-100 text-purple-700">
                      {client.clip_count}本
                    </span>
                  )}
                  {/* Connection status badge */}
                  {client.page_view_count > 0 ? (
                    <span className="px-2 py-0.5 rounded-full text-xs bg-emerald-100 text-emerald-700 flex items-center gap-1" title={`最終検知: ${client.last_seen_at || '不明'}`}>
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                      接続済み ({client.page_view_count}PV)
                    </span>
                  ) : (
                    <span className="px-2 py-0.5 rounded-full text-xs bg-red-100 text-red-600 flex items-center gap-1" title="page_viewイベントが検出されていません">
                      <span className="w-1.5 h-1.5 rounded-full bg-red-400"></span>
                      未接続
                    </span>
                  )}
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
                    クリップ管理
                  </button>
                  <button onClick={() => handleGetTag(client.client_id)} className="px-3 py-1.5 bg-green-100 text-green-700 rounded-lg text-xs hover:bg-green-200">GTMタグ</button>
                  <button onClick={() => startEdit(client)} className="px-3 py-1.5 bg-blue-100 text-blue-700 rounded-lg text-xs hover:bg-blue-200">編集</button>
                </div>
              </div>

              {/* Brand Login Info */}
              <div className="mt-3 pt-3 border-t border-gray-100">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-gray-500 font-medium">ブランドポータル:</span>
                  <div className="flex items-center gap-1 bg-gray-50 rounded px-2 py-1">
                    <span className="text-xs text-blue-600 font-mono">https://www.aitherhub.com/brand?id={client.client_id}</span>
                    <button onClick={() => copyToClipboard(`https://www.aitherhub.com/brand?id=${client.client_id}`)} className="text-gray-400 hover:text-blue-500 ml-1" title="URLをコピー">
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                    </button>
                  </div>
                  <div className="flex items-center gap-1 bg-gray-50 rounded px-2 py-1">
                    <span className="text-xs text-gray-500">ID:</span>
                    <span className="text-xs font-mono text-gray-700">{client.client_id}</span>
                    <button onClick={() => copyToClipboard(client.client_id)} className="text-gray-400 hover:text-blue-500 ml-1" title="IDをコピー">
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                    </button>
                  </div>
                  {/* Password display - always show if available */}
                  {(() => {
                    const pw = brandLoginInfo[client.client_id]?.password || client.password_plain;
                    const isEditing = brandLoginInfo[client.client_id]?.editing;
                    if (isEditing) {
                      return (
                        <div className="flex items-center gap-1 bg-blue-50 border border-blue-200 rounded px-2 py-1">
                          <span className="text-xs text-gray-500">PW:</span>
                          <input
                            type="text"
                            className="text-xs font-mono text-blue-700 font-bold bg-white border border-blue-300 rounded px-1.5 py-0.5 w-32 outline-none focus:border-blue-500"
                            defaultValue={pw || ''}
                            autoFocus
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                const newPw = e.target.value.trim();
                                if (newPw.length < 1) return;
                                handleSetPassword(client.client_id, newPw);
                              } else if (e.key === 'Escape') {
                                setBrandLoginInfo(prev => ({ ...prev, [client.client_id]: { ...prev[client.client_id], editing: false } }));
                              }
                            }}
                          />
                          <button onClick={(e) => {
                            const input = e.target.closest('div').querySelector('input');
                            const newPw = input?.value?.trim();
                            if (newPw && newPw.length >= 1) handleSetPassword(client.client_id, newPw);
                          }} className="px-1.5 py-0.5 bg-blue-500 text-white rounded text-[10px] hover:bg-blue-600">保存</button>
                          <button onClick={() => setBrandLoginInfo(prev => ({ ...prev, [client.client_id]: { ...prev[client.client_id], editing: false } }))} className="px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded text-[10px] hover:bg-gray-300">×</button>
                        </div>
                      );
                    }
                    if (pw) {
                      return (
                        <div className="flex items-center gap-1 bg-green-50 border border-green-200 rounded px-2 py-1">
                          <span className="text-xs text-gray-500">PW:</span>
                          <span className="text-xs font-mono text-green-700 font-bold">{pw}</span>
                          <button onClick={() => copyToClipboard(pw)} className="text-gray-400 hover:text-green-600 ml-1" title="パスワードをコピー">
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                          </button>
                          <button onClick={() => setBrandLoginInfo(prev => ({ ...prev, [client.client_id]: { ...prev[client.client_id], password: pw, editing: true } }))} className="text-gray-400 hover:text-blue-500 ml-0.5" title="パスワードを編集">
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                          </button>
                          <button onClick={() => {
                            const info = `ブランドポータル ログイン情報\n\nURL: https://www.aitherhub.com/brand?id=${client.client_id}\nID: ${client.client_id}\nパスワード: ${pw}`;
                            copyToClipboard(info);
                          }} className="px-2 py-0.5 bg-green-100 text-green-700 rounded text-[10px] hover:bg-green-200 ml-1" title="全情報をまとめてコピー">まとめてコピー</button>
                        </div>
                      );
                    }
                    return (
                      <div className="flex items-center gap-1">
                        <span className="text-xs text-gray-400 italic">PW未設定</span>
                        <button onClick={() => setBrandLoginInfo(prev => ({ ...prev, [client.client_id]: { editing: true } }))} className="px-2 py-1 bg-blue-100 text-blue-700 rounded text-xs hover:bg-blue-200 flex items-center gap-1">
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                          PW設定
                        </button>
                        <button onClick={() => handleResetPassword(client.client_id)} className="px-2 py-1 bg-orange-100 text-orange-700 rounded text-xs hover:bg-orange-200 flex items-center gap-1">
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" /></svg>
                          PW自動生成
                        </button>
                      </div>
                    );
                  })()}
                </div>
              </div>

              {/* Clip Preview Thumbnails */}
              {client.clips_preview && client.clips_preview.length > 0 && selectedClientForClips !== client.client_id && (
                <div className="mt-3 pt-3 border-t border-gray-100">
                  <div className="flex gap-2 overflow-x-auto pb-1">
                    {client.clips_preview.map((cp, idx) => (
                      <div
                        key={cp.clip_id || idx}
                        className="relative flex-shrink-0 rounded-lg overflow-hidden border border-gray-200 cursor-pointer hover:shadow-md transition-shadow group"
                        style={{ width: "72px", height: "128px" }}
                        onClick={() => setPreviewClip({ ...cp, clip_url: cp.clip_url })}
                      >
                        {cp.clip_url ? (
                          <video
                            src={cp.clip_url}
                            muted
                            playsInline
                            preload="metadata"
                            className="absolute inset-0 w-full h-full object-cover"
                            onLoadedData={(e) => { e.target.currentTime = 0.5; }}
                          />
                        ) : cp.thumbnail_url ? (
                          <img src={cp.thumbnail_url} alt="" className="absolute inset-0 w-full h-full object-cover" />
                        ) : (
                          <div className="absolute inset-0 bg-gray-200 flex items-center justify-center">
                            <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                          </div>
                        )}
                        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors flex items-center justify-center">
                          <svg className="w-5 h-5 text-white opacity-0 group-hover:opacity-100 transition-opacity drop-shadow" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                        </div>
                        {cp.duration_sec && (
                          <div className="absolute bottom-0.5 right-0.5 bg-black/70 text-white text-[8px] px-1 py-0.5 rounded">
                            {Math.floor(cp.duration_sec / 60)}:{String(Math.floor(cp.duration_sec % 60)).padStart(2, '0')}
                          </div>
                        )}
                      </div>
                    ))}
                    {client.clip_count > client.clips_preview.length && (
                      <div
                        className="relative flex-shrink-0 rounded-lg overflow-hidden border border-dashed border-gray-300 cursor-pointer hover:border-purple-400 hover:bg-purple-50 transition-colors flex items-center justify-center"
                        style={{ width: "72px", height: "128px" }}
                        onClick={() => {
                          setSelectedClientForClips(client.client_id);
                          fetchClipAssignments(client.client_id);
                        }}
                      >
                        <div className="text-center">
                          <span className="text-lg text-gray-400">+{client.clip_count - client.clips_preview.length}</span>
                          <p className="text-[8px] text-gray-400 mt-0.5">もっと見る</p>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Clip Assignment Panel */}
              {selectedClientForClips === client.client_id && (
                <div className="mt-4 pt-4 border-t space-y-3">
                  <div className="flex items-center justify-between">
                    <h5 className="text-sm font-medium text-gray-600">割り当てクリップ ({clipAssignments.length}本)</h5>
                    <button
                      onClick={openClipPicker}
                      className="px-4 py-2 bg-purple-600 text-white rounded-lg text-sm hover:bg-purple-700 flex items-center gap-2"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
                      クリップを追加
                    </button>
                  </div>

                  {clipAssignments.length > 0 ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                      {clipAssignments.map((clip, i) => (
                        <ClipCard
                          key={clip.clip_id || i}
                          clip={clip}
                          index={i}
                          isEditing={editingClipId === clip.clip_id}
                          editForm={editProductForm}
                          onStartEdit={() => startEditClipProduct(clip)}
                          onCancelEdit={() => { setEditingClipId(null); setEditProductForm({}); }}
                          onSaveEdit={() => handleUpdateClipProduct(clip.clip_id)}
                          onEditFormChange={(field, value) => setEditProductForm(prev => ({ ...prev, [field]: value }))}
                          onPreview={() => setPreviewClip(clip)}
                          onDelete={() => handleUnassignClip(clip.clip_id)}
                          onReassign={(toClientId) => handleReassignClip(clip.clip_id, toClientId)}
                          allClients={clients}
                          currentClientId={selectedClientForClips}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-8 text-gray-400 bg-gray-50 rounded-lg">
                      <svg className="w-12 h-12 mx-auto mb-2 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                      <p className="text-sm">クリップが割り当てられていません</p>
                      <p className="text-xs mt-1">「クリップを追加」ボタンから動画を検索して追加できます</p>
                    </div>
                  )}
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

      {/* ═══════════ Video Preview Modal ═══════════ */}
      {previewClip && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80" onClick={() => setPreviewClip(null)}>
          <div className="relative max-w-sm w-full mx-4" onClick={(e) => e.stopPropagation()}>
            <button
              onClick={() => setPreviewClip(null)}
              className="absolute -top-10 right-0 text-white/80 hover:text-white text-sm flex items-center gap-1"
            >
              閉じる ✕
            </button>
            <div className="bg-black rounded-2xl overflow-hidden shadow-2xl" style={{ aspectRatio: "9/16" }}>
              <video
                src={previewClip.clip_url}
                controls
                autoPlay
                playsInline
                className="w-full h-full object-contain"
              />
            </div>
            <div className="mt-3 text-white">
              <p className="font-semibold text-sm">{previewClip.product_name || previewClip.liver_name || "動画プレビュー"}</p>
              {previewClip.product_price && <p className="text-pink-400 font-bold text-sm mt-0.5">{previewClip.product_price}</p>}
              {previewClip.transcript_text && (
                <p className="text-white/60 text-xs mt-2 max-h-20 overflow-y-auto leading-relaxed">
                  {previewClip.transcript_text}
                </p>
              )}
              <p className="text-white/30 text-xs font-mono mt-2">{previewClip.clip_id}</p>
            </div>
          </div>
        </div>
      )}

      {/* ═══════════ Clip Picker Modal ═══════════ */}
      {showClipPicker && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowClipPicker(false)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl mx-4 max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            {/* Picker Header */}
            <div className="p-5 border-b flex items-center justify-between flex-shrink-0">
              <div>
                <h3 className="font-bold text-gray-800 text-lg">クリップを選択して追加</h3>
                <p className="text-xs text-gray-500 mt-0.5">動画をクリックでプレビュー、「追加」ボタンでウィジェットに割当</p>
              </div>
              <button onClick={() => setShowClipPicker(false)} className="text-gray-400 hover:text-gray-600 text-xl">✕</button>
            </div>

            {/* Search bar */}
            <div className="p-4 border-b flex-shrink-0">
              <div className="relative">
                <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
                <input
                  type="text"
                  value={clipSearchQuery}
                  onChange={(e) => setClipSearchQuery(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { setClipSearchOffset(0); searchClips(clipSearchQuery, 0); } }}
                  className="w-full pl-10 pr-24 py-2.5 border rounded-xl text-sm focus:ring-2 focus:ring-purple-300 focus:border-purple-400 outline-none"
                  placeholder="商品名、配信者名、字幕テキストで検索..."
                  autoFocus
                />
                <button
                  onClick={() => { setClipSearchOffset(0); searchClips(clipSearchQuery, 0); }}
                  className="absolute right-2 top-1/2 -translate-y-1/2 px-4 py-1.5 bg-purple-600 text-white rounded-lg text-xs hover:bg-purple-700"
                >
                  検索
                </button>
              </div>
              <p className="text-xs text-gray-400 mt-2">
                {clipSearchTotal > 0 ? `${clipSearchTotal}件のクリップが見つかりました` : clipSearchLoading ? "検索中..." : ""}
              </p>
            </div>

            {/* Results grid */}
            <div className="flex-1 overflow-y-auto p-4">
              {clipSearchLoading && clipSearchResults.length === 0 ? (
                <div className="flex items-center justify-center py-16">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-500"></div>
                </div>
              ) : clipSearchResults.length === 0 ? (
                <div className="text-center py-16 text-gray-400">
                  <p className="text-sm">検索結果がありません</p>
                  <p className="text-xs mt-1">キーワードを変えて検索してください</p>
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                    {clipSearchResults.map((clip) => {
                      const isAssigned = clipAssignments.some(a => a.clip_id === clip.id);
                      return (
                        <PickerClipCard
                          key={clip.id}
                          clip={clip}
                          isAssigned={isAssigned}
                          onPreview={() => setPreviewClip({ ...clip, clip_id: clip.id })}
                          onAssign={() => handleAssignClipFromPicker(clip)}
                        />
                      );
                    })}
                  </div>
                  {/* Load more */}
                  {clipSearchResults.length < clipSearchTotal && (
                    <div className="text-center mt-4">
                      <button
                        onClick={() => searchClips(clipSearchQuery, clipSearchOffset + 20)}
                        disabled={clipSearchLoading}
                        className="px-6 py-2 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200 disabled:opacity-50"
                      >
                        {clipSearchLoading ? "読み込み中..." : `もっと見る (${clipSearchResults.length}/${clipSearchTotal})`}
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════ PickerClipCard: クリップ検索結果のカード ═══════════ */
function PickerClipCard({ clip, isAssigned, onPreview, onAssign }) {
  const [hovering, setHovering] = useState(false);
  const videoRef = useRef(null);

  const formatDuration = (sec) => {
    if (!sec) return "";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  const handleMouseEnter = () => {
    setHovering(true);
    if (videoRef.current && clip.clip_url) {
      videoRef.current.currentTime = 0;
      videoRef.current.play().catch(() => {});
    }
  };

  const handleMouseLeave = () => {
    setHovering(false);
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = 0;
    }
  };

  const title = clip.product_name || clip.liver_name || "クリップ";

  return (
    <div
      className={`relative rounded-xl border overflow-hidden transition-all ${isAssigned ? "border-green-300 bg-green-50/50" : "border-gray-200 bg-white hover:shadow-lg"}`}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* Video area */}
      <div className="relative cursor-pointer" style={{ aspectRatio: "9/16", maxHeight: "200px" }} onClick={onPreview}>
        {clip.clip_url ? (
          <>
            <video
              ref={videoRef}
              src={clip.clip_url}
              muted
              playsInline
              loop
              preload="metadata"
              className="absolute inset-0 w-full h-full object-cover"
              style={{ opacity: hovering ? 1 : 0, transition: "opacity 0.3s" }}
            />
            <video
              src={clip.clip_url}
              muted
              playsInline
              preload="metadata"
              className="absolute inset-0 w-full h-full object-cover"
              style={{ opacity: hovering ? 0 : 1, transition: "opacity 0.3s" }}
              onLoadedData={(e) => { e.target.currentTime = 0.5; }}
            />
            {!hovering && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/10">
                <svg className="w-8 h-8 text-white/90 drop-shadow" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
              </div>
            )}
          </>
        ) : (
          <div className="absolute inset-0 bg-gray-200 flex items-center justify-center">
            <span className="text-xs text-gray-400">動画なし</span>
          </div>
        )}
        {clip.duration_sec && (
          <div className="absolute bottom-1 right-1 bg-black/70 text-white text-[10px] px-1 py-0.5 rounded">
            {formatDuration(clip.duration_sec)}
          </div>
        )}
        {isAssigned && (
          <div className="absolute top-1 left-1 bg-green-500 text-white text-[10px] px-1.5 py-0.5 rounded font-medium">
            割当済
          </div>
        )}
      </div>

      {/* Info + action */}
      <div className="p-2">
        <p className="text-xs font-medium text-gray-700 truncate" title={title}>{title}</p>
        {clip.transcript_text && (
          <p className="text-[10px] text-gray-400 mt-0.5 truncate">{clip.transcript_text.slice(0, 50)}...</p>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); onAssign(); }}
          disabled={isAssigned}
          className={`mt-1.5 w-full py-1.5 rounded-lg text-xs font-medium transition-colors ${
            isAssigned
              ? "bg-green-100 text-green-600 cursor-default"
              : "bg-purple-600 text-white hover:bg-purple-700"
          }`}
        >
          {isAssigned ? "追加済み" : "+ 追加"}
        </button>
      </div>
    </div>
  );
}

/* ═══════════ ClipCard: 割当済みクリップカード（プレビュー＋商品情報編集） ═══════════ */
function ClipCard({ clip, index, isEditing, editForm, onStartEdit, onCancelEdit, onSaveEdit, onEditFormChange, onPreview, onDelete, onReassign, allClients, currentClientId }) {
  const videoRef = useRef(null);
  const [isHovering, setIsHovering] = useState(false);
  const [videoError, setVideoError] = useState(false);

  const formatDuration = (sec) => {
    if (!sec) return "--:--";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  const handleMouseEnter = () => {
    setIsHovering(true);
    if (videoRef.current && clip.clip_url && !videoError) {
      videoRef.current.currentTime = 0;
      videoRef.current.play().catch(() => {});
    }
  };

  const handleMouseLeave = () => {
    setIsHovering(false);
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = 0;
    }
  };

  const hasVideo = !!clip.clip_url;
  const title = clip.product_name || clip.liver_name || `クリップ #${index + 1}`;
  const hasProductInfo = clip.product_name || clip.product_price || clip.product_url;

  return (
    <div
      className="relative bg-gray-50 rounded-xl border border-gray-200 overflow-hidden group cursor-pointer transition-shadow hover:shadow-lg"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* Video / Thumbnail area — click to preview */}
      <div className="relative w-full" style={{ aspectRatio: "9/16", maxHeight: "240px" }} onClick={onPreview}>
        {hasVideo && !videoError ? (
          <>
            <video
              ref={videoRef}
              src={clip.clip_url}
              muted
              playsInline
              loop
              preload="metadata"
              onError={() => setVideoError(true)}
              className="absolute inset-0 w-full h-full object-cover"
              style={{ opacity: isHovering ? 1 : 0, transition: "opacity 0.3s" }}
            />
            <video
              src={clip.clip_url}
              muted
              playsInline
              preload="metadata"
              className="absolute inset-0 w-full h-full object-cover"
              style={{ opacity: isHovering ? 0 : 1, transition: "opacity 0.3s" }}
            />
            {!isHovering && (
              <div className="absolute inset-0 flex items-center justify-center">
                <svg className="w-10 h-10 text-white/80 drop-shadow-lg" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </div>
            )}
          </>
        ) : clip.thumbnail_url ? (
          <img src={clip.thumbnail_url} alt={title} className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <div className="absolute inset-0 w-full h-full bg-gradient-to-br from-gray-200 to-gray-300 flex items-center justify-center">
            <div className="text-center">
              <svg className="w-8 h-8 text-gray-400 mx-auto mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <span className="text-xs text-gray-400">動画なし</span>
            </div>
          </div>
        )}

        {clip.duration_sec && (
          <div className="absolute bottom-2 right-2 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded">
            {formatDuration(clip.duration_sec)}
          </div>
        )}
        <div className={`absolute top-2 left-2 px-1.5 py-0.5 rounded text-xs font-medium ${hasVideo ? "bg-green-500/90 text-white" : "bg-red-500/90 text-white"}`}>
          {hasVideo ? "配信中" : "動画なし"}
        </div>
        {hasProductInfo && (
          <div className="absolute top-2 right-2 px-1.5 py-0.5 rounded text-xs font-medium bg-orange-500/90 text-white">
            商品設定済
          </div>
        )}
        {/* Click hint on hover */}
        {isHovering && (
          <div className="absolute bottom-2 left-2 bg-black/60 text-white text-[10px] px-2 py-1 rounded backdrop-blur-sm">
            クリックでプレビュー
          </div>
        )}
      </div>

      {/* Info area */}
      <div className="p-3">
        <p className="text-sm font-medium text-gray-800 truncate" title={title}>{title}</p>
        {clip.product_price && <p className="text-sm font-bold text-pink-600 mt-0.5">{clip.product_price}</p>}
        <p className="text-xs text-gray-400 font-mono mt-1 truncate" title={clip.clip_id}>{clip.clip_id?.slice(0, 8)}...</p>
        {clip.page_url_pattern && <p className="text-xs text-purple-500 mt-1 truncate" title={clip.page_url_pattern}>{clip.page_url_pattern}</p>}
        {clip.product_url && <p className="text-xs text-blue-500 mt-1 truncate" title={clip.product_url}>{clip.product_url}</p>}
        {clip.transcript_text && <p className="text-xs text-gray-500 mt-1 line-clamp-2" title={clip.transcript_text}>{clip.transcript_text.slice(0, 60)}...</p>}

        {/* Brand selector */}
        {allClients && allClients.length > 1 && (
          <div className="mt-2">
            <label className="block text-[10px] text-gray-400 mb-0.5">ブランド</label>
            <select
              value={currentClientId || ""}
              onChange={(e) => { e.stopPropagation(); if (onReassign) onReassign(e.target.value); }}
              onClick={(e) => e.stopPropagation()}
              className="w-full px-2 py-1.5 border rounded text-xs bg-white text-gray-700 focus:ring-2 focus:ring-purple-300"
            >
              {allClients.filter(c => c.is_active).map(c => (
                <option key={c.client_id} value={c.client_id}>
                  {c.name} {c.client_id === currentClientId ? "(現在)" : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="flex gap-1.5 mt-2">
          <button
            onClick={(e) => { e.stopPropagation(); onStartEdit(); }}
            className="flex-1 px-2 py-1.5 bg-orange-50 text-orange-600 rounded text-xs hover:bg-orange-100 border border-orange-200"
          >
            {hasProductInfo ? "商品情報編集" : "商品情報追加"}
          </button>
          {clip.video_id && (
            <button
              onClick={(e) => { e.stopPropagation(); window.open(`/video/${clip.video_id}?open_editor=1`, '_blank'); }}
              className="px-2 py-1.5 bg-indigo-50 text-indigo-600 rounded text-xs hover:bg-indigo-100 border border-indigo-200"
              title="CLIP EDITORで編集"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" /></svg>
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); if (onDelete) onDelete(); }}
            className="px-2 py-1.5 bg-red-50 text-red-500 rounded text-xs hover:bg-red-100 border border-red-200"
            title="割り当て解除"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
          </button>
        </div>
      </div>

      {/* Edit product info form (inline) */}
      {isEditing && (
        <div className="p-3 pt-0 space-y-2 border-t border-gray-200 bg-orange-50/50">
          {[
            { key: "product_name", label: "商品名", placeholder: "商品名" },
            { key: "product_price", label: "価格", placeholder: "¥3,980" },
            { key: "product_image_url", label: "商品画像URL", placeholder: "https://..." },
            { key: "product_url", label: "商品ページURL", placeholder: "https://kyogokupro.com/products/..." },
            { key: "product_cart_url", label: "カートURL（任意）", placeholder: "https://..." },
            { key: "page_url_pattern", label: "ページURLパターン", placeholder: "/products/*" },
          ].map(({ key, label, placeholder }) => (
            <div key={key}>
              <label className="block text-xs text-gray-500 mb-0.5">{label}</label>
              <input
                type="text"
                value={editForm[key] || ""}
                onChange={(e) => onEditFormChange(key, e.target.value)}
                className="w-full px-2 py-1.5 border rounded text-xs"
                placeholder={placeholder}
              />
            </div>
          ))}
          <div className="flex gap-2 pt-1">
            <button onClick={(e) => { e.stopPropagation(); onSaveEdit(); }} className="flex-1 px-2 py-1.5 bg-orange-600 text-white rounded text-xs hover:bg-orange-700">保存</button>
            <button onClick={(e) => { e.stopPropagation(); onCancelEdit(); }} className="flex-1 px-2 py-1.5 bg-gray-200 text-gray-600 rounded text-xs hover:bg-gray-300">キャンセル</button>
          </div>
        </div>
      )}
    </div>
  );
}
