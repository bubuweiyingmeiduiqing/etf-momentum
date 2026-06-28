"""Report orchestrator - data fetch, strategy compute, AI call, storage"""

import json, logging, os
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from core.trading_calendar import TradingCalendar
from core.strategy_engine import StrategyEngine, NumpyEncoder
from core.deepseek_client import DeepSeekClient

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

        # Price data: need from earliest report for continuous NAV
        earliest_date = all_reports[0].get("trade_date", "2026-01-01")
        price_data = self._load_price_matrix(earliest_date, end_date)

        # Compute NAV from inception (continuous, not reset each week)
        nav_result = self._compute_strategy_nav(all_reports, price_data, start_date, end_date)

        # Build market context
        market_ctx = self._build_market_context(price_data, start_date, end_date)

        return {
            "total_trading_days": len(period_reports),
            "rebalance_count": nav_result.get("period_rebalance_count", 0),
            "market_context": market_ctx,
            "r1_performance": self._build_r1_performance(nav_result, start_date, end_date),
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
                bench_return = (prices[-1][1] - prices[0][1]) / prices[0][1] * 100

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

        calmar = period_return / max_dd if max_dd > 0 else 0

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


    def _build_r1_performance(self, nav: dict, start: str, end: str) -> str:
        sr = nav.get("strategy_return", 0)
        br = nav.get("benchmark_return", 0)
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

        rows = []
        rows.append("<tr style='background:#0b5394;color:#fff'><th>\u6307\u6807</th><th>\u7b56\u7565\u503c</th><th>\u57fa\u51c6(\u6caa\u6df1300)</th><th>\u8d85\u989d</th><th>\u8bc4\u4ef7</th></tr>")
        cr = nav.get("cumulative_return", 0)
        rows.append("<tr><td><b>\u7d2f\u8ba1\u6536\u76ca(\u81ea\u8d77\u59cb)</b></td><td style='color:" + ("#1a7a1a" if cr>0 else "#b00020") + "'>" + f"{cr:+.2f}%</td><td>{br:+.2f}%</td><td style='color:{ex_color}'>{er:+.2f}%</td><td>" + (GREEN if er>0 else RED) + (" \u8dd1\u8d62" if er>0 else " \u8dd1\u8f93") + "</td></tr>")
        rows.append("<tr><td><b>\u672c\u5468\u6536\u76ca</b></td><td style='color:" + ("#1a7a1a" if sr>0 else "#b00020") + "'>" + f"{sr:+.2f}%</td><td>-</td><td>-</td><td></td></tr>")
        rows.append("<tr><td><b>\u6700\u5927\u56de\u64a4</b></td><td style='color:" + dd_color + "'>" + f"{md:.2f}%</td><td>-</td><td>-</td><td>" + (GREEN if md<5 else (WARN if md<10 else RED)) + (" \u4f18\u79c0" if md<5 else (" \u8b66\u6212" if md<10 else " \u5371\u9669")) + "</td></tr>")
        rows.append("<tr><td><b>\u590f\u666e\u6bd4\u7387</b></td><td>{:.2f}</td><td>-</td><td>-</td><td>".format(sh) + (GREEN if sh>1 else (WARN if sh>0 else RED)) + (" \u826f\u597d" if sh>1 else (" \u4e00\u822c" if sh>0 else " \u8d1f\u503c")) + "</td></tr>")
        rows.append("<tr><td><b>\u5361\u739b\u6bd4\u7387</b></td><td>{:.2f}</td><td>-</td><td>-</td><td></td></tr>".format(ca))
        rows.append("<tr><td><b>\u80dc\u7387</b></td><td>{:.1f}%</td><td>-</td><td>-</td><td></td></tr>".format(wr))
        rows.append("<tr><td><b>\u5e73\u5747\u76c8/\u4e8f</b></td><td>+{:.2f}% / {:.2f}%</td><td>-</td><td>-</td><td></td></tr>".format(aw, al))
        rows.append("<tr><td><b>\u76c8\u4e8f\u6bd4</b></td><td>{:.2f}</td><td>-</td><td>-</td><td>".format(pf) + (GREEN if pf>1.5 else (WARN if pf>1 else RED)) + "</td></tr>")
        rows.append("<tr><td><b>\u671f\u672b\u51c0\u503c</b></td><td><b>{:.2f}</b></td><td>-</td><td>-</td><td>\u8d77\u59cb100,000</td></tr>".format(fn))

        return (
            "<p><b>Data Timestamp:</b> " + end + " | All values computed from daily_summary close prices | DO NOT MODIFY</p>\n"
            + "<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%'>\n"
            + "\n".join(rows) + "\n</table>\n"
        )

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
