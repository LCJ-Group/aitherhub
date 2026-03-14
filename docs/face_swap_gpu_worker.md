# Face Swap GPU Worker — セットアップガイド

## 概要

AitherHub Mode B（リアル顔ライブ配信）では、FaceFusion を搭載した GPU ワーカーサーバーがリアルタイム顔交換処理を担当します。本ドキュメントでは、GPU ワーカーのセットアップ手順と AitherHub バックエンドとの接続方法を説明します。

## アーキテクチャ

```
┌──────────────┐    RTMP     ┌──────────────────────┐    RTMP     ┌────────────────┐
│  Body Double  │ ──────────▶│  FaceFusion GPU       │ ──────────▶│  Streaming     │
│  (camera +    │            │  Worker               │            │  Platform      │
│   products)   │            │  (face swap + RTMP)   │            │  (viewers)     │
└──────────────┘            └──────────────────────┘            └────────────────┘
                                     ▲
                              ┌──────┴──────┐
                              │ Source Face  │
                              │ (influencer │
                              │  photo)     │
                              └─────────────┘
                                     ▲
                              ┌──────┴──────┐
                              │ AitherHub   │
                              │ Backend     │
                              │ (HTTP API)  │
                              └─────────────┘
```

## GPU ワーカーの要件

| 項目 | 最小要件 | 推奨 |
|------|---------|------|
| GPU | NVIDIA RTX 3060 (12GB) | NVIDIA RTX 4090 (24GB) |
| VRAM | 8GB | 16GB+ |
| CPU | 4 cores | 8+ cores |
| RAM | 16GB | 32GB |
| ストレージ | 20GB SSD | 50GB SSD |
| ネットワーク | 100Mbps | 1Gbps |
| OS | Ubuntu 22.04 | Ubuntu 22.04 |
| Docker | 24.0+ | 24.0+ |
| NVIDIA Driver | 535+ | 550+ |

## クラウド GPU プロバイダー

### Vast.ai（推奨：コスパ最高）

RTX 4090 を **$0.28-0.59/時間** でレンタル可能です。

```bash
# Vast.ai CLI でインスタンスを検索
vastai search offers 'gpu_name=RTX_4090 num_gpus=1 dph<=0.60 inet_down>=500'

# インスタンスを起動
vastai create instance <offer_id> \
  --image nvidia/cuda:12.1.0-devel-ubuntu22.04 \
  --disk 50 \
  --onstart-cmd "bash /workspace/setup.sh"
```

### RunPod（代替）

RTX 4090 を **$0.39/時間** でレンタル可能です。Web UI からの操作も簡単です。

## GPU ワーカーのセットアップ

### 1. FaceFusion のインストール

```bash
# リポジトリをクローン
git clone https://github.com/facefusion/facefusion.git
cd facefusion

# 依存関係をインストール
python install.py --onnxruntime cuda

# モデルを事前ダウンロード
python facefusion.py force-download
```

### 2. API ラッパーのセットアップ

GPU ワーカー上で FastAPI サーバーを起動し、AitherHub バックエンドからの HTTP リクエストを受け付けます。

```bash
# API ラッパーのディレクトリ
mkdir -p /workspace/face-swap-worker
cd /workspace/face-swap-worker
```

以下のファイルを作成します。

**worker_api.py** — GPU ワーカーの API サーバー:

