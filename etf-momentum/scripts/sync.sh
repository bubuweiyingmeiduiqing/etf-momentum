#!/bin/bash
# ============================================================
# ETF Momentum - 服务器端快速同步与重启
# 用法: bash scripts/sync.sh
#
# 前置条件:
#   1. 服务器已通过 deploy.sh 完成首次部署
#   2. 本地代码已 push 到 GitHub
# ============================================================

set -e

APP_DIR="/opt/etf-momentum"
BRANCH="${1:-master}"

echo "========================================"  
echo " ETF Momentum - 代码同步与热更新"
echo "========================================"

# 1. 拉取最新代码
echo "[1/3] 拉取最新代码 (分支: $BRANCH)..."
cd "$APP_DIR"
git fetch origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

# 2. 安装/更新 Python 依赖
echo "[2/3] 检查 Python 依赖..."
if [ -f "$APP_DIR/venv/bin/pip" ]; then
    source "$APP_DIR/venv/bin/activate"
    pip install -r requirements.txt --quiet
fi

# 3. 重启服务
echo "[3/3] 重启服务..."
if command -v supervisorctl &> /dev/null; then
    sudo supervisorctl restart etf-momentum
    echo "  -> Supervisor 服务已重启"
else
    echo "  -> Supervisor 未安装，请手动重启服务"
fi

echo ""
echo "========================================"
echo " 同步完成！"
echo "========================================"
echo " 当前版本: $(git log -1 --oneline)"
echo " 查看日志: sudo tail -f /var/log/etf-momentum/access.log"
