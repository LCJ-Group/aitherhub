# PersonaPage UI Issues

## 現状
- PersonaPage.jsx (789行) - 完全なUI実装あり
- personaService.js - APIクライアント実装あり
- ページは /persona ルートで表示されるが、画面が灰色（空白）

## 問題点
1. ページが灰色で何も表示されない → ログインが必要？またはAPIエラー？
2. ビデオタガーのloadVideos()は `/api/v1/admin/videos?limit=500` を使用
   - これはavailable-videosエンドポイントではなくadmin/videosを使用
   - ペルソナ専用のavailable-videosエンドポイントを使うべき
3. datasetPreviewのフィールド名の不一致:
   - フロント: total_videos, total_tokens, sample_examples
   - バックエンド: video_count, segment_count, preview_examples
4. tagged_video_idsの取得方法: persona.tagged_video_ids を使用
   - バックエンドのpersona detailではtagged_videosを返す

## タグ付けフロー
- handleToggleVideoTag: personaService.tagVideos / untagVideos を呼ぶ
- バックエンドのtag-videos / untag-videos エンドポイントは動作確認済み
