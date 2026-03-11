# 調査メモ

## 症状
1. ryukyogoku-20260128-1005: エラー（24分経過）、11時間16分処理中 → 圧縮ステップ5%で639分経過
2. ryukyogoku-20260225-0601: 「解析を再開しました」→ 圧縮中2%で1007分経過（16時間以上）
3. 左サイドバーに複数のエラー動画あり

## 共通パターン
- アップロード完了 → キュー投入 → 「動画を1080pに圧縮中...」でスタック
- 進捗が2-5%から進まない
- stuck_video_monitorが再開を試みるが、また圧縮に戻る（無限ループ）

## 調査対象
- process_video.py の圧縮ステップ（fire_compress_async）
- stuck_video_monitor のリカバリロジック
- queue_worker.py の visibility timeout
- Worker の DB 接続問題
