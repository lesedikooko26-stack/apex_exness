"""
Risk Manager: lot sizing, daily loss limit, auto-shutoff.
"""
import logging
from datetime import datetime, date
from typing import List, Optional

from core.models import TradeSignal, Position, DailyStats, Direction
from config import RiskConfig

logger = logging.getLogger(__name__)


class RiskManager:

    def __init__(self, config: RiskConfig):
        self.config = config
        self.daily_stats = DailyStats(
            date             = str(date.today()),
            starting_balance = config.account_size,
            current_balance  = config.account_size,
        )
        self._high_watermark = config.account_size
        self._is_shutdown    = False

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown or self.daily_stats.shutoff_triggered

    def check_daily_shutoff(self) -> bool:
        if self._is_shutdown:
            return True
        loss_pct = (self.daily_stats.realized_pnl / self.daily_stats.starting_balance * 100)
        if loss_pct <= -self.config.max_daily_loss_pct:
            logger.critical(f"DAILY LOSS LIMIT {loss_pct:.2f}% — shutting down!")
            self.daily_stats.shutoff_triggered = True
            self._is_shutdown = True
            return True
        return False

    def manual_shutdown(self):
        self._is_shutdown = True
        logger.warning("Manual shutdown activated")

    def reset_daily(self, new_balance: float):
        self._is_shutdown = False
        self.daily_stats  = DailyStats(
            date             = str(date.today()),
            starting_balance = new_balance,
            current_balance  = new_balance,
        )
        self._high_watermark = new_balance
        logger.info(f"Daily stats reset. Balance: ${new_balance:,.2f}")

    def validate_signal(self, signal: TradeSignal, open_positions: List[Position]) -> tuple:
        if self.is_shutdown:
            return False, "Trading is shutdown"
        if self.check_daily_shutoff():
            return False, "Daily loss limit reached"
        open_count = len([p for p in open_positions if p.is_open])
        if open_count >= self.config.max_open_positions:
            return False, f"Max open positions ({self.config.max_open_positions}) reached"
        open_tickers = {p.ticker for p in open_positions if p.is_open}
        if signal.ticker in open_tickers:
            return False, f"Already have position in {signal.ticker}"
        if signal.reward_risk_ratio < self.config.min_reward_risk_ratio:
            return False, f"R/R {signal.reward_risk_ratio:.2f} below min {self.config.min_reward_risk_ratio}"
        return True, "OK"

    def record_trade_close(self, position: Position):
        pnl = position.realized_pnl
        self.daily_stats.realized_pnl   += pnl
        self.daily_stats.current_balance += pnl
        self.daily_stats.total_trades   += 1
        if pnl > 0:
            self.daily_stats.winning_trades += 1
        else:
            self.daily_stats.losing_trades  += 1
        if self.daily_stats.current_balance > self._high_watermark:
            self._high_watermark = self.daily_stats.current_balance
        dd = (self._high_watermark - self.daily_stats.current_balance) / self._high_watermark * 100
        self.daily_stats.max_drawdown = max(self.daily_stats.max_drawdown, dd)
        logger.info(
            f"Trade closed: {position.ticker} | PnL: ${pnl:+,.2f} | "
            f"Daily: ${self.daily_stats.realized_pnl:+,.2f} | "
            f"W/L: {self.daily_stats.winning_trades}/{self.daily_stats.losing_trades}"
        )

    def update_unrealized(self, positions: List[Position]):
        self.daily_stats.unrealized_pnl = sum(p.unrealized_pnl for p in positions if p.is_open)

    def get_stats(self) -> DailyStats:
        return self.daily_stats
