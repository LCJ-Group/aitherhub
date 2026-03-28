---
name: aitherhub-deploy
description: Aitherhub project deployment guide. Use when deploying changes to Aitherhub frontend, backend, or worker. Covers GitHub Actions workflows, Azure App Service, Azure Static Web Apps deployment, and RunPod Serverless GPU Worker operations (MuseTalk lipsync, FaceFusion face-swap).
---

# Aitherhub Deploy

## Repository

- Repo: `LCJ-Group/aitherhub`
- Clone: `gh repo clone LCJ-Group/aitherhub`
- Local path: `/home/ubuntu/aitherhub-repo`
- Branch: `master` (single branch, push to deploy)

## Architecture

| Component | Technology | Hosting | Deploy Trigger |
|---|---|---|---|
| Frontend | Vite + React | Azure Static Web Apps | Push to `master` |
| Backend (main) | FastAPI | Azure App Service (`aitherhubAPI`) | Push to `master` |
| GPU Worker | RunPod Serverless (FastAPI) | RunPod Serverless (L4/A5000) | Push to `master` (`gpu-worker/serverless/`) |

## Deployment Workflows (GitHub Actions)

All workflows trigger on push to `master` branch automatically.

### 1. Frontend (`deploy-swa-frontend.yml`)
- Builds: `cd frontend && npm install && npm run build`
- Deploys to: Azure Static Web Apps
- Typical time: ~2 minutes

### 2. Backend (`master_aitherhubapi.yml`)
- Builds: Python app with `requirements.txt`
- Deploys to: Azure App Service `aitherhubAPI`
- Typical time: ~5-10 minutes
- **Note**: After deploy completes, Azure App Service may take 1-2 extra minutes to restart.

### 3. GPU Worker (`build-gpu-worker.yml`)
- Builds: Multi-stage Dockerfile (`gpu-worker/serverless/Dockerfile`)
- Deploys to: GitHub Container Registry (`ghcr.io/lcj-group/aitherhub-gpu-worker:latest`)
- Trigger: Push to `gpu-worker/serverless/` path in `master` branch.
- Typical time: ~20-30 minutes

## GPU Worker (RunPod Serverless)

### Overview

The GPU Worker has been migrated from a persistent RunPod Pod to a **RunPod Serverless** endpoint. This provides auto-scaling, eliminates the need for manual deployments, and ensures high availability without managing a specific Pod.

The core of this architecture is a self-contained Docker image that includes all necessary code (FaceFusion, MuseTalk), dependencies, and essential models. This removes the dependency on a persistent `/workspace/` volume.

### Endpoint Details

| Setting | Value |
|---|---|
| Endpoint ID | `2noptqoq7n8f8g` |
| Docker Image | `ghcr.io/lcj-group/aitherhub-gpu-worker:latest` |
| GPUs | L4, RTX A5000, etc. (auto-scales) |
| Cold Start Time | ~1-2 minutes |

### Deployment Process

Deployment is now **fully automated**:

1.  **Push Changes**: Any changes pushed to the `gpu-worker/serverless/` directory in the `master` branch will automatically trigger the `build-gpu-worker.yml` GitHub Actions workflow.
2.  **Build Docker Image**: The workflow builds a new multi-stage Docker image, including all code and dependencies. This takes approximately 20-30 minutes.
3.  **Push to GHCR**: The newly built image is pushed to the GitHub Container Registry.
4.  **RunPod Auto-Update**: The RunPod Serverless endpoint is configured to watch the `latest` tag of the Docker image. When a new image is pushed, RunPod automatically initiates a rolling update, replacing old workers with new ones without downtime.

There are **no manual steps** required to deploy the GPU worker anymore.

### Backend ↔ GPU Worker Connection

- The backend (`aitherhubAPI`) now uses the `RunPodServerlessService` to send jobs to the Serverless endpoint.
- The service uses the RunPod API to submit jobs (`/run` or `/runsync`) and check their status (`/status`).
- **Configuration**: The `RUNPOD_API_KEY` and `RUNPOD_ENDPOINT_ID` are configured as fallback values directly in `runpod_serverless_service.py`. This was done to avoid complexities with Azure App Service environment variable management.

### Health & Verification

- **Backend Health**: The backend's health can be checked to verify its connection to the Serverless endpoint.
    - URL: `https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1/digital-human/musetalk/health`
    - Header: `X-Admin-Key: aither:hub`
    - A `cold_start` status is normal if the worker is idle.
- **Direct Endpoint Health**: The Serverless endpoint itself can be checked directly.
    - URL: `https://api.runpod.ai/v2/2noptqoq7n8f8g/runsync`
    - Method: `POST`
    - Body: `{"input": {"action": "health"}}`
    - Authorization: `Bearer <RUNPOD_API_KEY>`

## API Base URLs

- Main API: `https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net`
- Frontend: `https://www.aitherhub.com`
- GPU Worker API: `https://api.runpod.ai/v2/2noptqoq7n8f8g`
