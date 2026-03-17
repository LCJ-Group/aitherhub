# Aitherhub Development Skill

> **This file is the canonical reference for the aitherhub skill.**
> The skill file at `/home/ubuntu/skills/aitherhub/SKILL.md` should mirror this content.

---

## Session Start (MANDATORY — do this before ANY work)

### Step 1: Clone & Rebase

```bash
gh repo clone LCJ-Group/aitherhub
cd /home/ubuntu/aitherhub
git pull --rebase origin master
```

### Step 2: Install Safety Hooks (Layer 3)

```bash
bash scripts/install-hooks.sh
```

### Step 3: Clear File Locks (Layer 2)

```bash
curl -sf -X POST -H "X-Admin-Key: aither:hub" -H "Content-Type: application/json" \
  "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1/admin/dev-safety/clear" \
  -d '{}'
```

### Step 4: Load AI-Context (Layer 1)

```bash
curl -sf -H "X-Admin-Key: aither:hub" \
  "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1/admin/ai-context?scope=aitherhub" \
  | python3 -m json.tool
```

Read the response carefully. It contains:
- `dangers` — things you MUST NOT do
- `checklist_by_file` — checks before modifying specific files
- `checklist_by_feature` — checks before modifying specific features
- `dependencies` — file dependency map
- `rules` — what "working correctly" means
- `feature_status` — current state of each feature
- `preferences` — user's priorities and policies
- `lessons` — past mistakes to avoid
- `open_bugs` — unresolved bugs (report to user before starting)
- `error_videos` / `stuck_videos` — problematic videos
- `action_required` — **warnings about missing lessons or urgent issues (address FIRST)**

---

## 4-Layer Defense System

### Layer 1: AI-Context Rules (Social Defense)
- Loaded at session start via `/api/v1/admin/ai-context`
- Contains dangers, checklists, lessons that guide behavior
- **No code changes needed** — already implemented

### Layer 2: File Lock API (Technical Defense)
- Endpoint: `POST /api/v1/admin/dev-safety/lock`
- Before editing a file, acquire a lock:
  ```bash
  curl -sf -X POST -H "X-Admin-Key: aither:hub" -H "Content-Type: application/json" \
    "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1/admin/dev-safety/lock" \
    -d '{"session_id":"manus-session-YYYYMMDD","files":["backend/app/api/v1/endpoints/admin.py"]}'
  ```
- If `denied` array is non-empty, **DO NOT edit those files**
- Locks auto-expire after 2 hours
- Clear all locks at session start (Step 3 above)

### Layer 3: Pre-push Git Hook
- Installed via `scripts/install-hooks.sh`
- Checks before every `git push`:
  1. **Rebase check**: Blocks push if remote has newer commits
  2. **Deletion check**: Warns if >50 lines net deleted in any file
- Overhead: < 0.5 seconds

### Layer 4: GitHub Actions (Post-Detection)
- Workflow: `.github/workflows/safety_check.yml`
- Runs automatically on every push to master
- Checks:
  1. Large deletion detection across all changed files
  2. Protected file monitoring (admin.py, video_sales.py, etc.)
  3. Critical function verification (endpoints, components)
- Results visible in GitHub Actions summary

---

## Before Changing Code

1. **Lock files** you plan to edit (Layer 2)
2. Check `checklist_by_file` for every file you plan to modify
3. Check `checklist_by_feature` for every feature you plan to touch
4. Check `dependencies` to understand impact on other files
5. Check `dangers` to ensure you don't repeat past mistakes

## After Changing Code

1. Verify existing features still work (check `rules` for expected behavior)
2. Test on production: https://www.aitherhub.com
3. **Unlock files** after push is complete

---

## Recording (MANDATORY — do this after EVERY change)

