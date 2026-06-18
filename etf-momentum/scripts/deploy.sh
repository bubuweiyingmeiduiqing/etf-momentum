#!/bin/bash
# ============================================================
# ETF Momentum 云端部署脚本
# 适用于 Ubuntu/Debian 服务器
# ============================================================

set -e

echo "========================================"
echo " ETF Momentum - 云端部署"
echo "========================================"

# 1. 安装系统依赖
echo "[1/6] 安装系统依赖..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv nginx supervisor

# 2. 创建应用目录
APP_DIR="/opt/etf-momentum"
echo "[2/6] 创建应用目录: $APP_DIR"
sudo mkdir -p $APP_DIR
sudo cp -r . $APP_DIR
sudo chown -R $USER:$USER $APP_DIR

# 3. 创建 Python 虚拟环境并安装依赖
echo "[3/6] 安装 Python 依赖..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. 准备配置文件
echo "[4/6] 准备配置文件..."
if [ ! -f config/config.yaml ]; then
    cp config/config.example.yaml config/config.yaml
    echo "  -> 已从示例创建 config/config.yaml，请修改实际配置！"
fi

# 5. 配置 Supervisor 守护进程
echo "[5/6] 配置 Supervisor..."
sudo tee /etc/supervisor/conf.d/etf-momentum.conf > /dev/null << 'SUPERVISOR'
[program:etf-momentum]
command=/opt/etf-momentum/venv/bin/python /opt/etf-momentum/main.py
directory=/opt/etf-momentum
user=www-data
autostart=true
autorestart=true
stderr_logfile=/var/log/etf-momentum/error.log
stdout_logfile=/var/log/etf-momentum/access.log
SUPERVISOR
sudo mkdir -p /var/log/etf-momentum
sudo supervisorctl reread
sudo supervisorctl update

# 6. 配置 Nginx 反向代理
echo "[6/6] 配置 Nginx..."
sudo tee /etc/nginx/sites-available/etf-momentum > /dev/null << 'NGINX'
server {
    listen 80;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGINX
sudo ln -sf /etc/nginx/sites-available/etf-momentum /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "========================================"
echo " 部署完成！"
echo "========================================"
echo " Web 界面: http://<服务器IP>"
echo " 数据目录: $APP_DIR/data/"
echo " 日志目录: /var/log/etf-momentum/"
echo ""
echo " 下一步:"
echo " 1. 编辑配置: vim $APP_DIR/config/config.yaml"
echo " 2. 重启服务: sudo supervisorctl restart etf-momentum"
echo " 3. 初始化数据: cd $APP_DIR && venv/bin/python main.py --init-db"
