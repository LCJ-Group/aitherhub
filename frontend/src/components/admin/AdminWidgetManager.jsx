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
  const [newClipId, setNewClipId] = useState("");
  const [newPagePattern, setNewPagePattern] = useState("");
  // Product info fields for clip assignment
  const [newProductName, setNewProductName] = useState("");
  const [newProductPrice, setNewProductPrice] = useState("");
  const [newProductImageUrl, setNewProductImageUrl] = useState("");
  const [newProductUrl, setNewProductUrl] = useState("");
  const [newProductCartUrl, setNewProductCartUrl] = useState("");
  const [showProductFields, setShowProductFields] = useState(false);
  // Editing existing clip product info
  const [editingClipId, setEditingClipId] = useState(null);
  const [editProductForm, setEditProductForm] = useState({});

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

  // ── Assign clip (with product info) ──
  const handleAssignClip = async () => {
    if (!selectedClientForClips || !newClipId) return;
    try {
      setError(null);
      await axios.post(`${API_BASE}/api/v1/widget/admin/clients/${selectedClientForClips}/clips`, {
        clip_id: newClipId,
        page_url_pattern: newPagePattern || null,
        product_name: newProductName || null,
        product_price: newProductPrice || null,
        product_image_url: newProductImageUrl || null,
        product_url: newProductUrl || null,
        product_cart_url: newProductCartUrl || null,
      }, { headers });
      setNewClipId("");
      setNewPagePattern("");
      setNewProductName("");
      setNewProductPrice("");
      setNewProductImageUrl("");
      setNewProductUrl("");
      setNewProductCartUrl("");
      setShowProductFields(false);
      fetchClipAssignments(selectedClientForClips);
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
              className="px-4 py-2 bg-pink-600 text-white rounded-lg hover:bg-pink-700 text-sm font-medium"
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
              GTMタグ — {tagSnippet.client_name}
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
                GTMで「カスタムHTML」タグを新規作成し、上のコードを貼り付けて「すべてのページ」トリガーで公開してください。
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
                    クリップ
                  </button>
                  <button
                    onClick={() => handleGetTag(client.client_id)}
                    className="px-3 py-1.5 bg-green-100 text-green-700 rounded-lg text-xs hover:bg-green-200"
                  >
                    GTMタグ
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
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
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
                        />
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-gray-400">クリップが割り当てられていません</p>
                  )}

                  {/* New clip assignment form */}
                  <div className="bg-gray-50 rounded-lg p-4 space-y-3">
                    <h6 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">新規クリップ割当</h6>
                    <div className="flex gap-2 items-end">
                      <div className="flex-1">
                        <label className="block text-xs text-gray-500 mb-1">クリップID *</label>
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
                        onClick={() => setShowProductFields(!showProductFields)}
                        className={`px-3 py-2 rounded-lg text-xs whitespace-nowrap ${showProductFields ? "bg-orange-100 text-orange-700" : "bg-gray-200 text-gray-600 hover:bg-gray-300"}`}
                      >
                        {showProductFields ? "商品情報 ▲" : "商品情報 ▼"}
                      </button>
                      <button
                        onClick={handleAssignClip}
                        className="px-4 py-2 bg-purple-600 text-white rounded-lg text-sm hover:bg-purple-700 whitespace-nowrap"
                      >
                        割当
                      </button>
                    </div>

                    {/* Product info fields (expandable) */}
                    {showProductFields && (
                      <div className="grid grid-cols-2 gap-3 pt-2 border-t border-gray-200">
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">商品名</label>
                          <input
                            type="text"
                            value={newProductName}
                            onChange={(e) => setNewProductName(e.target.value)}
                            className="w-full px-3 py-2 border rounded-lg text-sm"
                            placeholder="例: KYOGOKUカラーシャンプー"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">価格（表示用テキスト）</label>
                          <input
                            type="text"
                            value={newProductPrice}
                            onChange={(e) => setNewProductPrice(e.target.value)}
                            className="w-full px-3 py-2 border rounded-lg text-sm"
                            placeholder="例: ¥3,980 / NT$1,280"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">商品画像URL</label>
                          <input
                            type="text"
                            value={newProductImageUrl}
                            onChange={(e) => setNewProductImageUrl(e.target.value)}
                            className="w-full px-3 py-2 border rounded-lg text-sm"
                            placeholder="https://..."
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">商品ページURL（購入ボタン遷移先）</label>
                          <input
                            type="text"
                            value={newProductUrl}
                            onChange={(e) => setNewProductUrl(e.target.value)}
                            className="w-full px-3 py-2 border rounded-lg text-sm"
                            placeholder="https://kyogokupro.com/products/..."
                          />
                        </div>
                        <div className="col-span-2">
                          <label className="block text-xs text-gray-500 mb-1">カートURL（カートに入れるボタン遷移先、任意）</label>
                          <input
                            type="text"
                            value={newProductCartUrl}
                            onChange={(e) => setNewProductCartUrl(e.target.value)}
                            className="w-full px-3 py-2 border rounded-lg text-sm"
                            placeholder="https://kyogokupro.com/cart/add?product_id=..."
                          />
                        </div>
                      </div>
                    )}
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