### Bug found → Record it
```bash
curl -sf -X POST -H "X-Admin-Key: aither:hub" -H "Content-Type: application/json" \
  "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1/admin/bug-reports" \
  -d '{"severity":"high","section_name":"<section>","title":"<title>","symptom":"<what happened>","cause":"<why>","resolution":"<how fixed>","affected_files":"<files>","status":"resolved","resolver":"manus-ai"}'
```

### Bug fixed → Create lesson (CRITICAL for knowledge retention)
```bash
curl -sf -X POST -H "X-Admin-Key: aither:hub" -H "Content-Type: application/json" \
  "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1/admin/lessons" \
  -d '{"category":"lesson","title":"<short rule>","content":"<details>","related_files":"<files>","related_feature":"aitherhub"}'
```

### New danger discovered → Record it
```bash
curl -sf -X POST -H "X-Admin-Key: aither:hub" -H "Content-Type: application/json" \
  "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1/admin/lessons" \
  -d '{"category":"danger","title":"<what NOT to do>","content":"<why>","related_files":"<files>","related_feature":"<feature>"}'
```

### New checklist item → Record it
```bash
curl -sf -X POST -H "X-Admin-Key: aither:hub" -H "Content-Type: application/json" \
  "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1/admin/lessons" \
  -d '{"category":"checklist","title":"<what to check>","content":"<details>","related_files":"<files>","related_feature":"<feature>"}'
```

### Work completed → Log it
```bash
curl -sf -X POST -H "X-Admin-Key: aither:hub" -H "Content-Type: application/json" \
  "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net/api/v1/admin/work-logs" \
  -d '{"action":"<deploy|bugfix|feature|refactor>","summary":"<what was done>","details":"<details>","commit_hash":"<hash>","files_changed":"<files>","deploy_target":"aitherhubAPI, frontend","author":"manus-ai"}'
```

---

## Infrastructure

| Item | Value |
|------|-------|
| Repo | `LCJ-Group/aitherhub` |
| API | `https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net` |
| Auth header | `X-Admin-Key: aither:hub` |
| Frontend | `https://www.aitherhub.com` |
| Admin | `https://www.aitherhub.com/admin` (ID: `aither` / PW: `hub`) |
| Deploy | Push to `master` → GitHub Actions auto-deploy |
| Deploy note | `verify_deploy` step fails due to URL mismatch — if `build_and_deploy` succeeds, deploy is complete |

---

## Worker VM (GPU Analysis Server)

| Item | Value |
|------|-------|
| SSH | `ssh -i workervm_key.pem azureuser@52.185.188.19` |
| Active code | `/opt/aitherhub/worker/` |
| Legacy (should be empty) | `/var/www/aitherhub/` |
| Frame storage | `/tmp/aitherhub_frames/` |
| Logs | `journalctl -u aitherhub-worker -f` |
| Disk (healthy) | <40GB used / >80GB free of 123GB total |
| Deploy | Push to `master` triggers `.github/workflows/deploy_worker.yml` |

---

## Video Analysis Pipeline (15 Steps)

| Step | Status Key | Progress % | Description |
|------|-----------|------------|-------------|
| 0 | STEP_COMPRESS_1080P | 1% | 1080p圧縮 |
| 1 | STEP_0_EXTRACT_FRAMES | 5% | フレーム抽出 (640px, q:v=8) |
| 2 | STEP_1_DETECT_PHASES | 10% | フェーズ検出 |
| 3 | STEP_2_EXTRACT_METRICS | 20% | メトリクス抽出 |
| 4 | STEP_3_TRANSCRIBE_AUDIO | 55% | 音声書き起こし (最長ステップ) |
| 5 | STEP_4_IMAGE_CAPTION | 70% | 画像キャプション |
| 6 | STEP_5_BUILD_PHASE_UNITS | 80% | フェーズユニット構築 |
| 7 | STEP_6_BUILD_PHASE_DESCRIPTION | 85% | フェーズ説明生成 |
| 8 | STEP_7_GROUPING | 90% | グルーピング |
| 9 | STEP_8_UPDATE_BEST_PHASE | 92% | ベストフェーズ更新 |
| 10 | STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES | 94% | 動画構造特徴量 |
| 11 | STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP | 95% | 構造グループ割当 |
| 12 | STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS | 96% | グループ統計更新 |
| 13 | STEP_12_UPDATE_VIDEO_STRUCTURE_BEST | 97% | ベスト構造更新 |
| 14 | STEP_12_5_PRODUCT_DETECTION | 98% | 商品検出 |
| 15 | STEP_13_BUILD_REPORTS | 98% | レポート生成 |
| 16 | STEP_14_FINALIZE | 99% | 最終処理 |
| - | DONE | 100% | 完了 |

