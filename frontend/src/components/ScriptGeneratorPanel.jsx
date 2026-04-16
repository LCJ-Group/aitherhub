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
    { value: "professional_friendly", label: window.__t('scriptGen_toneProfessional') },
    { value: "energetic", label: window.__t('scriptGen_toneEnergetic') },
    { value: "calm_expert", label: window.__t('scriptGen_toneCalmExpert') },
    { value: "casual", label: window.__t('scriptGen_toneCasual') },
  ];

  const languageOptions = [
    { value: "ja", label: window.__t('language_japanese') },
    { value: "zh", label: window.__t('scriptGen_langZh') },
    { value: "en", label: window.__t('language_english') },
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
      setError(err?.message || window.__t('scriptGen_generateFailed'));
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

  // Helper: format seconds to "X分Y秒"
  const formatDuration = (sec) => {
    if (!sec && sec !== 0) return "—";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return m > 0 ? `${m}${window.__t('minuteUnit')}${s}${window.__t('seconds')}` : `${s}${window.__t('seconds')}`;
  };

  // Helper: truncate CTA pre_talk to a short phrase
  const extractCTAPhrase = (cta) => {
    const text = cta.pre_talk || "";
    // Take first 60 chars as a representative phrase
    return text.length > 60 ? text.slice(0, 60) + "..." : text;
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
              <div className="text-gray-900 text-lg font-semibold">{window.__t('scriptGen_panelTitle')}</div>
              <div className="text-gray-500 text-sm mt-0.5">{window.__t('scriptGen_panelSubtitle')}</div>
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
                {window.__t('scriptGen_analyzingPatterns')}
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
                    <span className="text-xs font-semibold text-gray-700">{window.__t('scriptGen_ctaTitle')}</span>
                    {patterns.cta_phrases?.length > 0 && (
                      <span className="text-[10px] text-orange-500 font-medium">{patterns.cta_phrases.length}{window.__t('scriptGen_itemsSuffix')}</span>
                    )}
                  </div>
                  {patterns.cta_phrases?.length > 0 ? (
                    <div className="space-y-1">
                      {patterns.cta_phrases.slice(0, 5).map((cta, i) => (
                        <div key={i} className="text-xs text-gray-600 bg-orange-50 rounded-lg px-2 py-1.5 leading-relaxed">
                          <span className="font-medium text-orange-700">"{extractCTAPhrase(cta)}"</span>
                          <div className="flex items-center gap-1 mt-0.5">
                            <span className="text-[10px] text-orange-500">
                              {cta.moment_type === "order" ? window.__t('scriptGen_order') : window.__t('scriptGen_click')}
                            </span>
                            {cta.confidence && (
                              <span className="text-[10px] text-gray-400">
                                {window.__t('scriptGen_confidenceLabel')}{(cta.confidence * 100).toFixed(0)}%
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400">{window.__t('scriptGen_noData')}</p>
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
                    <span className="text-xs font-semibold text-gray-700">{window.__t('scriptGen_productDurationTitle')}</span>
                    {patterns.product_durations?.length > 0 && (
                      <span className="text-[10px] text-green-500 font-medium">{patterns.product_durations.length}{window.__t('scriptGen_itemsSuffix')}</span>
                    )}
                  </div>
                  {patterns.product_durations?.length > 0 ? (
                    <div className="space-y-1">
                      {patterns.product_durations.slice(0, 5).map((pd, i) => (
                        <div key={i} className="text-xs text-gray-600 bg-green-50 rounded-lg px-2 py-1.5">
                          <span className="font-medium text-green-700">
                            {(pd.product_name || "").length > 25
                              ? pd.product_name.slice(0, 25) + "..."
                              : pd.product_name}
                          </span>
                          <div className="flex items-center gap-1 mt-0.5">
                            <span className="text-[10px] text-green-500">
                              {formatDuration(pd.total_exposure_sec)}
                            </span>
                            {pd.had_sales && (
                              <span className="text-[10px] text-green-600 font-semibold">{window.__t('scriptGen_hasSales')}</span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400">{window.__t('scriptGen_noData')}</p>
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
                    <span className="text-xs font-semibold text-gray-700">{window.__t('scriptGen_topPhasesTitle')}</span>
                    {patterns.top_phases?.length > 0 && (
                      <span className="text-[10px] text-purple-500 font-medium">{patterns.top_phases.length}{window.__t('scriptGen_itemsSuffix')}</span>
                    )}
                  </div>
                  {patterns.top_phases?.length > 0 ? (
                    <div className="space-y-1">
                      {patterns.top_phases.slice(0, 5).map((phase, i) => (
                        <div key={i} className="text-xs text-gray-600 bg-purple-50 rounded-lg px-2 py-1.5">
                          <span className="font-medium text-purple-700">
                            {(phase.phase_description || "").length > 40
                              ? phase.phase_description.slice(0, 40) + "..."
                              : phase.phase_description || `Phase ${phase.phase_index}`}
                          </span>
                          <div className="flex items-center gap-1 mt-0.5">
                            {phase.composite_score != null && (
                              <span className="text-[10px] text-purple-500">
                                {window.__t('scriptGen_scoreLabel')}{phase.composite_score.toFixed(1)}
                              </span>
                            )}
                            {phase.gmv != null && phase.gmv > 0 && (
                              <span className="text-[10px] text-purple-400">
                                GMV: ¥{phase.gmv.toLocaleString()}
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400">{window.__t('scriptGen_noData')}</p>
                  )}
                </div>
              </div>
            )}

            {/* Generation Form */}
            <div className="rounded-xl bg-white/80 border border-indigo-100 p-4 space-y-4">
              <h4 className="text-sm font-semibold text-gray-800">{window.__t('scriptGen_scriptSettingsTitle')}</h4>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Product Focus */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">{window.__t('scriptGen_productFocusLabel')}</label>
                  <input
                    type="text"
                    value={productFocus}
                    onChange={(e) => setProductFocus(e.target.value)}
                    placeholder={window.__t('scriptGen_productFocusPlaceholder')}
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-300 focus:border-indigo-400 outline-none transition-all bg-white"
                  />
                </div>

                {/* Duration */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">{window.__t('scriptGen_scriptDurationLabel')}</label>
                  <select
                    value={durationMinutes}
                    onChange={(e) => setDurationMinutes(Number(e.target.value))}
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-300 focus:border-indigo-400 outline-none transition-all bg-white"
                  >
                    <option value={5}>5{window.__t('scriptGen_minutesSuffix')}</option>
                    <option value={10}>10{window.__t('scriptGen_minutesSuffix')}</option>
                    <option value={15}>15{window.__t('scriptGen_minutesSuffix')}</option>
                    <option value={20}>20{window.__t('scriptGen_minutesSuffix')}</option>
                    <option value={30}>30{window.__t('scriptGen_minutesSuffix')}</option>
                    <option value={60}>60{window.__t('scriptGen_minutesSuffix')}</option>
                  </select>
                </div>

                {/* Tone */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">{window.__t('scriptGen_toneLabel')}</label>
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
                  <label className="block text-xs font-medium text-gray-600 mb-1">{window.__t('scriptGen_languageLabel')}</label>
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
                <span className="text-xs text-gray-600">{window.__t('scriptGen_crossVideoLabel')}</span>
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
                    {window.__t('scriptGen_generatingScript')}
                  </>
                ) : (
                  <>
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 3v18"/>
                      <path d="M5.5 8.5L12 3l6.5 5.5"/>
                    </svg>
                    {window.__t('scriptGen_generateScriptBtn')}
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
                    <h4 className="text-sm font-semibold text-gray-800">{window.__t('scriptGen_generatedScriptTitle')}</h4>
                    <span className="px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 text-[10px] font-medium">
                      {script.char_count?.toLocaleString()}{window.__t('scriptGen_characters')}
                    </span>
                    <span className="px-2 py-0.5 rounded-full bg-purple-100 text-purple-700 text-[10px] font-medium">
                      {window.__t('scriptGen_approximately')}{script.estimated_duration_minutes}{window.__t('scriptGen_minutesSuffix')}
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
                        {window.__t('scriptGen_copied')}
                      </>
                    ) : (
                      <>
                        <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                        </svg>
                        {window.__t('scriptGen_copy')}
                      </>
                    )}
                  </button>
                </div>

                {/* Data Sources Badge — uses patterns_used from backend */}
                {script.patterns_used && (
                  <div className="flex flex-wrap gap-1.5">
                    {script.patterns_used.cta_phrases_found > 0 && (
                      <span className="px-2 py-0.5 rounded-full bg-orange-50 text-orange-600 text-[10px] font-medium border border-orange-100">
                        CTA {script.patterns_used.cta_phrases_found}{window.__t('scriptGen_itemsSuffix')}
                      </span>
                    )}
                    {script.patterns_used.top_phases_used > 0 && (
                      <span className="px-2 py-0.5 rounded-full bg-purple-50 text-purple-600 text-[10px] font-medium border border-purple-100">
                        {window.__t('scriptGen_topPhasesTitle')} {script.patterns_used.top_phases_used}{window.__t('scriptGen_itemsSuffix')}
                      </span>
                    )}
                    {script.patterns_used.products_analyzed > 0 && (
                      <span className="px-2 py-0.5 rounded-full bg-green-50 text-green-600 text-[10px] font-medium border border-green-100">
                        {window.__t('scriptGen_productAnalysisLabel')}{script.patterns_used.products_analyzed}{window.__t('scriptGen_itemsSuffix')}
                      </span>
                    )}
                    {script.patterns_used.cross_video_patterns && (
                      <span className="px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 text-[10px] font-medium border border-blue-100">
                        {window.__t('scriptGen_crossVideoAnalysisLabel')}{script.patterns_used.videos_in_cross_analysis}{window.__t('scriptGen_videosCountSuffix')}
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