/* ── ClipCard: サムネイル＋ホバー動画プレビュー付きカード（商品情報編集対応） ── */
function ClipCard({ clip, index, isEditing, editForm, onStartEdit, onCancelEdit, onSaveEdit, onEditFormChange }) {
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
      {/* Video / Thumbnail area */}
      <div className="relative w-full" style={{ aspectRatio: "9/16", maxHeight: "240px" }}>
        {hasVideo && !videoError ? (
          <>
            {/* Video element - plays on hover */}
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
            {/* Poster / first frame - shown when not hovering */}
            <video
              src={clip.clip_url}
              muted
              playsInline
              preload="metadata"
              className="absolute inset-0 w-full h-full object-cover"
              style={{ opacity: isHovering ? 0 : 1, transition: "opacity 0.3s" }}
            />
            {/* Play icon overlay */}
            {!isHovering && (
              <div className="absolute inset-0 flex items-center justify-center">
                <svg className="w-10 h-10 text-white/80 drop-shadow-lg" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </div>
            )}
          </>
        ) : clip.thumbnail_url ? (
          <img
            src={clip.thumbnail_url}
            alt={title}
            className="absolute inset-0 w-full h-full object-cover"
          />
        ) : (
          /* No video, no thumbnail - placeholder */
          <div className="absolute inset-0 w-full h-full bg-gradient-to-br from-gray-200 to-gray-300 flex items-center justify-center">
            <div className="text-center">
              <svg className="w-8 h-8 text-gray-400 mx-auto mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <span className="text-xs text-gray-400">動画なし</span>
            </div>
          </div>
        )}

        {/* Duration badge */}
        {clip.duration_sec && (
          <div className="absolute bottom-2 right-2 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded">
            {formatDuration(clip.duration_sec)}
          </div>
        )}

        {/* Status badge */}
        <div className={`absolute top-2 left-2 px-1.5 py-0.5 rounded text-xs font-medium ${
          hasVideo ? "bg-green-500/90 text-white" : "bg-red-500/90 text-white"
        }`}>
          {hasVideo ? "配信中" : "動画なし"}
        </div>

        {/* Product info badge */}
        {hasProductInfo && (
          <div className="absolute top-2 right-2 px-1.5 py-0.5 rounded text-xs font-medium bg-orange-500/90 text-white">
            商品設定済
          </div>
        )}
      </div>

      {/* Info area */}
      <div className="p-3">
        <p className="text-sm font-medium text-gray-800 truncate" title={title}>
          {title}
        </p>
        {clip.product_price && (
          <p className="text-sm font-bold text-pink-600 mt-0.5">{clip.product_price}</p>
        )}
        <p className="text-xs text-gray-400 font-mono mt-1 truncate" title={clip.clip_id}>
          {clip.clip_id?.slice(0, 8)}...
        </p>
        {clip.page_url_pattern && (
          <p className="text-xs text-purple-500 mt-1 truncate" title={clip.page_url_pattern}>
            {clip.page_url_pattern}
          </p>
        )}
        {clip.product_url && (
          <p className="text-xs text-blue-500 mt-1 truncate" title={clip.product_url}>
            {clip.product_url}
          </p>
        )}
        {clip.transcript_text && (
          <p className="text-xs text-gray-500 mt-1 line-clamp-2" title={clip.transcript_text}>
            {clip.transcript_text.slice(0, 60)}...
          </p>
        )}

        {/* Edit product info button */}
        <button
          onClick={(e) => { e.stopPropagation(); onStartEdit(); }}
          className="mt-2 w-full px-2 py-1.5 bg-orange-50 text-orange-600 rounded text-xs hover:bg-orange-100 border border-orange-200"
        >
          {hasProductInfo ? "商品情報を編集" : "商品情報を追加"}
        </button>
      </div>

      {/* Edit product info form (inline) */}
      {isEditing && (
        <div className="p-3 pt-0 space-y-2 border-t border-gray-200 bg-orange-50/50">
          <div>
            <label className="block text-xs text-gray-500 mb-0.5">商品名</label>
            <input
              type="text"
              value={editForm.product_name || ""}
              onChange={(e) => onEditFormChange("product_name", e.target.value)}
              className="w-full px-2 py-1.5 border rounded text-xs"
              placeholder="商品名"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-0.5">価格</label>
            <input
              type="text"
              value={editForm.product_price || ""}
              onChange={(e) => onEditFormChange("product_price", e.target.value)}
              className="w-full px-2 py-1.5 border rounded text-xs"
              placeholder="¥3,980"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-0.5">商品画像URL</label>
            <input
              type="text"
              value={editForm.product_image_url || ""}
              onChange={(e) => onEditFormChange("product_image_url", e.target.value)}
              className="w-full px-2 py-1.5 border rounded text-xs"
              placeholder="https://..."
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-0.5">商品ページURL</label>
            <input
              type="text"
              value={editForm.product_url || ""}
              onChange={(e) => onEditFormChange("product_url", e.target.value)}
              className="w-full px-2 py-1.5 border rounded text-xs"
              placeholder="https://kyogokupro.com/products/..."
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-0.5">カートURL（任意）</label>
            <input
              type="text"
              value={editForm.product_cart_url || ""}
              onChange={(e) => onEditFormChange("product_cart_url", e.target.value)}
              className="w-full px-2 py-1.5 border rounded text-xs"
              placeholder="https://..."
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-0.5">ページURLパターン</label>
            <input
              type="text"
              value={editForm.page_url_pattern || ""}
              onChange={(e) => onEditFormChange("page_url_pattern", e.target.value)}
              className="w-full px-2 py-1.5 border rounded text-xs"
              placeholder="/products/*"
            />
          </div>
          <div className="flex gap-2 pt-1">
            <button
              onClick={(e) => { e.stopPropagation(); onSaveEdit(); }}
              className="flex-1 px-2 py-1.5 bg-orange-600 text-white rounded text-xs hover:bg-orange-700"
            >
              保存
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onCancelEdit(); }}
              className="flex-1 px-2 py-1.5 bg-gray-200 text-gray-600 rounded text-xs hover:bg-gray-300"
            >
              キャンセル
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
