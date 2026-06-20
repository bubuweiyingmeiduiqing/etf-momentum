# ETF-Momentum 多资产全球轮动量化系统

基于风险调整动量策略的 ETF 量化交易辅助系统，覆盖数据采集、技术指标计算、告警通知、Web 看板。

> **核心提示词** → 见 [`prompts/`](prompts/) 目录

## 项目结构

```
etf-momentum/
  main.py              # 主入口
  prompts/             # 🔥 核心提示词（日报 + 复盘）
    daily_prompt.txt
    review_prompt.txt
    daily_config.json
    review_config.json
    daily_data_template.json
    review_data_template.json
  config/              # 系统配置（YAML）
  core/                # 数据采集、调度、数据库
  monitor/             # 技术指标计算与告警
  notify/              # Telegram / 邮件通知
  web/                 # Web 看板
  data/                # SQLite 数据库
```

## 策略要点

| 维度 | 规则 |
|------|------|
| ETF池 | 6支权益（500/红利/纳指/日经/科创50/创业板）+ 国债511010 |
| 评分 | 风险调整动量 = 20日收益率 ÷ 20日波动率 |
| 入场 | 价格>SMA20 + SMA20未下行 + 跨境溢价<1.5% |
| 仓位 | 风险平价，前2名，单支≤50% |
| 防御 | 均ATR>3.5% → 40%国债 |
| 止损 | SMA20破位 / 3倍ATR移动止损 |

## 快速启动

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
# 编辑 config/config.yaml 填入实际配置
python main.py --init-db    # 初始化数据库
python main.py               # 启动全部服务
```
