#!/bin/bash
# cron_retrain.sh – 毎日深夜3時に自動再学習を実行
# Cron entry: 0 3 * * * /opt/aitherhub/worker/batch/cron_retrain.sh >> /var/log/aitherhub_retrain.log 2>&1
#
# 処理フロー:
#   1. generate_dataset.py → /tmp/datasets/ にデータセット生成
#   2. train.py --input-dir → click + order 両方を1回で学習
#   3. save_metrics_to_db.py → DBにメトリクス保存
#   4. モデルファイルを worker/batch/models/latest/ にコピー
#   5. git commit & push → 自動デプロイ

set -e

AITHERHUB_DIR="/opt/aitherhub"
VENV_PYTHON="${AITHERHUB_DIR}/.venv/bin/python"
DATASET_DIR="/tmp/datasets"
MODELS_DIR="/tmp/models"
MODELS_DEPLOY_DIR="${AITHERHUB_DIR}/worker/batch/models/latest"
LOG_PREFIX="[cron_retrain $(date '+%Y-%m-%d %H:%M:%S')]"

# Load environment
cd ${AITHERHUB_DIR}
export $(grep -v '^#' ${AITHERHUB_DIR}/.env | xargs 2>/dev/null)

echo "${LOG_PREFIX} === Starting daily retrain ==="

# Step 1: Generate dataset
echo "${LOG_PREFIX} Step 1: Generating dataset..."
rm -rf ${DATASET_DIR}
mkdir -p ${DATASET_DIR}
${VENV_PYTHON} -u ${AITHERHUB_DIR}/worker/batch/generate_dataset.py --output-dir ${DATASET_DIR}
if [ $? -ne 0 ]; then
    echo "${LOG_PREFIX} ERROR: Dataset generation failed"
    exit 1
fi
echo "${LOG_PREFIX} Step 1: Dataset generated"

# Step 2: Train models (--input-dir processes both click and order internally)
echo "${LOG_PREFIX} Step 2: Training models (click + order)..."
rm -rf ${MODELS_DIR}
mkdir -p ${MODELS_DIR}
${VENV_PYTHON} -u ${AITHERHUB_DIR}/worker/batch/train.py --input-dir ${DATASET_DIR} --output-dir ${MODELS_DIR}
if [ $? -ne 0 ]; then
    echo "${LOG_PREFIX} ERROR: Training failed"
    exit 1
fi
echo "${LOG_PREFIX} Step 2: Training complete"

# Step 3: Save metrics to DB
echo "${LOG_PREFIX} Step 3: Saving metrics to DB..."
${VENV_PYTHON} -u ${AITHERHUB_DIR}/worker/batch/save_metrics_to_db.py --model-dir ${MODELS_DIR} || {
    echo "${LOG_PREFIX} WARNING: Metrics save failed (non-fatal)"
}
echo "${LOG_PREFIX} Step 3: Metrics saved"

# Step 4: Deploy model files
echo "${LOG_PREFIX} Step 4: Deploying model files..."
rm -rf ${MODELS_DEPLOY_DIR}
mkdir -p ${MODELS_DEPLOY_DIR}
cp ${MODELS_DIR}/*.pkl ${MODELS_DEPLOY_DIR}/ 2>/dev/null || true
cp ${MODELS_DIR}/*.json ${MODELS_DEPLOY_DIR}/ 2>/dev/null || true
echo "${LOG_PREFIX} Step 4: Model files deployed to ${MODELS_DEPLOY_DIR}"

# Step 5: Git commit & push
echo "${LOG_PREFIX} Step 5: Git commit & push..."
cd ${AITHERHUB_DIR}
git add worker/batch/models/latest/
CHANGED=$(git diff --cached --name-only)
if [ -n "$CHANGED" ]; then
    git commit -m "chore(ml): auto-retrain $(date '+%Y-%m-%d') - models updated"
    git push origin master
    echo "${LOG_PREFIX} Step 5: Pushed to master"
else
    echo "${LOG_PREFIX} Step 5: No model changes to push"
fi

echo "${LOG_PREFIX} === Daily retrain complete ==="
