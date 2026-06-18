# ETF Momentum —— 个人量化交易辅助系统

基于 Python 3 的股票/ETF 量化监测、分析、通知一站式辅助系统。

## 功能特性

- **📊 实时行情监控** - 基于 akshare 获取 A 股 ETF 实时行情
- **📈 技术指标计算** - MA/RSI/MACD/布林带等常用技术指标
- **🔔 智能告警** - 涨跌幅、放量、RSI 超买超卖、布林带突破等多维告警
- **💡 交易信号** - 综合指标信号生成
- **🌐 Web 可视化** - Flask 仪表盘，支持移动端查看
- **✈️ Telegram 通知** - 告警实时推送到 Telegram
- **📧 邮件通知** - 重要告警邮件通知
- **💾 轻量数据库** - SQLite 存储，本地可随时导出查询
- **☁️ 云端部署** - 一键部署脚本，支持 Supervisor + Nginx

## 项目结构

```
etf-momentum/
├── config/                 # 配置模块
│   ├── __init__.py         #   配置加载（支持环境变量覆盖）
│   └── config.example.yaml #   配置示例
├── core/                   # 核心模块
│   ├── database.py         #   SQLite 数据库封装
│   ├── models.py           #   数据模型定义
│   ├── fetcher.py          #   行情数据采集
│   └── scheduler.py        #   任务调度器
├── monitor/                # 监控模块
│   ├── indicators.py       #   技术指标计算
│   └── alerts.py           #   告警规则引擎
├── notify/                 # 通知模块
│   ├── telegram.py         #   Telegram Bot
│   └── email.py            #   邮件通知
├── web/                    # Web 界面
│   ├── app.py              #   Flask 应用
│   ├── templates/          #   HTML 模板
│   └── static/             #   静态资源
├── scripts/
│   └── deploy.sh           # 云端部署脚本
├── data/                   # 数据库文件目录
├── tests/                  # 测试目录
├── main.py                 # 主入口
├── run_web.py              # Web 服务独立入口
├── requirements.txt        # Python 依赖
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config/config.example.yaml config/config.yaml
# 编辑 config/config.yaml，填入 Telegram Token、邮箱等
```

敏感信息也可通过环境变量覆盖：
- `ETF_TELEGRAM_TOKEN` / `ETF_TELEGRAM_CHAT_ID`
- `ETF_EMAIL_PASSWORD`
- `ETF_WEB_SECRET_KEY`

### 3. 初始化数据

```bash
python main.py --init-db   # 同步历史数据到 SQLite
```

### 4. 运行

```bash
python main.py              # 启动 Web + 后台调度器
python main.py --web        # 仅 Web 界面
python main.py --fetch      # 执行一次数据抓取
```

浏览器打开 `http://localhost:5000` 查看仪表盘。

## 云端部署

```bash
bash scripts/deploy.sh
```

部署后通过 `http://<服务器IP>` 访问，Supervisor 保证进程自动重启。

## 数据库

SQLite 文件位于 `data/etf_momentum.db`，可直接用以下工具查询：

```bash
sqlite3 data/etf_momentum.db "SELECT * FROM quotes WHERE symbol='510050' LIMIT 10"
```

或使用任何 SQLite GUI 客户端（如 DB Browser for SQLite）连接查看。

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

## License

MIT
