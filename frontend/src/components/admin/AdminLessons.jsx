import { useState, useEffect, useCallback } from "react";
import axios from "axios";

/**
 * AdminLessons – プロジェクトの永続記憶（教訓・危険・依存・ルール・チェックリスト）
 *
 * lessons_learned テーブルのCRUD。
 * AI が毎回タスク開始時に ai-context 経由で読む知識ベース。
 * 人間も管理画面から閲覧・追加・編集・無効化できる。
 */

const CATEGORY_META = {
  danger:     { label: "危険",           color: "bg-red-600 text-white",        icon: "⛔" },
  checklist:  { label: "チェックリスト", color: "bg-blue-100 text-blue-700",    icon: "✅" },
  rule:       { label: "正常状態ルール", color: "bg-green-100 text-green-700",  icon: "📏" },
  dependency: { label: "依存関係",       color: "bg-purple-100 text-purple-700",icon: "🔗" },
  status:     { label: "機能ステータス", color: "bg-yellow-100 text-yellow-700",icon: "📊" },
  preference: { label: "ユーザー方針",   color: "bg-gray-100 text-gray-600",    icon: "⚙️" },
  lesson:     { label: "教訓",           color: "bg-orange-100 text-orange-700",icon: "💡" },
};

const CATEGORIES = Object.keys(CATEGORY_META);

const INITIAL_FORM = {
  category: "lesson",
  title: "",
  content: "",
  related_files: "",
  related_feature: "",
  source_bug_id: "",
};

