"""数据库模块 —— 基于 SQLite 的轻量化数据持久层（云端容错版）"""

import sqlite3
import os
import shutil
import logging
import time
from datetime import datetime, timedelta
from contextlib import contextmanager

from utils.retry import retry_on_failure

logger = logging.getLogger(__name__)


class Database:
    """SQLite 数据库封装，支持连接重试、WAL checkpoint、自动备份。"""

    def __init__(self, db_path: str = "data/etf_momentum.db",
                 backup_enabled: bool = True,
                 backup_dir: str = "data/backups",
                 backup_keep_days: int = 30,
                 wal_checkpoint_interval: int = 1000):
        self.db_path = db_path
        self.backup_enabled = backup_enabled
        self.backup_dir = backup_dir
        self.backup_keep_days = backup_keep_days
        self.wal_checkpoint_interval = wal_checkpoint_interval
        self._write_count = 0
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_tables()

    @contextmanager
    def _connect(self, max_retries: int = 3, retry_delay: float = 0.5):
        """获取数据库连接，支持连接重试。"""
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                conn = sqlite3.connect(self.db_path, timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=5000")
                yield conn
                conn.commit()
                self._write_count += 1
                self._maybe_checkpoint(conn)
                return
            except sqlite3.OperationalError as e:
                last_exc = e
                logger.warning("数据库连接失败 (第 %d/%d 次): %s", attempt, max_retries, e)
                if attempt < max_retries:
                    time.sleep(retry_delay * attempt)
                if 'conn' in locals():
                    try:
                        conn.rollback()
                    except Exception:
                        pass
            except Exception:
                if 'conn' in locals():
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                raise
            finally:
                if 'conn' in locals():
                    try:
                        conn.close()
                    except Exception:
                        pass

        raise RuntimeError(f"数据库连接失败（重试{max_retries}次）: {last_exc}")

    def _maybe_checkpoint(self, conn):
        """定期执行 WAL checkpoint，防止 WAL 文件无限增长。"""
        if self._write_count % self.wal_checkpoint_interval == 0:
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                logger.debug("WAL checkpoint 完成 (写入计数: %d)", self._write_count)
            except Exception as e:
                logger.warning("WAL checkpoint 失败: %s", e)

    def checkpoint_now(self):
        """强制执行 WAL checkpoint（可在定时任务中调用）。"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            logger.info("WAL checkpoint 强制执行完成")
        except Exception as e:
            logger.error("WAL checkpoint 强制失败: %s", e)

    def backup(self) -> str:
        """备份数据库到 backup_dir，返回备份文件路径。"""
        if not self.backup_enabled:
            return ""
        os.makedirs(self.backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.backup_dir, f"etf_momentum_{timestamp}.db")
        try:
            # 先做 checkpoint 确保 WAL 内容写入主文件
            src = sqlite3.connect(self.db_path)
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            # 备份
            dst = sqlite3.connect(backup_path)
            src.backup(dst)
            src.close()
            dst.close()
            logger.info("数据库备份完成: %s", backup_path)
            self._cleanup_old_backups()
            return backup_path
        except Exception as e:
            logger.error("数据库备份失败: %s", e)
            return ""

    def _cleanup_old_backups(self):
        """清理过期备份文件。"""
        cutoff = datetime.now() - timedelta(days=self.backup_keep_days)
        try:
            for fname in os.listdir(self.backup_dir):
                fpath = os.path.join(self.backup_dir, fname)
                if fname.startswith("etf_momentum_") and fname.endswith(".db"):
                    mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                    if mtime < cutoff:
                        os.remove(fpath)
                        logger.info("清理过期备份: %s", fname)
        except Exception as e:
            logger.warning("清理备份失败: %s", e)

    def health_check(self) -> tuple:
        """数据库健康检查，返回 (ok: bool, detail: str)。"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.execute("SELECT 1")
            # 检查表是否存在
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]
            conn.close()

            # 检查 WAL 文件大小
            wal_path = self.db_path + "-wal"
            wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
            wal_mb = wal_size / (1024 * 1024)

            detail = f"OK ({len(table_names)} tables"
            if wal_mb > 10:
                detail += f", WAL={wal_mb:.1f}MB ⚠️"
            detail += ")"
            return True, detail
        except Exception as e:
            return False, str(e)

    # ====== 以下是原有方法（保持不变） ======

    def _init_tables(self):
        """初始化数据表。"""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    timestamp DATETIME NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, amount REAL, change_pct REAL,
                    UNIQUE(symbol, timestamp)
                );
                CREATE TABLE IF NOT EXISTS indicators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp DATETIME NOT NULL,
                    ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL,
                    rsi REAL,
                    macd_dif REAL, macd_dea REAL, macd_hist REAL,
                    boll_upper REAL, boll_mid REAL, boll_lower REAL,
                    volume_ma REAL, volume_ratio REAL,
                    UNIQUE(symbol, timestamp)
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, name TEXT,
                    timestamp DATETIME NOT NULL,
                    alert_type TEXT NOT NULL,
                    level TEXT DEFAULT 'INFO',
                    message TEXT NOT NULL,
                    price REAL,
                    acknowledged INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, name TEXT,
                    timestamp DATETIME NOT NULL,
                    signal_type TEXT NOT NULL,
                    direction TEXT, price REAL,
                    reason TEXT, strength REAL DEFAULT 0.5
                );
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, name TEXT,
                    side TEXT NOT NULL,
                    quantity REAL, price REAL,
                    timestamp DATETIME NOT NULL,
                    status TEXT DEFAULT 'OPEN'
                );
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, date DATE NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, change_pct REAL,
                    ma5 REAL, ma10 REAL, ma20 REAL,
                    UNIQUE(symbol, date)
                );
                CREATE INDEX IF NOT EXISTS idx_quotes_symbol_ts ON quotes(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_indicators_symbol_ts ON indicators(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_alerts_symbol_ts ON alerts(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON signals(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_daily_summary_symbol_date ON daily_summary(symbol, date);
                CREATE TABLE IF NOT EXISTS daily_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE NOT NULL UNIQUE,
                    strategy_result TEXT,
                    data_input TEXT,
                    html_content TEXT,
                    position_advice TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS review_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    review_type TEXT NOT NULL,
                    html_content TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(trade_date);
                CREATE INDEX IF NOT EXISTS idx_review_reports_date ON review_reports(start_date, end_date);
            """)

    def insert_quote(self, symbol: str, data: dict) -> bool:
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO quotes (symbol, name, timestamp, open, high, low, close, volume, amount, change_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, data.get("name"), data["timestamp"],
                  data.get("open"), data.get("high"), data.get("low"),
                  data.get("close"), data.get("volume"), data.get("amount"),
                  data.get("change_pct")))
        return True

    def get_quotes(self, symbol: str, limit: int = 100, start: str = None, end: str = None):
        with self._connect() as conn:
            query = "SELECT * FROM quotes WHERE symbol = ?"
            params = [symbol]
            if start:
                query += " AND timestamp >= ?"
                params.append(start)
            if end:
                query += " AND timestamp <= ?"
                params.append(end)
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def insert_indicators(self, symbol: str, data: dict) -> bool:
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO indicators
                (symbol, timestamp, ma5, ma10, ma20, ma60, rsi, macd_dif, macd_dea, macd_hist,
                 boll_upper, boll_mid, boll_lower, volume_ma, volume_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, data["timestamp"],
                  data.get("ma5"), data.get("ma10"), data.get("ma20"), data.get("ma60"),
                  data.get("rsi"),
                  data.get("macd_dif"), data.get("macd_dea"), data.get("macd_hist"),
                  data.get("boll_upper"), data.get("boll_mid"), data.get("boll_lower"),
                  data.get("volume_ma"), data.get("volume_ratio")))
        return True

    def insert_alert(self, alert: dict) -> int:
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO alerts (symbol, name, timestamp, alert_type, level, message, price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (alert["symbol"], alert.get("name"), alert["timestamp"],
                  alert["alert_type"], alert.get("level", "INFO"),
                  alert["message"], alert.get("price")))
            return cur.lastrowid

    def get_recent_alerts(self, limit: int = 50):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def ack_alert(self, alert_id: int):
        with self._connect() as conn:
            conn.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))

    def insert_signal(self, signal: dict) -> int:
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO signals (symbol, name, timestamp, signal_type, direction, price, reason, strength)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (signal["symbol"], signal.get("name"), signal["timestamp"],
                  signal["signal_type"], signal.get("direction"),
                  signal.get("price"), signal.get("reason"), signal.get("strength", 0.5)))
            return cur.lastrowid

    def upsert_daily_summary(self, symbol: str, data: dict):
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_summary (symbol, date, open, high, low, close, volume, change_pct, ma5, ma10, ma20)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, data["date"],
                  data.get("open"), data.get("high"), data.get("low"), data.get("close"),
                  data.get("volume"), data.get("change_pct"),
                  data.get("ma5"), data.get("ma10"), data.get("ma20")))

    def count_alerts_today(self):
        today = datetime.now().strftime("%Y-%m-%d")
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE date(timestamp) = ?", (today,)
            ).fetchone()[0]

    def count_signals_today(self):
        today = datetime.now().strftime("%Y-%m-%d")
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM signals WHERE date(timestamp) = ?", (today,)
            ).fetchone()[0]

    def get_latest_quote(self, symbol: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM quotes WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                (symbol,)
            ).fetchone()
            return dict(row) if row else None

    def get_monitored_symbols(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol, name FROM quotes ORDER BY symbol"
            ).fetchall()
            return [dict(row) for row in rows]


    # ---- Daily/Review Report Operations ----
    def insert_daily_report(self, trade_date, strategy_result, data_input, html_content, position_advice=""):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_reports (trade_date, strategy_result, data_input, html_content, position_advice) VALUES (?, ?, ?, ?, ?)",
                (trade_date, strategy_result, data_input, html_content, position_advice))

    def get_daily_report(self, trade_date):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM daily_reports WHERE trade_date = ?", (trade_date,)).fetchone()
            return dict(row) if row else None

    def get_daily_reports(self, start_date=None, end_date=None, limit=30):
        with self._connect() as conn:
            query = "SELECT * FROM daily_reports WHERE 1=1"
            params = []
            if start_date:
                query += " AND trade_date >= ?"
                params.append(start_date)
            if end_date:
                query += " AND trade_date <= ?"
                params.append(end_date)
            query += " ORDER BY trade_date DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def get_recent_reports(self, limit=5):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM daily_reports ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def insert_review_report(self, start_date, end_date, review_type, html_content):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO review_reports (start_date, end_date, review_type, html_content) VALUES (?, ?, ?, ?)",
                (start_date, end_date, review_type, html_content))

    def get_review_reports(self, review_type=None, limit=10):
        with self._connect() as conn:
            query = "SELECT * FROM review_reports"
            params = []
            if review_type:
                query += " WHERE review_type = ?"
                params.append(review_type)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def get_daily_summary(self, symbol, limit=40):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_summary WHERE symbol = ? ORDER BY date ASC LIMIT ?", (symbol, limit)).fetchall()
            return [dict(row) for row in rows]
