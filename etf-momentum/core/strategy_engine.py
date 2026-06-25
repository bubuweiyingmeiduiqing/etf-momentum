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
            result.vol_trigger_active = result.avg_pool_atr_pct > (VOL_TRIGGER_ATR * 100)
            result.vol_trigger_detail = (
                f"全资产平均ATR {result.avg_pool_atr_pct:.2f}%，"
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
        # DATE VALIDATION: last row must match requested trade_date
        last_hist_date = hist[-1].get("date", "N/A")
        if last_hist_date != trade_date:
            logger.error("DATE MISMATCH: %s requested=%s latest_data=%s", code, trade_date, last_hist_date)
        # Build aligned arrays: filter out rows with missing/invalid OHLC data
        valid_rows = []
        for h in hist:
            c = h.get("close")
            if c is not None and isinstance(c, (int, float)) and 0.01 < c < 100000:
                valid_rows.append(h)
            else:
                logger.warning("%s row date=%s has invalid close=%s, skipping", code, h.get("date"), c)
        if len(valid_rows) < 9:
            logger.warning("%s only %d valid rows after filtering", code, len(valid_rows))
            return None
        closes = np.array([h["close"] for h in valid_rows], dtype=float)
        highs = np.array([h.get("high", h["close"]) for h in valid_rows], dtype=float)
        lows = np.array([h.get("low", h["close"]) for h in valid_rows], dtype=float)
        volumes = np.array([h.get("volume", 0) or 0 for h in valid_rows], dtype=float)
        # Verify last valid row date matches trade_date
        last_valid_date = valid_rows[-1].get("date", "N/A")
        if last_valid_date != trade_date:
            logger.error("DATE MISMATCH after filter: %s requested=%s valid_last=%s", code, trade_date, last_valid_date)
        latest = closes[-1]
        if latest <= 0:
            logger.error("%s latest close=%s is invalid, abort", code, latest)
            return None
        ind = EtfIndicators(code=code, name=name, trade_date=trade_date, close=latest)
        if len(closes) >= 21:
            if closes[-21] > 0.001:
                r20 = (closes[-1] / closes[-21] - 1) * 100
                if -80 < r20 < 200:
                    ind.return_20d_pct = round(r20, 2)
                else:
                    logger.error("%s return_20d=%.2f%% anomalous, data suspect", code, r20)
            else:
                logger.error("%s closes[-21]=%s near-zero, return skipped", code, closes[-21])
        if len(closes) >= 20:
            denom = closes[-21:-1]
            if np.any(np.abs(denom) < 0.001):
                logger.error("%s has near-zero price in 20d window, volatility skipped", code)
            else:
                rets = np.diff(closes[-21:]) / denom
                std_ret = float(np.std(rets))
                if std_ret < 0.5:
                    ind.volatility_20d_pct = round(std_ret * 100, 2)
                else:
                    logger.error("%s volatility_20d std=%.4f anomalous, data suspect", code, std_ret)
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
            if latest > 0.001:
                raw_atr_pct = ind.atr_14d / latest * 100
                if 0.01 < raw_atr_pct < 50:
                    ind.atr_pct = round(raw_atr_pct, 2)
                else:
                    logger.error("%s ATR%%=%.2f anomalous (atr_14d=%.4f latest=%.3f), data suspect", code, raw_atr_pct, ind.atr_14d, latest)
        if len(volumes) >= 20:
            vavg = np.mean(volumes[-21:-1])
            if vavg > 0:
                ind.volume_vs_20d_avg_ratio = round(float(volumes[-1] / vavg), 2)
        return ind


    def build_formatted_data(self, result, previous_positions=None):
        """3-section report: Price header + Summary with prior positions + Strategy table."""
        sections = {}
        NL = chr(10)
        prev = previous_positions or {}

        # ====== SECTION 1: PRICE HEADER ======
        price_rows = []
        for etf in result.etfs:
            ch5 = f"{etf.return_5d_pct:+.2f}%" if etf.return_5d_pct else "N/A"
            ch20 = f"{etf.return_20d_pct:+.2f}%" if etf.return_20d_pct else "N/A"
            price_rows.append(
                f"<tr><td>{etf.code}</td><td>{etf.name}</td>"
                f"<td><b>{etf.close:.3f}</b></td>"
                f"<td>{ch5}</td><td>{ch20}</td></tr>")
        sections["s1_price"] = (
            f"<h2>{chr(0x1F4CA)} {result.trade_date} 收盘数据</h2>" + NL
            + "<table border=1 cellpadding=5 cellspacing=0 style='border-collapse:collapse;width:100%'>" + NL
            + "<tr style='background:#0b5394;color:#fff'><th>代码</th><th>名称</th><th>收盘价</th><th>5日涨跌</th><th>20日涨跌</th></tr>" + NL
            + "".join(price_rows) + NL + "</table>"
        )

        # ====== SECTION 2: SUMMARY with prior positions ======
        avg_atr = round(result.avg_pool_atr_pct, 2)
        threshold = 3.5
        ratio = avg_atr / threshold
        if ratio < 0.7:
            light, lcolor, ltxt = chr(0x1F7E2), "#1a7a1a", "低波安全"
        elif ratio < 1.0:
            light, lcolor, ltxt = chr(0x1F7E1), "#e67e00", "关注"
        else:
            light, lcolor, ltxt = chr(0x1F534), "#b00020", "防御触发"

        # Prior position summary
        prior_text = ""
        if prev.get("trade_date") and prev.get("holdings"):
            prior_text = f"<p><b>上期持仓({prev['trade_date']}):</b> "
            for h in prev["holdings"]:
                pct = h.get("pct", h.get("risk_parity_weight", 0))
                prior_text += f"{h.get('code','?')} {h.get('name','?')} {pct}%, "
            prior_text = prior_text.rstrip(", ") + "</p>"
            if prev.get("vol_trigger"):
                prior_text += f"<p><b>上期模式:</b> {'防御' if prev['vol_trigger'] else '进攻'}</p>"

        # Special performers
        top_etf = sorted(result.etfs, key=lambda e: e.risk_adjusted_score or -999, reverse=True)[0]
        worst_etf = sorted(result.etfs, key=lambda e: e.risk_adjusted_score or 999)[0]
        high_vol = [e for e in result.etfs if e.atr_pct and e.atr_pct > 3.5]
        
        specials = ""
        if top_etf.risk_adjusted_score:
            specials += f"<li><b>动量领先:</b> {top_etf.code} {top_etf.name} 得分{top_etf.risk_adjusted_score:.2f} (20日收益{top_etf.return_20d_pct:+.2f}%)</li>"
        if high_vol:
            for hv in high_vol:
                specials += f"<li><b>高波动:</b> {hv.code} {hv.name} ATR {hv.atr_pct}% {'(超过3.5%阈值)' if hv.atr_pct and hv.atr_pct > 3.5 else ''}</li>"

        sections["s2_summary"] = (
            f"<div style='background:#f0f4f8;border-left:4px solid {lcolor};padding:12px;margin:8px 0'>" + NL
            + f"<b>{chr(0x1F4CA)} 市场情绪:</b> {light} {ltxt} | 平均ATR {avg_atr}% vs 阈值{threshold}% ({round(ratio*100)}%)" + NL
            + f"</div>" + NL
            + f"<h3>{chr(0x1F3AF)} 特殊表现</h3><ul>{specials}</ul>" + NL
            + prior_text
        )

        # ====== SECTION 3: STRATEGY TABLE (all-in-one) ======
        rows = []
        for etf in sorted(result.etfs, key=lambda e: e.risk_adjusted_score or -999, reverse=True):
            # Score emoji
            s = etf.risk_adjusted_score or 0
            if s >= 3: se = chr(0x1F7E2)
            elif s >= 1.5: se = chr(0x1F7E1)
            elif s > 0: se = chr(0x1F7E0)
            else: se = chr(0x1F534)
            # Trend
            if etf.filter_pass: te = chr(0x1F7E2)
            else: te = chr(0x1F534)
            # SMA20 direction
            dmap = {"上行": chr(0x1F7E2), "走平": chr(0x26A0)+chr(0xFE0F), "下行": chr(0x1F534)}
            de = dmap.get(etf.sma20_direction, "")
            # ATR highlight
            atr_style = "font-weight:bold;color:#b00020" if etf.atr_pct and etf.atr_pct > 3.5 else ""
            # Score style
            score_style = "font-weight:bold;color:#e67e00" if 1.0 <= s < 2.0 else ""

            rows.append(
                f"<tr>"
                f"<td>{etf.score_rank or '-'}</td><td>{etf.code}</td><td>{etf.name}</td>"
                f"<td>{etf.close:.3f}</td>"
                f"<td>{etf.return_20d_pct:+.2f}%</td>"
                f"<td>{etf.volatility_20d_pct:.2f}%</td>"
                f"<td style='{score_style}'>{se} {s:.2f}</td>"
                f"<td>{etf.sma20:.3f}</td><td>{de} {etf.sma20_direction}</td>"
                f"<td>{te} {'PASS' if etf.filter_pass else 'FAIL'}</td>"
                f"<td style='{atr_style}'>{etf.atr_pct}%</td>"
                f"</tr>")

        sections["s3_strategy"] = (
            f"<h2>{chr(0x1F4C8)} 策略参数总览</h2>" + NL
            + "<table border=1 cellpadding=5 cellspacing=0 style='border-collapse:collapse;width:100%;font-size:13px'>" + NL
            + "<tr style='background:#0b5394;color:#fff'>"
            + "<th>排名</th><th>代码</th><th>名称</th><th>收盘价</th><th>20日收益</th><th>20日波动</th><th>风险调整得分</th><th>SMA20</th><th>均线方向</th><th>趋势过滤</th><th>ATR%</th>"
            + "</tr>" + NL
            + "".join(rows) + NL + "</table>" + NL
            + f"<p><b>全资产平均ATR:</b> {avg_atr}% | <b>阈值:</b> {threshold}% | "
            + f"<b>模式:</b> {chr(0x1F6E1)+chr(0xFE0F) if result.vol_trigger_active else chr(0x2694)+chr(0xFE0F)} "
            + f"{'DEFENSE 防御' if result.vol_trigger_active else 'ATTACK 进攻'} | "
            + f"<b>通过过滤:</b> {len(result.candidates)}/{len(result.etfs)}</p>"
        )

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
