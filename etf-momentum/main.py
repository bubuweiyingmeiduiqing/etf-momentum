#!/usr/bin/env python3
"""
ETF Momentum 量化交易辅助系统 —— 主入口（云端守护进程版）
==========================================================
用法:
    python main.py                  # 启动全部服务（Web + 调度器）
    python main.py --web            # 仅启动 Web
    python main.py --scheduler      # 仅启动调度器
    python main.py --fetch          # 执行一次数据抓取
    python main.py --init-db        # 初始化数据库 + 拉取历史数据
    python main.py --daemon         # 守护进程模式（后台运行）
    python main.py --pid-file=PATH  # 指定 PID 文件路径
"""

import argparse
import logging
import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import load_config
from core.database import Database
from core.fetcher import DataFetcher
from core.scheduler import TaskScheduler
from monitor.indicators import IndicatorCalculator
from monitor.alerts import Alerter
from core.report_generator import ReportGenerator
from notify import Notifier
from utils.health import HealthChecker, PIDFile, GracefulShutdown

# ---- 日志配置（支持文件轮转） ----
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(LOG_DIR, "etf_momentum.log"),
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("main")

# ---- 全局组件（优雅关闭时使用） ----
_global_components = {}


def init_db(config):
    logger.info("初始化数据库...")
    db = Database(
        config["database"]["path"],
        backup_enabled=config.get("database", {}).get("backup_enabled", True),
    )
    fetcher = DataFetcher(config, database=db)
    logger.info("同步 %d 只标的历史数据...", len(fetcher.symbols))
    fetcher.fetch_all_history()
    logger.info("数据库初始化完成")
    return db


def run_fetch(config):
    db = Database(config["database"]["path"])
    fetcher = DataFetcher(config, database=db)
    indicator_calc = IndicatorCalculator(config, database=db)
    alerter = Alerter(config, database=db)
    notifier = Notifier(config)

    logger.info("开始数据抓取...")
    quotes = fetcher.fetch_all_realtime()
    for quote in quotes:
        try:
            indicators = indicator_calc.compute(quote)
            if indicators:
                db.insert_indicators(quote["symbol"], indicators)
            alerts = alerter.check(quote, indicators)
            for alert in alerts:
                db.insert_alert(alert)
                notifier.send_alert(alert)
        except Exception as e:
            logger.error("处理 %s 数据异常: %s", quote.get("symbol"), e)

    logger.info("抓取完成: %d 只标的, %d 条告警", len(quotes), db.count_alerts_today())


def run_all(config, shutdown_handler: GracefulShutdown = None):
    """启动 Web + 调度器，支持优雅关闭。"""
    db = Database(
        config["database"]["path"],
        backup_enabled=config.get("database", {}).get("backup_enabled", True),
        backup_dir=config.get("database", {}).get("backup_dir", "data/backups"),
    )
    fetcher = DataFetcher(config, database=db)
    indicator_calc = IndicatorCalculator(config, database=db)
    alerter = Alerter(config, database=db)
    notifier = Notifier(config)
    health_checker = HealthChecker()

    # 注册健康检查
    health_checker.register("database", db.health_check)

    # 创建日报生成器
    report_gen = ReportGenerator(config, db)

    # 存储全局引用
    _global_components.update({
        "db": db, "fetcher": fetcher, "notifier": notifier,
        "health_checker": health_checker,
        "report_generator": report_gen,
    })

    # 启动调度器
    scheduler = TaskScheduler(
        config, fetcher, indicator_calc, alerter, notifier,
        report_generator=report_gen,
        health_checker=health_checker, db=db,
    )
    scheduler.start()
    _global_components["scheduler"] = scheduler
    notifier.send_startup(len(fetcher.symbols))

    # 注册关闭回调
    if shutdown_handler:
        shutdown_handler.register(lambda: scheduler.stop(), "scheduler")
        shutdown_handler.register(lambda: db.checkpoint_now(), "db_checkpoint")
        shutdown_handler.register(lambda: db.backup(), "db_backup")

    # 启动 Web 服务（主线程）
    from web.app import app
    web_config = config.get("web", {})
    host = web_config.get("host", "0.0.0.0")
    port = web_config.get("port", 5000)
    use_production = web_config.get("production_server", False)

    logger.info("Web 服务启动: http://%s:%s (生产模式: %s)", host, port, use_production)
    _global_components["web"] = app

    try:
        if use_production:
            from waitress import serve
            serve(app, host=host, port=port, threads=4)
        else:
            app.run(host=host, port=port, debug=False, use_reloader=False)
    finally:
        if shutdown_handler:
            shutdown_handler.shutdown()


