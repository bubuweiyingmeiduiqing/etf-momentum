"""Report orchestrator - data fetch, strategy compute, AI call, storage"""

import json, logging, os
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from core.trading_calendar import TradingCalendar
from core.strategy_engine import StrategyEngine, NumpyEncoder
from core.deepseek_client import DeepSeekClient
from core.strategy_config import V2_CONFIG

logger = logging.getLogger(__name__)
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

NL = "\n"


class ReportGenerator:

    def __init__(self, config: dict, db, notifier=None):
        self.config = config
        self.db = db
        self.notifier = notifier
        self.calendar = TradingCalendar()
        self.engine = StrategyEngine(db)
        self.engine_v2 = StrategyEngine(db, V2_CONFIG)  # v2 optimized strategy for A/B comparison
        self.deepseek = DeepSeekClient(config)
        self._load_prompts()
        self._last_review_start = None
        self._last_review_end = None

    def _load_prompts(self):
        with open(os.path.join(PROMPTS_DIR, "daily_prompt.txt"), "r", encoding="utf-8") as f:
            self.daily_template = f.read()
        with open(os.path.join(PROMPTS_DIR, "review_prompt.txt"), "r", encoding="utf-8") as f:
            self.review_template = f.read()
        with open(os.path.join(PROMPTS_DIR, "daily_config.json"), "r", encoding="utf-8") as f:
            self.daily_system = NL.join(json.load(f).get("system_prompt", []))
        with open(os.path.join(PROMPTS_DIR, "review_config.json"), "r", encoding="utf-8") as f:
            self.review_system = NL.join(json.load(f).get("system_prompt", []))

    def generate_daily_report(self, trade_date: str = None) -> Optional[str]:
        if trade_date is None:
            trade_date = self.calendar.last_trade_day()
        logger.info("=== Generate daily report: %s ===", trade_date)

        today = datetime.now().strftime("%Y-%m-%d")
        if trade_date >= today:
            try:
                from core.database import Database as DB
                db2 = DB(self.config["database"]["path"])
                for sym in self.engine.pool:
                    rows = db2.get_daily_summary(sym, limit=1)
                    if not rows or rows[-1].get("date") != trade_date:
                        q = db2.get_latest_quote(sym)
                        if q and q.get("close"):
                            db2.upsert_daily_summary(sym, {
                                "date": trade_date,
                                "open": q.get("open"), "high": q.get("high"),
                                "low": q.get("low"), "close": q.get("close"),
                                "volume": q.get("volume"),
                                "change_pct": q.get("change_pct"),
                            })
                logger.info("Auto-synced quotes to daily_summary for %s", trade_date)
            except Exception as e:
                logger.warning("Auto-sync skipped: %s", e)

        for sym in self.engine.pool:
            rows = self.db.get_daily_summary(sym, limit=1)
            if rows:
                latest_date = rows[-1].get("date", "N/A")
                if latest_date != trade_date:
                    logger.error("STALE DATA: %s latest=%s requested=%s", sym, latest_date, trade_date)

        result = self.engine.compute_all(trade_date)
        if not result.etfs:
            logger.warning("No valid ETF data, skip")
            return None

        prev_positions = self._load_previous_positions()
        data_input = self.engine.build_data_input(result, prev_positions)
        formatted_data = self.engine.build_formatted_data(result, prev_positions)

        is_monday = datetime.now().weekday() == 0
        user_prompt = self.daily_template
        user_prompt = user_prompt.replace("{REPORT_DATE}", trade_date)
        user_prompt = user_prompt.replace("{IS_REBALANCE_DAY}",
            "\u662f\uff08\u5468\u4e00\u8c03\u4ed3\u65e5\uff09" if is_monday else "\u5426")
        user_prompt = user_prompt.replace("{PRECOMPUTED_S1}", formatted_data.get("s1_price", ""))
        user_prompt = user_prompt.replace("{PRECOMPUTED_S2}", formatted_data.get("s2_summary", ""))
        user_prompt = user_prompt.replace("{PRECOMPUTED_S3}", formatted_data.get("s3_strategy", ""))
        # Pre-fill action-oriented placeholders from strategy result
        holdings = result.target_holdings
        if len(holdings) >= 1:
            h1 = holdings[0]
            user_prompt = user_prompt.replace("{TOP1_CODE}", h1.get("code",""))
            user_prompt = user_prompt.replace("{TOP1_NAME}", h1.get("name",""))
            user_prompt = user_prompt.replace("{TOP1_AMOUNT}", str(int(h1.get("target_value",0))))
            user_prompt = user_prompt.replace("{TOP1_PCT}", str(h1.get("risk_parity_weight",0)) + "%")
        if len(holdings) >= 2:
            h2 = holdings[1]
            user_prompt = user_prompt.replace("{TOP2_CODE}", h2.get("code",""))
            user_prompt = user_prompt.replace("{TOP2_NAME}", h2.get("name",""))
            user_prompt = user_prompt.replace("{TOP2_AMOUNT}", str(int(h2.get("target_value",0))))
            user_prompt = user_prompt.replace("{TOP2_PCT}", str(h2.get("risk_parity_weight",0)) + "%")
        # Previous holdings summary
        prev_summary = "\u7a7a\u4ed3"
        if prev_positions.get("has_positions"):
            ph = prev_positions.get("holdings", [])
            if ph:
                prev_summary = ", ".join([h.get("name","") + " " + str(h.get("pct",0)) + "%" for h in ph])
        user_prompt = user_prompt.replace("{PREV_HOLDINGS}", prev_summary)
        # Current holdings
        if holdings:
            cur_summary = ", ".join([h.get("name","") + " " + str(h.get("risk_parity_weight",0)) + "%" for h in holdings])
        else:
            cur_summary = "\u7a7a\u4ed3"
        user_prompt = user_prompt.replace("{CURRENT_HOLDINGS}", cur_summary)
        # Risk note
        if result.vol_trigger_active:
            risk_note = "\u6ce2\u52a8\u7387\u89e6\u53d1\u9632\u5fa1\u6a21\u5f0f\uff0c\u5168\u8d44\u4ea7\u5e73\u5747ATR {:.1f}%\u8d853.5%\u9608\u503c\uff0c40%\u8f6c\u56fd\u503aETF".format(result.avg_pool_atr_pct)
        else:
            max_atr_etf = max(result.etfs, key=lambda e: e.atr_pct or 0) if result.etfs else None
            risk_note = "\u6ce2\u52a8\u7387\u6b63\u5e38(\u5747ATR {:.1f}%)\uff0c\u6700\u5927ATR\u54c1\u79cd: {} {:.1f}%".format(result.avg_pool_atr_pct, max_atr_etf.code if max_atr_etf else "N/A", max_atr_etf.atr_pct or 0 if max_atr_etf else 0)
        user_prompt = user_prompt.replace("{RISK_NOTE}", risk_note)


        for etf in result.etfs:
            placeholder = "{C_" + etf.code + "}"
            price_str = f"{etf.close:.3f}" if etf.close else "N/A"
            user_prompt = user_prompt.replace(placeholder, price_str)

        html = self.deepseek.chat(self.daily_system, user_prompt)
        self._save_daily_report(trade_date, result, data_input, html)
        if self.notifier:
            try:
                self.notifier.send_daily_report(trade_date, html)
            except Exception as e:
                logger.error("Send report failed: %s", e)
        logger.info("Daily report done: %s (%d chars)", trade_date, len(html))
        return html

    def generate_review_report(self, review_type: str = "weekly") -> Optional[str]:
        end_date = self.calendar.last_trade_day()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        if review_type == "weekly":
            start_dt = end_dt - timedelta(days=7)
        else:
            start_dt = end_dt - timedelta(days=30)
        start_date = self.calendar.last_trade_day(start_dt)

        logger.info("=== Generate %s review: %s ~ %s ===", review_type, start_date, end_date)
        self._last_review_start = start_date
        self._last_review_end = end_date
        sections = self._build_review_sections(start_date, end_date)

        user_prompt = self.review_template
        user_prompt = user_prompt.replace("{START_DATE}", start_date)
        user_prompt = user_prompt.replace("{END_DATE}", end_date)
        user_prompt = user_prompt.replace("{TRADING_DAYS}", str(sections.get("total_trading_days", 0)))
        user_prompt = user_prompt.replace("{REBALANCE_COUNT}", str(sections.get("rebalance_count", 0)))
        user_prompt = user_prompt.replace("{MARKET_CONTEXT}", sections.get("market_context", ""))
        user_prompt = user_prompt.replace("{PRECOMPUTED_R1}", sections.get("r1_performance", ""))
        user_prompt = user_prompt.replace("{PRECOMPUTED_R2}", sections.get("r2_rebalance", ""))
        user_prompt = user_prompt.replace("{PRECOMPUTED_R3}", sections.get("r3_stops", ""))
        user_prompt = user_prompt.replace("{PRECOMPUTED_R4}", sections.get("r4_filters", ""))

        html = self.deepseek.chat(self.review_system, user_prompt)
        self._save_review_report(start_date, end_date, review_type, html)
        logger.info("Review done (%d chars)", len(html))
        return html

    def _load_previous_positions(self) -> dict:
        rows = self.db.get_recent_reports(limit=1)
        if not rows:
            return {"has_positions": False}
        latest = rows[0]
        try:
            return json.loads(latest.get("position_advice", "{}"))
        except Exception:
            return {"has_positions": False}

    def _save_daily_report(self, trade_date, result, data_input, html):
        self.db.insert_daily_report(
            trade_date=trade_date,
            strategy_result=json.dumps(result.to_dict(), cls=NumpyEncoder, ensure_ascii=False),
            data_input=json.dumps(data_input, cls=NumpyEncoder, ensure_ascii=False),
            html_content=html,
            position_advice=json.dumps({
                "trade_date": trade_date,
                "holdings": [{"code": h["code"], "name": h["name"],
                              "pct": h.get("risk_parity_weight", 0),
                              "score": h.get("risk_adjusted_score", 0)} for h in result.target_holdings],
                "candidates": [[c[0], c[1], c[2], c[3]] for c in result.candidates],
                "vol_trigger": result.vol_trigger_active,
            }, cls=NumpyEncoder, ensure_ascii=False),
        )

    def _save_review_report(self, start_date, end_date, review_type, html):
        self.db.insert_review_report(
            start_date=start_date, end_date=end_date,
            review_type=review_type, html_content=html,
        )

    # ============================================================
    # REVIEW DATA COMPUTATION (rebuilt 2026-06-24)
    # ============================================================

    def send_rebalance_reminder(self, trade_date: str = None):
        """Send rebalance reminder email with current positions and candidates."""
        if trade_date is None:
            trade_date = self.calendar.last_trade_day()
        logger.info("=== Rebalance reminder for %s ===", trade_date)

        prev_positions = self._load_previous_positions()
        result = self.engine.compute_all(trade_date)

        if not result.etfs:
            logger.warning("No ETF data for rebalance reminder")
            return

        holdings = prev_positions.get("holdings", [])
        candidates = [[c[0], c[1], c[2], c[3]] for c in result.candidates]

        if self.notifier:
            try:
                self.notifier.send_rebalance_reminder(trade_date, holdings, candidates)
            except Exception as e:
                logger.error("Rebalance reminder send failed: %s", e)


    def _build_review_sections(self, start_date: str, end_date: str) -> dict:
        # Load ALL reports from inception for continuous NAV computation
        all_reports = self.db.get_daily_reports(limit=200)
        if not all_reports:
            logger.warning("No daily reports found")
            return {"total_trading_days": 0, "rebalance_count": 0,
                    "market_context": "", "r1_performance": "", "r2_rebalance": "",
                    "r3_stops": "", "r4_filters": ""}

        all_reports.sort(key=lambda r: r.get("trade_date", ""))
        period_reports = [r for r in all_reports if start_date <= r.get("trade_date", "") <= end_date]

        # Price data: load ALL available history for NAV & market context
        # Load ALL available price data (not limited by report dates)
        price_data = self._load_price_matrix("2000-01-01", end_date)

        # Compute NAV from inception (continuous, not reset each week)
        nav_result = self._compute_strategy_nav(all_reports, price_data, start_date, end_date)
        # Compute V2 (optimized) NAV for A/B comparison
        nav_result_v2 = self._compute_v2_strategy_nav(all_reports, price_data, start_date, end_date)

        # Build market context
        market_ctx = self._build_market_context(price_data, start_date, end_date)

        return {
            "total_trading_days": len(period_reports),
            "rebalance_count": nav_result.get("period_rebalance_count", 0),
            "market_context": market_ctx,
            "r1_performance": self._build_r1_performance(nav_result, nav_result_v2, start_date, end_date),
            "r2_rebalance": self._build_r2_rebalance(period_reports, price_data),
            "r3_stops": self._build_r3_stops(all_reports, price_data),
            "r4_filters": self._build_r4_filters(period_reports),
        }

    def _load_price_matrix(self, start_date: str, end_date: str) -> dict:
        price_data = {}
        for sym in self.engine.pool:
            rows = self.db.get_daily_summary(sym, limit=60)
            sym_prices = {}
            for r in rows:
                d = r.get("date", "")
                c = r.get("close")
                if d and c is not None and start_date <= d <= end_date:
                    sym_prices[d] = float(c)
            if sym_prices:
                price_data[sym] = sym_prices
        return price_data

    def _compute_strategy_nav(self, reports: list, price_data: dict, period_start: str = None, period_end: str = None) -> dict:
        INITIAL_CAPITAL = 100000.0
        nav = INITIAL_CAPITAL
        period_start_nav = INITIAL_CAPITAL
        period_start_set = False
        nav_history = []
        holdings = {}
        rebalance_dates = []
        daily_returns = []
        prev_nav = INITIAL_CAPITAL
        prev_date = None

        for r in reports:
            trade_date = r.get("trade_date", "")
            if not trade_date:
                continue

            if prev_date and holdings:
                total_value = 0.0
                for code, h in holdings.items():
                    close = price_data.get(code, {}).get(trade_date)
                    if close:
                        total_value += h["shares"] * close
                if total_value > 0:
                    nav = total_value

            pos_advice = {}
            try:
                pos_advice = json.loads(r.get("position_advice", "{}"))
            except Exception:
                pass

            target = pos_advice.get("holdings", [])
            if target:
                rebalance_dates.append(trade_date)
                new_holdings = {}
                for h in target:
                    code = h.get("code", "")
                    pct = h.get("pct", 0)
                    close = price_data.get(code, {}).get(trade_date)
                    if close and pct > 0:
                        alloc = nav * (pct / 100.0)
                        shares = alloc / close
                        new_holdings[code] = {"shares": shares, "name": h.get("name", "")}
                holdings = new_holdings

            actual_nav = nav
            if holdings:
                actual_nav = 0.0
                for code, h in holdings.items():
                    close = price_data.get(code, {}).get(trade_date)
                    if close:
                        actual_nav += h["shares"] * close
                if actual_nav > 0:
                    nav = actual_nav

            nav_history.append({"date": trade_date, "nav": round(actual_nav, 2)})

            # Capture NAV at period start
            if period_start and trade_date >= period_start and not period_start_set:
                period_start_nav = actual_nav
                period_start_set = True

            if prev_date:
                ret = (actual_nav - prev_nav) / prev_nav if prev_nav > 0 else 0
                daily_returns.append(ret)

            prev_nav = actual_nav
            prev_date = trade_date

        bench_return = 0.0
        if "510300" in price_data and price_data["510300"]:
            prices = sorted(price_data["510300"].items())
            if len(prices) >= 2:
                # Filter to period range for consistency with strategy_return
                period_prices = [(d, p) for d, p in prices if period_start and d >= period_start]
                if not period_prices:
                    period_prices = prices
                if len(period_prices) >= 2:
                    bench_return = (period_prices[-1][1] - period_prices[0][1]) / period_prices[0][1] * 100

        # Cumulative benchmark: inception to end
        cumulative_bench_return = 0.0
        if "510300" in price_data and price_data["510300"]:
            all_prices = sorted(price_data["510300"].items())
            if len(all_prices) >= 2:
                cumulative_bench_return = (all_prices[-1][1] - all_prices[0][1]) / all_prices[0][1] * 100
        # Period return: from period_start to period_end
        period_return = ((nav - period_start_nav) / period_start_nav) * 100 if period_start_nav > 0 else 0
        # Cumulative return: from inception
        cumulative_return = ((nav - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100

        max_dd = 0.0
        peak = INITIAL_CAPITAL
        for nh in nav_history:
            nv = nh["nav"]
            if nv > peak:
                peak = nv
            dd = (peak - nv) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        sharpe = 0.0
        if len(daily_returns) > 1:
            avg_ret = np.mean(daily_returns)
            std_ret = np.std(daily_returns, ddof=1)
            if std_ret > 0:
                sharpe = (avg_ret / std_ret) * np.sqrt(252)

        calmar = cumulative_return / max_dd if max_dd > 0 else 0

        wins = sum(1 for r in daily_returns if r > 0)
        total = len(daily_returns)
        win_rate = (wins / total * 100) if total > 0 else 0

        win_vals = [r for r in daily_returns if r > 0]
        loss_vals = [r for r in daily_returns if r < 0]
        avg_win = np.mean(win_vals) * 100 if win_vals else 0
        avg_loss = np.mean(loss_vals) * 100 if loss_vals else 0

        total_win = sum(win_vals) if win_vals else 0
        total_loss = abs(sum(loss_vals)) if loss_vals else 1
        profit_factor = total_win / total_loss if total_loss > 0 else 0

        return {
            "rebalance_count": len(rebalance_dates),
            "period_rebalance_count": len([d for d in rebalance_dates if period_start and d >= period_start]),
            "strategy_return": round(period_return, 2),
            "cumulative_return": round(cumulative_return, 2),
            "benchmark_return": round(bench_return, 2),
            "cumulative_benchmark_return": round(cumulative_bench_return, 2),
            "excess_return": round(period_return - bench_return, 2),
            "max_drawdown": round(max_dd, 2),
            "sharpe": round(sharpe, 2),
            "calmar": round(calmar, 2),
            "win_rate": round(win_rate, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "final_nav": round(nav, 2),
        }

    def _compute_v2_strategy_nav(self, reports: list, price_data: dict,
                                  period_start: str = None, period_end: str = None) -> dict:
        """Backtest V2 (optimized) strategy over the same period for A/B comparison."""
        INITIAL_CAPITAL = 100000.0
        nav = INITIAL_CAPITAL
        period_start_nav = INITIAL_CAPITAL
        period_start_set = False
        nav_history = []
        holdings = {}
        rebalance_dates = []
        daily_returns = []
        prev_nav = INITIAL_CAPITAL
        prev_date = None
        total_trade_cost = 0.0
        trade_cost_rate = self.engine_v2.cfg.trade_cost_bps / 10000.0  # bps -> decimal

        for r in reports:
            trade_date = r.get("trade_date", "")
            if not trade_date:
                continue

            # Update NAV from market prices before rebalance
            if prev_date and holdings:
                total_value = 0.0
                for code, h in holdings.items():
                    close = price_data.get(code, {}).get(trade_date)
                    if close:
                        total_value += h["shares"] * close
                if total_value > 0:
                    nav = total_value

            # Run V2 strategy for this date
            v2_result = self.engine_v2.compute_all(trade_date)
            target = v2_result.target_holdings if v2_result else []

            if target:
                rebalance_dates.append(trade_date)
                # Apply trade costs: sell old + buy new
                if holdings:
                    old_value = 0.0
                    for code, h in holdings.items():
                        close = price_data.get(code, {}).get(trade_date)
                        if close:
                            old_value += h["shares"] * close
                    total_trade_cost += old_value * trade_cost_rate  # sell cost

                new_holdings = {}
                new_value = 0.0
                for h in target:
                    code = h.get("code", "")
                    target_val = h.get("target_value", 0)
                    close = price_data.get(code, {}).get(trade_date)
                    if close and target_val > 0:
                        shares = target_val / close
                        new_holdings[code] = {"shares": shares, "name": h.get("name", "")}
                        new_value += target_val
                holdings = new_holdings
                if new_value > 0:
                    total_trade_cost += new_value * trade_cost_rate  # buy cost

            actual_nav = nav
            if holdings:
                actual_nav = 0.0
                for code, h in holdings.items():
                    close = price_data.get(code, {}).get(trade_date)
                    if close:
                        actual_nav += h["shares"] * close
                if actual_nav > 0:
                    nav = actual_nav

            nav_history.append({"date": trade_date, "nav": round(actual_nav, 2)})

            if period_start and trade_date >= period_start and not period_start_set:
                period_start_nav = actual_nav
                period_start_set = True

            if prev_date:
                ret = (actual_nav - prev_nav) / prev_nav if prev_nav > 0 else 0
                daily_returns.append(ret)

            prev_nav = actual_nav
            prev_date = trade_date

        # Benchmark (same as v1)
        bench_return = 0.0
        if "510300" in price_data and price_data["510300"]:
            prices = sorted(price_data["510300"].items())
            period_prices = [(d, p) for d, p in prices if period_start and d >= period_start]
            if not period_prices:
                period_prices = prices
            if len(period_prices) >= 2:
                bench_return = (period_prices[-1][1] - period_prices[0][1]) / period_prices[0][1] * 100

        cumulative_bench_return = 0.0
        if "510300" in price_data and price_data["510300"]:
            all_prices = sorted(price_data["510300"].items())
            if len(all_prices) >= 2:
                cumulative_bench_return = (all_prices[-1][1] - all_prices[0][1]) / all_prices[0][1] * 100

        period_return = ((nav - period_start_nav) / period_start_nav) * 100 if period_start_nav > 0 else 0
        cumulative_return = ((nav - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100

        max_dd = 0.0
        peak = INITIAL_CAPITAL
        for nh in nav_history:
            nv = nh["nav"]
            if nv > peak:
                peak = nv
            dd = (peak - nv) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        sharpe = 0.0
        if len(daily_returns) > 1:
            avg_ret = np.mean(daily_returns)
            std_ret = np.std(daily_returns, ddof=1)
            if std_ret > 0:
                sharpe = (avg_ret / std_ret) * np.sqrt(252)

        calmar = cumulative_return / max_dd if max_dd > 0 else 0

        wins = sum(1 for r in daily_returns if r > 0)
        total = len(daily_returns)
        win_rate = (wins / total * 100) if total > 0 else 0

        win_vals = [r for r in daily_returns if r > 0]
        loss_vals = [r for r in daily_returns if r < 0]
        avg_win = np.mean(win_vals) * 100 if win_vals else 0
        avg_loss = np.mean(loss_vals) * 100 if loss_vals else 0

        total_win = sum(win_vals) if win_vals else 0
        total_loss = abs(sum(loss_vals)) if loss_vals else 1
        profit_factor = total_win / total_loss if total_loss > 0 else 0

        return {
            "rebalance_count": len(rebalance_dates),
            "period_rebalance_count": len([d for d in rebalance_dates if period_start and d >= period_start]),
            "strategy_return": round(period_return, 2),
            "cumulative_return": round(cumulative_return, 2),
            "benchmark_return": round(bench_return, 2),
            "cumulative_benchmark_return": round(cumulative_bench_return, 2),
            "excess_return": round(period_return - bench_return, 2),
            "max_drawdown": round(max_dd, 2),
            "sharpe": round(sharpe, 2),
            "calmar": round(calmar, 2),
            "win_rate": round(win_rate, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "final_nav": round(nav, 2),
            "total_trade_cost": round(total_trade_cost, 2),
        }

    def _build_market_context(self, price_data: dict, start_date: str, end_date: str) -> str:
        """Build market macro context HTML for the review period."""
        rows = []
        total_rows = []
        for sym in self.engine.pool:
            info = self.engine.pool.get(sym, {})
            name = info.get("name", sym)
            prices = price_data.get(sym, {})
            if not prices:
                continue
            sorted_dates = sorted(prices.keys())
            period_prices = {d: p for d, p in prices.items() if start_date <= d <= end_date}
            if not period_prices:
                continue
            p_dates = sorted(period_prices.keys())
            first_p = period_prices[p_dates[0]]
            last_p = period_prices[p_dates[-1]]
            ret = (last_p - first_p) / first_p * 100 if first_p > 0 else 0
            rows.append(f"<tr><td>{sym}</td><td>{name}</td><td>{first_p:.3f}</td><td>{last_p:.3f}</td><td style='color:{'#1a7a1a' if ret>0 else '#b00020'}'>{ret:+.2f}%</td></tr>")
            total_rows.append(ret)

        if not rows:
            return "<p>No market data available for this period</p>\n"

        # Overall market summary
        avg_ret = sum(total_rows) / len(total_rows) if total_rows else 0
        best = max(total_rows) if total_rows else 0
        worst = min(total_rows) if total_rows else 0
        green_count = sum(1 for r in total_rows if r > 0)
        red_count = sum(1 for r in total_rows if r < 0)

        style = ""
        if avg_ret > 2: style = "\u5f3a\u52b2\u4e0a\u6da8 (bullish)"
        elif avg_ret > 0: style = "\u5c0f\u5e45\u4e0a\u6da8 (mildly bullish)"
        elif avg_ret > -2: style = "\u5c0f\u5e45\u4e0b\u8dcc (mildly bearish)"
        else: style = "\u660e\u663e\u4e0b\u8dcc (bearish)"

        return (
            f"<p><b>Period:</b> {start_date} ~ {end_date} | DO NOT MODIFY</p>\n"
            + f"<div style='background:#f0f4f8;border-left:4px solid #0b5394;padding:10px;margin:8px 0'>\n"
            + f"<b>\u5e02\u573a\u98ce\u683c:</b> {style} | \u5e73\u5747\u6536\u76ca {avg_ret:+.2f}% | "
            + f"\u6da8\u8dcc\u6bd4 {green_count}:{red_count} | "
            + f"\u6700\u4f73 {best:+.2f}% / \u6700\u5dee {worst:+.2f}%\n"
            + f"</div>\n"
            + "<table border=1 cellpadding=5 cellspacing=0 style='border-collapse:collapse;width:100%'>\n"
            + "<tr style='background:#0b5394;color:#fff'><th>\u4ee3\u7801</th><th>\u540d\u79f0</th><th>\u671f\u521d\u4ef7</th><th>\u671f\u672b\u4ef7</th><th>\u5468\u6536\u76ca</th></tr>\n"
            + "".join(rows) + "</table>\n"
        )


    def _build_r1_performance(self, nav: dict, nav_v2: dict = None, start: str = "", end: str = "") -> str:
        sr = nav.get("strategy_return", 0)
        br = nav.get("benchmark_return", 0)
        cbr = nav.get("cumulative_benchmark_return", 0)
        er = nav.get("excess_return", 0)
        md = nav.get("max_drawdown", 0)
        sh = nav.get("sharpe", 0)
        ca = nav.get("calmar", 0)
        wr = nav.get("win_rate", 0)
        aw = nav.get("avg_win", 0)
        al = nav.get("avg_loss", 0)
        pf = nav.get("profit_factor", 0)
        fn = nav.get("final_nav", 0)

        GREEN = "\U0001F7E2"
        RED = "\U0001F534"
        WARN = "\u26A0\uFE0F"

        ex_color = "#1a7a1a" if er > 0 else "#b00020"
        dd_color = "#1a7a1a" if md < 5 else ("#e67e00" if md < 10 else "#b00020")
        cr = nav.get("cumulative_return", 0)
        cex_color = "#1a7a1a" if cr > cbr else "#b00020"

        rows = []
        rows.append("<tr style='background:#0b5394;color:#fff'><th>指标</th><th>策略值</th><th>基准(沪深300)</th><th>超额</th><th>评价</th></tr>")
        rows.append("<tr><td><b>累计收益(自起始)</b></td><td style='color:" + ("#1a7a1a" if cr>0 else "#b00020") + "'>" + f"{cr:+.2f}%</td><td>{cbr:+.2f}%</td><td style='color:{cex_color}'>{cr-cbr:+.2f}%</td><td>" + (GREEN if cr>cbr else RED) + (" 跑赢" if cr>cbr else " 跑输") + "</td></tr>")
        rows.append("<tr><td><b>本周收益</b></td><td style='color:" + ("#1a7a1a" if sr>0 else "#b00020") + "'>" + f"{sr:+.2f}%</td><td>-</td><td>-</td><td></td></tr>")
        rows.append("<tr><td><b>最大回撤</b></td><td style='color:" + dd_color + "'>" + f"{md:.2f}%</td><td>-</td><td>-</td><td>" + (GREEN if md<5 else (WARN if md<10 else RED)) + (" 优秀" if md<5 else (" 警戒" if md<10 else " 危险")) + "</td></tr>")
        rows.append("<tr><td><b>夏普比率</b></td><td>{:.2f}</td><td>-</td><td>-</td><td>".format(sh) + (GREEN if sh>1 else (WARN if sh>0 else RED)) + (" 良好" if sh>1 else (" 一般" if sh>0 else " 负值")) + "</td></tr>")
        rows.append("<tr><td><b>卡玛比率</b></td><td>{:.2f}</td><td>-</td><td>-</td><td></td></tr>".format(ca))
        rows.append("<tr><td><b>胜率</b></td><td>{:.1f}%</td><td>-</td><td>-</td><td></td></tr>".format(wr))
        rows.append("<tr><td><b>平均盈/亏</b></td><td>+{:.2f}% / {:.2f}%</td><td>-</td><td>-</td><td></td></tr>".format(aw, al))
        rows.append("<tr><td><b>盈亏比</b></td><td>{:.2f}</td><td>-</td><td>-</td><td>".format(pf) + (GREEN if pf>1.5 else (WARN if pf>1 else RED)) + "</td></tr>")
        rows.append("<tr><td><b>期末净值</b></td><td><b>{:.2f}</b></td><td>-</td><td>-</td><td>起始100,000</td></tr>".format(fn))

        v1_section = (
            "<h3>V1 原版策略</h3>\n"
            + "<p><b>Data Timestamp:</b> " + end + " | DO NOT MODIFY</p>\n"
            + "<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%'>\n"
            + "\n".join(rows) + "\n</table>\n"
        )

        # V2 comparison section
        v2_section = ""
        if nav_v2:
            delta = lambda v1, v2: v2 - v1
            delta_str = lambda d: f"{d:+.2f}"
            d_cr = delta(cr, nav_v2.get("cumulative_return", 0))
            d_sr = delta(sr, nav_v2.get("strategy_return", 0))
            d_md = delta(md, nav_v2.get("max_drawdown", 0))
            d_sh = delta(sh, nav_v2.get("sharpe", 0))
            d_ca = delta(ca, nav_v2.get("calmar", 0))
            d_wr = delta(wr, nav_v2.get("win_rate", 0))
            d_fn = delta(fn, nav_v2.get("final_nav", 0))
            tc = nav_v2.get("total_trade_cost", 0)

            d_color = lambda x: "#1a7a1a" if x > 0 else ("#b00020" if x < 0 else "#666")
            # For drawdown, lower is better (inverted)
            dd_color_delta = "#1a7a1a" if d_md < 0 else ("#b00020" if d_md > 0 else "#666")
            d_icon = lambda x: (GREEN + " +" if x > 0 else (RED + " " if x < 0 else ""))

            comp_rows = []
            comp_rows.append("<tr style='background:#0b5394;color:#fff'><th>指标</th><th>V1原版</th><th>V2优化</th><th>差异</th><th>评价</th></tr>")
            comp_rows.append(f"<tr><td><b>累计收益</b></td><td>{cr:+.2f}%</td><td>{nav_v2.get('cumulative_return',0):+.2f}%</td><td style='color:{d_color(d_cr)}'>{delta_str(d_cr)}%</td><td>{d_icon(d_cr)}</td></tr>")
            comp_rows.append(f"<tr><td><b>本周收益</b></td><td>{sr:+.2f}%</td><td>{nav_v2.get('strategy_return',0):+.2f}%</td><td style='color:{d_color(d_sr)}'>{delta_str(d_sr)}%</td><td>{d_icon(d_sr)}</td></tr>")
            comp_rows.append(f"<tr><td><b>最大回撤</b></td><td>{md:.2f}%</td><td>{nav_v2.get('max_drawdown',0):.2f}%</td><td style='color:{dd_color_delta}'>{delta_str(d_md)}%</td><td>{GREEN if d_md < 0 else (RED if d_md > 0 else '')}</td></tr>")
            comp_rows.append(f"<tr><td><b>夏普比率</b></td><td>{sh:.2f}</td><td>{nav_v2.get('sharpe',0):.2f}</td><td style='color:{d_color(d_sh)}'>{delta_str(d_sh)}</td><td>{d_icon(d_sh)}</td></tr>")
            comp_rows.append(f"<tr><td><b>卡玛比率</b></td><td>{ca:.2f}</td><td>{nav_v2.get('calmar',0):.2f}</td><td style='color:{d_color(d_ca)}'>{delta_str(d_ca)}</td><td>{d_icon(d_ca)}</td></tr>")
            comp_rows.append(f"<tr><td><b>胜率</b></td><td>{wr:.1f}%</td><td>{nav_v2.get('win_rate',0):.1f}%</td><td style='color:{d_color(d_wr)}'>{delta_str(d_wr)}%</td><td>{d_icon(d_wr)}</td></tr>")
            comp_rows.append(f"<tr><td><b>期末净值</b></td><td>{fn:.2f}</td><td>{nav_v2.get('final_nav',0):.2f}</td><td style='color:{d_color(d_fn)}'>{delta_str(d_fn)}</td><td>{d_icon(d_fn)}</td></tr>")
            comp_rows.append(f"<tr style='background:#f5f5f5'><td><b>交易成本(V2)</b></td><td>-</td><td>{tc:.2f}</td><td colspan=2>V2扣除{self.engine_v2.cfg.trade_cost_bps:.0f}bps单边成本</td></tr>")

            v2_section = (
                "<h3>V1 vs V2 策略对比 (A/B Test)</h3>\n"
                + "<p><b>V2优化项:</b> P0趋势过滤放宽(10日动量+SMA20容差0.5%) | "
                + "P1动量加权分配 | P2最低仓位10% & 交易成本0.1% | DO NOT MODIFY</p>\n"
                + "<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%'>\n"
                + "\n".join(comp_rows) + "\n</table>\n"
            )

        return v1_section + v2_section

    def _build_r2_rebalance(self, reports: list, price_data: dict) -> str:
        rows = []
        cnt = 0
        for r in reports:
            pos = {}
            try:
                pos = json.loads(r.get("position_advice", "{}"))
            except Exception:
                continue
            target = pos.get("holdings", [])
            if not target:
                continue
            cnt += 1
            trade_date = r.get("trade_date", "")
            vol_trig = "YES" if pos.get("vol_trigger") else "NO"
            etf_str = ", ".join([h.get("name","") + " " + str(h.get("pct",0)) + "%" for h in target])
            rows.append("<tr><td>" + trade_date + "</td><td>" + str(cnt) + "</td><td>" + etf_str + "</td><td>" + vol_trig + "</td></tr>")

        if not rows:
            return "<p>\u65e0\u8c03\u4ed3\u8bb0\u5f55</p>\n"

        return (
            "<p><b>Data Timestamp:</b> From position_advice in daily_reports | DO NOT MODIFY</p>\n"
            + "<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%'>\n"
            + "<tr style='background:#0b5394;color:#fff'><th>\u65e5\u671f</th><th>#</th><th>\u6301\u4ed3ETF(\u6743\u91cd)</th><th>\u6ce2\u52a8\u7387\u89e6\u53d1</th></tr>\n"
            + "\n".join(rows) + "\n</table>\n"
        )

    def _build_r3_stops(self, reports: list, price_data: dict) -> str:
        events = []
        prev_holdings = set()
        for r in reports:
            pos = {}
            try:
                pos = json.loads(r.get("position_advice", "{}"))
            except Exception:
                continue
            curr_codes = set()
            for h in pos.get("holdings", []):
                curr_codes.add(h.get("code", ""))
            trade_date = r.get("trade_date", "")
            removed = prev_holdings - curr_codes
            if removed and prev_holdings:
                for code in removed:
                    close = price_data.get(code, {}).get(trade_date)
                    events.append({"date": trade_date, "code": code,
                                   "name": self.engine.pool.get(code, {}).get("name", code),
                                   "close": close if close else 0})
            if curr_codes:
                prev_holdings = curr_codes

        if not events:
            return "<p>\u65e0\u6b62\u635f\u4e8b\u4ef6</p>\n"

        RED = "\U0001F534"
        rows = []
        for ev in events:
            rows.append("<tr><td>" + ev["date"] + "</td><td>" + ev["code"] + "</td><td>" + ev["name"] + "</td><td>" + f"{ev['close']:.3f}" + "</td><td style='color:#b00020'>" + RED + " \u79fb\u9664</td></tr>")

        return (
            "<p><b>Data Timestamp:</b> Detected from position_advice changes | DO NOT MODIFY</p>\n"
            + "<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%'>\n"
            + "<tr style='background:#0b5394;color:#fff'><th>\u65e5\u671f</th><th>\u4ee3\u7801</th><th>\u540d\u79f0</th><th>\u9000\u51fa\u4ef7</th><th>\u4e8b\u4ef6</th></tr>\n"
            + "\n".join(rows) + "\n</table>\n"
        )

    def _build_r4_filters(self, reports: list) -> str:
        total_passes = 0
        total_checks = 0
        for r in reports:
            try:
                sr = json.loads(r.get("strategy_result", "{}"))
            except Exception:
                continue
            etfs = sr.get("etfs", [])
            for etf in etfs:
                tf = etf.get("trend_filter", {})
                total_checks += 1
                if tf.get("filter_pass"):
                    total_passes += 1

        pass_rate = (total_passes / total_checks * 100) if total_checks > 0 else 0
        GREEN = "\U0001F7E2"
        RED = "\U0001F534"

        return (
            "<p><b>Data Timestamp:</b> Computed from stored strategy_result | DO NOT MODIFY</p>\n"
            + "<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%'>\n"
            + "<tr style='background:#0b5394;color:#fff'><th>\u8fc7\u6ee4\u5668</th><th>\u603b\u68c0\u67e5</th><th>\u901a\u8fc7</th><th>\u901a\u8fc7\u7387</th><th>\u8bc4\u4ef7</th></tr>\n"
            + "<tr><td><b>\u8d8b\u52bf\u8fc7\u6ee4(SMA20)</b></td><td>" + str(total_checks) + "</td><td>" + str(total_passes) + "</td><td>" + f"{pass_rate:.1f}%</td><td>" + (GREEN if pass_rate>50 else RED) + (" \u5408\u7406" if pass_rate>50 else " \u8fc7\u4e25") + "</td></tr>\n"
            + "</table>\n"
        )


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "item"):
            return obj.item()
        return super().default(obj)
