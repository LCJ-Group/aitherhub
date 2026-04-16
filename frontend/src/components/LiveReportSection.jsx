import { useState, useEffect, useCallback } from "react";
import VideoService from "../base/services/videoService";
import { useSectionState } from "../base/hooks/useSectionState";
import { ErrorState } from "./SectionStateUI";

/**
 * LiveReportSection – AitherHub Live Report v1
 *
 * 3-layer report display:
 *   1. 勝ち区間 TOP3 (Strong Segments)
 *   2. 弱い区間 TOP3 (Weak Segments)
 *   3. {window.__t('liveReportImprovementSuggestions')} 3つ (Improvement Suggestions)
 *
 * Props:
 *   videoData – the full video detail object (must contain .id)
 */

// ── Priority badge colors ──
const PRIORITY_COLORS = {
  high: "bg-red-100 text-red-700 border-red-300",
  medium: "bg-amber-100 text-amber-700 border-amber-300",
  low: "bg-blue-100 text-blue-700 border-blue-300",
};

const PRIORITY_LABELS = {
  high: window.__t('energyHigh'),
  medium: window.__t('energyMedium'),
  low: window.__t('energyLow'),
};

// ── Signal badge config ──
const SIGNAL_BADGES = {
  csv_order: { label: window.__t('liveReportSignalOrder'), color: "bg-emerald-100 text-emerald-700" },
  csv_gmv: { label: window.__t('live_sales'), color: "bg-emerald-100 text-emerald-700" },
  csv_product_clicks: { label: window.__t('clickCount'), color: "bg-blue-100 text-blue-700" },
  purchase_popup: { label: window.__t('liveReportSignalPurchasePop'), color: "bg-rose-100 text-rose-700" },
  product_viewers: { label: window.__t('liveReportSignalViewPop'), color: "bg-purple-100 text-purple-700" },
  human_rating: { label: window.__t('liveReportSignalHumanRating'), color: "bg-amber-100 text-amber-700" },
  cta_high: { label: window.__t('liveReportSignalCta'), color: "bg-indigo-100 text-indigo-700" },
  viewer_spike: { label: window.__t('liveReportSignalViewerSpike'), color: "bg-cyan-100 text-cyan-700" },
  comment_spike: { label: window.__t('liveReportSignalCommentSpike'), color: "bg-teal-100 text-teal-700" },
};

