/**
 * CaptionOverlayPlayer - リアルタイム字幕オーバーレイ付き動画プレイヤー
 * 
 * 字幕をHTML/CSSで動画上にリアルタイム表示。
 * 編集した字幕は即座にプレビューに反映される（再エンコード不要）。
 * ダウンロード時のみバックエンドで字幕を焼き込む。
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { Play, Pause, Volume2, VolumeX, Maximize2, Download, Loader2, Edit3, Save, RotateCcw } from "lucide-react";

export default function CaptionOverlayPlayer({
  videoUrl,
  captions = [],
  onCaptionsChange,
  jobId,
  clipId,
  adminKey,
  apiBase = "",
  hookText = "",
  ctaText = "",
  onHookChange,
  onCtaChange,
  showEditPanel = true,
  compact = false,
}) {
  const videoRef = useRef(null);
  const containerRef = useRef(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [currentCaption, setCurrentCaption] = useState(null);
  const [editingIdx, setEditingIdx] = useState(null);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [saved, setSaved] = useState(false);

  // Track current caption based on video time
  useEffect(() => {
    if (!captions.length) return;
    const active = captions.find(cap => {
      const start = cap.start ?? cap.start_time ?? 0;
      const end = cap.end ?? cap.end_time ?? start + 2;
      return currentTime >= start && currentTime <= end;
    });
    setCurrentCaption(active || null);
  }, [currentTime, captions]);

  // Time update handler
  const handleTimeUpdate = useCallback(() => {
    if (videoRef.current) {
      setCurrentTime(videoRef.current.currentTime);
    }
  }, []);

  const handleLoadedMetadata = useCallback(() => {
    if (videoRef.current) {
      setDuration(videoRef.current.duration);
    }
  }, []);

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (playing) {
      videoRef.current.pause();
    } else {
      videoRef.current.play();
    }
    setPlaying(!playing);
  };

  const handleSeek = (e) => {
    if (!videoRef.current) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    videoRef.current.currentTime = pct * duration;
  };

  // Jump to caption time
  const jumpToCaption = (cap) => {
    if (!videoRef.current) return;
    const start = cap.start ?? cap.start_time ?? 0;
    videoRef.current.currentTime = start;
    if (!playing) {
      videoRef.current.play();
      setPlaying(true);
    }
  };

  // Start editing a caption
  const startEdit = (idx) => {
    setEditingIdx(idx);
    setEditText(captions[idx].text);
    setSaved(false);
  };

  // Apply edit
  const applyEdit = (idx) => {
    if (!onCaptionsChange) return;
    const updated = [...captions];
    updated[idx] = { ...updated[idx], text: editText };
    onCaptionsChange(updated);
    setEditingIdx(null);
    setSaved(false);
  };

  // Cancel edit
  const cancelEdit = () => {
    setEditingIdx(null);
    setEditText("");
  };

  // Save captions to backend
  const saveCaptions = async () => {
    if (!jobId || !adminKey) return;
    setSaving(true);
    try {
      const res = await fetch(`${apiBase}/api/v1/ai-clip/jobs/${jobId}/captions`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Key": adminKey,
        },
        body: JSON.stringify({
          captions: captions,
          hook_text: hookText,
          cta_text: ctaText,
        }),
      });
      if (!res.ok) throw new Error("保存に失敗しました");
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      alert("字幕の保存に失敗: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  // Regenerate (burn subtitles) and download
  const regenerateAndDownload = async () => {
    if (!jobId || !adminKey) return;
    if (!confirm("修正した字幕で動画を再エンコードしてダウンロードしますか？\n（2〜3分かかります）")) return;
    setRegenerating(true);
    try {
      // First save captions
      await fetch(`${apiBase}/api/v1/ai-clip/jobs/${jobId}/captions`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Key": adminKey,
        },
        body: JSON.stringify({
          captions: captions,
          hook_text: hookText,
          cta_text: ctaText,
        }),
      });
      // Then trigger regeneration
      const res = await fetch(`${apiBase}/api/v1/ai-clip/jobs/${jobId}/regenerate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Key": adminKey,
        },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error("再エンコード開始に失敗");
      const data = await res.json();
      alert(`再エンコードを開始しました（ジョブID: ${data.job_id}）\n完了後にダウンロードURLが表示されます。`);
    } catch (e) {
      alert("再エンコードに失敗: " + e.message);
    } finally {
      setRegenerating(false);
    }
  };

  const formatTime = (t) => {
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  return (
    <div className="space-y-3">
      {/* Video Player with Caption Overlay */}
      <div ref={containerRef} className="relative rounded-xl overflow-hidden bg-black group">
        <video
          ref={videoRef}
          src={videoUrl}
          className={`w-full ${compact ? "max-h-[40vh]" : "max-h-[50vh]"} aspect-[9/16] object-contain`}
          onTimeUpdate={handleTimeUpdate}
          onLoadedMetadata={handleLoadedMetadata}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          muted={muted}
          playsInline
          onClick={togglePlay}
        />

        {/* Caption Overlay */}
        {currentCaption && (
          <div className="absolute bottom-16 left-0 right-0 flex justify-center pointer-events-none px-4">
            <div className="bg-black/70 backdrop-blur-sm rounded-lg px-4 py-2 max-w-[90%]">
              <p className="text-white text-sm md:text-base font-bold text-center leading-tight drop-shadow-lg">
                {currentCaption.text}
              </p>
            </div>
          </div>
        )}

        {/* Play overlay */}
        {!playing && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/20 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer" onClick={togglePlay}>
            <div className="w-14 h-14 rounded-full bg-white/90 flex items-center justify-center shadow-lg">
              <Play className="w-6 h-6 text-gray-800 ml-1" />
            </div>
          </div>
        )}

        {/* Controls bar */}
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-3 opacity-0 group-hover:opacity-100 transition-opacity">
          {/* Progress bar */}
          <div className="w-full h-1.5 bg-white/30 rounded-full cursor-pointer mb-2" onClick={handleSeek}>
            <div
              className="h-full bg-emerald-400 rounded-full transition-all"
              style={{ width: `${duration ? (currentTime / duration) * 100 : 0}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-white text-xs">
            <div className="flex items-center gap-2">
              <button onClick={togglePlay} className="hover:text-emerald-300 transition">
                {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
              </button>
              <button onClick={() => setMuted(!muted)} className="hover:text-emerald-300 transition">
                {muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
              </button>
              <span>{formatTime(currentTime)} / {formatTime(duration)}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Caption Edit Panel */}
      {showEditPanel && captions.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-semibold text-gray-600 flex items-center gap-1">
              <Edit3 className="w-3.5 h-3.5" />
              字幕編集 ({captions.length}件)
              <span className="text-gray-400 font-normal ml-1">クリックで再生位置ジャンプ</span>
            </h4>
            <div className="flex items-center gap-2">
              {saved && <span className="text-xs text-emerald-500 font-medium">✓ 保存済み</span>}
              <button
                onClick={saveCaptions}
                disabled={saving}
                className="text-xs px-2.5 py-1 bg-blue-500 hover:bg-blue-600 text-white rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center gap-1"
              >
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                保存
              </button>
              <button
                onClick={regenerateAndDownload}
                disabled={regenerating}
                className="text-xs px-2.5 py-1 bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-600 hover:to-red-600 text-white rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center gap-1"
              >
                {regenerating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                字幕焼込みDL
              </button>
            </div>
          </div>

          {/* Hook & CTA quick edit */}
          {(onHookChange || onCtaChange) && (
            <div className="grid grid-cols-2 gap-2">
              {onHookChange && (
                <div>
                  <label className="text-[10px] text-gray-400 uppercase">フック</label>
                  <input
                    type="text"
                    value={hookText}
                    onChange={e => onHookChange(e.target.value)}
                    className="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-emerald-300 focus:border-emerald-400"
                    placeholder="最初3秒のフック"
                  />
                </div>
              )}
              {onCtaChange && (
                <div>
                  <label className="text-[10px] text-gray-400 uppercase">CTA</label>
                  <input
                    type="text"
                    value={ctaText}
                    onChange={e => onCtaChange(e.target.value)}
                    className="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-emerald-300 focus:border-emerald-400"
                    placeholder="購入誘導テキスト"
                  />
                </div>
              )}
            </div>
          )}

          {/* Caption timeline list */}
          <div className="max-h-[200px] overflow-y-auto border border-gray-200 rounded-lg bg-gray-50 divide-y divide-gray-100">
            {captions.map((cap, idx) => {
              const start = cap.start ?? cap.start_time ?? 0;
              const end = cap.end ?? cap.end_time ?? start + 2;
              const isActive = currentTime >= start && currentTime <= end;
              const isEditing = editingIdx === idx;

              return (
                <div
                  key={idx}
                  className={`flex items-center gap-2 px-3 py-1.5 text-xs transition-colors cursor-pointer ${
                    isActive ? "bg-emerald-50 border-l-2 border-emerald-500" : "hover:bg-gray-100"
                  }`}
                  onClick={() => !isEditing && jumpToCaption(cap)}
                >
                  <span className="flex-shrink-0 w-10 text-gray-400 font-mono text-[10px]">
                    {start.toFixed(1)}s
                  </span>
                  {isEditing ? (
                    <div className="flex-1 flex items-center gap-1">
                      <input
                        type="text"
                        value={editText}
                        onChange={e => setEditText(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === "Enter") applyEdit(idx);
                          if (e.key === "Escape") cancelEdit();
                        }}
                        className="flex-1 border border-emerald-300 rounded px-2 py-0.5 text-xs focus:ring-1 focus:ring-emerald-400"
                        autoFocus
                        onClick={e => e.stopPropagation()}
                      />
                      <button
                        onClick={(e) => { e.stopPropagation(); applyEdit(idx); }}
                        className="text-emerald-600 hover:text-emerald-700 font-bold px-1"
                      >
                        ✓
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); cancelEdit(); }}
                        className="text-gray-400 hover:text-gray-600 px-1"
                      >
                        ✕
                      </button>
                    </div>
                  ) : (
                    <span
                      className={`flex-1 ${isActive ? "text-emerald-700 font-semibold" : "text-gray-700"}`}
                      onDoubleClick={(e) => { e.stopPropagation(); startEdit(idx); }}
                      title="ダブルクリックで編集"
                    >
                      {cap.text}
                    </span>
                  )}
                  {!isEditing && (
                    <button
                      onClick={(e) => { e.stopPropagation(); startEdit(idx); }}
                      className="flex-shrink-0 text-gray-300 hover:text-emerald-500 transition"
                      title="編集"
                    >
                      <Edit3 className="w-3 h-3" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-gray-400 text-center">
            ダブルクリックで字幕を直接編集 • 変更はリアルタイムでプレビューに反映 • 「字幕焼込みDL」で最終動画を生成
          </p>
        </div>
      )}
    </div>
  );
}
