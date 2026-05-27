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
  const [uploadingFile, setUploadingFile] = useState(false);
  const [uploadProgress, setUploadProgress] = useState('');
  const [analyzing, setAnalyzing] = useState(false);

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
      await axios.post(`${API_BASE}/api/v1/editing-style/profiles`, {
        name: newProfileName.trim(),
        description: newProfileDesc.trim(),
      }, { headers: { 'X-Admin-Key': adminKey } });
      setNewProfileName('');
      setNewProfileDesc('');
      setShowCreateForm(false);
      fetchProfiles();
    } catch (e) {
      alert('作成失敗: ' + (e.response?.data?.detail || e.message));
    }
  };

  // Delete profile
  const handleDeleteProfile = async (profileId) => {
    if (!confirm('このプロファイルを削除しますか？サンプルデータも全て削除されます。')) return;
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

  // Select profile (load details)
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

  // Upload sample video
  const handleUploadSample = async (file, sampleType) => {
    if (!selectedProfile) return;
    setUploadingFile(true);
    setUploadProgress('アップロード中...');
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('profile_id', selectedProfile.id);
      formData.append('sample_type', sampleType);

      await axios.post(`${API_BASE}/api/v1/editing-style/upload-sample`, formData, {
        headers: {
          'X-Admin-Key': adminKey,
          'Content-Type': 'multipart/form-data',
        },
        onUploadProgress: (e) => {
          const pct = Math.round((e.loaded / e.total) * 100);
          setUploadProgress(`アップロード中... ${pct}%`);
        }
      });
      setUploadProgress('アップロード完了！');
      // Refresh profile
      handleSelectProfile(selectedProfile.id);
    } catch (e) {
      setUploadProgress('');
      alert('アップロード失敗: ' + (e.response?.data?.detail || e.message));
    } finally {
      setUploadingFile(false);
      setTimeout(() => setUploadProgress(''), 3000);
    }
  };

  // Analyze single sample (Phase 1)
  const handleAnalyzeSingle = async (sampleId) => {
    if (!selectedProfile) return;
    setAnalyzing(true);
    try {
      await axios.post(`${API_BASE}/api/v1/editing-style/analyze`, {
        profile_id: selectedProfile.id,
        sample_id: sampleId,
      }, { headers: { 'X-Admin-Key': adminKey } });
      alert('分析を開始しました。数分後にリロードしてください。');
      // Refresh after a short delay
      setTimeout(() => handleSelectProfile(selectedProfile.id), 5000);
    } catch (e) {
      alert('分析開始失敗: ' + (e.response?.data?.detail || e.message));
    } finally {
      setAnalyzing(false);
    }
  };

  // Analyze pair (Phase 2)
  const handleAnalyzePair = async (finishedId, originalId) => {
    if (!selectedProfile) return;
    setAnalyzing(true);
    try {
      await axios.post(`${API_BASE}/api/v1/editing-style/analyze-pair`, {
        profile_id: selectedProfile.id,
        finished_sample_id: finishedId,
        original_sample_id: originalId,
      }, { headers: { 'X-Admin-Key': adminKey } });
      alert('ペア分析を開始しました。数分後にリロードしてください。');
      setTimeout(() => handleSelectProfile(selectedProfile.id), 5000);
    } catch (e) {
      alert('ペア分析開始失敗: ' + (e.response?.data?.detail || e.message));
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-800">🎨 編集スタイル学習</h2>
          <p className="text-sm text-gray-500 mt-1">
            お手本動画をアップロードし、AIが編集スタイルを学習します。学習結果は次回のAIクリップ生成に反映されます。
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
            <button
              onClick={handleCreateProfile}
              className="px-4 py-2 bg-indigo-600 text-white rounded-md text-sm hover:bg-indigo-700"
            >
              作成
            </button>
            <button
              onClick={() => setShowCreateForm(false)}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-md text-sm hover:bg-gray-300"
            >
              キャンセル
            </button>
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
                  profile.status === 'active'
                    ? 'bg-green-100 text-green-700'
                    : 'bg-gray-100 text-gray-500'
                }`}>
                  {profile.status === 'active' ? '学習済み' : '下書き'}
                </span>
              </div>
              {profile.description && (
                <p className="text-sm text-gray-500 mt-1">{profile.description}</p>
              )}
              <div className="flex items-center gap-3 mt-2 text-xs text-gray-400">
                <span>サンプル: {profile.sample_count}本</span>
                {profile.style_params && Object.keys(profile.style_params).length > 0 && (
                  <span className="text-indigo-500">✓ パラメータ{Object.keys(profile.style_params).length}項目</span>
                )}
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

      {/* Selected Profile Detail */}
      {selectedProfile && (
        <ProfileDetail
          profile={selectedProfile}
          adminKey={adminKey}
          onUpload={handleUploadSample}
          onAnalyzeSingle={handleAnalyzeSingle}
          onAnalyzePair={handleAnalyzePair}
          onDelete={handleDeleteProfile}
          onRefresh={() => handleSelectProfile(selectedProfile.id)}
          uploadingFile={uploadingFile}
          uploadProgress={uploadProgress}
          analyzing={analyzing}
        />
      )}
    </div>
  );
}

// ─── Profile Detail Component ────────────────────────────────────────────────

function ProfileDetail({
  profile, adminKey, onUpload, onAnalyzeSingle, onAnalyzePair,
  onDelete, onRefresh, uploadingFile, uploadProgress, analyzing
}) {
  const [pairFinishedId, setPairFinishedId] = useState('');
  const [pairOriginalId, setPairOriginalId] = useState('');

  const finishedSamples = (profile.samples || []).filter(s => s.sample_type === 'finished');
  const originalSamples = (profile.samples || []).filter(s => s.sample_type === 'original');

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold text-gray-800">{profile.name}</h3>
          <p className="text-sm text-gray-500">{profile.description || '説明なし'}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onRefresh}
            className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200"
          >
            🔄 更新
          </button>
          <button
            onClick={() => onDelete(profile.id)}
            className="px-3 py-1.5 text-sm bg-red-50 text-red-600 rounded-md hover:bg-red-100"
          >
            🗑️ 削除
          </button>
        </div>
      </div>

      {/* Style Params Display */}
      {profile.style_params && Object.keys(profile.style_params).length > 0 && (
        <div className="bg-gradient-to-r from-indigo-50 to-purple-50 border border-indigo-200 rounded-lg p-4">
          <h4 className="font-medium text-indigo-800 mb-3">📊 学習済みスタイルパラメータ</h4>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {Object.entries(profile.style_params).map(([key, value]) => (
              <div key={key} className="bg-white rounded-md p-2 border border-indigo-100">
                <div className="text-xs text-gray-500">{formatParamLabel(key)}</div>
                <div className="text-sm font-medium text-gray-800">{formatParamValue(key, value)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Upload Section */}
      <div className="space-y-4">
        <h4 className="font-medium text-gray-700">📤 サンプル動画アップロード</h4>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Phase 1: Finished video */}
          <div className="border border-dashed border-indigo-300 rounded-lg p-4 bg-indigo-50/30">
            <h5 className="text-sm font-medium text-indigo-700 mb-2">Phase 1: お手本完成動画</h5>
            <p className="text-xs text-gray-500 mb-3">
              編集済みの完成動画をアップロードします。AIがカットのリズム、テロップスタイル等を分析します。
            </p>
            <input
              type="file"
              accept="video/*"
              disabled={uploadingFile}
              onChange={(e) => {
                if (e.target.files?.[0]) onUpload(e.target.files[0], 'finished');
                e.target.value = '';
              }}
              className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-medium file:bg-indigo-100 file:text-indigo-700 hover:file:bg-indigo-200"
            />
          </div>

          {/* Phase 2: Original video */}
          <div className="border border-dashed border-orange-300 rounded-lg p-4 bg-orange-50/30">
            <h5 className="text-sm font-medium text-orange-700 mb-2">Phase 2: 元の長尺動画</h5>
            <p className="text-xs text-gray-500 mb-3">
              編集前の元動画をアップロードします。完成動画とペアで分析すると精度が大幅に向上します。
            </p>
            <input
              type="file"
              accept="video/*"
              disabled={uploadingFile}
              onChange={(e) => {
                if (e.target.files?.[0]) onUpload(e.target.files[0], 'original');
                e.target.value = '';
              }}
              className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-medium file:bg-orange-100 file:text-orange-700 hover:file:bg-orange-200"
            />
          </div>
        </div>

        {uploadProgress && (
          <div className="text-sm text-indigo-600 bg-indigo-50 px-3 py-2 rounded-md">
            {uploadProgress}
          </div>
        )}
      </div>

      {/* Samples List */}
      {(profile.samples || []).length > 0 && (
        <div className="space-y-4">
          <h4 className="font-medium text-gray-700">📁 アップロード済みサンプル</h4>

          <div className="space-y-2">
            {(profile.samples || []).map((sample) => (
              <div key={sample.id} className="flex items-center justify-between bg-gray-50 rounded-lg p-3 border border-gray-100">
                <div className="flex items-center gap-3">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    sample.sample_type === 'finished'
                      ? 'bg-indigo-100 text-indigo-700'
                      : 'bg-orange-100 text-orange-700'
                  }`}>
                    {sample.sample_type === 'finished' ? '完成動画' : '元動画'}
                  </span>
                  <span className="text-sm text-gray-700">{sample.filename}</span>
                  {sample.duration_sec > 0 && (
                    <span className="text-xs text-gray-400">{Math.round(sample.duration_sec)}秒</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    sample.analysis_status === 'done' ? 'bg-green-100 text-green-700' :
                    sample.analysis_status === 'analyzing' ? 'bg-yellow-100 text-yellow-700' :
                    sample.analysis_status === 'error' ? 'bg-red-100 text-red-700' :
                    'bg-gray-100 text-gray-500'
                  }`}>
                    {sample.analysis_status === 'done' ? '✓ 分析完了' :
                     sample.analysis_status === 'analyzing' ? '⏳ 分析中...' :
                     sample.analysis_status === 'error' ? '✗ エラー' :
                     '未分析'}
                  </span>
                  {sample.analysis_status === 'pending' && sample.sample_type === 'finished' && (
                    <button
                      onClick={() => onAnalyzeSingle(sample.id)}
                      disabled={analyzing}
                      className="text-xs px-2 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
                    >
                      分析開始
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Phase 2: Pair Analysis */}
          {finishedSamples.length > 0 && originalSamples.length > 0 && (
            <div className="bg-gradient-to-r from-orange-50 to-indigo-50 border border-orange-200 rounded-lg p-4 space-y-3">
              <h5 className="font-medium text-gray-700">🔗 Phase 2: ペア分析（差分比較学習）</h5>
              <p className="text-xs text-gray-500">
                完成動画と元動画をペアで選択し、AIが「何をカットしたか」を正確に学習します。
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-gray-500 block mb-1">完成動画:</label>
                  <select
                    value={pairFinishedId}
                    onChange={(e) => setPairFinishedId(e.target.value)}
                    className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
                  >
                    <option value="">選択...</option>
                    {finishedSamples.map(s => (
                      <option key={s.id} value={s.id}>{s.filename}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">元動画:</label>
                  <select
                    value={pairOriginalId}
                    onChange={(e) => setPairOriginalId(e.target.value)}
                    className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
                  >
                    <option value="">選択...</option>
                    {originalSamples.map(s => (
                      <option key={s.id} value={s.id}>{s.filename}</option>
                    ))}
                  </select>
                </div>
              </div>
              <button
                onClick={() => {
                  if (pairFinishedId && pairOriginalId) {
                    onAnalyzePair(pairFinishedId, pairOriginalId);
                  } else {
                    alert('完成動画と元動画の両方を選択してください');
                  }
                }}
                disabled={analyzing || !pairFinishedId || !pairOriginalId}
                className="px-4 py-2 bg-orange-600 text-white rounded-md text-sm hover:bg-orange-700 disabled:opacity-50"
              >
                {analyzing ? '分析中...' : '🔍 ペア分析を開始'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Analysis Results */}
      {profile.samples?.some(s => s.analysis_status === 'done' && s.analysis_result) && (
        <div className="space-y-3">
          <h4 className="font-medium text-gray-700">📈 分析結果詳細</h4>
          {profile.samples.filter(s => s.analysis_status === 'done' && s.analysis_result).map(sample => (
            <div key={sample.id} className="bg-gray-50 rounded-lg p-3 border border-gray-100">
              <div className="text-sm font-medium text-gray-700 mb-2">
                {sample.filename}
                {sample.analysis_result?.type === 'pair_analysis' && (
                  <span className="ml-2 text-xs bg-orange-100 text-orange-700 px-2 py-0.5 rounded-full">ペア分析</span>
                )}
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                {sample.analysis_result?.duration_sec && (
                  <div className="bg-white p-2 rounded border">
                    <div className="text-gray-400">動画長</div>
                    <div className="font-medium">{Math.round(sample.analysis_result.duration_sec)}秒</div>
                  </div>
                )}
                {sample.analysis_result?.scene_count != null && (
                  <div className="bg-white p-2 rounded border">
                    <div className="text-gray-400">シーンカット数</div>
                    <div className="font-medium">{sample.analysis_result.scene_count}回</div>
                  </div>
                )}
                {sample.analysis_result?.avg_cut_interval && (
                  <div className="bg-white p-2 rounded border">
                    <div className="text-gray-400">平均カット間隔</div>
                    <div className="font-medium">{sample.analysis_result.avg_cut_interval}秒</div>
                  </div>
                )}
                {sample.analysis_result?.cut_ratio != null && (
                  <div className="bg-white p-2 rounded border">
                    <div className="text-gray-400">カット率</div>
                    <div className="font-medium">{(sample.analysis_result.cut_ratio * 100).toFixed(1)}%</div>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
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
    preferred_clip_duration_sec: '好みのクリップ長',
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
