# -*- coding: utf-8 -*-
"""
中国银行(601988) KDJ-J值 短线波段交易策略回测系统
目标：3万元全仓进出，每笔盈利1000左右（~3.3%），根据日线KDJ的J值买卖
高频操作，全年反复波段
"""

import sys, os
os.environ['PYTHONIOENCODING'] = 'utf-8'

import akshare as ak
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ===================== 参数 =====================
STOCK_CODE = "601988"
CAPITAL = 30000
TARGET_PROFIT = 1000
TARGET_PROFIT_PCT = TARGET_PROFIT / CAPITAL  # 3.33%
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
SLIPPAGE = 0.001
MIN_HOLD_DAYS = 1  # T+1

print("=" * 85)
print(f"  中国银行({STOCK_CODE}) KDJ-J值短线波段策略回测")
print(f"  资金: {CAPITAL:,}元 | 目标: 每笔±{TARGET_PROFIT}元 | T+1规则")
print("=" * 85)

# ===================== 数据获取 =====================
print("\n[1/4] 获取数据...")
df_raw = ak.stock_zh_a_hist(
    symbol=STOCK_CODE, period="daily",
    start_date="20190101", end_date="20260608", adjust="qfq"
)
df_raw = df_raw.rename(columns={
    "日期": "date", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low", "成交量": "volume",
    "成交额": "amount", "涨跌幅": "pct_change"
})
df_raw["date"] = pd.to_datetime(df_raw["date"])
df_raw = df_raw.sort_values("date").reset_index(drop=True)
print(f"  数据: {df_raw['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df_raw['date'].iloc[-1].strftime('%Y-%m-%d')}")

# ===================== 计算KDJ =====================
print("\n[2/4] 计算KDJ指标...")

close = df_raw["close"].values
high = df_raw["high"].values
low = df_raw["low"].values

n = 9
# RSV
low_n = df_raw["low"].rolling(n).min()
high_n = df_raw["high"].rolling(n).max()
rsv = (close - low_n) / (high_n - low_n) * 100

# K, D, J
kdj_k = np.full(len(close), 50.0)
kdj_d = np.full(len(close), 50.0)
kdj_j = np.full(len(close), 50.0)

for i in range(1, len(close)):
    if pd.notna(rsv.iloc[i]):
        kdj_k[i] = 2/3 * kdj_k[i-1] + 1/3 * rsv.iloc[i]
        kdj_d[i] = 2/3 * kdj_d[i-1] + 1/3 * kdj_k[i]
        kdj_j[i] = 3 * kdj_k[i] - 2 * kdj_d[i]

df = df_raw.copy()
df["kdj_k"] = kdj_k
df["kdj_d"] = kdj_d
df["kdj_j"] = kdj_j

# 辅助指标
df["ma5"] = df["close"].rolling(5).mean()
df["ma10"] = df["close"].rolling(10).mean()
df["ma20"] = df["close"].rolling(20).mean()
df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
df["vol_ma5"] = df["volume"].rolling(5).mean()
df["vol_ratio"] = df["volume"] / df["vol_ma5"]

# 前一日J值
df["j_prev"] = df["kdj_j"].shift(1)
df["j_prev2"] = df["kdj_j"].shift(2)
df["j_prev3"] = df["kdj_j"].shift(3)
# J值变化
df["j_delta"] = df["kdj_j"] - df["j_prev"]
# J值变化加速
df["j_delta2"] = df["j_delta"] - df["j_delta"].shift(1)

df = df.dropna().reset_index(drop=True)
print(f"  有效数据: {len(df)} 条")

# J值分布统计
print(f"  J值范围: {df['kdj_j'].min():.0f} ~ {df['kdj_j'].max():.0f}")
print(f"  J<0天数: {(df['kdj_j'] < 0).sum()} ({(df['kdj_j'] < 0).mean():.1%})")
print(f"  J>100天数: {(df['kdj_j'] > 100).sum()} ({(df['kdj_j'] > 100).mean():.1%})")
print(f"  J<20天数: {(df['kdj_j'] < 20).sum()} ({(df['kdj_j'] < 20).mean():.1%})")
print(f"  J>80天数: {(df['kdj_j'] > 80).sum()} ({(df['kdj_j'] > 80).mean():.1%})")

