import { useState, useEffect } from "react";
import axios from "axios";

function Section({ title, icon, children }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 mb-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
        <span>{icon}</span> {title}
      </h3>
      {children}
    </div>
  );
}

function InfoRow({ label, value, mono = false }) {
  return (
    <div className="flex justify-between py-1.5 border-b border-gray-50 last:border-0">
      <span className="text-xs text-gray-500">{label}</span>
      <span className={`text-xs text-gray-800 ${mono ? "font-mono" : ""}`}>
        {value ?? "-"}
      </span>
    </div>
  );
}

function PipelineStep({ step, index }) {
  const icons = {
    success: <span className="text-green-500">&#10003;</span>,
    running: <span className="text-orange-500 animate-pulse">&#9679;</span>,
    failed: <span className="text-red-500">&#10007;</span>,
    pending: <span className="text-gray-300">&#9675;</span>,
    unknown: <span className="text-gray-400">?</span>,
  };
  const bgColors = {
    success: "bg-green-50",
    running: "bg-orange-50",
    failed: "bg-red-50",
    pending: "bg-gray-50",
    unknown: "bg-gray-50",
  };

  return (
    <div className={`flex items-center gap-3 px-3 py-2 rounded-lg ${bgColors[step.status] || "bg-gray-50"}`}>
      <span className="w-5 text-center">{icons[step.status]}</span>
      <span className="text-xs text-gray-500 w-6 font-mono">{index}</span>
      <span className="text-xs text-gray-700 flex-1">{step.label}</span>
      <span className={`text-xs px-2 py-0.5 rounded ${
        step.status === "success" ? "text-green-700 bg-green-100" :
        step.status === "running" ? "text-orange-700 bg-orange-100" :
        step.status === "failed" ? "text-red-700 bg-red-100" :
        "text-gray-500 bg-gray-100"
      }`}>
        {step.status}
      </span>
    </div>
  );
}