def run_scheduler_only(config, shutdown_handler: GracefulShutdown = None):
    """仅运行调度器（适合拆分部署）。"""
    db = Database(config["database"]["path"])
    fetcher = DataFetcher(config, database=db)
    indicator_calc = IndicatorCalculator(config, database=db)
    alerter = Alerter(config, database=db)
    notifier = Notifier(config)

    report_gen = ReportGenerator(config, db)
    scheduler = TaskScheduler(config, fetcher, indicator_calc, alerter, notifier, report_generator=report_gen, db=db)
    scheduler.start()
    notifier.send_startup(len(fetcher.symbols))

    if shutdown_handler:
        shutdown_handler.register(lambda: scheduler.stop(), "scheduler")

    logger.info("调度器独立运行中，按 Ctrl+C 退出...")
    try:
        while not (shutdown_handler and shutdown_handler.is_shutting_down):
            time.sleep(10)
    finally:
        if shutdown_handler:
            shutdown_handler.shutdown()
        else:
            scheduler.stop()


def run_daemon(config):
    """守护进程模式：后台运行，输出重定向到日志文件。"""
    import signal

    # 忽略终端信号
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    # fork 子进程
    pid = os.fork()
    if pid > 0:
        logger.info("守护进程已启动 (PID=%d)", pid)
        sys.exit(0)

    # 子进程：脱离终端
    os.setsid()
    os.umask(0o022)

    # 二次 fork 防止重新获取终端
    pid2 = os.fork()
    if pid2 > 0:
        sys.exit(0)

    # 重定向标准流到日志
    log_path = os.path.join(LOG_DIR, "daemon.log")
    sys.stdout = open(log_path, "a")
    sys.stderr = open(log_path, "a")
    sys.stdin = open(os.devnull, "r")

    # 设置日志（仅文件输出）
    root_logger = logging.getLogger()
    for h in root_logger.handlers[:]:
        if isinstance(h, logging.StreamHandler) and h.stream in (sys.__stdout__, sys.__stderr__):
            root_logger.removeHandler(h)
    root_logger.addHandler(logging.FileHandler(log_path, encoding="utf-8"))

    # PID 文件
    pid_path = config.get("daemon", {}).get("pid_file", "etf_momentum.pid")
    pid_file = PIDFile(pid_path)
    if not pid_file.acquire():
        sys.exit(1)

    logger.info("守护进程模式启动 (PID=%d)", os.getpid())
    shutdown = GracefulShutdown()
    shutdown.setup_signals()
    shutdown.register(pid_file.release, "pid_file")

    run_all(config, shutdown_handler=shutdown)


def main():
    parser = argparse.ArgumentParser(description="ETF Momentum 量化交易辅助系统")
    parser.add_argument("--web", action="store_true", help="仅启动 Web")
    parser.add_argument("--scheduler", action="store_true", help="仅启动调度器")
    parser.add_argument("--fetch", action="store_true", help="执行一次数据抓取")
    parser.add_argument("--init-db", action="store_true", help="初始化数据库")
    parser.add_argument("--daemon", action="store_true", help="守护进程模式")
    parser.add_argument("--pid-file", default=None, help="PID 文件路径")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--get-chat-id", action="store_true", help="获取Telegram chat_id并保存到配置")
    args = parser.parse_args()

    config = load_config(args.config)

    # PID 文件优先
    if args.pid_file:
        config.setdefault("daemon", {})["pid_file"] = args.pid_file

    if args.get_chat_id:
        from notify.telegram import TelegramNotifier
        tn = TelegramNotifier(config)
        chat_id = tn.get_chat_id()
        if chat_id:
            print(f"chat_id: {chat_id}")
            # Auto-save to config.yaml if exists
            import yaml
            cfg_path = args.config or "config/config.yaml"
            try:
                with open(cfg_path, "r", encoding="utf-8") as f2:
                    yc = yaml.safe_load(f2)
                yc.setdefault("telegram", {})["chat_id"] = chat_id
                with open(cfg_path, "w", encoding="utf-8") as f2:
                    yaml.dump(yc, f2, allow_unicode=True, default_flow_style=False)
                print(f"Saved chat_id to {cfg_path}")
            except Exception as e:
                print(f"Auto-save failed: {e}")
                print(f"Please manually set chat_id in config: {chat_id}")
        else:
            print("No messages found. Please send /start to @linsey_stock_bot on Telegram first.")
    elif args.init_db:
        init_db(config)
    elif args.fetch:
        run_fetch(config)
    elif args.daemon:
        run_daemon(config)
    elif args.web:
        run_scheduler = config.get("web", {}).get("also_run_scheduler", False)
        if run_scheduler:
            shutdown = GracefulShutdown()
            shutdown.setup_signals()
            t = threading.Thread(target=run_scheduler_only, args=(config, shutdown), daemon=True)
            t.start()
        from web.app import run as web_run
        web_run()
    elif args.scheduler:
        shutdown = GracefulShutdown()
        shutdown.setup_signals()
        run_scheduler_only(config, shutdown_handler=shutdown)
    else:
        shutdown = GracefulShutdown()
        shutdown.setup_signals()
        run_all(config, shutdown_handler=shutdown)


if __name__ == "__main__":
    main()
