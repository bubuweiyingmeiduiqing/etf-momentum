# -*- coding: utf-8 -*-
"""
中国银行(601988) 波段交易策略回测系统
目标：3万元全仓进出，每笔盈利1000左右（约3.3%收益率），全年波段操作
"""

import sys
import os
# Fix Windows/PowerShell GBK encoding
os.environ['PYTHONIOENCODING'] = 'utf-8'

import akshare as ak
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # 非交互式后端
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ===================== 参数配置 =====================
STOCK_CODE = "601988"
CAPITAL = 30000          # 初始资金
TARGET_PROFIT = 1000     # 目标每笔盈利
TARGET_PROFIT_PCT = TARGET_PROFIT / CAPITAL  # 约3.33%
COMMISSION_RATE = 0.0003  # 佣金万3
STAMP_TAX_RATE = 0.001   # 印花税千1 (仅卖出)
MIN_HOLD_DAYS = 1        # 最少持有天数 (T+1)
SLIPPAGE = 0.001         # 滑点千1

print("=" * 80)
print(f"  中国银行({STOCK_CODE}) 波段交易策略回测系统")
print(f"  初始资金: {CAPITAL:,}元 | 目标每笔盈利: {TARGET_PROFIT}元 (~{TARGET_PROFIT_PCT:.1%})")
print("=" * 80)

# ===================== 数据获取 =====================
print("\n[1/6] 正在获取历史数据...")

# 获取日线数据 (复权)
try:
    # akshare 获取A股日线数据
    df_raw = ak.stock_zh_a_hist(
        symbol=STOCK_CODE,
        period="daily",
        start_date="20190101",
        end_date="20260608",
        adjust="qfq"  # 前复权
    )
    print(f"  ✓ 成功获取 {len(df_raw)} 条日线数据")
except Exception as e:
    print(f"  akshare stock_zh_a_hist 失败: {e}")
    print("  尝试备用接口...")
    try:
        df_raw = ak.stock_zh_a_daily(
            symbol=f"sh{STOCK_CODE}",
            adjust="qfq"
        )
        print(f"  ✓ 备用接口获取 {len(df_raw)} 条数据")
    except Exception as e2:
        print(f"  备用接口也失败: {e2}")
        raise

# 标准化列名
df_raw = df_raw.rename(columns={
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct_change",
    "涨跌额": "change",
    "换手率": "turnover"
})

df_raw["date"] = pd.to_datetime(df_raw["date"])
df_raw = df_raw.sort_values("date").reset_index(drop=True)

# 确保有必要的列
for col in ["open", "close", "high", "low", "volume"]:
    if col not in df_raw.columns:
        raise ValueError(f"缺少必要列: {col}")

print(f"  数据范围: {df_raw['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df_raw['date'].iloc[-1].strftime('%Y-%m-%d')}")
print(f"  价格范围: ¥{df_raw['close'].min():.2f} ~ ¥{df_raw['close'].max():.2f}")

# ===================== 指标计算 =====================
print("\n[2/6] 计算技术指标...")

