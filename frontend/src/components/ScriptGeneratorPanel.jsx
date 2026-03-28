import { useState, useCallback } from "react";
import VideoService from "../base/services/videoService";

/**
 * ScriptGeneratorPanel — Data-driven script generation UI
 *
 * This component allows users to generate live commerce scripts
 * that are grounded in real performance data (CTA phrases, product
 * durations, top-performing phases) from their past livestreams.
 */
export default function ScriptGeneratorPanel({ videoId, videoData }) {
  const [isOpen, setIsOpen] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [script, setScript] = useState(null);
  const [patterns, setPatterns] = useState(null);
  const [patternsLoading, setPatternsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  // Form state
  const [productFocus, setProductFocus] = useState("");
  const [durationMinutes, setDurationMinutes] = useState(10);
  const [tone, setTone] = useState("professional_friendly");
  const [language, setLanguage] = useState("ja");
  const [crossVideo, setCrossVideo] = useState(true);

  const toneOptions = [
    { value: "professional_friendly", label: "プロフェッショナル（親しみやすい）" },
    { value: "energetic", label: "ハイテンション" },
    { value: "calm_expert", label: "落ち着いた専門家" },
    { value: "casual", label: "カジュアル" },
  ];

  const languageOptions = [
    { value: "ja", label: "日本語" },
    { value: "zh", label: "中文" },
    { value: "en", label: "English" },
  ];

  // Load winning patterns when panel opens
  const loadPatterns = useCallback(async () => {
    if (patterns || patternsLoading) return;
    setPatternsLoading(true);
    try {
      const data = await VideoService.getWinningPatterns(videoId);
      setPatterns(data);
    } catch (err) {
      console.warn("Failed to load winning patterns:", err);
    } finally {
      setPatternsLoading(false);
    }
  }, [videoId, patterns, patternsLoading]);

  const handleToggle = () => {
    const next = !isOpen;
    setIsOpen(next);
    if (next) loadPatterns();
  };

  const handleGenerate = async () => {
    setIsGenerating(true);
    setError(null);
    setScript(null);
    try {
      const result = await VideoService.generateScript(videoId, {
        product_focus: productFocus || null,
        tone,
        language,
        duration_minutes: durationMinutes,
        cross_video: crossVideo,
      });
      setScript(result);
    } catch (err) {
      setError(err?.message || "台本の生成に失敗しました");
    } finally {
      setIsGenerating(false);
    }
  };

  const handleCopy = () => {
    if (!script?.script) return;
    navigator.clipboard.writeText(script.script).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="w-full mt-4 mx-auto">
      <div className="rounded-2xl bg-gradient-to-br from-indigo-50 to-purple-50 border border-indigo-200/60 shadow-sm">
        {/* Header */}
        <div
          onClick={handleToggle}
          className="flex items-center justify-between p-5 cursor-pointer hover:bg-indigo-100/40 transition-all duration-200 rounded-t-2xl"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-sm">
              <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
                <polyline points="10 9 9 9 8 9"/>
              </svg>
            </div>
            <div>
              <div className="text-gray-900 text-lg font-semibold">売れる台本を生成</div>
              <div className="text-gray-500 text-sm mt-0.5">実績データに基づいたライブコマース台本</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="px-2.5 py-1 rounded-full bg-indigo-100 text-indigo-700 text-[11px] font-semibold tracking-wide">
              DATA-DRIVEN
            </span>
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className={`w-5 h-5 text-gray-400 transition-transform duration-200 ${isOpen ? "rotate-180" : ""}`}
              viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            >
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
        </div>

        {/* Expandable Content */}
        {isOpen && (
          <div className="px-5 pb-5 space-y-5">
            {/* Winning Patterns Summary */}
            {patternsLoading && (
              <div className="flex items-center gap-2 text-sm text-indigo-600">
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
                勝ちパターンを分析中...
              </div>
            )}

            {patterns && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {/* CTA Phrases */}
                <div className="rounded-xl bg-white/80 border border-indigo-100 p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-6 h-6 rounded-lg bg-orange-100 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-orange-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                      </svg>
                    </div>
                    <span className="text-xs font-semibold text-gray-700">CTA（行動喚起）</span>
                  </div>
                  {patterns.cta_phrases?.length > 0 ? (
                    <div className="space-y-1">
                      {patterns.cta_phrases.slice(0, 5).map((cta, i) => (
                        <div key={i} className="text-xs text-gray-600 bg-orange-50 rounded-lg px-2 py-1.5 leading-relaxed">
                          <span className="font-medium text-orange-700">"{cta.phrase}"</span>
                          {cta.impact_score && (
                            <span className="ml-1 text-[10px] text-orange-500">
                              (効果: {(cta.impact_score * 100).toFixed(0)}%)
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400">データなし</p>
                  )}
                </div>

                {/* Product Durations */}
                <div className="rounded-xl bg-white/80 border border-indigo-100 p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-6 h-6 rounded-lg bg-green-100 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-green-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
                      </svg>
                    </div>
                    <span className="text-xs font-semibold text-gray-700">商品説明の最適時間</span>
                  </div>
                  {patterns.product_durations?.length > 0 ? (
                    <div className="space-y-1">
                      {patterns.product_durations.slice(0, 5).map((pd, i) => (
                        <div key={i} className="text-xs text-gray-600 bg-green-50 rounded-lg px-2 py-1.5">
                          <span className="font-medium text-green-700">{pd.product_name}</span>
                          <span className="ml-1 text-[10px] text-green-500">
                            {Math.floor(pd.total_seconds / 60)}分{Math.floor(pd.total_seconds % 60)}秒
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400">データなし</p>
                  )}
                </div>

                {/* Top Phases */}
                <div className="rounded-xl bg-white/80 border border-indigo-100 p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-6 h-6 rounded-lg bg-purple-100 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-purple-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
                      </svg>
                    </div>
                    <span className="text-xs font-semibold text-gray-700">高パフォーマンスフェーズ</span>
                  </div>
                  {patterns.top_phases?.length > 0 ? (
                    <div className="space-y-1">
                      {patterns.top_phases.slice(0, 5).map((phase, i) => (
                        <div key={i} className="text-xs text-gray-600 bg-purple-50 rounded-lg px-2 py-1.5">
                          <span className="font-medium text-purple-700">
                            {phase.description?.slice(0, 40) || `Phase ${phase.phase_index}`}
                          </span>
                          {phase.score && (
                            <span className="ml-1 text-[10px] text-purple-500">
                              (スコア: {phase.score.toFixed(1)})
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400">データなし</p>
                  )}
                </div>
              </div>
            )}

            {/* Generation Form */}
            <div className="rounded-xl bg-white/80 border border-indigo-100 p-4 space-y-4">
              <h4 className="text-sm font-semibold text-gray-800">台本設定</h4>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Product Focus */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">注力商品（任意）</label>
                  <input
                    type="text"
                    value={productFocus}
                    onChange={(e) => setProductFocus(e.target.value)}
                    placeholder="例: KYOGOKU シグネチャーシャンプー"
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-300 focus:border-indigo-400 outline-none transition-all"
                  />
                </div>

                {/* Duration */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">台本の長さ</label>
                  <select
                    value={durationMinutes}
                    onChange={(e) => setDurationMinutes(Number(e.target.value))}
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-300 focus:border-indigo-400 outline-none transition-all bg-white"
                  >
                    <option value={5}>5分</option>
                    <option value={10}>10分</option>
                    <option value={15}>15分</option>
                    <option value={20}>20分</option>
                    <option value={30}>30分</option>
                    <option value={60}>60分</option>
                  </select>
                </div>

                {/* Tone */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">トーン</label>
                  <select
                    value={tone}
                    onChange={(e) => setTone(e.target.value)}
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-300 focus:border-indigo-400 outline-none transition-all bg-white"
                  >
                    {toneOptions.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>

                {/* Language */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">言語</label>
                  <select
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-300 focus:border-indigo-400 outline-none transition-all bg-white"
                  >
                    {languageOptions.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Cross-video toggle */}
              <div className="flex items-center gap-3">
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={crossVideo}
                    onChange={(e) => setCrossVideo(e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-indigo-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600"></div>
                </label>
                <span className="text-xs text-gray-600">他の配信の勝ちパターンも参考にする</span>
              </div>

              {/* Generate Button */}
              <button
                onClick={handleGenerate}
                disabled={isGenerating}
                className={`w-full py-3 px-4 rounded-xl text-sm font-semibold transition-all duration-200 flex items-center justify-center gap-2 ${
                  isGenerating
                    ? "bg-gray-200 text-gray-500 cursor-not-allowed"
                    : "bg-gradient-to-r from-indigo-600 to-purple-600 text-white hover:from-indigo-700 hover:to-purple-700 shadow-md hover:shadow-lg"
                }`}
              >
                {isGenerating ? (
                  <>
                    <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                    </svg>
                    実績データを分析して台本を生成中...
                  </>
                ) : (
                  <>
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 3v18"/>
                      <path d="M5.5 8.5L12 3l6.5 5.5"/>
                    </svg>
                    台本を生成する
                  </>
                )}
              </button>
            </div>

            {/* Error */}
            {error && (
              <div className="rounded-xl bg-red-50 border border-red-200 p-4">
                <p className="text-sm text-red-700">{error}</p>
              </div>
            )}

            {/* Generated Script */}
            {script && (
              <div className="rounded-xl bg-white border border-indigo-200 p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <h4 className="text-sm font-semibold text-gray-800">生成された台本</h4>
                    <span className="px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 text-[10px] font-medium">
                      {script.char_count?.toLocaleString()}文字
                    </span>
                    <span className="px-2 py-0.5 rounded-full bg-purple-100 text-purple-700 text-[10px] font-medium">
                      約{script.estimated_duration_minutes}分
                    </span>
                  </div>
                  <button
                    onClick={handleCopy}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-100 hover:bg-gray-200 text-gray-700 transition-all flex items-center gap-1"
                  >
                    {copied ? (
                      <>
                        <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-green-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12"/>
                        </svg>
                        コピー済み
                      </>
                    ) : (
                      <>
                        <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                        </svg>
                        コピー
                      </>
                    )}
                  </button>
                </div>

                {/* Data Sources Badge */}
                {script.data_sources && (
                  <div className="flex flex-wrap gap-1.5">
                    {script.data_sources.cta_phrases_count > 0 && (
                      <span className="px-2 py-0.5 rounded-full bg-orange-50 text-orange-600 text-[10px] font-medium border border-orange-100">
                        CTA {script.data_sources.cta_phrases_count}件
                      </span>
                    )}
                    {script.data_sources.top_phases_count > 0 && (
                      <span className="px-2 py-0.5 rounded-full bg-purple-50 text-purple-600 text-[10px] font-medium border border-purple-100">
                        高パフォーマンス {script.data_sources.top_phases_count}件
                      </span>
                    )}
                    {script.data_sources.product_durations_count > 0 && (
                      <span className="px-2 py-0.5 rounded-full bg-green-50 text-green-600 text-[10px] font-medium border border-green-100">
                        商品分析 {script.data_sources.product_durations_count}件
                      </span>
                    )}
                    {script.data_sources.cross_video_patterns && (
                      <span className="px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 text-[10px] font-medium border border-blue-100">
                        クロス配信分析
                      </span>
                    )}
                  </div>
                )}

                {/* Script Content */}
                <div className="bg-gray-50 rounded-xl p-4 max-h-[500px] overflow-y-auto scrollbar-custom">
                  <pre className="text-sm text-gray-800 whitespace-pre-wrap font-sans leading-relaxed">
                    {script.script}
                  </pre>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