function formatDuration(sec) {
  if (!sec) return "-";
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}分${s}秒`;
}

export default function AdminVideoDetail({ videoId, adminKey, onBack }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!videoId) return;
    fetchDetail();
  }, [videoId]);

  const fetchDetail = async () => {
    try {
      setLoading(true);
      setError(null);
      const baseURL = import.meta.env.VITE_API_BASE_URL;
      const res = await axios.get(`${baseURL}/api/v1/admin/videos/${videoId}`, {
        headers: { "X-Admin-Key": adminKey },
      });
      setData(res.data);
    } catch (err) {
      setError("動画詳細の取得に失敗しました");
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-orange-500"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-16">
        <p className="text-red-500 mb-4">{error}</p>
        <button onClick={onBack} className="text-sm text-orange-500 hover:underline">
          一覧に戻る
        </button>
      </div>
    );
  }

  if (!data) return null;

  const { basic_info, queue_info, processing_state, pipeline_steps, phases, sales_moments, reports, transcript, human_labels, dataset } = data;

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={onBack}
          className="text-gray-400 hover:text-gray-600 transition-colors"
        >
          ← 一覧
        </button>
        <div className="flex-1">
          <h2 className="text-lg font-bold text-gray-800 truncate">
            {basic_info.filename || basic_info.video_id.slice(0, 8)}
          </h2>
          <span className="text-xs text-gray-400 font-mono">{basic_info.video_id}</span>
        </div>
        <span className={`px-3 py-1 rounded-full text-sm font-medium ${
          basic_info.status === "DONE" ? "bg-green-100 text-green-800" :
          basic_info.status === "ERROR" ? "bg-red-100 text-red-800" :
          "bg-orange-100 text-orange-800"
        }`}>
          {basic_info.status}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Left Column */}
        <div>
          {/* Basic Info */}
          <Section title="基本情報" icon="&#128196;">
            <InfoRow label="Upload Type" value={
              <span className={`px-2 py-0.5 rounded text-xs ${
                basic_info.upload_type === "clean_video"
                  ? "bg-purple-100 text-purple-700"
                  : "bg-blue-100 text-blue-700"
              }`}>
                {basic_info.upload_type === "clean_video" ? "CSV (clean_video)" : "画面収録"}
              </span>
            } />
            <InfoRow label="Duration" value={formatDuration(basic_info.duration_sec)} />
            <InfoRow label="User" value={basic_info.user_email} />
            <InfoRow label="Created" value={basic_info.created_at ? new Date(basic_info.created_at).toLocaleString("ja-JP") : "-"} />
            <InfoRow label="Updated" value={basic_info.updated_at ? new Date(basic_info.updated_at).toLocaleString("ja-JP") : "-"} />
            <InfoRow label="Step Progress" value={`${basic_info.step_progress || 0}%`} />
            <InfoRow label="Excel (商品)" value={basic_info.has_excel_product ? "あり" : "なし"} />
            <InfoRow label="Excel (トレンド)" value={basic_info.has_excel_trend ? "あり" : "なし"} />
            <InfoRow label="圧縮動画" value={basic_info.has_compressed ? "あり" : "なし"} />
            {basic_info.top_products && (
              <InfoRow label="Top Products" value={basic_info.top_products} />
            )}
            {basic_info.time_offset_seconds > 0 && (
              <InfoRow label="Time Offset" value={`${basic_info.time_offset_seconds}秒`} mono />
            )}
          </Section>

          {/* Queue Info */}
          <Section title="キュー情報" icon="&#128230;">
            <InfoRow label="Enqueued At" value={queue_info.enqueued_at ? new Date(queue_info.enqueued_at).toLocaleString("ja-JP") : "-"} />
            <InfoRow label="Worker Claimed" value={queue_info.worker_claimed_at ? new Date(queue_info.worker_claimed_at).toLocaleString("ja-JP") : "-"} />
            <InfoRow label="Worker Instance" value={queue_info.worker_instance_id} mono />
            <InfoRow label="Dequeue Count" value={queue_info.dequeue_count} mono />
            <InfoRow label="Enqueue Status" value={queue_info.enqueue_status} />
            {queue_info.enqueue_error && (
              <div className="mt-2 p-2 bg-red-50 rounded text-xs text-red-700 break-all">
                {queue_info.enqueue_error}
              </div>
            )}
          </Section>

          {/* Dataset Status */}
          <Section title="学習データ (Dataset)" icon="&#129302;">
            <div className="flex items-center gap-3 mb-3">
              <span className={`px-3 py-1.5 rounded-lg text-sm font-semibold ${
                dataset.status === "included" ? "bg-emerald-100 text-emerald-800" :
                dataset.status === "pending" ? "bg-yellow-100 text-yellow-800" :
                "bg-gray-100 text-gray-600"
              }`}>
                {dataset.status === "included" ? "学習対象" :
                 dataset.status === "pending" ? "処理中" : "除外"}
              </span>
              {dataset.excluded_reason && (
                <span className="text-xs text-gray-500">
                  理由: {dataset.excluded_reason}
                </span>
              )}
            </div>
            <InfoRow label="Phases" value={phases.total} mono />
            <InfoRow label="Sales Moments" value={sales_moments.total} mono />
            <InfoRow label="Reports" value={reports.count} mono />
            <InfoRow label="Transcript Segments" value={transcript.segment_count >= 0 ? transcript.segment_count : "N/A"} mono />
          </Section>
        </div>

        {/* Right Column */}
        <div>
          {/* Pipeline Steps */}
          <Section title="パイプライン処理結果" icon="&#9881;">
            <div className="space-y-1">
              {pipeline_steps.map((step, i) => (
                <PipelineStep key={step.step_name} step={step} index={i} />
              ))}
            </div>
            {processing_state && (
              <div className="mt-3 pt-3 border-t border-gray-100">
                <p className="text-xs text-gray-500 mb-2">Processing State (DB)</p>
                <div className="grid grid-cols-2 gap-2">
                  {["frames_extracted", "audio_extracted", "speech_done", "vision_done"].map((key) => (
                    <div key={key} className="flex items-center gap-1.5">
                      <span className={`w-2 h-2 rounded-full ${processing_state[key] ? "bg-green-500" : "bg-gray-300"}`}></span>
                      <span className="text-xs text-gray-600">{key.replace(/_/g, " ")}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Section>

          {/* Sales Moments */}
          <Section title="Sales Moments" icon="&#128176;">
            {sales_moments.total === 0 ? (
              <p className="text-xs text-gray-400">検出されたmomentはありません</p>
            ) : (
              <div>
                <div className="text-sm font-semibold text-gray-700 mb-2">
                  合計: {sales_moments.total}
                </div>
                {Object.entries(sales_moments.by_source).map(([source, items]) => (
                  <div key={source} className="mb-3">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                        source === "screen" ? "bg-blue-100 text-blue-700" : "bg-purple-100 text-purple-700"
                      }`}>
                        {source}
                      </span>
                    </div>
                    <div className="space-y-1 ml-2">
                      {items.map((item, i) => (
                        <div key={i} className="flex items-center justify-between text-xs py-1 border-b border-gray-50">
                          <div>
                            <span className="text-gray-700 font-medium">{item.moment_type}</span>
                            {item.moment_type_detail && (
                              <span className="text-gray-400 ml-1">({item.moment_type_detail})</span>
                            )}
                          </div>
                          <div className="flex gap-2">
                            <span className="font-mono text-gray-600">{item.count}件</span>
                            {item.avg_confidence && (
                              <span className="text-gray-400">conf: {item.avg_confidence}</span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>

          {/* Human Labels */}
          <Section title="人間ラベル" icon="&#128100;">
            <div className="grid grid-cols-3 gap-3 mb-3">
              <div className="bg-yellow-50 rounded-lg p-3 text-center">
                <div className="text-2xl font-bold text-yellow-700">{human_labels.rated_phases}</div>
                <div className="text-xs text-yellow-600 mt-1">評価済み</div>
              </div>
              <div className="bg-blue-50 rounded-lg p-3 text-center">
                <div className="text-2xl font-bold text-blue-700">{human_labels.tagged_phases}</div>
                <div className="text-xs text-blue-600 mt-1">タグ済み</div>
              </div>
              <div className="bg-green-50 rounded-lg p-3 text-center">
                <div className="text-2xl font-bold text-green-700">{human_labels.commented_phases}</div>
                <div className="text-xs text-green-600 mt-1">コメント済み</div>
              </div>
            </div>
            {human_labels.avg_rating && (
              <InfoRow label="平均評価" value={`${human_labels.avg_rating} / 5.0`} />
            )}
            {human_labels.reviewers && (
              <InfoRow label="レビュアー" value={human_labels.reviewers} />
            )}
            <InfoRow label="全Phases" value={`${phases.total} phases`} mono />
            {phases.total > 0 && (
              <div className="mt-2">
                <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
                  レビュー進捗
                </div>
                <div className="w-full bg-gray-100 rounded-full h-2">
                  <div
                    className="bg-orange-500 h-2 rounded-full transition-all"
                    style={{ width: `${Math.min(100, Math.round((human_labels.rated_phases / phases.total) * 100))}%` }}
                  ></div>
                </div>
                <div className="text-xs text-gray-400 mt-1 text-right">
                  {Math.round((human_labels.rated_phases / phases.total) * 100)}%
                </div>
              </div>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}
