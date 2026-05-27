import { useState, useEffect, useMemo, useRef } from 'react';

/* ─────────────────────────────────────────────
   AI Video Editor Demo — AitherHub LP
   Simulates AI auto-editing a video in fast-forward
   Inspired by AitherHub's actual CLIP EDITOR UI
   ───────────────────────────────────────────── */

const FACE_IMAGE_URL = 'https://files.manuscdn.com/user_upload_by_module/session_file/310519663320462236/paaqFtiWCkPpYpMF.webp';

// AI processing phases
const AI_PHASES = [
  { label: 'AI 音声解析中...', icon: '🎙️', duration: 3000 },
  { label: '無音区間を検出中...', icon: '🔇', duration: 2500 },
  { label: 'カットポイント決定中...', icon: '✂️', duration: 2000 },
  { label: '字幕を自動生成中...', icon: '💬', duration: 3000 },
  { label: 'テロップスタイル適用中...', icon: '🎨', duration: 2000 },
  { label: 'フック最適化中...', icon: '🎯', duration: 1500 },
  { label: 'エクスポート準備完了', icon: '✅', duration: 2000 },
];

// Subtitle lines that appear during playback
const SUBTITLES = [
  { time: 0, text: '今日はめちゃくちゃいい商品紹介します' },
  { time: 12, text: 'これマジで売れてるやつなんですけど' },
  { time: 22, text: '見てくださいこの質感やばくないですか' },
  { time: 35, text: 'もう在庫残りわずかなんで' },
  { time: 45, text: '今買わないと絶対後悔しますよ' },
  { time: 55, text: 'リンクは概要欄に貼っておきます' },
  { time: 68, text: 'フォローしてくれた人には特別クーポン' },
  { time: 78, text: 'ありがとうございます！また明日！' },
];

// Timeline clips with colors
const TIMELINE_CLIPS = [
  { start: 0, end: 8, color: '#8b5cf6', label: 'Hook', type: 'keep' },
  { start: 8, end: 12, color: '#ef4444', label: '無音', type: 'cut' },
  { start: 12, end: 28, color: '#06b6d4', label: '商品紹介', type: 'keep' },
  { start: 28, end: 32, color: '#ef4444', label: '無音', type: 'cut' },
  { start: 32, end: 50, color: '#10b981', label: 'CTA', type: 'keep' },
  { start: 50, end: 53, color: '#ef4444', label: '無音', type: 'cut' },
  { start: 53, end: 72, color: '#f59e0b', label: '実演', type: 'keep' },
  { start: 72, end: 75, color: '#ef4444', label: '無音', type: 'cut' },
  { start: 75, end: 89, color: '#ec4899', label: 'クロージング', type: 'keep' },
];

// Waveform data (pre-generated)
function generateWaveform() {
  const bars = [];
  for (let i = 0; i < 180; i++) {
    // Create realistic-looking waveform with some silence gaps
    const isSilence = (i > 16 && i < 24) || (i > 56 && i < 64) || (i > 100 && i < 106) || (i > 144 && i < 150);
    bars.push(isSilence ? Math.random() * 0.08 : Math.random() * 0.7 + 0.3);
  }
  return bars;
}

