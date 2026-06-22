'''策略引擎 —— 基于 v1-base.py 的指标计算与信号生成'''

import logging
import numpy as np
import json
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

STRATEGY_ETF_POOL = {
    "510500": {"name": "中证500ETF", "market": "A股", "cross_border": False},
    "510300": {"name": "沪深300ETF", "market": "A股", "cross_border": False},
    "513100": {"name": "纳指ETF", "market": "美股跨境", "cross_border": True},
    "513520": {"name": "日经ETF", "market": "日股跨境", "cross_border": True},
    "588000": {"name": "科创50ETF", "market": "A股", "cross_border": False},
    "510050": {"name": "上证50ETF", "market": "A股", "cross_border": False},
}
BOND_ETF = {"code": "511010", "name": "国债ETF"}
TOTAL_CAPITAL = 100000.0
MAX_PREMIUM_RATE = 0.015
VOL_TRIGGER_ATR = 0.035
DEFENSE_BOND_PCT = 0.40
ATR_STOP_MULT = 3.0
MAX_HOLDINGS = 2
SINGLE_MAX_WEIGHT = 0.50


@dataclass
class EtfIndicators:
    code: str
    name: str
    trade_date: str
    close: float
    return_20d_pct: Optional[float] = None
    volatility_20d_pct: Optional[float] = None
    risk_adjusted_score: Optional[float] = None
    sma20: Optional[float] = None
    close_above_sma20: bool = False
    sma20_direction: str = "N/A"
    sma20_slope_pct: Optional[float] = None
    filter_pass: bool = False
    atr_14d: Optional[float] = None
    atr_pct: Optional[float] = None
    return_5d_pct: Optional[float] = None
    return_10d_pct: Optional[float] = None
    sma5: Optional[float] = None
    sma5_direction: str = "N/A"
    volume_vs_20d_avg_ratio: Optional[float] = None
    is_bullish_today: bool = False
    premium_rate: Optional[float] = None
    premium_exceeded: bool = False
    score_rank: int = 0
    score_eligible: bool = False
    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if v is not None and hasattr(v, "item"):
                d[k] = v.item()
        return d


@dataclass
class StrategyResult:
    trade_date: str
    etfs: list = field(default_factory=list)
    avg_pool_atr_pct: float = 0.0
    vol_trigger_active: bool = False
    vol_trigger_detail: str = ""
    candidates: list = field(default_factory=list)
    target_holdings: list = field(default_factory=list)
    bond_allocation_pct: float = 0.0
    equity_allocation: float = TOTAL_CAPITAL
    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date,
            "market_environment": {
                "avg_pool_atr_pct": round(self.avg_pool_atr_pct, 4),
                "vol_trigger_active": bool(self.vol_trigger_active),
                "vol_trigger_detail": self.vol_trigger_detail,
            },
            "etfs": [e.to_dict() for e in self.etfs],
            "candidates": self.candidates,
            "target_holdings": self.target_holdings,
            "bond_allocation_pct": self.bond_allocation_pct,
            "equity_allocation": self.equity_allocation,
        }


