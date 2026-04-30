# 採点者アカウント制 実装TODO

## Phase 1: DB設計・バックエンド
- [ ] reviewersテーブル作成（main_startup.py）
- [ ] review_sessionsテーブル作成（main_startup.py）
- [ ] reviewer認証API（reviewer_auth.py新規作成）
  - [ ] POST /api/v1/reviewer/login — ログイン+セッション開始
  - [ ] POST /api/v1/reviewer/logout — ログアウト+セッション終了
  - [ ] GET /api/v1/reviewer/me — 現在のレビュアー情報
  - [ ] POST /api/v1/reviewer/heartbeat — セッション生存確認
- [ ] 採点API修正（admin.py admin_rate_phase）
  - [ ] reviewer_idをvideo_phasesに記録
  - [ ] review_sessionsのclips_reviewedを更新
- [ ] 管理者用reviewer管理API（admin.py）
  - [ ] POST /admin/reviewers — 採点者アカウント作成
  - [ ] GET /admin/reviewers — 採点者一覧+統計
  - [ ] PUT /admin/reviewers/{id} — 採点者編集
  - [ ] DELETE /admin/reviewers/{id} — 採点者無効化
  - [ ] GET /admin/reviewer-stats — 採点者統計ダッシュボード
  - [ ] GET /admin/review-sessions — セッション一覧

## Phase 2: フロントエンド — 採点者専用UI
- [ ] ReviewerLogin.jsx — ログイン画面
- [ ] ReviewerDashboard.jsx — 採点者専用ダッシュボード
  - [ ] 未採点クリップ一覧
  - [ ] 採点UI（既存のFeedbackCard UIをベース）
  - [ ] 自分の統計（今日の採点数、累計、平均評価）
  - [ ] セッション情報（開始時刻、経過時間）
- [ ] App.jsx — /reviewer ルート追加

## Phase 3: フロントエンド — 管理者ダッシュボード
- [ ] AdminDashboard.jsx — 「採点者」タブ追加
  - [ ] 採点者一覧テーブル
  - [ ] 採点者作成フォーム
  - [ ] 採点者ごとの統計（採点数、平均評価、分布、作業時間）
  - [ ] セッション履歴
  - [ ] feedbacksタブに「採点者」フィルタ追加

## Phase 4: 既存フローとの統合
- [ ] video_phasesテーブルにreviewer_idカラム追加
- [ ] 既存の採点データは reviewer_id=NULL（不明）として保持
- [ ] admin_rate_phaseにreviewer_id対応追加
- [ ] get_all_feedbacksにreviewer_idフィルタ追加