def calc_indicators(df):
    """计算所有技术指标"""
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    # --- 均线 ---
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma10"] = df["close"].rolling(10).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma120"] = df["close"].rolling(120).mean()

    # --- 布林带 (20,2) ---
    df["bb_mid"] = df["ma20"]
    df["bb_std"] = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]  # 带宽
    # 价格在布林带中的位置 (0=下轨, 1=上轨)
    df["bb_position"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])

    # --- RSI (14) ---
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # --- MACD ---
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd_dif"] = ema12 - ema26
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = 2 * (df["macd_dif"] - df["macd_dea"])

    # --- KDJ ---
    n = 9
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n) * 100
    df["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
    df["kdj_d"] = df["kdj_k"].ewm(com=2, adjust=False).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

    # --- ATR (14) ---
    tr1 = df["high"] - df["low"]
    tr2 = abs(df["high"] - df["close"].shift(1))
    tr3 = abs(df["low"] - df["close"].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_pct"] = df["atr"] / df["close"]  # ATR百分比

    # --- 成交量均线 ---
    df["vol_ma5"] = df["volume"].rolling(5).mean()
    df["vol_ma10"] = df["volume"].rolling(10).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma10"]

    # --- 价格通道 ---
    df["hh_20"] = df["high"].rolling(20).max()
    df["ll_20"] = df["low"].rolling(20).min()

    # --- 趋势强度 ---
    df["trend"] = df["ma20"].pct_change(5)  # MA20的5日变化率 判断趋势方向

    return df

df = calc_indicators(df_raw)

# 去除NaN行(前120行)
df = df.dropna().reset_index(drop=True)
print(f"  ✓ 指标计算完成，有效数据 {len(df)} 条")

# ===================== 策略定义 =====================
print("\n[3/6] 定义交易策略...")

class StrategyResult:
    def __init__(self, name):
        self.name = name
        self.trades = []
        self.signals = []  # (date, signal_type, price, reason)

    def add_signal(self, date, signal, price, reason=""):
        self.signals.append({
            "date": date, "signal": signal, "price": price, "reason": reason
        })

    def add_trade(self, buy_date, buy_price, sell_date, sell_price, shares, reason_buy, reason_sell):
        buy_cost = buy_price * shares * (1 + COMMISSION_RATE)
        sell_income = sell_price * shares * (1 - COMMISSION_RATE - STAMP_TAX_RATE)
        profit = sell_income - buy_cost
        profit_pct = profit / buy_cost
        hold_days = (sell_date - buy_date).days

        self.trades.append({
            "buy_date": buy_date,
            "buy_price": buy_price,
            "sell_date": sell_date,
            "sell_price": sell_price,
            "shares": shares,
            "buy_cost": buy_cost,
            "sell_income": sell_income,
            "profit": profit,
            "profit_pct": profit_pct,
            "hold_days": hold_days,
            "reason_buy": reason_buy,
            "reason_sell": reason_sell
        })

    def summary(self):
        if not self.trades:
            return {
                "name": self.name,
                "total_trades": 0,
                "win_rate": 0,
                "total_profit": 0,
                "total_return": 0,
                "avg_profit": 0,
                "avg_hold_days": 0,
                "max_profit": 0,
                "max_loss": 0,
                "sharpe_like": 0,
                "score": 0
            }

        profits = [t["profit"] for t in self.trades]
        win_trades = [p for p in profits if p > 0]
        loss_trades = [p for p in profits if p < 0]
        total_profit = sum(profits)
        total_return = total_profit / CAPITAL
        win_rate = len(win_trades) / len(profits)
        avg_profit = np.mean(profits)
        avg_hold = np.mean([t["hold_days"] for t in self.trades])
        max_profit = max(profits) if profits else 0
        max_loss = min(profits) if profits else 0

        # 类夏普比率 (考虑手续费后)
        if len(profits) > 1:
            sharpe_like = np.mean(profits) / (np.std(profits) + 1e-9) * np.sqrt(len(profits))
        else:
            sharpe_like = 0

        # 综合评分：
        # 考虑：总收益(40%) + 胜率(25%) + 平均盈利(15%) + 类夏普(10%) + 交易次数(10%)
        score = (
            total_return * 100 * 0.40 +
            win_rate * 100 * 0.25 +
            (avg_profit / CAPITAL * 100) * 0.15 +
            sharpe_like * 0.10 +
            min(len(profits) / 12, 1) * 100 * 0.10  # 年化12次以上满分
        )

        return {
            "name": self.name,
            "total_trades": len(self.trades),
            "win_trades": len(win_trades),
            "loss_trades": len(loss_trades),
            "win_rate": win_rate,
            "total_profit": total_profit,
            "total_return": total_return,
            "avg_profit": avg_profit,
            "avg_profit_pct": avg_profit / CAPITAL,
            "avg_hold_days": avg_hold,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "sharpe_like": sharpe_like,
            "score": score,
            "final_capital": CAPITAL + total_profit,
            "profit_std": np.std(profits) if len(profits) > 1 else 0
        }


def backtest_strategy(df, strategy_name, buy_signal, sell_signal, buy_reason_fn, sell_reason_fn):
    """
    通用回测引擎
    buy_signal(df, i) -> bool
    sell_signal(df, i, buy_price, hold_days) -> bool
    """
    result = StrategyResult(strategy_name)
    in_position = False
    buy_price = 0
    buy_date = None
    buy_idx = 0
    shares = 0

    for i in range(1, len(df)):
        if not in_position:
            # 寻找买入信号
            if buy_signal(df, i):
                buy_price = df["close"].iloc[i] * (1 + SLIPPAGE)  # 考虑滑点
                buy_date = df["date"].iloc[i]
                buy_idx = i
                shares = int(CAPITAL / buy_price / 100) * 100  # 整手
                if shares < 100:
                    continue  # 不够买一手
                in_position = True
                reason = buy_reason_fn(df, i)
                result.add_signal(buy_date, "BUY", buy_price, reason)
        else:
            # 寻找卖出信号
            hold_days = (df["date"].iloc[i] - buy_date).days
            if sell_signal(df, i, buy_price, hold_days) and hold_days >= MIN_HOLD_DAYS:
                sell_price = df["close"].iloc[i] * (1 - SLIPPAGE)
                sell_date = df["date"].iloc[i]
                reason = sell_reason_fn(df, i, buy_price, sell_price)
                result.add_signal(sell_date, "SELL", sell_price, reason)
                result.add_trade(buy_date, buy_price, sell_date, sell_price,
                                shares, result.signals[-2]["reason"], reason)
                in_position = False

    # 如果最后一天仍持仓，强制平仓
    if in_position:
        sell_price = df["close"].iloc[-1] * (1 - SLIPPAGE)
        sell_date = df["date"].iloc[-1]
        result.add_signal(sell_date, "FORCE_SELL", sell_price, "回测结束强制平仓")
        result.add_trade(buy_date, buy_price, sell_date, sell_price,
                        shares, result.signals[-2]["reason"], "强制平仓")

    return result


# ---------- 策略1: 布林带 ----------
def strat_bollinger(df):
    def buy_sig(df, i):
        # 买入：价格从下轨下方突破到下轨上方，且RSI<40（超卖反弹）
        c1 = df["close"].iloc[i-1] <= df["bb_lower"].iloc[i-1]  # 前一日在下轨下方
        c2 = df["close"].iloc[i] > df["bb_lower"].iloc[i]       # 今日突破下轨
        c3 = df["rsi"].iloc[i] < 40
        return c1 and c2 and c3

    def sell_sig(df, i, buy_price, hold_days):
        profit_pct = (df["close"].iloc[i] - buy_price) / buy_price
        # 卖出：触及上轨 或 盈利达标 或 回落到中轨下方
        c1 = df["close"].iloc[i] >= df["bb_upper"].iloc[i] * 0.98  # 接近上轨
        c2 = profit_pct >= TARGET_PROFIT_PCT  # 达到目标盈利
        c3 = df["close"].iloc[i] < df["bb_mid"].iloc[i] and profit_pct > 0.005  # 有盈利跌回中轨
        c4 = profit_pct < -0.03  # 止损3%
        return c1 or c2 or c3 or c4

    def buy_reason(df, i):
        return f"布林下轨反弹 RSI={df['rsi'].iloc[i]:.0f} BB_pos={df['bb_position'].iloc[i]:.2f}"

    def sell_reason(df, i, bp, sp):
        pp = (sp - bp) / bp
        return f"布林{'上轨' if df['close'].iloc[i] >= df['bb_upper'].iloc[i]*0.98 else '目标止盈' if pp >= TARGET_PROFIT_PCT else '止损'} PP={pp:.2%}"

    return backtest_strategy(df, "布林带策略 (Bollinger)", buy_sig, sell_sig, buy_reason, sell_reason)


# ---------- 策略2: RSI ----------
def strat_rsi(df):
    def buy_sig(df, i):
        # 买入：RSI从30下方回升到30上方
        c1 = df["rsi"].iloc[i-1] < 30
        c2 = df["rsi"].iloc[i] >= 30
        c3 = df["close"].iloc[i] > df["close"].iloc[i-1]  # 当天收阳
        return c1 and c2 and c3

    def sell_sig(df, i, buy_price, hold_days):
        profit_pct = (df["close"].iloc[i] - buy_price) / buy_price
        # 卖出：RSI>70超买 或 盈利达标 或 止损
        c1 = df["rsi"].iloc[i] > 70
        c2 = profit_pct >= TARGET_PROFIT_PCT
        c3 = profit_pct < -0.03
        return c1 or c2 or c3

    def buy_reason(df, i):
        return f"RSI超卖反弹 RSI={df['rsi'].iloc[i]:.0f}"

    def sell_reason(df, i, bp, sp):
        pp = (sp - bp) / bp
        return f"RSI{'超买' if df['rsi'].iloc[i] > 70 else '目标止盈' if pp >= TARGET_PROFIT_PCT else '止损'} RSI={df['rsi'].iloc[i]:.0f} PP={pp:.2%}"

    return backtest_strategy(df, "RSI策略", buy_sig, sell_sig, buy_reason, sell_reason)


# ---------- 策略3: MACD ----------
def strat_macd(df):
    def buy_sig(df, i):
        # 买入：MACD金叉 (DIF上穿DEA)，且在零轴下方更好
        c1 = df["macd_dif"].iloc[i-1] <= df["macd_dea"].iloc[i-1]
        c2 = df["macd_dif"].iloc[i] > df["macd_dea"].iloc[i]
        c3 = df["close"].iloc[i] > df["ma20"].iloc[i]  # 站上20日线确认
        return c1 and c2 and c3

    def sell_sig(df, i, buy_price, hold_days):
        profit_pct = (df["close"].iloc[i] - buy_price) / buy_price
        # 卖出：MACD死叉 或 盈利达标 或 止损
        c1 = df["macd_dif"].iloc[i-1] >= df["macd_dea"].iloc[i-1] and df["macd_dif"].iloc[i] < df["macd_dea"].iloc[i]
        c2 = profit_pct >= TARGET_PROFIT_PCT
        c3 = profit_pct < -0.03
        c4 = df["macd_hist"].iloc[i] < df["macd_hist"].iloc[i-1] and profit_pct > 0.01 and hold_days > 3
        return c1 or c2 or c3 or c4

    def buy_reason(df, i):
        return f"MACD金叉 DIF={df['macd_dif'].iloc[i]:.3f} DEA={df['macd_dea'].iloc[i]:.3f}"

    def sell_reason(df, i, bp, sp):
        pp = (sp - bp) / bp
        return f"MACD{'死叉' if df['macd_dif'].iloc[i] < df['macd_dea'].iloc[i] else '目标止盈' if pp >= TARGET_PROFIT_PCT else '柱缩'} PP={pp:.2%}"

    return backtest_strategy(df, "MACD策略", buy_sig, sell_sig, buy_reason, sell_reason)


# ---------- 策略4: KDJ ----------
def strat_kdj(df):
    def buy_sig(df, i):
        # 买入：KDJ的J值低于0后回升，且K上穿D
        c1 = df["kdj_j"].iloc[i-1] < 0
        c2 = df["kdj_j"].iloc[i] > df["kdj_j"].iloc[i-1]
        c3 = df["kdj_k"].iloc[i] > df["kdj_d"].iloc[i]
        c4 = df["close"].iloc[i] > df["close"].iloc[i-1]
        return (c1 and c2 and c4) or (c3 and df["kdj_k"].iloc[i] < 30 and c4)

    def sell_sig(df, i, buy_price, hold_days):
        profit_pct = (df["close"].iloc[i] - buy_price) / buy_price
        # 卖出：KDJ高位死叉 或 盈利达标 或 止损
        c1 = df["kdj_k"].iloc[i-1] >= df["kdj_d"].iloc[i-1] and df["kdj_k"].iloc[i] < df["kdj_d"].iloc[i] and profit_pct > 0
        c2 = profit_pct >= TARGET_PROFIT_PCT
        c3 = profit_pct < -0.03
        return c1 or c2 or c3

    def buy_reason(df, i):
        return f"KDJ超卖反弹 K={df['kdj_k'].iloc[i]:.0f} D={df['kdj_d'].iloc[i]:.0f} J={df['kdj_j'].iloc[i]:.0f}"

    def sell_reason(df, i, bp, sp):
        pp = (sp - bp) / bp
        return f"KDJ{'死叉' if df['kdj_k'].iloc[i] < df['kdj_d'].iloc[i] else '目标止盈' if pp >= TARGET_PROFIT_PCT else '止损'} PP={pp:.2%}"

    return backtest_strategy(df, "KDJ策略", buy_sig, sell_sig, buy_reason, sell_reason)


# ---------- 策略5: 均线金叉 ----------
def strat_ma_cross(df):
    def buy_sig(df, i):
        # 买入：MA5上穿MA20，且MA20向上（多头趋势）
        c1 = df["ma5"].iloc[i-1] <= df["ma20"].iloc[i-1]
        c2 = df["ma5"].iloc[i] > df["ma20"].iloc[i]
        c3 = df["ma20"].iloc[i] > df["ma20"].iloc[i-3]  # MA20上翘
        c4 = df["volume"].iloc[i] > df["vol_ma5"].iloc[i] * 0.8  # 成交量不能太萎缩
        return c1 and c2 and c3 and c4

    def sell_sig(df, i, buy_price, hold_days):
        profit_pct = (df["close"].iloc[i] - buy_price) / buy_price
        # 卖出：MA5下穿MA10 或 盈利达标 或 止损
        c1 = df["ma5"].iloc[i-1] >= df["ma10"].iloc[i-1] and df["ma5"].iloc[i] < df["ma10"].iloc[i] and profit_pct > 0
        c2 = profit_pct >= TARGET_PROFIT_PCT
        c3 = profit_pct < -0.03
        c4 = df["close"].iloc[i] < df["ma20"].iloc[i] and profit_pct > 0.01 and hold_days > 5
        return c1 or c2 or c3 or c4

    def buy_reason(df, i):
        return f"均线金叉 MA5={df['ma5'].iloc[i]:.2f} MA20={df['ma20'].iloc[i]:.2f}"

    def sell_reason(df, i, bp, sp):
        pp = (sp - bp) / bp
        return f"均线{'死叉' if df['ma5'].iloc[i] < df['ma10'].iloc[i] else '目标止盈' if pp >= TARGET_PROFIT_PCT else '止损'} PP={pp:.2%}"

    return backtest_strategy(df, "均线金叉策略 (MA)", buy_sig, sell_sig, buy_reason, sell_reason)


# ---------- 策略6: 多指标共振(增强版) ----------
def strat_combined(df):
    def buy_sig(df, i):
        score = 0
        # RSI超卖
        if df["rsi"].iloc[i] < 35:
            score += 2
        elif df["rsi"].iloc[i] < 45:
            score += 1
        # 布林带位置
        if df["bb_position"].iloc[i] < 0.15:
            score += 2
        elif df["bb_position"].iloc[i] < 0.3:
            score += 1
        # KDJ超卖
        if df["kdj_j"].iloc[i] < 0:
            score += 2
        elif df["kdj_j"].iloc[i] < 20:
            score += 1
        # MACD金叉
        if df["macd_dif"].iloc[i] > df["macd_dea"].iloc[i] and df["macd_dif"].iloc[i-1] <= df["macd_dea"].iloc[i-1]:
            score += 2
        # 价格在MA20下方(低吸)
        if df["close"].iloc[i] < df["ma20"].iloc[i]:
            score += 1
        # 当天收阳线
        if df["close"].iloc[i] > df["open"].iloc[i]:
            score += 1
        # 放量
        if df["vol_ratio"].iloc[i] > 1.2:
            score += 1

        return score >= 5

    def sell_sig(df, i, buy_price, hold_days):
        profit_pct = (df["close"].iloc[i] - buy_price) / buy_price

        # 强制止损
        if profit_pct < -0.03:
            return True

        # 达到目标盈利
        if profit_pct >= TARGET_PROFIT_PCT:
            return True

        # 多指标共振卖出
        sell_score = 0
        if df["rsi"].iloc[i] > 65:
            sell_score += 2
        if df["bb_position"].iloc[i] > 0.85:
            sell_score += 2
        if df["kdj_j"].iloc[i] > 90:
            sell_score += 2
        if df["macd_dif"].iloc[i-1] >= df["macd_dea"].iloc[i-1] and df["macd_dif"].iloc[i] < df["macd_dea"].iloc[i]:
            sell_score += 2
        if df["close"].iloc[i] > df["ma5"].iloc[i] * 1.03:
            sell_score += 1

        return sell_score >= 4

    def buy_reason(df, i):
        return f"多指标共振买入 RSI={df['rsi'].iloc[i]:.0f} BB={df['bb_position'].iloc[i]:.2f} KDJ_J={df['kdj_j'].iloc[i]:.0f}"

    def sell_reason(df, i, bp, sp):
        pp = (sp - bp) / bp
        return f"多指标共振卖出 PP={pp:.2%} RSI={df['rsi'].iloc[i]:.0f}"

    return backtest_strategy(df, "多指标共振策略 (综合)", buy_sig, sell_sig, buy_reason, sell_reason)


# ---------- 策略7: 布林带+KDJ ----------
def strat_bollinger_kdj(df):
    def buy_sig(df, i):
        # 布林带下轨附近 + KDJ超卖
        c1 = df["bb_position"].iloc[i] < 0.2  # 在下轨附近
        c2 = df["kdj_j"].iloc[i] < 30
        c3 = df["close"].iloc[i] > df["close"].iloc[i-1]  # 当天收阳
        return c1 and c2 and c3

    def sell_sig(df, i, buy_price, hold_days):
        profit_pct = (df["close"].iloc[i] - buy_price) / buy_price
        c1 = df["bb_position"].iloc[i] > 0.8 and df["kdj_j"].iloc[i] > 70
        c2 = profit_pct >= TARGET_PROFIT_PCT
        c3 = profit_pct < -0.03
        return c1 or c2 or c3

    def buy_reason(df, i):
        return f"BB下轨+KDJ超卖 BB={df['bb_position'].iloc[i]:.2f} J={df['kdj_j'].iloc[i]:.0f}"

    def sell_reason(df, i, bp, sp):
        pp = (sp - bp) / bp
        return f"BB上轨+KDJ超买 PP={pp:.2%}"

    return backtest_strategy(df, "布林+KDJ策略", buy_sig, sell_sig, buy_reason, sell_reason)


# ===================== 运行回测 =====================
print("\n[4/6] 运行回测...")

strategies = [
    strat_bollinger,
    strat_rsi,
    strat_macd,
    strat_kdj,
    strat_ma_cross,
    strat_bollinger_kdj,
    strat_combined
]

results = []
for strat_fn in strategies:
    result = strat_fn(df)
    summary = result.summary()
    results.append((result, summary))
    print(f"  ✓ {summary['name']}: {summary['total_trades']}笔交易, "
          f"胜率{summary['win_rate']:.1%}, 总收益¥{summary['total_profit']:,.0f}")

# ===================== 结果对比 =====================
print("\n[5/6] 生成对比报告...")

print("\n" + "=" * 90)
print("                        策略对比总览")
print("=" * 90)

# 按收益率排序
results_sorted = sorted(results, key=lambda x: x[1]["total_return"], reverse=True)

header = f"{'策略名称':<24s} {'交易笔数':>6s} {'胜率':>7s} {'总收益':>10s} {'收益率':>7s} {'平均盈利':>10s} {'均持天数':>7s} {'评分':>6s}"
print(header)
print("-" * 90)

for _, s in results_sorted:
    print(f"{s['name']:<24s} {s['total_trades']:>6d} {s['win_rate']:>6.1%} "
          f"¥{s['total_profit']:>9,.0f} {s['total_return']:>6.1%} "
          f"¥{s['avg_profit']:>9,.0f} {s['avg_hold_days']:>6.1f}天 {s['score']:>5.1f}")

print("=" * 90)

# 选出最优
best_result, best_summary = results_sorted[0]
print(f"\n  🏆 最优策略: {best_summary['name']}")
print(f"    总收益: ¥{best_summary['total_profit']:,.0f} "
      f"({best_summary['total_return']:.1%})")
print(f"    交易笔数: {best_summary['total_trades']}")
print(f"    胜率: {best_summary['win_rate']:.1%}")
print(f"    最终资金: ¥{best_summary['final_capital']:,.0f}")

# ===================== 逐年分析 =====================
print("\n[6/6] 逐年分析...")

def yearly_analysis(trades):
    """按年统计"""
    if not trades:
        return pd.DataFrame()
    annual = {}
    for t in trades:
        year = t["buy_date"].year
        if year not in annual:
            annual[year] = {"trades": 0, "wins": 0, "profit": 0}
        annual[year]["trades"] += 1
        annual[year]["wins"] += 1 if t["profit"] > 0 else 0
        annual[year]["profit"] += t["profit"]

    rows = []
    for year in sorted(annual.keys()):
        d = annual[year]
        rows.append({
            "年份": year,
            "交易次数": d["trades"],
            "盈利次数": d["wins"],
            "胜率": f"{d['wins']/d['trades']:.1%}" if d["trades"] > 0 else "N/A",
            "年度收益": f"¥{d['profit']:,.0f}",
            "年化收益": f"{d['profit']/CAPITAL:.1%}"
        })
    return pd.DataFrame(rows)

print(f"\n--- {best_summary['name']} 逐年表现 ---")
ya = yearly_analysis(best_result.trades)
if not ya.empty:
    print(ya.to_string(index=False))
else:
    print("  无交易记录")

# ===================== 详细交易记录 =====================
print(f"\n--- {best_summary['name']} 完整交易记录 ---")
print(f"{'序号':<5s} {'买入日期':<12s} {'买入价':>7s} {'卖出日期':<12s} {'卖出价':>7s} {'持有天数':>7s} {'盈亏':>10s} {'收益率':>7s} {'买入理由':>25s} {'卖出理由':>25s}")
print("-" * 130)

for i, t in enumerate(best_result.trades):
    print(f"{i+1:<5d} {t['buy_date'].strftime('%Y-%m-%d'):<12s} ¥{t['buy_price']:>6.2f} "
          f"{t['sell_date'].strftime('%Y-%m-%d'):<12s} ¥{t['sell_price']:>6.2f} "
          f"{t['hold_days']:>5d}天 ¥{t['profit']:>8,.0f} {t['profit_pct']:>6.1%} "
          f"{t['reason_buy']:<25s} {t['reason_sell']:<25s}")
print("-" * 130)

# ===================== 图表输出 =====================
print("\n生成可视化图表...")

fig, axes = plt.subplots(3, 2, figsize=(18, 14))
fig.suptitle(f"中国银行({STOCK_CODE}) 策略回测对比\n"
             f"初始资金: ¥{CAPITAL:,} | 目标盈利: ¥{TARGET_PROFIT} | "
             f"数据: {df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}",
             fontsize=13, fontweight="bold")

# 图1: 各策略收益对比柱状图
ax1 = axes[0, 0]
names = [s["name"] for _, s in results_sorted]
profits = [s["total_profit"] for _, s in results_sorted]
colors = ["#2ecc71" if p > 0 else "#e74c3c" for p in profits]
bars = ax1.barh(range(len(names)), profits, color=colors, edgecolor="white")
ax1.set_yticks(range(len(names)))
ax1.set_yticklabels([n[:15] for n in names], fontsize=9)
ax1.set_xlabel("总收益 (元)")
ax1.set_title("各策略总收益对比")
ax1.axvline(0, color="black", linewidth=0.5)
for i, (bar, p) in enumerate(zip(bars, profits)):
    ax1.text(bar.get_width() + 50, bar.get_y() + bar.get_height()/2,
             f"¥{p:,.0f}", va="center", fontsize=8)

# 图2: 胜率对比
ax2 = axes[0, 1]
win_rates = [s["win_rate"]*100 for _, s in results_sorted]
bars2 = ax2.bar(range(len(names)), win_rates, color="#3498db", edgecolor="white")
ax2.set_xticks(range(len(names)))
ax2.set_xticklabels([n[:12] for n in names], rotation=30, fontsize=8)
ax2.set_ylabel("胜率 (%)")
ax2.set_title("各策略胜率对比")
ax2.axhline(50, color="red", linestyle="--", linewidth=0.8, label="50%")
for bar, wr in zip(bars2, win_rates):
    ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
             f"{wr:.1f}%", ha="center", fontsize=8)
ax2.set_ylim(0, max(win_rates) + 15)
ax2.legend(fontsize=8)

# 图3: 资金曲线 (最优策略)
ax3 = axes[1, 0]
best_trades = best_result.trades
if best_trades:
    dates = [df["date"].iloc[0]]
    values = [CAPITAL]
    current = CAPITAL
    for t in best_trades:
        dates.append(t["sell_date"])
        current += t["profit"]
        values.append(current)

    # 加上买入持有基准
    buy_hold = CAPITAL * df["close"] / df["close"].iloc[0]

    ax3.plot(df["date"], buy_hold, color="gray", linewidth=0.8, alpha=0.6, label="买入持有")
    ax3.step(dates, values, where="post", color="#e74c3c", linewidth=2, label=best_summary["name"])
    ax3.axhline(CAPITAL, color="black", linestyle="--", linewidth=0.5)
    ax3.set_ylabel("资金 (元)")
    ax3.set_title("最优策略资金曲线 vs 买入持有")
    ax3.legend(fontsize=8)
    ax3.grid(True, alpha=0.3)

# 图4: 每笔盈亏分布
ax4 = axes[1, 1]
if best_trades:
    trade_profits = [t["profit"] for t in best_trades]
    trade_nums = list(range(1, len(trade_profits) + 1))
    bar_colors = ["#2ecc71" if p > 0 else "#e74c3c" for p in trade_profits]
    ax4.bar(trade_nums, trade_profits, color=bar_colors, edgecolor="white")
    ax4.axhline(0, color="black", linewidth=0.5)
    ax4.axhline(TARGET_PROFIT, color="orange", linestyle="--", linewidth=0.8, label=f"目标{TARGET_PROFIT}元")
    ax4.set_xlabel("交易序号")
    ax4.set_ylabel("盈亏 (元)")
    ax4.set_title(f"{best_summary['name']} 每笔盈亏")
    ax4.legend(fontsize=8)
    # 添加累计盈亏线
    cumsum = np.cumsum(trade_profits)
    ax4_twin = ax4.twinx()
    ax4_twin.plot(trade_nums, cumsum, "b-o", linewidth=1.5, markersize=4, alpha=0.6)
    ax4_twin.set_ylabel("累计盈亏 (元)", color="blue")
    ax4_twin.tick_params(axis="y", colors="blue")

# 图5: 年度收益热力图风格
ax5 = axes[2, 0]
all_years_set = set()
for _, s in results_sorted:
    if s["total_trades"] > 0:
        for t in results[[r[0].name for r in results].index(s["name"])][0].trades:
            all_years_set.add(t["buy_date"].year)
all_years = sorted(all_years_set)

if all_years:
    yearly_data = {}
    for result_obj, s in results_sorted:
        yd = {}
        for t in result_obj.trades:
            y = t["buy_date"].year
            yd[y] = yd.get(y, 0) + t["profit"]
        yearly_data[s["name"]] = [yd.get(y, 0) for y in all_years]

    x = np.arange(len(all_years))
    bar_width = 0.12
    colors_list = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22"]

    for idx, (name, yprofits) in enumerate(yearly_data.items()):
        ax5.bar(x + idx * bar_width, yprofits, bar_width,
                label=name[:12], color=colors_list[idx % len(colors_list)], alpha=0.85)

    ax5.set_xticks(x + bar_width * (len(yearly_data)-1)/2)
    ax5.set_xticklabels([str(y) for y in all_years])
    ax5.set_ylabel("年度收益 (元)")
    ax5.set_title("各策略年度收益")
    ax5.axhline(0, color="black", linewidth=0.5)
    ax5.legend(fontsize=6, loc="upper left")

# 图6: 策略指标散点图 (收益率 vs 胜率)
ax6 = axes[2, 1]
for _, s in results_sorted:
    ax6.scatter(s["win_rate"]*100, s["total_return"]*100,
               s=s["total_trades"]*20, alpha=0.7, label=s["name"][:15])
    ax6.annotate(s["name"][:10],
                (s["win_rate"]*100, s["total_return"]*100),
                fontsize=7, xytext=(3, 3), textcoords="offset points")

ax6.set_xlabel("胜率 (%)")
ax6.set_ylabel("总收益率 (%)")
ax6.set_title("策略胜率 vs 收益率 (气泡大小=交易次数)")
ax6.axhline(0, color="black", linewidth=0.5)
ax6.grid(True, alpha=0.3)

plt.tight_layout()
chart_path = "D:\\mycode\\backtest_601988_chart.png"
plt.savefig(chart_path, dpi=150, bbox_inches="tight")
print(f"  ✓ 图表已保存: {chart_path}")

# ===================== 最终推荐 =====================
print("\n" + "=" * 80)
print("                      最终推荐")
print("=" * 80)
print(f"""
📊 策略: {best_summary['name']}
💰 总收益: ¥{best_summary['total_profit']:,.0f} ({best_summary['total_return']:.1%})
📈 交易次数: {best_summary['total_trades']}笔
✅ 胜率: {best_summary['win_rate']:.1%}
📐 平均盈利: ¥{best_summary['avg_profit']:,.0f}
📅 平均持仓: {best_summary['avg_hold_days']:.1f}天
💵 最终资金: ¥{best_summary['final_capital']:,.0f}
⭐ 综合评分: {best_summary['score']:.1f}

🔥 最优策略回测总结：
""")

# 打印最优策略的年度明细
print(f"--- {best_summary['name']} 年度明细 ---")
ya_best = yearly_analysis(best_result.trades)
if not ya_best.empty:
    # 计算年均交易次数
    years_span = best_result.trades[-1]["buy_date"].year - best_result.trades[0]["buy_date"].year + 1
    avg_trades_per_year = len(best_result.trades) / max(years_span, 1)

    print(ya_best.to_string(index=False))
    print(f"\n年均交易: {avg_trades_per_year:.1f}次")
    print(f"年均收益: ¥{best_summary['total_profit'] / max(years_span, 1):,.0f}")

# ===================== 保存完整报告 =====================
report_path = "D:\\mycode\\backtest_601988_report.txt"
with open(report_path, "w", encoding="utf-8") as f:
    f.write("=" * 90 + "\n")
    f.write(f"中国银行({STOCK_CODE}) 波段交易策略回测报告\n")
    f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"数据范围: {df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}\n")
    f.write(f"初始资金: ¥{CAPITAL:,} | 目标每笔盈利: ¥{TARGET_PROFIT}\n")
    f.write("=" * 90 + "\n\n")

    f.write("策略排名:\n")
    f.write("-" * 90 + "\n")
    for idx, (_, s) in enumerate(results_sorted):
        f.write(f"{idx+1}. {s['name']:<25s} 收益: ¥{s['total_profit']:>8,.0f} "
                f"({s['total_return']:>5.1%})  胜率: {s['win_rate']:>5.1%}  "
                f"交易: {s['total_trades']:>3d}笔  评分: {s['score']:>5.1f}\n")

    f.write("\n" + "=" * 90 + "\n")
    f.write(f"最优策略: {best_summary['name']}\n")
    f.write("=" * 90 + "\n")
    f.write(f"总收益: ¥{best_summary['total_profit']:,.0f}\n")
    f.write(f"收益率: {best_summary['total_return']:.1%}\n")
    f.write(f"交易笔数: {best_summary['total_trades']}\n")
    f.write(f"胜率: {best_summary['win_rate']:.1%}\n")
    f.write(f"平均盈利: ¥{best_summary['avg_profit']:,.0f}\n")
    f.write(f"平均持仓: {best_summary['avg_hold_days']:.1f}天\n")
    f.write(f"最终资金: ¥{best_summary['final_capital']:,.0f}\n\n")

    f.write("完整交易记录:\n")
    f.write("-" * 110 + "\n")
    for i, t in enumerate(best_result.trades):
        f.write(f"{i+1:>3d}. {t['buy_date'].strftime('%Y-%m-%d')} 买入 ¥{t['buy_price']:.2f} → "
                f"{t['sell_date'].strftime('%Y-%m-%d')} 卖出 ¥{t['sell_price']:.2f}  "
                f"持仓{t['hold_days']:>3d}天 盈亏: ¥{t['profit']:>8,.0f} ({t['profit_pct']:>5.1%})\n")

    f.write("\n逐年分析:\n")
    if not ya_best.empty:
        f.write(ya_best.to_string(index=False))
        f.write("\n")

print(f"  ✓ 报告已保存: {report_path}")
print("\n🎉 回测完成！")