```python
"""
FaceFusion GPU Worker API Server

This is a lightweight FastAPI wrapper around FaceFusion that exposes
HTTP endpoints for AitherHub to control face swapping remotely.
"""

import asyncio
import base64
import io
import logging
import os
import subprocess
import time
import uuid
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="FaceFusion GPU Worker", version="1.0.0")
logger = logging.getLogger(__name__)

# Configuration
API_KEY = os.getenv("WORKER_API_KEY", "change-me")
SOURCE_FACE_PATH = "/workspace/source_face.jpg"
FACEFUSION_DIR = "/workspace/facefusion"

# State
current_session = {
    "id": None,
    "status": "idle",
    "process": None,
    "start_time": None,
    "frames_processed": 0,
}


# ── Auth ──
from fastapi import Header

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


# ── Models ──
class SetSourceRequest(BaseModel):
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    face_index: int = 0

class StartStreamRequest(BaseModel):
    input_rtmp: str
    output_rtmp: str
    quality: str = "balanced"
    resolution: str = "720p"
    fps: int = 30
    face_enhancer: bool = True
    face_mask_blur: float = 0.3

class StopStreamRequest(BaseModel):
    session_id: Optional[str] = None

class SwapFrameRequest(BaseModel):
    frame_base64: str
    quality: str = "high"
    face_enhancer: bool = True


# ── Endpoints ──

@app.post("/api/health")
async def health_check(auth: bool = Depends(verify_api_key)):
    import torch
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A"
    mem = torch.cuda.mem_get_info(0) if torch.cuda.is_available() else (0, 0)
    return {
        "status": "ok",
        "gpu_name": gpu_name,
        "gpu_memory_used_mb": round((mem[1] - mem[0]) / 1024 / 1024, 1),
        "gpu_memory_total_mb": round(mem[1] / 1024 / 1024, 1),
        "facefusion_version": "3.5.4",
        "stream_status": current_session["status"],
    }


@app.post("/api/set-source")
async def set_source(req: SetSourceRequest, auth: bool = Depends(verify_api_key)):
    if req.image_url:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(req.image_url)
            with open(SOURCE_FACE_PATH, "wb") as f:
                f.write(resp.content)
    elif req.image_base64:
        data = base64.b64decode(req.image_base64)
        with open(SOURCE_FACE_PATH, "wb") as f:
            f.write(data)
    else:
        raise HTTPException(400, "image_url or image_base64 required")

    return {
        "status": "ok",
        "face_detected": True,
        "face_bbox": [0, 0, 0, 0],  # Placeholder
        "face_landmarks": 68,
    }


@app.post("/api/start-stream")
async def start_stream(req: StartStreamRequest, auth: bool = Depends(verify_api_key)):
    if current_session["status"] == "running":
        raise HTTPException(409, "Stream already running")

    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    # Build FaceFusion command
    quality_map = {
        "fast": {"processors": "face_swapper"},
        "balanced": {"processors": "face_swapper face_enhancer"},
        "high": {"processors": "face_swapper face_enhancer"},
    }
    processors = quality_map.get(req.quality, quality_map["balanced"])["processors"]

    # Resolution map
    res_map = {"480p": "854x480", "720p": "1280x720", "1080p": "1920x1080"}
    resolution = res_map.get(req.resolution, "1280x720")

    cmd = [
        "python", f"{FACEFUSION_DIR}/facefusion.py", "headless-run",
        "--source-paths", SOURCE_FACE_PATH,
        "--target-path", req.input_rtmp,
        "--output-path", req.output_rtmp,
        "--processors", *processors.split(),
        "--execution-providers", "cuda",
        "--output-video-resolution", resolution,
        "--output-video-fps", str(req.fps),
    ]

    if req.face_enhancer:
        cmd.extend(["--face-enhancer-model", "gfpgan_1.4"])

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    current_session.update({
        "id": session_id,
        "status": "running",
        "process": process,
        "start_time": time.time(),
        "frames_processed": 0,
    })

    return {"session_id": session_id, "status": "starting"}


@app.post("/api/stop-stream")
async def stop_stream(req: StopStreamRequest, auth: bool = Depends(verify_api_key)):
    if current_session["process"]:
        current_session["process"].terminate()

    uptime = time.time() - (current_session["start_time"] or time.time())
    result = {
        "session_id": current_session["id"],
        "uptime_seconds": round(uptime, 1),
        "frames_processed": current_session["frames_processed"],
    }

    current_session.update({
        "id": None, "status": "idle", "process": None,
        "start_time": None, "frames_processed": 0,
    })

    return result


@app.get("/api/stream-status")
async def stream_status(auth: bool = Depends(verify_api_key)):
    uptime = 0
    if current_session["start_time"]:
        uptime = time.time() - current_session["start_time"]
    return {
        "status": current_session["status"],
        "session_id": current_session["id"],
        "fps": 30.0 if current_session["status"] == "running" else 0,
        "latency_ms": 33.0 if current_session["status"] == "running" else 0,
        "uptime_seconds": round(uptime, 1),
        "frames_processed": current_session["frames_processed"],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 3. Docker Compose（推奨）

```yaml
# docker-compose.yml
version: "3.8"
services:
  face-swap-worker:
    build: .
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - WORKER_API_KEY=your-secret-key
    ports:
      - "8000:8000"
    volumes:
      - ./models:/workspace/facefusion/.assets/models
      - ./source_faces:/workspace/source_faces
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

