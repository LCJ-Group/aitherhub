/**
 * Liver Clone Page - UI Translation Dictionary
 * Supports: ja (Japanese), en (English), zh (Simplified Chinese)
 */
const LC_TEXTS = {
  // Header
  subtitle: {
    ja: "リアルタイム顔変換 + 声変換 ライブ配信",
    en: "Real-time Face Swap + Voice Conversion Live Streaming",
    zh: "实时换脸 + 声音转换 直播",
  },
  statusConfiguring: {
    ja: "設定中", en: "Configuring", zh: "配置中",
  },
  statusIdle: {
    ja: "待機中", en: "Idle", zh: "待机中",
  },
  gpuReady: {
    ja: "GPU Ready", en: "GPU Ready", zh: "GPU 就绪",
  },
  gpuOffline: {
    ja: "GPU Offline", en: "GPU Offline", zh: "GPU 离线",
  },
  // Preview area
  streaming: {
    ja: "配信中...", en: "Streaming...", zh: "直播中...",
  },
  checkPlatform: {
    ja: "プラットフォームで確認してください",
    en: "Check on your streaming platform",
    zh: "请在平台上确认",
  },
  previewConnecting: {
    ja: "プレビュー接続中...", en: "Connecting preview...", zh: "预览连接中...",
  },
  clickToChange: {
    ja: "クリックで変更", en: "Click to change", zh: "点击更换",
  },
  uploadFace: {
    ja: "顔写真をアップロード", en: "Upload face photo", zh: "上传人脸照片",
  },
  clickOrButton: {
    ja: "クリックまたは右の⬆ボタン", en: "Click or use the ⬆ button", zh: "点击或使用右侧⬆按钮",
  },
  startPreview: {
    ja: "プレビュー開始", en: "Start Preview", zh: "开始预览",
  },
  stop: {
    ja: "停止", en: "Stop", zh: "停止",
  },
  record: {
    ja: "録画", en: "Record", zh: "录制",
  },
  recordTooltip: {
    ja: "9:16縦動画で録画開始", en: "Start recording in 9:16 portrait", zh: "以9:16竖屏录制",
  },
  downloadTooltip: {
    ja: "9:16縦動画をダウンロード", en: "Download 9:16 portrait video", zh: "下载9:16竖屏视频",
  },
  // Metrics
  streamMetrics: {
    ja: "配信メトリクス", en: "Stream Metrics", zh: "直播指标",
  },
  latency: {
    ja: "遅延", en: "Latency", zh: "延迟",
  },
  mode: {
    ja: "モード", en: "Mode", zh: "模式",
  },
  speechCount: {
    ja: "発話数", en: "Speech Count", zh: "发话数",
  },
  speaking: {
    ja: "🗣️ 発話中...", en: "🗣️ Speaking...", zh: "🗣️ 说话中...",
  },
  manualSpeak: {
    ja: "手動発話", en: "Manual Speak", zh: "手动发话",
  },
  speakPlaceholder: {
    ja: "テキストを入力して読み上げ...", en: "Enter text to speak...", zh: "输入文字朗读...",
  },
  // Tabs
  tabSettings: {
    ja: "設定", en: "Settings", zh: "设置",
  },
  tabComments: {
    ja: "コメント", en: "Comments", zh: "评论",
  },
  tabAutopilot: {
    ja: "Auto Pilot", en: "Auto Pilot", zh: "Auto Pilot",
  },
  // Face Settings
  faceSettings: {
    ja: "顔設定", en: "Face Settings", zh: "人脸设置",
  },
  sourceFaceImage: {
    ja: "ソース顔画像", en: "Source Face Image", zh: "源人脸图片",
  },
  enterImageUrl: {
    ja: "または画像URLを入力...", en: "Or enter image URL...", zh: "或输入图片URL...",
  },
  saveWithName: {
    ja: "名前をつけて保存...", en: "Save with name...", zh: "命名并保存...",
  },
  save: {
    ja: "保存", en: "Save", zh: "保存",
  },
  savedFaces: {
    ja: "保存済み顔", en: "Saved Faces", zh: "已保存的脸",
  },
  // Stream Settings
  streamSettings: {
    ja: "配信設定", en: "Stream Settings", zh: "直播设置",
  },
  inputRtmp: {
    ja: "入力RTMP URL（OBSから）", en: "Input RTMP URL (from OBS)", zh: "输入RTMP URL（来自OBS）",
  },
  outputRtmp: {
    ja: "出力RTMP URL（配信先）", en: "Output RTMP URL (destination)", zh: "输出RTMP URL（目标平台）",
  },
  resolution: {
    ja: "解像度", en: "Resolution", zh: "分辨率",
  },
  // Voice Settings
  voiceSettings: {
    ja: "音声設定", en: "Voice Settings", zh: "语音设置",
  },
  voiceIdLabel: {
    ja: "Voice ID（ElevenLabs）", en: "Voice ID (ElevenLabs)", zh: "Voice ID（ElevenLabs）",
  },
  selectSavedVoice: {
    ja: "-- 保存済みVoice IDを選択 --", en: "-- Select saved Voice ID --", zh: "-- 选择已保存的Voice ID --",
  },
  saveVoiceName: {
    ja: "名前を付けて保存...", en: "Save with name...", zh: "命名并保存...",
  },
  validating: {
    ja: "検証中...", en: "Validating...", zh: "验证中...",
  },
  voiceConfirmed: {
    ja: "Voice ID確認済み", en: "Voice ID confirmed", zh: "Voice ID已确认",
  },
  delete: {
    ja: "削除", en: "Delete", zh: "删除",
  },
  realtimeVoice: {
    ja: "リアルタイム音声変換", en: "Real-time Voice Conversion", zh: "实时语音转换",
  },
  voiceDesc: {
    ja: "マイク音声をAI声に変換（プレビュー時）",
    en: "Convert mic audio to AI voice (during preview)",
    zh: "将麦克风音频转换为AI声音（预览时）",
  },
  voiceConverting: {
    ja: "音声変換中...", en: "Converting voice...", zh: "语音转换中...",
  },
  stability: {
    ja: "安定性", en: "Stability", zh: "稳定性",
  },
  similarity: {
    ja: "類似度", en: "Similarity", zh: "相似度",
  },
  // Mode settings
  modeLabel: {
    ja: "モード", en: "Mode", zh: "模式",
  },
  modeManual: {
    ja: "手動", en: "Manual", zh: "手动",
  },
  modeManualDesc: {
    ja: "人が喋る→変換", en: "You speak → convert", zh: "人说话→转换",
  },
  modeAuto: {
    ja: "自動", en: "Auto", zh: "自动",
  },
  modeAutoDesc: {
    ja: "AI自動配信", en: "AI auto streaming", zh: "AI自动直播",
  },
  modeHybrid: {
    ja: "ハイブリッド", en: "Hybrid", zh: "混合",
  },
  modeHybridDesc: {
    ja: "喋る時は変換、黙ったらAI", en: "Convert when speaking, AI when silent", zh: "说话时转换，沉默时AI",
  },
  vadThreshold: {
    ja: "VAD閾値", en: "VAD Threshold", zh: "VAD阈值",
  },
  silenceTimeout: {
    ja: "無音タイムアウト", en: "Silence Timeout", zh: "静音超时",
  },
  languageLabel: {
    ja: "言語", en: "Language", zh: "语言",
  },
  // Product section
  productIntro: {
    ja: "商品紹介", en: "Product Introduction", zh: "商品介绍",
  },
  autoProductDetect: {
    ja: "自動商品検出", en: "Auto Product Detection", zh: "自动商品检测",
  },
  autoProductDesc: {
    ja: "カメラに商品を映すと自動で紹介",
    en: "Auto-introduce when product shown to camera",
    zh: "将商品对准镜头自动介绍",
  },
  detectingProduct: {
    ja: "商品を検出中...", en: "Detecting product...", zh: "检测商品中...",
  },
  uploadProduct: {
    ja: "商品画像をアップロード", en: "Upload product image", zh: "上传商品图片",
  },
  identifyingProduct: {
    ja: "AIが商品を識別中...", en: "AI identifying product...", zh: "AI识别商品中...",
  },
  productNamePlaceholder: {
    ja: "商品名（任意）", en: "Product name (optional)", zh: "商品名称（选填）",
  },
  productInfoPlaceholder: {
    ja: "商品情報（任意：価格、特徴など）",
    en: "Product info (optional: price, features)",
    zh: "商品信息（选填：价格、特点等）",
  },
  regenerate: {
    ja: "再生成", en: "Regenerate", zh: "重新生成",
  },
  readingAloud: {
    ja: "読み上げ中...", en: "Reading aloud...", zh: "朗读中...",
  },
  readAloud: {
    ja: "読み上げ", en: "Read Aloud", zh: "朗读",
  },
  generatingScript: {
    ja: "スクリプト生成中...", en: "Generating script...", zh: "生成脚本中...",
  },
  generateScript: {
    ja: "AIスクリプトを生成", en: "Generate AI Script", zh: "生成AI脚本",
  },
  productUploadHint: {
    ja: "商品画像をアップロードすると、AIが自動で商品紹介スクリプトを生成します。",
    en: "Upload a product image and AI will auto-generate an introduction script.",
    zh: "上传商品图片，AI将自动生成商品介绍脚本。",
  },
  // Session controls
  createSession: {
    ja: "セッション作成", en: "Create Session", zh: "创建会话",
  },
  startStream: {
    ja: "配信開始", en: "Start Stream", zh: "开始直播",
  },
  stopStream: {
    ja: "配信停止", en: "Stop Stream", zh: "停止直播",
  },
  // Comments tab
  commentResponse: {
    ja: "コメント返答", en: "Comment Response", zh: "评论回复",
  },
  commentPlaceholder: {
    ja: "コメントを入力して返答を生成...",
    en: "Enter comment to generate response...",
    zh: "输入评论以生成回复...",
  },
  noComments: {
    ja: "コメントがまだありません", en: "No comments yet", zh: "暂无评论",
  },
  // Auto Pilot tab
  autopilotSettings: {
    ja: "Auto Pilot設定", en: "Auto Pilot Settings", zh: "Auto Pilot设置",
  },
  personaName: {
    ja: "ペルソナ名", en: "Persona Name", zh: "角色名称",
  },
  personaPlaceholder: {
    ja: "例: KYOGOKU Ryu", en: "e.g. KYOGOKU Ryu", zh: "例：KYOGOKU Ryu",
  },
  speakingStyle: {
    ja: "話し方・スタイル", en: "Speaking Style", zh: "说话风格",
  },
  stylePlaceholder: {
    ja: "例: Professional yet friendly, high energy, confident...",
    en: "e.g. Professional yet friendly, high energy, confident...",
    zh: "例：专业友善、高能量、自信...",
  },
  openingScript: {
    ja: "オープニングスクリプト", en: "Opening Script", zh: "开场脚本",
  },
  openingPlaceholder: {
    ja: "配信開始時に自動で話す内容...",
    en: "Content to auto-speak when stream starts...",
    zh: "直播开始时自动说的内容...",
  },
  autopilotNote: {
    ja: "※ Auto Pilotはハイブリッドモードで人が黙っている時に自動で台本を生成して話します。",
    en: "※ Auto Pilot generates and speaks scripts automatically when silent in hybrid mode.",
    zh: "※ Auto Pilot在混合模式下，当人沉默时会自动生成并朗读脚本。",
  },
  // Error messages
  errorVoiceNotSet: {
    ja: "Voice IDが設定されていません", en: "Voice ID is not set", zh: "未设置Voice ID",
  },
  errorTtsFailed: {
    ja: "音声生成に失敗しました", en: "Voice generation failed", zh: "语音生成失败",
  },
  errorProductScript: {
    ja: "商品スクリプト生成に失敗しました",
    en: "Product script generation failed",
    zh: "商品脚本生成失败",
  },
  errorSourceUpload: {
    ja: "ソース顔のアップロードに失敗しました",
    en: "Failed to upload source face",
    zh: "上传源人脸失败",
  },
  errorCameraNotFound: {
    ja: "📷 カメラが検出できません\n\n• ブラウザのカメラ許可を「許可」にしてください\n• カメラが接続されているか確認してください\n• 他のアプリがカメラを使用中の場合は閉じてください",
    en: "📷 Camera not detected\n\n• Please allow camera access in your browser\n• Check that your camera is connected\n• Close other apps that may be using the camera",
    zh: "📷 未检测到摄像头\n\n• 请在浏览器中允许摄像头访问\n• 确认摄像头已连接\n• 关闭可能正在使用摄像头的其他应用",
  },
  errorCameraPermission: {
    ja: "📷 カメラのアクセスが拒否されました\n\nブラウザの設定でカメラの許可を「許可」に変更してから、ページを再読み込みしてください。",
    en: "📷 Camera access denied\n\nPlease change camera permission to 'Allow' in your browser settings, then reload the page.",
    zh: "📷 摄像头访问被拒绝\n\n请在浏览器设置中将摄像头权限改为"允许"，然后重新加载页面。",
  },
  errorCameraInUse: {
    ja: "📷 カメラが他のアプリで使用中です\n\n他のアプリ（Zoom、Teams等）を閉じてから再試行してください。",
    en: "📷 Camera is in use by another app\n\nPlease close other apps (Zoom, Teams, etc.) and try again.",
    zh: "📷 摄像头正被其他应用使用\n\n请关闭其他应用（Zoom、Teams等）后重试。",
  },
  // UI language label
  uiLanguage: {
    ja: "表示言語", en: "UI Language", zh: "显示语言",
  },
  // Quality
  qualityLabel: {
    ja: "品質", en: "Quality", zh: "品质",
  },
  qualityHigh: {
    ja: "High", en: "High", zh: "高",
  },
  qualityMedium: {
    ja: "Medium", en: "Medium", zh: "中",
  },
  qualityLow: {
    ja: "Low", en: "Low", zh: "低",
  },
  // Recording
  recording: {
    ja: "録画中", en: "Recording", zh: "录制中",
  },
  stopRecording: {
    ja: "録画停止", en: "Stop Recording", zh: "停止录制",
  },
  // Misc
  noImage: {
    ja: "No Image", en: "No Image", zh: "无图片",
  },
  creating: {
    ja: "作成中...", en: "Creating...", zh: "创建中...",
  },
  starting: {
    ja: "開始中...", en: "Starting...", zh: "启动中...",
  },
};

/**
 * Get translated text for the given key and UI language.
 * Falls back to English if key or language not found.
 */
export function lcText(key, uiLang = "ja") {
  const entry = LC_TEXTS[key];
  if (!entry) return key;
  return entry[uiLang] || entry["en"] || entry["ja"] || key;
}

export default LC_TEXTS;
