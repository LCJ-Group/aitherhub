# AitherHub Worker Deploy Guide

## 概要

このガイドは、新しいWorker（queue_worker.py + recovery + pipeline）を本番VMにデプロイする手順です。

**安全設計**: 全ての変更は段階的に適用でき、各ステップでrollbackが可能です。

> **v6 重要**: 全パスが `/opt/aitherhub` に統一されました。`/var/www/aitherhub` は使用しません。

---

## 前提条件

- SSH で Worker VM にアクセスできること
- `azureuser` ユーザーで操作すること
- リポジトリが `/opt/aitherhub` にクローンされていること

---

## Step 1: コードの更新

```bash
cd /opt/aitherhub
git pull origin master
```

**確認**: 新しいディレクトリが存在すること

```bash
ls -la shared/
ls -la worker/pipeline/
ls -la worker/recovery/
ls -la worker/entrypoints/
```

---

## Step 2: Python依存パッケージのインストール

```bash
# Pipeline用
pip3 install scenedetect[opencv] openai

# shared層用（既にインストール済みの可能性あり）
pip3 install python-dotenv sqlalchemy[asyncio] asyncpg azure-storage-queue azure-storage-blob
```

---

## Step 3: DB マイグレーション実行

```bash
# .envからDATABASE_URLを読み込む
source /opt/aitherhub/.env

# Phase 3: clip_jobs 正式化
psql "$DATABASE_URL" -f backend/migrations/add_clip_jobs_v1.sql

# Phase 4: パイプラインテーブル
psql "$DATABASE_URL" -f backend/migrations/add_pipeline_tables_v1.sql
```

**確認**: テーブルが作成されたこと

```bash
psql "$DATABASE_URL" -c "\dt video_*"
```

期待される出力に以下が含まれること:
- `video_scenes`
- `video_transcripts`
- `video_segments`
- `video_events`
- `video_sales_moments`
- `video_pipeline_runs`

---

## Step 4: 現在のWorkerを停止

```bash
# 現在のWorkerの状態を確認
sudo systemctl status simple-worker

# 現在処理中のジョブがないことを確認（ログを見る）
journalctl -u simple-worker -n 20 --no-pager

# Workerを停止（処理中のジョブがある場合は完了を待つ）
sudo systemctl stop simple-worker
```

---

## Step 5A: 安全テスト（手動起動）

**systemdを切り替える前に、まず手動で新Workerが起動するか確認します。**

```bash
cd /opt/aitherhub

# 環境変数を読み込む
source .env
export PYTHONPATH="/opt/aitherhub:/opt/aitherhub/worker/batch"

# 新Workerを手動起動（Ctrl+Cで停止可能）
python3 -m worker.entrypoints.queue_worker
```

**成功の判断基準**:

```
[startup]   FFMPEG: OK
[startup]   TEMP_DIR: OK
[startup]   QUEUE: OK
[startup]   DATABASE: OK
[startup] All checks passed. Worker is ready to start.
[worker] Queue Worker started
```

**失敗した場合**: エラーメッセージを確認し、Step 5B（rollback）へ。

---

## Step 5B: 問題があった場合のrollback

```bash
# 旧Workerに戻す
cd /opt/aitherhub
source .env
export PYTHONPATH="/opt/aitherhub:/opt/aitherhub/worker/batch"

# 旧Workerを手動起動
python3 worker/controller/simple_worker.py
```

または systemd で旧Workerを再起動:

```bash
sudo systemctl start simple-worker
```

---

## Step 6: systemdサービスの切替

**Step 5Aが成功した場合のみ実行。**

```bash
# 新しいsystemdサービスファイルをコピー
sudo cp deploy/simple-worker.service /etc/systemd/system/simple-worker.service
sudo cp deploy/worker-health.service /etc/systemd/system/worker-health.service

# systemdをリロード
sudo systemctl daemon-reload

# 新Workerを起動
sudo systemctl start simple-worker
sudo systemctl start worker-health

# 状態確認
sudo systemctl status simple-worker
sudo systemctl status worker-health

# ログ確認
journalctl -u simple-worker -f
```

---

## Step 7: Pipeline有効化（オプション）

**Pipelineはデフォルトで無効です。** 動画分析後にパイプラインを自動実行したい場合:

```bash
# .envファイルに追加
echo "PIPELINE_ENABLED=true" >> /opt/aitherhub/.env

# Workerを再起動
sudo systemctl restart simple-worker
```

