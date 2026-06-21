# -*- coding: utf-8 -*-
"""
中国银行(601988) 高频率波段交易策略回测 v3
核心思路：降低单笔目标到2%，用高频+高胜率弥补，全年反复操作
"""

import sys, os
os.environ['PYTHONIOENCODING'] = 'utf-8'

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

STOCK_CODE = "601988"
CAPITAL = 30000
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
SLIPPAGE = 0.001

print("=" * 90)
print(f"  中国银行({STOCK_CODE}) 高频波段策略回测 v3")
print(f"  资金¥{CAPITAL:,} | 追求高频次、高胜率、稳健复利")
print("=" * 90)

# ===================== 数据 =====================
print("\n[1/5] 获取数据...")
# 优先用 yfinance 加载预存数据
yf_path = "D:\\mycode\\601988_yf.csv"
if os.path.exists(yf_path):
    df_raw = pd.read_csv(yf_path, index_col=0, parse_dates=True)
    df_raw = df_raw.reset_index()
    # After reset_index(), the old index column may be called "Date" or "index"
    date_col = "Date" if "Date" in df_raw.columns else "index"
    df_raw = df_raw.rename(columns={
        date_col: "date",
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume"
    })
    # Drop unused yfinance columns
    for c in ["Dividends", "Stock Splits"]:
        if c in df_raw.columns:
            df_raw = df_raw.drop(columns=[c])
    # Remove timezone
    df_raw["date"] = pd.to_datetime(df_raw["date"])
    if hasattr(df_raw["date"].iloc[0], 'tz'):
        df_raw["date"] = df_raw["date"].dt.tz_localize(None)
else:
    # Fallback: try akshare
    import akshare as ak
    df_raw = ak.stock_zh_a_hist(
        symbol=STOCK_CODE, period="daily",
        start_date="20150101", end_date="20260608", adjust="qfq"
    )
    df_raw = df_raw.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume", "涨跌幅": "pct_change"
    })
df_raw["date"] = pd.to_datetime(df_raw["date"])
df_raw = df_raw.sort_values("date").reset_index(drop=True)
print(f"  数据: {df_raw['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df_raw['date'].iloc[-1].strftime('%Y-%m-%d')}")

# ===================== 指标计算 =====================
print("\n[2/5] 计算指标...")

close = df_raw["close"].values
high = df_raw["high"].values
low = df_raw["low"].values
vol = df_raw["volume"].values

# KDJ (9,3,3)
n_kdj = 9
low_n = df_raw["low"].rolling(n_kdj).min()
high_n = df_raw["high"].rolling(n_kdj).max()
rsv = (close - low_n) / (high_n - low_n) * 100

k = np.full(len(close), 50.0)
d = np.full(len(close), 50.0)
j = np.full(len(close), 50.0)
for i in range(1, len(close)):
    if pd.notna(rsv.iloc[i]):
        k[i] = 2/3 * k[i-1] + 1/3 * rsv.iloc[i]
        d[i] = 2/3 * d[i-1] + 1/3 * k[i]
        j[i] = 3 * k[i] - 2 * d[i]

df = df_raw.copy()
df["kdj_k"] = k; df["kdj_d"] = d; df["kdj_j"] = j
df["j_prev"] = df["kdj_j"].shift(1)
df["j_prev2"] = df["kdj_j"].shift(2)
df["j_delta"] = df["kdj_j"] - df["j_prev"]
df["j_delta_abs3"] = df["kdj_j"] - df["kdj_j"].shift(3)

# 均线
df["ma5"] = df["close"].rolling(5).mean()
df["ma10"] = df["close"].rolling(10).mean()
df["ma20"] = df["close"].rolling(20).mean()
df["ma60"] = df["close"].rolling(60).mean()

# 量能
df["vol_ma5"] = df["volume"].rolling(5).mean()
df["vol_ma20"] = df["volume"].rolling(20).mean()
df["vol_ratio5"] = df["volume"] / df["vol_ma5"]
df["vol_ratio20"] = df["volume"] / df["vol_ma20"]

# ATR
tr1 = df["high"] - df["low"]
tr2 = abs(df["high"] - df["close"].shift(1))
tr3 = abs(df["low"] - df["close"].shift(1))
df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
df["atr14"] = df["tr"].rolling(14).mean()
df["atr_pct"] = df["atr14"] / df["close"]

# 价格位置
df["pct_from_ma20"] = (df["close"] - df["ma20"]) / df["ma20"]
df["pct_from_ma60"] = (df["close"] - df["ma60"]) / df["ma60"]

# 涨跌
df["up_day"] = (df["close"] > df["open"]).astype(int)
df["change_1d"] = df["close"].pct_change()

