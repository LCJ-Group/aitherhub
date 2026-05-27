import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

export default function EditingStylePanel({ adminKey }) {
  const [profiles, setProfiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newProfileName, setNewProfileName] = useState('');
  const [newProfileDesc, setNewProfileDesc] = useState('');

  // Fetch profiles
  const fetchProfiles = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(`${API_BASE}/api/v1/editing-style/profiles`, {
        headers: { 'X-Admin-Key': adminKey }
      });
      setProfiles(res.data.profiles || []);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }, [adminKey]);

  useEffect(() => { fetchProfiles(); }, [fetchProfiles]);

  // Create profile
  const handleCreateProfile = async () => {
    if (!newProfileName.trim()) return;
    try {
      const res = await axios.post(`${API_BASE}/api/v1/editing-style/profiles`, {
        name: newProfileName.trim(),
        description: newProfileDesc.trim(),
      }, { headers: { 'X-Admin-Key': adminKey } });
      setNewProfileName('');
      setNewProfileDesc('');
      setShowCreateForm(false);
      fetchProfiles();
      // Auto-select the new profile
      if (res.data?.id) {
        handleSelectProfile(res.data.id);
      }
    } catch (e) {
      alert('作成失敗: ' + (e.response?.data?.detail || e.message));
    }
  };

  // Delete profile
  const handleDeleteProfile = async (profileId) => {
    if (!confirm('このプロファイルを削除しますか？')) return;
    try {
      await axios.delete(`${API_BASE}/api/v1/editing-style/profiles/${profileId}`, {
        headers: { 'X-Admin-Key': adminKey }
      });
      setSelectedProfile(null);
      fetchProfiles();
    } catch (e) {
      alert('削除失敗: ' + (e.response?.data?.detail || e.message));
    }
  };

  // Select profile
  const handleSelectProfile = async (profileId) => {
    try {
      const res = await axios.get(`${API_BASE}/api/v1/editing-style/profiles/${profileId}`, {
        headers: { 'X-Admin-Key': adminKey }
      });
      setSelectedProfile(res.data);
    } catch (e) {
      alert('読み込み失敗: ' + (e.response?.data?.detail || e.message));
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-800">🎨 編集スタイル学習</h2>
          <p className="text-sm text-gray-500 mt-1">
            お手本動画をアップロードし、AIが編集スタイルを学習。学習結果は次回のAIクリップ生成に自動反映。
          </p>
        </div>
        <button
          onClick={() => setShowCreateForm(!showCreateForm)}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm font-medium"
        >
          + 新規プロファイル
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Create Form */}
      {showCreateForm && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4 space-y-3">
          <h3 className="font-medium text-indigo-800">新規プロファイル作成</h3>
          <input
            type="text"
            placeholder="プロファイル名（例: 黄松松スタイル）"
            value={newProfileName}
            onChange={(e) => setNewProfileName(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
          />
          <input
            type="text"
            placeholder="説明（任意）"
            value={newProfileDesc}
            onChange={(e) => setNewProfileDesc(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
          />
          <div className="flex gap-2">
            <button onClick={handleCreateProfile} className="px-4 py-2 bg-indigo-600 text-white rounded-md text-sm hover:bg-indigo-700">作成</button>
            <button onClick={() => setShowCreateForm(false)} className="px-4 py-2 bg-gray-200 text-gray-700 rounded-md text-sm hover:bg-gray-300">キャンセル</button>
          </div>
        </div>
      )}

      {/* Profile List */}
      {loading ? (
        <div className="flex justify-center py-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500"></div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {profiles.map((profile) => (
            <div
              key={profile.id}
              onClick={() => handleSelectProfile(profile.id)}
              className={`border rounded-lg p-4 cursor-pointer transition hover:shadow-md ${
                selectedProfile?.id === profile.id
                  ? 'border-indigo-500 bg-indigo-50 shadow-md'
                  : 'border-gray-200 hover:border-indigo-300'
              }`}
            >
              <div className="flex items-center justify-between">
                <h3 className="font-medium text-gray-800">{profile.name}</h3>
                <span className={`text-xs px-2 py-0.5 rounded-full ${
                  profile.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                }`}>
                  {profile.status === 'active' ? '学習済み' : '下書き'}
                </span>
              </div>
              {profile.description && <p className="text-sm text-gray-500 mt-1">{profile.description}</p>}
              <div className="flex items-center gap-3 mt-2 text-xs text-gray-400">
                <span>サンプル: {profile.sample_count}本</span>
              </div>
            </div>
          ))}
          {profiles.length === 0 && (
            <div className="col-span-full text-center py-8 text-gray-400">
              プロファイルがありません。「新規プロファイル」ボタンから作成してください。
            </div>
          )}
        </div>
      )}

      {/* Selected Profile Detail - Simplified Pair Upload */}
      {selectedProfile && (
        <PairUploadPanel
          profile={selectedProfile}
          adminKey={adminKey}
          onDelete={handleDeleteProfile}
          onRefresh={() => handleSelectProfile(selectedProfile.id)}
        />
      )}
    </div>
  );
}

// ─── Simplified Pair Upload Panel ────────────────────────────────────────────

function PairUploadPanel({ profile, adminKey, onDelete, onRefresh }) {
  const [pairs, setPairs] = useState([]);
  const [uploading, setUploading] = useState({});
  const [autoAnalyzing, setAutoAnalyzing] = useState(false);

  // Build pairs from samples
  useEffect(() => {
    const samples = profile.samples || [];
    const finished = samples.filter(s => s.sample_type === 'finished');
    const original = samples.filter(s => s.sample_type === 'original');
    
    // Match pairs by upload order (finished[0] + original[0], etc.)
    const builtPairs = [];
    const maxLen = Math.max(finished.length, original.length);
    for (let i = 0; i < maxLen; i++) {
      builtPairs.push({
        index: i,
        finished: finished[i] || null,
        original: original[i] || null,
      });
    }
    // Always add an empty pair at the end for new uploads
    builtPairs.push({ index: maxLen, finished: null, original: null });
    setPairs(builtPairs);
  }, [profile.samples]);

  // Upload file immediately on selection
  const handleFileSelect = async (file, sampleType, pairIndex) => {
    const uploadKey = `${pairIndex}-${sampleType}`;
    setUploading(prev => ({ ...prev, [uploadKey]: { progress: 0, name: file.name } }));
    
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('profile_id', profile.id);
      formData.append('sample_type', sampleType);

      await axios.post(`${API_BASE}/api/v1/editing-style/upload-sample`, formData, {
        headers: { 'X-Admin-Key': adminKey, 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (e) => {
          const pct = Math.round((e.loaded / e.total) * 100);
          setUploading(prev => ({ ...prev, [uploadKey]: { ...prev[uploadKey], progress: pct } }));
        }
      });
      
      setUploading(prev => ({ ...prev, [uploadKey]: { ...prev[uploadKey], progress: 100, done: true } }));
      
      // Auto-refresh and check if we can auto-analyze
      setTimeout(() => {
        onRefresh();
        setUploading(prev => {
          const next = { ...prev };
          delete next[uploadKey];
          return next;
        });
      }, 1000);
      
    } catch (e) {
      setUploading(prev => ({ ...prev, [uploadKey]: { ...prev[uploadKey], error: true } }));
      alert('アップロード失敗: ' + (e.response?.data?.detail || e.message));
      setTimeout(() => {
        setUploading(prev => {
          const next = { ...prev };
          delete next[uploadKey];
          return next;
        });
      }, 3000);
    }
  };

  // Auto-analyze: when both finished + original exist and not yet analyzed
  const handleAutoAnalyze = async () => {
    const samples = profile.samples || [];
    const pendingFinished = samples.filter(s => s.sample_type === 'finished' && s.analysis_status === 'pending');
    const pendingOriginal = samples.filter(s => s.sample_type === 'original' && s.analysis_status === 'pending');
    
    if (pendingFinished.length === 0 && pendingOriginal.length === 0) {
      alert('分析対象のサンプルがありません');
      return;
    }

    setAutoAnalyzing(true);
    try {
      // If we have pairs (both finished + original pending), do pair analysis
      if (pendingFinished.length > 0 && pendingOriginal.length > 0) {
        await axios.post(`${API_BASE}/api/v1/editing-style/analyze-pair`, {
          profile_id: profile.id,
          finished_sample_id: pendingFinished[0].id,
          original_sample_id: pendingOriginal[0].id,
        }, { headers: { 'X-Admin-Key': adminKey } });
      } else if (pendingFinished.length > 0) {
        // Single analysis for finished video
        await axios.post(`${API_BASE}/api/v1/editing-style/analyze`, {
          profile_id: profile.id,
          sample_id: pendingFinished[0].id,
        }, { headers: { 'X-Admin-Key': adminKey } });
      }
      
      alert('✅ 分析を開始しました！数分後に自動で学習結果が反映されます。');
      setTimeout(() => onRefresh(), 3000);
    } catch (e) {
      alert('分析開始失敗: ' + (e.response?.data?.detail || e.message));
    } finally {
      setAutoAnalyzing(false);
    }
  };

  // Analyze all pending pairs
  const handleAnalyzeAll = async () => {
    const samples = profile.samples || [];
    const finished = samples.filter(s => s.sample_type === 'finished' && s.analysis_status === 'pending');
    const original = samples.filter(s => s.sample_type === 'original' && s.analysis_status === 'pending');
    
    if (finished.length === 0) {
      alert('未分析の完成動画がありません');
      return;
    }

    setAutoAnalyzing(true);
    try {
      // Pair analysis for each matched pair
      const pairCount = Math.min(finished.length, original.length);
      for (let i = 0; i < pairCount; i++) {
        await axios.post(`${API_BASE}/api/v1/editing-style/analyze-pair`, {
          profile_id: profile.id,
          finished_sample_id: finished[i].id,
          original_sample_id: original[i].id,
        }, { headers: { 'X-Admin-Key': adminKey } });
      }
      // Single analysis for remaining finished without pairs
      for (let i = pairCount; i < finished.length; i++) {
        await axios.post(`${API_BASE}/api/v1/editing-style/analyze`, {
          profile_id: profile.id,
          sample_id: finished[i].id,
        }, { headers: { 'X-Admin-Key': adminKey } });
      }
      
      alert(`✅ ${finished.length}件の分析を開始しました！`);
      setTimeout(() => onRefresh(), 3000);
    } catch (e) {
      alert('分析開始失敗: ' + (e.response?.data?.detail || e.message));
    } finally {
      setAutoAnalyzing(false);
    }
  };

  const samples = profile.samples || [];
  const pendingCount = samples.filter(s => s.analysis_status === 'pending').length;
  const doneCount = samples.filter(s => s.analysis_status === 'done').length;
  const analyzingCount = samples.filter(s => s.analysis_status === 'analyzing').length;

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5 shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold text-gray-800">{profile.name}</h3>
          {profile.description && <p className="text-sm text-gray-500">{profile.description}</p>}
          <div className="flex gap-3 mt-1 text-xs">
            {doneCount > 0 && <span className="text-green-600">✓ {doneCount}件分析済み</span>}
            {analyzingCount > 0 && <span className="text-yellow-600">⏳ {analyzingCount}件分析中</span>}
            {pendingCount > 0 && <span className="text-gray-400">{pendingCount}件未分析</span>}
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={onRefresh} className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200">🔄</button>
          <button onClick={() => onDelete(profile.id)} className="px-3 py-1.5 text-sm bg-red-50 text-red-600 rounded-md hover:bg-red-100">🗑️</button>
        </div>
      </div>

      {/* Style Params (if learned) */}
      {profile.style_params && Object.keys(profile.style_params).length > 0 && (
        <div className="bg-gradient-to-r from-green-50 to-emerald-50 border border-green-200 rounded-lg p-4">
          <h4 className="font-medium text-green-800 mb-2 text-sm">✅ 学習済みスタイル</h4>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {Object.entries(profile.style_params).map(([key, value]) => (
              <div key={key} className="bg-white rounded-md p-2 border border-green-100 text-xs">
                <div className="text-gray-400">{formatParamLabel(key)}</div>
                <div className="font-medium text-gray-800">{formatParamValue(key, value)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Quick Upload Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="font-medium text-gray-700 text-sm">📤 動画ペアをアップロード</h4>
          {pendingCount > 0 && (
            <button
              onClick={handleAnalyzeAll}
              disabled={autoAnalyzing}
              className="px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50 font-medium"
            >
              {autoAnalyzing ? '⏳ 分析中...' : `🚀 全て分析開始 (${pendingCount}件)`}
            </button>
          )}
        </div>
        
        <p className="text-xs text-gray-400">
          「完成動画」と「元の長尺動画」をペアでアップロード → 自動で差分学習します。完成動画だけでもOK。
        </p>

        {/* Pair Upload Rows */}
        <div className="space-y-2">
          {pairs.map((pair) => (
            <PairRow
              key={pair.index}
              pair={pair}
              pairIndex={pair.index}
              uploading={uploading}
              onFileSelect={handleFileSelect}
            />
          ))}
        </div>
      </div>

      {/* Analysis Results */}
      {samples.filter(s => s.analysis_status === 'done' && s.analysis_result).length > 0 && (
        <div className="space-y-2">
          <h4 className="font-medium text-gray-700 text-sm">📊 分析結果</h4>
          {samples.filter(s => s.analysis_status === 'done' && s.analysis_result).map(sample => (
            <div key={sample.id} className="bg-gray-50 rounded-lg p-3 border border-gray-100 text-xs">
              <div className="flex items-center gap-2 mb-2">
                <span className={`px-2 py-0.5 rounded-full ${
                  sample.sample_type === 'finished' ? 'bg-indigo-100 text-indigo-700' : 'bg-orange-100 text-orange-700'
                }`}>
                  {sample.sample_type === 'finished' ? '完成' : '元動画'}
                </span>
                <span className="text-gray-700 font-medium">{sample.filename}</span>
                {sample.analysis_result?.type === 'pair_analysis' && (
                  <span className="bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full">ペア分析</span>
                )}
              </div>
              <div className="grid grid-cols-4 gap-2">
                {sample.analysis_result?.duration_sec && (
                  <div className="bg-white p-1.5 rounded border"><span className="text-gray-400">長さ</span> <span className="font-medium">{Math.round(sample.analysis_result.duration_sec)}秒</span></div>
                )}
                {sample.analysis_result?.scene_count != null && (
                  <div className="bg-white p-1.5 rounded border"><span className="text-gray-400">カット数</span> <span className="font-medium">{sample.analysis_result.scene_count}回</span></div>
                )}
                {sample.analysis_result?.avg_cut_interval && (
                  <div className="bg-white p-1.5 rounded border"><span className="text-gray-400">カット間隔</span> <span className="font-medium">{sample.analysis_result.avg_cut_interval}秒</span></div>
                )}
                {sample.analysis_result?.cut_ratio != null && (
                  <div className="bg-white p-1.5 rounded border"><span className="text-gray-400">カット率</span> <span className="font-medium">{(sample.analysis_result.cut_ratio * 100).toFixed(1)}%</span></div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Pair Row Component ────────────────────────────────────────────────────────

function PairRow({ pair, pairIndex, uploading, onFileSelect }) {
  const finishedKey = `${pairIndex}-finished`;
  const originalKey = `${pairIndex}-original`;
  const finishedUpload = uploading[finishedKey];
  const originalUpload = uploading[originalKey];

  return (
    <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg border border-gray-100">
      <span className="text-xs text-gray-400 font-mono w-6">#{pairIndex + 1}</span>
      
      {/* Finished Video */}
      <div className="flex-1">
        {pair.finished ? (
          <div className="flex items-center gap-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700">完成</span>
            <span className="text-xs text-gray-700 truncate max-w-[150px]">{pair.finished.filename}</span>
            <StatusBadge status={pair.finished.analysis_status} />
          </div>
        ) : finishedUpload ? (
          <UploadProgress upload={finishedUpload} label="完成動画" />
        ) : (
          <label className="cursor-pointer inline-flex items-center gap-1 px-3 py-1.5 bg-indigo-100 text-indigo-700 rounded-md text-xs font-medium hover:bg-indigo-200 transition">
            📹 完成動画を選択
            <input
              type="file"
              accept="video/*"
              className="hidden"
              onChange={(e) => {
                if (e.target.files?.[0]) onFileSelect(e.target.files[0], 'finished', pairIndex);
                e.target.value = '';
              }}
            />
          </label>
        )}
      </div>

      <span className="text-gray-300">→</span>

      {/* Original Video */}
      <div className="flex-1">
        {pair.original ? (
          <div className="flex items-center gap-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700">元動画</span>
            <span className="text-xs text-gray-700 truncate max-w-[150px]">{pair.original.filename}</span>
            <StatusBadge status={pair.original.analysis_status} />
          </div>
        ) : originalUpload ? (
          <UploadProgress upload={originalUpload} label="元動画" />
        ) : (
          <label className="cursor-pointer inline-flex items-center gap-1 px-3 py-1.5 bg-orange-100 text-orange-700 rounded-md text-xs font-medium hover:bg-orange-200 transition">
            📼 元動画を選択
            <input
              type="file"
              accept="video/*"
              className="hidden"
              onChange={(e) => {
                if (e.target.files?.[0]) onFileSelect(e.target.files[0], 'original', pairIndex);
                e.target.value = '';
              }}
            />
          </label>
        )}
      </div>
    </div>
  );
}

// ─── Small Components ────────────────────────────────────────────────────────

function StatusBadge({ status }) {
  if (status === 'done') return <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-100 text-green-700">✓</span>;
  if (status === 'analyzing') return <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-700 animate-pulse">分析中</span>;
  if (status === 'error') return <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-100 text-red-700">エラー</span>;
  return <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">待機</span>;
}

function UploadProgress({ upload, label }) {
  if (upload.error) {
    return <span className="text-xs text-red-600">❌ {label} アップロード失敗</span>;
  }
  if (upload.done) {
    return <span className="text-xs text-green-600">✅ {upload.name} 完了</span>;
  }
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div className="h-full bg-indigo-500 rounded-full transition-all" style={{ width: `${upload.progress}%` }}></div>
      </div>
      <span className="text-[10px] text-gray-500">{upload.progress}%</span>
    </div>
  );
}

// ─── Helper Functions ────────────────────────────────────────────────────────

function formatParamLabel(key) {
  const labels = {
    hook_style: 'フックスタイル',
    pacing: 'テンポ',
    silence_tolerance_sec: '無音許容(秒)',
    silence_threshold_sec: '無音閾値(秒)',
    content_density: '情報密度',
    cut_aggressiveness: 'カット積極性',
    preferred_clip_duration_sec: 'クリップ長',
    hook_duration_sec: 'フック長(秒)',
    subtitle_style_preference: '字幕スタイル',
    transition_style: 'トランジション',
    transition_preference: 'トランジション',
    energy_level: 'エネルギー',
    editing_philosophy: '編集方針',
    silence_handling: '無音処理',
    filler_handling: 'フィラー処理',
    content_filter: 'コンテンツフィルタ',
    keeps_greetings: '挨拶を残す',
    keeps_reactions: 'リアクション残す',
    hook_creation: 'フック作成',
    max_single_segment_sec: '最大セグメント長',
    preferred_segment_duration: '好みセグメント長',
    cut_ratio: 'カット率',
  };
  return labels[key] || key;
}

function formatParamValue(key, value) {
  if (typeof value === 'boolean') return value ? 'はい' : 'いいえ';
  if (typeof value === 'number') {
    if (key.includes('ratio') || key === 'cut_aggressiveness') return `${(value * 100).toFixed(0)}%`;
    if (key.includes('sec') || key.includes('duration')) return `${value}秒`;
    return String(value);
  }
  const valueLabels = {
    fast: '速い', medium: '普通', slow: 'ゆっくり',
    high: '高い', low: '低い',
    aggressive: '積極的', moderate: 'バランス', conservative: '控えめ',
    strict: '厳しい', lenient: '緩い',
    always_cut: '必ずカット', sometimes_cut: '状況次第', keep: '残す',
    question: '疑問形', command: '命令形', shock: '衝撃', story: 'ストーリー', direct: '直接的',
    hard_cut: 'ハードカット', hard: 'ハードカット', fade: 'フェード', mixed: '混合',
    extract: '既存から抽出', create: '新規作成', none: 'なし',
    pop: 'ポップ', simple: 'シンプル', box: 'ボックス', gradient: 'グラデーション',
  };
  return valueLabels[value] || String(value);
}
