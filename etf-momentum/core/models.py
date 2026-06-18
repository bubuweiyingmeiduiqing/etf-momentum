"""数据模型定义 —— 使用 dataclass 规范化数据结构"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Quote:
    symbol: str
    timestamp: datetime
    name: Optional[str] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None
    change_pct: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp
        return d


@dataclass
class Indicator:
    symbol: str
    timestamp: datetime
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    rsi: Optional[float] = None
    macd_dif: Optional[float] = None
    macd_dea: Optional[float] = None
    macd_hist: Optional[float] = None
    boll_upper: Optional[float] = None
    boll_mid: Optional[float] = None
    boll_lower: Optional[float] = None
    volume_ma: Optional[float] = None
    volume_ratio: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp
        return d


@dataclass
class Alert:
    symbol: str
    alert_type: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    name: Optional[str] = None
    level: str = "INFO"
    price: Optional[float] = None
    acknowledged: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp
        return d


@dataclass
class Signal:
    symbol: str
    signal_type: str
    timestamp: datetime = field(default_factory=datetime.now)
    name: Optional[str] = None
    direction: Optional[str] = None
    price: Optional[float] = None
    reason: Optional[str] = None
    strength: float = 0.5

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp
        return d
