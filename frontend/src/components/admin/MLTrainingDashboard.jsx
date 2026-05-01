import { useState, useEffect, useCallback } from "react";
import axios from "axios";

const API = import.meta.env.VITE_API_URL || "";

export default function MLTrainingDashboard({ adminKey }) {
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [metrics, setMetrics] = useState({ click: [], order: [] });
  const [featureImportance, setFeatureImportance] = useState(null);
  const [loading, setLoading] = useState(true);
  const [triggerLoading, setTriggerLoading] = useState(false);
  const [activeModel, setActiveModel] = useState("click");
  const [effectiveness, setEffectiveness] = useState(null);

  const headers = { "X-Admin-Key": adminKey };

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [statusRes, runsRes, metricsRes, fiRes, effectRes] = await Promise.all([
        axios.get(`${API}/api/v1/ml-training/status`, { headers }),
        axios.get(`${API}/api/v1/ml-training/runs?limit=20`, { headers }),
        axios.get(`${API}/api/v1/ml-training/metrics`, { headers }),
        axios.get(`${API}/api/v1/ml-training/feature-importance?target=${activeModel}`, { headers }),
        axios.get(`${API}/api/v1/ml-training/effectiveness`, { headers }).catch(() => ({ data: null })),
      ]);
      setStatus(statusRes.data);
      setRuns(runsRes.data.runs || []);
      setMetrics(metricsRes.data || { click: [], order: [] });
      setFeatureImportance(fiRes.data);
      setEffectiveness(effectRes.data);
    } catch (e) {
      console.error("ML Training fetch error:", e);
    } finally {
      setLoading(false);
    }
  }, [adminKey, activeModel]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const triggerTraining = async () => {
    setTriggerLoading(true);
    try {
      await axios.post(`${API}/api/v1/ml-training/trigger`, { target: "both" }, { headers });
      alert("学習をキューに追加しました。数分後に結果が反映されます。");
      fetchData();
    } catch (e) {
      alert("学習トリガーに失敗しました: " + (e.response?.data?.detail || e.message));
    } finally {
      setTriggerLoading(false);
    }
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-500">AI学習データを読み込み中...</div>;
  }

  const latestClick = status?.models?.click;
  const latestOrder = status?.models?.order;
  const dataSummary = status?.data_summary || {};

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">🧠 AI切り抜き学習</h2>
          <p className="text-sm text-gray-500 mt-1">
            フィードバック・売上データからクリップ品質を自動学習
          </p>
        </div>
        <button
          onClick={triggerTraining}
          disabled={triggerLoading}
          className="px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 transition-all font-medium"
        >
          {triggerLoading ? "⏳ 実行中..." : "🚀 今すぐ再学習"}
        </button>
      </div>

      {/* Data Summary */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="総フェーズ数" value={dataSummary.total_phases?.toLocaleString()} color="blue" />
        <StatCard label="採点済み" value={dataSummary.rated_phases?.toLocaleString()} color="green" />
        <StatCard label="NG判定" value={dataSummary.ng_phases?.toLocaleString()} color="red" />
        <StatCard label="総クリップ" value={dataSummary.total_clips?.toLocaleString()} color="purple" />
        <StatCard label="売れたクリップ" value={dataSummary.sold_clips?.toLocaleString()} color="orange" />
      </div>

      {/* Model Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ModelCard title="クリック予測モデル" model={latestClick} icon="🖱️" />
        <ModelCard title="注文予測モデル" model={latestOrder} icon="🛒" />
      </div>

      {/* Metrics Chart */}
      <div className="bg-white rounded-xl border p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">📈 精度推移</h3>
          <div className="flex gap-2">
            <button
              onClick={() => setActiveModel("click")}
              className={`px-3 py-1 rounded-md text-sm ${activeModel === "click" ? "bg-blue-100 text-blue-700" : "text-gray-500"}`}
            >
              クリック
            </button>
            <button
              onClick={() => setActiveModel("order")}
              className={`px-3 py-1 rounded-md text-sm ${activeModel === "order" ? "bg-green-100 text-green-700" : "text-gray-500"}`}
            >
              注文
            </button>
          </div>
        </div>
        <MetricsChart data={metrics[activeModel] || []} />
      </div>

      {/* Feature Importance */}
      <div className="bg-white rounded-xl border p-6">
        <h3 className="text-lg font-semibold mb-4">🎯 特徴量重要度（{activeModel === "click" ? "クリック" : "注文"}モデル）</h3>
        <FeatureImportanceChart data={featureImportance} />
      </div>

      {/* Version Effectiveness - NEW */}
      <div className="bg-white rounded-xl border p-6">
        <h3 className="text-lg font-semibold mb-4">AI Version Effectiveness</h3>
        <p className="text-xs text-gray-500 mb-4">各AIバージョンで生成されたクリップの採点・採用率・NG率を比較</p>
        <VersionEffectivenessChart data={effectiveness} />
      </div>

      {/* Training History */}
      <div className="bg-white rounded-xl border p-6">
        <h3 className="text-lg font-semibold mb-4">📋 学習履歴</h3>
        <TrainingHistory runs={runs} />
      </div>
    </div>
  );
}