# ===================== 通用回测引擎 =====================
class TradeRecord:
    def __init__(self):
        self.trades = []

    def add(self, buy_date, buy_price, sell_date, sell_price, shares, reason_b, reason_s):
        buy_cost = buy_price * shares * (1 + COMMISSION_RATE)
        sell_income = sell_price * shares * (1 - COMMISSION_RATE - STAMP_TAX_RATE)
        profit = sell_income - buy_cost
        self.trades.append({
            "buy_date": buy_date, "buy_price": buy_price,
            "sell_date": sell_date, "sell_price": sell_price,
            "shares": shares, "buy_cost": buy_cost, "sell_income": sell_income,
            "profit": profit, "profit_pct": profit / buy_cost,
            "hold_days": (sell_date - buy_date).days,
            "reason_b": reason_b, "reason_s": reason_s
        })

    def summary(self, name):
        if not self.trades:
            return {"name": name, "total_trades": 0, "win_rate": 0, "total_profit": 0,
                    "total_return": 0, "avg_profit": 0, "avg_hold": 0, "score": 0, "final_cap": CAPITAL}

        profits = [t["profit"] for t in self.trades]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        total = sum(profits)
        wr = len(wins) / len(profits)
        avg_p = np.mean(profits)
        avg_h = np.mean([t["hold_days"] for t in self.trades])
        final_cap = CAPITAL + total
        n = len(profits)

        sharpe = np.mean(profits) / (np.std(profits)+1e-9) * np.sqrt(n) if n > 1 else 0
        profit_consistency = 1 - (np.std(profits) / (abs(np.mean(profits)) + 1e-9)) / 5
        profit_consistency = max(0, min(1, profit_consistency))

        # 评分：收益(35%) + 胜率(25%) + 交易频次(20%) + 收益稳定(10%) + 夏普(10%)
        score = (
            (total / CAPITAL) * 100 * 0.35 +
            wr * 100 * 0.25 +
            min(n / 30, 1) * 100 * 0.20 +
            profit_consistency * 100 * 0.10 +
            max(sharpe, -2) * 5 * 0.10
        )
        return {
            "name": name, "total_trades": n, "win_trades": len(wins),
            "loss_trades": len(losses), "win_rate": wr,
            "total_profit": total, "total_return": total / CAPITAL,
            "avg_profit": avg_p, "avg_profit_pct": avg_p / CAPITAL,
            "avg_hold": avg_h, "max_profit": max(profits),
            "max_loss": min(profits), "sharpe": sharpe,
            "score": score, "final_cap": final_cap,
            "profit_std": np.std(profits)
        }


def run_backtest(df, name, buy_fn, sell_fn):
    """通用回测"""
    rec = TradeRecord()
    pos = False
    bp, bd, bi, shares = 0, None, 0, 0
    rb = ""

    for i in range(1, len(df)):
        if not pos:
            signal, reason = buy_fn(df, i)
            if signal:
                bp = df["close"].iloc[i] * (1 + SLIPPAGE)
                bd = df["date"].iloc[i]
                bi = i
                shares = int(CAPITAL / bp / 100) * 100
                if shares < 100:
                    continue
                pos = True
                rb = reason
        else:
            hold_days = (df["date"].iloc[i] - bd).days
            signal, reason = sell_fn(df, i, bp, hold_days)
            if signal and hold_days >= MIN_HOLD_DAYS:
                sp = df["close"].iloc[i] * (1 - SLIPPAGE)
                sd = df["date"].iloc[i]
                rec.add(bd, bp, sd, sp, shares, rb, reason)
                pos = False

    if pos:
        sp = df["close"].iloc[-1] * (1 - SLIPPAGE)
        sd = df["date"].iloc[-1]
        rec.add(bd, bp, sd, sp, shares, rb, "强制平仓")
    return rec


# ===================== 策略定义 =====================
print("\n[3/4] 定义并运行KDJ-J值策略...")

strategies = []