export default function AdminLessons({ adminKey }) {
  const [lessons, setLessons] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState(null);
  const [form, setForm] = useState({ ...INITIAL_FORM });
  const [expandedId, setExpandedId] = useState(null);
  const [filterCategory, setFilterCategory] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const baseURL = import.meta.env.VITE_API_BASE_URL;

  const fetchLessons = useCallback(async () => {
    setLoading(true);
    try {
      const params = { limit: 200 };
      if (filterCategory) params.category = filterCategory;
      params.is_active = showInactive ? "" : "true";
      // Remove empty params
      if (!params.is_active) delete params.is_active;
      const res = await axios.get(`${baseURL}/api/v1/admin/lessons`, {
        headers: { "X-Admin-Key": adminKey }, params,
      });
      setLessons(res.data.lessons || []);
      setTotal(res.data.total || 0);
    } catch (e) {
      console.error("Failed to fetch lessons:", e);
    }
    setLoading(false);
  }, [baseURL, adminKey, filterCategory, showInactive]);

  useEffect(() => { fetchLessons(); }, [fetchLessons]);

  const handleSubmit = async () => {
    try {
      const payload = {
        ...form,
        source_bug_id: form.source_bug_id ? parseInt(form.source_bug_id) : null,
      };
      if (editId) {
        await axios.put(`${baseURL}/api/v1/admin/lessons/${editId}`, payload, {
          headers: { "X-Admin-Key": adminKey },
        });
      } else {
        await axios.post(`${baseURL}/api/v1/admin/lessons`, payload, {
          headers: { "X-Admin-Key": adminKey },
        });
      }
      setShowForm(false);
      setEditId(null);
      setForm({ ...INITIAL_FORM });
      fetchLessons();
    } catch (e) {
      alert("保存に失敗しました: " + (e.response?.data?.detail || e.message));
    }
  };

  const handleEdit = (lesson) => {
    setForm({
      category: lesson.category || "lesson",
      title: lesson.title || "",
      content: lesson.content || "",
      related_files: lesson.related_files || "",
      related_feature: lesson.related_feature || "",
      source_bug_id: lesson.source_bug_id || "",
    });
    setEditId(lesson.id);
    setShowForm(true);
  };

  const handleDeactivate = async (id) => {
    if (!window.confirm("この教訓を無効化しますか？（データは保持されます）")) return;
    try {
      await axios.delete(`${baseURL}/api/v1/admin/lessons/${id}`, {
        headers: { "X-Admin-Key": adminKey },
      });
      fetchLessons();
    } catch (e) {
      alert("無効化に失敗しました: " + (e.response?.data?.detail || e.message));
    }
  };

  const handleReactivate = async (id) => {
    try {
      await axios.put(`${baseURL}/api/v1/admin/lessons/${id}`, { is_active: true }, {
        headers: { "X-Admin-Key": adminKey },
      });
      fetchLessons();
    } catch (e) {
      alert("有効化に失敗しました: " + (e.response?.data?.detail || e.message));
    }
  };

  const catMeta = (cat) => CATEGORY_META[cat] || { label: cat, color: "bg-gray-100 text-gray-600", icon: "📝" };

  // カテゴリ別の件数集計
  const categoryCounts = lessons.reduce((acc, l) => {
    acc[l.category] = (acc[l.category] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      {/* ヘッダー */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold flex items-center gap-2">
            🧠 プロジェクトの記憶
            <span className="text-sm font-normal text-gray-500">{total}件</span>
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            AIが毎回タスク開始時に読む知識ベース。教訓・危険・チェックリスト・依存関係・ルールを管理します。
          </p>
        </div>
        <button
          onClick={() => { setShowForm(true); setEditId(null); setForm({ ...INITIAL_FORM }); }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium"
        >
          + 新規追加
        </button>
      </div>

      {/* カテゴリサマリーカード */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
        {CATEGORIES.map((cat) => {
          const meta = catMeta(cat);
          const count = categoryCounts[cat] || 0;
          const isActive = filterCategory === cat;
          return (
            <button
              key={cat}
              onClick={() => setFilterCategory(isActive ? "" : cat)}
              className={`p-2 rounded-lg text-center text-xs font-medium transition-all border-2 ${
                isActive ? "border-blue-500 shadow-md" : "border-transparent"
              } ${meta.color} hover:opacity-80`}
            >
              <div className="text-lg">{meta.icon}</div>
              <div>{meta.label}</div>
              <div className="text-lg font-bold">{count}</div>
            </button>
          );
        })}
      </div>

      {/* フィルタバー */}
      <div className="flex items-center gap-3 text-sm">
        <label className="flex items-center gap-1 text-gray-600">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(e) => setShowInactive(e.target.checked)}
            className="rounded"
          />
          無効化済みも表示
        </label>
        {filterCategory && (
          <button
            onClick={() => setFilterCategory("")}
            className="text-blue-600 hover:underline"
          >
            フィルタ解除
          </button>
        )}
      </div>

      {/* 新規作成/編集フォーム */}
      {showForm && (
        <div className="bg-white border rounded-lg p-4 shadow-sm space-y-3">
          <h3 className="font-bold text-sm">
            {editId ? "教訓を編集" : "新しい教訓を追加"}
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">カテゴリ</label>
              <select
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="w-full border rounded px-2 py-1.5 text-sm"
              >
                {CATEGORIES.map((cat) => (
                  <option key={cat} value={cat}>
                    {catMeta(cat).icon} {catMeta(cat).label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">関連機能</label>
              <input
                value={form.related_feature}
                onChange={(e) => setForm({ ...form, related_feature: e.target.value })}
                className="w-full border rounded px-2 py-1.5 text-sm"
                placeholder="例: グラフ表示, アップロード, 解析パイプライン"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">タイトル（AIサマリーに表示される短い要約）</label>
            <input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              className="w-full border rounded px-2 py-1.5 text-sm"
              placeholder="例: ステータスをuploadedに戻すな — データが消失する"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">詳細内容</label>
            <textarea
              value={form.content}
              onChange={(e) => setForm({ ...form, content: e.target.value })}
              className="w-full border rounded px-2 py-1.5 text-sm"
              rows={4}
              placeholder={
                form.category === "dependency"
                  ? "依存先ファイル（カンマ区切り）: CsvAssetPanel.jsx, process_video.py"
                  : form.category === "status"
                  ? "現在の状態: OK / 要注意 / 壊れている"
                  : "詳細な説明..."
              }
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">関連ファイル（カンマ区切り）</label>
              <input
                value={form.related_files}
                onChange={(e) => setForm({ ...form, related_files: e.target.value })}
                className="w-full border rounded px-2 py-1.5 text-sm"
                placeholder="例: video.py, CsvAssetPanel.jsx"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">元バグID（任意）</label>
              <input
                value={form.source_bug_id}
                onChange={(e) => setForm({ ...form, source_bug_id: e.target.value })}
                className="w-full border rounded px-2 py-1.5 text-sm"
                placeholder="例: 1"
              />
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleSubmit}
              className="px-4 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700"
            >
              {editId ? "更新" : "追加"}
            </button>
            <button
              onClick={() => { setShowForm(false); setEditId(null); }}
              className="px-4 py-1.5 bg-gray-200 rounded text-sm hover:bg-gray-300"
            >
              キャンセル
            </button>
          </div>
        </div>
      )}

      {/* 教訓一覧 */}
      {loading ? (
        <div className="text-center text-gray-400 py-8">読み込み中...</div>
      ) : lessons.length === 0 ? (
        <div className="text-center text-gray-400 py-8">教訓がありません</div>
      ) : (
        <div className="space-y-2">
          {lessons.map((l) => {
            const meta = catMeta(l.category);
            const isExpanded = expandedId === l.id;
            const isInactive = !l.is_active;
            return (
              <div
                key={l.id}
                className={`border rounded-lg overflow-hidden transition-all ${
                  isInactive ? "opacity-50 bg-gray-50" : "bg-white"
                } ${l.category === "danger" ? "border-red-300" : ""}`}
              >
                {/* ヘッダー行 */}
                <div
                  className="flex items-center gap-3 p-3 cursor-pointer hover:bg-gray-50"
                  onClick={() => setExpandedId(isExpanded ? null : l.id)}
                >
                  <span className="text-lg">{meta.icon}</span>
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${meta.color}`}>
                    {meta.label}
                  </span>
                  <span className="flex-1 text-sm font-medium truncate">{l.title}</span>
                  {l.related_feature && (
                    <span className="px-2 py-0.5 bg-indigo-50 text-indigo-600 rounded text-xs">
                      {l.related_feature}
                    </span>
                  )}
                  {isInactive && (
                    <span className="px-2 py-0.5 bg-gray-200 text-gray-500 rounded text-xs">
                      無効
                    </span>
                  )}
                  <span className="text-xs text-gray-400">
                    {l.created_at ? new Date(l.created_at).toLocaleDateString("ja-JP") : ""}
                  </span>
                  <span className="text-gray-400">{isExpanded ? "▲" : "▼"}</span>
                </div>

                {/* 展開詳細 */}
                {isExpanded && (
                  <div className="border-t px-4 py-3 bg-gray-50 space-y-2 text-sm">
                    {l.content && (
                      <div>
                        <span className="text-xs text-gray-500 font-medium">詳細:</span>
                        <p className="mt-1 whitespace-pre-wrap text-gray-700">{l.content}</p>
                      </div>
                    )}
                    {l.related_files && (
                      <div>
                        <span className="text-xs text-gray-500 font-medium">関連ファイル:</span>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {l.related_files.split(",").map((f, i) => (
                            <span key={i} className="px-2 py-0.5 bg-gray-200 rounded text-xs font-mono">
                              {f.trim()}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {l.source_bug_id && (
                      <div className="text-xs text-gray-500">
                        元バグ: #{l.source_bug_id}
                      </div>
                    )}
                    <div className="flex gap-2 pt-2">
                      <button
                        onClick={(e) => { e.stopPropagation(); handleEdit(l); }}
                        className="px-3 py-1 bg-blue-100 text-blue-700 rounded text-xs hover:bg-blue-200"
                      >
                        編集
                      </button>
                      {l.is_active ? (
                        <button
                          onClick={(e) => { e.stopPropagation(); handleDeactivate(l.id); }}
                          className="px-3 py-1 bg-red-100 text-red-700 rounded text-xs hover:bg-red-200"
                        >
                          無効化
                        </button>
                      ) : (
                        <button
                          onClick={(e) => { e.stopPropagation(); handleReactivate(l.id); }}
                          className="px-3 py-1 bg-green-100 text-green-700 rounded text-xs hover:bg-green-200"
                        >
                          有効化
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
