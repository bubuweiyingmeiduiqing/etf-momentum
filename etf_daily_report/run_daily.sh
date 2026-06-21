#!/bin/bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/etf_daily_report}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/venv/bin/python}"

cd "$PROJECT_DIR"
mkdir -p logs

# 可选：在项目根目录创建 .env，写入：
# export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
# export GMAIL_APP_PASSWORD="你的 Gmail 应用专用密码"
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

"$PYTHON_BIN" etf_report.py >> logs/cron.log 2>&1