# ---------- 策略A: J值极端区域 ----------
def strat_a():
    """J<0超卖买入，J>100超买卖出"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        # J从0下方拐头向上，或J连续3天在0下方后任意一天买入
        c1 = jp < 0 and j > jp and j > -15
        c2 = df["j_prev3"].iloc[i] < 0 and df["j_prev2"].iloc[i] < 0 and df["j_prev"].iloc[i] < 0 and j > jp
        c3 = df["close"].iloc[i] > df["open"].iloc[i]  # 收阳确认
        if (c1 or c2) and c3:
            return True, f"J超卖反弹 J={j:.0f}→{j-jp:+.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold_days):
        j = df["kdj_j"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.03:
            return True, f"止损 J={j:.0f} PP={pp:.2%}"
        if pp >= TARGET_PROFIT_PCT:
            return True, f"目标止盈 J={j:.0f} PP={pp:.2%}"
        if j > 105:
            return True, f"J超卖区 J={j:.0f} PP={pp:.2%}"
        # J从高位回落
        if j < 90 and df["j_prev"].iloc[i] > 100 and pp > 0.005:
            return True, f"J高位回落 J={j:.0f} PP={pp:.2%}"
        return False, ""

    rec = run_backtest(df, "J极端值(超卖买/超买卖)", buy_fn, sell_fn)
    return rec, rec.summary("J极端值(超卖买/超买卖)")

strategies.append(strat_a)

# ---------- 策略B: J值20-80区域操作 ----------
def strat_b():
    """J<20买入，J>80卖出，捕捉中段波动"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        # J从20下方上穿20，且J拐头向上
        c1 = jp < 20 and j >= 20
        # J在0-20区间内拐头，且收阳
        c2 = jp < 20 and j > jp and j < 40 and df["close"].iloc[i] > df["open"].iloc[i]
        # J极度超卖
        c3 = jp < 0 and j > jp
        if c1 or c2 or c3:
            return True, f"J低位买入 J={j:.0f} ({jp:.0f}→{j:.0f})"
        return False, ""

    def sell_fn(df, i, bp, hold_days):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.025:
            return True, f"止损 J={j:.0f} PP={pp:.2%}"
        if pp >= TARGET_PROFIT_PCT:
            return True, f"目标止盈 J={j:.0f} PP={pp:.2%}"
        # J从上方下穿80
        if jp >= 80 and j < 80 and pp > 0.005:
            return True, f"J高位死叉 J={j:.0f} PP={pp:.2%}"
        # J>100极端
        if j > 105:
            return True, f"J极端超买 J={j:.0f} PP={pp:.2%}"
        # 持有超7天，J>50且有盈利就走
        if hold_days > 7 and j > 50 and pp > 0.01:
            return True, f"持仓过长止盈 J={j:.0f} PP={pp:.2%}"
        return False, ""

    rec = run_backtest(df, "J值20-80区域操作", buy_fn, sell_fn)
    return rec, rec.summary("J值20-80区域操作")

strategies.append(strat_b)

# ---------- 策略C: J值金叉死叉 + 加速 ----------
def strat_c():
    """J值拐点：J从低点加速上升买入，J从高点减速卖出"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        jd = df["j_delta"].iloc[i]
        jd2 = df["j_delta2"].iloc[i]
        # J在低位(<-10)出现加速向上拐点
        c1 = j < 10 and jd > 5 and jd2 > 0
        # J金叉K线 (J线上穿K线)
        jp_below_k = jp <= df["kdj_k"].iloc[i-1]
        j_above_k = j > df["kdj_k"].iloc[i]
        c2 = jp_below_k and j_above_k and j < 30
        # J值连续两天加速向上
        c3 = jd > 3 and df["j_delta"].iloc[i-1] > 2 and j < 25
        if (c1 or c2 or c3) and df["close"].iloc[i] > df["open"].iloc[i]:
            return True, f"J拐点启动 J={j:.0f} dJ={jd:+.0f} d2J={jd2:+.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold_days):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        jd = df["j_delta"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.025:
            return True, f"止损 J={j:.0f} PP={pp:.2%}"
        if pp >= TARGET_PROFIT_PCT:
            return True, f"目标止盈 J={j:.0f} PP={pp:.2%}"
        # J死叉K线
        if jp >= df["kdj_k"].iloc[i-1] and j < df["kdj_k"].iloc[i] and pp > 0.005:
            return True, f"J死叉K线 J={j:.0f} PP={pp:.2%}"
        # J值转为负加速度 (涨不动了)
        if j > 60 and jd < -3 and df["j_delta2"].iloc[i] < 0 and pp > 0.008:
            return True, f"J涨势衰竭 J={j:.0f} dJ={jd:+.0f} PP={pp:.2%}"
        # J>95 超买
        if j > 95:
            return True, f"J超买 J={j:.0f} PP={pp:.2%}"
        return False, ""

    rec = run_backtest(df, "J值拐点加速策略", buy_fn, sell_fn)
    return rec, rec.summary("J值拐点加速策略")

strategies.append(strat_c)

# ---------- 策略D: 激进J值摆动 ----------
def strat_d():
    """J值30点以上摆动即操作，提高频次"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        # J<15买入
        c1 = jp < 15 and j > jp
        # J从低位快速拉升
        c2 = j < 20 and df["j_delta"].iloc[i] > 2
        # J值在低位横盘后突破
        c3 = (df["j_prev3"].iloc[i] < 20 and df["j_prev2"].iloc[i] < 25 and
              df["j_prev"].iloc[i] < 25 and j > df["j_prev"].iloc[i] and j > 20)
        if c1 or c2 or c3:
            return True, f"J低吸 J={j:.0f} dJ={df['j_delta'].iloc[i]:+.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold_days):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.025:
            return True, f"止损 J={j:.0f} PP={pp:.2%}"
        if pp >= TARGET_PROFIT_PCT:
            return True, f"止盈 J={j:.0f} PP={pp:.2%}"
        # J>85卖出
        if j > 85 and jp < j and pp > 0.005:
            return True, f"J高抛 J={j:.0f} PP={pp:.2%}"
        # 持有3天有1.5%利润就走
        if hold_days >= 3 and pp > 0.015:
            return True, f"快进快出 J={j:.0f} PP={pp:.2%}"
        # J值冲高回落
        if jp > 80 and j < jp - 3 and pp > 0.005:
            return True, f"J冲高回落 J={j:.0f} PP={pp:.2%}"
        return False, ""

    rec = run_backtest(df, "J值激进摆动策略", buy_fn, sell_fn)
    return rec, rec.summary("J值激进摆动策略")