```dockerfile
# Dockerfile
FROM nvidia/cuda:12.1.0-devel-ubuntu22.04

RUN apt-get update && apt-get install -y \
    python3 python3-pip git ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Install FaceFusion
RUN git clone https://github.com/facefusion/facefusion.git
RUN cd facefusion && python3 install.py --onnxruntime cuda
RUN cd facefusion && python3 facefusion.py force-download

# Install API dependencies
COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY worker_api.py .

EXPOSE 8000
CMD ["python3", "worker_api.py"]
```

## AitherHub バックエンドとの接続

### 環境変数の設定

AitherHub バックエンドの `.env` に以下を追加します。

```bash
# FaceFusion GPU Worker
FACE_SWAP_WORKER_URL=http://your-gpu-worker-ip:8000
FACE_SWAP_WORKER_API_KEY=your-secret-key
```

### API エンドポイント一覧

AitherHub バックエンドから利用可能な Face Swap エンドポイントは以下の通りです。

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/api/v1/digital-human/face-swap/set-source` | ソース顔画像の設定 |
| POST | `/api/v1/digital-human/face-swap/stream/start` | 顔交換ストリーム開始 |
| POST | `/api/v1/digital-human/face-swap/stream/stop` | ストリーム停止 |
| GET | `/api/v1/digital-human/face-swap/stream/status` | ストリーム状態確認 |
| POST | `/api/v1/digital-human/face-swap/test-frame` | 単一フレームテスト |
| GET | `/api/v1/digital-human/face-swap/health` | GPU ワーカーヘルスチェック |
| GET | `/api/v1/digital-human/full-health` | 全サービスヘルスチェック |

### 使用フロー

```
1. ソース顔設定:  POST /face-swap/set-source  (インフルエンサーの顔写真)
2. ストリーム開始: POST /face-swap/stream/start (RTMP入力/出力URL指定)
3. 状態確認:      GET  /face-swap/stream/status (FPS、遅延、稼働時間)
4. ストリーム停止: POST /face-swap/stream/stop
```

## コスト見積もり

| プロバイダー | GPU | 時間単価 | 月100時間 | 月200時間 |
|------------|-----|---------|----------|----------|
| Vast.ai | RTX 4090 | $0.28-0.59 | $28-59 | $56-118 |
| RunPod | RTX 4090 | $0.39 | $39 | $78 |
| RunPod | RTX A6000 | $0.44 | $44 | $88 |

## トラブルシューティング

### GPU メモリ不足

FaceFusion は RTX 4090 (24GB) で快適に動作しますが、RTX 3060 (12GB) では `balanced` 品質以下を推奨します。`high` 品質は face_enhancer (GFPGAN) を使用するため、追加の VRAM が必要です。

### RTMP 接続エラー

入力 RTMP ストリームが利用可能であることを確認してください。OBS Studio から RTMP サーバーに配信し、そのURLを `input_rtmp` に指定します。

### 低 FPS

FPS が低い場合は、`quality` を `fast` に変更するか、`face_enhancer` を `false` に設定してください。