class StrategyEngine:
    def __init__(self, db):
        self.db = db
        self.pool = STRATEGY_ETF_POOL

    def compute_all(self, trade_date: str):
        result = StrategyResult(trade_date=trade_date)
        all_atr_pcts = []
        for code, info in self.pool.items():
            ind = self._compute_one(code, info["name"], trade_date, info["cross_border"])
            if ind:
                result.etfs.append(ind)
                if ind.atr_pct:
                    all_atr_pcts.append(ind.atr_pct)
        if all_atr_pcts:
            import numpy as np
            result.avg_pool_atr_pct = float(np.mean(all_atr_pcts))
            result.vol_trigger_active = result.avg_pool_atr_pct > VOL_TRIGGER_ATR
            result.vol_trigger_detail = (
                f"全资产平均ATR {result.avg_pool_atr_pct*100:.2f}%，"
                f"{'已触发' if result.vol_trigger_active else '未触发'}3.5%截断阈值"
            )
        eligible = [e for e in result.etfs if e.filter_pass and not e.premium_exceeded and (e.risk_adjusted_score or 0) > 0]
        eligible.sort(key=lambda x: x.risk_adjusted_score or 0, reverse=True)
        for i, e in enumerate(eligible):
            e.score_rank = i + 1
            e.score_eligible = True
        for e in result.etfs:
            if e not in eligible:
                e.score_rank = 0
                e.score_eligible = False
        result.candidates = [(e.code, e.name, e.risk_adjusted_score, e.atr_pct) for e in eligible]
        top2 = eligible[:MAX_HOLDINGS]
        if top2:
            inv_atrs = [1.0 / max(e.atr_pct, 0.001) for e in top2]
            inv_sum = sum(inv_atrs)
            weights = [inv / inv_sum for inv in inv_atrs]
            if result.vol_trigger_active:
                result.bond_allocation_pct = DEFENSE_BOND_PCT
                result.equity_allocation = TOTAL_CAPITAL * (1 - DEFENSE_BOND_PCT)
            equity_money = result.equity_allocation
            for e, w in zip(top2, weights):
                if len(top2) == 1:
                    w = min(w, SINGLE_MAX_WEIGHT)
                result.target_holdings.append({
                    "code": e.code, "name": e.name,
                    "risk_adjusted_score": e.risk_adjusted_score,
                    "atr_pct": e.atr_pct,
                    "risk_parity_weight": round(w * 100, 1),
                    "target_value": round(equity_money * w, 2),
                })
        return result

    def _compute_one(self, code, name, trade_date, cross_border):
        import numpy as np
        hist = self.db.get_daily_summary(code, limit=40)
        if not hist or len(hist) < 9:
            logger.warning("%s history <20 rows", code)
            return None
        closes = np.array([h["close"] for h in hist if h.get("close")], dtype=float)
        highs = np.array([h.get("high", h.get("close")) for h in hist], dtype=float)
        lows = np.array([h.get("low", h.get("close")) for h in hist], dtype=float)
        volumes = np.array([h.get("volume", 0) for h in hist], dtype=float)
        if len(closes) < 9:
            return None
        latest = closes[-1]
        ind = EtfIndicators(code=code, name=name, trade_date=trade_date, close=latest)
        if len(closes) >= 21:
            ind.return_20d_pct = round((closes[-1] / closes[-21] - 1) * 100, 2)
        if len(closes) >= 20:
            rets = np.diff(closes[-21:]) / closes[-21:-1]
            ind.volatility_20d_pct = round(float(np.std(rets) * 100), 2)
        if ind.return_20d_pct and ind.volatility_20d_pct and ind.volatility_20d_pct > 0:
            ind.risk_adjusted_score = round(ind.return_20d_pct / ind.volatility_20d_pct, 2)
        if len(closes) >= 5:
            ind.sma5 = round(float(np.mean(closes[-5:])), 4)
            sma5_prev = round(float(np.mean(closes[-8:-3])), 4) if len(closes) >= 8 else None
            if sma5_prev:
                if ind.sma5 > sma5_prev * 1.003:
                    ind.sma5_direction = "上行"
                elif ind.sma5 < sma5_prev * 0.997:
                    ind.sma5_direction = "下行"
                else:
                    ind.sma5_direction = "走平"
        if len(closes) >= 20:
            ind.sma20 = round(float(np.mean(closes[-20:])), 4)
            ind.close_above_sma20 = latest > ind.sma20
            sma20_prev = round(float(np.mean(closes[-21:-1])), 4) if len(closes) >= 21 else None
            if sma20_prev and sma20_prev != 0:
                ind.sma20_slope_pct = round((ind.sma20 - sma20_prev) / sma20_prev * 100, 2)
                if ind.sma20_slope_pct > 0.3:
                    ind.sma20_direction = "上行"
                elif ind.sma20_slope_pct < -0.3:
                    ind.sma20_direction = "下行"
                else:
                    ind.sma20_direction = "走平"
            ind.filter_pass = ind.close_above_sma20 and ind.sma20_direction != "下行"
        if len(closes) >= 6:
            ind.return_5d_pct = round((closes[-1] / closes[-6] - 1) * 100, 2)
        if len(closes) >= 11:
            ind.return_10d_pct = round((closes[-1] / closes[-11] - 1) * 100, 2)
        if len(closes) >= 15:
            trs = []
            for i in range(-15, 0):
                h = highs[i]
                lv = lows[i]
                pc = closes[i-1]
                tr = max(h - lv, abs(h - pc), abs(pc - lv))
                trs.append(tr)
            ind.atr_14d = round(float(np.mean(trs)), 4)
            if latest > 0:
                ind.atr_pct = round(ind.atr_14d / latest * 100, 2)
        if len(volumes) >= 20:
            vavg = np.mean(volumes[-21:-1])
            if vavg > 0:
                ind.volume_vs_20d_avg_ratio = round(float(volumes[-1] / vavg), 2)
        return ind