df = df.dropna().reset_index(drop=True)
print(f"  有效数据: {len(df)} 条")
print(f"  日均振幅: {((df['high']-df['low'])/df['close']).mean():.2%}")
print(f"  日均涨跌: {abs(df['change_1d']).mean():.2%}")

# ===================== 回测引擎 =====================
class TradeLog:
    def __init__(self):
        self.trades = []

    def add(self, bd, bp, sd, sp, shares, rb, rs):
        cost = bp * shares * (1 + COMMISSION_RATE)
        income = sp * shares * (1 - COMMISSION_RATE - STAMP_TAX_RATE)
        profit = income - cost
        self.trades.append({
            "buy_date": bd, "buy_price": bp, "sell_date": sd, "sell_price": sp,
            "shares": shares, "profit": profit, "profit_pct": profit / cost,
            "hold_days": (sd - bd).days, "reason_b": rb, "reason_s": rs
        })

    def summary(self, name):
        if not self.trades:
            s = {"name": name, "total_trades": 0, "total_profit": 0, "final_cap": CAPITAL}
            for k in ["win_rate", "total_return", "avg_profit", "avg_hold",
                       "max_profit", "max_loss", "sharpe", "score"]:
                s[k] = 0
            return s

        ps = [t["profit"] for t in self.trades]
        ws = sum(1 for p in ps if p > 0)
        total = sum(ps)
        n = len(ps)
        wr = ws / n
        avg_p = np.mean(ps)
        avg_h = np.mean([t["hold_days"] for t in self.trades])
        std_p = np.std(ps) if n > 1 else 0
        sharpe = avg_p / (std_p + 1e-9) * np.sqrt(n) if n > 1 else 0

        # 评分：年化收益40% + 胜率25% + 交易频次15% + 盈亏比10% + 夏普10%
        years = (self.trades[-1]["buy_date"] - self.trades[0]["buy_date"]).days / 365
        cagr = (CAPITAL + total) / CAPITAL ** (1 / max(years, 0.5)) - 1 if years > 0 else 0
        # 简化评分
        score = (
            max(total / CAPITAL, -0.5) * 100 * 0.40 +
            wr * 100 * 0.25 +
            min(n / max(years, 0.5) / 24, 1) * 100 * 0.15 +  # 年均24次满分
            min(abs(avg_p) / (abs(min(ps)) + 1), 0.5) * 200 * 0.10 +  # 盈亏比
            max(sharpe, -5) * 3 * 0.10
        )

        return {
            "name": name, "total_trades": n, "win_trades": ws,
            "loss_trades": n - ws, "win_rate": wr,
            "total_profit": total, "total_return": total / CAPITAL,
            "avg_profit": avg_p, "avg_hold": avg_h,
            "max_profit": max(ps), "max_loss": min(ps),
            "sharpe": sharpe, "score": score,
            "final_cap": CAPITAL + total, "years": years,
            "cagr": (CAPITAL + total) / CAPITAL ** (1 / max(years, 0.5)) - 1
        }


def backtest(df, name, buy_fn, sell_fn):
    log = TradeLog()
    pos = False
    bp = bd = shares = 0
    bi = 0
    rb = ""

    for i in range(1, len(df)):
        if not pos:
            sig, reason = buy_fn(df, i)
            if sig:
                bp = df["close"].iloc[i] * (1 + SLIPPAGE)
                bd = df["date"].iloc[i]
                bi = i
                shares = int(CAPITAL / bp / 100) * 100
                if shares < 100:
                    continue
                pos = True
                rb = reason
        else:
            hold = (df["date"].iloc[i] - bd).days
            sig, reason = sell_fn(df, i, bp, hold)
            if sig and hold >= 1:
                sp = df["close"].iloc[i] * (1 - SLIPPAGE)
                sd = df["date"].iloc[i]
                log.add(bd, bp, sd, sp, shares, rb, reason)
                pos = False

    if pos:
        sp = df["close"].iloc[-1] * (1 - SLIPPAGE)
        log.add(bd, bp, df["date"].iloc[-1], sp, shares, rb, "强制平仓")

    return log


# ===================== 策略 =====================
print("\n[3/5] 运行策略...")

all_logs = []

# ---------- 策略1: J<0买入，止盈2.5% / 止损2% / J>85卖出 ----------
def s1():
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        if j < 0 and j > jp:  # J值负数区域拐头向上
            return True, f"J<0反弹 J={j:.0f}"
        if j < -10:  # 极度超卖
            return True, f"J极度超卖 J={j:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.02: return True, f"止损-2% J={j:.0f} PP={pp:.2%}"
        if pp >= 0.025: return True, f"止盈+2.5% J={j:.0f} PP={pp:.2%}"
        if j > 85 and j < jp and pp > 0.005: return True, f"J高位回落 J={j:.0f} PP={pp:.2%}"
        if j > 105: return True, f"J极端超买 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S1_J<0买入_2.5%止盈", buy_fn, sell_fn)
    return log, log.summary("S1_J<0买入_2.5%止盈")

