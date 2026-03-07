import React, { useState, useRef, useCallback, useEffect } from "react";
import VideoService from "../base/services/videoService";

/**
 * Lightning Clip Editor
 * AI生成後の最小編集機能: Trim (±3秒) / Caption Edit / Export
 */
const LightningClipEditor = ({ videoId, clip, onClose, onClipUpdated }) => {
  const videoRef = useRef(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  // Trim state
  const [trimStart, setTrimStart] = useState(clip?.time_start || 0);
  const [trimEnd, setTrimEnd] = useState(clip?.time_end || 0);
  const originalStart = clip?.time_start || 0;
  const originalEnd = clip?.time_end || 0;
  const MAX_TRIM_DELTA = 3.0; // ±3 seconds max

  // Caption state
  const [captions, setCaptions] = useState([]);
  const [editingCaptionIdx, setEditingCaptionIdx] = useState(null);

  // UI state
  const [isTrimming, setIsTrimming] = useState(false);
  const [isSavingCaptions, setIsSavingCaptions] = useState(false);
  const [activeTab, setActiveTab] = useState("trim"); // "trim" | "captions"
  const [statusMessage, setStatusMessage] = useState(null);

  // Format seconds to MM:SS
  const formatTime = (sec) => {
    if (!sec && sec !== 0) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  // Video event handlers
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
    if (isPlaying) {
      videoRef.current.pause();
    } else {
      videoRef.current.play();
    }
    setIsPlaying(!isPlaying);
  };

  // Trim handlers
  const handleTrimStartChange = (delta) => {
    const newStart = Math.max(0, trimStart + delta);
    const clampedStart = Math.max(
      originalStart - MAX_TRIM_DELTA,
      Math.min(originalStart + MAX_TRIM_DELTA, newStart)
    );
    if (clampedStart < trimEnd - 1) {
      setTrimStart(Math.round(clampedStart * 10) / 10);
    }
  };

  const handleTrimEndChange = (delta) => {
    const newEnd = trimEnd + delta;
    const clampedEnd = Math.max(
      originalEnd - MAX_TRIM_DELTA,
      Math.min(originalEnd + MAX_TRIM_DELTA, newEnd)
    );
    if (clampedEnd > trimStart + 1) {
      setTrimEnd(Math.round(clampedEnd * 10) / 10);
    }
  };

  const handleApplyTrim = async () => {
    if (!clip?.clip_id) return;
    setIsTrimming(true);
    setStatusMessage(null);
    try {
      const res = await VideoService.trimClip(
        videoId,
        clip.clip_id,
        trimStart,
        trimEnd
      );
      setStatusMessage({ type: "success", text: "トリム適用中... 新しいクリップを生成しています" });
      if (onClipUpdated) {
        onClipUpdated(res);
      }
    } catch (e) {
      setStatusMessage({ type: "error", text: `トリム失敗: ${e.message}` });
    } finally {
      setIsTrimming(false);
    }
  };

  // Caption handlers
  const handleCaptionTextChange = (idx, newText) => {
    setCaptions((prev) => {
      const updated = [...prev];
      updated[idx] = { ...updated[idx], text: newText };
      return updated;
    });
  };

  const handleCaptionEmphasisToggle = (idx) => {
    setCaptions((prev) => {
      const updated = [...prev];
      updated[idx] = { ...updated[idx], emphasis: !updated[idx].emphasis };
      return updated;
    });
  };

  const handleSaveCaptions = async () => {
    if (!clip?.clip_id) return;
    setIsSavingCaptions(true);
    setStatusMessage(null);
    try {
      await VideoService.updateClipCaptions(videoId, clip.clip_id, captions);
      setStatusMessage({ type: "success", text: "字幕を保存しました" });
    } catch (e) {
      setStatusMessage({ type: "error", text: `字幕保存失敗: ${e.message}` });
    } finally {
      setIsSavingCaptions(false);
    }
  };

  // Initialize captions from clip data (mock for now)
  useEffect(() => {
    if (clip?.captions) {
      setCaptions(clip.captions);
    }
  }, [clip]);

  if (!clip) return null;

  const trimDelta = (trimEnd - trimStart) - (originalEnd - originalStart);

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0,0,0,0.7)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div
        style={{
          backgroundColor: "#1a1a2e",
          borderRadius: 16,
          width: "90%",
          maxWidth: 900,
          maxHeight: "90vh",
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "16px 20px",
            borderBottom: "1px solid #333",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 20 }}>⚡</span>
            <h3 style={{ margin: 0, color: "#fff", fontSize: 18 }}>
              Lightning Clip Editor
            </h3>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "#888",
              fontSize: 24,
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
          {/* Video Preview */}
          <div
            style={{
              flex: "0 0 360px",
              padding: 16,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
            }}
          >
            {clip.clip_url ? (
              <video
                ref={videoRef}
                src={clip.clip_url}
                onTimeUpdate={handleTimeUpdate}
                onLoadedMetadata={handleLoadedMetadata}
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
                style={{
                  width: 200,
                  height: 356,
                  borderRadius: 12,
                  backgroundColor: "#000",
                  objectFit: "cover",
                }}
              />
            ) : (
              <div
                style={{
                  width: 200,
                  height: 356,
                  borderRadius: 12,
                  backgroundColor: "#000",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#666",
                }}
              >
                プレビューなし
              </div>
            )}

            {/* Playback controls */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                marginTop: 12,
              }}
            >
              <button
                onClick={togglePlay}
                style={{
                  background: "#FF6B35",
                  border: "none",
                  borderRadius: "50%",
                  width: 40,
                  height: 40,
                  color: "#fff",
                  fontSize: 18,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                {isPlaying ? "⏸" : "▶"}
              </button>
              <span style={{ color: "#aaa", fontSize: 13 }}>
                {formatTime(currentTime)} / {formatTime(duration)}
              </span>
            </div>

            {/* Clip info */}
            <div
              style={{
                marginTop: 12,
                padding: "8px 12px",
                backgroundColor: "#252540",
                borderRadius: 8,
                width: "100%",
              }}
            >
              <div style={{ color: "#888", fontSize: 11, marginBottom: 4 }}>
                元の区間
              </div>
              <div style={{ color: "#fff", fontSize: 14 }}>
                {formatTime(originalStart)} - {formatTime(originalEnd)}
              </div>
            </div>
          </div>

          {/* Editor Panel */}
          <div style={{ flex: 1, padding: 16, minWidth: 0 }}>
            {/* Tabs */}
            <div
              style={{
                display: "flex",
                gap: 4,
                marginBottom: 16,
                borderBottom: "1px solid #333",
                paddingBottom: 8,
              }}
            >
              {[
                { key: "trim", label: "Trim", icon: "✂️" },
                { key: "captions", label: "字幕編集", icon: "📝" },
              ].map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  style={{
                    padding: "8px 16px",
                    border: "none",
                    borderRadius: 8,
                    backgroundColor:
                      activeTab === tab.key ? "#FF6B35" : "transparent",
                    color: activeTab === tab.key ? "#fff" : "#888",
                    cursor: "pointer",
                    fontSize: 14,
                    fontWeight: activeTab === tab.key ? 600 : 400,
                  }}
                >
                  {tab.icon} {tab.label}
                </button>
              ))}
            </div>

            {/* Trim Tab */}
            {activeTab === "trim" && (
              <div>
                <div style={{ marginBottom: 20 }}>
                  <div
                    style={{
                      color: "#aaa",
                      fontSize: 12,
                      marginBottom: 8,
                    }}
                  >
                    開始時間（±3秒）
                  </div>
                  <div
                    style={{ display: "flex", alignItems: "center", gap: 8 }}
                  >
                    <button
                      onClick={() => handleTrimStartChange(-1)}
                      style={trimBtnStyle}
                    >
                      -1s
                    </button>
                    <button
                      onClick={() => handleTrimStartChange(-0.5)}
                      style={trimBtnStyle}
                    >
                      -0.5s
                    </button>
                    <span
                      style={{
                        color: "#fff",
                        fontSize: 20,
                        fontWeight: 700,
                        minWidth: 70,
                        textAlign: "center",
                      }}
                    >
                      {formatTime(trimStart)}
                    </span>
                    <button
                      onClick={() => handleTrimStartChange(0.5)}
                      style={trimBtnStyle}
                    >
                      +0.5s
                    </button>
                    <button
                      onClick={() => handleTrimStartChange(1)}
                      style={trimBtnStyle}
                    >
                      +1s
                    </button>
                  </div>
                  <div
                    style={{
                      color: "#666",
                      fontSize: 11,
                      marginTop: 4,
                    }}
                  >
                    元: {formatTime(originalStart)} → 変更後:{" "}
                    {formatTime(trimStart)} (
                    {(trimStart - originalStart) >= 0 ? "+" : ""}
                    {(trimStart - originalStart).toFixed(1)}s)
                  </div>
                </div>

                <div style={{ marginBottom: 20 }}>
                  <div
                    style={{
                      color: "#aaa",
                      fontSize: 12,
                      marginBottom: 8,
                    }}
                  >
                    終了時間（±3秒）
                  </div>
                  <div
                    style={{ display: "flex", alignItems: "center", gap: 8 }}
                  >
                    <button
                      onClick={() => handleTrimEndChange(-1)}
                      style={trimBtnStyle}
                    >
                      -1s
                    </button>
                    <button
                      onClick={() => handleTrimEndChange(-0.5)}
                      style={trimBtnStyle}
                    >
                      -0.5s
                    </button>
                    <span
                      style={{
                        color: "#fff",
                        fontSize: 20,
                        fontWeight: 700,
                        minWidth: 70,
                        textAlign: "center",
                      }}
                    >
                      {formatTime(trimEnd)}
                    </span>
                    <button
                      onClick={() => handleTrimEndChange(0.5)}
                      style={trimBtnStyle}
                    >
                      +0.5s
                    </button>
                    <button
                      onClick={() => handleTrimEndChange(1)}
                      style={trimBtnStyle}
                    >
                      +1s
                    </button>
                  </div>
                  <div
                    style={{
                      color: "#666",
                      fontSize: 11,
                      marginTop: 4,
                    }}
                  >
                    元: {formatTime(originalEnd)} → 変更後:{" "}
                    {formatTime(trimEnd)} (
                    {(trimEnd - originalEnd) >= 0 ? "+" : ""}
                    {(trimEnd - originalEnd).toFixed(1)}s)
                  </div>
                </div>

                {/* Duration summary */}
                <div
                  style={{
                    padding: "12px 16px",
                    backgroundColor: "#252540",
                    borderRadius: 8,
                    marginBottom: 16,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                    }}
                  >
                    <span style={{ color: "#aaa", fontSize: 13 }}>
                      クリップ長
                    </span>
                    <span style={{ color: "#fff", fontSize: 14, fontWeight: 600 }}>
                      {(trimEnd - trimStart).toFixed(1)}秒
                      {trimDelta !== 0 && (
                        <span
                          style={{
                            color: trimDelta > 0 ? "#4CAF50" : "#FF5722",
                            marginLeft: 8,
                            fontSize: 12,
                          }}
                        >
                          ({trimDelta > 0 ? "+" : ""}
                          {trimDelta.toFixed(1)}s)
                        </span>
                      )}
                    </span>
                  </div>
                </div>

                <button
                  onClick={handleApplyTrim}
                  disabled={
                    isTrimming ||
                    (trimStart === originalStart && trimEnd === originalEnd)
                  }
                  style={{
                    width: "100%",
                    padding: "12px 0",
                    border: "none",
                    borderRadius: 8,
                    backgroundColor:
                      trimStart === originalStart && trimEnd === originalEnd
                        ? "#333"
                        : "#FF6B35",
                    color: "#fff",
                    fontSize: 15,
                    fontWeight: 600,
                    cursor:
                      trimStart === originalStart && trimEnd === originalEnd
                        ? "not-allowed"
                        : "pointer",
                    opacity: isTrimming ? 0.6 : 1,
                  }}
                >
                  {isTrimming ? "⏳ 生成中..." : "✂️ トリムを適用して再生成"}
                </button>
              </div>
            )}

            {/* Captions Tab */}
            {activeTab === "captions" && (
              <div>
                {captions.length === 0 ? (
                  <div
                    style={{
                      color: "#666",
                      textAlign: "center",
                      padding: 40,
                    }}
                  >
                    字幕データがありません。
                    <br />
                    クリップ生成後に字幕が表示されます。
                  </div>
                ) : (
                  <div
                    style={{
                      maxHeight: 300,
                      overflowY: "auto",
                      marginBottom: 16,
                    }}
                  >
                    {captions.map((cap, idx) => (
                      <div
                        key={idx}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "8px 0",
                          borderBottom: "1px solid #252540",
                        }}
                      >
                        <span
                          style={{
                            color: "#666",
                            fontSize: 11,
                            minWidth: 50,
                          }}
                        >
                          {formatTime(cap.start)}
                        </span>
                        {editingCaptionIdx === idx ? (
                          <input
                            type="text"
                            value={cap.text}
                            onChange={(e) =>
                              handleCaptionTextChange(idx, e.target.value)
                            }
                            onBlur={() => setEditingCaptionIdx(null)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") setEditingCaptionIdx(null);
                            }}
                            autoFocus
                            style={{
                              flex: 1,
                              padding: "6px 8px",
                              backgroundColor: "#252540",
                              border: "1px solid #FF6B35",
                              borderRadius: 4,
                              color: "#fff",
                              fontSize: 14,
                              outline: "none",
                            }}
                          />
                        ) : (
                          <span
                            onClick={() => setEditingCaptionIdx(idx)}
                            style={{
                              flex: 1,
                              color: cap.emphasis ? "#FFD700" : "#fff",
                              fontSize: cap.emphasis ? 16 : 14,
                              fontWeight: cap.emphasis ? 700 : 400,
                              cursor: "pointer",
                              padding: "4px 0",
                            }}
                          >
                            {cap.text}
                          </span>
                        )}
                        <button
                          onClick={() => handleCaptionEmphasisToggle(idx)}
                          title={cap.emphasis ? "強調を解除" : "強調する"}
                          style={{
                            background: cap.emphasis ? "#FFD700" : "#333",
                            border: "none",
                            borderRadius: 4,
                            padding: "4px 8px",
                            color: cap.emphasis ? "#000" : "#888",
                            fontSize: 11,
                            cursor: "pointer",
                            fontWeight: 600,
                          }}
                        >
                          {cap.emphasis ? "★" : "☆"}
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                {captions.length > 0 && (
                  <button
                    onClick={handleSaveCaptions}
                    disabled={isSavingCaptions}
                    style={{
                      width: "100%",
                      padding: "12px 0",
                      border: "none",
                      borderRadius: 8,
                      backgroundColor: "#4CAF50",
                      color: "#fff",
                      fontSize: 15,
                      fontWeight: 600,
                      cursor: "pointer",
                      opacity: isSavingCaptions ? 0.6 : 1,
                    }}
                  >
                    {isSavingCaptions ? "⏳ 保存中..." : "💾 字幕を保存"}
                  </button>
                )}
              </div>
            )}

            {/* Status message */}
            {statusMessage && (
              <div
                style={{
                  marginTop: 12,
                  padding: "10px 14px",
                  borderRadius: 8,
                  backgroundColor:
                    statusMessage.type === "success" ? "#1b3a1b" : "#3a1b1b",
                  color:
                    statusMessage.type === "success" ? "#4CAF50" : "#FF5722",
                  fontSize: 13,
                }}
              >
                {statusMessage.text}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "12px 20px",
            borderTop: "1px solid #333",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span style={{ color: "#666", fontSize: 12 }}>
            Phase {clip.phase_index ?? "?"} | Clip ID: {clip.clip_id?.slice(0, 8)}...
          </span>
          {clip.clip_url && (
            <a
              href={clip.clip_url}
              download
              style={{
                padding: "8px 20px",
                backgroundColor: "#6C5CE7",
                color: "#fff",
                borderRadius: 8,
                textDecoration: "none",
                fontSize: 14,
                fontWeight: 600,
              }}
            >
              📥 MP4ダウンロード
            </a>
          )}
        </div>
      </div>
    </div>
  );
};

const trimBtnStyle = {
  padding: "6px 12px",
  border: "1px solid #444",
  borderRadius: 6,
  backgroundColor: "#252540",
  color: "#ddd",
  fontSize: 13,
  cursor: "pointer",
};

export default LightningClipEditor;
