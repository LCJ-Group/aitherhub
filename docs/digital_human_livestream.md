# 數智人直播 API 対接モジュール (PoC)

## 概要

本モジュールは AitherHub と騰訊雲智能數智人（Tencent Cloud IVH）直播 API を対接し、AitherHub の動画分析結果から AI デジタルヒューマンのライブ配信台本を自動生成する PoC 実装です。

AitherHub が蓄積するライブコマース分析データ（フェーズ分析、売上指標、音声書き起こし、インサイト）を活用し、高パフォーマンスのフェーズを優先的に台本に反映することで、データドリブンなライブ配信を実現します。

**ハイブリッドアーキテクチャ**: ElevenLabs の声音クローン技術と騰訊雲數智人を組み合わせることで、日本語を含む32以上の言語で自分の声によるデジタルヒューマン直播を実現します。

**Mode B（リアル顔ライブ配信）**: FaceFusion GPU ワーカーによるリアルタイム顔交換を追加。ボディダブルの顔をインフルエンサーの顔にリアルタイムで置換し、ElevenLabs 音声クローンと組み合わせて完全なクローンライブ配信を実現します。詳細は [Face Swap GPU Worker ガイド](./face_swap_gpu_worker.md) を参照してください。

## アーキテクチャ

```
┌──────────────────────────────────────────────────────────────────┐
│                      AitherHub Backend                            │
│                                                                    │
│  ┌──────────────┐    ┌───────────────────┐                        │
│  │ Digital Human │    │  Script Generator │                        │
│  │  Endpoints    │───▶│    Service        │                        │
│  │  (FastAPI)    │    │  (LLM-powered)    │                        │
│  └──────┬───────┘    └────────┬──────────┘                        │
│         │                     │                                    │
│         │              ┌──────▼──────────┐                        │
│         │              │  AitherHub DB    │                        │
│         │              │  (phases, GMV,   │                        │
│         │              │   insights, STT) │                        │
│         │              └─────────────────┘                        │
│         │                                                          │
│  ┌──────▼──────────────────────────────────────┐                  │
│  │        Hybrid Livestream Service              │                  │
│  │  (Orchestrates ElevenLabs + Tencent Cloud)    │                  │
│  └──────┬─────────────────────┬────────────────┘                  │
│         │                     │                                    │
│  ┌──────▼──────────┐  ┌──────▼──────────────────┐                │
│  │ ElevenLabs TTS  │  │ Tencent Digital Human   │                │
│  │ Service          │  │ Service (API Client)    │                │
│  │ - Voice Cloning  │  │ - HMAC-SHA256 signing   │                │
│  │ - Japanese TTS   │  │ - HTTP client (httpx)   │                │
│  │ - PCM 16kHz out  │  │ - Liveroom management   │                │
│  └──────┬──────────┘  └──────┬──────────────────┘                │
└─────────┼─────────────────────┼────────────────────────────────────┘
          │                     │
          ▼                     ▼
┌─────────────────────┐ ┌─────────────────────────┐
│  ElevenLabs API     │ │  Tencent Cloud IVH API  │
│  api.elevenlabs.io  │ │  gw.tvs.qq.com          │
│  - TTS (32+ langs)  │ │  - Open Liveroom        │
│  - Voice Cloning    │ │  - Get Liveroom         │
│  - Streaming TTS    │ │  - List Liverooms       │
└─────────────────────┘ │  - Takeover             │
                        │  - Close Liveroom       │
                        └─────────────────────────┘
```

### ハイブリッドフロー

```
テキスト入力（台本/コメント）
    │
    ▼
┌─────────────────────────┐
│  ElevenLabs TTS API     │  ← 自分のクローン声で音声生成
│  (声音クローン, 日本語)   │     PCM 16kHz 16bit mono
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  腾讯云数智人            │  ← 音声に合わせて口型同期
│  (Audio Driver /        │     リアルタイム直播出力
│   Liveroom API)         │
└─────────────────────────┘
```

## ファイル構成

| ファイル | 説明 |
|---------|------|
| `backend/app/services/tencent_digital_human_service.py` | 騰訊雲 IVH API クライアント（署名生成、全5エンドポイント対応） |
| `backend/app/services/elevenlabs_tts_service.py` | ElevenLabs TTS クライアント（声音クローン、日本語対応、PCM出力） |
| `backend/app/services/hybrid_livestream_service.py` | ハイブリッド編成サービス（ElevenLabs + 騰訊雲の連携） |
| `backend/app/services/script_generator_service.py` | 台本自動生成サービス（分析データ取得、フェーズスコアリング、LLM台本生成） |
| `backend/app/api/v1/endpoints/digital_human.py` | FastAPI エンドポイント（16個のAPI: Mode A 10個 + Mode B 7個） |
| `backend/app/schemas/digital_human_schema.py` | Pydantic リクエスト/レスポンススキーマ（Mode A + Mode B） |
| `backend/app/services/face_swap_service.py` | FaceFusion GPU ワーカー制御クライアント（Mode B） |
| `tests/test_digital_human.py` | Mode A ユニットテスト（33テスト） |
| `tests/test_face_swap.py` | Mode B ユニットテスト（24テスト） |
| `docs/face_swap_gpu_worker.md` | GPU ワーカーセットアップガイド |

