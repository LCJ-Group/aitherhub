import { useState, useRef, useCallback } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || 'https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1';

/* ═══════════════════════════════════════════════════════════════
   LiverGuidePage — 撮影ガイド & 素材アップロード LP
   ライバーが「商品を持たない汎用素材」を撮影・アップロードするためのページ
   ═══════════════════════════════════════════════════════════════ */

// ─── Styles ───
const styles = {
  page: {
    minHeight: '100vh',
    background: 'linear-gradient(180deg, #0a0a0f 0%, #1a1a2e 50%, #0a0a0f 100%)',
    color: '#e0e0e0',
    fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
  },
  hero: {
    textAlign: 'center',
    padding: '80px 20px 60px',
    position: 'relative',
    overflow: 'hidden',
  },
  heroTitle: {
    fontSize: 'clamp(2rem, 5vw, 3.5rem)',
    fontWeight: 800,
    background: 'linear-gradient(135deg, #00d4ff, #7c3aed, #f472b6)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
    marginBottom: '16px',
    lineHeight: 1.2,
  },
  heroSubtitle: {
    fontSize: 'clamp(1rem, 2.5vw, 1.3rem)',
    color: '#94a3b8',
    maxWidth: '600px',
    margin: '0 auto',
    lineHeight: 1.6,
  },
  container: {
    maxWidth: '900px',
    margin: '0 auto',
    padding: '0 20px',
  },
  section: {
    marginBottom: '60px',
  },
  sectionTitle: {
    fontSize: 'clamp(1.5rem, 3vw, 2rem)',
    fontWeight: 700,
    color: '#fff',
    marginBottom: '24px',
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  stepsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))',
    gap: '24px',
    marginBottom: '40px',
  },
  stepCard: {
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: '16px',
    padding: '32px 24px',
    textAlign: 'center',
    transition: 'transform 0.2s, border-color 0.2s',
  },
  stepNumber: {
    width: '48px',
    height: '48px',
    borderRadius: '50%',
    background: 'linear-gradient(135deg, #7c3aed, #00d4ff)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    margin: '0 auto 16px',
    fontSize: '1.2rem',
    fontWeight: 700,
    color: '#fff',
  },
  stepTitle: {
    fontSize: '1.1rem',
    fontWeight: 600,
    color: '#fff',
    marginBottom: '8px',
  },
  stepDesc: {
    fontSize: '0.9rem',
    color: '#94a3b8',
    lineHeight: 1.5,
  },
  guideCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: '16px',
    padding: '32px',
    marginBottom: '24px',
  },
  guideTitle: {
    fontSize: '1.2rem',
    fontWeight: 600,
    color: '#fff',
    marginBottom: '16px',
  },
  guideList: {
    listStyle: 'none',
    padding: 0,
    margin: 0,
  },
  guideItem: {
    padding: '12px 0',
    borderBottom: '1px solid rgba(255,255,255,0.05)',
    display: 'flex',
    alignItems: 'flex-start',
    gap: '12px',
    fontSize: '0.95rem',
    lineHeight: 1.6,
  },
  goodBadGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
    gap: '24px',
  },
  goodCard: {
    background: 'rgba(34, 197, 94, 0.08)',
    border: '1px solid rgba(34, 197, 94, 0.2)',
    borderRadius: '16px',
    padding: '24px',
  },
  badCard: {
    background: 'rgba(239, 68, 68, 0.08)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: '16px',
    padding: '24px',
  },
  goodBadTitle: {
    fontSize: '1.1rem',
    fontWeight: 600,
    marginBottom: '12px',
  },
  goodBadList: {
    listStyle: 'none',
    padding: 0,
    margin: 0,
    fontSize: '0.9rem',
    lineHeight: 1.8,
  },
  // Upload section
  uploadSection: {
    background: 'rgba(124, 58, 237, 0.05)',
    border: '1px solid rgba(124, 58, 237, 0.2)',
    borderRadius: '20px',
    padding: '40px 32px',
    marginTop: '60px',
  },
  inputGroup: {
    marginBottom: '20px',
  },
  label: {
    display: 'block',
    fontSize: '0.9rem',
    fontWeight: 500,
    color: '#94a3b8',
    marginBottom: '8px',
  },
  input: {
    width: '100%',
    padding: '12px 16px',
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.15)',
    borderRadius: '10px',
    color: '#fff',
    fontSize: '1rem',
    outline: 'none',
    transition: 'border-color 0.2s',
    boxSizing: 'border-box',
  },
  select: {
    width: '100%',
    padding: '12px 16px',
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.15)',
    borderRadius: '10px',
    color: '#fff',
    fontSize: '1rem',
    outline: 'none',
    appearance: 'none',
    boxSizing: 'border-box',
  },
  dropzone: {
    border: '2px dashed rgba(124, 58, 237, 0.4)',
    borderRadius: '16px',
    padding: '48px 24px',
    textAlign: 'center',
    cursor: 'pointer',
    transition: 'border-color 0.2s, background 0.2s',
    marginTop: '20px',
  },
  dropzoneActive: {
    borderColor: '#7c3aed',
    background: 'rgba(124, 58, 237, 0.1)',
  },
  dropzoneText: {
    fontSize: '1rem',
    color: '#94a3b8',
    marginBottom: '8px',
  },
  dropzoneHint: {
    fontSize: '0.85rem',
    color: '#64748b',
  },
  progressBar: {
    width: '100%',
    height: '8px',
    background: 'rgba(255,255,255,0.1)',
    borderRadius: '4px',
    overflow: 'hidden',
    marginTop: '16px',
  },
  progressFill: {
    height: '100%',
    background: 'linear-gradient(90deg, #7c3aed, #00d4ff)',
    borderRadius: '4px',
    transition: 'width 0.3s',
  },
  successMessage: {
    background: 'rgba(34, 197, 94, 0.1)',
    border: '1px solid rgba(34, 197, 94, 0.3)',
    borderRadius: '12px',
    padding: '16px 20px',
    marginTop: '16px',
    color: '#4ade80',
    fontSize: '0.95rem',
  },
  errorMessage: {
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: '12px',
    padding: '16px 20px',
    marginTop: '16px',
    color: '#f87171',
    fontSize: '0.95rem',
  },
  faqItem: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: '12px',
    padding: '20px 24px',
    marginBottom: '12px',
  },
  faqQuestion: {
    fontSize: '1rem',
    fontWeight: 600,
    color: '#fff',
    marginBottom: '8px',
  },
  faqAnswer: {
    fontSize: '0.9rem',
    color: '#94a3b8',
    lineHeight: 1.6,
  },
};