all_logs.append(s1())

# ---------- 策略2: J<0买入，采用移动止盈 ----------
def s2():
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        if j < 0 and j > jp:
            return True, f"J<0反弹 J={j:.0f}"
        if j < -12:
            return True, f"J极度超卖 J={j:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.02: return True, f"止损-2% J={j:.0f} PP={pp:.2%}"
        if pp >= 0.025: return True, f"止盈+2.5% J={j:.0f} PP={pp:.2%}"
        if pp > 0.01 and j > 60 and j < jp: return True, f"移动止盈 J={j:.0f} PP={pp:.2%}"
        if j > 95: return True, f"J超买 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S2_J<0买入_移动止盈", buy_fn, sell_fn)
    return log, log.summary("S2_J<0买入_移动止盈")

all_logs.append(s2())

# ---------- 策略3: J<5买入 + MA20过滤 ----------
def s3():
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        c1 = df["close"].iloc[i] < df["ma20"].iloc[i]  # 在20日线下方低吸
        c2 = j < 5 and j > jp and c1
        c3 = j < -8 and c1
        if c2 or c3:
            return True, f"J<5+MA20下方 J={j:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.02: return True, f"止损-2% J={j:.0f} PP={pp:.2%}"
        if pp >= 0.025: return True, f"止盈+2.5% J={j:.0f} PP={pp:.2%}"
        # 回到MA20上方且盈利
        if df["close"].iloc[i] > df["ma20"].iloc[i] and pp > 0.01 and hold > 3:
            return True, f"突破MA20 J={j:.0f} PP={pp:.2%}"
        if j > 85 and j < jp and pp > 0.005: return True, f"J高位回落 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S3_J<5+MA20过滤", buy_fn, sell_fn)
    return log, log.summary("S3_J<5+MA20过滤")

all_logs.append(s3())

# ---------- 策略4: J值双底买入 ----------
def s4():
    """J值形成W底（低位二次探底）"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        jp2 = df["j_prev2"].iloc[i]
        # J值低位二次拐头 (W底)
        c1 = (jp2 < 0 and jp > jp2 and jp < 15 and j > jp)
        # 或J在0-15区间连续两天拐头
        c2 = j < 15 and j > jp and jp > jp2 and df["close"].iloc[i] > df["open"].iloc[i]
        if c1 or c2:
            return True, f"J值双底 J={j:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.02: return True, f"止损-2% J={j:.0f} PP={pp:.2%}"
        if pp >= 0.025: return True, f"止盈+2.5% J={j:.0f} PP={pp:.2%}"
        if j > 80 and j < jp and pp > 0.005: return True, f"J高位回落 J={j:.0f} PP={pp:.2%}"
        # 涨了3天赚了1.5%以上, J>50
        if hold > 3 and pp > 0.015 and j > 50: return True, f"短线收割 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S4_J值双底买入", buy_fn, sell_fn)
    return log, log.summary("S4_J值双底买入")

all_logs.append(s4())

# ---------- 策略5: J值+成交量+均线 三重过滤 ----------
def s5():
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        vr = df["vol_ratio5"].iloc[i]
        # J<10 + 放量 + MA20下方
        c1 = j < 10 and j > jp and vr > 0.8 and df["close"].iloc[i] < df["ma20"].iloc[i]
        # J<-5 + 缩量止跌
        c2 = j < -5 and vr < 1.5 and df["change_1d"].iloc[i] < 0.005
        if c1 or c2:
            return True, f"三重过滤 J={j:.0f} 量比={vr:.2f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.02: return True, f"止损-2% J={j:.0f} PP={pp:.2%}"
        if pp >= 0.02: return True, f"止盈+2% J={j:.0f} PP={pp:.2%}"
        if j > 80 and df["vol_ratio5"].iloc[i] < 0.9 and pp > 0.005:
            return True, f"J高+缩量 J={j:.0f} PP={pp:.2%}"
        if j > 100: return True, f"J极端超买 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S5_J+量+MA三重过滤", buy_fn, sell_fn)
    return log, log.summary("S5_J+量+MA三重过滤")

all_logs.append(s5())

# ---------- 策略6: 超激进高频 ----------
def s6():
    """几乎每次J<20就买，赚1.5%就跑，止损1.5%"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        # J<15买入
        if j < 15 and j > jp:
            return True, f"低吸 J={j:.0f}"
        if j < -8:
            return True, f"超卖 J={j:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.015: return True, f"止损-1.5% J={j:.0f} PP={pp:.2%}"
        if pp >= 0.02: return True, f"止盈+2% J={j:.0f} PP={pp:.2%}"
        if j > 75 and pp > 0.008: return True, f"J高位快出 J={j:.0f} PP={pp:.2%}"
        if hold >= 2 and pp > 0.012: return True, f"快进快出 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S6_超激进高频", buy_fn, sell_fn)
    return log, log.summary("S6_超激进高频")

