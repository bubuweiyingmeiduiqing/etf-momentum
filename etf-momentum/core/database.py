"""数据库模块 —— 基于 SQLite 的轻量化数据持久层"""

import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager


class Database:
    """SQLite 数据库封装，提供连接池式管理和便捷查询。"""

    def __init__(self, db_path: str = "data/etf_momentum.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_tables()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self):
        """初始化数据表。"""
        with self._connect() as conn:
            conn.executescript("""
                -- 行情快照表
                CREATE TABLE IF NOT EXISTS quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    timestamp DATETIME NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    change_pct REAL,
                    UNIQUE(symbol, timestamp)
                );

                -- 指标计算结果表
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

                -- 告警记录表
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    timestamp DATETIME NOT NULL,
                    alert_type TEXT NOT NULL,
                    level TEXT DEFAULT 'INFO',
                    message TEXT NOT NULL,
                    price REAL,
                    acknowledged INTEGER DEFAULT 0
                );

                -- 交易信号表
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    timestamp DATETIME NOT NULL,
                    signal_type TEXT NOT NULL,
                    direction TEXT,
                    price REAL,
                    reason TEXT,
                    strength REAL DEFAULT 0.5
                );

                -- 持仓记录表
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    side TEXT NOT NULL,
                    quantity REAL,
                    price REAL,
                    timestamp DATETIME NOT NULL,
                    status TEXT DEFAULT 'OPEN'
                );

                -- 日统计汇总表
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date DATE NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, change_pct REAL,
                    ma5 REAL, ma10 REAL, ma20 REAL,
                    UNIQUE(symbol, date)
                );

                -- 索引
                CREATE INDEX IF NOT EXISTS idx_quotes_symbol_ts ON quotes(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_indicators_symbol_ts ON indicators(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_alerts_symbol_ts ON alerts(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON signals(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_daily_summary_symbol_date ON daily_summary(symbol, date);
            """)

    # ---- 行情操作 ----
    def insert_quote(self, symbol: str, data: dict) -> bool:
        """插入一条行情快照。"""
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO quotes (symbol, name, timestamp, open, high, low, close, volume, amount, change_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, data.get("name"), data["timestamp"],
                data.get("open"), data.get("high"), data.get("low"), data.get("close"),
                data.get("volume"), data.get("amount"), data.get("change_pct")
            ))
        return True

    def get_quotes(self, symbol: str, start: str = None, end: str = None, limit: int = 100):
        """查询行情数据。"""
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

    # ---- 指标操作 ----
    def insert_indicators(self, symbol: str, data: dict) -> bool:
        """插入一条指标数据。"""
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO indicators
                (symbol, timestamp, ma5, ma10, ma20, ma60, rsi, macd_dif, macd_dea, macd_hist,
                 boll_upper, boll_mid, boll_lower, volume_ma, volume_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, data["timestamp"],
                data.get("ma5"), data.get("ma10"), data.get("ma20"), data.get("ma60"),
                data.get("rsi"),
                data.get("macd_dif"), data.get("macd_dea"), data.get("macd_hist"),
                data.get("boll_upper"), data.get("boll_mid"), data.get("boll_lower"),
                data.get("volume_ma"), data.get("volume_ratio")
            ))
        return True

    # ---- 告警操作 ----
    def insert_alert(self, alert: dict) -> int:
        """插入一条告警记录，返回告警 ID。"""
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO alerts (symbol, name, timestamp, alert_type, level, message, price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                alert["symbol"], alert.get("name"), alert["timestamp"],
                alert["alert_type"], alert.get("level", "INFO"),
                alert["message"], alert.get("price")
            ))
            return cur.lastrowid

    def get_recent_alerts(self, limit: int = 50):
        """获取最近告警。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def ack_alert(self, alert_id: int):
        """确认告警。"""
        with self._connect() as conn:
            conn.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))

    # ---- 信号操作 ----
    def insert_signal(self, signal: dict) -> int:
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO signals (symbol, name, timestamp, signal_type, direction, price, reason, strength)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal["symbol"], signal.get("name"), signal["timestamp"],
                signal["signal_type"], signal.get("direction"),
                signal.get("price"), signal.get("reason"), signal.get("strength", 0.5)
            ))
            return cur.lastrowid

    # ---- 日统计 ----
    def upsert_daily_summary(self, symbol: str, data: dict):
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_summary (symbol, date, open, high, low, close, volume, change_pct, ma5, ma10, ma20)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, data["date"],
                data.get("open"), data.get("high"), data.get("low"), data.get("close"),
                data.get("volume"), data.get("change_pct"),
                data.get("ma5"), data.get("ma10"), data.get("ma20")
            ))

    # ---- 统计查询 ----
    def count_alerts_today(self):
        """今日告警数。"""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE date(timestamp) = ?", (today,)
            ).fetchone()[0]

    def count_signals_today(self):
        """今日信号数。"""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM signals WHERE date(timestamp) = ?", (today,)
            ).fetchone()[0]

    def get_latest_quote(self, symbol: str) -> dict:
        """获取某标的最近一次行情。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM quotes WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                (symbol,)
            ).fetchone()
            return dict(row) if row else None

    def get_monitored_symbols(self) -> list:
        """获取所有已有数据的标的列表。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol, name FROM quotes ORDER BY symbol"
            ).fetchall()
            return [dict(row) for row in rows]
