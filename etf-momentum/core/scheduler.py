"""任务调度模块 —— 基于 APScheduler 管理定时任务"""

import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class TaskScheduler:
    """后台任务调度器，管理数据采集、指标计算、报告生成等定时任务。"""

    def __init__(self, config: dict, fetcher, indicator_calc, alerter, notifier):
        self.config = config.get("scheduler", {})
        self.fetcher = fetcher
        self.indicator_calc = indicator_calc
        self.alerter = alerter
        self.notifier = notifier
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._tasks_registered = False

    def start(self):
        """启动调度器。"""
        if not self.config.get("enabled", True):
            logger.info("调度器已禁用")
            return

        if not self._tasks_registered:
            self._register_tasks()
            self._tasks_registered = True

        self.scheduler.start()
        logger.info(f"调度器已启动，监控 {len(self.fetcher.symbols)} 只标的")

    def stop(self):
        """停止调度器。"""
        self.scheduler.shutdown(wait=False)
        logger.info("调度器已停止")

    def _register_tasks(self):
        """注册所有定时任务。"""
        # 盘中定时抓取（每 5 分钟）
        fetch_interval = self.fetcher.config.get("interval_minutes", 5)
        if fetch_interval > 0:
            self.scheduler.add_job(
                self._fetch_and_process,
                "interval",
                minutes=fetch_interval,
                id="fetch_realtime",
                name="实时行情抓取",
                replace_existing=True,
            )

        # 日终汇总报告
        daily_report_time = self.config.get("daily_report", "15:30")
        hour, minute = daily_report_time.split(":")
        self.scheduler.add_job(
            self._daily_report,
            CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
            id="daily_report",
            name="日终汇总报告",
            replace_existing=True,
        )

        logger.info(f"已注册 {len(self.scheduler.get_jobs())} 个定时任务")

    def _fetch_and_process(self):
        """抓取数据 → 计算指标 → 检查告警（核心链路）。"""
        if self.config.get("trade_only", True) and not self.fetcher.is_trade_day():
            return

        logger.debug("开始盘中数据抓取...")
        quotes = self.fetcher.fetch_all_realtime()

        for quote in quotes:
            # 计算技术指标
            indicators = self.indicator_calc.compute(quote)
            if indicators:
                self.fetcher.db.insert_indicators(quote["symbol"], indicators)

            # 检查告警规则
            alerts = self.alerter.check(quote, indicators)
            for alert in alerts:
                self.fetcher.db.insert_alert(alert)
                self.notifier.send_alert(alert)

    def _daily_report(self):
        """生成并发送日终报告。"""
        if not self.fetcher.is_trade_day():
            return

        logger.info("生成日终报告...")
        today = datetime.now().strftime("%Y-%m-%d")
        symbols = self.fetcher.symbols

        report_lines = [f"📊 日终报告 {today}", "=" * 30]
        for sym in symbols:
            quote = self.fetcher.db.get_latest_quote(sym)
            if quote:
                report_lines.append(
                    f"{sym} {quote.get('name','')}: "
                    f"收盘 {quote['close']} "
                    f"涨跌 {quote.get('change_pct','N/A')}%"
                )

        report = "\n".join(report_lines)
        self.notifier.send_report(report)
        logger.info("日终报告已发送")
