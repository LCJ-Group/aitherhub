import React, { useState, useEffect, useCallback, useRef } from 'react';
import VideoService from '../base/services/videoService';

/**
 * ClipFeedbackPanel — Human-in-the-Loop Feedback UI
 * 
 * Displays under each clip candidate / Lightning Editor:
 *   ① Quick Rating: 👍 Good Clip / 👎 Needs Fix
 *   ② Reason Tags: hook_weak, too_long, cut_position, subtitle, etc.
 *   ③ Sales Confirmation: "Is this the selling moment?" YES / NO
 */

const REASON_TAGS = [
  { key: 'hook_weak', label: 'フック弱い', emoji: '🎣' },
  { key: 'too_long', label: '長すぎ', emoji: '⏱️' },
  { key: 'too_short', label: '短すぎ', emoji: '⚡' },
  { key: 'cut_position', label: 'カット位置', emoji: '✂️' },
  { key: 'subtitle', label: '字幕', emoji: '💬' },
  { key: 'audio', label: '音声', emoji: '🔊' },
  { key: 'irrelevant', label: '関係ない', emoji: '❌' },
  { key: 'perfect', label: '完璧', emoji: '✨' },
];

const ClipFeedbackPanel = ({
  videoId,
  phaseIndex,
  timeStart,
  timeEnd,
  clipId = null,
  aiScore = null,
  scoreBreakdown = null,
  onFeedbackSubmitted = () => {},
  compact = false,
}) => {
  const [rating, setRating] = useState(null); // 'good' | 'bad' | null
  const [selectedReasons, setSelectedReasons] = useState([]);
  const [salesConfirm, setSalesConfirm] = useState(null); // true | false | null
  const [salesNote, setSalesNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [salesSubmitted, setSalesSubmitted] = useState(false);
  const [error, setError] = useState(null);

  // Load existing feedback on mount
  useEffect(() => {
    if (!videoId) return;
    const loadExisting = async () => {
      try {
        const ratingsResp = await VideoService.getClipRatings(videoId);
        if (ratingsResp?.ratings) {
          const existing = ratingsResp.ratings.find(r => r.phase_index === phaseIndex);
          if (existing) {
            setRating(existing.rating);
            setSelectedReasons(existing.reason_tags || []);
            setSubmitted(true);
          }
        }
      } catch (e) {
        // Ignore load errors
      }
      try {
        const salesResp = await VideoService.getSalesConfirmations(videoId);
        if (salesResp?.confirmations) {
          const existing = salesResp.confirmations.find(c => c.phase_index === phaseIndex);
          if (existing) {
            setSalesConfirm(existing.is_sales_moment);
            if (existing.note) setSalesNote(existing.note);
            setSalesSubmitted(true);
          }
        }
      } catch (e) {
        // Ignore load errors
      }
    };
    loadExisting();
  }, [videoId, phaseIndex]);

  const handleRating = useCallback(async (newRating) => {
    setRating(newRating);
    setError(null);
    setSubmitting(true);
    try {
      await VideoService.submitClipRating(videoId, {
        phase_index: phaseIndex,
        time_start: timeStart,
        time_end: timeEnd,
        rating: newRating,
        reason_tags: selectedReasons.length > 0 ? selectedReasons : null,
        clip_id: clipId,
        ai_score_at_feedback: aiScore,
        score_breakdown: scoreBreakdown,
      });
      setSubmitted(true);
      onFeedbackSubmitted({ type: 'rating', rating: newRating, reasons: selectedReasons });
    } catch (e) {
      setError('評価の保存に失敗しました');
    } finally {
      setSubmitting(false);
    }
  }, [videoId, phaseIndex, timeStart, timeEnd, clipId, aiScore, scoreBreakdown, selectedReasons, onFeedbackSubmitted]);

  const toggleReason = useCallback((key) => {
    setSelectedReasons(prev => {
      const next = prev.includes(key) ? prev.filter(r => r !== key) : [...prev, key];
      // Auto-save if rating already set
      if (rating) {
        VideoService.submitClipRating(videoId, {
          phase_index: phaseIndex,
          time_start: timeStart,
          time_end: timeEnd,
          rating,
          reason_tags: next.length > 0 ? next : null,
          clip_id: clipId,
          ai_score_at_feedback: aiScore,
          score_breakdown: scoreBreakdown,
        }).catch(() => {});
      }
      return next;
    });
  }, [rating, videoId, phaseIndex, timeStart, timeEnd, clipId, aiScore, scoreBreakdown]);

  // Use ref to always get latest salesNote in callbacks
  const salesNoteRef = useRef(salesNote);
  useEffect(() => { salesNoteRef.current = salesNote; }, [salesNote]);

  const handleSalesConfirmation = useCallback(async (isSalesMoment, noteOverride) => {
    setSalesConfirm(isSalesMoment);
    setError(null);
    const currentNote = noteOverride !== undefined ? noteOverride : salesNoteRef.current;
    try {
      await VideoService.submitSalesConfirmation(videoId, {
        phase_index: phaseIndex,
        time_start: timeStart,
        time_end: timeEnd,
        is_sales_moment: isSalesMoment,
        clip_id: clipId,
        note: currentNote || null,
      });
      setSalesSubmitted(true);
      onFeedbackSubmitted({ type: 'sales_confirmation', is_sales_moment: isSalesMoment });
    } catch (e) {
      setError('確認の保存に失敗しました');
    }
  }, [videoId, phaseIndex, timeStart, timeEnd, clipId, onFeedbackSubmitted]);

  // Debounced auto-save for salesNote changes
  const saveTimerRef = useRef(null);
  useEffect(() => {
    if (salesConfirm === null || !salesNote) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      handleSalesConfirmation(salesConfirm, salesNote);
    }, 1000);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [salesNote, salesConfirm, handleSalesConfirmation]);

  // Compact mode: just rating buttons
  if (compact) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: '8px',
        padding: '4px 0',
      }}>
        <button
          onClick={() => handleRating('good')}
          disabled={submitting}
          style={{
            padding: '4px 12px', borderRadius: '16px', border: 'none',
            cursor: 'pointer', fontSize: '13px', fontWeight: 600,
            background: rating === 'good' ? '#10b981' : '#f3f4f6',
            color: rating === 'good' ? '#fff' : '#374151',
            transition: 'all 0.2s',
          }}
        >
          👍 {rating === 'good' && submitted ? '使える' : 'Good'}
        </button>
        <button
          onClick={() => handleRating('bad')}
          disabled={submitting}
          style={{
            padding: '4px 12px', borderRadius: '16px', border: 'none',
            cursor: 'pointer', fontSize: '13px', fontWeight: 600,
            background: rating === 'bad' ? '#ef4444' : '#f3f4f6',
            color: rating === 'bad' ? '#fff' : '#374151',
            transition: 'all 0.2s',
          }}
        >
          👎 {rating === 'bad' && submitted ? '微妙' : 'Fix'}
        </button>
      </div>
    );
  }

  return (
    <div style={{
      background: '#f9fafb', borderRadius: '12px', padding: '16px',
      border: '1px solid #e5e7eb', marginTop: '12px',
    }}>
      {/* Header */}
      <div style={{
        fontSize: '13px', fontWeight: 700, color: '#374151',
        marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px',
      }}>
        <span style={{ fontSize: '16px' }}>🔄</span>
        クリップの評価
        {submitted && (
          <span style={{
            fontSize: '11px', background: '#d1fae5', color: '#065f46',
            padding: '2px 8px', borderRadius: '10px',
          }}>
            保存済み
          </span>
        )}
      </div>

      {/* ① Quick Rating */}
      <div style={{
        display: 'flex', gap: '8px', marginBottom: '12px',
      }}>
        <button
          onClick={() => handleRating('good')}
          disabled={submitting}
          style={{
            flex: 1, padding: '10px 16px', borderRadius: '10px',
            border: rating === 'good' ? '2px solid #10b981' : '2px solid #e5e7eb',
            cursor: 'pointer', fontSize: '14px', fontWeight: 700,
            background: rating === 'good' ? '#d1fae5' : '#fff',
            color: rating === 'good' ? '#065f46' : '#374151',
            transition: 'all 0.2s',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
          }}
        >
          <span style={{ fontSize: '20px' }}>👍</span>
          使えるクリップ
        </button>
        <button
          onClick={() => handleRating('bad')}
          disabled={submitting}
          style={{
            flex: 1, padding: '10px 16px', borderRadius: '10px',
            border: rating === 'bad' ? '2px solid #ef4444' : '2px solid #e5e7eb',
            cursor: 'pointer', fontSize: '14px', fontWeight: 700,
            background: rating === 'bad' ? '#fee2e2' : '#fff',
            color: rating === 'bad' ? '#991b1b' : '#374151',
            transition: 'all 0.2s',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
          }}
        >
          <span style={{ fontSize: '20px' }}>👎</span>
          微妙なクリップ
        </button>
      </div>

      {/* ② Reason Tags (show after rating) */}
      {rating && (
        <div style={{ marginBottom: '12px' }}>
          <div style={{
            fontSize: '12px', color: '#6b7280', marginBottom: '8px', fontWeight: 600,
          }}>
            {rating === 'bad' ? 'なぜ微妙？（複数選択可）' : 'どこが良い？（任意）'}
          </div>
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: '6px',
          }}>
            {REASON_TAGS.map(tag => {
              // Show "perfect" only for good rating, hide for bad
              if (tag.key === 'perfect' && rating === 'bad') return null;
              // Show problem tags only for bad rating
              if (['hook_weak', 'too_long', 'too_short', 'cut_position', 'subtitle', 'audio', 'irrelevant'].includes(tag.key) && rating === 'good' && tag.key !== 'perfect') {
                // Still show for good rating but less prominently
              }
              const isSelected = selectedReasons.includes(tag.key);
              return (
                <button
                  key={tag.key}
                  onClick={() => toggleReason(tag.key)}
                  style={{
                    padding: '4px 10px', borderRadius: '16px',
                    border: isSelected ? '2px solid #6366f1' : '1px solid #d1d5db',
                    background: isSelected ? '#eef2ff' : '#fff',
                    color: isSelected ? '#4338ca' : '#6b7280',
                    fontSize: '12px', cursor: 'pointer',
                    fontWeight: isSelected ? 600 : 400,
                    transition: 'all 0.15s',
                  }}
                >
                  {tag.emoji} {tag.label}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* ③ Sales Confirmation */}
      <div style={{
        borderTop: '1px solid #e5e7eb', paddingTop: '12px',
      }}>
        <div style={{
          fontSize: '13px', fontWeight: 700, color: '#374151',
          marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px',
        }}>
          <span style={{ fontSize: '16px' }}>💰</span>
          このクリップは売れた理由の部分ですか？
          {salesSubmitted && (
            <span style={{
              fontSize: '11px', background: '#fef3c7', color: '#92400e',
              padding: '2px 8px', borderRadius: '10px',
            }}>
              Sales DNA {salesConfirm ? '✓' : '✗'}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
          <button
            onClick={() => handleSalesConfirmation(true)}
            style={{
              flex: 1, padding: '8px 12px', borderRadius: '8px',
              border: salesConfirm === true ? '2px solid #f59e0b' : '2px solid #e5e7eb',
              background: salesConfirm === true ? '#fef3c7' : '#fff',
              color: salesConfirm === true ? '#92400e' : '#374151',
              cursor: 'pointer', fontSize: '13px', fontWeight: 600,
              transition: 'all 0.2s',
            }}
          >
            ✅ YES — 売れた瞬間
          </button>
          <button
            onClick={() => handleSalesConfirmation(false)}
            style={{
              flex: 1, padding: '8px 12px', borderRadius: '8px',
              border: salesConfirm === false ? '2px solid #6b7280' : '2px solid #e5e7eb',
              background: salesConfirm === false ? '#f3f4f6' : '#fff',
              color: salesConfirm === false ? '#374151' : '#6b7280',
              cursor: 'pointer', fontSize: '13px', fontWeight: 600,
              transition: 'all 0.2s',
            }}
          >
            ❌ NO — 違う部分
          </button>
        </div>
        {salesConfirm !== null && (
          <input
            type="text"
            value={salesNote}
            onChange={(e) => setSalesNote(e.target.value)}
            placeholder="メモ（任意）: 例「商品紹介の瞬間」"
            style={{
              width: '100%', padding: '6px 10px', borderRadius: '6px',
              border: '1px solid #d1d5db', fontSize: '12px',
              background: '#fff', color: '#374151',
              boxSizing: 'border-box',
            }}
          />
        )}
      </div>

      {/* Error */}
      {error && (
        <div style={{
          marginTop: '8px', padding: '6px 10px', borderRadius: '6px',
          background: '#fee2e2', color: '#991b1b', fontSize: '12px',
        }}>
          {error}
        </div>
      )}
    </div>
  );
};

export default ClipFeedbackPanel;
