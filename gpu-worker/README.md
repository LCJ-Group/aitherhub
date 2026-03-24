# AitherHub GPU Worker

AitherHub のGPUワーカーサーバーです。2つのモードをサポートしています。

- **Mode A（デジタルヒューマン）**: MuseTalk v1.5 によるリップシンク動画生成
- **Mode B（リアル顔ライブ配信）**: FaceFusion によるリアルタイム顔交換

## アーキテクチャ

```
Mode A: デジタルヒューマン（リップシンク）
┌──────────────┐   テキスト   ┌──────────────┐   音声+顔   ┌──────────────────┐
│  AitherHub   │ ──────────▶│  Backend     │ ──────────▶│  GPU Worker       │
│  Frontend    │            │  (TTS生成)   │            │  (MuseTalk v1.5   │
│              │◀──────────│              │◀──────────│   リップシンク)    │
└──────────────┘   動画URL   └──────────────┘   動画     └──────────────────┘

Mode B: リアル顔ライブ配信（顔交換）
┌──────────────┐    RTMP     ┌──────────────────────┐    RTMP     ┌────────────────┐
│  Body Double  │ ──────────▶│  GPU Worker           │ ──────────▶│  配信           │
│  (カメラ +    │            │  (FaceFusion          │            │  プラットフォーム │
│   商品紹介)   │            │   顔交換 + 補正)      │            │  (視聴者)       │
└──────────────┘            └──────────────────────┘            └────────────────┘
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
git clone https://github.com/proteanstudios/aitherhub-repo.git
cd aitherhub-repo/gpu-worker

# API キーを設定
export WORKER_API_KEY="your-secret-key"

# セットアップ＆起動（FaceFusion + MuseTalk 両方をインストール）
bash runpod-setup.sh
```

セットアップスクリプトが自動的に以下を行います：
- システム依存関係のインストール（ffmpeg等）
- FaceFusion のインストールとモデルダウンロード
- MuseTalk v1.5 のインストールと依存関係
- MuseTalk のランタイムパッチ適用（diffusers互換性、FaceParsing パス修正）
- ワーカーAPIサーバーの起動

### 3. 動作確認

```bash
# ヘルスチェック
curl -H "X-Api-Key: your-secret-key" http://localhost:8000/api/health

# MuseTalk ヘルスチェック
curl -H "X-Api-Key: your-secret-key" http://localhost:8000/api/digital-human/health
```

## API エンドポイント

すべてのリクエストに `X-Api-Key` ヘッダーが必要です。

### Mode A: デジタルヒューマン（MuseTalk）

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/digital-human/health` | MuseTalk の状態確認 |
| POST | `/api/digital-human/generate` | リップシンク動画生成ジョブの開始 |
| GET | `/api/digital-human/status/{job_id}` | ジョブの進行状況確認 |
| GET | `/api/digital-human/download/{job_id}` | 生成された動画のダウンロード |

### Mode B: 顔交換（FaceFusion）

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/health` | GPU・FaceFusion の状態確認 |
| POST | `/api/set-source` | ソース顔画像の設定 |
| POST | `/api/start-stream` | リアルタイム顔交換ストリーム開始 |
| POST | `/api/stop-stream` | ストリーム停止 |
| GET | `/api/stream-status` | ストリーム状態・メトリクス |
| POST | `/api/swap-frame` | 単一フレームの顔交換テスト |

## MuseTalk ランタイムパッチ

`runpod-setup.sh` が自動的に以下のパッチを適用します：

1. **vae.py**: `AutoencoderKL.from_pretrained()` に `low_cpu_mem_usage=False` を追加（diffusers >= 0.28 の meta tensor 問題を回避）
2. **FaceParsing __init__.py**: モデルパスを相対パスから絶対パスに変更（cwd 依存の問題を回避）

## AitherHub バックエンドとの接続

AitherHub バックエンドは RunPod Discovery Service を使って GPU Worker を自動検出します。手動設定が必要な場合は `.env` に以下を追加します。

```bash
# RunPod の公開 URL（Pod の設定画面で確認）
FACE_SWAP_WORKER_URL=https://your-pod-id-8000.proxy.runpod.net
FACE_SWAP_WORKER_API_KEY=your-secret-key
```

## トラブルシューティング

### MuseTalk モデルのロードに失敗する

```
Failed to load MuseTalk models: ...meta tensor...
```

`runpod-setup.sh` が自動的にパッチを適用しますが、手動で修正する場合：

```bash
# vae.py のパッチ
sed -i 's/AutoencoderKL.from_pretrained(model_path)/AutoencoderKL.from_pretrained(model_path, low_cpu_mem_usage=False)/g' \
    /workspace/MuseTalk/musetalk/utils/vae.py
```

### FaceParsing の resnet18 が見つからない

```
FileNotFoundError: ./models/face-parse-bisent/resnet18-5c106cde.pth
```

FaceParsing が相対パスを使用しているため、cwd が MuseTalk ディレクトリでない場合に発生します。`runpod-setup.sh` が自動的に絶対パスに修正します。

### GPU メモリ不足

MuseTalk + FaceFusion の両方をロードすると VRAM を大量に消費します。RTX 4090（24GB）を推奨します。

## コスト見積もり

| 使い方 | 月間時間 | RunPod Community | RunPod Secure |
|--------|---------|-----------------|---------------|
| 週1回2時間 | 8時間 | **$2.72**（約410円） | **$4.72**（約710円） |
| 週3回2時間 | 24時間 | **$8.16**（約1,230円） | **$14.16**（約2,120円） |
| 毎日2時間 | 60時間 | **$20.40**（約3,060円） | **$35.40**（約5,310円） |
| 毎日4時間 | 120時間 | **$40.80**（約6,120円） | **$70.80**（約10,620円） |

ストレージ（30GB Volume）: 約 $1.50/月 追加