all_logs.append(s6())

# ---------- 策略7: J值底部反转+均线多头 ----------
def s7():
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        # MA多头排列时，J<20买入
        ma_bull = df["ma5"].iloc[i] > df["ma10"].iloc[i] > df["ma20"].iloc[i]
        c1 = j < 20 and j > jp and ma_bull
        # 或MA走平时，J<0买入
        ma_flat = abs(df["pct_from_ma20"].iloc[i]) < 0.03
        c2 = j < 0 and j > jp and ma_flat
        if c1 or c2:
            return True, f"J+MA共振 J={j:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp < -0.02: return True, f"止损-2% J={j:.0f} PP={pp:.2%}"
        if pp >= 0.025: return True, f"止盈+2.5% J={j:.0f} PP={pp:.2%}"
        if j > 80 and j < jp and pp > 0.005: return True, f"J高位回落 J={j:.0f} PP={pp:.2%}"
        # MA5下穿MA10
        if (df["ma5"].iloc[i-1] >= df["ma10"].iloc[i-1] and
            df["ma5"].iloc[i] < df["ma10"].iloc[i] and pp > 0.005):
            return True, f"MA死叉 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S7_J+MA趋势共振", buy_fn, sell_fn)
    return log, log.summary("S7_J+MA趋势共振")
all_logs.append(s7())

# ---------- 策略8: 结合买入持有的混合策略 ----------
def s8():
    """J<0买入，不止损（做长线），但J>90止盈"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        if j < -5 and j > jp:
            return True, f"J<-5超卖 J={j:.0f}"
        if j < -12:
            return True, f"J极度超卖 J={j:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        if pp >= 0.025: return True, f"止盈+2.5% J={j:.0f} PP={pp:.2%}"
        if j > 90 and pp > 0.01: return True, f"J超买 J={j:.0f} PP={pp:.2%}"
        # 盈利情况下，J从高位回落
        if j > 70 and df["j_prev"].iloc[i] > 80 and pp > 0.015:
            return True, f"J回落止盈 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S8_J超卖买入_不止损", buy_fn, sell_fn)
    return log, log.summary("S8_J超卖买入_不止损")
all_logs.append(s8())

# ---------- 策略9: ATR动态止盈止损 ----------
def s9():
    """使用ATR动态调整目标"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        if j < 10 and j > jp and df["close"].iloc[i] > df["open"].iloc[i]:
            return True, f"J<10阳线 J={j:.0f}"
        if j < -5:
            return True, f"J<-5超卖 J={j:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        atr_pct = df["atr_pct"].iloc[i]
        # 动态止损：2倍ATR或最低2%
        dyn_stop = max(2 * atr_pct, 0.02)
        if pp < -dyn_stop:
            return True, f"动态止损 J={j:.0f} PP={pp:.2%} ATR={atr_pct:.2%}"
        # 动态止盈：3倍ATR或最低2.5%
        dyn_target = max(3 * atr_pct, 0.025)
        if pp >= dyn_target:
            return True, f"动态止盈 J={j:.0f} PP={pp:.2%}"
        if j > 85 and j < jp and pp > 0.005:
            return True, f"J高位回落 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S9_ATR动态止盈止损", buy_fn, sell_fn)
    return log, log.summary("S9_ATR动态止盈止损")
all_logs.append(s9())

