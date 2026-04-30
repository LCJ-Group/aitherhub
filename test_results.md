# 採点者アカウント制 テスト結果

## バックエンドAPI テスト

| テスト項目 | 結果 | 詳細 |
|---|---|---|
| 採点者作成 (POST /admin/reviewers) | OK | reviewer_id=40, テスト採点者 |
| 採点者ログイン (POST /reviewer/login) | OK | JWT token + session_id返却 |
| 採点者情報 (GET /reviewer/me) | OK | セッション情報 + 統計 |
| フィードバック取得 (GET /reviewer/feedbacks) | OK | 8,474件（クリップあり） |
| 採点 (PUT /reviewer/rate/{video_id}/{phase_index}) | OK | reviewer_id=40で記録 |
| ログアウト (POST /reviewer/logout) | OK | セッション終了 + clips_reviewed記録 |
| 管理者: 採点者一覧 (GET /admin/reviewers) | OK | 統計付き |
| 管理者: セッション一覧 (GET /admin/review-sessions) | OK | duration_minutes記録 |
| 管理者: feedbacksにreviewer_name (GET /admin/feedbacks) | OK | reviewer_name表示 |
| 管理者: reviewer_idフィルタ | OK | 特定採点者の採点のみ表示 |
| バリデーション (rating=0) | OK | 400エラー返却 |

## フロントエンド テスト

| テスト項目 | 結果 | 詳細 |
|---|---|---|
| /reviewer ログイン画面 | OK | メール+パスワード入力 |
| ログイン後の採点UI | OK | セッション時間、統計、フィードバック一覧表示 |
| 星評価ボタン | OK | 1-5点の星ボタン表示 |
| フィルタ（未採点/全て/採点済み/自分の採点） | OK | セレクトボックス |
| クリップフィルタ | OK | クリップあり/なし |
| ログアウトボタン | OK | 表示確認 |