// ── Sub Components ──

function StatCard({ label, value, color }) {
  const colors = {
    blue: "bg-blue-50 text-blue-700 border-blue-200",
    green: "bg-green-50 text-green-700 border-green-200",
    red: "bg-red-50 text-red-700 border-red-200",
    purple: "bg-purple-50 text-purple-700 border-purple-200",
    orange: "bg-orange-50 text-orange-700 border-orange-200",
  };
  return (
    <div className={`rounded-lg border p-3 text-center ${colors[color]}`}>
      <div className="text-2xl font-bold">{value || "—"}</div>
      <div className="text-xs mt-1 opacity-75">{label}</div>
    </div>
  );
}

function ModelCard({ title, model, icon }) {
  if (!model) {
    return (
      <div className="bg-gray-50 rounded-xl border p-5">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xl">{icon}</span>
          <h4 className="font-semibold text-gray-700">{title}</h4>
        </div>
        <p className="text-sm text-gray-400">未学習</p>
      </div>
    );
  }
  return (
    <div className="bg-white rounded-xl border p-5 hover:shadow-md transition-shadow">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xl">{icon}</span>
        <h4 className="font-semibold text-gray-700">{title}</h4>
        <span className="ml-auto text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full">
          v{model.model_version || "?"}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3 text-center">
        <div>
          <div className="text-lg font-bold text-blue-600">
            {model.auc_score ? (model.auc_score * 100).toFixed(1) + "%" : "—"}
          </div>
          <div className="text-xs text-gray-500">AUC</div>
        </div>
        <div>
          <div className="text-lg font-bold text-green-600">
            {model.precision_at_5 ? (model.precision_at_5 * 100).toFixed(1) + "%" : "—"}
          </div>
          <div className="text-xs text-gray-500">Precision@5</div>
        </div>
        <div>
          <div className="text-lg font-bold text-purple-600">
            {model.f1_score ? (model.f1_score * 100).toFixed(1) + "%" : "—"}
          </div>
          <div className="text-xs text-gray-500">F1</div>
        </div>
      </div>
      <div className="mt-3 text-xs text-gray-400">
        最終学習: {model.last_trained ? new Date(model.last_trained).toLocaleString("ja-JP") : "—"}
        {" | "}データ: {model.dataset_size?.toLocaleString() || "—"}件
      </div>
    </div>
  );
}

