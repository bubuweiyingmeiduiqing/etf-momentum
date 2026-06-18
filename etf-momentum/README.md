# ETF Momentum —— 个人量化交易辅助系统

基于 Python 3 的股票/ETF 量化监测、分析、通知一站式辅助系统。

## 功能特性

- **📊 实时行情监控** - 基于 akshare 获取 A 股 ETF 实时行情
- **📈 技术指标计算** - MA/RSI/MACD/布林带等常用技术指标
- **🔔 智能告警** - 涨跌幅、放量、RSI 超买超卖、布林带突破等多维告警
- **💡 交易信号** - 综合指标信号生成
- **🌐 Web 可视化** - Flask 仪表盘，深色主题，移动端适配
- **✈️ Telegram 通知** - 告警实时推送到 Telegram
- **📧 邮件通知** - 重要告警邮件通知
- **💾 轻量数据库** - SQLite WAL 模式，本地可随时导出查询
- **☁️ 云端部署** - 一键部署 + 热更新，Supervisor + Nginx 守护

## 项目结构

```
etf-momentum/
├── config/                 # 配置模块（YAML + 环境变量覆盖）
├── core/                   # 核心引擎
│   ├── database.py         #   SQLite 数据库（7张表，WAL模式）
│   ├── fetcher.py          #   行情数据采集（akshare）
│   ├── models.py           #   数据模型（Quote/Indicator/Alert/Signal）
│   └── scheduler.py        #   定时任务调度
├── monitor/                # 监控分析
│   ├── indicators.py       #   技术指标（MA/RSI/MACD/布林带）
│   └── alerts.py           #   告警规则引擎
├── notify/                 # 消息通知
│   ├── telegram.py         #   Telegram Bot
│   └── email.py            #   SMTP 邮件
├── web/                    # Web 仪表盘
│   ├── app.py              #   Flask 应用 + REST API
│   └── templates/          #   深色主题页面
├── scripts/
│   ├── deploy.sh           #   云端首次部署（完整安装）
│   └── sync.sh             #   服务器热更新（拉代码 + 重启）
├── .github/workflows/
│   └── deploy.yml          #   GitHub Actions 自动部署（可选）
├── data/                   #   数据库文件目录
├── tests/                  #   测试目录
├── main.py                 #   统一入口
└── requirements.txt        #   Python 依赖
```

## 快速开始（本地）

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml   # 编辑配置
python main.py --init-db     # 同步历史数据
python main.py               # 启动 Web + 调度器
```

浏览器打开 `http://localhost:5000` 查看仪表盘。

---

## 云端部署完整指南

### 整体流程：本地 → GitHub → 云服务器

```
 本地开发          GitHub          云服务器
┌─────────┐     ┌─────────┐     ┌──────────┐
│ git push │ ──> │ 代码仓库 │ <── │ git pull │
│ 修改代码 │     │ 版本管理 │     │ 运行服务 │
└─────────┘     └─────────┘     └──────────┘
                     │                │
                     ▼                ▼
              GitHub Actions   supervisor 守护
              (自动部署可选)    Nginx 反向代理
```

### 第一步：代码推送到 GitHub

```bash
# 在本地 etf-momentum 目录下
git remote add origin https://github.com/你的用户名/你的仓库.git
git push -u origin master --tags
```

### 第二步：服务器首次部署

```bash
# SSH 登录云服务器后，一键部署
git clone https://github.com/你的用户名/你的仓库.git /opt/etf-momentum
cd /opt/etf-momentum
bash scripts/deploy.sh
```

`deploy.sh` 会自动完成：
1. 安装 Python 虚拟环境 + 全部依赖
2. 创建配置文件（需手动编辑填入密钥）
3. 初始化 SQLite 数据库 + 拉取历史数据
4. 配置 Supervisor 守护进程（崩溃自动重启）
5. 配置 Nginx 反向代理（域名/SSL 自行追加）

### 第三步：配置密钥

```bash
vim /opt/etf-momentum/config/config.yaml
# 填入 Telegram Bot Token、Chat ID、邮箱 SMTP 密码等
```

敏感信息也可放在 `/opt/etf-momentum/.env` 中用环境变量覆盖：
```bash
ETF_TELEGRAM_TOKEN=123456:ABC...
ETF_TELEGRAM_CHAT_ID=789012
ETF_EMAIL_PASSWORD=your_app_password
```

重启生效：`sudo supervisorctl restart etf-momentum`

### 第四步：日常更新（本地改完代码后）

```bash
# 本地：推送到 GitHub
git add . && git commit -m "feat: xxx" && git push

# 服务器：一键同步
ssh 你的服务器 "cd /opt/etf-momentum && bash scripts/sync.sh"
```

或者用 **GitHub Actions 自动部署**（见下方）。

### 可选：GitHub Actions 自动部署

1. 在 GitHub 仓库 `Settings → Secrets` 中添加三个密钥：
   - `SSH_HOST`：云服务器 IP
   - `SSH_USER`：SSH 用户名
   - `SSH_KEY`：`cat ~/.ssh/id_rsa` 的内容

2. 之后每次 `git push` 到 master 分支，GitHub Actions 会自动 SSH 到服务器执行 `sync.sh`

---

## 入口说明

| 命令 | 用途 |
|------|------|
| `python main.py` | 启动全部服务（Web + 调度器） |
| `python main.py --web` | 仅启动 Web 界面 |
| `python main.py --scheduler` | 仅启动后台调度器 |
| `python main.py --fetch` | 执行一次数据抓取 + 指标计算 + 告警检查 |
| `python main.py --init-db` | 初始化数据库 + 同步历史数据 |

## 数据库

SQLite 文件位于 `data/etf_momentum.db`，可从服务器下载到本地查询：

```bash
# 从服务器下载数据库
scp 你的服务器:/opt/etf-momentum/data/etf_momentum.db .

# 本地查询
sqlite3 etf_momentum.db "SELECT * FROM quotes WHERE symbol='510050' LIMIT 10"
```

或使用任何 SQLite GUI（如 DB Browser for SQLite）直连查看。

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| Web 框架 | Flask |
| 数据库 | SQLite (WAL 模式) |
| 数据源 | akshare |
| 调度器 | APScheduler |
| 通知 | python-telegram-bot + SMTP |
| 部署 | Supervisor + Nginx |
| CI/CD | GitHub Actions（可选） |

## License

MIT