**Pipeline専用ジョブ**を手動でキューに入れる場合:

```python
# Azure Queue に直接メッセージを送信
import json
from azure.storage.queue import QueueClient

client = QueueClient.from_connection_string(conn_str, "video-jobs")
client.send_message(json.dumps({
    "job_type": "video_pipeline",
    "video_id": "your-video-id",
    "blob_url": "https://...",
    "user_id": "your-user-id"
}))
```

---

## Step 8: 動画アップロードテスト

```bash
# 1. Health check確認
curl http://localhost:8081/health

# 2. テスト動画をアップロード（API経由）
curl -X POST http://localhost:8000/api/video/upload \
  -F "file=@test_video.mp4"

# 3. Workerログを監視
journalctl -u simple-worker -f
```

**成功の判断基準**:

```
[worker] Starting video analysis: video_id=xxx
[worker] Video analysis completed: video_id=xxx
```

Pipeline有効時は追加で:

```
[pipeline] Starting pipeline for video=xxx (7 steps)
[pipeline] [1/7] scene_detection — completed
[pipeline] [2/7] speech_extraction — completed
...
[pipeline] Pipeline finished for video=xxx
```

---

## 完全rollback手順

何か問題が起きた場合、以下の手順で完全に元に戻せます。

### 1. Workerを旧版に戻す

```bash
# 新Workerを停止
sudo systemctl stop simple-worker
sudo systemctl stop worker-health

# 旧systemdファイルに戻す（ExecStartを変更）
sudo sed -i 's|ExecStart=.*|ExecStart=/opt/aitherhub/.venv/bin/python /opt/aitherhub/worker/controller/simple_worker.py|' /etc/systemd/system/simple-worker.service
sudo systemctl daemon-reload
sudo systemctl start simple-worker
```

### 2. DB rollback（必要な場合のみ）

```bash
source /opt/aitherhub/.env

# パイプラインテーブルを削除
psql "$DATABASE_URL" -c "
DROP TABLE IF EXISTS video_pipeline_runs CASCADE;
DROP TABLE IF EXISTS video_sales_moments CASCADE;
DROP TABLE IF EXISTS video_events CASCADE;
DROP TABLE IF EXISTS video_segments CASCADE;
DROP TABLE IF EXISTS video_transcripts CASCADE;
DROP TABLE IF EXISTS video_scenes CASCADE;
"

# clip_jobs カラムを削除（注意: 通常は不要）
psql "$DATABASE_URL" -c "
ALTER TABLE video_clips
    DROP COLUMN IF EXISTS attempt_count,
    DROP COLUMN IF EXISTS max_attempts,
    DROP COLUMN IF EXISTS heartbeat_at,
    DROP COLUMN IF EXISTS started_at,
    DROP COLUMN IF EXISTS finished_at,
    DROP COLUMN IF EXISTS worker_id,
    DROP COLUMN IF EXISTS last_error_code,
    DROP COLUMN IF EXISTS last_error_message,
    DROP COLUMN IF EXISTS queue_message_id,
    DROP COLUMN IF EXISTS enqueued_at,
    DROP COLUMN IF EXISTS speed_factor,
    DROP COLUMN IF EXISTS duration_ms;
DROP VIEW IF EXISTS v_stale_clip_jobs;
DROP VIEW IF EXISTS v_dead_clip_jobs;
"
```

### 3. Pipeline無効化

```bash
# .envからPIPELINE_ENABLEDを削除
sed -i '/PIPELINE_ENABLED/d' /opt/aitherhub/.env
sudo systemctl restart simple-worker
```

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `ModuleNotFoundError: shared` | PYTHONPATHに `/opt/aitherhub` が含まれていない | systemdの `Environment=PYTHONPATH=...` を確認 |
| `ModuleNotFoundError: scenedetect` | PySceneDetectが未インストール | `pip3 install scenedetect[opencv]` |
| `RuntimeError: DATABASE_URL is not set` | .envが読み込まれていない | `EnvironmentFile=/opt/aitherhub/.env` を確認 |
| `startup check FAILED: ffmpeg` | ffmpegが未インストール | `sudo apt install ffmpeg` |
| `startup check FAILED: queue` | Azure Queue接続文字列が不正 | `.env` の `AZURE_STORAGE_CONNECTION_STRING` を確認 |
| Worker起動後すぐ停止 | startup checkが失敗 | `journalctl -u simple-worker -n 50` でログ確認 |
