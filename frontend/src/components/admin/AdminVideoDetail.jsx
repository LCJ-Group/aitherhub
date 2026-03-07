import { useState, useEffect } from "react";
import axios from "axios";

function Section({ title, icon, children, action }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 mb-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
          <span>{icon}</span> {title}
        </h3>
        {action && action}
      </div>
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

function formatNumber(n) {
  if (n === null || n === undefined) return "-";
  return Number(n).toLocaleString("ja-JP");
}

// ── Recalculate Metrics Section ──────────────────────────────────────────────

function RecalcSection({ videoId, adminKey, uploadType, onRecalcComplete }) {
  const [recalcState, setRecalcState] = useState("idle"); // idle | loading | dry-run-done | executing | done | error
  const [dryRunResult, setDryRunResult] = useState(null);
  const [executeResult, setExecuteResult] = useState(null);
  const [recalcLogs, setRecalcLogs] = useState([]);
  const [errorMsg, setErrorMsg] = useState(null);
  const [showLogs, setShowLogs] = useState(false);

  const baseURL = import.meta.env.VITE_API_BASE_URL;

  const isCleanVideo = uploadType === "clean_video";

  const runDryRun = async () => {
    setRecalcState("loading");
    setErrorMsg(null);
    setDryRunResult(null);
    setExecuteResult(null);
    try {
      const res = await axios.post(
        `${baseURL}/api/v1/admin/recompute-phase-metrics/${videoId}?dry_run=true`,
        {},
        { headers: { "X-Admin-Key": adminKey } }
      );
      setDryRunResult(res.data);
      setRecalcLogs(res.data.logs || []);
      setRecalcState("dry-run-done");
    } catch (err) {
      setErrorMsg(err.response?.data?.detail || err.message || "Dry run failed");
      setRecalcState("error");
    }
  };

  const runExecute = async () => {
    setRecalcState("executing");
    setErrorMsg(null);
    try {
      const res = await axios.post(
        `${baseURL}/api/v1/admin/recompute-phase-metrics/${videoId}?dry_run=false`,
        {},
        { headers: { "X-Admin-Key": adminKey } }
      );
      setExecuteResult(res.data);
      setRecalcLogs(res.data.logs || []);
      setRecalcState("done");
      if (onRecalcComplete) onRecalcComplete();
    } catch (err) {
      setErrorMsg(err.response?.data?.detail || err.message || "Execute failed");
      setRecalcState("error");
    }
  };

  if (!isCleanVideo) {
    return (
      <Section title="Recalculate Metrics" icon="&#9889;">
        <p className="text-xs text-gray-400">
          CSV (clean_video) タイプの動画のみ再計算できます。
        </p>
      </Section>
    );
  }

  return (
    <Section title="Recalculate Metrics" icon="&#9889;">
      {/* Data Protection Notice */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4">
        <div className="flex items-start gap-2">
          <span className="text-blue-500 text-sm mt-0.5">&#9432;</span>
          <div className="text-xs text-blue-700">
            <p className="font-semibold mb-1">データ保護ルール</p>
            <p>
              <span className="font-medium">Derived Data のみ更新</span>
              （gmv, order_count, viewer_count, clicks 等）
            </p>
            <p className="mt-0.5">
              Raw Data / Human Data（評価, タグ, コメント）は
              <span className="font-semibold text-blue-800"> 一切変更しません</span>
            </p>
          </div>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex gap-3 mb-4">
        <button
          onClick={runDryRun}
          disabled={recalcState === "loading" || recalcState === "executing"}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            recalcState === "loading"
              ? "bg-gray-100 text-gray-400 cursor-not-allowed"
              : "bg-gray-100 text-gray-700 hover:bg-gray-200"
          }`}
        >
          {recalcState === "loading" && (
            <span className="animate-spin inline-block w-4 h-4 border-2 border-gray-300 border-t-gray-600 rounded-full"></span>
          )}
          Dry Run
        </button>

        <button
          onClick={runExecute}
          disabled={recalcState !== "dry-run-done"}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            recalcState === "dry-run-done"
              ? "bg-orange-500 text-white hover:bg-orange-600"
              : "bg-gray-100 text-gray-400 cursor-not-allowed"
          }`}
        >
          {recalcState === "executing" && (
            <span className="animate-spin inline-block w-4 h-4 border-2 border-orange-200 border-t-white rounded-full"></span>
          )}
          Recalculate Metrics
        </button>
      </div>

      {/* Dry Run Result */}
      {dryRunResult && recalcState === "dry-run-done" && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-yellow-600">&#9888;</span>
            <span className="text-sm font-semibold text-yellow-800">Dry Run 結果（プレビュー）</span>
          </div>

          {/* Summary */}
          <div className="grid grid-cols-3 gap-3 mb-3">
            <div className="bg-white rounded-lg p-3 text-center border border-yellow-100">
              <div className="text-lg font-bold text-gray-800">
                {dryRunResult.diff?.phases_changed ?? 0}
              </div>
              <div className="text-xs text-gray-500 mt-1">変更フェーズ</div>
            </div>
            <div className="bg-white rounded-lg p-3 text-center border border-yellow-100">
              <div className={`text-lg font-bold ${
                (dryRunResult.diff?.gmv_delta || 0) !== 0 ? "text-orange-600" : "text-gray-800"
              }`}>
                {(dryRunResult.diff?.gmv_delta || 0) > 0 ? "+" : ""}
                {formatNumber(dryRunResult.diff?.gmv_delta || 0)}
              </div>
              <div className="text-xs text-gray-500 mt-1">GMV差分</div>
            </div>
            <div className="bg-white rounded-lg p-3 text-center border border-yellow-100">
              <div className={`text-lg font-bold ${
                (dryRunResult.diff?.orders_delta || 0) !== 0 ? "text-orange-600" : "text-gray-800"
              }`}>
                {(dryRunResult.diff?.orders_delta || 0) > 0 ? "+" : ""}
                {dryRunResult.diff?.orders_delta || 0}
              </div>
              <div className="text-xs text-gray-500 mt-1">注文差分</div>
            </div>
          </div>

          {/* Phase Diffs */}
          {dryRunResult.diff?.phase_diffs?.length > 0 && (
            <div className="mb-3">
              <p className="text-xs font-semibold text-yellow-700 mb-2">フェーズ別変更:</p>
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {dryRunResult.diff.phase_diffs.map((pd, i) => (
                  <div key={i} className="bg-white rounded px-3 py-1.5 text-xs border border-yellow-100">
                    <span className="font-mono text-gray-500">Phase {pd.phase_index}</span>
                    {pd.gmv && (
                      <span className="ml-2 text-gray-700">
                        GMV: {formatNumber(pd.gmv.before)} → <span className="font-semibold text-orange-600">{formatNumber(pd.gmv.after)}</span>
                      </span>
                    )}
                    {pd.order_count && (
                      <span className="ml-2 text-gray-700">
                        Orders: {pd.order_count.before} → <span className="font-semibold text-orange-600">{pd.order_count.after}</span>
                      </span>
                    )}
                    {pd.product_clicks && (
                      <span className="ml-2 text-gray-700">
                        Clicks: {pd.product_clicks.before} → <span className="font-semibold text-orange-600">{pd.product_clicks.after}</span>
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {dryRunResult.diff?.phases_changed === 0 && (
            <p className="text-xs text-yellow-700">変更なし — 現在のデータは最新ロジックと一致しています。</p>
          )}

          <div className="flex items-center gap-2 mt-3">
            <span className="text-xs text-yellow-600">Logic Version: v{dryRunResult.logic_version}</span>
            <span className="text-xs text-yellow-600">|</span>
            <span className="text-xs text-yellow-600">{dryRunResult.duration_ms}ms</span>
          </div>
        </div>
      )}

      {/* Execute Result */}
      {executeResult && recalcState === "done" && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4 mb-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-green-600">&#10003;</span>
            <span className="text-sm font-semibold text-green-800">再計算完了</span>
          </div>
          <div className="grid grid-cols-3 gap-3 mb-3">
            <div className="bg-white rounded-lg p-3 text-center border border-green-100">
              <div className="text-lg font-bold text-green-700">
                {executeResult.phases_updated}
              </div>
              <div className="text-xs text-gray-500 mt-1">更新フェーズ</div>
            </div>
            <div className="bg-white rounded-lg p-3 text-center border border-green-100">
              <div className="text-lg font-bold text-gray-800">
                {formatNumber(executeResult.after_summary?.total_gmv || 0)}
              </div>
              <div className="text-xs text-gray-500 mt-1">Total GMV</div>
            </div>
            <div className="bg-white rounded-lg p-3 text-center border border-green-100">
              <div className="text-lg font-bold text-gray-800">
                {executeResult.after_summary?.total_orders || 0}
              </div>
              <div className="text-xs text-gray-500 mt-1">Total Orders</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-green-600">Logic Version: v{executeResult.logic_version}</span>
            <span className="text-xs text-green-600">|</span>
            <span className="text-xs text-green-600">{executeResult.duration_ms}ms</span>
          </div>
        </div>
      )}

      {/* Error */}
      {errorMsg && recalcState === "error" && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-red-500">&#10007;</span>
            <span className="text-sm font-semibold text-red-800">エラー</span>
          </div>
          <p className="text-xs text-red-700 break-all">{errorMsg}</p>
          <button
            onClick={runDryRun}
            className="mt-3 text-xs text-red-600 hover:text-red-800 underline"
          >
            再試行
          </button>
        </div>
      )}

      {/* Logs Toggle */}
      {recalcLogs.length > 0 && (
        <div>
          <button
            onClick={() => setShowLogs(!showLogs)}
            className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
          >
            <span>{showLogs ? "&#9660;" : "&#9654;"}</span>
            実行ログ ({recalcLogs.length}行)
          </button>
          {showLogs && (
            <div className="mt-2 bg-gray-900 rounded-lg p-3 max-h-60 overflow-y-auto">
              {recalcLogs.map((line, i) => (
                <div key={i} className={`text-xs font-mono leading-relaxed ${
                  line.includes("ERROR") ? "text-red-400" :
                  line.includes("WARN") ? "text-yellow-400" :
                  line.includes("Phase") ? "text-cyan-400" :
                  "text-gray-300"
                }`}>
                  {line}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Section>
  );
}

// ── Recalc History Section ───────────────────────────────────────────────────

function RecalcHistory({ videoId, adminKey }) {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const baseURL = import.meta.env.VITE_API_BASE_URL;

  const fetchLogs = async () => {
    setLoading(true);
    try {
      const res = await axios.get(
        `${baseURL}/api/v1/admin/recalc-log/${videoId}?limit=10`,
        { headers: { "X-Admin-Key": adminKey } }
      );
      setLogs(res.data.logs || []);
    } catch {
      setLogs([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (expanded && logs.length === 0) {
      fetchLogs();
    }
  }, [expanded]);

  return (
    <Section title="再計算履歴" icon="&#128203;">
      <button
        onClick={() => { setExpanded(!expanded); if (!expanded) fetchLogs(); }}
        className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1 mb-2"
      >
        <span>{expanded ? "&#9660;" : "&#9654;"}</span>
        {expanded ? "閉じる" : "履歴を表示"}
      </button>

      {expanded && loading && (
        <div className="flex justify-center py-4">
          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-orange-500"></div>
        </div>
      )}

      {expanded && !loading && logs.length === 0 && (
        <p className="text-xs text-gray-400">再計算履歴はありません</p>
      )}

      {expanded && !loading && logs.length > 0 && (
        <div className="space-y-2">
          {logs.map((log) => (
            <div key={log.id} className={`rounded-lg p-3 text-xs border ${
              log.status === "success" ? "bg-green-50 border-green-100" :
              log.status === "error" ? "bg-red-50 border-red-100" :
              "bg-gray-50 border-gray-100"
            }`}>
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded font-medium ${
                    log.mode === "execute" ? "bg-orange-100 text-orange-700" : "bg-gray-100 text-gray-600"
                  }`}>
                    {log.mode}
                  </span>
                  <span className={`px-2 py-0.5 rounded ${
                    log.status === "success" ? "bg-green-100 text-green-700" :
                    log.status === "error" ? "bg-red-100 text-red-700" :
                    "bg-gray-100 text-gray-600"
                  }`}>
                    {log.status}
                  </span>
                  <span className="text-gray-400">v{log.logic_version}</span>
                </div>
                <span className="text-gray-400">
                  {log.created_at ? new Date(log.created_at).toLocaleString("ja-JP") : "-"}
                </span>
              </div>
              <div className="flex items-center gap-3 text-gray-500">
                <span>by: {log.triggered_by || "-"}</span>
                <span>{log.duration_ms}ms</span>
                {log.diff && (
                  <span>
                    {log.diff.phases_changed} phases changed
                    {log.diff.gmv_delta !== 0 && `, GMV ${log.diff.gmv_delta > 0 ? "+" : ""}${formatNumber(log.diff.gmv_delta)}`}
                  </span>
                )}
              </div>
              {log.error_message && (
                <div className="mt-1 text-red-600 break-all">{log.error_message}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ── Main Component ───────────────────────────────────────────────────────────

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

          {/* Recalculate Metrics */}
          <RecalcSection
            videoId={videoId}
            adminKey={adminKey}
            uploadType={basic_info.upload_type}
            onRecalcComplete={fetchDetail}
          />

          {/* Recalc History */}
          <RecalcHistory videoId={videoId} adminKey={adminKey} />
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