﻿    def build_formatted_data(self, result, previous_positions=None):
        """Build COMPLETE pre-formatted HTML data sections. Model only adds commentary."""
        sections = {}

        # Section 2: Momentum Ranking
        rows = []
        for etf in sorted(result.etfs, key=lambda e: e.risk_adjusted_score or -999, reverse=True):
            score = str(round(etf.risk_adjusted_score, 2)) if etf.risk_adjusted_score else "N/A"
            ret20 = ("+" if etf.return_20d_pct and etf.return_20d_pct > 0 else "") + str(etf.return_20d_pct) + "%" if etf.return_20d_pct else "N/A"
            vol20 = str(etf.volatility_20d_pct) + "%" if etf.volatility_20d_pct else "N/A"
            status_parts = []
            if etf.filter_pass: status_parts.append("Trend OK")
            else: status_parts.append("Trend FAIL")
            if etf.score_eligible: status_parts.append("#" + str(etf.score_rank))
            status = ", ".join(status_parts)
            rows.append("<tr><td>" + str(etf.score_rank if etf.score_rank else "-") + "</td><td>" + etf.code + "</td><td>" + etf.name + "</td><td>" + ret20 + "</td><td>" + vol20 + "</td><td>" + score + "</td><td>" + status + "</td></tr>")

        sections["momentum"] = "<h2>Risk-Adjusted Momentum Ranking</h2>\n<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%'>\n<tr style='background:#0b5394;color:#fff'><th>Rank</th><th>Code</th><th>Name</th><th>20d Ret%</th><th>20d Vol%</th><th>Score</th><th>Status</th></tr>\n" + "".join(rows) + "\n</table>"

        # Section 3: Trend Filter - THIS IS THE CRITICAL ONE
        rows2 = []
        for etf in result.etfs:
            sma20 = str(round(etf.sma20, 4)) if etf.sma20 else "N/A"
            sma5 = str(round(etf.sma5, 4)) if etf.sma5 else "N/A"
            slope = str(etf.sma20_slope_pct) + "%" if etf.sma20_slope_pct else "N/A"
            above = "YES" if etf.close_above_sma20 else "NO"
            passed = "PASS" if etf.filter_pass else "FAIL"
            price = str(round(etf.close, 3))
            rows2.append("<tr><td>" + etf.code + "</td><td>" + etf.name + "</td><td><b>" + price + "</b></td><td>" + sma20 + "</td><td>" + etf.sma20_direction + "</td><td>" + slope + "</td><td>" + above + "</td><td>" + passed + "</td><td>" + sma5 + "</td><td>" + etf.sma5_direction + "</td></tr>")

        sections["trend"] = "<h2>Trend Filter & SMA System</h2>\n<p>CRITICAL: All prices below are real market data from Yahoo Finance. DO NOT CHANGE any number. Use ONLY these values.</p>\n<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%'>\n<tr style='background:#0b5394;color:#fff'><th>Code</th><th>Name</th><th>CLOSE PRICE</th><th>SMA20</th><th>SMA20 Dir</th><th>Slope</th><th>Price>SMA20</th><th>Filter</th><th>SMA5</th><th>SMA5 Dir</th></tr>\n" + "".join(rows2) + "\n</table>"

        # Section 4: ATR
        rows3 = []
        for etf in result.etfs:
            atr14 = str(round(etf.atr_14d, 4)) if etf.atr_14d else "N/A"
            atrpct = str(etf.atr_pct) + "%" if etf.atr_pct else "N/A"
            ret5 = str(etf.return_5d_pct) + "%" if etf.return_5d_pct else "N/A"
            ret10 = str(etf.return_10d_pct) + "%" if etf.return_10d_pct else "N/A"
            rows3.append("<tr><td>" + etf.code + "</td><td>" + etf.name + "</td><td>" + atr14 + "</td><td>" + atrpct + "</td><td>" + ret5 + "</td><td>" + ret10 + "</td></tr>")

        avg_atr = str(round(result.avg_pool_atr_pct, 2))
        trigger = "DEFENSE MODE ACTIVE" if result.vol_trigger_active else "ATTACK MODE"
        sections["volatility"] = "<h2>Volatility & Risk Exposure</h2>\n<p>Avg Pool ATR%: <b>" + avg_atr + "%</b> | Status: <b>" + trigger + "</b></p>\n<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%'>\n<tr style='background:#0b5394;color:#fff'><th>Code</th><th>Name</th><th>ATR(14d)</th><th>ATR%</th><th>5d Ret%</th><th>10d Ret%</th></tr>\n" + "".join(rows3) + "\n</table>"

        # Section 6: Candidates
        if result.candidates:
            cand_lines = []
            for c in result.candidates:
                cand_lines.append("<li>" + str(c[0]) + " " + str(c[1]) + " Score: " + str(c[2]) + " ATR%: " + str(c[3]) + "</li>")
            sections["candidates"] = "<h3>Candidates (all filters passed)</h3><ul>" + "".join(cand_lines) + "</ul>"
        else:
            sections["candidates"] = "<h3>Candidates</h3><p>No symbols passed all filters</p>"

        return sections

    def build_data_input(self, result, previous_positions=None, cross_border_premiums=None):
        from datetime import datetime
        data = {
            "report_date": result.trade_date,
            "is_rebalance_day": datetime.now().weekday() == 0,
            "rebalance_weekday": "周一",
            "market_environment": {
                "benchmark_index": "沪深300",
                "benchmark_return_20d_pct": 0,
                "avg_pool_atr_pct": round(result.avg_pool_atr_pct, 2),
                "vol_trigger_active": bool(result.vol_trigger_active),
                "vol_trigger_detail": result.vol_trigger_detail,
            },
            "etfs": [],
            "cross_border_premium": cross_border_premiums or [],
            "current_positions": previous_positions or {"has_positions": False},
            "previous_week_summary": {},
        }
        for etf in result.etfs:
            entry = {
                "code": etf.code, "name": etf.name,
                "market": self.pool[etf.code]["market"],
                "price_snapshot": {"latest_close": etf.close, "previous_close": None,
                                   "open": None, "high": None, "low": None,
                                   "volume": None, "turnover": None, "daily_change_pct": None},
                "momentum_scoring": {"return_20d_pct": etf.return_20d_pct,
                                     "volatility_20d_pct": etf.volatility_20d_pct,
                                     "risk_adjusted_score": etf.risk_adjusted_score,
                                     "score_rank": etf.score_rank,
                                     "score_eligible": bool(etf.score_eligible)},
                "trend_filter": {"sma20": etf.sma20,
                                 "close_above_sma20": bool(etf.close_above_sma20),
                                 "sma20_direction": etf.sma20_direction,
                                 "sma20_slope_pct": etf.sma20_slope_pct,
                                 "filter_pass": bool(etf.filter_pass)},
                "atr_indicators": {"atr_14d_value": etf.atr_14d, "atr_pct": etf.atr_pct},
                "supplementary_momentum": {"return_5d_pct": etf.return_5d_pct,
                                           "return_10d_pct": etf.return_10d_pct,
                                           "sma5": etf.sma5,
                                           "sma5_direction": etf.sma5_direction,
                                           "volume_vs_20d_avg_ratio": etf.volume_vs_20d_avg_ratio},
            }
            data["etfs"].append(entry)
        return data


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "item"):
            return obj.item()
        return super().default(obj)
