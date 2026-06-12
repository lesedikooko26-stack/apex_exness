"""
APEX Trading Engine — Optimised for higher win rate
Changes: respects use_momentum_breakout / use_trend_continuation flags in config
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config import AppConfig, config as default_config
from core.data import MarketDataFetcher
from core.models import TradeSignal, SignalStatus
from strategies.trend_continuation import TrendContinuationStrategy
from execution.broker import create_broker
from execution.order_manager import OrderManager
from risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class TradingEngine:

    def __init__(self, config: AppConfig = None):
        self.config      = config or default_config
        self._running    = False
        self._thread:    Optional[threading.Thread] = None
        self._signals:   List[TradeSignal] = []
        self._lock       = threading.Lock()
        self._scan_count = 0
        self._last_scan: Optional[datetime] = None
        self._force_scan = True
        self._recent_signals: Dict[str, datetime] = {}
        self._signal_cooldown = timedelta(minutes=5)

        logger.info("Initializing APEX Trading Engine (Exness MT5)...")

        self.data_fetcher  = MarketDataFetcher()
        self.risk_manager  = RiskManager(self.config.risk)
        self.broker        = create_broker(self.config.broker)
        self.order_manager = OrderManager(self.broker, self.risk_manager, self.config)

        # Build strategy list based on config flags
        self.strategies = []

        use_mb = getattr(self.config.strategy, 'use_momentum_breakout', True)
        use_tc = getattr(self.config.strategy, 'use_trend_continuation', True)

        if use_mb:
            try:
                from strategies.momentum_breakout import MomentumBreakoutStrategy
                self.strategies.append(MomentumBreakoutStrategy(self.config, self.data_fetcher))
                logger.info("Strategy enabled: Momentum Breakout")
            except Exception as e:
                logger.error(f"Could not load Momentum Breakout: {e}")

        if use_tc:
            self.strategies.append(TrendContinuationStrategy(self.config, self.data_fetcher))
            logger.info("Strategy enabled: Trend Continuation")

        if not self.strategies:
            logger.error("No strategies enabled! Check config.strategy flags.")

        logger.info(f"Active strategies: {[s.NAME for s in self.strategies]}")
        logger.info(f"Tickers ({len(self.config.data.tickers)}): {', '.join(self.config.data.tickers)}")
        logger.info(
            f"Account: ${self.config.risk.account_size:,.0f} | "
            f"Risk/trade: {self.config.risk.fixed_risk_pct}% | "
            f"Max loss/day: {self.config.risk.max_daily_loss_pct}% | "
            f"Max positions: {self.config.risk.max_open_positions} | "
            f"Max lots: {self.config.risk.max_lot_size} | "
            f"Min stop: {self.config.strategy.min_stop_pips} pips | "
            f"ATR stop mult: {getattr(self.config.strategy, 'atr_stop_multiplier', 1.5)}x | "
            f"Scan: {self.config.strategy.scan_interval_seconds}s"
        )

    def is_market_open(self) -> bool:
        return self.broker.is_market_open()

    def is_weekend(self) -> bool:
        return datetime.utcnow().weekday() in (5, 6)

    def scan_once(self, force: bool = False) -> List[TradeSignal]:
        now = datetime.utcnow()

        if self.risk_manager.is_shutdown:
            logger.warning("Engine shutdown — daily loss limit hit. Not scanning.")
            return []

        market_open = self.is_market_open()

        if not market_open and not force and not self._force_scan:
            if self.is_weekend():
                logger.info("Weekend — market closed.")
            else:
                logger.info(f"Market closed ({now.strftime('%H:%M UTC')}). Waiting.")
            return []

        self._last_scan  = now
        self._scan_count += 1

        open_positions = self.order_manager.get_open_positions()
        open_tickers   = {p.ticker for p in open_positions}
        open_count     = len(open_positions)
        max_pos        = self.config.risk.max_open_positions

        logger.info(
            f"--- Scan #{self._scan_count} | "
            f"{now.strftime('%H:%M:%S UTC')} | "
            f"Positions: {open_count}/{max_pos} | "
            f"{'LIVE' if market_open else 'FORCED'} ---"
        )

        # Update P&L
        price_map = self._get_prices()
        if price_map:
            self.order_manager.update_positions(price_map)
        else:
            logger.warning("No prices fetched")

        # Skip scan if at max positions
        if open_count >= max_pos:
            logger.info(f"At max positions ({open_count}/{max_pos}) — waiting for a close")
            return []

        # Build scan list
        tickers_to_scan = [t for t in self.config.data.tickers if t not in open_tickers]
        self._clean_cooldowns()
        tickers_to_scan = [
            t for t in tickers_to_scan
            if not any(
                f"{t}:{s.NAME}" in self._recent_signals
                for s in self.strategies
            )
        ]

        if not tickers_to_scan:
            logger.info("All tickers in cooldown or already open")
            return []

        logger.info(f"Scanning: {', '.join(tickers_to_scan)}")

        new_signals = []
        for strategy in self.strategies:
            if len(self.order_manager.get_open_positions()) >= max_pos:
                logger.info("Max positions reached mid-scan — stopping")
                break
            logger.info(f"Running: {strategy.NAME}")
            try:
                signals = strategy.scan_all(tickers_to_scan)
                logger.info(f"{strategy.NAME}: {len(signals)} signal(s)")
                for sig in signals:
                    if self._is_duplicate(sig):
                        continue
                    new_signals.append(sig)
                    self.order_manager.log_signal(sig)
                    logger.info(
                        f"SIGNAL | {sig.ticker} {sig.direction.value} | "
                        f"Entry:{sig.entry_price:.5f} "
                        f"SL:{sig.stop_price:.5f} ({sig.stop_pips:.1f}p) "
                        f"TP:{sig.target_price:.5f} ({sig.target_pips:.1f}p) | "
                        f"R/R:{sig.reward_risk_ratio:.2f} | "
                        f"Lots:{sig.lot_size} | "
                        f"Conf:{sig.confidence:.0%}"
                    )
            except Exception as e:
                logger.error(f"Strategy error ({strategy.NAME}): {e}", exc_info=True)

        if not new_signals:
            logger.info("No signals this scan")
        else:
            logger.info(f"{len(new_signals)} signal(s) — threshold: 48%")

        executed = 0
        for sig in new_signals:
            if len(self.order_manager.get_open_positions()) >= max_pos:
                break
            if sig.confidence >= 0.48:
                logger.info(f"Executing: {sig.ticker} {sig.direction.value} (conf {sig.confidence:.0%})")
                self._execute(sig)
                executed += 1
                self._recent_signals[f"{sig.ticker}:{sig.strategy}"] = datetime.utcnow()
            else:
                logger.info(f"Skipped (conf {sig.confidence:.0%} < 48%): {sig.ticker}")

        if executed:
            logger.info(f"Executed {executed} order(s)")

        with self._lock:
            self._signals = new_signals + [
                s for s in self._signals
                if s.status == SignalStatus.ACTIVE
                and s.expires_at
                and s.expires_at > datetime.now()
            ]

        logger.info(f"--- Scan #{self._scan_count} complete ---")
        return new_signals

    def _clean_cooldowns(self):
        now     = datetime.utcnow()
        expired = [k for k, t in self._recent_signals.items()
                   if (now - t) > self._signal_cooldown]
        for k in expired:
            del self._recent_signals[k]

    def _execute(self, signal: TradeSignal):
        if self.risk_manager.is_shutdown:
            return
        position = self.order_manager.execute_signal(signal)
        if position:
            signal.status = SignalStatus.ACTIVE
            self.order_manager.confirm_fill(
                signal.id, signal.lot_size, signal.entry_price
            )
        else:
            logger.warning(f"Execution failed for {signal.ticker} — check logs above")

    def _is_duplicate(self, signal: TradeSignal) -> bool:
        with self._lock:
            for s in self._signals:
                if (s.ticker   == signal.ticker and
                    s.strategy == signal.strategy and
                    s.status not in (SignalStatus.CANCELLED, SignalStatus.EXPIRED)):
                    return True
        return False

    def _get_prices(self) -> Dict[str, float]:
        prices = {}
        for ticker in self.config.data.tickers:
            try:
                p = self.broker.get_current_price(ticker)
                if p and float(p) > 0:
                    prices[ticker] = float(p)
            except Exception:
                pass
        return prices

    def start(self, force_scan: bool = True):
        if self._running:
            logger.warning("Engine already running")
            return
        self._force_scan = force_scan
        self._running    = True
        self._thread     = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            f"Engine started | "
            f"Interval: {self.config.strategy.scan_interval_seconds}s | "
            f"Force scan: {force_scan}"
        )

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Engine stopped")

    def _loop(self):
        logger.info("Scan loop started")
        while self._running:
            try:
                self.scan_once(force=self._force_scan)
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
            time.sleep(self.config.strategy.scan_interval_seconds)
        logger.info("Scan loop ended")

    def emergency_stop(self):
        logger.critical("EMERGENCY STOP — closing all positions!")
        self.risk_manager.manual_shutdown()
        self.order_manager.close_all_positions("emergency_stop")
        self.stop()

    def get_state(self) -> dict:
        open_pos = self.order_manager.get_open_positions()
        all_pos  = self.order_manager.get_all_positions()
        stats    = self.risk_manager.get_stats()
        now      = datetime.utcnow()
        return {
            "engine": {
                "running":       self._running,
                "shutdown":      self.risk_manager.is_shutdown,
                "market_open":   self.is_market_open(),
                "force_scan":    self._force_scan,
                "scan_count":    self._scan_count,
                "last_scan":     self._last_scan.isoformat() if self._last_scan else None,
                "tickers":       self.config.data.tickers,
                "current_time":  now.strftime("%H:%M:%S UTC"),
                "open_count":    len(open_pos),
                "max_positions": self.config.risk.max_open_positions,
                "strategies":    [s.NAME for s in self.strategies],
            },
            "signals":   [s.to_dict() for s in self._signals[-20:]],
            "positions": {
                "open":   [p.to_dict() for p in open_pos],
                "closed": [p.to_dict() for p in all_pos if not p.is_open][-10:],
            },
            "stats":  stats.to_dict(),
            "config": {
                "account_size":           self.config.risk.account_size,
                "max_risk_per_trade_pct": self.config.risk.fixed_risk_pct,
                "max_daily_loss_pct":     self.config.risk.max_daily_loss_pct,
                "max_open_positions":     self.config.risk.max_open_positions,
                "max_lot_size":           self.config.risk.max_lot_size,
                "min_stop_pips":          self.config.strategy.min_stop_pips,
                "broker":                 "exness_mt5",
                "paper_trading":          not bool(self.config.broker.exness_login),
                "scan_interval":          self.config.strategy.scan_interval_seconds,
                "data_interval":          self.config.data.historical_interval,
            }
        }

    @property
    def signals(self) -> List[TradeSignal]:
        with self._lock:
            return list(self._signals)