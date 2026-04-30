import { useState, useEffect, useCallback } from "react";

/**
 * SubtitleDictionary — 字幕カスタム辞書管理コンポーネント
 * 
 * Features:
 * 1. 置換辞書: 誤認識テキスト → 正しいテキスト
 * 2. 分割禁止: 字幕の行分割で途中で切れない単語
 * 3. カテゴリ分類: brand, product, person, other
 * 4. 一括インポート
 */
export default function SubtitleDictionary({ adminKey }) {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [filterCategory, setFilterCategory] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [importText, setImportText] = useState("");
  const [saving, setSaving] = useState(false);

  // Form state
  const [form, setForm] = useState({
    from_text: "",
    to_text: "",
    no_break: true,
    category: "brand",
    notes: "",
  });

  const baseURL = import.meta.env.VITE_API_BASE_URL;

  const fetchEntries = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ active_only: "true" });
      if (filterCategory) params.set("category", filterCategory);
      const res = await fetch(`${baseURL}/api/v1/subtitle-dictionary?${params}`, {
        headers: { "X-Admin-Key": adminKey },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEntries(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [baseURL, adminKey, filterCategory]);

  useEffect(() => {
    fetchEntries();
  }, [fetchEntries]);

  const resetForm = () => {
    setForm({ from_text: "", to_text: "", no_break: true, category: "brand", notes: "" });
    setShowAdd(false);
    setEditingId(null);
  };

  const handleSave = async () => {
    if (!form.from_text.trim()) return;
    setSaving(true);
    try {
      const url = editingId
        ? `${baseURL}/api/v1/subtitle-dictionary/${editingId}`
        : `${baseURL}/api/v1/subtitle-dictionary`;
      const method = editingId ? "PUT" : "POST";
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json", "X-Admin-Key": adminKey },
        body: JSON.stringify(form),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      resetForm();
      fetchEntries();
    } catch (err) {
      alert(`保存失敗: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id, fromText) => {
    if (!confirm(`「${fromText}」を削除しますか？`)) return;
    try {
      const res = await fetch(`${baseURL}/api/v1/subtitle-dictionary/${id}`, {
        method: "DELETE",
        headers: { "X-Admin-Key": adminKey },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      fetchEntries();
    } catch (err) {
      alert(`削除失敗: ${err.message}`);
    }
  };

  const handleEdit = (entry) => {
    setForm({
      from_text: entry.from_text,
      to_text: entry.to_text,
      no_break: entry.no_break,
      category: entry.category,
      notes: entry.notes || "",
    });
    setEditingId(entry.id);
    setShowAdd(true);
  };

  const handleBulkImport = async () => {
    if (!importText.trim()) return;
    setSaving(true);
    try {
      // Parse: each line is "from_text → to_text" or just "word" (no-break only)
      const lines = importText.trim().split("\n").filter(l => l.trim());
      const items = lines.map(line => {
        const parts = line.split(/[→\->]+/).map(s => s.trim());
        if (parts.length >= 2) {
          return { from_text: parts[0], to_text: parts[1], no_break: true, category: "brand" };
        }
        return { from_text: parts[0], to_text: "", no_break: true, category: "brand" };
      });

      const res = await fetch(`${baseURL}/api/v1/subtitle-dictionary/bulk-import`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Key": adminKey },
        body: JSON.stringify(items),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();
      alert(`インポート完了: ${result.imported}件追加, ${result.skipped}件スキップ`);
      setImportText("");
      setShowImport(false);
      fetchEntries();
    } catch (err) {
      alert(`インポート失敗: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const categories = [
    { value: "brand", label: "ブランド", color: "bg-purple-100 text-purple-700" },
    { value: "product", label: "商品名", color: "bg-blue-100 text-blue-700" },
    { value: "person", label: "人名", color: "bg-green-100 text-green-700" },
    { value: "other", label: "その他", color: "bg-gray-100 text-gray-700" },
  ];

  const getCategoryBadge = (cat) => {
    const c = categories.find(x => x.value === cat) || categories[3];
    return <span className={`px-2 py-0.5 rounded text-xs font-medium ${c.color}`}>{c.label}</span>;
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">📖 字幕カスタム辞書</h2>
          <p className="text-sm text-gray-500 mt-1">
            音声認識の誤りを自動修正 + 字幕の改行ルールを設定
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowImport(!showImport)}
            className="px-3 py-1.5 text-sm bg-gray-100 hover:bg-gray-200 rounded-md transition"
          >
            📥 一括インポート
          </button>
          <button
            onClick={() => { resetForm(); setShowAdd(true); }}
            className="px-3 py-1.5 text-sm bg-indigo-600 text-white hover:bg-indigo-700 rounded-md transition"
          >
            ＋ 追加
          </button>
        </div>
      </div>

      {/* Info box */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
        <strong>使い方:</strong>
        <ul className="mt-1 space-y-0.5 list-disc list-inside">
          <li><strong>置換</strong>: 「京獄 → KYOGOKU」のように誤認識を自動修正</li>
          <li><strong>分割禁止</strong>: 「ケラチンシャンプー」が途中で改行されない</li>
          <li><strong>Whisper強化</strong>: 登録した単語はWhisperの認識精度も向上</li>
        </ul>
      </div>

      {/* Bulk Import */}
      {showImport && (
        <div className="bg-gray-50 border rounded-lg p-4 space-y-3">
          <h3 className="font-medium text-sm">一括インポート</h3>
          <p className="text-xs text-gray-500">
            1行1エントリ。置換: 「誤認識 → 正しい表記」、分割禁止のみ: 「単語」
          </p>
          <textarea
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder={`京獄 → KYOGOKU\nきょうごく → KYOGOKU\nケラチンシャンプー\nブラックダイヤモンド`}
            className="w-full h-32 px-3 py-2 border rounded-md text-sm font-mono resize-y"
          />
          <div className="flex gap-2">
            <button
              onClick={handleBulkImport}
              disabled={saving || !importText.trim()}
              className="px-3 py-1.5 text-sm bg-green-600 text-white hover:bg-green-700 rounded-md disabled:opacity-50"
            >
              {saving ? "処理中..." : "インポート実行"}
            </button>
            <button
              onClick={() => setShowImport(false)}
              className="px-3 py-1.5 text-sm bg-gray-200 hover:bg-gray-300 rounded-md"
            >
              キャンセル
            </button>
          </div>
        </div>
      )}

      {/* Add/Edit Form */}
      {showAdd && (
        <div className="bg-white border-2 border-indigo-200 rounded-lg p-4 space-y-3">
          <h3 className="font-medium text-sm">
            {editingId ? "エントリ編集" : "新規エントリ追加"}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                認識テキスト（from）*
              </label>
              <input
                type="text"
                value={form.from_text}
                onChange={(e) => setForm({ ...form, from_text: e.target.value })}
                placeholder="京獄、きょうごく"
                className="w-full px-3 py-2 border rounded-md text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                正しい表記（to）
                <span className="text-gray-400 ml-1">空欄=分割禁止のみ</span>
              </label>
              <input
                type="text"
                value={form.to_text}
                onChange={(e) => setForm({ ...form, to_text: e.target.value })}
                placeholder="KYOGOKU"
                className="w-full px-3 py-2 border rounded-md text-sm"
              />
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">カテゴリ</label>
              <select
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="px-3 py-1.5 border rounded-md text-sm"
              >
                {categories.map(c => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 mt-4">
              <input
                type="checkbox"
                id="no_break"
                checked={form.no_break}
                onChange={(e) => setForm({ ...form, no_break: e.target.checked })}
                className="w-4 h-4 text-indigo-600"
              />
              <label htmlFor="no_break" className="text-sm text-gray-700">
                分割禁止（改行で途切れない）
              </label>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">メモ（任意）</label>
            <input
              type="text"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              placeholder="例: タイ語配信でよく誤認識される"
              className="w-full px-3 py-2 border rounded-md text-sm"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              disabled={saving || !form.from_text.trim()}
              className="px-4 py-2 text-sm bg-indigo-600 text-white hover:bg-indigo-700 rounded-md disabled:opacity-50"
            >
              {saving ? "保存中..." : editingId ? "更新" : "追加"}
            </button>
            <button
              onClick={resetForm}
              className="px-4 py-2 text-sm bg-gray-200 hover:bg-gray-300 rounded-md"
            >
              キャンセル
            </button>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-gray-500">フィルタ:</span>
        <button
          onClick={() => setFilterCategory("")}
          className={`px-2 py-1 text-xs rounded ${!filterCategory ? "bg-indigo-100 text-indigo-700 font-medium" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
        >
          全て
        </button>
        {categories.map(c => (
          <button
            key={c.value}
            onClick={() => setFilterCategory(c.value)}
            className={`px-2 py-1 text-xs rounded ${filterCategory === c.value ? "bg-indigo-100 text-indigo-700 font-medium" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
          >
            {c.label}
          </button>
        ))}
        <span className="ml-auto text-xs text-gray-400">{entries.length}件</span>
      </div>

      {/* Table */}
      {loading ? (
        <div className="text-center py-8 text-gray-400">読み込み中...</div>
      ) : error ? (
        <div className="text-center py-8 text-red-500">エラー: {error}</div>
      ) : entries.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          <p className="text-2xl mb-2">📖</p>
          <p>辞書エントリがありません</p>
          <p className="text-xs mt-1">「＋ 追加」ボタンで最初のエントリを登録しましょう</p>
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">認識テキスト</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">→</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">正しい表記</th>
                <th className="px-3 py-2 text-center text-xs font-medium text-gray-500">分割禁止</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">カテゴリ</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {entries.map((entry) => (
                <tr key={entry.id} className="hover:bg-gray-50">
                  <td className="px-3 py-2 font-mono text-red-600">{entry.from_text}</td>
                  <td className="px-3 py-2 text-gray-400">→</td>
                  <td className="px-3 py-2 font-mono text-green-700 font-medium">
                    {entry.to_text || <span className="text-gray-300 italic">（なし）</span>}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {entry.no_break ? (
                      <span className="text-green-600">✓</span>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2">{getCategoryBadge(entry.category)}</td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => handleEdit(entry)}
                      className="text-indigo-600 hover:text-indigo-800 text-xs mr-2"
                    >
                      編集
                    </button>
                    <button
                      onClick={() => handleDelete(entry.id, entry.from_text)}
                      className="text-red-500 hover:text-red-700 text-xs"
                    >
                      削除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
