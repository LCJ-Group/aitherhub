# FaceFusion GPU Worker

AitherHub Mode B（リアル顔ライブ配信）用の GPU ワーカーサーバーです。FaceFusion によるリアルタイム顔交換処理を担当し、AitherHub バックエンドから HTTP API で制御されます。

## アーキテクチャ

```
┌──────────────┐    RTMP     ┌──────────────────────┐    RTMP     ┌────────────────┐
│  Body Double  │ ──────────▶│  GPU Worker           │ ──────────▶│  配信           │
│  (カメラ +    │            │  (FaceFusion          │            │  プラットフォーム │
│   商品紹介)   │            │   顔交換 + 補正)      │            │  (視聴者)       │
└──────────────┘            └──────────────────────┘            └────────────────┘
                                     ▲
                              ┌──────┴──────┐
                              │ AitherHub   │
                              │ Backend     │
                              │ (HTTP API)  │
                              └─────────────┘
```

## クイックスタート（RunPod）

### 1. RunPod で GPU Pod を作成

[RunPod](https://www.runpod.io/) にログインし、以下の設定で Pod を作成します。

| 設定 | 値 |
|------|-----|
| GPU | RTX 4090（24GB） |
| Cloud Type | Community Cloud（$0.34/hr） |
| Template | RunPod Pytorch 2.1 |
| Disk | 50GB |
| Volume | 30GB（モデル保存用） |

### 2. セットアップスクリプトを実行

Pod の Web Terminal または SSH で以下を実行します。

```bash
# リポジトリをクローン
cd /workspace
git clone https://github.com/LCJ-Group/aitherhub.git
cd aitherhub/gpu-worker

# API キーを設定
export WORKER_API_KEY="your-secret-key"

# セットアップ＆起動
bash runpod-setup.sh
```

### 3. 動作確認

```bash
# ヘルスチェック
curl -H "X-Api-Key: your-secret-key" http://localhost:8000/api/health

# ソース顔を設定（インフルエンサーの顔写真）
curl -X POST -H "X-Api-Key: your-secret-key" \
  -F "file=@influencer_face.jpg" \
  http://localhost:8000/api/set-source

# 単一フレームテスト
curl -X POST -H "X-Api-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"image_base64": "<base64-encoded-image>", "quality": "high"}' \
  http://localhost:8000/api/swap-frame
```

## Docker で起動

```bash
cd gpu-worker

# .env を作成
cp .env.example .env
# WORKER_API_KEY を編集

# ビルド＆起動
docker compose up -d

# ログ確認
docker compose logs -f
```

## API エンドポイント

すべてのリクエストに `X-Api-Key` ヘッダーが必要です。

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/health` | GPU・FaceFusion の状態確認 |
| POST | `/api/set-source` | ソース顔画像の設定（ファイル/Base64/URL） |
| POST | `/api/start-stream` | リアルタイム顔交換ストリーム開始 |
| POST | `/api/stop-stream` | ストリーム停止 |
| GET | `/api/stream-status` | ストリーム状態・メトリクス |
| POST | `/api/swap-frame` | 単一フレームの顔交換テスト |
| GET | `/api/config` | 現在の FaceFusion 設定を取得 |
| POST | `/api/config` | FaceFusion 設定を変更 |

## 品質プリセット

| プリセット | face_swapper | face_enhancer | 推定 FPS (4090) | VRAM |
|-----------|-------------|---------------|----------------|------|
| `fast` | inswapper_128 | なし | 25-30 | ~4GB |
| `balanced` | inswapper_128 | gfpgan_1.4 | 18-25 | ~8GB |
| `high` | inswapper_128 | gfpgan_1.4 | 15-20 | ~12GB |

## AitherHub バックエンドとの接続

AitherHub バックエンドの `.env` に以下を追加します。

```bash
# RunPod の公開 URL（Pod の設定画面で確認）
FACE_SWAP_WORKER_URL=https://your-pod-id-8000.proxy.runpod.net
FACE_SWAP_WORKER_API_KEY=your-secret-key
```

## コスト見積もり

| 使い方 | 月間時間 | RunPod Community | RunPod Secure |
|--------|---------|-----------------|---------------|
| 週1回2時間 | 8時間 | **$2.72**（約410円） | **$4.72**（約710円） |
| 週3回2時間 | 24時間 | **$8.16**（約1,230円） | **$14.16**（約2,120円） |
| 毎日2時間 | 60時間 | **$20.40**（約3,060円） | **$35.40**（約5,310円） |
| 毎日4時間 | 120時間 | **$40.80**（約6,120円） | **$70.80**（約10,620円） |

ストレージ（30GB Volume）: 約 $1.50/月 追加

## トラブルシューティング

### GPU メモリ不足

`high` 品質で VRAM 不足が発生する場合は、`balanced` または `fast` に切り替えてください。

```bash
curl -X POST -H "X-Api-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"face_enhancer_enabled": false}' \
  http://localhost:8000/api/config
```

### FaceFusion モデルのダウンロードが遅い

初回起動時にモデル（約5GB）をダウンロードします。RunPod の Volume にモデルを保存すれば、Pod を再起動しても再ダウンロード不要です。

### RTMP ストリームが接続できない

OBS Studio の配信設定で、RTMP URL が正しいことを確認してください。GPU ワーカーの `input_rtmp` には、OBS が配信する RTMP サーバーの URL を指定します。
