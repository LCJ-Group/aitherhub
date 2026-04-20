import React, { useState, useEffect, useCallback, useRef } from 'react';
import VideoService from '../base/services/videoService';
import { useTranslation } from 'react-i18next';

/**
 * ClipFeedbackPanel — Human-in-the-Loop Feedback UI
 * 
 * Displays under each clip candidate / Lightning Editor:
 *   ① Quick Rating: 👍 Good Clip / 👎 Needs Fix
 *   ② Reason Tags: hook_weak, too_long, cut_position, subtitle, etc.
 *   ③ Sales Confirmation: "Is this the selling moment?" YES / NO
 *   ④ Explicit SAVE button to persist all selections
 */

const REASON_TAGS = [
  { key: 'hook_weak', label: window.__t('auto_322', 'フック弱い'), emoji: '🎣' },
  { key: 'too_long', label: window.__t('auto_354', '長すぎ'), emoji: '⏱️' },
  { key: 'too_short', label: window.__t('auto_350', '短すぎ'), emoji: '⚡' },
  { key: 'cut_position', label: window.__t('auto_313', 'カット位置'), emoji: '✂️' },
  { key: 'subtitle', label: window.__t('auto_334', '字幕'), emoji: '💬' },
  { key: 'audio', label: window.__t('auto_356', '音声'), emoji: '🔊' },
  { key: 'irrelevant', label: window.__t('auto_355', '関係ない'), emoji: '❌' },
  { key: 'perfect', label: window.__t('auto_337', '完璧'), emoji: '✨' },
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
  useTranslation(); // triggers re-render on language change
  const [rating, setRating] = useState(null); // 'good' | 'bad' | null
  const [selectedReasons, setSelectedReasons] = useState([]);
  const [salesConfirm, setSalesConfirm] = useState(null); // true | false | null
  const [salesNote, setSalesNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [salesSubmitted, setSalesSubmitted] = useState(false);
  const [error, setError] = useState(null);
  const [successMsg, setSuccessMsg] = useState(null);
  // Track if user has unsaved changes
  const [dirty, setDirty] = useState(false);
  // Store initial loaded values to detect changes
  const loadedRef = useRef({ rating: null, salesConfirm: null, reasons: [] });

  // Load existing feedback on mount
  useEffect(() => {
    if (!videoId) return;
    const loadExisting = async () => {
      try {
        const ratingsResp = await VideoService.getClipRatings(videoId);
        if (ratingsResp?.ratings) {
          // Use String comparison to avoid type mismatch (API returns string, prop may be number)
          const existing = ratingsResp.ratings.find(r => String(r.phase_index) === String(phaseIndex));
          if (existing) {
            setRating(existing.rating);
            setSelectedReasons(existing.reason_tags || []);
            setSubmitted(true);
            loadedRef.current.rating = existing.rating;
            loadedRef.current.reasons = existing.reason_tags || [];
          }
        }
      } catch (e) {
        console.warn('[Feedback] Failed to load ratings from API:', e);
      }
      try {
        const salesResp = await VideoService.getSalesConfirmations(videoId);
        if (salesResp?.confirmations) {
          const existing = salesResp.confirmations.find(c => String(c.phase_index) === String(phaseIndex));
          if (existing) {
            setSalesConfirm(existing.is_sales_moment);
            if (existing.note) setSalesNote(existing.note);
            setSalesSubmitted(true);
            loadedRef.current.salesConfirm = existing.is_sales_moment;
          }
        }
      } catch (e) {
        console.warn('[Feedback] Failed to load sales confirmations from API:', e);
      }
      // Fallback: restore from localStorage if API returned nothing
      const lsKey = `clipFeedback_${videoId}_${phaseIndex}`;
      try {
        const cached = localStorage.getItem(lsKey);
        if (cached) {
          const fb = JSON.parse(cached);
          if (!rating && fb.rating) {
            setRating(fb.rating);
            setSelectedReasons(fb.selectedReasons || []);
            setSubmitted(true);
            loadedRef.current.rating = fb.rating;
            loadedRef.current.reasons = fb.selectedReasons || [];
          }
          if (salesConfirm === null && fb.salesConfirm != null) {
            setSalesConfirm(fb.salesConfirm);
            if (fb.salesNote) setSalesNote(fb.salesNote);
            setSalesSubmitted(true);
            loadedRef.current.salesConfirm = fb.salesConfirm;
          }
        }
      } catch (lsErr) {
        // localStorage unavailable
      }
    };
    loadExisting();
  }, [videoId, phaseIndex]);

  // Mark dirty when user changes anything
  const handleRatingSelect = useCallback((newRating) => {
    setRating(newRating);
    setDirty(true);
    setError(null);
    setSuccessMsg(null);
  }, []);

  const toggleReason = useCallback((key) => {
    setSelectedReasons(prev => {
      const next = prev.includes(key) ? prev.filter(r => r !== key) : [...prev, key];
      return next;
    });
    setDirty(true);
    setSuccessMsg(null);
  }, []);

  const handleSalesSelect = useCallback((isSalesMoment) => {
    setSalesConfirm(isSalesMoment);
    setDirty(true);
    setError(null);
    setSuccessMsg(null);
  }, []);

  const handleSalesNoteChange = useCallback((e) => {
    setSalesNote(e.target.value);
    setDirty(true);
    setSuccessMsg(null);
  }, []);

  // ─── SAVE ALL ───
  const handleSaveAll = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    setSuccessMsg(null);

    let ratingOk = true;
    let salesOk = true;

    // Save rating if set
    if (rating) {
      try {
        await VideoService.submitClipRating(videoId, {
          phase_index: phaseIndex,
          time_start: timeStart,
          time_end: timeEnd,
          rating,
          reason_tags: selectedReasons.length > 0 ? selectedReasons : null,
          clip_id: clipId,
          ai_score_at_feedback: aiScore,
          score_breakdown: scoreBreakdown,
        });
        setSubmitted(true);
        loadedRef.current.rating = rating;
        loadedRef.current.reasons = [...selectedReasons];
      } catch (e) {
        ratingOk = false;
      }
    }

    // Save sales confirmation if set
    if (salesConfirm !== null) {
      try {
        await VideoService.submitSalesConfirmation(videoId, {
          phase_index: phaseIndex,
          time_start: timeStart,
          time_end: timeEnd,
          is_sales_moment: salesConfirm,
          clip_id: clipId,
          note: salesNote || null,
        });
        setSalesSubmitted(true);
        loadedRef.current.salesConfirm = salesConfirm;
      } catch (e) {
        salesOk = false;
      }
    }

    setSubmitting(false);

    // Backup to localStorage on successful save
    if (ratingOk || salesOk) {
      const lsKey = `clipFeedback_${videoId}_${phaseIndex}`;
      try {
        const fb = { rating, selectedReasons, salesConfirm, salesNote, savedAt: Date.now() };
        localStorage.setItem(lsKey, JSON.stringify(fb));
      } catch (lsErr) { /* ignore */ }
    }

    if (ratingOk && salesOk) {
      setDirty(false);
      setSuccessMsg(window.__t('auto_329', '保存しました'));
      onFeedbackSubmitted({
        type: 'all',
        rating,
        reasons: selectedReasons,
        is_sales_moment: salesConfirm,
      });
      // Clear success message after 3 seconds
      setTimeout(() => setSuccessMsg(null), 3000);
    } else {
      setError(window.__t('auto_326', '一部の保存に失敗しました。もう一度お試しください。'));
    }
  }, [videoId, phaseIndex, timeStart, timeEnd, clipId, aiScore, scoreBreakdown, rating, selectedReasons, salesConfirm, salesNote, onFeedbackSubmitted]);

  // Check if there's anything to save
  const canSave = rating !== null || salesConfirm !== null;

  // Compact mode: just rating buttons
  if (compact) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: '8px',
        padding: '4px 0',
      }}>
        <button
          onClick={() => { handleRatingSelect('good'); }}
          disabled={submitting}
          style={{
            padding: '4px 12px', borderRadius: '16px', border: 'none',
            cursor: 'pointer', fontSize: '13px', fontWeight: 600,
            background: rating === 'good' ? '#10b981' : '#f3f4f6',
            color: rating === 'good' ? '#fff' : '#374151',
            transition: 'all 0.2s',
          }}
        >
          {'\uD83D\uDC4D'} {rating === 'good' && submitted ? [window.__t('auto_328', '使える')] : 'Good'}
        </button>
        <button
          onClick={() => { handleRatingSelect('bad'); }}
          disabled={submitting}
          style={{
            padding: '4px 12px', borderRadius: '16px', border: 'none',
            cursor: 'pointer', fontSize: '13px', fontWeight: 600,
            background: rating === 'bad' ? '#ef4444' : '#f3f4f6',
            color: rating === 'bad' ? '#fff' : '#374151',
            transition: 'all 0.2s',
          }}
        >
          {'\uD83D\uDC4E'} {rating === 'bad' && submitted ? [window.__t('auto_340', '微妙')] : 'Fix'}
        </button>
        {dirty && canSave && (
          <button
            onClick={handleSaveAll}
            disabled={submitting}
            style={{
              padding: '4px 12px', borderRadius: '16px', border: 'none',
              cursor: 'pointer', fontSize: '12px', fontWeight: 700,
              background: '#6366f1', color: '#fff',
            }}
          >
            {submitting ? '...' : window.__t('common_save', '保存')}
          </button>
        )}
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
        <span style={{ fontSize: '16px' }}>{'\uD83D\uDD04'}</span>
        クリップの評価
        {submitted && !dirty && (
          <span style={{
            fontSize: '11px', background: '#d1fae5', color: '#065f46',
            padding: '2px 8px', borderRadius: '10px',
          }}>
            保存済み
          </span>
        )}
        {dirty && (
          <span style={{
            fontSize: '11px', background: '#fef3c7', color: '#92400e',
            padding: '2px 8px', borderRadius: '10px',
          }}>
            未保存
          </span>
        )}
      </div>

      {/* ① Quick Rating */}
      <div style={{
        display: 'flex', gap: '8px', marginBottom: '12px',
      }}>
        <button
          onClick={() => handleRatingSelect('good')}
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
          <span style={{ fontSize: '20px' }}>{'\uD83D\uDC4D'}</span>
          使えるクリップ
        </button>
        <button
          onClick={() => handleRatingSelect('bad')}
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
          <span style={{ fontSize: '20px' }}>{'\uD83D\uDC4E'}</span>
          微妙なクリップ
        </button>
      </div>

      {/* ② Reason Tags (show after rating) */}
      {rating && (
        <div style={{ marginBottom: '12px' }}>
          <div style={{
            fontSize: '12px', color: '#6b7280', marginBottom: '8px', fontWeight: 600,
          }}>
            {rating === 'bad' ? [window.__t('auto_309', 'なぜ微妙？（複数選択可）')] : window.__t('auto_308', 'どこが良い？（任意）')}
          </div>
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: '6px',
          }}>
            {REASON_TAGS.map(tag => {
              // Show "perfect" only for good rating, hide for bad
              if (tag.key === 'perfect' && rating === 'bad') return null;
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
          <span style={{ fontSize: '16px' }}>{'\uD83D\uDCB0'}</span>
          このクリップは売れた理由の部分ですか？
          {salesSubmitted && !dirty && (
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
            onClick={() => handleSalesSelect(true)}
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
            onClick={() => handleSalesSelect(false)}
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
            onChange={handleSalesNoteChange}
            placeholder={window.__t('auto_323', 'メモ（任意）: 例「商品紹介の瞬間」')}
            style={{
              width: '100%', padding: '6px 10px', borderRadius: '6px',
              border: '1px solid #d1d5db', fontSize: '12px',
              background: '#fff', color: '#374151',
              boxSizing: 'border-box',
            }}
          />
        )}
      </div>

      {/* ④ SAVE BUTTON */}
      <div style={{
        marginTop: '16px', paddingTop: '12px',
        borderTop: '1px solid #e5e7eb',
      }}>
        <button
          onClick={handleSaveAll}
          disabled={submitting || !canSave}
          style={{
            width: '100%',
            padding: '12px 16px',
            borderRadius: '10px',
            border: 'none',
            cursor: (submitting || !canSave) ? 'not-allowed' : 'pointer',
            fontSize: '15px',
            fontWeight: 800,
            background: !canSave
              ? '#e5e7eb'
              : dirty
                ? '#6366f1'
                : '#10b981',
            color: !canSave ? '#9ca3af' : '#fff',
            transition: 'all 0.2s',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            opacity: submitting ? 0.7 : 1,
          }}
        >
          {submitting ? (
            <>{window.__t('auto_330', '保存中...')}</>
          ) : dirty ? (
            <>{window.__t('auto_360', '💾 評価を保存')}</>
          ) : submitted || salesSubmitted ? (
            <>{window.__t('auto_305', '✅ 保存済み')}</>
          ) : (
            <>{window.__t('auto_360', '💾 評価を保存')}</>
          )}
        </button>
      </div>

      {/* Success message */}
      {successMsg && (
        <div style={{
          marginTop: '8px', padding: '8px 12px', borderRadius: '8px',
          background: '#d1fae5', color: '#065f46', fontSize: '13px',
          fontWeight: 600, textAlign: 'center',
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
        }}>
          ✅ {successMsg}
        </div>
      )}

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