export default function AIVideoEditorDemo() {
  const [currentTime, setCurrentTime] = useState(0);
  const [phaseIndex, setPhaseIndex] = useState(0);
  const [isProcessing, setIsProcessing] = useState(true);
  const [showCuts, setShowCuts] = useState(false);
  const [showSubtitles, setShowSubtitles] = useState(false);
  const [activeTab, setActiveTab] = useState(0);
  const [cutAnimation, setCutAnimation] = useState(null);
  const totalDuration = 89; // seconds
  const waveform = useMemo(() => generateWaveform(), []);
  const containerRef = useRef(null);
  const [isVisible, setIsVisible] = useState(false);

  // Intersection observer for triggering animation when visible
  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) setIsVisible(true); },
      { threshold: 0.3 }
    );
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // AI Phase progression
  useEffect(() => {
    if (!isVisible) return;
    let timeout;
    const advancePhase = () => {
      setPhaseIndex(prev => {
        const next = prev + 1;
        if (next >= AI_PHASES.length) {
          setIsProcessing(false);
          return prev;
        }
        // Trigger visual effects at certain phases
        if (next === 3) setShowCuts(true);
        if (next === 4) setShowSubtitles(true);
        if (next === 4) setActiveTab(1);
        if (next === 5) setActiveTab(2);
        timeout = setTimeout(advancePhase, AI_PHASES[next].duration);
        return next;
      });
    };
    timeout = setTimeout(advancePhase, AI_PHASES[0].duration);
    return () => clearTimeout(timeout);
  }, [isVisible]);

  // Playhead animation (fast-forward feel - 6x speed)
  useEffect(() => {
    if (!isVisible) return;
    const interval = setInterval(() => {
      setCurrentTime(prev => {
        const next = prev + 0.15; // Fast forward
        return next >= totalDuration ? 0 : next;
      });
    }, 30);
    return () => clearInterval(interval);
  }, [isVisible]);

  // Cut animation trigger
  useEffect(() => {
    if (!showCuts) return;
    const cutClips = TIMELINE_CLIPS.filter(c => c.type === 'cut');
    let i = 0;
    const interval = setInterval(() => {
      if (i < cutClips.length) {
        setCutAnimation(cutClips[i]);
        setTimeout(() => setCutAnimation(null), 600);
        i++;
      } else {
        clearInterval(interval);
      }
    }, 800);
    return () => clearInterval(interval);
  }, [showCuts]);

  const currentSubtitle = SUBTITLES.filter(s => s.time <= currentTime).pop();
  const progressPercent = (currentTime / totalDuration) * 100;
  const currentPhase = AI_PHASES[phaseIndex];

  const tabs = ['字幕', 'AI分析', 'Trim', '評価'];

  return (
    <div ref={containerRef} style={{
      background: 'linear-gradient(180deg, #0f0a1e 0%, #1a1035 100%)',
      border: '1px solid rgba(139, 92, 246, 0.3)',
      borderRadius: '16px',
      overflow: 'hidden',
      boxShadow: '0 0 60px rgba(139, 92, 246, 0.15), inset 0 1px 0 rgba(255,255,255,0.05)',
      opacity: isVisible ? 1 : 0,
      transform: isVisible ? 'translateY(0)' : 'translateY(30px)',
      transition: 'opacity 0.8s ease, transform 0.8s ease',
    }}>
      {/* ═══ TOP BAR ═══ */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '10px 16px',
        background: 'rgba(0,0,0,0.4)',
        borderBottom: '1px solid rgba(139, 92, 246, 0.2)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ color: '#a78bfa', fontSize: '13px', fontWeight: '700', letterSpacing: '1px' }}>← CLIP EDITOR</span>
          <span style={{
            padding: '2px 8px',
            background: 'rgba(139, 92, 246, 0.2)',
            border: '1px solid rgba(139, 92, 246, 0.4)',
            borderRadius: '4px',
            fontSize: '11px',
            color: '#c4b5fd',
            fontFamily: 'monospace',
          }}>Phase 2 | 0:00 - 1:29</span>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button style={{
            padding: '5px 12px',
            background: 'linear-gradient(135deg, #f59e0b, #d97706)',
            border: 'none',
            borderRadius: '6px',
            color: '#fff',
            fontSize: '11px',
            fontWeight: '600',
            cursor: 'pointer',
          }}>字幕付き Export</button>
          <button style={{
            padding: '5px 12px',
            background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
            border: 'none',
            borderRadius: '6px',
            color: '#fff',
            fontSize: '11px',
            fontWeight: '600',
            cursor: 'pointer',
          }}>Export MP4</button>
        </div>
      </div>

      {/* ═══ MAIN CONTENT ═══ */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: '0',
        minHeight: '320px',
      }}>
        {/* LEFT: Video Preview */}
        <div style={{
          position: 'relative',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '16px',
          background: '#000',
          borderRight: '1px solid rgba(139, 92, 246, 0.15)',
        }}>
          {/* Video frame */}
          <div style={{
            position: 'relative',
            width: '180px',
            height: '320px',
            borderRadius: '12px',
            overflow: 'hidden',
            boxShadow: '0 0 30px rgba(139, 92, 246, 0.3)',
            border: '2px solid rgba(139, 92, 246, 0.4)',
          }}>
            <img
              src={FACE_IMAGE_URL}
              alt="Live commerce preview"
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                objectPosition: 'center top',
              }}
            />
            {/* Subtitle overlay */}
            {showSubtitles && currentSubtitle && (
              <div style={{
                position: 'absolute',
                bottom: '20px',
                left: '8px',
                right: '8px',
                textAlign: 'center',
                animation: 'subtitlePop 0.3s ease',
              }}>
                <span style={{
                  display: 'inline-block',
                  padding: '4px 10px',
                  background: 'rgba(0,0,0,0.75)',
                  borderRadius: '4px',
                  color: '#fff',
                  fontSize: '11px',
                  fontWeight: '600',
                  textShadow: '0 1px 2px rgba(0,0,0,0.5)',
                  border: '1px solid rgba(255,255,255,0.1)',
                }}>
                  {currentSubtitle.text}
                </span>
              </div>
            )}
            {/* Phase indicator */}
            <div style={{
              position: 'absolute',
              top: '8px',
              left: '8px',
              padding: '2px 6px',
              background: 'rgba(0,0,0,0.7)',
              borderRadius: '4px',
              fontSize: '9px',
              color: '#94a3b8',
            }}>
              0:00 – 1:29 Phase 1
            </div>
          </div>

          {/* Time display */}
          <div style={{
            position: 'absolute',
            bottom: '12px',
            left: '50%',
            transform: 'translateX(-50%)',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '11px',
            color: '#64748b',
            fontFamily: 'monospace',
          }}>
            <span style={{ color: '#22d3ee' }}>{formatTime(currentTime)}</span>
            <span>/</span>
            <span>1:29</span>
            <span style={{
              marginLeft: '8px',
              padding: '1px 5px',
              background: 'rgba(34, 211, 238, 0.15)',
              border: '1px solid rgba(34, 211, 238, 0.3)',
              borderRadius: '3px',
              color: '#22d3ee',
              fontSize: '9px',
            }}>6x</span>
          </div>
        </div>

        {/* RIGHT: Settings Panel */}
        <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {/* Tabs */}
          <div style={{ display: 'flex', gap: '0', borderBottom: '1px solid rgba(139, 92, 246, 0.2)' }}>
            {tabs.map((tab, i) => (
              <div key={i} style={{
                padding: '8px 14px',
                fontSize: '12px',
                fontWeight: activeTab === i ? '600' : '400',
                color: activeTab === i ? '#a78bfa' : '#64748b',
                borderBottom: activeTab === i ? '2px solid #a78bfa' : '2px solid transparent',
                cursor: 'pointer',
                transition: 'all 0.3s',
              }}>{tab}</div>
            ))}
          </div>

          {/* AI Status */}
          <div style={{
            padding: '10px 12px',
            background: isProcessing
              ? 'linear-gradient(135deg, rgba(139, 92, 246, 0.1), rgba(34, 211, 238, 0.05))'
              : 'rgba(16, 185, 129, 0.1)',
            border: `1px solid ${isProcessing ? 'rgba(139, 92, 246, 0.3)' : 'rgba(16, 185, 129, 0.3)'}`,
            borderRadius: '8px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '14px' }}>{currentPhase.icon}</span>
              <span style={{
                fontSize: '12px',
                color: isProcessing ? '#c4b5fd' : '#6ee7b7',
                fontWeight: '500',
              }}>
                {currentPhase.label}
              </span>
              {isProcessing && (
                <div style={{
                  marginLeft: 'auto',
                  width: '12px',
                  height: '12px',
                  border: '2px solid rgba(139, 92, 246, 0.3)',
                  borderTop: '2px solid #a78bfa',
                  borderRadius: '50%',
                  animation: 'spin 0.6s linear infinite',
                }} />
              )}
            </div>
            {/* Progress bar */}
            <div style={{
              marginTop: '8px',
              height: '3px',
              background: 'rgba(139, 92, 246, 0.2)',
              borderRadius: '2px',
              overflow: 'hidden',
            }}>
              <div style={{
                height: '100%',
                width: `${((phaseIndex + 1) / AI_PHASES.length) * 100}%`,
                background: 'linear-gradient(90deg, #8b5cf6, #22d3ee)',
                borderRadius: '2px',
                transition: 'width 0.5s ease',
              }} />
            </div>
          </div>

          {/* Subtitle Style Section */}
          <div>
            <div style={{ fontSize: '11px', color: '#94a3b8', marginBottom: '6px', fontWeight: '500' }}>字幕スタイル</div>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {['シンプル', 'ボックス', '縁取り', 'ポップ', 'グラデーション'].map((style, i) => (
                <div key={i} style={{
                  padding: '4px 10px',
                  fontSize: '10px',
                  background: i === 0 ? 'rgba(245, 158, 11, 0.15)' : 'rgba(255,255,255,0.03)',
                  border: `1px solid ${i === 0 ? 'rgba(245, 158, 11, 0.5)' : 'rgba(255,255,255,0.1)'}`,
                  borderRadius: '4px',
                  color: i === 0 ? '#fbbf24' : '#64748b',
                  position: 'relative',
                }}>
                  {style}
                  {i === 0 && <span style={{
                    position: 'absolute',
                    top: '-4px',
                    right: '-4px',
                    fontSize: '7px',
                    padding: '1px 3px',
                    background: '#f59e0b',
                    borderRadius: '2px',
                    color: '#000',
                    fontWeight: '700',
                  }}>AI</span>}
                </div>
              ))}
            </div>
          </div>

          {/* AI Recommendation */}
          <div style={{
            padding: '8px 10px',
            background: 'rgba(245, 158, 11, 0.05)',
            border: '1px solid rgba(245, 158, 11, 0.2)',
            borderRadius: '6px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
              <span style={{ fontSize: '11px' }}>✨</span>
              <span style={{ fontSize: '10px', color: '#fbbf24', fontWeight: '600' }}>AIおすすめ</span>
              <span style={{ fontSize: '9px', color: '#94a3b8' }}>パーソナライズ済み (60%)</span>
            </div>
            <div style={{ fontSize: '9px', color: '#64748b', lineHeight: '1.5' }}>
              シンプル — あなたが最もよく使うスタイル（1件のフィードバックに基づく）
            </div>
          </div>

          {/* Font size slider */}
          <div>
            <div style={{ fontSize: '11px', color: '#94a3b8', marginBottom: '6px' }}>フォントサイズ</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '10px', color: '#64748b' }}>A</span>
              <div style={{
                flex: 1,
                height: '3px',
                background: 'rgba(245, 158, 11, 0.2)',
                borderRadius: '2px',
                position: 'relative',
              }}>
                <div style={{
                  position: 'absolute',
                  left: '40%',
                  top: '-4px',
                  width: '10px',
                  height: '10px',
                  background: '#f59e0b',
                  borderRadius: '50%',
                  boxShadow: '0 0 6px rgba(245, 158, 11, 0.5)',
                }} />
              </div>
              <span style={{ fontSize: '13px', color: '#64748b' }}>A</span>
              <span style={{
                padding: '2px 6px',
                background: 'rgba(255,255,255,0.05)',
                borderRadius: '3px',
                fontSize: '10px',
                color: '#94a3b8',
                fontFamily: 'monospace',
              }}>16</span>
            </div>
          </div>
        </div>
      </div>

      {/* ═══ TIMELINE SECTION ═══ */}
      <div style={{
        padding: '8px 16px 12px',
        background: 'rgba(0,0,0,0.3)',
        borderTop: '1px solid rgba(139, 92, 246, 0.15)',
      }}>
        {/* Transport controls */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '12px',
          marginBottom: '8px',
        }}>
          <span style={{ fontSize: '10px', color: '#64748b', cursor: 'pointer' }}>-5s</span>
          <div style={{
            width: '28px',
            height: '28px',
            borderRadius: '50%',
            background: 'linear-gradient(135deg, #f59e0b, #d97706)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 0 12px rgba(245, 158, 11, 0.4)',
          }}>
            <span style={{ fontSize: '10px', color: '#fff' }}>⏸</span>
          </div>
          <span style={{ fontSize: '10px', color: '#64748b', cursor: 'pointer' }}>+5s</span>
          <span style={{
            marginLeft: '12px',
            fontSize: '11px',
            color: '#22d3ee',
            fontFamily: 'monospace',
          }}>{formatTime(currentTime)} / 1:29</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: '4px' }}>
            {['1x', '1.5x', '2x'].map((speed, i) => (
              <span key={i} style={{
                padding: '2px 6px',
                fontSize: '9px',
                background: i === 0 ? 'rgba(245, 158, 11, 0.2)' : 'transparent',
                border: `1px solid ${i === 0 ? 'rgba(245, 158, 11, 0.5)' : 'rgba(255,255,255,0.1)'}`,
                borderRadius: '3px',
                color: i === 0 ? '#fbbf24' : '#64748b',
              }}>{speed}</span>
            ))}
          </div>
        </div>

        {/* Waveform + Timeline */}
        <div style={{ position: 'relative', height: '50px', borderRadius: '6px', overflow: 'hidden' }}>
          {/* Waveform bars */}
          <div style={{
            display: 'flex',
            alignItems: 'flex-end',
            height: '100%',
            gap: '1px',
            position: 'relative',
          }}>
            {waveform.map((h, i) => {
              const barPosition = (i / waveform.length) * 100;
              const clip = TIMELINE_CLIPS.find(c => {
                const clipStart = (c.start / totalDuration) * 100;
                const clipEnd = (c.end / totalDuration) * 100;
                return barPosition >= clipStart && barPosition <= clipEnd;
              });
              const isCut = clip && clip.type === 'cut' && showCuts;
              const isPassed = barPosition < progressPercent;
              
              let barColor;
              if (isCut) {
                barColor = 'rgba(239, 68, 68, 0.6)';
              } else if (isPassed) {
                barColor = clip ? clip.color : '#6366f1';
              } else {
                barColor = 'rgba(100, 116, 139, 0.3)';
              }

              return (
                <div key={i} style={{
                  flex: 1,
                  height: `${h * 100}%`,
                  background: barColor,
                  borderRadius: '1px 1px 0 0',
                  transition: 'background 0.2s, opacity 0.3s',
                  opacity: isCut ? 0.4 : 1,
                  position: 'relative',
                }}>
                  {/* Cut line animation */}
                  {isCut && cutAnimation && clip === cutAnimation && (
                    <div style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      right: 0,
                      bottom: 0,
                      background: 'rgba(239, 68, 68, 0.8)',
                      animation: 'cutFlash 0.6s ease',
                    }} />
                  )}
                </div>
              );
            })}
          </div>

          {/* Playhead */}
          <div style={{
            position: 'absolute',
            left: `${progressPercent}%`,
            top: 0,
            bottom: 0,
            width: '2px',
            background: '#fff',
            boxShadow: '0 0 8px rgba(255,255,255,0.8), 0 0 16px rgba(34, 211, 238, 0.5)',
            zIndex: 10,
            transition: 'left 0.03s linear',
          }}>
            {/* Playhead top marker */}
            <div style={{
              position: 'absolute',
              top: '-3px',
              left: '-4px',
              width: '10px',
              height: '6px',
              background: '#fff',
              borderRadius: '2px',
              boxShadow: '0 0 4px rgba(255,255,255,0.5)',
            }} />
          </div>

          {/* Cut markers overlay */}
          {showCuts && TIMELINE_CLIPS.filter(c => c.type === 'cut').map((clip, i) => (
            <div key={i} style={{
              position: 'absolute',
              left: `${(clip.start / totalDuration) * 100}%`,
              width: `${((clip.end - clip.start) / totalDuration) * 100}%`,
              top: 0,
              bottom: 0,
              background: 'repeating-linear-gradient(45deg, transparent, transparent 3px, rgba(239, 68, 68, 0.1) 3px, rgba(239, 68, 68, 0.1) 6px)',
              borderLeft: '1px solid rgba(239, 68, 68, 0.5)',
              borderRight: '1px solid rgba(239, 68, 68, 0.5)',
              pointerEvents: 'none',
            }} />
          ))}
        </div>

        {/* Clip labels below timeline */}
        <div style={{ position: 'relative', height: '20px', marginTop: '4px' }}>
          {TIMELINE_CLIPS.filter(c => c.type === 'keep').map((clip, i) => (
            <div key={i} style={{
              position: 'absolute',
              left: `${(clip.start / totalDuration) * 100}%`,
              width: `${((clip.end - clip.start) / totalDuration) * 100}%`,
              top: 0,
              height: '16px',
              background: `${clip.color}22`,
              border: `1px solid ${clip.color}55`,
              borderRadius: '3px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '8px',
              color: clip.color,
              fontWeight: '600',
              overflow: 'hidden',
              whiteSpace: 'nowrap',
            }}>
              {clip.label}
            </div>
          ))}
        </div>

        {/* Bottom status bar */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginTop: '8px',
          padding: '4px 8px',
          background: 'rgba(0,0,0,0.3)',
          borderRadius: '4px',
          fontSize: '9px',
          color: '#64748b',
          fontFamily: 'monospace',
        }}>
          <div style={{ display: 'flex', gap: '12px' }}>
            <span>Space: 再生/停止</span>
            <span>Del: セグメント削除</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ color: '#ef4444' }}>赤 = 無音区間 ({TIMELINE_CLIPS.filter(c => c.type === 'cut').length}箇所)</span>
            <span>|</span>
            <span style={{ color: '#10b981' }}>
              {showCuts ? '✓ カット済み' : '解析中...'}
            </span>
          </div>
        </div>
      </div>

      {/* CSS Animations */}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        @keyframes subtitlePop {
          from { transform: translateY(5px); opacity: 0; }
          to { transform: translateY(0); opacity: 1; }
        }
        @keyframes cutFlash {
          0% { opacity: 0; transform: scaleX(0); }
          50% { opacity: 1; transform: scaleX(1.2); }
          100% { opacity: 0; transform: scaleX(1); }
        }
      `}</style>
    </div>
  );
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}