function MetricsChart({ data }) {
  if (!data || data.length === 0) {
    return <div className="text-center py-8 text-gray-400">まだ学習履歴がありません</div>;
  }

  const maxAuc = Math.max(...data.map(d => d.auc || 0));
  const minAuc = Math.min(...data.map(d => d.auc || 0));
  const range = maxAuc - minAuc || 0.1;

  return (
    <div className="space-y-2">
      <div className="flex items-end gap-1 h-32">
        {data.map((d, i) => {
          const height = ((d.auc - minAuc) / range) * 100;
          return (
            <div key={i} className="flex-1 flex flex-col items-center justify-end group relative">
              <div className="absolute -top-6 hidden group-hover:block bg-gray-800 text-white text-xs px-2 py-1 rounded whitespace-nowrap z-10">
                AUC: {(d.auc * 100).toFixed(1)}% | {d.date?.split("T")[0]}
              </div>
              <div
                className="w-full bg-blue-400 rounded-t hover:bg-blue-600 transition-colors min-h-[4px]"
                style={{ height: `${Math.max(height, 4)}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-xs text-gray-400">
        <span>{data[0]?.date?.split("T")[0]}</span>
        <span>{data[data.length - 1]?.date?.split("T")[0]}</span>
      </div>
      <div className="flex justify-between text-xs text-gray-500 mt-1">
        <span>AUC: {(minAuc * 100).toFixed(1)}%</span>
        <span>→ {(maxAuc * 100).toFixed(1)}%</span>
      </div>
    </div>
  );
}

function FeatureImportanceChart({ data }) {
  if (!data || !data.features || Object.keys(data.features).length === 0) {
    return <div className="text-center py-8 text-gray-400">特徴量データがありません</div>;
  }

  // Sort by importance and take top 15
  const features = Object.entries(data.features)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15);

  const maxVal = features[0]?.[1] || 1;

  // Friendly names for features
  const featureNames = {
    "duration_sec": "フェーズ長さ",
    "cta_score": "CTAスコア",
    "position_ratio": "動画内の位置",
    "has_product_mention": "商品言及",
    "word_count": "単語数",
    "speech_rate": "話速",
    "energy_mean": "音声エネルギー",
    "viewer_trend": "視聴者推移",
    "fq_blur_score": "画質（ブレ）",
    "fq_brightness_mean": "明るさ",
    "af_energy_mean": "音声パワー",
    "af_pitch_mean": "声の高さ",
    "af_silence_ratio": "無音率",
  };

  return (
    <div className="space-y-2">
      {features.map(([name, value], i) => (
        <div key={name} className="flex items-center gap-3">
          <div className="w-40 text-xs text-gray-600 truncate text-right">
            {featureNames[name] || name}
          </div>
          <div className="flex-1 h-5 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${(value / maxVal) * 100}%`,
                backgroundColor: i < 3 ? "#7c3aed" : i < 7 ? "#3b82f6" : "#94a3b8",
              }}
            />
          </div>
          <div className="w-12 text-xs text-gray-500 text-right">
            {(value * 100).toFixed(1)}%
          </div>
        </div>
      ))}
      {data.trained_at && (
        <div className="text-xs text-gray-400 mt-2">
          モデル: v{data.model_version} | 学習日: {new Date(data.trained_at).toLocaleString("ja-JP")}
        </div>
      )}
    </div>
  );
}

function VersionEffectivenessChart({ data }) {
  if (!data || !data.version_clip_stats || data.version_clip_stats.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400">
        <p>まだバージョン別データがありません</p>
        <p className="text-xs mt-1">AIモデルでクリップが生成されると、ここに効果計測データが表示されます</p>
      </div>
    );
  }

  const stats = data.version_clip_stats;
  const aucHistory = data.auc_history || [];

  return (
    <div className="space-y-4">
      {/* Version comparison table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="pb-2 pr-4">AIバージョン</th>
              <th className="pb-2 pr-4">クリップ数</th>
              <th className="pb-2 pr-4">採用率</th>
              <th className="pb-2 pr-4">NG率</th>
              <th className="pb-2 pr-4">レビュー済</th>
              <th className="pb-2">期間</th>
            </tr>
          </thead>
          <tbody>
            {stats.map((v) => (
              <tr key={v.version} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-2.5 pr-4">
                  <span className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full ${
                    v.version === 'pre-AI' 
                      ? 'bg-gray-100 text-gray-600' 
                      : 'bg-purple-100 text-purple-700'
                  }`}>
                    {v.version === 'pre-AI' ? '— Pre-AI' : `AI ${v.version}`}
                  </span>
                </td>
                <td className="py-2.5 pr-4 font-medium">{v.total_clips}</td>
                <td className="py-2.5 pr-4">
                  <span className={`font-medium ${v.adopt_rate > 50 ? 'text-green-600' : v.adopt_rate > 30 ? 'text-yellow-600' : 'text-gray-500'}`}>
                    {v.reviewed_count > 0 ? `${v.adopt_rate}%` : '—'}
                  </span>
                  {v.reviewed_count > 0 && (
                    <span className="text-xs text-gray-400 ml-1">({v.adopt_count}/{v.reviewed_count})</span>
                  )}
                </td>
                <td className="py-2.5 pr-4">
                  <span className={`font-medium ${v.ng_rate > 30 ? 'text-red-600' : v.ng_rate > 15 ? 'text-yellow-600' : 'text-green-600'}`}>
                    {v.ng_rate}%
                  </span>
                  <span className="text-xs text-gray-400 ml-1">({v.ng_count})</span>
                </td>
                <td className="py-2.5 pr-4 text-xs text-gray-500">{v.reviewed_count}</td>
                <td className="py-2.5 text-xs text-gray-400">
                  {v.first_clip_at ? new Date(v.first_clip_at).toLocaleDateString("ja-JP", { month: "short", day: "numeric" }) : '—'}
                  {v.last_clip_at && v.first_clip_at !== v.last_clip_at && (
                    <> ~ {new Date(v.last_clip_at).toLocaleDateString("ja-JP", { month: "short", day: "numeric" })}</>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* AUC trend mini chart */}
      {aucHistory.length > 0 && (
        <div className="mt-4 pt-4 border-t">
          <h4 className="text-sm font-medium text-gray-700 mb-2">AUCスコア推移</h4>
          <div className="flex items-end gap-2 h-20">
            {aucHistory.map((h, i) => {
              const height = (h.auc_score - 0.5) / 0.5 * 100;
              return (
                <div key={i} className="flex-1 flex flex-col items-center justify-end group relative">
                  <div className="absolute -top-8 hidden group-hover:block bg-gray-800 text-white text-xs px-2 py-1 rounded whitespace-nowrap z-10">
                    {h.model_version} ({h.target}): {(h.auc_score * 100).toFixed(1)}%
                  </div>
                  <div
                    className={`w-full rounded-t min-h-[4px] ${
                      h.target === 'click' ? 'bg-blue-400 hover:bg-blue-600' : 'bg-green-400 hover:bg-green-600'
                    }`}
                    style={{ height: `${Math.max(height, 4)}%` }}
                  />
                  <div className="text-[9px] text-gray-400 mt-1 truncate w-full text-center">
                    {h.model_version?.replace('v7.', '')}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="flex gap-3 mt-2 text-xs text-gray-500">
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-blue-400 rounded-full"></span>Click</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-green-400 rounded-full"></span>Order</span>
          </div>
        </div>
      )}
    </div>
  );
}

function TrainingHistory({ runs }) {
  if (!runs || runs.length === 0) {
    return <div className="text-center py-8 text-gray-400">学習履歴がありません</div>;
  }

  const statusColors = {
    completed: "bg-green-100 text-green-700",
    running: "bg-blue-100 text-blue-700",
    queued: "bg-yellow-100 text-yellow-700",
    failed: "bg-red-100 text-red-700",
  };

  const statusLabels = {
    completed: "完了",
    running: "実行中",
    queued: "待機中",
    failed: "失敗",
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 border-b">
            <th className="pb-2 pr-4">日時</th>
            <th className="pb-2 pr-4">ターゲット</th>
            <th className="pb-2 pr-4">バージョン</th>
            <th className="pb-2 pr-4">ステータス</th>
            <th className="pb-2 pr-4">データ数</th>
            <th className="pb-2 pr-4">AUC</th>
            <th className="pb-2 pr-4">Precision@5</th>
            <th className="pb-2">F1</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.run_id} className="border-b border-gray-50 hover:bg-gray-50">
              <td className="py-2 pr-4 text-xs text-gray-500">
                {run.started_at ? new Date(run.started_at).toLocaleString("ja-JP", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}
              </td>
              <td className="py-2 pr-4">
                <span className={`text-xs px-2 py-0.5 rounded ${run.target === "click" ? "bg-blue-50 text-blue-600" : "bg-green-50 text-green-600"}`}>
                  {run.target === "click" ? "クリック" : "注文"}
                </span>
              </td>
              <td className="py-2 pr-4 text-xs">{run.model_version || "—"}</td>
              <td className="py-2 pr-4">
                <span className={`text-xs px-2 py-0.5 rounded ${statusColors[run.status] || "bg-gray-100 text-gray-600"}`}>
                  {statusLabels[run.status] || run.status}
                </span>
              </td>
              <td className="py-2 pr-4 text-xs">{run.dataset_size?.toLocaleString() || "—"}</td>
              <td className="py-2 pr-4 text-xs font-medium">
                {run.auc_score ? (run.auc_score * 100).toFixed(1) + "%" : "—"}
              </td>
              <td className="py-2 pr-4 text-xs">
                {run.precision_at_5 ? (run.precision_at_5 * 100).toFixed(1) + "%" : "—"}
              </td>
              <td className="py-2 text-xs">
                {run.f1_score ? (run.f1_score * 100).toFixed(1) + "%" : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