## API エンドポイント

すべてのエンドポイントは `X-Admin-Key: aither:hub` ヘッダーが必要です。

### 1. 直播間作成

分析データから台本を自動生成し、直播間を作成します。ハイブリッドモード対応。

```
POST /api/v1/digital-human/liveroom/create
```

**リクエスト例（分析データから自動生成 + ハイブリッド声音）:**

```json
{
  "video_id": "abc-123-def",
  "cycle_times": 5,
  "protocol": "rtmp",
  "product_focus": "KYOGOKU シグネチャーシャンプー",
  "tone": "professional_friendly",
  "language": "ja",
  "use_hybrid_voice": true,
  "elevenlabs_voice_id": "your-cloned-voice-id"
}
```

**リクエスト例（手動台本）:**

```json
{
  "scripts": ["皆さん、こんにちは！今日は特別な商品をご紹介します..."],
  "cycle_times": 3,
  "protocol": "rtmp"
}
```

### 2. 直播間ステータス照会

```
GET /api/v1/digital-human/liveroom/{liveroom_id}
```

| Status | Label | 説明 |
|--------|-------|------|
| 0 | INITIAL | 初期化中 |
| 1 | STREAM_CREATING | ストリーム作成中 |
| 2 | STREAM_READY | ストリーム準備完了（配信開始可能） |
| 3 | SCRIPT_SPLIT_DONE | 台本分割完了 |
| 4 | SCHEDULING | スケジューリング中 |
| 5 | SCHEDULE_DONE | スケジューリング完了 |
| 6 | CLOSED | 閉鎖済み |

### 3. 直播間一覧

```
GET /api/v1/digital-human/liverooms?page_size=20&page_index=1
```

v5.x.x プロトコルでは `PageIndex`（1始まり）と `PageSize`（1-1000）を使用します。

### 4. 即時挿播（Takeover）

直播中にリアルタイムでテキストを挿入し、數智人に即座に読み上げさせます。ハイブリッドモード対応。

```
POST /api/v1/digital-human/liveroom/{liveroom_id}/takeover
```

**リクエスト例（ハイブリッド声音）:**

```json
{
  "content": "皆さん、今だけ特別価格です！残り10個！",
  "use_hybrid_voice": true,
  "elevenlabs_voice_id": "your-cloned-voice-id",
  "language": "ja"
}
```

**リクエスト例（AI自動生成）:**

```json
{
  "event_context": "商品Aが直近5分で50個売れました",
  "event_type": "engagement_spike",
  "language": "ja"
}
```

### 5. 直播間閉鎖

```
POST /api/v1/digital-human/liveroom/{liveroom_id}/close
```

### 6. 台本プレビュー生成

```
POST /api/v1/digital-human/script/generate
```

### 7. ElevenLabs 音声生成

テキストからクローン声音で音声を生成します。

```
POST /api/v1/digital-human/voice/generate-audio
```

```json
{
  "texts": ["こんにちは、皆さん！", "今日は素晴らしい商品をご紹介します。"],
  "language": "ja",
  "voice_id": "your-cloned-voice-id"
}
```

### 8. ElevenLabs 声音一覧

```
GET /api/v1/digital-human/voice/list
```

### 9. ハイブリッドヘルスチェック

```
GET /api/v1/digital-human/hybrid/health
```

### 10. ヘルスチェック

```
GET /api/v1/digital-human/health
```

## 台本自動生成ロジック

**Step 1: データ取得** — AitherHub DB から対象動画の分析データ（phases, insights, speech_segments, reports）を取得します。

**Step 2: フェーズスコアリング** — 各フェーズを以下の重み付けでスコアリングし、高パフォーマンスのフェーズを優先します。

| 指標 | 重み | 説明 |
|------|------|------|
| GMV | 40% | 売上金額 |
| Delta View | 25% | 視聴者増加数 |
| Delta Like | 15% | いいね増加数 |
| CTA Score | 20% | CTA効果スコア |

**Step 3: LLM台本生成** — GPT-4.1-mini を使用して、トップパフォーマンスフェーズの内容、元の配信者の話し方、分析インサイトを参考に、數智人向けの台本を生成します。

**Step 4: フォールバック** — LLM が利用できない場合、フェーズ説明と音声テキストから簡易台本を構築します。

## ハイブリッド声音アーキテクチャ

騰訊雲の声音复刻は日本語をサポートしていないため、ElevenLabs の声音クローン技術と組み合わせたハイブリッドアーキテクチャを採用しています。