export default function LiveReportSection({ videoData }) {
  // useSectionState for initial fetch (GET existing report)
  const { state: fetchState, data: report, error: fetchError, execute, retry, setData: setReport } = useSectionState("LiveReport");
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState(null);
  const [version, setVersion] = useState(0);

  const videoId = videoData?.id;

  // Fetch existing report on mount
  useEffect(() => {
    if (!videoId) return;
    execute(
      async () => {
        const res = await VideoService.getLiveReport(videoId);
        if (res?.report) {
          setVersion(res.version || 0);
          return res.report;
        }
        return null;
      },
      {
        videoId,
        endpoint: `/api/v1/videos/${videoId}/live-report`,
      }
    );
  }, [videoId]);

  // Generate report (separate from useSectionState since it's a POST action)
  const handleGenerate = async () => {
    if (!videoId) return;
    setGenerating(true);
    setGenError(null);
    try {
      const res = await VideoService.generateLiveReport(videoId);
      if (res?.report) {
        setReport(res.report);
        setVersion(res.version || 0);
      }
    } catch (err) {
      console.error("Failed to generate report:", err);
      setGenError(err?.response?.data?.detail || window.__t('liveReportGenError'));
    } finally {
      setGenerating(false);
    }
  };

  // ── Render helpers ──

  const renderSignalBadges = (signals) => {
    if (!signals || signals.length === 0) return null;
    return (
      <div className="flex flex-wrap gap-1 mt-1">
        {signals.map((sig) => {
          const badge = SIGNAL_BADGES[sig] || { label: sig, color: "bg-gray-100 text-gray-600" };
          return (
            <span
              key={sig}
              className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${badge.color}`}
            >
              {badge.label}
            </span>
          );
        })}
      </div>
    );
  };

  const renderScoreBar = (score, maxScore = 5) => {
    const pct = Math.min((score / maxScore) * 100, 100);
    const color = pct >= 60 ? "bg-emerald-500" : pct >= 30 ? "bg-amber-500" : "bg-red-400";
    return (
      <div className="flex items-center gap-2">
        <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
          <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
        </div>
        <span className="text-xs font-semibold text-gray-500 w-8 text-right">{score}</span>
      </div>
    );
  };

  const renderStrongSegment = (seg, idx) => (
    <div
      key={seg.phase_index}
      className="bg-white border border-emerald-200 rounded-xl p-4 shadow-sm hover:shadow-md transition-shadow"
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold text-emerald-600">#{idx + 1}</span>
          <span className="text-sm font-semibold text-gray-800">
            {seg.time_range_display}
          </span>
        </div>
        {renderScoreBar(seg.score)}
      </div>

      {/* Interpretation */}
      <p className="text-sm text-gray-700 leading-relaxed mb-2">
        {seg.interpretation}
      </p>

      {/* Signal badges */}
      {renderSignalBadges(seg.signals)}

      {/* Metrics summary */}
      <div className="grid grid-cols-3 gap-2 mt-3 text-xs">
        {seg.metrics?.gmv > 0 && (
          <div className="bg-emerald-50 rounded-lg p-2 text-center">
            <div className="text-emerald-600 font-bold">
              ¥{seg.metrics.gmv.toLocaleString()}
            </div>
            <div className="text-gray-500">{window.__t('live_sales')}</div>
          </div>
        )}
        {seg.metrics?.order_count > 0 && (
          <div className="bg-emerald-50 rounded-lg p-2 text-center">
            <div className="text-emerald-600 font-bold">{seg.metrics.order_count}</div>
            <div className="text-gray-500">{window.__t('liveReportMetricOrder')}</div>
          </div>
        )}
        {seg.metrics?.viewer_count > 0 && (
          <div className="bg-blue-50 rounded-lg p-2 text-center">
            <div className="text-blue-600 font-bold">{seg.metrics.viewer_count}</div>
            <div className="text-gray-500">{window.__t('live_viewers')}</div>
          </div>
        )}
        {seg.metrics?.product_clicks > 0 && (
          <div className="bg-purple-50 rounded-lg p-2 text-center">
            <div className="text-purple-600 font-bold">{seg.metrics.product_clicks}</div>
            <div className="text-gray-500">{window.__t('clickCount')}</div>
          </div>
        )}
        {seg.metrics?.comment_count > 0 && (
          <div className="bg-cyan-50 rounded-lg p-2 text-center">
            <div className="text-cyan-600 font-bold">{seg.metrics.comment_count}</div>
            <div className="text-gray-500">{window.__t('live_comments')}</div>
          </div>
        )}
      </div>

      {/* Reproducible points */}
      {seg.reproducible_points && seg.reproducible_points.length > 0 && (
        <div className="mt-3 pt-2 border-t border-emerald-100">
          <div className="text-xs font-semibold text-emerald-600 mb-1">
            {window.__t('liveReportReproduciblePoints')}
          </div>
          <ul className="text-xs text-gray-600 space-y-0.5">
            {seg.reproducible_points.map((pt, i) => (
              <li key={i} className="flex items-start gap-1">
                <span className="text-emerald-400 mt-0.5">&#10003;</span>
                <span>{pt}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );

  const renderWeakSegment = (seg, idx) => (
    <div
      key={seg.phase_index}
      className="bg-white border border-red-200 rounded-xl p-4 shadow-sm hover:shadow-md transition-shadow"
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold text-red-500">#{idx + 1}</span>
          <span className="text-sm font-semibold text-gray-800">
            {seg.time_range_display}
          </span>
        </div>
        {renderScoreBar(seg.score)}
      </div>

      {/* Interpretation */}
      <p className="text-sm text-gray-700 leading-relaxed mb-2">
        {seg.interpretation}
      </p>

      {/* Signal badges (or lack thereof) */}
      {seg.signals && seg.signals.length > 0
        ? renderSignalBadges(seg.signals)
        : (
          <div className="flex flex-wrap gap-1 mt-1">
            <span className="text-xs px-1.5 py-0.5 rounded-full font-medium bg-gray-100 text-gray-500">
              {window.__t('liveReportNoSignal')}
            </span>
          </div>
        )}

      {/* Cut points */}
      {seg.cut_points && seg.cut_points.length > 0 && (
        <div className="mt-3 pt-2 border-t border-red-100">
          <div className="text-xs font-semibold text-red-500 mb-1">
            {window.__t('liveReportCutPoints')}
          </div>
          <ul className="text-xs text-gray-600 space-y-0.5">
            {seg.cut_points.map((pt, i) => (
              <li key={i} className="flex items-start gap-1">
                <span className="text-red-400 mt-0.5">&#10007;</span>
                <span>{pt}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );

  const renderSuggestion = (sug, idx) => {
    const priorityColor = PRIORITY_COLORS[sug.priority] || PRIORITY_COLORS.medium;
    const priorityLabel = PRIORITY_LABELS[sug.priority] || sug.priority;
    return (
      <div
        key={idx}
        className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm hover:shadow-md transition-shadow"
      >
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center">
            <span className="text-indigo-600 font-bold text-sm">{idx + 1}</span>
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm font-semibold text-gray-800">{sug.category}</span>
              <span
                className={`text-xs px-1.5 py-0.5 rounded-full font-medium border ${priorityColor}`}
              >
                {priorityLabel}
              </span>
            </div>
            <p className="text-sm text-gray-700 leading-relaxed">{sug.suggestion}</p>
          </div>
        </div>
      </div>
    );
  };

  // ── Summary metrics ──
  const renderSummary = () => {
    if (!report?.summary_metrics) return null;
    const m = report.summary_metrics;
    return (
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <div className="bg-white rounded-xl p-3 border border-gray-200 text-center">
          <div className="text-2xl font-bold text-gray-800">{m.total_phases || 0}</div>
          <div className="text-xs text-gray-500">{window.__t('liveReportAnalysisPhase')}</div>
        </div>
        <div className="bg-white rounded-xl p-3 border border-gray-200 text-center">
          <div className="text-2xl font-bold text-emerald-600">
            {m.total_gmv > 0 ? `¥${m.total_gmv.toLocaleString()}` : "-"}
          </div>
          <div className="text-xs text-gray-500">{window.__t('analytics_totalSales', '総売上')}</div>
        </div>
        <div className="bg-white rounded-xl p-3 border border-gray-200 text-center">
          <div className="text-2xl font-bold text-blue-600">{m.viewer_peak || "-"}</div>
          <div className="text-xs text-gray-500">
            {window.__t('liveReportViewerPeak')}{m.viewer_peak_time ? ` (${m.viewer_peak_time})` : ""}
          </div>
        </div>
        <div className="bg-white rounded-xl p-3 border border-gray-200 text-center">
          <div className="text-2xl font-bold text-purple-600">
            {m.strong_count || 0} / {m.weak_count || 0}
          </div>
          <div className="text-xs text-gray-500">{window.__t('liveReportStrongWeakPhase')}</div>
        </div>
      </div>
    );
  };

  const isLoading = fetchState === "loading";

  // ── Main render ──
  return (
    <div className="mt-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-bold text-gray-800">Live Report</h2>
          {version > 0 && (
            <span className="text-xs text-gray-400">v{version}</span>
          )}
        </div>
        <button
          onClick={handleGenerate}
          disabled={generating || isLoading}
          className={`px-4 py-2 rounded-lg text-sm font-semibold transition-all ${
            generating
              ? "bg-gray-200 text-gray-500 cursor-not-allowed"
              : "bg-indigo-600 text-white hover:bg-indigo-700 active:bg-indigo-800 shadow-sm"
          }`}
        >
          {generating ? (
            <span className="flex items-center gap-2">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              {window.__t('autoVideo_generating')}
            </span>
          ) : report ? window.__t('clip_regenerate') : window.__t('liveReportGenerate')}
        </button>
      </div>

      {/* Fetch Error - SectionStateUI統一 */}
      {fetchState === "error" && (
        <div className="mb-4">
          <ErrorState error={fetchError} onRetry={retry} sectionName="Live Report" compact />
        </div>
      )}

      {/* Generation Error */}
      {genError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-sm text-red-700">
          {genError}
        </div>
      )}

      {/* Loading */}
      {isLoading && !report && (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-3 border-purple-200 border-t-purple-600 rounded-full animate-spin" />
        </div>
      )}

      {/* No report yet (empty or not_found) */}
      {(fetchState === "empty" || (fetchState === "success" && !report)) && !isLoading && (
        <div className="bg-gray-50 rounded-xl p-8 text-center border border-dashed border-gray-300">
          <div className="text-gray-400 text-4xl mb-3">&#128202;</div>
          <p className="text-gray-500 text-sm">
            {window.__t('liveReportNotGeneratedYet')}<br />
            {window.__t('liveReportClickToStart')}
          </p>
        </div>
      )}

      {/* Report content */}
      {report && (
        <div>
          {/* Summary */}
          {renderSummary()}

          {/* Strong Segments TOP 3 */}
          <div className="mb-6">
            <h3 className="text-base font-bold text-emerald-700 mb-3 flex items-center gap-2">
              <span className="w-6 h-6 rounded-full bg-emerald-100 flex items-center justify-center text-emerald-600 text-xs font-bold">
                &#9650;
              </span>
              {window.__t('liveReportStrongSegmentsTop')} {report.strong_segments?.length || 0}
            </h3>
            <div className="grid gap-3">
              {report.strong_segments?.map((seg, idx) => renderStrongSegment(seg, idx))}
              {(!report.strong_segments || report.strong_segments.length === 0) && (
                <div className="text-sm text-gray-400 p-4 text-center bg-gray-50 rounded-lg">
                  {window.__t('liveReportNoStrongSegments')}
                </div>
              )}
            </div>
          </div>

          {/* Weak Segments TOP 3 */}
          <div className="mb-6">
            <h3 className="text-base font-bold text-red-600 mb-3 flex items-center gap-2">
              <span className="w-6 h-6 rounded-full bg-red-100 flex items-center justify-center text-red-500 text-xs font-bold">
                &#9660;
              </span>
              {window.__t('liveReportWeakSegmentsTop')} {report.weak_segments?.length || 0}
            </h3>
            <div className="grid gap-3">
              {report.weak_segments?.map((seg, idx) => renderWeakSegment(seg, idx))}
              {(!report.weak_segments || report.weak_segments.length === 0) && (
                <div className="text-sm text-gray-400 p-4 text-center bg-gray-50 rounded-lg">
                  {window.__t('liveReportNoWeakSegments')}
                </div>
              )}
            </div>
          </div>

          {/* Improvement Suggestions */}
          <div className="mb-6">
            <h3 className="text-base font-bold text-indigo-700 mb-3 flex items-center gap-2">
              <span className="w-6 h-6 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600 text-xs font-bold">
                &#9733;
              </span>
              {window.__t('liveReportImprovementSuggestions')}
            </h3>
            <div className="grid gap-3">
              {report.suggestions?.map((sug, idx) => renderSuggestion(sug, idx))}
              {(!report.suggestions || report.suggestions.length === 0) && (
                <div className="text-sm text-gray-400 p-4 text-center bg-gray-50 rounded-lg">
                  {window.__t('liveReportImprovementSuggestions')}がありません
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
