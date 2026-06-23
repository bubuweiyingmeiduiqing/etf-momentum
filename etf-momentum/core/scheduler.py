"""Task scheduler with report generation and error recovery"""

import logging, traceback
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

logger = logging.getLogger(__name__)


class TaskScheduler:

    def __init__(self, config, fetcher, indicator_calc, alerter, notifier,
                 report_generator=None, health_checker=None, db=None):
        self.sched_cfg = config.get("scheduler", {})
        self.fetcher = fetcher
        self.indicator_calc = indicator_calc
        self.alerter = alerter
        self.notifier = notifier
        self.report_generator = report_generator
        self.health_checker = health_checker
        self.db = db
        self.scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 300},
        )
        self.scheduler.add_listener(self._on_job_event, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)
        self._tasks_registered = False
        self._error_count = 0
        self._last_error_time = None

    def _on_job_event(self, event):
        if event.exception:
            self._error_count += 1
            self._last_error_time = datetime.now()
            tb = "".join(traceback.format_tb(event.traceback)) if event.traceback else ""
            logger.error("Job error [%s]: %s\n%s", event.job_id, event.exception, tb)

    def start(self):
        if not self.sched_cfg.get("enabled", True):
            logger.info("Scheduler disabled")
            return
        if not self._tasks_registered:
            self._register_tasks()
            self._tasks_registered = True
        self.scheduler.start()
        logger.info("Scheduler started, %d symbols", len(self.fetcher.symbols))

    def stop(self):
        self.scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped")

    def _register_tasks(self):
        fetch_interval = self.fetcher.config.get("interval_minutes", 5)
        if fetch_interval > 0:
            self.scheduler.add_job(self._safe_fetch, "interval", minutes=fetch_interval,
                                   id="fetch_realtime", name="Realtime fetch", replace_existing=True)

        daily_time = self.sched_cfg.get("daily_report", "15:30")
        h, m = daily_time.split(":")
        self.scheduler.add_job(self._safe_daily_report,
                               CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                               id="daily_report", name="Daily summary", replace_existing=True)

        # AI daily report: 5 min after market close
        rh, rm = int(h), int(m) + 5
        if rm >= 60:
            rh += 1; rm -= 60
        self.scheduler.add_job(self._safe_generate_daily_report,
                               CronTrigger(hour=rh, minute=rm, day_of_week="mon-fri"),
                               id="ai_daily_report", name="AI Daily Report", replace_existing=True)

        # Weekly review: Saturday 10:00
        self.scheduler.add_job(self._safe_generate_weekly_review,
                               CronTrigger(hour=10, minute=0, day_of_week="sat"),
                               id="weekly_review", name="Weekly Review", replace_existing=True)

        # Monthly review: 1st of month 10:30
        self.scheduler.add_job(self._safe_generate_monthly_review,
                               CronTrigger(hour=10, minute=30, day=1),
                               id="monthly_review", name="Monthly Review", replace_existing=True)

        # DB backup: daily 3:00 AM
        self.scheduler.add_job(self._safe_backup, CronTrigger(hour=3, minute=0),
                               id="db_backup", name="DB Backup", replace_existing=True)

        # WAL checkpoint: hourly
        self.scheduler.add_job(self._safe_checkpoint, "interval", hours=1,
                               id="wal_checkpoint", name="WAL Checkpoint", replace_existing=True)

        logger.info("Registered %d tasks", len(self.scheduler.get_jobs()))

    def _safe_fetch(self):
        try:
            self._fetch_and_process()
        except Exception as e:
            logger.error("Fetch error: %s", e)
            try:
                self.notifier.send_error("DataFetch", e, "5-min fetch cycle failed")
            except Exception:
                pass

    def _safe_daily_report(self):
        try: self._daily_report()
        except Exception as e: logger.error("Daily report error: %s", e)

    def _safe_generate_daily_report(self):
        try:
            self._sync_quotes_to_daily()
            if self.report_generator:
                self.report_generator.generate_daily_report()
        except Exception as e:
            logger.error("AI daily report error: %s", e)
            try:
                self.notifier.send_error("DailyReport", e, "AI report generation failed")
            except Exception:
                pass

    def _safe_generate_weekly_review(self):
        try:
            if self.report_generator:
                self.report_generator.generate_review_report("weekly")
        except Exception as e:
            logger.error("Weekly review error: %s", e)
            try:
                self.notifier.send_error("WeeklyReview", e, "Weekly review failed")
            except Exception:
                pass

    def _safe_generate_monthly_review(self):
        try:
            if self.report_generator:
                self.report_generator.generate_review_report("monthly")
        except Exception as e:
            logger.error("Monthly review error: %s", e)
            try:
                self.notifier.send_error("MonthlyReview", e, "Monthly review failed")
            except Exception:
                pass

    def _safe_backup(self):
        try:
            if self.db and hasattr(self.db, "backup"):
                self.db.backup()
        except Exception as e: logger.error("Backup error: %s", e)

    def _safe_checkpoint(self):
        try:
            if self.db and hasattr(self.db, "checkpoint_now"):
                self.db.checkpoint_now()
        except Exception as e: logger.error("Checkpoint error: %s", e)

    def _fetch_and_process(self):
        if self.sched_cfg.get("trade_only", True) and not self.fetcher.is_trade_day():
            return
        quotes = self.fetcher.fetch_all_realtime()
        for quote in quotes:
            try:
                ind = self.indicator_calc.compute(quote)
                if ind:
                    self.fetcher.db.insert_indicators(quote["symbol"], ind)
                alerts = self.alerter.check(quote, ind)
                for a in alerts:
                    self.fetcher.db.insert_alert(a)
                    self.notifier.send_alert(a)
            except Exception as e:
                logger.error("Process %s error: %s", quote.get("symbol"), e)


    def _sync_quotes_to_daily(self):
        """Copy latest quotes into daily_summary for report generation."""
        if not self.db or not self.fetcher:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        for sym in self.fetcher.symbols:
            try:
                q = self.fetcher.db.get_latest_quote(sym)
                if q and q.get("close"):
                    self.fetcher.db.upsert_daily_summary(sym, {
                        "date": today,
                        "open": q.get("open"),
                        "high": q.get("high"),
                        "low": q.get("low"),
                        "close": q.get("close"),
                        "volume": q.get("volume"),
                        "change_pct": q.get("change_pct"),
                    })
            except Exception as e:
                logger.error("Sync quote to daily failed for %s: %s", sym, e)
        # Verify sync by reading back
        for sym in self.fetcher.symbols[:2]:
            rows = self.db.get_daily_summary(sym, limit=1)
            if rows:
                latest = rows[-1].get("date", "N/A")
                if latest == today:
                    logger.info("Verified %s synced to daily_summary date=%s", sym, today)
                else:
                    logger.error("SYNC FAILED: %s daily_summary latest=%s expected=%s", sym, latest, today)
        logger.info("Synced quotes to daily_summary for %s", today)

    def _daily_report(self):
        if not self.fetcher.is_trade_day():
            return
        today = datetime.now().strftime("%Y-%m-%d")
        lines = ["Daily Report " + today, "=" * 30]
        for sym in self.fetcher.symbols:
            q = self.fetcher.db.get_latest_quote(sym)
            if q:
                lines.append("%s %s: close %s chg %s%%" % (sym, q.get("name",""), q.get("close","N/A"), q.get("change_pct","N/A")))
        self.notifier.send_report("\n".join(lines))

    @property
    def error_count(self):
        return self._error_count

    @property
    def last_error_time(self):
        return self._last_error_time