# ---------- 策略10: 综合最优参数 ----------
def s10():
    """经过参数优化的综合策略"""
    def buy_fn(df, i):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        k_val = df["kdj_k"].iloc[i]; d_val = df["kdj_d"].iloc[i]
        # 主要信号: J<5且J拐头，且K<30
        c1 = j < 5 and j > jp and k_val < 30
        # 辅助信号: J<-10极端超卖
        c2 = j < -10
        # 辅助信号: J在低位金叉K
        c3 = jp <= df["kdj_k"].iloc[i-1] and j > k_val and j < 25
        # 辅助: 连续超卖后阳线
        c4 = (df["j_prev2"].iloc[i] < 0 and df["j_prev"].iloc[i] < 5 and
              j > jp and df["close"].iloc[i] > df["open"].iloc[i])
        if c1 or c2 or c3 or c4:
            return True, f"综合买入 J={j:.0f} K={k_val:.0f}"
        return False, ""

    def sell_fn(df, i, bp, hold):
        j = df["kdj_j"].iloc[i]; jp = df["j_prev"].iloc[i]
        k_val = df["kdj_k"].iloc[i]; d_val = df["kdj_d"].iloc[i]
        pp = (df["close"].iloc[i] - bp) / bp
        # 止损-2.5%（稍宽避免被扫）
        if pp < -0.025: return True, f"止损-2.5% J={j:.0f} PP={pp:.2%}"
        # 目标止盈+2.5%
        if pp >= 0.025: return True, f"止盈+2.5% J={j:.0f} PP={pp:.2%}"
        # J>90超买区
        if j > 95 and j < jp and pp > 0.005:
            return True, f"J超卖区回落 J={j:.0f} PP={pp:.2%}"
        # KDJ高位死叉且有盈利
        if (df["kdj_k"].iloc[i-1] >= df["kdj_d"].iloc[i-1] and
            k_val < d_val and jp > j and pp > 0.008 and hold > 3):
            return True, f"KDJ死叉止盈 J={j:.0f} PP={pp:.2%}"
        # 有1.5%以上利润+J>60+持有5天以上
        if pp > 0.015 and j > 60 and hold >= 5:
            return True, f"稳健止盈 J={j:.0f} PP={pp:.2%}"
        return False, ""

    log = backtest(df, "S10_综合优化策略", buy_fn, sell_fn)
    return log, log.summary("S10_综合优化策略")
all_logs.append(s10())

print("  各策略回测完成!")

# ===================== 对比排名 =====================
print("\n[4/5] 策略排名...\n")

summaries = [l[1] for l in all_logs]
logs = [l[0] for l in all_logs]
ranked = sorted(range(len(summaries)), key=lambda x: summaries[x]["total_profit"], reverse=True)

print("=" * 100)
print(f"{'排名':<4s} {'策略':<28s} {'笔数':>4s} {'胜率':>6s} {'总收益':>10s} {'收益率':>7s} "
      f"{'均盈':>8s} {'均持':>5s} {'最大亏':>8s} {'评分':>5s}")
print("-" * 100)
for rank, idx in enumerate(ranked):
    s = summaries[idx]
    print(f"{rank+1:<4d} {s['name']:<28s} {s['total_trades']:>4d} {s['win_rate']:>5.1%} "
          f"¥{s['total_profit']:>9,.0f} {s['total_return']:>6.1%} ¥{s['avg_profit']:>7,.0f} "
          f"{s['avg_hold']:>4.1f}天 ¥{s['max_loss']:>7,.0f} {s['score']:>5.1f}")
print("=" * 100)

# ===================== 最优策略详析 =====================
best_idx = ranked[0]
best_log = logs[best_idx]
best_sum = summaries[best_idx]

print(f"\n>>> 最优策略: {best_sum['name']}")
print(f"    总收益: ¥{best_sum['total_profit']:,.0f} ({best_sum['total_return']:.1%})")
print(f"    交易: {best_sum['total_trades']}笔 | 胜率: {best_sum['win_rate']:.1%}")
print(f"    资金: ¥{CAPITAL:,} → ¥{best_sum['final_cap']:,.0f}")
print(f"    均持: {best_sum['avg_hold']:.1f}天 | 最大单笔亏损: ¥{best_sum['max_loss']:,.0f}")

# 逐年分析
yearly = {}
for t in best_log.trades:
    y = t["buy_date"].year
    yearly.setdefault(y, {"trades": 0, "wins": 0, "profit": 0})
    yearly[y]["trades"] += 1
    yearly[y]["wins"] += 1 if t["profit"] > 0 else 0
    yearly[y]["profit"] += t["profit"]

print(f"\n--- {best_sum['name']} 逐年表现 ---")
print(f"{'年份':<8s} {'交易':>5s} {'胜率':>7s} {'年度收益':>12s} {'年化':>7s} {'累计资金':>10s}")
print("-" * 55)
cum = CAPITAL
for y in sorted(yearly.keys()):
    d = yearly[y]
    cum += d["profit"]
    print(f"{y:<8d} {d['trades']:>5d} {d['wins']/d['trades']:>6.1%} "
          f"¥{d['profit']:>11,.0f} {d['profit']/CAPITAL:>6.1%} ¥{cum:>9,.0f}")

n_years = max(1, max(yearly.keys()) - min(yearly.keys()) + 1)
print(f"\n  回测跨度: {n_years}年")
print(f"  年均交易: {best_sum['total_trades']/n_years:.1f}次")
print(f"  月均交易: {best_sum['total_trades']/max(n_years*12,1):.1f}次")
print(f"  年均收益: ¥{best_sum['total_profit']/n_years:,.0f}")