strategies.append(strat_d)

# ---------- 策略E: J值三线共振 ----------
def strat_e():
    """J+K+D三值综合判断"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]
        k = df["kdj_k"].iloc[i]
        d = df["kdj_d"].iloc[i]
        jp = df["j_prev"].iloc[i]
        # 三线都在低位 + J拐头
        c1 = j < 15 and k < 30 and d < 35 and j > jp
        # J线上穿D线（原版KDJ金叉）+ 低位
        c2 = df["j_prev"].iloc[i] <= df["kdj_d"].iloc[i-1] and j > d and j < 40
        # K线在20下方金叉D线
        c3 = (df["kdj_k"].iloc[i-1] <= df["kdj_d"].iloc[i-1] and k > d and k < 30)
        if c1 or c2 or c3:
            return True, f"共振买入 J={j:.0f} K={k:.0f} D={d:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold_days):
        j = df["kdj_j"].iloc[i]
        k = df["kdj_k"].iloc[i]
        d = df["kdj_d"].iloc[i]
        jp = df["j_prev"].iloc[i]
        kp = df["kdj_k"].iloc[i-1]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.025:
            return True, f"止损 J={j:.0f} PP={pp:.2%}"
        if pp >= TARGET_PROFIT_PCT:
            return True, f"止盈 J={j:.0f} PP={pp:.2%}"
        # KDJ三线高位死叉
        if kp >= df["kdj_d"].iloc[i-1] and k < d and jp > j and pp > 0.005:
            return True, f"共振卖出 J={j:.0f} K={k:.0f} PP={pp:.2%}"
        # J线跌破K线
        if jp >= kp and j < k and pp > 0.005:
            return True, f"J破K J={j:.0f} PP={pp:.2%}"
        # 三线都在高位
        if j > 90 and k > 75 and d > 70:
            return True, f"三线高位 J={j:.0f} PP={pp:.2%}"
        return False, ""

    rec = run_backtest(df, "J值三线共振策略", buy_fn, sell_fn)
    return rec, rec.summary("J值三线共振策略")

strategies.append(strat_e)

# ---------- 策略F: J值+量能配合 ----------
def strat_f():
    """J值超卖+放量买入，J值超买+缩量卖出"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        vr = df["vol_ratio"].iloc[i]
        # J<-5超卖 + 放量（量比>1.2）+ 收阳
        c1 = j < -5 and vr > 1.2 and df["close"].iloc[i] > df["open"].iloc[i]
        # J<10 + 放量明显 + 拐头
        c2 = j < 10 and j > jp and vr > 1.0 and df["close"].iloc[i] > df["open"].iloc[i]
        # J<-15极端超卖，放量抄底
        c3 = j < -15 and vr > 0.9
        if c1 or c2 or c3:
            return True, f"J超卖+放量 J={j:.0f} 量比={vr:.1f}"
        return False, ""

    def sell_fn(df, i, bp, hold_days):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        vr = df["vol_ratio"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.025:
            return True, f"止损 J={j:.0f} PP={pp:.2%}"
        if pp >= TARGET_PROFIT_PCT:
            return True, f"止盈 J={j:.0f} PP={pp:.2%}"
        # J>90 + 缩量
        if j > 90 and vr < 0.9 and pp > 0.005:
            return True, f"J超买+缩量 J={j:.0f} 量比={vr:.1f} PP={pp:.2%}"
        # J高位回落+缩量
        if jp > 85 and j < jp and vr < 1.0 and pp > 0.005:
            return True, f"J高位缩量回落 J={j:.0f} PP={pp:.2%}"
        # J>100
        if j > 105:
            return True, f"J极端超买 J={j:.0f} PP={pp:.2%}"
        return False, ""

    rec = run_backtest(df, "J值+量能配合策略", buy_fn, sell_fn)
    return rec, rec.summary("J值+量能配合策略")

strategies.append(strat_f)

# ---------- 策略G: 微利高频（最激进）----------
def strat_g():
    """只要能赚1%就走，止损1.5%，追求高周转"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        # J<5且拐头
        c1 = j < 5 and j > jp
        # J一天跌超20点后的反弹
        c2 = j < 15 and df["j_delta"].iloc[i] > 0 and df["j_delta"].iloc[i-1] < -10
        # J<25且连续两天拐头
        c3 = j < 25 and j > jp and df["j_delta"].iloc[i-1] > 0
        if c1 or c2 or c3:
            return True, f"高频低吸 J={j:.0f} dJ={df['j_delta'].iloc[i]:+.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold_days):
        j = df["kdj_j"].iloc[i]
        jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        # 止损1.5%（更激进止损）
        if pp < -0.015:
            return True, f"紧止损 J={j:.0f} PP={pp:.2%}"
        # 达到3.3%目标
        if pp >= TARGET_PROFIT_PCT:
            return True, f"目标止盈 J={j:.0f} PP={pp:.2%}"
        # 有1.5%利润+J>60（见好就收）
        if pp > 0.015 and j > 60:
            return True, f"微利收割 J={j:.0f} PP={pp:.2%}"
        # 有2%利润+持有3天
        if pp > 0.02 and hold_days >= 3:
            return True, f"短线止盈 J={j:.0f} PP={pp:.2%}"
        # J>80且回落
        if j > 80 and j < jp and pp > 0.005:
            return True, f"J高位 J={j:.0f} PP={pp:.2%}"
        return False, ""

    rec = run_backtest(df, "微利高频策略", buy_fn, sell_fn)
    return rec, rec.summary("微利高频策略")

strategies.append(strat_g)

# ===================== 执行所有策略回测 =====================
all_records = []
all_summaries = []

for strat_fn in strategies:
    rec, summ = strat_fn()
    all_records.append(rec)
    all_summaries.append(summ)
    print(f"  {summ['name']}: {summ['total_trades']:>3d}笔 "
          f"胜率{summ['win_rate']:.1%} 收益¥{summ['total_profit']:>8,.0f} "
          f"({summ['total_return']:.1%}) 均持{summ['avg_hold']:.1f}天")

# ===================== 对比报告 =====================
print("\n[4/4] 策略对比与推荐...\n")
print("=" * 95)
print("                    KDJ-J值短线波段策略对比总览")
print("=" * 95)

sorted_idx = sorted(range(len(all_summaries)),
                    key=lambda x: all_summaries[x]["total_profit"], reverse=True)

header = (f"{'排名':<4s} {'策略名称':<24s} {'笔数':>4s} {'胜率':>6s} "
          f"{'总收益':>10s} {'收益率':>7s} {'均盈':>8s} {'均持':>5s} {'最大亏':>8s} {'评分':>5s}")
print(header)
print("-" * 95)

for rank, idx in enumerate(sorted_idx):
    s = all_summaries[idx]
    print(f"{rank+1:<4d} {s['name']:<24s} {s['total_trades']:>4d} {s['win_rate']:>5.1%} "
          f"¥{s['total_profit']:>9,.0f} {s['total_return']:>6.1%} "
          f"¥{s['avg_profit']:>7,.0f} {s['avg_hold']:>4.1f}天 "
          f"¥{s['max_loss']:>7,.0f} {s['score']:>4.1f}")

print("=" * 95)

# 最优策略
best_idx = sorted_idx[0]
best_rec = all_records[best_idx]
best_sum = all_summaries[best_idx]

print(f"\n  >>> 最优策略: {best_sum['name']}")
print(f"      总收益: ¥{best_sum['total_profit']:,.0f} ({best_sum['total_return']:.1%})")
print(f"      交易笔数: {best_sum['total_trades']} | 胜率: {best_sum['win_rate']:.1%}")
print(f"      最终资金: ¥{best_sum['final_cap']:,.0f}")
print(f"      平均盈利: ¥{best_sum['avg_profit']:,.0f} | 均持: {best_sum['avg_hold']:.1f}天")

# ===================== 最优策略逐年分析 =====================
print(f"\n--- {best_sum['name']} 逐年表现 ---")
yearly = {}
for t in best_rec.trades:
    y = t["buy_date"].year
    if y not in yearly:
        yearly[y] = {"trades": 0, "wins": 0, "profit": 0}
    yearly[y]["trades"] += 1
    yearly[y]["wins"] += 1 if t["profit"] > 0 else 0
    yearly[y]["profit"] += t["profit"]

print(f"{'年份':<8s} {'交易次数':>7s} {'盈利次数':>7s} {'胜率':>7s} {'年度收益':>12s} {'年化收益':>8s}")
print("-" * 55)
for y in sorted(yearly.keys()):
    d = yearly[y]
    print(f"{y:<8d} {d['trades']:>7d} {d['wins']:>7d} {d['wins']/d['trades']:>6.1%} "
          f"¥{d['profit']:>11,.0f} {d['profit']/CAPITAL:>7.1%}")

n_years = max(1, max(yearly.keys()) - min(yearly.keys()) + 1)
print(f"\n  年均交易: {best_sum['total_trades']/n_years:.1f}次")
print(f"  年均收益: ¥{best_sum['total_profit']/n_years:,.0f}")
print(f"  月均交易: {best_sum['total_trades']/max(n_years*12,1):.1f}次")

# ===================== 完整交易记录 =====================
print(f"\n--- {best_sum['name']} 完整交易记录 ---")
print(f"{'#':<4s} {'买入日':<12s} {'买入价':>7s} {'卖出日':<12s} {'卖出价':>7s} "
      f"{'持仓':>5s} {'盈亏':>9s} {'盈利率':>7s} {'买因':<30s} {'卖因':<30s}")
print("-" * 130)
for i, t in enumerate(best_rec.trades):
    print(f"{i+1:<4d} {t['buy_date'].strftime('%Y-%m-%d'):<12s} ¥{t['buy_price']:>6.2f} "
          f"{t['sell_date'].strftime('%Y-%m-%d'):<12s} ¥{t['sell_price']:>6.2f} "
          f"{t['hold_days']:>4d}天 ¥{t['profit']:>7,.0f} {t['profit_pct']:>6.1%} "
          f"{t['reason_b']:<30s} {t['reason_s']:<30s}")
print("-" * 130)

# ===================== 可视化 =====================
print("\n生成图表...")

fig, axes = plt.subplots(2, 3, figsize=(20, 12))
fig.suptitle(f"中国银行({STOCK_CODE}) KDJ-J值短线波段策略回测\n"
             f"初始资金¥{CAPITAL:,} | 目标¥{TARGET_PROFIT}/笔 | "
             f"{df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}",
             fontsize=14, fontweight="bold")

# 图1: 各策略收益柱状图
ax1 = axes[0, 0]
names_short = [s["name"][:16] for s in [all_summaries[i] for i in sorted_idx]]
profits = [all_summaries[i]["total_profit"] for i in sorted_idx]
colors = ["#27ae60" if p > 0 else "#e74c3c" for p in profits]
bars = ax1.barh(range(len(names_short)), profits, color=colors, edgecolor="white")
ax1.set_yticks(range(len(names_short)))
ax1.set_yticklabels(names_short, fontsize=8)
ax1.set_xlabel("总收益 (元)")
ax1.set_title("各策略总收益对比")
ax1.axvline(0, color="black", linewidth=0.5)
for bar, p in zip(bars, profits):
    ax1.text(bar.get_width() + (50 if p >= 0 else -200),
             bar.get_y() + bar.get_height()/2, f"¥{p:,.0f}", va="center", fontsize=7)

# 图2: 交易频次与胜率散点
ax2 = axes[0, 1]
for i, s in enumerate(all_summaries):
    ax2.scatter(s["total_trades"], s["win_rate"]*100,
               s=s["total_profit"]/100+100, alpha=0.7, label=s["name"][:14])
    ax2.annotate(s["name"][:12], (s["total_trades"], s["win_rate"]*100),
                fontsize=6, xytext=(3, 3), textcoords="offset points")
ax2.set_xlabel("交易笔数")
ax2.set_ylabel("胜率 (%)")
ax2.set_title("交易频次 vs 胜率 (气泡=收益)")
ax2.axhline(50, color="red", linestyle="--", linewidth=0.5, alpha=0.5)
ax2.grid(True, alpha=0.3)

# 图3: 最优策略资金曲线
ax3 = axes[0, 2]
if best_rec.trades:
    dates = [df["date"].iloc[0]]
    values = [CAPITAL]
    cur = CAPITAL
    for t in best_rec.trades:
        dates.append(t["sell_date"])
        cur += t["profit"]
        values.append(cur)
    buy_hold = CAPITAL * df["close"] / df["close"].iloc[0]
    ax3.plot(df["date"], buy_hold, color="gray", linewidth=0.6, alpha=0.5, label="买入持有")
    ax3.step(dates, values, where="post", color="#e74c3c", linewidth=1.8, label=best_sum["name"])
    ax3.axhline(CAPITAL, color="black", linestyle="--", linewidth=0.5)
    ax3.set_ylabel("资金 (元)")
    ax3.set_title("最优策略资金曲线 vs 买入持有")
    ax3.legend(fontsize=7)
    ax3.grid(True, alpha=0.3)

# 图4: 最优策略每笔盈亏
ax4 = axes[1, 0]
if best_rec.trades:
    tps = [t["profit"] for t in best_rec.trades]
    nums = range(1, len(tps)+1)
    bcols = ["#27ae60" if p > 0 else "#e74c3c" for p in tps]
    ax4.bar(nums, tps, color=bcols, edgecolor="white")
    ax4.axhline(0, color="black", linewidth=0.5)
    ax4.axhline(TARGET_PROFIT, color="orange", linestyle="--", linewidth=0.8,
               label=f"目标{TARGET_PROFIT}元")
    ax4.set_xlabel("交易序号")
    ax4.set_ylabel("盈亏 (元)")
    ax4.set_title(f"{best_sum['name']} 每笔盈亏")
    ax4.legend(fontsize=7)
    ax4_twin = ax4.twinx()
    ax4_twin.plot(nums, np.cumsum(tps), "b-o", linewidth=1, markersize=3, alpha=0.5)
    ax4_twin.set_ylabel("累计盈亏 (元)", color="blue")
    ax4_twin.tick_params(axis="y", colors="blue")

# 图5: J值分布 vs 交易信号（最优策略）
ax5 = axes[1, 1]
# 画J值走势和买卖点
sample_end = min(500, len(df))
sample_start = max(0, sample_end - 300)
x_range = range(sample_start, sample_end)
ax5.plot(df["date"].iloc[sample_start:sample_end],
         df["kdj_j"].iloc[sample_start:sample_end],
         color="#3498db", linewidth=0.8, label="J值")
ax5.axhline(0, color="red", linestyle="--", linewidth=0.5, alpha=0.5)
ax5.axhline(100, color="green", linestyle="--", linewidth=0.5, alpha=0.5)
# 标买卖点
signal_dates = [t["buy_date"] for t in best_rec.trades]
signal_dates_s = [t["sell_date"] for t in best_rec.trades]
for sd in signal_dates:
    if sample_start <= df[df["date"] == sd].index[0] <= sample_end:
        idx = df[df["date"] == sd].index[0]
        ax5.scatter(df["date"].iloc[idx], df["kdj_j"].iloc[idx],
                   color="red", marker="^", s=50, zorder=5)
for sd in signal_dates_s:
    if sample_start <= df[df["date"] == sd].index[0] <= sample_end:
        idx = df[df["date"] == sd].index[0]
        ax5.scatter(df["date"].iloc[idx], df["kdj_j"].iloc[idx],
                   color="green", marker="v", s=50, zorder=5)
ax5.set_ylabel("J值")
ax5.set_title(f"{best_sum['name']} 买卖信号 (▲买 ▼卖)")
ax5.legend(fontsize=7)
ax5.grid(True, alpha=0.3)
ax5.tick_params(axis='x', rotation=30)

# 图6: 年度收益对比
ax6 = axes[1, 2]
all_years = sorted(set().union(*[{t["buy_date"].year for t in rec.trades}
                                   for rec in all_records]))
if all_years:
    x = np.arange(len(all_years))
    w = 0.11
    colors7 = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22"]
    for j, rec in enumerate(all_records):
        yd = {}
        for t in rec.trades:
            yd[t["buy_date"].year] = yd.get(t["buy_date"].year, 0) + t["profit"]
        yp = [yd.get(y, 0) for y in all_years]
        ax6.bar(x + j*w, yp, w, label=all_summaries[j]["name"][:14],
               color=colors7[j], alpha=0.8)
    ax6.set_xticks(x + w*3)
    ax6.set_xticklabels([str(y) for y in all_years], fontsize=8)
    ax6.set_ylabel("年度收益 (元)")
    ax6.set_title("各策略年度收益对比")
    ax6.axhline(0, color="black", linewidth=0.5)
    ax6.legend(fontsize=5, loc="upper left")

plt.tight_layout()
chart_path = "D:\\mycode\\backtest_601988_kdj_chart.png"
plt.savefig(chart_path, dpi=150, bbox_inches="tight")
print(f"  图表已保存: {chart_path}")

# ===================== 最终推荐 =====================
print("\n" + "=" * 85)
print("                        最终推荐")
print("=" * 85)
print(f"""
  [策略] {best_sum['name']}
  [收益] ¥{best_sum['total_profit']:,.0f} ({best_sum['total_return']:.1%})
  [交易] {best_sum['total_trades']}笔 | 胜率{best_sum['win_rate']:.1%} | 均持{best_sum['avg_hold']:.1f}天
  [资金] ¥{CAPITAL:,} → ¥{best_sum['final_cap']:,.0f}
  [频次] 年均{best_sum['total_trades']/n_years:.1f}次 (~{best_sum['total_trades']/max(n_years*12,1):.1f}次/月)
  [评分] {best_sum['score']:.1f}
""")

# 第二名
if len(sorted_idx) > 1:
    s2 = all_summaries[sorted_idx[1]]
    print(f"  第二名: {s2['name']}")
    print(f"  收益¥{s2['total_profit']:,.0f} | {s2['total_trades']}笔 | 胜率{s2['win_rate']:.1%}")

# 第三名
if len(sorted_idx) > 2:
    s3 = all_summaries[sorted_idx[2]]
    print(f"  第三名: {s3['name']}")
    print(f"  收益¥{s3['total_profit']:,.0f} | {s3['total_trades']}笔 | 胜率{s3['win_rate']:.1%}")

# 保存报告
report_path = "D:\\mycode\\backtest_601988_kdj_report.txt"
with open(report_path, "w", encoding="utf-8") as f:
    f.write("=" * 90 + "\n")
    f.write(f"中国银行({STOCK_CODE}) KDJ-J值短线波段策略回测报告\n")
    f.write(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')} | ")
    f.write(f"数据: {df['date'].iloc[0].strftime('%Y-%m-%d')}~{df['date'].iloc[-1].strftime('%Y-%m-%d')}\n")
    f.write(f"资金: ¥{CAPITAL:,} | 目标: ¥{TARGET_PROFIT}/笔\n")
    f.write("=" * 90 + "\n\n")

    f.write("策略排名:\n" + "-" * 90 + "\n")
    for rank, idx in enumerate(sorted_idx):
        s = all_summaries[idx]
        f.write(f"{rank+1}. {s['name']:<26s} {s['total_trades']:>3d}笔 "
                f"胜率{s['win_rate']:>5.1%} 收益¥{s['total_profit']:>9,.0f} "
                f"({s['total_return']:>5.1%}) 均持{s['avg_hold']:>4.1f}天 "
                f"评分{s['score']:>5.1f}\n")

    f.write(f"\n最优: {best_sum['name']}\n")
    f.write("-" * 60 + "\n")
    for i, t in enumerate(best_rec.trades):
        f.write(f"{i+1:>3d}. {t['buy_date'].strftime('%Y-%m-%d')} ¥{t['buy_price']:.2f} → "
                f"{t['sell_date'].strftime('%Y-%m-%d')} ¥{t['sell_price']:.2f} "
                f"{t['hold_days']:>3d}天 ¥{t['profit']:>8,.0f} ({t['profit_pct']:>5.1%})\n")

print(f"  报告已保存: {report_path}")
print("\n回测完成!")