export default function LiverGuidePage() {
  const [username, setUsername] = useState('');
  const [materialType, setMaterialType] = useState('generic');
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [success, setSuccess] = useState('');
  const [error, setError] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [uploadCount, setUploadCount] = useState(0);
  const fileRef = useRef(null);

  const handleUpload = useCallback(async (file) => {
    if (!file) return;
    if (!file.type.startsWith('video/')) {
      setError('動画ファイル（MP4, MOV等）を選択してください');
      return;
    }
    if (!username.trim()) {
      setError('TikTokユーザー名を入力してください');
      return;
    }
    if (file.size > 500 * 1024 * 1024) {
      setError('ファイルサイズは500MB以下にしてください');
      return;
    }

    setUploading(true);
    setProgress(0);
    setError('');
    setSuccess('');

    try {
      // Step 1: Get SAS upload URL
      const sasRes = await fetch(`${API_BASE}/liver-guide/upload-sas`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tiktok_username: username.trim(),
          filename: file.name,
          material_type: materialType,
        }),
      });
      if (!sasRes.ok) {
        const errData = await sasRes.json().catch(() => ({}));
        throw new Error(errData.detail || `SAS取得失敗 (${sasRes.status})`);
      }
      const sasData = await sasRes.json();

      // Step 2: Upload directly to Azure Blob
      await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            setProgress(Math.round((e.loaded / e.total) * 90)); // 90% for upload
          }
        };
        xhr.onload = () => xhr.status < 400 ? resolve() : reject(new Error(`Upload failed: ${xhr.status}`));
        xhr.onerror = () => reject(new Error('ネットワークエラー'));
        xhr.open('PUT', sasData.upload_url);
        xhr.setRequestHeader('x-ms-blob-type', 'BlockBlob');
        xhr.setRequestHeader('Content-Type', file.type);
        xhr.send(file);
      });

      setProgress(95);

      // Step 3: Register material in DB
      const regRes = await fetch(`${API_BASE}/liver-guide/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tiktok_username: username.trim(),
          blob_url: sasData.blob_url,
          material_id: sasData.material_id,
          material_type: materialType,
        }),
      });
      if (!regRes.ok) {
        const errData = await regRes.json().catch(() => ({}));
        throw new Error(errData.detail || `登録失敗 (${regRes.status})`);
      }

      setProgress(100);
      setUploadCount(prev => prev + 1);
      setSuccess(`✅ 素材「${file.name}」のアップロードが完了しました！（合計 ${uploadCount + 1} 本）`);
    } catch (err) {
      setError(`❌ アップロードに失敗しました: ${err.message}`);
    } finally {
      setUploading(false);
      setTimeout(() => setProgress(0), 2000);
    }
  }, [username, materialType, uploadCount]);

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer?.files?.[0];
    if (file) handleUpload(file);
  };

  return (
    <div style={styles.page}>
      {/* Hero */}
      <div style={styles.hero}>
        <h1 style={styles.heroTitle}>AIライバー素材を撮影しよう</h1>
        <p style={styles.heroSubtitle}>
          たった1回の撮影で、AIがあなたの顔で何百本もの商品紹介動画を自動生成します。
          商品を持たない汎用素材を撮影するだけでOK！
        </p>
      </div>

      <div style={styles.container}>
        {/* 3 Steps */}
        <div style={styles.section}>
          <h2 style={styles.sectionTitle}>
            <span>⚡</span> 3ステップで完了
          </h2>
          <div style={styles.stepsGrid}>
            <div style={styles.stepCard}>
              <div style={styles.stepNumber}>1</div>
              <div style={styles.stepTitle}>撮影する</div>
              <div style={styles.stepDesc}>
                下のガイドに従って、商品を持たずにカメラに向かって話す動画を撮影
              </div>
            </div>
            <div style={styles.stepCard}>
              <div style={styles.stepNumber}>2</div>
              <div style={styles.stepTitle}>アップロード</div>
              <div style={styles.stepDesc}>
                このページの下部から動画ファイルをアップロード（30秒〜1分）
              </div>
            </div>
            <div style={styles.stepCard}>
              <div style={styles.stepNumber}>3</div>
              <div style={styles.stepTitle}>AI量産開始</div>
              <div style={styles.stepDesc}>
                あなたの素材を使って、どんな商品の紹介動画もAIが自動生成！
              </div>
            </div>
          </div>
        </div>

        {/* Shooting Guide */}
        <div style={styles.section}>
          <h2 style={styles.sectionTitle}>
            <span>🎬</span> 撮影ガイド
          </h2>

          {/* Camera Settings */}
          <div style={styles.guideCard}>
            <div style={styles.guideTitle}>📱 カメラ設定</div>
            <ul style={styles.guideList}>
              <li style={styles.guideItem}>
                <span style={{ color: '#00d4ff', flexShrink: 0 }}>●</span>
                <span><strong>向き:</strong> 縦型（9:16）で撮影。TikTok/Reelsと同じ向き</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#00d4ff', flexShrink: 0 }}>●</span>
                <span><strong>画角:</strong> バストアップ（胸から上）。顔全体がしっかり映るように</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#00d4ff', flexShrink: 0 }}>●</span>
                <span><strong>解像度:</strong> 1080×1920（フルHD）以上推奨</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#00d4ff', flexShrink: 0 }}>●</span>
                <span><strong>長さ:</strong> 30秒〜1分程度。3〜5パターン撮ると◎</span>
              </li>
            </ul>
          </div>

          {/* Lighting & Background */}
          <div style={styles.guideCard}>
            <div style={styles.guideTitle}>💡 照明 & 背景</div>
            <ul style={styles.guideList}>
              <li style={styles.guideItem}>
                <span style={{ color: '#f472b6', flexShrink: 0 }}>●</span>
                <span><strong>照明:</strong> 顔が明るく、影が少ない状態。リングライトや窓際の自然光がベスト</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#f472b6', flexShrink: 0 }}>●</span>
                <span><strong>背景:</strong> シンプルな背景（白壁、部屋、スタジオ）。ごちゃごちゃしない場所</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#f472b6', flexShrink: 0 }}>●</span>
                <span><strong>逆光NG:</strong> 窓を背にしない。顔が暗くなるとAI処理の品質が下がります</span>
              </li>
            </ul>
          </div>

          {/* Performance */}
          <div style={styles.guideCard}>
            <div style={styles.guideTitle}>🎭 演技のポイント</div>
            <ul style={styles.guideList}>
              <li style={styles.guideItem}>
                <span style={{ color: '#4ade80', flexShrink: 0 }}>●</span>
                <span><strong>カメラ目線:</strong> レンズをまっすぐ見て話す。視聴者に語りかける感じ</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#4ade80', flexShrink: 0 }}>●</span>
                <span><strong>商品を持たない:</strong> 手は自然に身振りする程度。何も持たないでOK</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#4ade80', flexShrink: 0 }}>●</span>
                <span><strong>表情豊かに:</strong> 笑顔、驚き、感動など表情を大きめに。AIが口の動きを差し替えるので、話す内容は何でもOK</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#4ade80', flexShrink: 0 }}>●</span>
                <span><strong>自然な動き:</strong> 完全に静止せず、軽いジェスチャーや頷きを入れると自然に仕上がります</span>
              </li>
            </ul>
          </div>

          {/* Good vs Bad Examples */}
          <div style={styles.goodBadGrid}>
            <div style={styles.goodCard}>
              <div style={{ ...styles.goodBadTitle, color: '#4ade80' }}>✅ 良い例</div>
              <ul style={styles.goodBadList}>
                <li>✓ 顔全体が明るく映っている</li>
                <li>✓ カメラ目線で話している</li>
                <li>✓ 手に何も持っていない</li>
                <li>✓ 背景がシンプル</li>
                <li>✓ 表情が豊かで自然</li>
                <li>✓ 縦型で撮影</li>
                <li>✓ 30秒〜1分の長さ</li>
              </ul>
            </div>
            <div style={styles.badCard}>
              <div style={{ ...styles.goodBadTitle, color: '#f87171' }}>❌ 悪い例</div>
              <ul style={styles.goodBadList}>
                <li>✗ 顔が暗い・逆光</li>
                <li>✗ 横を向いている</li>
                <li>✗ 特定の商品を持っている</li>
                <li>✗ 背景がごちゃごちゃ</li>
                <li>✗ 無表情・棒読み</li>
                <li>✗ 横型で撮影</li>
                <li>✗ 5秒以下の短すぎる動画</li>
              </ul>
            </div>
          </div>
        </div>

        {/* Why this works */}
        <div style={styles.section}>
          <div style={styles.guideCard}>
            <div style={styles.guideTitle}>🤖 なぜ商品を持たなくていいの？</div>
            <p style={{ color: '#94a3b8', lineHeight: 1.8, margin: 0 }}>
              AIが以下を自動で処理するからです：
            </p>
            <ul style={{ ...styles.guideList, marginTop: '12px' }}>
              <li style={styles.guideItem}>
                <span style={{ color: '#7c3aed', flexShrink: 0 }}>🎙️</span>
                <span><strong>音声:</strong> ElevenLabs AIがあなたの声で商品紹介の台本を読み上げ</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#7c3aed', flexShrink: 0 }}>👄</span>
                <span><strong>リップシンク:</strong> HeyGen AIが口の動きを音声に完全に合わせる</span>
              </li>
              <li style={styles.guideItem}>
                <span style={{ color: '#7c3aed', flexShrink: 0 }}>🖼️</span>
                <span><strong>商品表示:</strong> 商品画像はオーバーレイで自然に配置</span>
              </li>
            </ul>
            <p style={{ color: '#94a3b8', lineHeight: 1.8, marginTop: '16px', marginBottom: 0 }}>
              つまり、<strong style={{ color: '#fff' }}>あなたの「自然に動いている顔の映像」</strong>さえあれば、
              どんな商品の紹介動画もAIが自動で作れます！
            </p>
          </div>
        </div>

        {/* Upload Section */}
        <div style={styles.uploadSection}>
          <h2 style={{ ...styles.sectionTitle, justifyContent: 'center' }}>
            <span>📤</span> 素材をアップロード
          </h2>

          {/* Username input */}
          <div style={styles.inputGroup}>
            <label style={styles.label}>TikTokユーザー名 *</label>
            <input
              type="text"
              placeholder="例: ryukyogoku"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              style={styles.input}
            />
          </div>

          {/* Material type */}
          <div style={styles.inputGroup}>
            <label style={styles.label}>素材タイプ</label>
            <select
              value={materialType}
              onChange={(e) => setMaterialType(e.target.value)}
              style={styles.select}
            >
              <option value="generic">汎用（商品なし・トーク）</option>
              <option value="greeting">挨拶・オープニング</option>
              <option value="reaction">リアクション・驚き</option>
              <option value="closing">エンディング・締め</option>
            </select>
          </div>

          {/* Dropzone */}
          <div
            style={{
              ...styles.dropzone,
              ...(dragOver ? styles.dropzoneActive : {}),
              ...(uploading ? { opacity: 0.6, pointerEvents: 'none' } : {}),
            }}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => !uploading && fileRef.current?.click()}
          >
            <input
              ref={fileRef}
              type="file"
              accept="video/*"
              style={{ display: 'none' }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleUpload(file);
                e.target.value = '';
              }}
            />
            {uploading ? (
              <>
                <div style={styles.dropzoneText}>⏳ アップロード中...</div>
                <div style={styles.progressBar}>
                  <div style={{ ...styles.progressFill, width: `${progress}%` }} />
                </div>
                <div style={{ ...styles.dropzoneHint, marginTop: '8px' }}>{progress}%</div>
              </>
            ) : (
              <>
                <div style={{ fontSize: '2.5rem', marginBottom: '12px' }}>🎬</div>
                <div style={styles.dropzoneText}>
                  ここに動画ファイルをドラッグ＆ドロップ
                </div>
                <div style={styles.dropzoneHint}>
                  またはクリックしてファイルを選択（MP4, MOV / 最大500MB）
                </div>
              </>
            )}
          </div>

          {/* Success/Error messages */}
          {success && <div style={styles.successMessage}>{success}</div>}
          {error && <div style={styles.errorMessage}>{error}</div>}

          {uploadCount > 0 && (
            <div style={{ textAlign: 'center', marginTop: '20px', color: '#94a3b8', fontSize: '0.9rem' }}>
              📊 このセッションでアップロードした素材: <strong style={{ color: '#fff' }}>{uploadCount}本</strong>
            </div>
          )}
        </div>

        {/* FAQ */}
        <div style={{ ...styles.section, marginTop: '60px' }}>
          <h2 style={styles.sectionTitle}>
            <span>❓</span> よくある質問
          </h2>

          <div style={styles.faqItem}>
            <div style={styles.faqQuestion}>Q: 話す内容は何でもいいの？</div>
            <div style={styles.faqAnswer}>
              はい！AIが音声を完全に差し替えるので、話す内容は関係ありません。
              「今日は天気がいいですね」でも「1,2,3,4...」でもOK。
              大事なのは自然な表情と口の動きです。
            </div>
          </div>

          <div style={styles.faqItem}>
            <div style={styles.faqQuestion}>Q: 何本くらい撮ればいい？</div>
            <div style={styles.faqAnswer}>
              最低1本あればAI動画生成は可能です。ただし、3〜5パターン（表情やジェスチャーが違うもの）を
              撮っておくと、より自然でバリエーション豊かな動画が生成できます。
            </div>
          </div>

          <div style={styles.faqItem}>
            <div style={styles.faqQuestion}>Q: スマホで撮影してもいい？</div>
            <div style={styles.faqAnswer}>
              もちろん！最近のスマホは十分な画質があります。
              インカメラよりアウトカメラの方が画質が良いので、三脚やスマホスタンドを使うのがおすすめです。
            </div>
          </div>

          <div style={styles.faqItem}>
            <div style={styles.faqQuestion}>Q: 撮影した素材はどう使われるの？</div>
            <div style={styles.faqAnswer}>
              あなたの素材は、ECブランドの商品紹介動画をAIで自動生成する際に使用されます。
              あなたの顔で、様々な商品を紹介する動画が量産されます。
              利用規約に基づき、適切に管理されます。
            </div>
          </div>

          <div style={styles.faqItem}>
            <div style={styles.faqQuestion}>Q: 報酬はどうなるの？</div>
            <div style={styles.faqAnswer}>
              素材が商品動画に使用された回数に応じて報酬が発生します。
              詳細は個別にご案内いたします。
            </div>
          </div>
        </div>

        {/* Footer */}
        <div style={{ textAlign: 'center', padding: '40px 0 60px', color: '#64748b', fontSize: '0.85rem' }}>
          <p>© 2024 AitherHub — AI Video Generation Platform</p>
          <p style={{ marginTop: '8px' }}>
            ご質問は <a href="mailto:support@aitherhub.com" style={{ color: '#7c3aed' }}>support@aitherhub.com</a> まで
          </p>
        </div>
      </div>
    </div>
  );
}