# ===================== 交易记录 =====================
print(f"\n--- {best_sum['name']} 交易记录(最近30笔) ---")
print(f"{'#':<4s} {'买入日':<12s} {'价':>6s} {'卖出日':<12s} {'价':>6s} {'持':>3s} {'盈亏':>9s} {'率':>6s} "
      f"{'买因':<35s} {'卖因':<35s}")
print("-" * 130)
for i, t in enumerate(best_log.trades[-30:]):
    idx = len(best_log.trades) - 30 + i + 1
    print(f"{idx:<4d} {t['buy_date'].strftime('%Y-%m-%d'):<12s} ¥{t['buy_price']:>5.2f} "
          f"{t['sell_date'].strftime('%Y-%m-%d'):<12s} ¥{t['sell_price']:>5.2f} "
          f"{t['hold_days']:>3d} ¥{t['profit']:>7,.0f} {t['profit_pct']:>5.1%} "
          f"{t['reason_b']:<35s} {t['reason_s']:<35s}")
print("-" * 130)

# ===================== 前3名对比 =====================
print(f"\n--- TOP3 策略对比 ---")
for r in range(min(3, len(ranked))):
    idx = ranked[r]
    s = summaries[idx]
    log = logs[idx]
    print(f"\n  [{r+1}] {s['name']}")
    print(f"      收益¥{s['total_profit']:,.0f} | 胜率{s['win_rate']:.1%} | {s['total_trades']}笔")
    # 最大回撤近似
    cumsum = np.cumsum([t["profit"] for t in log.trades])
    if len(cumsum) > 0:
        peak = np.maximum.accumulate(cumsum)
        dd = peak - cumsum
        max_dd = dd.max()
        print(f"      最大连续回撤: ¥{max_dd:,.0f} | 盈亏比: {abs(s['avg_profit']/min(s['max_loss'],-1)):.1f}")

# ===================== 可视化 =====================
print("\n[5/5] 生成图表...")

fig, axes = plt.subplots(3, 3, figsize=(22, 15))
fig.suptitle(f"中国银行({STOCK_CODE}) 高频波段策略回测 v3\n"
             f"资金¥{CAPITAL:,} | 数据{df['date'].iloc[0].strftime('%Y-%m')}~{df['date'].iloc[-1].strftime('%Y-%m')}",
             fontsize=14, fontweight="bold")

# 图1: 各策略收益
ax = axes[0, 0]
names_short = [summaries[i]["name"][:20] for i in ranked]
profits = [summaries[i]["total_profit"] for i in ranked]
colors = ["#27ae60" if p > 0 else "#e74c3c" for p in profits]
bars = ax.barh(range(len(names_short)), profits, color=colors)
ax.set_yticks(range(len(names_short)))
ax.set_yticklabels(names_short, fontsize=7)
ax.set_xlabel("总收益(元)")
ax.set_title("各策略总收益排名")
ax.axvline(0, color="black", linewidth=0.5)
for bar, p in zip(bars, profits):
    ax.text(bar.get_width() + (30 if p >= 0 else -300), bar.get_y() + bar.get_height()/2,
            f"¥{p:,.0f}", va="center", fontsize=7)

# 图2: 资金曲线(Top3)
ax = axes[0, 1]
buy_hold = CAPITAL * df["close"] / df["close"].iloc[0]
ax.plot(df["date"], buy_hold, color="gray", linewidth=0.5, alpha=0.4, label="买入持有")
colors3 = ["#e74c3c", "#3498db", "#2ecc71"]
for r in range(min(3, len(ranked))):
    idx = ranked[r]
    log = logs[idx]
    s = summaries[idx]
    if log.trades:
        dates = [df["date"].iloc[0]]
        vals = [CAPITAL]
        cur = CAPITAL
        for t in log.trades:
            dates.append(t["sell_date"])
            cur += t["profit"]
            vals.append(cur)
        ax.step(dates, vals, where="post", color=colors3[r], linewidth=1.5,
               label=f"{s['name'][:18]} ({s['total_return']:.1%})")
ax.axhline(CAPITAL, color="black", linestyle="--", linewidth=0.5)
ax.set_ylabel("资金(元)")
ax.set_title("TOP3策略资金曲线")
ax.legend(fontsize=6)
ax.grid(True, alpha=0.3)

# 图3: 最优策略每笔盈亏
ax = axes[0, 2]
if best_log.trades:
    tps = [t["profit"] for t in best_log.trades]
    nums = range(1, len(tps)+1)
    bcols = ["#27ae60" if p > 0 else "#e74c3c" for p in tps]
    ax.bar(nums, tps, color=bcols, edgecolor="white", width=0.8)
    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_xlabel("交易序号")
    ax.set_ylabel("盈亏(元)")
    ax.set_title(f"{best_sum['name'][:20]} 每笔盈亏")
    ax2 = ax.twinx()
    ax2.plot(nums, np.cumsum(tps), "b-", linewidth=1.5, alpha=0.6)
    ax2.set_ylabel("累计盈亏(元)", color="blue")

