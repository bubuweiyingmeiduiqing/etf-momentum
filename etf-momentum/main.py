#!/usr/bin/env python3
"""
ETF Momentum 量化交易辅助系统 —— 主入口
===========================================
用法:
    python main.py                  # 启动全部服务（Web + 调度器）
    python main.py --web            # 仅启动 Web 界面
    python main.py --scheduler      # 仅启动调度器
    python main.py --fetch          # 执行一次数据抓取
    python main.py --init-db        # 初始化数据库 + 拉取历史数据
"""

import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import load_config
from core.database import Database
from core.fetcher import DataFetcher
from core.scheduler import TaskScheduler
from monitor.indicators import IndicatorCalculator
from monitor.alerts import Alerter
from notify import Notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def init_db(config):
    """初始化数据库并同步历史数据。"""
    logger.info("初始化数据库...")
    db = Database(config["database"]["path"])
    fetcher = DataFetcher(config, database=db)
    logger.info(f"同步 {len(fetcher.symbols)} 只标的历史数据...")
    fetcher.fetch_all_history()
    logger.info("数据库初始化完成 ✓")
    return db


def run_fetch(config):
    """执行一次数据抓取并计算指标、检查告警。"""
    db = Database(config["database"]["path"])
    fetcher = DataFetcher(config, database=db)
    indicator_calc = IndicatorCalculator(config, database=db)
    alerter = Alerter(config, database=db)
    notifier = Notifier(config)

    logger.info("开始数据抓取...")
    quotes = fetcher.fetch_all_realtime()

    for quote in quotes:
        indicators = indicator_calc.compute(quote)
        if indicators:
            db.insert_indicators(quote["symbol"], indicators)
        alerts = alerter.check(quote, indicators)
        for alert in alerts:
            db.insert_alert(alert)
            notifier.send_alert(alert)

    logger.info(f"抓取完成: {len(quotes)} 只标的, {db.count_alerts_today()} 条告警")


def run_all(config):
    """启动 Web + 调度器。"""
    import threading
    db = Database(config["database"]["path"])
    fetcher = DataFetcher(config, database=db)
    indicator_calc = IndicatorCalculator(config, database=db)
    alerter = Alerter(config, database=db)
    notifier = Notifier(config)

    # 启动调度器（后台线程）
    scheduler = TaskScheduler(config, fetcher, indicator_calc, alerter, notifier)
    scheduler.start()

    # 启动 Web 服务（主线程）
    from web.app import app
    web_config = config.get("web", {})
    logger.info(f"Web 服务启动: http://{web_config.get('host','0.0.0.0')}:{web_config.get('port',5000)}")
    try:
        app.run(
            host=web_config.get("host", "0.0.0.0"),
            port=web_config.get("port", 5000),
            debug=web_config.get("debug", False),
            use_reloader=False,
        )
    finally:
        scheduler.stop()


def main():
    parser = argparse.ArgumentParser(description="ETF Momentum 量化交易辅助系统")
    parser.add_argument("--web", action="store_true", help="仅启动 Web 界面")
    parser.add_argument("--scheduler", action="store_true", help="仅启动调度器")
    parser.add_argument("--fetch", action="store_true", help="执行一次数据抓取")
    parser.add_argument("--init-db", action="store_true", help="初始化数据库 + 历史数据")
    parser.add_argument("--config", default=None, help="配置文件路径")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.init_db:
        init_db(config)
    elif args.fetch:
        run_fetch(config)
    elif args.web:
        from web.app import app
        web_config = config.get("web", {})
        logger.info(f"Web 服务启动: http://{web_config.get('host','0.0.0.0')}:{web_config.get('port',5000)}")
        app.run(
            host=web_config.get("host", "0.0.0.0"),
            port=web_config.get("port", 5000),
            debug=web_config.get("debug", False),
        )
    elif args.scheduler:
        db = Database(config["database"]["path"])
        fetcher = DataFetcher(config, database=db)
        indicator_calc = IndicatorCalculator(config, database=db)
        alerter = Alerter(config, database=db)
        notifier = Notifier(config)
        scheduler = TaskScheduler(config, fetcher, indicator_calc, alerter, notifier)
        scheduler.start()
        try:
            import time
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            scheduler.stop()
    else:
        run_all(config)


if __name__ == "__main__":
    main()
