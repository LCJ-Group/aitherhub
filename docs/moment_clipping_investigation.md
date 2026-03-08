# Moment-based Clipping 調査結果

## 既存の基盤

### Worker側（既に実装済み）
1. **screen_moment_extractor.py** - GPT-4o Visionで画面収録フレームを分析
   - purchase_popup 検出
   - product_viewers_popup 検出
   - viewer_spike 検出
   - comment_spike 検出
   - → video_sales_moments テーブルに保存

2. **process_video.py STEP 5.7** - screen_recording の場合のみ実行
   - detect_screen_moments() → bulk_insert_sales_moments(source="screen")

### Backend API側（既に実装済み）
1. **sales_moment_clip_service.py** - スパイク検出 + クリップ候補生成
2. **sales_clip_service.py** - フェーズ単位のスコアリング
3. **hook_detection_service.py** - TikTok向けフック検出
4. **moment_engine.py** - 拡張機能のモーメント検出（ext_sessions用）

### Frontend側（既に実装済み）
1. **SalesMomentClips.jsx** - スパイク検出クリップUI
2. **SalesClipCandidates.jsx** - フェーズベースクリップUI
3. **LightningClipEditor.jsx** - Trim/Caption/Export
4. **ClipFeedbackPanel.jsx** - AI学習用フィードバック

## 7機能の実装方針

### 既存で対応済み（拡張が必要）
- Purchase Popup Clip → screen_moment_extractor で purchase_popup 検出済み
- Comment Explosion Clip → comment_spike 検出済み
- Viewer Spike Clip → viewer_spike 検出済み

### 新規実装が必要
- Gift / Like Animation Clip → screen_moment_extractor に追加
- Chat Highlight Overlay → 新規サービス
- Product Reveal Detection → screen_moment_extractor に追加
- Auto Zoom → Worker側のFFmpeg処理 + フロントエンド

## 結論
既存の screen_moment_extractor.py が Purchase Popup / Comment Spike / Viewer Spike を
既に検出している。フロントエンドで moment_type_detail 別にグループ化して表示する
UIを作れば、ユーザーが求めている体験の大部分が実現できる。