# 图4: 年度收益对比
ax = axes[1, 0]
all_years = sorted(set().union(*[{t["buy_date"].year for t in log.trades} for log in logs]))
if all_years:
    x = np.arange(len(all_years))
    w = 0.08
    colors10 = plt.cm.tab10(np.linspace(0, 1, 10))
    for j, log in enumerate(logs):
        yd = {}
        for t in log.trades:
            yd[t["buy_date"].year] = yd.get(t["buy_date"].year, 0) + t["profit"]
        yp = [yd.get(y, 0) for y in all_years]
        ax.bar(x + j*w, yp, w, label=summaries[j]["name"][:14], color=colors10[j], alpha=0.8)
    ax.set_xticks(x + w*4.5)
    ax.set_xticklabels([str(y) for y in all_years], fontsize=7)
    ax.set_ylabel("年度收益(元)")
    ax.set_title("各策略年度收益")
    ax.axhline(0, color="black", linewidth=0.5)
    ax.legend(fontsize=5, ncol=2, loc="upper left")

# 图5: J值与买卖点
ax = axes[1, 1]
n_show = min(400, len(df))
start_show = max(0, len(df) - n_show)
x_idx = range(start_show, len(df))
ax.plot(df["date"].iloc[start_show:], df["kdj_j"].iloc[start_show:], "#3498db", linewidth=0.6, label="J值")
ax.axhline(0, color="red", linestyle="--", linewidth=0.5, alpha=0.6, label="J=0")
ax.axhline(100, color="green", linestyle="--", linewidth=0.5, alpha=0.6, label="J=100")
# 买卖点
for t in best_log.trades:
    bd = t["buy_date"]
    sd = t["sell_date"]
    if bd in df["date"].values:
        idx_b = df[df["date"] == bd].index[0]
        if start_show <= idx_b < len(df):
            ax.scatter(bd, df["kdj_j"].iloc[idx_b], marker="^", color="red", s=40, zorder=5)
    if sd in df["date"].values:
        idx_s = df[df["date"] == sd].index[0]
        if start_show <= idx_s < len(df):
            ax.scatter(sd, df["kdj_j"].iloc[idx_s], marker="v", color="green", s=40, zorder=5)
ax.set_ylabel("J值")
ax.set_title(f"{best_sum['name'][:20]} 买卖信号(▲买 ▼卖)")
ax.legend(fontsize=7)
ax.grid(True, alpha=0.3)
ax.tick_params(axis='x', rotation=30)

# 图6: 胜率vs收益散点
ax = axes[1, 2]
for i, s in enumerate(summaries):
    ax.scatter(s["win_rate"]*100, s["total_return"]*100, s=s["total_trades"]*15,
              alpha=0.7, label=s["name"][:12])
    ax.annotate(s["name"][:10], (s["win_rate"]*100, s["total_return"]*100),
               fontsize=5, xytext=(2, 2), textcoords="offset points")
ax.set_xlabel("胜率(%)")
ax.set_ylabel("总收益率(%)")
ax.set_title("胜率vs收益率 (气泡=交易次数)")
ax.axhline(0, color="black", linewidth=0.5)
ax.grid(True, alpha=0.3)

# 图7: 历年累计收益
ax = axes[2, 0]
for r in range(min(5, len(ranked))):
    idx = ranked[r]
    log = logs[idx]
    s = summaries[idx]
    if log.trades:
        dates_cum = [log.trades[0]["buy_date"]]
        vals_cum = [0]
        for t in log.trades:
            dates_cum.append(t["sell_date"])
            vals_cum.append(vals_cum[-1] + t["profit"])
        ax.plot(dates_cum, vals_cum, linewidth=1.5, label=s["name"][:18], color=colors10[r])
ax.axhline(0, color="black", linewidth=0.5)
ax.set_ylabel("累计收益(元)")
ax.set_title("TOP5策略累计收益曲线")
ax.legend(fontsize=6)
ax.grid(True, alpha=0.3)

# 图8: 月收益分布
ax = axes[2, 1]
monthly_profits = {}
for y in sorted(yearly.keys()):
    for m in range(1, 13):
        mp = sum(t["profit"] for t in best_log.trades
                if t["buy_date"].year == y and t["buy_date"].month == m)
        monthly_profits.setdefault(m, []).append(mp)

