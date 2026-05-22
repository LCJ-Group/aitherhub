import { useState, useEffect, useCallback, useRef } from "react";
import axios from "axios";
import CaptionOverlayPlayer from "./CaptionOverlayPlayer";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

// ── Styles ──
const SUBTITLE_STYLES = [
  { id: "auto", name: "自動（シーン別）", desc: "シーンに応じてスタイルを自動切替" },
  { id: "simple", name: "シンプル", desc: "白文字＋影" },
  { id: "box", name: "ボックス", desc: "白文字＋黒背景" },
  { id: "outline", name: "アウトライン", desc: "白文字＋太い縁取り" },
  { id: "pop", name: "ポップ", desc: "黄色文字＋オレンジ縁取り" },
  { id: "gradient", name: "グラデーション", desc: "白文字＋紫グラデ背景" },
  { id: "karaoke", name: "カラオケ", desc: "ハイライト付き" },
];

const TRANSITIONS = [
  { id: "fade", name: "フェード" },
  { id: "fadeblack", name: "フェード（黒）" },
  { id: "fadewhite", name: "フェード（白）" },
  { id: "slideleft", name: "スライド左" },
  { id: "slideright", name: "スライド右" },
  { id: "wipeleft", name: "ワイプ左" },
  { id: "wiperight", name: "ワイプ右" },
  { id: "smoothleft", name: "スムーズ左" },
  { id: "smoothright", name: "スムーズ右" },
];

