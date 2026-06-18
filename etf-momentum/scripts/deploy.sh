#!/bin/bash
# ============================================================
# ETF Momentum - 从零初始化云端环境（首次部署）
# 
# 用法（在服务器上执行）:
#   git clone https://github.com/你的用户名/你的仓库.git /opt/etf-momentum
#   cd /opt/etf-momentum && bash scripts/deploy.sh
# ============================================================

set -e

echo "========================================"
echo " ETF Momentum - 云端首次部署"
echo "========================================"

APP_DIR="/opt/etf-momentum"
REPO_URL="${1:-}"  # GitHub 仓库地址，如 https://github.com/user/repo.git

# 如果提供了仓库地址，先 clone
if [ -n "$REPO_URL" ] && [ ! -d "$APP_DIR/.git" ]; then
    echo "[0] 克隆仓库: $REPO_URL"
    sudo mkdir -p /opt
    sudo git clone "$REPO_URL" "$APP_DIR"
    sudo chown -R "$USER:$USER" "$APP_DIR"
fi

cd "$APP_DIR"

# 1. 安装系统依赖
echo "[1/6] 安装系统依赖..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv nginx supervisor git

# 2. 创建 Python 虚拟环境
echo "[2/6] 创建 Python 虚拟环境..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt

# 3. 配置文件
echo "[3/6] 准备配置文件..."
if [ ! -f config/config.yaml ]; then
    cp config/config.example.yaml config/config.yaml
    echo "  -> 已创建 config/config.yaml，请编辑填入实际配置！"
    echo "  -> vim $APP_DIR/config/config.yaml"
fi

# 4. 初始化数据库
echo "[4/6] 初始化数据库 & 同步历史数据..."
source venv/bin/activate
python main.py --init-db

# 5. 配置 Supervisor
echo "[5/6] 配置 Supervisor 守护进程..."
sudo mkdir -p /var/log/etf-momentum
sudo tee /etc/supervisor/conf.d/etf-momentum.conf > /dev/null << 'SUPERVISOR'
[program:etf-momentum]
command=/opt/etf-momentum/venv/bin/python /opt/etf-momentum/main.py
directory=/opt/etf-momentum
user=www-data
autostart=true
autorestart=true
stderr_logfile=/var/log/etf-momentum/error.log
stdout_logfile=/var/log/etf-momentum/access.log
environment=PATH="/opt/etf-momentum/venv/bin"
SUPERVISOR
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start etf-momentum

# 6. 配置 Nginx
echo "[6/6] 配置 Nginx 反向代理..."
sudo tee /etc/nginx/sites-available/etf-momentum > /dev/null << 'NGINX'
server {
    listen 80;
    server_name _;
    client_max_body_size 10M;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX
sudo ln -sf /etc/nginx/sites-available/etf-momentum /etc/nginx/sites-enabled/ 2>/dev/null || true
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "========================================"
echo " 部署完成！"
echo "========================================"
echo " Web 界面: http://$(curl -s ifconfig.me 2>/dev/null || echo '<服务器IP>')"
echo " 应用目录: $APP_DIR"
echo " 数据文件: $APP_DIR/data/etf_momentum.db"
echo ""
echo " 下一步:"
echo " 1. 编辑配置: vim $APP_DIR/config/config.yaml"
echo " 2. 重启服务: sudo supervisorctl restart etf-momentum"
echo " 3. 查看日志: sudo tail -f /var/log/etf-momentum/access.log"
echo ""
echo " 日常更新:"
echo "   cd $APP_DIR && bash scripts/sync.sh"
