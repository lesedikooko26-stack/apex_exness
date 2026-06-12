"""
Core models for Exness MT5 — lot-based sizing, pip P&L.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class Direction(str, Enum):
    LONG  = "LONG"
    SHORT = "SHORT"


class SignalStatus(str, Enum):
    PENDING   = "pending"
    ACTIVE    = "active"
    FILLED    = "filled"
    CANCELLED = "cancelled"
    EXPIRED   = "expired"


@dataclass
class TradeSignal:
    id:            str       = field(default_factory=lambda: str(uuid.uuid4())[:8])
    ticker:        str       = ""
    strategy:      str       = ""
    direction:     Direction = Direction.LONG
    entry_price:   float = 0.0
    target_price:  float = 0.0
    stop_price:    float = 0.0
    current_price: float = 0.0
    lot_size:          float = 0.01
    stop_pips:         float = 0.0
    target_pips:       float = 0.0
    risk_dollars:      float = 0.0
    risk_pct:          float = 0.0
    reward_risk_ratio: float = 0.0
    confidence:  float = 0.0
    rationale:   str   = ""
    indicators:  dict  = field(default_factory=dict)
    status:     SignalStatus       = SignalStatus.PENDING
    created_at: datetime           = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "ticker":           self.ticker,
            "strategy":         self.strategy,
            "direction":        self.direction.value,
            "entry_price":      round(self.entry_price,       5),
            "target_price":     round(self.target_price,      5),
            "stop_price":       round(self.stop_price,        5),
            "current_price":    round(self.current_price,     5),
            "lot_size":         round(self.lot_size,          2),
            "stop_pips":        round(self.stop_pips,         1),
            "target_pips":      round(self.target_pips,       1),
            "risk_dollars":     round(self.risk_dollars,      2),
            "risk_pct":         round(self.risk_pct,          2),
            "reward_risk_ratio":round(self.reward_risk_ratio, 2),
            "confidence":       round(self.confidence,        2),
            "rationale":        self.rationale,
            "status":           self.status.value,
            "created_at":       self.created_at.isoformat(),
        }


@dataclass
class Position:
    id:            str       = field(default_factory=lambda: str(uuid.uuid4())[:8])
    signal_id:     str       = ""
    ticker:        str       = ""
    strategy:      str       = ""
    direction:     Direction = Direction.LONG
    entry_price:   float = 0.0
    current_price: float = 0.0
    stop_price:    float = 0.0
    target_price:  float = 0.0
    lot_size:      float = 0.0
    mt5_ticket:      Optional[int] = None
    broker_order_id: Optional[str] = None
    stop_order_id:   Optional[str] = None
    unrealized_pnl:     float = 0.0
    unrealized_pnl_pct: float = 0.0
    realized_pnl:       float = 0.0
    opened_at:  datetime           = field(default_factory=datetime.now)
    closed_at:  Optional[datetime] = None
    is_open:    bool               = True

    def update_pnl(self, current_price: float, pip_value: float = 10.0, pip_size: float = 0.0001):
        self.current_price = current_price
        if pip_size <= 0:
            pip_size = 0.0001
        pip_diff = (current_price - self.entry_price) / pip_size
        if self.direction == Direction.SHORT:
            pip_diff = -pip_diff
        self.unrealized_pnl = pip_diff * pip_value * self.lot_size
        cost = self.entry_price * self.lot_size * 100000
        self.unrealized_pnl_pct = (self.unrealized_pnl / cost * 100) if cost else 0.0

    def to_dict(self) -> dict:
        return {
            "id":                 self.id,
            "ticker":             self.ticker,
            "strategy":           self.strategy,
            "direction":          self.direction.value,
            "entry_price":        round(self.entry_price,        5),
            "current_price":      round(self.current_price,      5),
            "stop_price":         round(self.stop_price,         5),
            "target_price":       round(self.target_price,       5),
            "lot_size":           round(self.lot_size,           2),
            "mt5_ticket":         self.mt5_ticket,
            "unrealized_pnl":     round(self.unrealized_pnl,     2),
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct, 2),
            "realized_pnl":       round(self.realized_pnl,       2),
            "is_open":            self.is_open,
            "opened_at":          self.opened_at.isoformat(),
            "closed_at":          self.closed_at.isoformat() if self.closed_at else None,
        }


@dataclass
class DailyStats:
    date:              str   = ""
    starting_balance:  float = 0.0
    current_balance:   float = 0.0
    realized_pnl:      float = 0.0
    unrealized_pnl:    float = 0.0
    total_trades:      int   = 0
    winning_trades:    int   = 0
    losing_trades:     int   = 0
    max_drawdown:      float = 0.0
    shutoff_triggered: bool  = False

    @property
    def win_rate(self) -> float:
        return (self.winning_trades / self.total_trades * 100) if self.total_trades else 0.0

    @property
    def daily_pnl_pct(self) -> float:
        return (self.realized_pnl / self.starting_balance * 100) if self.starting_balance else 0.0

    def to_dict(self) -> dict:
        return {
            "date":              self.date,
            "starting_balance":  self.starting_balance,
            "current_balance":   self.current_balance,
            "realized_pnl":      round(self.realized_pnl,   2),
            "unrealized_pnl":    round(self.unrealized_pnl, 2),
            "total_trades":      self.total_trades,
            "winning_trades":    self.winning_trades,
            "losing_trades":     self.losing_trades,
            "win_rate":          round(self.win_rate,        1),
            "daily_pnl_pct":     round(self.daily_pnl_pct,  2),
            "max_drawdown":      round(self.max_drawdown,    2),
            "shutoff_triggered": self.shutoff_triggered,
        }
