"""
Order Manager: Forex position lifecycle.
Fixed: execution failure debugging, safer type casting, better error messages.
"""
import csv
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.models import Position, TradeSignal, Direction, SignalStatus
from core.data import get_pip_size, price_to_pips
from risk.risk_manager import RiskManager
from config import AppConfig

logger = logging.getLogger(__name__)

try:
    from trade_logger import setup_csv, write_csv_row, refresh_excel
    FANCY_LOGGER = True
except ImportError:
    FANCY_LOGGER = False


class OrderManager:

    def __init__(self, broker, risk: RiskManager, config: AppConfig):
        self.broker     = broker
        self.risk       = risk
        self.config     = config
        self.positions: Dict[str, Position] = {}
        self._trade_num = 0
        self._setup_db()
        if FANCY_LOGGER:
            setup_csv(config.csv_log_path)
        else:
            self._setup_csv_basic()

    def _setup_db(self):
        Path(self.config.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.config.db_path, check_same_thread=False)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY, signal_id TEXT, ticker TEXT, strategy TEXT,
                direction TEXT, entry_price REAL, exit_price REAL, stop_price REAL,
                target_price REAL, lot_size REAL, realized_pnl REAL, unrealized_pnl REAL,
                mt5_ticket INTEGER, broker_order_id TEXT,
                opened_at TEXT, closed_at TEXT, is_open INTEGER, notes TEXT
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id TEXT PRIMARY KEY, ticker TEXT, strategy TEXT, direction TEXT,
                entry_price REAL, target_price REAL, stop_price REAL,
                lot_size REAL, stop_pips REAL, confidence REAL,
                rationale TEXT, indicators TEXT, status TEXT, created_at TEXT
            )
        """)
        self.db.commit()

    def _setup_csv_basic(self):
        Path(self.config.csv_log_path).parent.mkdir(parents=True, exist_ok=True)
        if not Path(self.config.csv_log_path).exists():
            with open(self.config.csv_log_path, "w", newline="") as f:
                csv.writer(f).writerow([
                    "Trade #", "Date", "Time", "Pair", "Strategy", "Direction",
                    "Lots", "Entry Price", "Exit Price", "Stop Loss", "Take Profit",
                    "Stop (pips)", "P&L ($)", "P&L (%)", "Duration (min)", "Result",
                    "R/R Ratio", "Reason Closed", "MT5 Ticket"
                ])

    def log_signal(self, signal: TradeSignal):
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(signal.id),
                    str(signal.ticker),
                    str(signal.strategy),
                    str(signal.direction.value),
                    float(signal.entry_price),
                    float(signal.target_price),
                    float(signal.stop_price),
                    float(signal.lot_size),
                    float(signal.stop_pips),
                    float(signal.confidence),
                    str(signal.rationale),
                    json.dumps(signal.indicators or {}),
                    str(signal.status.value),
                    signal.created_at.isoformat(),
                )
            )
            self.db.commit()
        except Exception as e:
            logger.error(f"Signal log error: {e}")

    def _is_trading_hours(self) -> bool:
        """
        Forex trades 24/5. Only block on weekends.
        Trading hours in config are used as a soft preference but
        the market itself determines if trading is possible via broker.is_market_open().
        """
        from datetime import timezone
        now = datetime.now(timezone.utc)
        # Block weekends (Saturday=5, Sunday=6)
        if now.weekday() in (5, 6):
            return False
        # Block Friday after 22:00 UTC (market close)
        if now.weekday() == 4 and now.hour >= 22:
            return False
        # Apply configured trading hours if set (0 start = no restriction)
        start = int(getattr(self.config.strategy, 'trading_hours_start', 0))
        end   = int(getattr(self.config.strategy, 'trading_hours_end', 23))
        if start == 0 and end >= 23:
            return True  # No restriction
        return start <= now.hour < end

    def execute_signal(self, signal: TradeSignal) -> Optional[Position]:
        if self.risk.is_shutdown:
            logger.warning("Trading shutdown — not executing")
            return None

        if not self._is_trading_hours():
            logger.info(f"Outside trading hours (07:00–21:00 UTC) — skipping {signal.ticker}")
            return None

        ok, reason = self.risk.validate_signal(signal, list(self.positions.values()))
        if not ok:
            logger.warning(f"Signal rejected [{signal.ticker}]: {reason}")
            return None

        # Safe type casts — prevents NoneType errors
        try:
            side         = "buy" if signal.direction == Direction.LONG else "sell"
            pip_size     = float(get_pip_size(signal.ticker))
            min_stop     = float(getattr(self.config.strategy, 'min_stop_pips', 8.0))
            min_rr       = float(self.config.risk.min_reward_risk_ratio)
            fixed_risk   = float(self.config.risk.fixed_risk_pct)
            account_size = float(self.config.risk.account_size)
            max_lots     = float(getattr(self.config.risk, 'max_lot_size', 0.5))
        except Exception as e:
            logger.error(f"Config cast error for {signal.ticker}: {e}")
            return None

        # Get live price from broker
        try:
            tick  = self.broker.get_tick(signal.ticker)
            if tick:
                price = float(tick["ask"]) if side == "buy" else float(tick["bid"])
            else:
                price = float(signal.entry_price)
                logger.warning(f"No tick for {signal.ticker} — using signal entry price {price:.5f}")
        except Exception as e:
            logger.error(f"Tick fetch error for {signal.ticker}: {e}")
            price = float(signal.entry_price)

        if price <= 0:
            logger.error(f"Invalid price {price} for {signal.ticker}")
            return None

        # Calculate stop distance
        raw_stop_pips = float(signal.stop_pips) if signal.stop_pips > 0 else 0.0
        if raw_stop_pips < min_stop:
            raw_stop_pips = min_stop

        # Recalculate SL/TP from live price
        if side == "buy":
            stop_price  = round(price - raw_stop_pips * pip_size, 5)
            take_profit = round(price + raw_stop_pips * min_rr * pip_size, 5)
        else:
            stop_price  = round(price + raw_stop_pips * pip_size, 5)
            take_profit = round(price - raw_stop_pips * min_rr * pip_size, 5)

        # Final direction sanity check
        if side == "buy":
            if stop_price >= price:
                stop_price  = round(price - min_stop * pip_size, 5)
                logger.warning(f"Fixed BUY stop for {signal.ticker}: {stop_price:.5f}")
            if take_profit <= price:
                take_profit = round(price + min_stop * 2.0 * pip_size, 5)
                logger.warning(f"Fixed BUY target for {signal.ticker}: {take_profit:.5f}")
        else:
            if stop_price <= price:
                stop_price  = round(price + min_stop * pip_size, 5)
                logger.warning(f"Fixed SELL stop for {signal.ticker}: {stop_price:.5f}")
            if take_profit >= price:
                take_profit = round(price - min_stop * 2.0 * pip_size, 5)
                logger.warning(f"Fixed SELL target for {signal.ticker}: {take_profit:.5f}")

        # Lot size calculation
        try:
            risk_dollars = account_size * (fixed_risk / 100.0)
            lot_size     = self.broker.calculate_lot_size(
                signal.ticker, risk_dollars, raw_stop_pips
            )
            lot_size = max(0.01, min(round(float(lot_size), 2), max_lots))
        except Exception as e:
            logger.error(f"Lot size calc error for {signal.ticker}: {e}")
            lot_size = 0.01

        logger.info(
            f"Placing order: {side.upper()} {lot_size} lots {signal.ticker} "
            f"@ ~{price:.5f} | SL:{stop_price:.5f} TP:{take_profit:.5f} | "
            f"Stop:{raw_stop_pips:.1f} pips | Risk:${risk_dollars:.2f}"
        )

        try:
            order = self.broker.place_market_order(
                symbol      = signal.ticker,
                lots        = lot_size,
                side        = side,
                stop_loss   = float(stop_price),
                take_profit = float(take_profit),
                comment     = f"APEX {signal.strategy[:10]}",
            )
        except Exception as e:
            logger.error(f"Broker order error for {signal.ticker}: {e}", exc_info=True)
            return None

        if not order:
            logger.error(f"Order returned None for {signal.ticker} — check broker logs")
            return None

        ticket   = order.get("ticket")
        order_id = str(order.get("id", ticket or "mock"))
        fill_px  = float(order.get("filled_avg_price", price))

        position = Position(
            signal_id       = signal.id,
            ticker          = signal.ticker,
            strategy        = signal.strategy,
            direction       = signal.direction,
            entry_price     = fill_px,
            current_price   = fill_px,
            stop_price      = float(stop_price),
            target_price    = float(take_profit),
            lot_size        = lot_size,
            mt5_ticket      = int(ticket) if ticket else None,
            broker_order_id = order_id,
            is_open         = True,
        )
        self.positions[signal.id] = position
        self._save_position(position)
        logger.info(
            f"Position opened: {signal.ticker} {side.upper()} {lot_size} lots | "
            f"Ticket:{ticket} | Entry:{fill_px:.5f}"
        )
        return position

    def confirm_fill(self, signal_id: str, lots: float, avg_price: float):
        pos = self.positions.get(signal_id)
        if pos:
            pos.lot_size    = float(lots)
            pos.entry_price = float(avg_price)
            pos.is_open     = True
            self._save_position(pos)

    def update_positions(self, price_map: Dict[str, float]):
        for pos in self.positions.values():
            if not pos.is_open:
                continue
            price = price_map.get(pos.ticker)
            if price:
                try:
                    pip_size = float(get_pip_size(pos.ticker))
                    pip_val  = float(self.broker.get_pip_value_per_lot(pos.ticker))
                    pos.update_pnl(float(price), pip_value=pip_val, pip_size=pip_size)
                    self._check_exit(pos, float(price))
                except Exception as e:
                    logger.error(f"Position update error {pos.ticker}: {e}")
        self.risk.update_unrealized(list(self.positions.values()))

    def _check_exit(self, pos: Position, price: float):
        if not pos.is_open:
            return
        if pos.direction == Direction.LONG:
            if price >= pos.target_price:
                logger.info(f"Target hit: {pos.ticker} @ {price:.5f}")
                self.close_position(pos.signal_id, price, "target_hit")
            elif price <= pos.stop_price:
                logger.info(f"Stop hit: {pos.ticker} @ {price:.5f}")
                self.close_position(pos.signal_id, price, "stop_hit")
        else:
            if price <= pos.target_price:
                logger.info(f"Target hit SHORT: {pos.ticker} @ {price:.5f}")
                self.close_position(pos.signal_id, price, "target_hit")
            elif price >= pos.stop_price:
                logger.info(f"Stop hit SHORT: {pos.ticker} @ {price:.5f}")
                self.close_position(pos.signal_id, price, "stop_hit")

    def close_position(self, signal_id: str, exit_price: float, reason: str = "manual") -> bool:
        pos = self.positions.get(signal_id)
        if not pos or not pos.is_open:
            return False

        try:
            if pos.mt5_ticket:
                self.broker.close_position(int(pos.mt5_ticket))
        except Exception as e:
            logger.error(f"Broker close error for ticket {pos.mt5_ticket}: {e}")

        try:
            pip_size = float(get_pip_size(pos.ticker))
            pip_val  = float(self.broker.get_pip_value_per_lot(pos.ticker))
            pip_diff = (float(exit_price) - float(pos.entry_price)) / pip_size
            if pos.direction == Direction.SHORT:
                pip_diff = -pip_diff
            pos.realized_pnl   = pip_diff * pip_val * float(pos.lot_size)
        except Exception as e:
            logger.error(f"PnL calc error for {pos.ticker}: {e}")
            pos.realized_pnl = 0.0

        pos.current_price  = float(exit_price)
        pos.unrealized_pnl = 0.0
        pos.is_open        = False
        pos.closed_at      = datetime.now()

        self.risk.record_trade_close(pos)
        self._save_position(pos)

        self._trade_num += 1
        stop_pips   = price_to_pips(pos.ticker, abs(pos.entry_price - pos.stop_price))
        target_pips = price_to_pips(pos.ticker, abs(pos.entry_price - pos.target_price))
        rr          = (target_pips / stop_pips) if stop_pips > 0 else 0.0

        if FANCY_LOGGER:
            try:
                write_csv_row(
                    path        = self.config.csv_log_path,
                    trade_num   = self._trade_num,
                    position    = pos,
                    exit_price  = float(exit_price),
                    reason      = reason,
                    stop_pips   = stop_pips,
                    target_pips = target_pips,
                    rr_ratio    = rr,
                )
                refresh_excel(self.config.csv_log_path)
            except Exception as e:
                logger.error(f"Trade log error: {e}")
        else:
            self._log_csv_basic(pos, float(exit_price), reason, stop_pips, rr)

        icon = "WIN ✅" if pos.realized_pnl > 0 else "LOSS ❌"
        logger.info(
            f"[{icon}] Closed: {pos.ticker} | "
            f"PnL: ${pos.realized_pnl:+,.2f} | Reason: {reason}"
        )
        return True

    def close_all_positions(self, reason: str = "manual"):
        for sid in list(self.positions.keys()):
            pos = self.positions[sid]
            if pos.is_open:
                price = self._get_price(pos.ticker) or pos.current_price
                self.close_position(sid, price, reason)

    def _log_csv_basic(self, pos, exit_price, reason, stop_pips, rr):
        try:
            duration = 0
            if pos.closed_at and pos.opened_at:
                duration = int((pos.closed_at - pos.opened_at).total_seconds() / 60)
            cost    = float(pos.entry_price) * float(pos.lot_size) * 100000
            pnl_pct = (pos.realized_pnl / cost * 100) if cost else 0.0
            result  = "WIN" if pos.realized_pnl > 0 else "LOSS"
            with open(self.config.csv_log_path, "a", newline="") as f:
                csv.writer(f).writerow([
                    self._trade_num,
                    str(pos.opened_at.date()),
                    pos.opened_at.strftime("%H:%M:%S"),
                    pos.ticker, pos.strategy, pos.direction.value,
                    pos.lot_size,
                    round(float(pos.entry_price), 5),
                    round(float(exit_price), 5),
                    round(float(pos.stop_price), 5),
                    round(float(pos.target_price), 5),
                    round(stop_pips, 1),
                    round(pos.realized_pnl, 2),
                    round(pnl_pct, 2),
                    duration, result,
                    round(rr, 2),
                    reason.replace("_", " ").title(),
                    pos.mt5_ticket or "—"
                ])
        except Exception as e:
            logger.error(f"CSV log error: {e}")

    def _save_position(self, pos: Position):
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(pos.id), str(pos.signal_id),
                    str(pos.ticker), str(pos.strategy),
                    str(pos.direction.value),
                    float(pos.entry_price), float(pos.current_price),
                    float(pos.stop_price),  float(pos.target_price),
                    float(pos.lot_size),
                    float(pos.realized_pnl), float(pos.unrealized_pnl),
                    int(pos.mt5_ticket) if pos.mt5_ticket else None,
                    str(pos.broker_order_id) if pos.broker_order_id else None,
                    pos.opened_at.isoformat(),
                    pos.closed_at.isoformat() if pos.closed_at else None,
                    1 if pos.is_open else 0,
                    ""
                )
            )
            self.db.commit()
        except Exception as e:
            logger.error(f"DB save error: {e}")

    def get_open_positions(self) -> List[Position]:
        return [p for p in self.positions.values() if p.is_open]

    def get_all_positions(self) -> List[Position]:
        return list(self.positions.values())

    def load_trade_history(self, limit: int = 50) -> List[dict]:
        try:
            cur  = self.db.execute(
                "SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?", (limit,)
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Load history error: {e}")
            return []

    def _get_price(self, ticker: str) -> Optional[float]:
        try:
            return self.broker.get_current_price(ticker)
        except Exception:
            return None