export default function AutoAIClipPanel({ adminKey }) {
  // ── State ──
  const [brands, setBrands] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [activeTab, setActiveTab] = useState("generate"); // generate, jobs, completed, candidates
  const [jobFilter, setJobFilter] = useState("all"); // all, done, error
  const [completedClips, setCompletedClips] = useState([]);
  const [completedLoading, setCompletedLoading] = useState(false);
  const [completedTotal, setCompletedTotal] = useState(0);

  // ── Config (V2: new effect options) ──
  const [config, setConfig] = useState({
    brand_id: "",
    max_clips: 5,
    subtitle_style: "auto",
    enable_sfx: true,
    enable_transitions: true,
    transition_type: "fade",
    transition_duration: 0.5,
    enable_hook: true,
    hook_text: "",
    enable_thumbnail: true,
    min_duration: 10,
    max_duration: 60,
    min_cta_score: 0,
    min_importance: 0,
    target_language: "auto",
    position_y: 75,
    // V2 new options
    enable_silence_cut: true,
    enable_zoom_pulse: true,
    enable_progress_bar: true,
    enable_flash_intro: true,
    enable_loop_fade: true,
    enable_cta: true,
    enable_keyword_highlight: true,
    enable_subtitle_animation: true,
    zoom_intensity: 1.08,
    silence_threshold_db: -30,
  });

  // ── Polling for active job ──
  const [activeJobId, setActiveJobId] = useState(null);
  const [activeJob, setActiveJob] = useState(null);

  // ── Preview state ──
  const [previewUrl, setPreviewUrl] = useState(null); // currently playing video URL
  const [previewClipId, setPreviewClipId] = useState(null);

  // ── Caption editing state ──
  const [editingJobId, setEditingJobId] = useState(null);
  const [editingClipId, setEditingClipId] = useState(null);
  const [editCaptions, setEditCaptions] = useState([]);
  const [editHook, setEditHook] = useState("");
  const [editCta, setEditCta] = useState("");
  const [captionLoading, setCaptionLoading] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const headers = { "X-Admin-Key": adminKey };

  // ── Fetch initial data ──
  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [brandsRes, jobsRes, templatesRes, completedRes] = await Promise.all([
        axios.get(`${API_BASE}/api/v1/ai-clip/brands`, { headers }).catch(() => ({ data: { brands: [] } })),
        axios.get(`${API_BASE}/api/v1/ai-clip/jobs?limit=20`, { headers }).catch(() => ({ data: { jobs: [] } })),
        axios.get(`${API_BASE}/api/v1/ai-clip/templates`, { headers }).catch(() => ({ data: { templates: [] } })),
        axios.get(`${API_BASE}/api/v1/ai-clip/completed-clips?limit=1`, { headers }).catch(() => ({ data: { total: 0 } })),
      ]);
      setBrands(brandsRes.data.brands || []);
      setJobs(jobsRes.data.jobs || []);
      setTemplates(templatesRes.data.templates || []);
      setCompletedTotal(completedRes.data.total || 0);
    } catch (e) {
      console.error("AI Clip data fetch error:", e);
    } finally {
      setLoading(false);
    }
  }, [adminKey]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // ── Fetch candidates ──
  const fetchCandidates = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (config.brand_id) params.append("brand_id", config.brand_id);
      params.append("min_cta_score", config.min_cta_score);
      params.append("min_importance", config.min_importance);
      params.append("min_duration", config.min_duration);
      params.append("max_duration", config.max_duration);
      params.append("limit", "30");
      const res = await axios.get(`${API_BASE}/api/v1/ai-clip/candidates?${params}`, { headers });
      setCandidates(res.data.candidates || []);
    } catch (e) {
      console.error("Candidates fetch error:", e);
      setCandidates([]);
    }
  }, [config.brand_id, config.min_cta_score, config.min_importance, config.min_duration, config.max_duration, adminKey]);

  useEffect(() => {
    if (activeTab === "candidates") {
      fetchCandidates();
    }
  }, [activeTab, fetchCandidates]);

  // ── Fetch completed clips ──
  const fetchCompletedClips = useCallback(async () => {
    setCompletedLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/api/v1/ai-clip/completed-clips?limit=100`, { headers });
      setCompletedClips(res.data.clips || []);
      setCompletedTotal(res.data.total || 0);
    } catch (e) {
      console.error("Completed clips fetch error:", e);
      setCompletedClips([]);
    } finally {
      setCompletedLoading(false);
    }
  }, [adminKey]);

  useEffect(() => {
    if (activeTab === "completed") {
      fetchCompletedClips();
    }
  }, [activeTab, fetchCompletedClips]);

  // ── Poll active job with stall detection ──
  const [jobStalled, setJobStalled] = useState(false);
  const lastJobProgressRef = useRef({ pct: 0, time: Date.now() });
  useEffect(() => {
    if (!activeJobId) return;
    setJobStalled(false);
    lastJobProgressRef.current = { pct: 0, time: Date.now() };
    const interval = setInterval(async () => {
      try {
        const res = await axios.get(`${API_BASE}/api/v1/ai-clip/jobs/${activeJobId}`, { headers });
        setActiveJob(res.data);
        if (res.data.status === "done" || res.data.status === "failed") {
          clearInterval(interval);
          setJobStalled(false);
          fetchData(); // Refresh jobs list
        } else {
          const currentPct = res.data.progress_pct || 0;
          if (currentPct !== lastJobProgressRef.current.pct) {
            lastJobProgressRef.current = { pct: currentPct, time: Date.now() };
            setJobStalled(false);
          } else if (Date.now() - lastJobProgressRef.current.time > 5 * 60 * 1000) {
            setJobStalled(true);
          }
        }
      } catch (e) {
        console.error("Job poll error:", e);
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [activeJobId]);

  // ── Generate ──
  const handleGenerate = async () => {
    if (generating) return;
    setGenerating(true);
    try {
      const payload = { ...config };
      if (!payload.brand_id) delete payload.brand_id;
      if (!payload.hook_text) delete payload.hook_text;
      const res = await axios.post(`${API_BASE}/api/v1/ai-clip/generate`, payload, { headers });
      setActiveJobId(res.data.job_id);
      setActiveJob({ job_id: res.data.job_id, status: "queued", progress_pct: 0 });
      setActiveTab("jobs");
    } catch (e) {
      alert("生成開始に失敗しました: " + (e.response?.data?.detail || e.message));
    } finally {
      setGenerating(false);
    }
  };

  // ── Apply template (V2: support new fields) ──
  const applyTemplate = (template) => {
    const c = template.config || template;
    setConfig(prev => ({
      ...prev,
      subtitle_style: c.subtitle_style || prev.subtitle_style,
      enable_sfx: c.enable_sfx ?? prev.enable_sfx,
      enable_transitions: c.enable_transitions ?? prev.enable_transitions,
      transition_type: c.transition_type || prev.transition_type,
      enable_hook: c.enable_hook ?? prev.enable_hook,
      min_cta_score: c.min_cta_score ?? prev.min_cta_score,
      max_duration: c.max_duration ?? prev.max_duration,
      // V2 fields
      enable_silence_cut: c.enable_silence_cut ?? prev.enable_silence_cut,
      enable_zoom_pulse: c.enable_zoom_pulse ?? prev.enable_zoom_pulse,
      enable_progress_bar: c.enable_progress_bar ?? prev.enable_progress_bar,
      enable_flash_intro: c.enable_flash_intro ?? prev.enable_flash_intro,
      enable_loop_fade: c.enable_loop_fade ?? prev.enable_loop_fade,
      enable_cta: c.enable_cta ?? prev.enable_cta,
      enable_keyword_highlight: c.enable_keyword_highlight ?? prev.enable_keyword_highlight,
      enable_subtitle_animation: c.enable_subtitle_animation ?? prev.enable_subtitle_animation,
      zoom_intensity: c.zoom_intensity ?? prev.zoom_intensity,
    }));
  };

  // ── Caption editing handlers ──
  const openCaptionEditor = async (jobId, clipId, videoUrl) => {
    setCaptionLoading(true);
    setEditingJobId(jobId);
    setEditingClipId(clipId);
    if (videoUrl) setPreviewUrl(videoUrl);
    try {
      const res = await axios.get(`${API_BASE}/api/v1/ai-clip/jobs/${jobId}/captions`, { headers });
      setEditCaptions(res.data.captions || []);
      setEditHook(res.data.hook_text || "");
      setEditCta(res.data.cta_text || "");
      // Also try to get video URL from job details if not provided
      if (!videoUrl) {
        try {
          const jobRes = await axios.get(`${API_BASE}/api/v1/ai-clip/jobs/${jobId}`, { headers });
          const result = jobRes.data?.results?.find(r => r.clip_id === clipId);
          if (result?.download_url) setPreviewUrl(result.download_url);
        } catch (_) {}
      }
    } catch (e) {
      alert("字幕データの取得に失敗: " + (e.response?.data?.detail || e.message));
      setEditingJobId(null);
      setEditingClipId(null);
    } finally {
      setCaptionLoading(false);
    }
  };

  const saveCaptions = async () => {
    try {
      await axios.patch(
        `${API_BASE}/api/v1/ai-clip/jobs/${editingJobId}/captions`,
        { captions: editCaptions, hook_text: editHook, cta_text: editCta },
        { headers: { ...headers, "Content-Type": "application/json" } }
      );
      alert("字幕を保存しました");
    } catch (e) {
      alert("保存に失敗: " + (e.response?.data?.detail || e.message));
    }
  };

  const handleRegenerate = async () => {
    if (!confirm("修正した字幕で動画を再エンコードしますか？\n（2〜3分かかります）")) return;
    setRegenerating(true);
    try {
      // First save the captions
      await axios.patch(
        `${API_BASE}/api/v1/ai-clip/jobs/${editingJobId}/captions`,
        { captions: editCaptions, hook_text: editHook, cta_text: editCta },
        { headers: { ...headers, "Content-Type": "application/json" } }
      );
      // Then trigger regeneration
      const res = await axios.post(
        `${API_BASE}/api/v1/ai-clip/jobs/${editingJobId}/regenerate`,
        {},
        { headers }
      );
      alert("再エンコードを開始しました。完了まで2〜3分お待ちください。");
      setEditingJobId(null);
      setEditingClipId(null);
      // Start polling the new job
      if (res.data.job_id) {
        setActiveJobId(res.data.job_id);
        setActiveJob({ job_id: res.data.job_id, status: "processing", progress_pct: 0 });
        setActiveTab("jobs");
      }
    } catch (e) {
      alert("再エンコード開始に失敗: " + (e.response?.data?.detail || e.message));
    } finally {
      setRegenerating(false);
    }
  };

  const closeCaptionEditor = () => {
    setEditingJobId(null);
    setEditingClipId(null);
    setEditCaptions([]);
    setEditHook("");
    setEditCta("");
  };

  // ── Loading ──
  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-purple-500"></div>
        <span className="ml-3 text-gray-500">AI クリップ生成データを読み込み中...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2">
            🤖 全自動AIクリップ生成 <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">V2.17</span>
            <span className="relative group">
              <span className="cursor-help text-gray-400 hover:text-gray-600 text-sm">ℹ️</span>
              <span className="absolute left-0 top-6 z-50 hidden group-hover:block w-72 p-3 bg-gray-900 text-white text-xs rounded-lg shadow-lg leading-relaxed">
                <b>V2.17 字幕品質改善:</b><br/>
                ・字幕重畳防止（0.1秒ギャップ制御）<br/>
                ・長文自動フォント縮小＋自然改行<br/>
                ・キーワード強調（商品名=金色, CTA=緑, 数字=橙）<br/>
                ・助詞・句読点での自然な改行位置<br/>
                <br/>
                <b>設計思想:</b><br/>
                ・1行に収まる字幕で視認性UP<br/>
                ・重要ワードを色分けで目立たせる<br/>
                ・字幕切替時の重なりを完全排除
              </span>
            </span>
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            字幕・フック・CTA・ズームパルス・無音カット・進行バー・キーワードハイライトを全自動生成
          </p>
        </div>
        <button
          onClick={fetchData}
          className="px-3 py-1.5 text-sm bg-gray-100 hover:bg-gray-200 rounded-md transition-colors"
        >
          🔄 更新
        </button>
      </div>

      {/* Sub-tabs */}
      <div className="flex gap-2 border-b pb-2">
        {[
          { id: "generate", label: "⚡ 生成設定" },
          { id: "jobs", label: `📋 ジョブ一覧 (${jobs.length})` },
          { id: "completed", label: `🎬 完成動画 (${completedTotal})` },
          { id: "candidates", label: "🎯 候補プレビュー" },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 rounded-t-md text-sm font-medium transition-all ${
              activeTab === tab.id
                ? "bg-purple-50 text-purple-700 border-b-2 border-purple-500"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Generate Tab */}
      {activeTab === "generate" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left: Config */}
          <div className="lg:col-span-2 space-y-6">
            {/* Templates */}
            <section className="bg-white rounded-lg border p-4">
              <h3 className="font-semibold text-gray-700 mb-3">📋 テンプレート</h3>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {templates.map(t => (
                  <button
                    key={t.id}
                    onClick={() => applyTemplate(t)}
                    className="p-3 border rounded-lg hover:border-purple-400 hover:bg-purple-50 transition-all text-left"
                  >
                    <div className="font-medium text-sm text-gray-800">{t.name}</div>
                    <div className="text-xs text-gray-500 mt-1 line-clamp-2">{t.description}</div>
                  </button>
                ))}
              </div>
            </section>

            {/* Brand & Clip Selection */}
            <section className="bg-white rounded-lg border p-4">
              <h3 className="font-semibold text-gray-700 mb-3">🏷️ クリップ選定条件</h3>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">ブランド</label>
                  <select
                    value={config.brand_id}
                    onChange={e => setConfig(prev => ({ ...prev, brand_id: e.target.value }))}
                    className="w-full border rounded-md px-3 py-2 text-sm"
                  >
                    <option value="">全ブランド（割り当て済みのみ）</option>
                    {brands.map(b => (
                      <option key={b.client_id} value={b.client_id}>
                        {b.client_name || b.brand_name} ({b.clip_count}件)
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">最大生成数</label>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={config.max_clips}
                    onChange={e => setConfig(prev => ({ ...prev, max_clips: parseInt(e.target.value) || 5 }))}
                    className="w-full border rounded-md px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">最小クリップ長（秒）</label>
                  <input
                    type="number"
                    min={5}
                    max={120}
                    value={config.min_duration}
                    onChange={e => setConfig(prev => ({ ...prev, min_duration: parseFloat(e.target.value) || 10 }))}
                    className="w-full border rounded-md px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">最大クリップ長（秒）</label>
                  <input
                    type="number"
                    min={10}
                    max={180}
                    value={config.max_duration}
                    onChange={e => setConfig(prev => ({ ...prev, max_duration: parseFloat(e.target.value) || 60 }))}
                    className="w-full border rounded-md px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">最小CTAスコア</label>
                  <select
                    value={config.min_cta_score}
                    onChange={e => setConfig(prev => ({ ...prev, min_cta_score: parseInt(e.target.value) }))}
                    className="w-full border rounded-md px-3 py-2 text-sm"
                  >
                    {[0,1,2,3,4,5].map(v => <option key={v} value={v}>{v === 0 ? "指定なし" : `${v}以上`}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">最小重要度</label>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    step={0.1}
                    value={config.min_importance}
                    onChange={e => setConfig(prev => ({ ...prev, min_importance: parseFloat(e.target.value) || 0 }))}
                    className="w-full border rounded-md px-3 py-2 text-sm"
                  />
                </div>
              </div>
            </section>

            {/* Subtitle & Style */}
            <section className="bg-white rounded-lg border p-4">
              <h3 className="font-semibold text-gray-700 mb-3">🎨 字幕スタイル</h3>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">スタイル</label>
                  <select
                    value={config.subtitle_style}
                    onChange={e => setConfig(prev => ({ ...prev, subtitle_style: e.target.value }))}
                    className="w-full border rounded-md px-3 py-2 text-sm"
                  >
                    {SUBTITLE_STYLES.map(s => (
                      <option key={s.id} value={s.id}>{s.name} - {s.desc}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">字幕言語</label>
                  <select
                    value={config.target_language}
                    onChange={e => setConfig(prev => ({ ...prev, target_language: e.target.value }))}
                    className="w-full border rounded-md px-3 py-2 text-sm"
                  >
                    <option value="auto">自動検出</option>
                    <option value="ja">日本語</option>
                    <option value="zh">中国語（簡体）</option>
                    <option value="zh-tw">中国語（繁体）</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">字幕位置 Y（%）</label>
                  <input
                    type="range"
                    min={10}
                    max={95}
                    value={config.position_y}
                    onChange={e => setConfig(prev => ({ ...prev, position_y: parseFloat(e.target.value) }))}
                    className="w-full"
                  />
                  <div className="text-xs text-gray-400 text-center">{config.position_y}%</div>
                </div>
              </div>
            </section>

            {/* V2: Advanced Effects */}
            <section className="bg-white rounded-lg border p-4">
              <h3 className="font-semibold text-gray-700 mb-3">🎬 V2 プロダクション・エフェクト</h3>
              <p className="text-xs text-gray-400 mb-4">プロ編集風の演出を自動適用します。TikTok/Reelsの視聴維持率・完視聴率を最大化。</p>

              <div className="grid grid-cols-2 gap-x-6 gap-y-3">
                {/* Silence Cut */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_silence_cut}
                    onChange={e => setConfig(prev => ({ ...prev, enable_silence_cut: e.target.checked }))}
                    className="rounded text-purple-600"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-700">✂️ 無音カット</span>
                    <p className="text-xs text-gray-400">無音区間を自動検出してカット</p>
                  </div>
                </label>

                {/* Zoom Pulse */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_zoom_pulse}
                    onChange={e => setConfig(prev => ({ ...prev, enable_zoom_pulse: e.target.checked }))}
                    className="rounded text-purple-600"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-700">🔍 ズームパルス</span>
                    <p className="text-xs text-gray-400">強調ポイントで自動ズーム</p>
                  </div>
                </label>

                {/* Progress Bar */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_progress_bar}
                    onChange={e => setConfig(prev => ({ ...prev, enable_progress_bar: e.target.checked }))}
                    className="rounded text-purple-600"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-700">📊 進行バー</span>
                    <p className="text-xs text-gray-400">画面下部に白い進行バー表示</p>
                  </div>
                </label>

                {/* Flash Intro */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_flash_intro}
                    onChange={e => setConfig(prev => ({ ...prev, enable_flash_intro: e.target.checked }))}
                    className="rounded text-purple-600"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-700">⚡ フラッシュイントロ</span>
                    <p className="text-xs text-gray-400">最初0.5秒の明るさブースト</p>
                  </div>
                </label>

                {/* Loop Fade */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_loop_fade}
                    onChange={e => setConfig(prev => ({ ...prev, enable_loop_fade: e.target.checked }))}
                    className="rounded text-purple-600"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-700">🔄 ループフェード</span>
                    <p className="text-xs text-gray-400">最後1秒フェードアウト</p>
                  </div>
                </label>

                {/* CTA */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_cta}
                    onChange={e => setConfig(prev => ({ ...prev, enable_cta: e.target.checked }))}
                    className="rounded text-purple-600"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-700">📢 CTA自動生成</span>
                    <p className="text-xs text-gray-400">最後3秒に行動喚起テキスト</p>
                  </div>
                </label>

                {/* Keyword Highlight */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_keyword_highlight}
                    onChange={e => setConfig(prev => ({ ...prev, enable_keyword_highlight: e.target.checked }))}
                    className="rounded text-purple-600"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-700">🌟 キーワードハイライト</span>
                    <p className="text-xs text-gray-400">商品名・CTAワードを色分け</p>
                  </div>
                </label>

                {/* Subtitle Animation */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_subtitle_animation}
                    onChange={e => setConfig(prev => ({ ...prev, enable_subtitle_animation: e.target.checked }))}
                    className="rounded text-purple-600"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-700">✨ 字幕アニメーション</span>
                    <p className="text-xs text-gray-400">フェードイン・スケール演出</p>
                  </div>
                </label>
              </div>

              {/* Zoom Intensity Slider */}
              {config.enable_zoom_pulse && (
                <div className="mt-4 pt-3 border-t">
                  <label className="block text-sm font-medium text-gray-600 mb-1">
                    ズーム強度: {((config.zoom_intensity - 1) * 100).toFixed(0)}%
                  </label>
                  <input
                    type="range"
                    min={100}
                    max={130}
                    value={Math.round(config.zoom_intensity * 100)}
                    onChange={e => setConfig(prev => ({ ...prev, zoom_intensity: parseInt(e.target.value) / 100 }))}
                    className="w-full"
                  />
                  <div className="flex justify-between text-xs text-gray-400">
                    <span>控えめ (0%)</span>
                    <span>強め (30%)</span>
                  </div>
                </div>
              )}

              {/* Silence Threshold */}
              {config.enable_silence_cut && (
                <div className="mt-3 pt-3 border-t">
                  <label className="block text-sm font-medium text-gray-600 mb-1">
                    無音検出感度: {config.silence_threshold_db}dB
                  </label>
                  <input
                    type="range"
                    min={-60}
                    max={-10}
                    value={config.silence_threshold_db}
                    onChange={e => setConfig(prev => ({ ...prev, silence_threshold_db: parseInt(e.target.value) }))}
                    className="w-full"
                  />
                  <div className="flex justify-between text-xs text-gray-400">
                    <span>高感度 (-60dB)</span>
                    <span>低感度 (-10dB)</span>
                  </div>
                </div>
              )}
            </section>

            {/* Original Effects */}
            <section className="bg-white rounded-lg border p-4">
              <h3 className="font-semibold text-gray-700 mb-3">✨ 基本エフェクト</h3>
              <div className="grid grid-cols-2 gap-4">
                {/* Hook */}
                <div className="col-span-2">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={config.enable_hook}
                      onChange={e => setConfig(prev => ({ ...prev, enable_hook: e.target.checked }))}
                      className="rounded"
                    />
                    <span className="text-sm font-medium text-gray-700">🎯 最初3秒フック（大きな文字でインパクト）</span>
                  </label>
                  {config.enable_hook && (
                    <input
                      type="text"
                      placeholder="カスタムフックテキスト（空欄=AI自動生成）"
                      value={config.hook_text}
                      onChange={e => setConfig(prev => ({ ...prev, hook_text: e.target.value }))}
                      className="mt-2 w-full border rounded-md px-3 py-2 text-sm"
                      maxLength={30}
                    />
                  )}
                </div>

                {/* SFX */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_sfx}
                    onChange={e => setConfig(prev => ({ ...prev, enable_sfx: e.target.checked }))}
                    className="rounded"
                  />
                  <span className="text-sm font-medium text-gray-700">🔊 効果音自動挿入</span>
                </label>

                {/* Thumbnail */}
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={config.enable_thumbnail}
                    onChange={e => setConfig(prev => ({ ...prev, enable_thumbnail: e.target.checked }))}
                    className="rounded"
                  />
                  <span className="text-sm font-medium text-gray-700">🖼️ サムネイル自動生成（テキスト入り）</span>
                </label>

                {/* Transitions */}
                <div className="col-span-2">
                  <label className="flex items-center gap-2 mb-2">
                    <input
                      type="checkbox"
                      checked={config.enable_transitions}
                      onChange={e => setConfig(prev => ({ ...prev, enable_transitions: e.target.checked }))}
                      className="rounded"
                    />
                    <span className="text-sm font-medium text-gray-700">🎬 トランジション</span>
                  </label>
                  {config.enable_transitions && (
                    <div className="flex gap-4 ml-6">
                      <select
                        value={config.transition_type}
                        onChange={e => setConfig(prev => ({ ...prev, transition_type: e.target.value }))}
                        className="border rounded-md px-3 py-1.5 text-sm"
                      >
                        {TRANSITIONS.map(t => (
                          <option key={t.id} value={t.id}>{t.name}</option>
                        ))}
                      </select>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-500">時間:</span>
                        <input
                          type="number"
                          min={0.1}
                          max={2}
                          step={0.1}
                          value={config.transition_duration}
                          onChange={e => setConfig(prev => ({ ...prev, transition_duration: parseFloat(e.target.value) || 0.5 }))}
                          className="w-16 border rounded-md px-2 py-1 text-sm"
                        />
                        <span className="text-xs text-gray-500">秒</span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </section>
          </div>

          {/* Right: Summary & Generate Button */}
          <div className="space-y-4">
            <div className="bg-purple-50 rounded-lg border border-purple-200 p-4 sticky top-4">
              <h3 className="font-semibold text-purple-800 mb-3">📊 生成サマリー</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-gray-600">ブランド:</span>
                  <span className="font-medium">{config.brand_id ? brands.find(b => b.client_id === config.brand_id)?.client_name || config.brand_id : "全ブランド"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">最大生成数:</span>
                  <span className="font-medium">{config.max_clips}件</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">字幕スタイル:</span>
                  <span className="font-medium">{SUBTITLE_STYLES.find(s => s.id === config.subtitle_style)?.name}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">フック:</span>
                  <span className="font-medium">{config.enable_hook ? "ON" : "OFF"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">CTA:</span>
                  <span className="font-medium">{config.enable_cta ? "ON" : "OFF"}</span>
                </div>
              </div>

              {/* V2 Effects Summary */}
              <div className="mt-3 pt-3 border-t border-purple-200">
                <h4 className="text-xs font-semibold text-purple-700 mb-2">V2 エフェクト</h4>
                <div className="flex flex-wrap gap-1.5">
                  {config.enable_silence_cut && <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">✂️ 無音カット</span>}
                  {config.enable_zoom_pulse && <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">🔍 ズーム</span>}
                  {config.enable_progress_bar && <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">📊 進行バー</span>}
                  {config.enable_flash_intro && <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">⚡ フラッシュ</span>}
                  {config.enable_loop_fade && <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">🔄 ループ</span>}
                  {config.enable_keyword_highlight && <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">🌟 ハイライト</span>}
                  {config.enable_subtitle_animation && <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">✨ アニメ</span>}
                </div>
              </div>

              <button
                onClick={handleGenerate}
                disabled={generating}
                className={`w-full mt-4 py-3 rounded-lg font-bold text-white transition-all ${
                  generating
                    ? "bg-gray-400 cursor-not-allowed"
                    : "bg-gradient-to-r from-purple-600 to-pink-500 hover:from-purple-700 hover:to-pink-600 shadow-lg hover:shadow-xl"
                }`}
              >
                {generating ? "⏳ 生成中..." : "🚀 V2 全自動生成を開始"}
              </button>

              {activeJob && activeJob.status !== "done" && activeJob.status !== "failed" && (
                <div className="mt-4 p-3 bg-white rounded-lg border">
                  {jobStalled && (
                    <div className="flex items-center gap-2 p-2 mb-2 bg-amber-50 border border-amber-200 rounded-lg">
                      <span className="text-amber-500">⚠️</span>
                      <div>
                        <p className="text-xs font-medium text-amber-700">処理が停止している可能性があります</p>
                        <p className="text-[10px] text-amber-600">サーバー側で自動リカバリーが実行されます。しばらくお待ちください。</p>
                      </div>
                    </div>
                  )}
                  <div className="flex items-center justify-between mb-2">
                    <span className={`text-xs font-medium ${jobStalled ? 'text-amber-600' : 'text-purple-600'}`}>{jobStalled ? '応答待ち' : '進捗'}</span>
                    <span className={`text-sm font-bold ${jobStalled ? 'text-amber-700' : 'text-purple-700'}`}>{activeJob.progress_pct || 0}%</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-3">
                    <div
                      className="bg-gradient-to-r from-purple-500 to-pink-500 h-3 rounded-full transition-all duration-500"
                      style={{ width: `${activeJob.progress_pct || 0}%` }}
                    ></div>
                  </div>
                  <p className="text-xs text-gray-600 mt-2 font-medium">{activeJob.current_step}</p>
                  {/* Time info - step-based ETA */}
                  {(() => {
                    const createdAt = activeJob.created_at ? new Date(activeJob.created_at) : null;
                    const now = new Date();
                    const elapsedMs = createdAt ? now - createdAt : 0;
                    const elapsedSec = Math.floor(elapsedMs / 1000);
                    const elapsedMin = Math.floor(elapsedSec / 60);
                    const elapsedRemSec = elapsedSec % 60;
                    const pct = activeJob.progress_pct || 0;
                    const typicalTotalSec = 180;
                    const remainPct = Math.max(0, 100 - pct);
                    const estimatedRemainSec = Math.round((remainPct / 100) * typicalTotalSec);
                    let etaText;
                    if (pct < 3) {
                      etaText = "約3分";
                    } else if (pct >= 95) {
                      etaText = "まもなく完了";
                    } else {
                      const etaMin = Math.floor(estimatedRemainSec / 60);
                      const etaSec = estimatedRemainSec % 60;
                      etaText = etaMin > 0 ? `約${etaMin}分${etaSec}秒` : `約${etaSec}秒`;
                    }
                    return (
                      <div className="flex items-center justify-between mt-2 text-xs text-gray-500">
                        <span>⏱ 経過: {elapsedMin > 0 ? `${elapsedMin}分${elapsedRemSec}秒` : `${elapsedRemSec}秒`}</span>
                        <span>⏳ 残り: {etaText}</span>
                      </div>
                    );
                  })()}
                  <p className="text-xs text-gray-400 mt-1 text-center">※ このモーダルを閉じても生成は続行されます</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Jobs Tab */}
      {activeTab === "jobs" && (
        <div className="space-y-4">
          {/* Active Job Detail */}
          {activeJob && (
            <div className={`p-4 rounded-lg border-2 ${
              activeJob.status === "done" ? "border-green-300 bg-green-50" :
              activeJob.status === "failed" ? "border-red-300 bg-red-50" :
              "border-purple-300 bg-purple-50"
            }`}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-gray-800">
                  {activeJob.status === "done" ? "✅" : activeJob.status === "failed" ? "❌" : "⏳"}
                  {" "}アクティブジョブ
                </h3>
                <span className="text-xs text-gray-500">{activeJob.job_id?.slice(0, 8)}</span>
              </div>

              {/* Progress */}
              <div className="mb-3">
                <div className="flex justify-between text-sm mb-1">
                  <span>{activeJob.current_step || activeJob.status}</span>
                  <span className="flex items-center gap-3">
                    <span className="font-bold text-purple-700">{activeJob.progress_pct || 0}%</span>
                    <span>{activeJob.clips_completed || 0}/{activeJob.clips_total || 0}件</span>
                  </span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-3">
                  <div
                    className={`h-3 rounded-full transition-all duration-500 ${
                      activeJob.status === "done" ? "bg-green-500" :
                      activeJob.status === "failed" ? "bg-red-500" :
                      "bg-gradient-to-r from-purple-500 to-pink-500"
                    }`}
                    style={{ width: `${activeJob.progress_pct || 0}%` }}
                  ></div>
                </div>
                {/* Time info - step-based ETA */}
                {activeJob.status !== "done" && activeJob.status !== "failed" && (() => {
                  const createdAt = activeJob.created_at ? new Date(activeJob.created_at) : null;
                  const now = new Date();
                  const elapsedMs = createdAt ? now - createdAt : 0;
                  const elapsedSec = Math.floor(elapsedMs / 1000);
                  const elapsedMin = Math.floor(elapsedSec / 60);
                  const elapsedRemSec = elapsedSec % 60;
                  const pct = activeJob.progress_pct || 0;
                  const typicalTotalSec = 180;
                  const remainPct = Math.max(0, 100 - pct);
                  const estimatedRemainSec = Math.round((remainPct / 100) * typicalTotalSec);
                  let etaText;
                  if (pct < 3) {
                    etaText = "約3分";
                  } else if (pct >= 95) {
                    etaText = "まもなく完了";
                  } else {
                    const etaMin = Math.floor(estimatedRemainSec / 60);
                    const etaSec = estimatedRemainSec % 60;
                    etaText = etaMin > 0 ? `約${etaMin}分${etaSec}秒` : `約${etaSec}秒`;
                  }
                  return (
                    <div className="flex items-center justify-between mt-2 text-xs text-gray-500">
                      <span>⏱ 経過: {elapsedMin > 0 ? `${elapsedMin}分${elapsedRemSec}秒` : `${elapsedRemSec}秒`}</span>
                      <span>⏳ 残り: {etaText}</span>
                    </div>
                  );
                })()}
              </div>

              {/* Error */}
              {activeJob.error && (
                <div className="text-sm text-red-600 bg-red-100 p-2 rounded">{activeJob.error}</div>
              )}

              {/* Results */}
              {activeJob.results && activeJob.results.length > 0 && (
                <div className="mt-3 space-y-2">
                  <h4 className="text-sm font-medium text-gray-700">生成結果:</h4>
                  {activeJob.results.map((r, i) => (
                    <div key={i} className="bg-white p-3 rounded border text-sm">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-gray-600">
                          {r.status === "done" ? "✅" : "❌"} クリップ {r.clip_id?.slice(0, 8)}
                        </span>
                        <div className="flex items-center gap-3">
                          {r.download_url && (
                            <button
                              onClick={() => {
                                if (previewClipId === r.clip_id) {
                                  setPreviewUrl(null);
                                  setPreviewClipId(null);
                                } else {
                                  setPreviewUrl(r.download_url);
                                  setPreviewClipId(r.clip_id);
                                }
                              }}
                              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
                                previewClipId === r.clip_id
                                  ? "bg-purple-600 text-white shadow-md"
                                  : "bg-purple-50 text-purple-700 hover:bg-purple-100"
                              }`}
                            >
                              {previewClipId === r.clip_id ? "⏹ 閉じる" : "▶ プレビュー"}
                            </button>
                          )}
                          {r.download_url && (
                            <a
                              href={r.download_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-purple-600 hover:text-purple-800 font-medium text-xs"
                            >
                              ⬇️ ダウンロード
                            </a>
                          )}
                          {r.error && <span className="text-red-500 text-xs">{r.error}</span>}
                        </div>
                      </div>

                      {/* Inline Video Preview */}
                      {previewClipId === r.clip_id && previewUrl && (
                        <div className="mt-3 mb-3 rounded-lg overflow-hidden bg-black shadow-lg">
                          <video
                            key={previewUrl}
                            controls
                            autoPlay
                            playsInline
                            className="w-full max-h-[480px] mx-auto"
                            style={{ maxWidth: "360px", margin: "0 auto", display: "block" }}
                          >
                            <source src={previewUrl} type="video/mp4" />
                            お使いのブラウザは動画再生に対応していません。
                          </video>
                        </div>
                      )}

                      {/* V2: Show applied effects */}
                      {r.effects_applied && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {r.effects_applied.silence_cut && <span className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">✂️ 無音カット</span>}
                          {r.effects_applied.zoom_pulse && <span className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">🔍 ズーム×{r.zoom_points}</span>}
                          {r.effects_applied.progress_bar && <span className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">📊 進行バー</span>}
                          {r.effects_applied.flash_intro && <span className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">⚡ フラッシュ</span>}
                          {r.effects_applied.loop_fade && <span className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">🔄 ループ</span>}
                          {r.effects_applied.cta && <span className="text-xs bg-green-50 text-green-600 px-1.5 py-0.5 rounded">📢 CTA</span>}
                          {r.effects_applied.keyword_highlight && <span className="text-xs bg-yellow-50 text-yellow-700 px-1.5 py-0.5 rounded">🌟 ハイライト</span>}
                          {r.effects_applied.subtitle_animation && <span className="text-xs bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded">✨ アニメ</span>}
                        </div>
                      )}

                      {/* V2: Show hook & CTA text */}
                      {(r.hook_text || r.cta_text) && (
                        <div className="mt-2 text-xs text-gray-500 space-y-0.5">
                          {r.hook_text && <div>🎯 フック: 「{r.hook_text}」</div>}
                          {r.cta_text && <div>📢 CTA: 「{r.cta_text}」</div>}
                          {r.captions_count > 0 && <div>💬 字幕: {r.captions_count}セグメント</div>}
                          {r.duration_sec && <div>⏱️ 長さ: {r.duration_sec.toFixed(1)}秒</div>}
                        </div>
                      )}
                      {/* Caption Edit Button */}
                      {r.status === "done" && r.captions_count > 0 && (
                        <button
                          onClick={() => openCaptionEditor(activeJob.job_id, r.clip_id, r.download_url)}
                          className="mt-2 px-3 py-1.5 bg-yellow-50 text-yellow-700 border border-yellow-300 rounded-md text-xs font-medium hover:bg-yellow-100 transition-colors"
                        >
                          ✏️ 字幕を編集
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Jobs List */}
          <div className="bg-white rounded-lg border">
            <div className="p-3 border-b flex items-center justify-between">
              <h3 className="font-semibold text-gray-700">ジョブ履歴</h3>
              <div className="flex gap-1">
                {[
                  { id: "all", label: `全て (${jobs.length})`, color: "gray" },
                  { id: "done", label: `✅ 完成 (${jobs.filter(j => j.status === "done").length})`, color: "green" },
                  { id: "error", label: `❌ エラー (${jobs.filter(j => j.status === "failed" || j.status === "error").length})`, color: "red" },
                ].map(f => (
                  <button
                    key={f.id}
                    onClick={() => setJobFilter(f.id)}
                    className={`px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
                      jobFilter === f.id
                        ? f.color === "green" ? "bg-green-100 text-green-700 ring-1 ring-green-300"
                        : f.color === "red" ? "bg-red-100 text-red-700 ring-1 ring-red-300"
                        : "bg-gray-100 text-gray-700 ring-1 ring-gray-300"
                        : "text-gray-500 hover:bg-gray-50"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>
            {(() => {
              const filteredJobs = jobFilter === "all" ? jobs
                : jobFilter === "done" ? jobs.filter(j => j.status === "done")
                : jobs.filter(j => j.status === "failed" || j.status === "error");
              return filteredJobs.length === 0 ? (
                <div className="p-8 text-center text-gray-400">
                  {jobFilter === "all" ? "まだジョブがありません" : `${jobFilter === "done" ? "完成" : "エラー"}ジョブはありません`}
                </div>
              ) : (
              <div className="divide-y">
                {filteredJobs.map(job => (
                  <div
                    key={job.job_id}
                    className="p-3 hover:bg-gray-50 cursor-pointer"
                    onClick={() => { setActiveJobId(job.job_id); setActiveJob(job); }}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${
                          job.status === "done" ? "bg-green-500" :
                          job.status === "failed" ? "bg-red-500" :
                          job.status === "queued" ? "bg-yellow-500" :
                          "bg-purple-500 animate-pulse"
                        }`}></span>
                        <span className="text-sm font-medium text-gray-700">
                          {job.job_id?.slice(0, 8)}
                        </span>
                        <span className="text-xs text-gray-400">
                          {job.clips_completed || 0}/{job.clips_total || 0}件
                        </span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className={`text-xs px-2 py-0.5 rounded-full ${
                          job.status === "done" ? "bg-green-100 text-green-700" :
                          job.status === "failed" ? "bg-red-100 text-red-700" :
                          "bg-purple-100 text-purple-700"
                        }`}>
                          {job.status}
                        </span>
                        <span className="text-xs text-gray-400">
                          {job.created_at ? new Date(job.created_at).toLocaleString("ja-JP") : "—"}
                        </span>
                      </div>
                    </div>
                    {job.current_step && (
                      <p className="text-xs text-gray-500 mt-1 ml-4">{job.current_step}</p>
                    )}
                  </div>
                ))}
              </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* Completed Clips Tab */}
      {activeTab === "completed" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-gray-500">
              完成したAIクリップ: <strong>{completedTotal}件</strong>
            </p>
            <button
              onClick={fetchCompletedClips}
              disabled={completedLoading}
              className="px-3 py-1.5 text-sm bg-gray-100 hover:bg-gray-200 rounded-md disabled:opacity-50"
            >
              {completedLoading ? "読み込み中..." : "🔄 更新"}
            </button>
          </div>

          {completedLoading && completedClips.length === 0 ? (
            <div className="p-8 text-center text-gray-400 bg-white rounded-lg border">
              <div className="animate-spin inline-block w-6 h-6 border-2 border-purple-500 border-t-transparent rounded-full mb-2"></div>
              <p>読み込み中...</p>
            </div>
          ) : completedClips.length === 0 ? (
            <div className="p-8 text-center text-gray-400 bg-white rounded-lg border">
              <p className="text-4xl mb-2">🎬</p>
              <p>まだ完成した動画がありません</p>
              <p className="text-xs mt-1">AIクリップ生成を実行すると、ここに完成動画が表示されます</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {completedClips.map((clip, idx) => (
                <div key={`${clip.job_id}-${clip.clip_id}-${idx}`} className="bg-white rounded-lg border overflow-hidden hover:shadow-md transition-shadow">
                  {/* Video Preview */}
                  <div className="relative bg-black aspect-video">
                    {(clip.download_url || clip.blob_url) ? (
                      <video
                        src={clip.download_url || clip.blob_url}
                        className="w-full h-full object-contain"
                        controls
                        preload="metadata"
                        poster={clip.thumbnail_url || undefined}
                      />
                    ) : clip.thumbnail_url ? (
                      <img src={clip.thumbnail_url} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-gray-500">
                        <span className="text-3xl">🎬</span>
                      </div>
                    )}
                    {/* Duration badge */}
                    {clip.duration_sec && (
                      <span className="absolute bottom-2 right-2 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded">
                        {Math.floor(clip.duration_sec / 60)}:{String(Math.floor(clip.duration_sec % 60)).padStart(2, '0')}
                      </span>
                    )}
                  </div>
                  {/* Info */}
                  <div className="p-3 space-y-2">
                    {/* Hook text */}
                    {clip.hook_text && (
                      <p className="text-sm font-medium text-gray-800 line-clamp-2">
                        🪝 {clip.hook_text}
                      </p>
                    )}
                    {/* CTA text */}
                    {clip.cta_text && (
                      <p className="text-xs text-orange-600 line-clamp-1">
                        📢 {clip.cta_text}
                      </p>
                    )}
                    {/* Meta info */}
                    <div className="flex flex-wrap gap-1.5 text-xs">
                      {clip.captions_count > 0 && (
                        <span className="bg-green-100 text-green-700 px-1.5 py-0.5 rounded">
                          字幕:{clip.captions_count}
                        </span>
                      )}
                      {clip.effects_applied && Object.entries(clip.effects_applied)
                        .filter(([, v]) => v)
                        .slice(0, 3)
                        .map(([k]) => (
                          <span key={k} className="bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded">
                            {k === 'silence_cut' ? '無音カット' :
                             k === 'zoom_pulse' ? 'ズーム' :
                             k === 'progress_bar' ? '進行バー' :
                             k === 'flash_intro' ? 'フラッシュ' :
                             k === 'loop_fade' ? 'ループ' :
                             k === 'cta' ? 'CTA' :
                             k === 'keyword_highlight' ? 'キーワード' :
                             k === 'subtitle_animation' ? '字幕アニメ' : k}
                          </span>
                        ))
                      }
                      {clip.file_size && (
                        <span className="bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
                          {(clip.file_size / 1024 / 1024).toFixed(1)}MB
                        </span>
                      )}
                    </div>
                    {/* Source info */}
                    {clip.source_clip && (
                      <div className="text-xs text-gray-500 border-t pt-1">
                        {clip.source_clip.product_name && <span>🏷️ {clip.source_clip.product_name}</span>}
                        {clip.source_clip.liver_name && <span className="ml-2">👤 {clip.source_clip.liver_name}</span>}
                      </div>
                    )}
                    {/* Date & Actions */}
                    <div className="flex items-center justify-between text-xs text-gray-400 border-t pt-1">
                      <span>{clip.created_at ? new Date(clip.created_at).toLocaleDateString('ja-JP') : ''}</span>
                      <div className="flex gap-2">
                        {clip.captions_count > 0 && clip.job_id && (
                          <button
                            onClick={() => openCaptionEditor(clip.job_id, clip.clip_id, clip.download_url || clip.blob_url)}
                            className="text-yellow-600 hover:text-yellow-800 font-medium"
                          >
                            ✏️ 編集
                          </button>
                        )}
                        {clip.download_url && (
                          <a
                            href={clip.download_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-500 hover:text-blue-700"
                          >
                            ⬇️ DL
                          </a>
                        )}
                        {clip.blob_url && (
                          <a
                            href={clip.blob_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-purple-500 hover:text-purple-700"
                          >
                            🔗 URL
                          </a>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Caption Editor Modal with Real-time Preview */}
      {editingJobId && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={closeCaptionEditor}>
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-hidden" onClick={e => e.stopPropagation()}>
            {/* Modal Header */}
            <div className="flex items-center justify-between p-4 border-b bg-gradient-to-r from-yellow-50 to-orange-50">
              <h3 className="font-bold text-gray-800 flex items-center gap-2">
                ✏️ 字幕・テキスト編集（リアルタイムプレビュー）
              </h3>
              <button onClick={closeCaptionEditor} className="text-gray-400 hover:text-gray-600 text-xl">✕</button>
            </div>

            {captionLoading ? (
              <div className="p-8 text-center">
                <div className="animate-spin inline-block w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full mb-2"></div>
                <p className="text-gray-500">字幕データを読み込み中...</p>
              </div>
            ) : (
              <div className="overflow-y-auto max-h-[calc(90vh-80px)] p-4">
                <CaptionOverlayPlayer
                  videoUrl={previewUrl}
                  captions={editCaptions}
                  onCaptionsChange={setEditCaptions}
                  jobId={editingJobId}
                  clipId={editingClipId}
                  adminKey={adminKey}
                  apiBase={API_BASE}
                  hookText={editHook}
                  ctaText={editCta}
                  onHookChange={setEditHook}
                  onCtaChange={setEditCta}
                  showEditPanel={true}
                  subtitlesBurnedIn={true}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {/* Candidates Tab */}
      {activeTab === "candidates" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-gray-500">
              条件に合致する候補クリップ: <strong>{candidates.length}件</strong>
            </p>
            <button
              onClick={fetchCandidates}
              className="px-3 py-1.5 text-sm bg-gray-100 hover:bg-gray-200 rounded-md"
            >
              🔄 再取得
            </button>
          </div>

          {candidates.length === 0 ? (
            <div className="p-8 text-center text-gray-400 bg-white rounded-lg border">
              条件に合うクリップが見つかりません。フィルタ条件を緩めてください。
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {candidates.map(c => (
                <div key={c.clip_id} className="bg-white rounded-lg border overflow-hidden hover:shadow-md transition-shadow">
                  {/* Thumbnail */}
                  <div className="h-32 bg-gray-100 flex items-center justify-center">
                    {c.thumbnail_url ? (
                      <img src={c.thumbnail_url} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <span className="text-3xl">🎬</span>
                    )}
                  </div>
                  {/* Info */}
                  <div className="p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-mono text-gray-400">{c.clip_id?.slice(0, 8)}</span>
                      <span className="text-xs text-gray-500">{c.duration_sec?.toFixed(0)}秒</span>
                    </div>
                    <p className="text-sm text-gray-700 line-clamp-2 mb-2">
                      {c.transcript_text || "(字幕なし)"}
                    </p>
                    <div className="flex items-center gap-2 text-xs">
                      {c.cta_score > 0 && (
                        <span className="bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded">
                          CTA:{c.cta_score}
                        </span>
                      )}
                      {c.importance_score > 0 && (
                        <span className="bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">
                          重要度:{c.importance_score?.toFixed(1)}
                        </span>
                      )}
                      {c.has_captions && (
                        <span className="bg-green-100 text-green-700 px-1.5 py-0.5 rounded">字幕有</span>
                      )}
                    </div>
                    {c.product_name && (
                      <p className="text-xs text-gray-500 mt-1 truncate">🏷️ {c.product_name}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
