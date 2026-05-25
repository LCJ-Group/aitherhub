import { useState, useEffect, useCallback } from "react";
import axios from "axios";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

export default function ProductMasterPanel({ adminKey }) {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [editingProduct, setEditingProduct] = useState(null);
  const [formData, setFormData] = useState({
    product_name: "",
    brand_name: "",
    product_image_urls: "",
    keywords: "",
  });
  const [saving, setSaving] = useState(false);

  const fetchProducts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(`${BASE_URL}/api/v1/ai-clip/product-master`, {
        headers: { "X-Admin-Key": adminKey },
        timeout: 15000,
      });
      setProducts(res.data || []);
    } catch (err) {
      setError(err.message || "取得に失敗しました");
    } finally {
      setLoading(false);
    }
  }, [adminKey]);

  useEffect(() => {
    fetchProducts();
  }, [fetchProducts]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!formData.product_name.trim()) return;
    setSaving(true);
    try {
      const payload = {
        product_name: formData.product_name.trim(),
        brand_name: formData.brand_name.trim(),
        product_image_urls: formData.product_image_urls
          .split("\n")
          .map((u) => u.trim())
          .filter(Boolean),
        keywords: formData.keywords
          .split(",")
          .map((k) => k.trim())
          .filter(Boolean),
      };
      if (editingProduct) {
        await axios.put(
          `${BASE_URL}/api/v1/ai-clip/product-master/${editingProduct.id}`,
          payload,
          { headers: { "X-Admin-Key": adminKey }, timeout: 15000 }
        );
      } else {
        await axios.post(`${BASE_URL}/api/v1/ai-clip/product-master`, payload, {
          headers: { "X-Admin-Key": adminKey },
          timeout: 15000,
        });
      }
      setShowForm(false);
      setEditingProduct(null);
      setFormData({ product_name: "", brand_name: "", product_image_urls: "", keywords: "" });
      fetchProducts();
    } catch (err) {
      alert("保存に失敗しました: " + (err.response?.data?.detail || err.message));
    } finally {
      setSaving(false);
    }
  };

  const handleEdit = (product) => {
    setEditingProduct(product);
    setFormData({
      product_name: product.product_name || "",
      brand_name: product.brand_name || "",
      product_image_urls: (product.product_image_urls || []).join("\n"),
      keywords: (product.keywords || []).join(", "),
    });
    setShowForm(true);
  };

  const handleDelete = async (product) => {
    if (!confirm(`「${product.product_name}」を削除しますか？`)) return;
    try {
      await axios.delete(
        `${BASE_URL}/api/v1/ai-clip/product-master/${product.id}`,
        { headers: { "X-Admin-Key": adminKey }, timeout: 15000 }
      );
      fetchProducts();
    } catch (err) {
      alert("削除に失敗しました: " + (err.response?.data?.detail || err.message));
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2">
            📦 商品マスター
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            商品を登録すると、AIクリップ生成時に商品名で自動マッチングして画像が適用されます
          </p>
        </div>
        <button
          onClick={() => {
            setEditingProduct(null);
            setFormData({ product_name: "", brand_name: "", product_image_urls: "", keywords: "" });
            setShowForm(true);
          }}
          className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium transition-colors"
        >
          + 新規登録
        </button>
      </div>

      {/* Form Modal */}
      {showForm && (
        <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
          <h3 className="text-lg font-semibold mb-4">
            {editingProduct ? "商品を編集" : "新規商品を登録"}
          </h3>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                商品名 <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={formData.product_name}
                onChange={(e) => setFormData({ ...formData, product_name: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500"
                placeholder="例: Dr.Kozu Vampire Mask"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                ブランド名
              </label>
              <input
                type="text"
                value={formData.brand_name}
                onChange={(e) => setFormData({ ...formData, brand_name: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500"
                placeholder="例: Dr.Kozu"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                商品画像URL（1行に1URL）
              </label>
              <textarea
                value={formData.product_image_urls}
                onChange={(e) => setFormData({ ...formData, product_image_urls: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500 font-mono text-xs"
                rows={5}
                placeholder={"https://example.com/image1.jpg\nhttps://example.com/image2.jpg\nhttps://example.com/image3.jpg"}
              />
              <p className="text-xs text-gray-400 mt-1">
                複数画像を登録すると、PiP合成時にローテーション表示されます
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                キーワード（カンマ区切り）
              </label>
              <input
                type="text"
                value={formData.keywords}
                onChange={(e) => setFormData({ ...formData, keywords: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500"
                placeholder="例: バンパイアマスク, ヴァンパイアマスク, vampire mask"
              />
              <p className="text-xs text-gray-400 mt-1">
                商品名以外でもマッチさせたいキーワードを登録できます
              </p>
            </div>
            <div className="flex gap-3">
              <button
                type="submit"
                disabled={saving}
                className="px-6 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                {saving ? "保存中..." : editingProduct ? "更新" : "登録"}
              </button>
              <button
                type="button"
                onClick={() => { setShowForm(false); setEditingProduct(null); }}
                className="px-6 py-2 bg-gray-200 hover:bg-gray-300 text-gray-700 rounded-lg text-sm font-medium transition-colors"
              >
                キャンセル
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-green-500"></div>
        </div>
      )}

      {/* Product List */}
      {!loading && products.length === 0 && (
        <div className="text-center py-12 text-gray-400">
          <p className="text-4xl mb-2">📦</p>
          <p>商品が登録されていません</p>
          <p className="text-xs mt-1">「新規登録」から商品を追加してください</p>
        </div>
      )}

      {!loading && products.length > 0 && (
        <div className="grid gap-4">
          {products.map((product) => (
            <div
              key={product.id}
              className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm hover:shadow-md transition-shadow"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-gray-800">{product.product_name}</h3>
                    {product.brand_name && (
                      <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">
                        {product.brand_name}
                      </span>
                    )}
                  </div>
                  {product.keywords && product.keywords.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {product.keywords.map((kw, i) => (
                        <span
                          key={i}
                          className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded"
                        >
                          {kw}
                        </span>
                      ))}
                    </div>
                  )}
                  {/* Image thumbnails */}
                  {product.product_image_urls && product.product_image_urls.length > 0 && (
                    <div className="flex gap-2 mt-3 overflow-x-auto">
                      {product.product_image_urls.map((url, i) => (
                        <img
                          key={i}
                          src={url}
                          alt={`${product.product_name} ${i + 1}`}
                          className="w-16 h-16 object-cover rounded-lg border border-gray-200 flex-shrink-0"
                          onError={(e) => { e.target.style.display = 'none'; }}
                        />
                      ))}
                      <span className="text-xs text-gray-400 self-center ml-1">
                        {product.product_image_urls.length}枚
                      </span>
                    </div>
                  )}
                </div>
                <div className="flex gap-2 ml-4">
                  <button
                    onClick={() => handleEdit(product)}
                    className="px-3 py-1.5 bg-blue-50 hover:bg-blue-100 text-blue-700 rounded-lg text-xs font-medium transition-colors"
                  >
                    編集
                  </button>
                  <button
                    onClick={() => handleDelete(product)}
                    className="px-3 py-1.5 bg-red-50 hover:bg-red-100 text-red-700 rounded-lg text-xs font-medium transition-colors"
                  >
                    削除
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Info */}
      <div className="bg-green-50 border border-green-200 rounded-xl p-4">
        <h4 className="font-medium text-green-800 mb-2">💡 商品マスターの使い方</h4>
        <ul className="text-sm text-green-700 space-y-1">
          <li>• 商品名と画像URLを登録すると、AIクリップ生成時に自動でマッチングされます</li>
          <li>• クリップの「product_name」と商品マスターの「商品名」が一致すると画像が自動適用</li>
          <li>• キーワードを登録すると、部分一致でもマッチします（例: 「バンパイアマスク」→「Vampire Mask」）</li>
          <li>• 複数画像を登録すると、PiP合成時にローテーション表示されます（3秒ごとに切り替え）</li>
          <li>• video_mode が「product_overlay」または「audio_only」の場合に自動適用されます</li>
        </ul>
      </div>
    </div>
  );
}