months = list(range(1, 13))
avg_monthly = [np.mean(monthly_profits.get(m, [0])) for m in months]
bar_colors_m = ["#27ae60" if p > 0 else "#e74c3c" for p in avg_monthly]
ax.bar(months, avg_monthly, color=bar_colors_m, edgecolor="white")
ax.axhline(0, color="black", linewidth=0.5)
ax.set_xlabel("月份")
ax.set_ylabel("月均收益(元)")
ax.set_title(f"{best_sum['name'][:20]} 各月平均收益")
ax.set_xticks(months)

# 图9: 持仓天数分布
ax = axes[2, 2]
if best_log.trades:
    hold_days = [t["hold_days"] for t in best_log.trades]
    ax.hist(hold_days, bins=20, color="#3498db", edgecolor="white", alpha=0.8)
    ax.axvline(np.mean(hold_days), color="red", linestyle="--", linewidth=1, label=f"均值{np.mean(hold_days):.1f}天")
    ax.set_xlabel("持仓天数")
    ax.set_ylabel("频次")
    ax.set_title("持仓天数分布")
    ax.legend(fontsize=8)

plt.tight_layout()
chart_path = "D:\\mycode\\backtest_601988_v3_chart.png"
plt.savefig(chart_path, dpi=150, bbox_inches="tight")
print(f"  图表已保存: {chart_path}")

# ===================== 最终推荐 =====================
print("\n" + "=" * 90)
print("                       最终推荐")
print("=" * 90)

# 综合分析
best = best_sum
# 检查是否有收益不错且交易频次更高的
high_freq_candidates = [(i, s) for i, s in enumerate(summaries)
                         if s["total_profit"] > 0 and s["total_trades"] > best["total_trades"] * 1.5]
best_freq = None
if high_freq_candidates:
    best_freq = max(high_freq_candidates, key=lambda x: x[1]["total_profit"])

print(f"""
  [首选策略] {best['name']}
    - 总收益: ¥{best['total_profit']:,.0f} ({best['total_return']:.1%})
    - {best['total_trades']}笔交易 | 胜率{best['win_rate']:.1%} | 均持{best['avg_hold']:.1f}天
    - 资金: ¥{CAPITAL:,} → ¥{best['final_cap']:,.0f}
    - 年均{best['total_trades']/n_years:.1f}次 | 月均{best['total_trades']/max(n_years*12,1):.1f}次
""")

if best_freq:
    bf = best_freq[1]
    print(f"""  [高频备选] {bf['name']}
    - 总收益: ¥{bf['total_profit']:,.0f} ({bf['total_return']:.1%})
    - {bf['total_trades']}笔 | 胜率{bf['win_rate']:.1%} | 均持{bf['avg_hold']:.1f}天
""")

# 实际建议
print("  [实际建议]")
print("    中国银行日振幅通常0.5%-1.5%，抓2%+的波段需要足够的耐心。")
print("    J值<0买入是相对可靠的低点信号，但银行股可能长期在低位横盘。")
print("    建议：J<0时买入，目标2%-3%止盈，2%止损，不贪不惧。")
print("    银行股适合「买了等」而非「追涨杀跌」，股息也是额外收益。")

# 保存
report_path = "D:\\mycode\\backtest_601988_v3_report.txt"
with open(report_path, "w", encoding="utf-8") as f:
    f.write(f"中国银行({STOCK_CODE}) 高频波段策略回测报告 v3\n")
    f.write(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    f.write(f"数据: {df['date'].iloc[0].strftime('%Y-%m-%d')}~{df['date'].iloc[-1].strftime('%Y-%m-%d')}\n")
    f.write(f"资金: ¥{CAPITAL:,}\n\n")

    f.write("策略排名:\n" + "-"*90 + "\n")
    for rank, idx in enumerate(ranked):
        s = summaries[idx]
        f.write(f"{rank+1}. {s['name']:<28s} {s['total_trades']:>4d}笔 胜率{s['win_rate']:>5.1%} "
                f"¥{s['total_profit']:>9,.0f}({s['total_return']:>5.1%}) 均持{s['avg_hold']:>4.1f}天\n")

    f.write(f"\n最优策略: {best['name']}\n")
    f.write(f"总收益: ¥{best['total_profit']:,.0f}\n")
    f.write(f"交易: {best['total_trades']}笔 | 胜率{best['win_rate']:.1%} | 均持{best['avg_hold']:.1f}天\n")
    f.write("\n交易记录:\n" + "-"*110 + "\n")
    for i, t in enumerate(best_log.trades):
        f.write(f"{i+1:>3d}. {t['buy_date'].strftime('%Y-%m-%d')} ¥{t['buy_price']:.2f} → "
                f"{t['sell_date'].strftime('%Y-%m-%d')} ¥{t['sell_price']:.2f} "
                f"{t['hold_days']:>3d}天 ¥{t['profit']:>8,.0f}({t['profit_pct']:>5.1%})\n")

print(f"  报告已保存: {report_path}")
print("\n回测完成!")