This mapping is used in `ProcessingSteps.jsx` (detailed view) and `Sidebar.jsx` (compact "解析中 XX%").

---

## Key Architecture Notes

### Frontend State Management
- **Sidebar.jsx** maintains its own `videos[]` state via `doFetchVideos()` polling (15s during processing, 60s for errors)
- **MainContent.jsx** maintains its own `videoData` state for the selected video
- These are **separate states** — changes in MainContent do NOT auto-propagate to Sidebar
- To sync: call `onUploadSuccess()` which increments `refreshKey` in `MainLayout.jsx`, triggering Sidebar re-fetch
- **refreshKey flow**: `MainContent.onUploadSuccess()` → `MainLayout.setRefreshKey(prev+1)` → `Sidebar.useEffect([refreshKey])` → `doFetchVideos()`

### Frame Extraction Optimization (2026-03-18)
- Resolution: 640px width (was 1280px) — sufficient for GPT Vision
- JPEG quality: q:v=8 — ~30KB/frame (was ~120KB)
- 9-hour video (32K frames): 0.95GB (was 3.8GB) — 75% reduction
- Early cleanup: frames deleted after STEP_1 (phase detection)

---

## Known Lessons

| Date | Category | Lesson |
|------|----------|--------|
| 2026-03-18 | Worker/Disk | Legacy paths `/var/www/aitherhub/` waste 19GB (.venv, .git). `deploy_worker.yml` auto-cleans. When disk >80%, check legacy paths first. |
| 2026-03-18 | Worker/Frames | 640px + q:v=8 is optimal for GPT Vision (75% disk savings). After STEP_1, frames should be cleaned. |
| 2026-03-18 | Frontend/State | Sidebar and MainContent have SEPARATE video state. Use `onUploadSuccess()` → `refreshKey` to sync. |
| 2026-03-18 | Frontend/Subtitle | ClipEditorV2 captions may be local or absolute time. Auto-detect by checking if first caption start is close to clip start. |
| 2026-03-13 | Frontend/JSX | JSX text content does NOT interpret `\uXXXX` Unicode escapes. Use `{'\u2728'}` (JS expression) or actual Unicode chars instead. JS string literals in props/variables DO work correctly. |
| 2026-03-13 | Frontend/Loading | Always add safety timeouts to API-dependent loading states to prevent infinite spinners. |
| 2026-03-13 | Backend/Decorator | Empty `@router.post()` decorator without path causes import errors — always specify path. |
| 2026-03-13 | Frontend/Performance | `preload="auto"` on `<video>` causes full file download (14.7GB for 9h video). Use `preload="metadata"`. Also: readyState >= 2 is sufficient for playback, `#t=` media fragment helps browser seek, and minimized thumbnails should use `preload="none"`. |

---

## Absolute Rules

- NEVER reset DONE/COMPLETED video status — all analysis data will be lost
- NEVER use "uploaded" as fallback — use STEP_0_EXTRACT_FRAMES
- NEVER edit files via GitHub Web UI — always deploy through git push
- NEVER make destructive changes without user confirmation
- Stability > New features — never break existing functionality
- Prefer root-cause fixes over temporary workarounds
- ALWAYS run `git pull --rebase origin master` before editing any file
- ALWAYS lock files before editing (Layer 2) and unlock after pushing
