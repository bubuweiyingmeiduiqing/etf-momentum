# ETF Daily Report

这个目录是一个可部署到云服务器的 Python 定时任务，用于：

1. 通过 `AKShare` 获取科创50ETF（588000）和中证500ETF（510500）的最新价格与日线数据（Yahoo Finance 备用）。
2. 将价格、涨跌幅、近 5/10/20 日收益、日线表格等数据填入配置化提示词。
3. 调用 DeepSeek API 生成基于量化交易、动量、均线与 2-6 周持仓周期的 HTML 分析日报。
4. 通过 Gmail SMTP 发送到指定邮箱。
5. 每运行 5 次，自动总结两支 ETF 的阶段收益，并生成提示词优化建议。

> 自动分析仅供研究参考，不构成投资建议。

## 目录结构

```text
etf_daily_report/
  etf_report.py
  config.example.json
  requirements.txt
  run_daily.sh
  README.md
  state/
  logs/
```

运行后会自动生成：

- `state/history.json`：运行次数、每次 ETF 收盘价、阶段收益、提示词建议摘要。
- `logs/etf_report.log`：程序运行日志。
- `logs/cron.log`：通过 `run_daily.sh` 执行时的 cron 输出日志。

## 本地或服务器安装

```bash
cd /home/ubuntu/etf_daily_report
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
```

编辑 `config.json`：

- `email.sender`：你的 Gmail 发件邮箱。
- `email.receiver`：收件邮箱；多个收件人可用英文逗号分隔，或改成 JSON 数组。
- `market_data.daily_days`：每次提供给 DeepSeek 的最近日线条数，默认 20。
- `report.summary_every_runs`：每几次运行生成阶段总结，默认 5。
- `prompts.system_prompt`：发送给 DeepSeek 的系统提示词，用于设置全局角色和约束。
- `prompts.etf_data_block_template`：每支 ETF 的行情数据块模板。
- `prompts.daily_prompt_template`：每日分析提示词模板。
- `prompts.summary_prompt_template`：每 N 次运行后的收益复盘和提示词优化建议模板。

## 配置提示词

项目提示词已经放在 `config.json` 的 `prompts` 字段中，包括系统提示词、ETF 数据块模板、每日分析模板和阶段复盘模板，便于不改代码直接调整分析风格。当前示例提示词聚焦量化交易、动量、均线趋势、量价关系和 2-6 周持仓周期。

支持的模板字段：

### `prompts.system_prompt`

这个模板用于 DeepSeek API 的 system message，用来设置全局角色、语气和风险约束。默认示例为 A 股 ETF 量化交易分析助手，要求围绕 2-6 周持仓周期、动量、均线趋势、量价关系和风险控制进行判断。

可以写成 JSON 字符串，也可以写成字符串数组；数组会自动用换行拼接。

### `prompts.etf_data_block_template`

这个模板用于生成每支 ETF 的数据块，支持以下占位符：

- `{{name}}`：ETF 名称，例如 `科创50ETF`。
- `{{code}}`：ETF 代码，例如 `588000`。
- `{{latest_date}}`：最新交易日。
- `{{close}}`：最新收盘价。
- `{{previous_close}}`：上一交易日收盘价。
- `{{daily_pct}}`：当日涨跌幅。
- `{{return_5d}}` / `{{return_10d}}` / `{{return_20d}}`：近 5/10/20 日收益。
- `{{period_high}}` / `{{period_low}}`：当前日线窗口内最高/最低价。
- `{{volume}}`：最新成交量。
- `{{turnover}}`：最新成交额。
- `{{daily_table}}`：最近日线表格。

### `prompts.daily_prompt_template`

这个模板用于生成每天发给 DeepSeek 的主提示词，必须保留：

- `{{etf_blocks}}`：程序会把两支 ETF 的数据块填入这里。

你可以在这个模板中调整分析框架，例如增加：

- 更重视止损位和信号失效条件。
- 更重视动量延续/衰减。
- 更重视均线趋势和区间高低点位置。
- 要求输出 2-6 周持仓周期下的交易提示。
- 要求对两支 ETF 做相对强弱比较。

### `prompts.summary_prompt_template`

这个模板用于每运行 N 次后的复盘，支持：

- `{{recent_runs}}`：最近 N 次运行记录。
- `{{period_returns}}`：最近 N 次运行区间收益。
- `{{current_daily_prompt}}`：当前日报提示词，便于 DeepSeek 针对提示词给优化建议。

提示词模板可以写成 JSON 字符串，也可以像 `config.example.json` 一样写成字符串数组；程序会自动用换行拼接数组内容。

## 配置密钥

推荐用环境变量，不要把密钥写进源码。

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
export GMAIL_APP_PASSWORD="你的 Gmail 应用专用密码"
```

如果使用 `run_daily.sh`，也可以在项目根目录创建 `.env`：

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
export GMAIL_APP_PASSWORD="你的 Gmail 应用专用密码"
```

`.env` 已被 `.gitignore` 忽略，不建议提交。

### Gmail 应用专用密码

Gmail SMTP 通常需要开启两步验证，然后在 Google 账号中创建“应用专用密码”。不要使用 Gmail 登录密码。

## 手动运行

```bash
python etf_report.py
```

只生成邮件 HTML 预览、不发送邮件：

```bash
python etf_report.py --skip-email
```

预览文件会保存到 `logs/preview_run_*.html`。

## 云服务器定时运行

先确认脚本可执行：

```bash
chmod +x /home/ubuntu/etf_daily_report/run_daily.sh
```

手动测试：

```bash
/home/ubuntu/etf_daily_report/run_daily.sh
```

加入 crontab：

```bash
crontab -e
```

例如每个交易日 15:30 执行：

```cron
30 15 * * 1-5 /home/ubuntu/etf_daily_report/run_daily.sh
```

如服务器时区不是中国时区，请先配置服务器时区或调整 cron 时间。

## 5 次运行总结逻辑

程序每次运行都会把本次 ETF 收盘价、涨跌幅和日报摘要追加到 `state/history.json`。

当累计运行次数满足：

```text
run_count % report.summary_every_runs == 0
```

程序会取最近 N 次记录，计算两支 ETF 从这段起点到终点的收益，并再次调用 DeepSeek 生成：

- 最近 N 次运行收益复盘。
- 两支 ETF 相对强弱和风险机会总结。
- 动量、均线/趋势、量价判断的信号有效性评估。
- 提示词优化建议。
- 建议加入日报提示词的片段。

默认 N = 5。

## 常见问题

### 1. 提示 `config.json` 不存在

复制模板：

```bash
cp config.example.json config.json
```

### 2. DeepSeek Key 或 Gmail 密码未配置

设置环境变量：

```bash
export DEEPSEEK_API_KEY="..."
export GMAIL_APP_PASSWORD="..."
```

### 3. cron 没有发送邮件

检查：

```bash
tail -n 200 logs/cron.log
tail -n 200 logs/etf_report.log
```

cron 环境变量较少，建议把密钥写在项目根目录 `.env` 中，或写入服务器用户的 shell profile。