### 動作モード

**Mode A: Liveroom + テキスト駆動（現在のメイン）**

台本テキストを騰訊雲 Liveroom API に送信し、騰訊雲の TTS で音声を生成します。ElevenLabs で事前に音声を生成し、将来の Interactive Session モードに備えます。

**Mode B: リアル顔ライブ配信（FaceFusion 顔交換）** ✅ 実装済み

ボディダブル（替え玉）がカメラの前で商品を紹介し、FaceFusion GPU ワーカーがリアルタイムでインフルエンサーの顔に交換します。ElevenLabs 音声クローンと組み合わせて、顔も声も完全にクローンされたライブ配信を実現します。

```
Body Double (camera) → GPU Worker (face swap) → Platform (viewers)
Script Text → ElevenLabs TTS (voice clone) → Audio output
```

**Mode C: Interactive Session + 音声駆動（将来拡張）**

ElevenLabs で生成した PCM 音声を WebSocket 経由で騰訊雲の Audio Driver に送信し、リアルタイムで口型同期を行います。

### ElevenLabs 声音クローン

ElevenLabs は32以上の言語（日本語含む）で声音クローンをサポートしています。

- **必要な素材**: 1分の音声サンプル
- **対応言語**: 日本語、中国語、英語、韓国語など32+言語
- **出力形式**: PCM 16kHz 16bit mono（騰訊雲互換）
- **料金**: 月額5ドルから

### 音声チャンキング

ElevenLabs の出力を騰訊雲 WebSocket Audio Driver に送信するため、以下のパラメータでチャンキングします。

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| チャンクサイズ | 5120 bytes | 160ms分の PCM 16kHz 16bit |
| 初期バースト | 6チャンク | 最大速度で送信 |
| 後続間隔 | 120ms | チャンク間の送信間隔 |
| 終了パケット | IsFinal=True | 空のAudioで音声終了を通知 |

## 環境変数

`.env` に以下の変数を設定してください。

```
# 騰訊雲 IVH (v5.x.x)
TENCENT_IVH_BASE_URL=https://gw.tvs.qq.com
TENCENT_IVH_APPKEY=your-appkey
TENCENT_IVH_ACCESS_TOKEN=your-access-token
TENCENT_IVH_PROJECT_ID=your-virtualman-project-id
TENCENT_IVH_PROTOCOL=rtmp

# ElevenLabs (声音クローン)
ELEVENLABS_API_KEY=your-elevenlabs-api-key
ELEVENLABS_VOICE_ID=your-cloned-voice-id
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
ELEVENLABS_BASE_URL=https://api.elevenlabs.io

# ハイブリッド設定
HYBRID_DEFAULT_LANGUAGE=ja
```

### 必要なリソース

| リソース | プラットフォーム | 必須 | 説明 |
|---------|---------------|------|------|
| 形象定制/租赁 | 騰訊雲 | ✅ | 数智人の外見 |
| 会话互动并发数 | 騰訊雲 | ✅ | 直播に必要な並行配額 |
| 声音复刻 | 騰訊雲 | 任意 | 中国語/英語のカスタムボイス |
| Voice Clone | ElevenLabs | ✅ | 日本語対応の声音クローン |
| API Key | ElevenLabs | ✅ | TTS API アクセス |

### 腾讯云购买指南

购买页面: https://buy.cloud.tencent.com/ivh

| 项目 | 最低价格 | 说明 |
|------|---------|------|
| 形象定制（2D小样本通用口型）| 2,500元/个 | 需要1分钟视频素材 |
| 会话互动并发数（日包） | 140元/天/路 | 需手动激活 |
| 会话互动并发数（月包） | 3,500元/月/路 | 自动激活 |

## テスト

```bash
cd /path/to/aitherhub
PYTHONPATH=backend python3 -m pytest tests/test_digital_human.py -v
# 33 tests passed
```

## 今後の拡張計画

1. **GPU ワーカー自動スケーリング**: 配信スケジュールに基づいて Vast.ai/RunPod の GPU インスタンスを自動起動・停止
2. **Interactive Session モード**: ElevenLabs TTS + 騰訊雲 Audio Driver の WebSocket 連携を実装し、完全なクローン声音での直播を実現
2. **リアルタイム分析連携**: AitherHub のライブ分析パイプラインと連携し、配信中のエンゲージメントデータに基づいて自動的に Takeover を発動
3. **台本テンプレート管理**: 商品カテゴリ別の台本テンプレートを DB で管理
4. **A/B テスト**: 異なる台本バリエーションの配信パフォーマンスを比較
5. **コールバック処理**: 騰訊雲からのコールバック通知を受信し、直播間のステータス変更を自動処理
6. **フロントエンド UI**: 管理ダッシュボードに數智人直播管理画面を追加
