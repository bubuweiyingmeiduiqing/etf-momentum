"""Report orchestrator - data fetch, strategy compute, AI call, storage"""

import json, logging, os
from datetime import datetime, timedelta
from typing import Optional

from core.trading_calendar import TradingCalendar
from core.strategy_engine import StrategyEngine, NumpyEncoder
from core.deepseek_client import DeepSeekClient

logger = logging.getLogger(__name__)
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


class ReportGenerator:

    def __init__(self, config: dict, db, notifier=None):
        self.config = config
        self.db = db
        self.notifier = notifier
        self.calendar = TradingCalendar()
        self.engine = StrategyEngine(db)
        self.deepseek = DeepSeekClient(config)
        self._load_prompts()

    def _load_prompts(self):
        with open(os.path.join(PROMPTS_DIR, "daily_prompt.txt"), "r", encoding="utf-8") as f:
            self.daily_template = f.read()
        with open(os.path.join(PROMPTS_DIR, "review_prompt.txt"), "r", encoding="utf-8") as f:
            self.review_template = f.read()
        with open(os.path.join(PROMPTS_DIR, "daily_config.json"), "r", encoding="utf-8") as f:
            self.daily_system = "\n".join(json.load(f).get("system_prompt", []))
        with open(os.path.join(PROMPTS_DIR, "review_config.json"), "r", encoding="utf-8") as f:
            self.review_system = "\n".join(json.load(f).get("system_prompt", []))

    def generate_daily_report(self, trade_date: str = None) -> Optional[str]:
        if trade_date is None:
            trade_date = self.calendar.last_trade_day()
        logger.info("=== Generate daily report: %s ===", trade_date)

        # Auto-sync: copy latest quotes to daily_summary if today's data is missing
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

        # DATA FRESHNESS CHECK: verify daily_summary has data for this date
        for sym in ["510500", "513100"]:
            rows = self.db.get_daily_summary(sym, limit=1)
            if rows:
                latest_date = rows[-1].get("date", "N/A")
                if latest_date != trade_date:
                    logger.error("STALE DATA: %s latest=%s requested=%s - report may use wrong prices!", sym, latest_date, trade_date)

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

        # Inject actual closing prices as anti-hallucination anchors
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
        review_data = self._build_review_data(start_date, end_date)

        user_prompt = self.review_template
        user_prompt = user_prompt.replace("{{start_date}}", start_date)
        user_prompt = user_prompt.replace("{{end_date}}", end_date)
        user_prompt = user_prompt.replace("{{total_trading_days}}", str(review_data.get("total_trading_days", 0)))
        user_prompt = user_prompt.replace("{{rebalance_count}}", str(review_data.get("rebalance_count", 0)))
        user_prompt = user_prompt.replace("{{REVIEW_DATA}}", json.dumps(review_data, cls=NumpyEncoder, ensure_ascii=False, indent=2))
        user_prompt = user_prompt.replace("{{current_prompt_version}}", "v3.0")

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

    def _build_review_data(self, start_date, end_date):
        reports = self.db.get_daily_reports(start_date, end_date)
        trade_days = [r.get("trade_date") for r in reports if r.get("trade_date")]
        return {
            "review_period": {
                "start_date": start_date, "end_date": end_date,
                "total_trading_days": len(trade_days),
                "rebalance_count": max(1, len(trade_days) // 5 + 1),
            },
            "performance_summary": {
                "strategy_cumulative_return_pct": 0,
                "benchmark_cumulative_return_pct": 0,
                "benchmark_name": "\u6caa\u6df1300",
                "excess_return_pct": 0,
            },
            "rebalance_history": [],
            "stop_loss_events": [],
        }
