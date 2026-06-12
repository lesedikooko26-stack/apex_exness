"""
Exness MT5 Broker + MockBroker fallback.
MT5 must be running and logged into your Exness account before starting.
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config import BrokerConfig

logger = logging.getLogger(__name__)


class ExnessMT5Broker:
    MAGIC = 20240101

    def __init__(self, config: BrokerConfig):
        self.config     = config
        self._mt5       = None
        self._connected = False
        self._connect()

    def _connect(self):
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
        except ImportError:
            logger.error("MetaTrader5 not installed. Run: pip install MetaTrader5 (Windows only)")
            return

        ok = self._mt5.initialize(path=self.config.mt5_path) if self.config.mt5_path else self._mt5.initialize()
        if not ok:
            logger.error(f"MT5 initialize() failed: {self._mt5.last_error()}")
            return

        ok = self._mt5.login(
            login    = int(self.config.exness_login),
            password = self.config.exness_password,
            server   = self.config.exness_server,
        )
        if not ok:
            logger.error(
                f"MT5 login failed for account {self.config.exness_login}.\n"
                f"Error: {self._mt5.last_error()}\n"
                f"Check login number, password, and server name (e.g. Exness-MT5Trial7)"
            )
            self._mt5.shutdown()
            return

        self._connected = True
        info = self._mt5.account_info()
        logger.info(
            f"Connected to Exness MT5 | Account: {info.login} | "
            f"Balance: {info.currency} {info.balance:,.2f} | "
            f"Leverage: 1:{info.leverage} | Server: {info.server}"
        )

    def _ok(self) -> bool:
        if not self._connected or self._mt5 is None:
            logger.error("MT5 not connected — is MetaTrader 5 running and logged in?")
            return False
        return True

    def disconnect(self):
        if self._mt5:
            self._mt5.shutdown()
        self._connected = False

    def get_account(self) -> dict:
        if not self._ok():
            return {}
        info = self._mt5.account_info()
        if info is None:
            return {}
        return {
            "balance":     info.balance,
            "equity":      info.equity,
            "free_margin": info.margin_free,
            "profit":      info.profit,
            "currency":    info.currency,
            "leverage":    info.leverage,
        }

    def get_balance(self) -> float:
        return self.get_account().get("balance", 0.0)

    def get_free_margin(self) -> float:
        return self.get_account().get("free_margin", 0.0)

    def _select(self, symbol: str) -> bool:
        if not self._ok():
            return False
        return self._mt5.symbol_select(symbol, True)

    def get_tick(self, symbol: str) -> Optional[dict]:
        if not self._ok():
            return None
        self._select(symbol)
        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"No tick for {symbol}")
            return None
        return {"bid": tick.bid, "ask": tick.ask, "last": tick.last}

    def get_current_price(self, symbol: str) -> Optional[float]:
        tick = self.get_tick(symbol)
        return ((tick["bid"] + tick["ask"]) / 2) if tick else None

    def get_pip_value_per_lot(self, symbol: str) -> float:
        if not self._ok():
            return 10.0
        self._select(symbol)
        info = self._mt5.symbol_info(symbol)
        if info is None:
            return 10.0
        point      = info.point
        tick_value = info.trade_tick_value
        tick_size  = info.trade_tick_size
        pip_size   = point * 10 if info.digits in (3, 5) else point
        return (tick_value * (pip_size / tick_size)) if tick_size > 0 else 10.0

    def calculate_lot_size(self, symbol: str, risk_dollars: float, stop_pips: float) -> float:
        if stop_pips <= 0:
            return 0.01
        if not self._ok():
            return 0.01
        self._select(symbol)
        info = self._mt5.symbol_info(symbol)
        if info is None:
            return 0.01
        pip_value = self.get_pip_value_per_lot(symbol)
        raw_lots  = risk_dollars / (stop_pips * pip_value)
        step = info.volume_step
        lots = round(round(raw_lots / step) * step, 2)
        lots = max(lots, info.volume_min)
        lots = min(lots, info.volume_max)
        return lots

    def _send(self, request: dict) -> Optional[dict]:
        if not self._ok():
            return None
        mt5   = self._mt5
        check = mt5.order_check(request)
        if check is None:
            logger.error(f"order_check None: {mt5.last_error()}")
            return None
        if check.retcode != 0:
            logger.error(f"order_check failed [{check.retcode}]: {check.comment}")
            return None
        result = mt5.order_send(request)
        if result is None:
            logger.error(f"order_send None: {mt5.last_error()}")
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order rejected [{result.retcode}]: {result.comment}")
            return None
        logger.info(f"Order OK | Ticket: {result.order} | Vol: {result.volume} | Price: {result.price:.5f}")
        return {
            "id": str(result.order), "ticket": result.order,
            "volume": result.volume, "price": result.price,
            "status": "filled", "filled_qty": result.volume, "filled_avg_price": result.price,
        }

    def place_market_order(self, symbol: str, lots: float, side: str,
                           stop_loss: float = 0.0, take_profit: float = 0.0,
                           comment: str = "APEX") -> Optional[dict]:
        if not self._ok():
            return None
        mt5  = self._mt5
        tick = self.get_tick(symbol)
        if not tick:
            return None
        order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
        price      = tick["ask"] if side == "buy" else tick["bid"]

        if side == "buy":
            if stop_loss != 0 and stop_loss >= price:
                stop_loss = round(price * 0.998, 5)
            if take_profit != 0 and take_profit <= price:
                take_profit = round(price * 1.004, 5)
        else:
            if stop_loss != 0 and stop_loss <= price:
                stop_loss = round(price * 1.002, 5)
            if take_profit != 0 and take_profit >= price:
                take_profit = round(price * 0.996, 5)

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       float(lots),
            "type":         order_type,
            "price":        price,
            "sl":           float(stop_loss),
            "tp":           float(take_profit),
            "deviation":    20,
            "magic":        self.MAGIC,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        logger.info(
            f"Placing {side.upper()} {lots} lots {symbol} @ {price:.5f} | "
            f"SL: {stop_loss:.5f} | TP: {take_profit:.5f}"
        )
        return self._send(request)

    def modify_sl_tp(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        if not self._ok():
            return False
        request = {"action": self._mt5.TRADE_ACTION_SLTP, "position": ticket,
                   "sl": float(stop_loss), "tp": float(take_profit)}
        result = self._mt5.order_send(request)
        if result and result.retcode == self._mt5.TRADE_RETCODE_DONE:
            logger.info(f"Modified {ticket} | SL: {stop_loss:.5f} TP: {take_profit:.5f}")
            return True
        logger.error(f"Modify failed: {result.comment if result else self._mt5.last_error()}")
        return False

    def cancel_order(self, order_id: str) -> bool:
        if not self._ok():
            return False
        result = self._mt5.order_send({"action": self._mt5.TRADE_ACTION_REMOVE, "order": int(order_id)})
        return bool(result and result.retcode == self._mt5.TRADE_RETCODE_DONE)

    def get_positions(self) -> List[dict]:
        if not self._ok():
            return []
        positions = self._mt5.positions_get()
        if positions is None:
            return []
        return [
            {
                "id": str(p.ticket), "ticket": p.ticket, "symbol": p.symbol,
                "lots": p.volume, "side": "buy" if p.type == 0 else "sell",
                "open_price": p.price_open, "current_price": p.price_current,
                "sl": p.sl, "tp": p.tp, "profit": p.profit, "swap": p.swap,
                "open_time": datetime.fromtimestamp(p.time),
            }
            for p in positions if p.magic == self.MAGIC
        ]

    def close_position(self, ticket: int) -> bool:
        if not self._ok():
            return False
        mt5       = self._mt5
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.error(f"Position {ticket} not found")
            return False
        pos        = positions[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        tick       = self.get_tick(pos.symbol)
        if not tick:
            return False
        price = tick["bid"] if pos.type == 0 else tick["ask"]
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol,
            "volume": pos.volume, "type": close_type, "position": ticket,
            "price": price, "deviation": 20, "magic": self.MAGIC,
            "comment": "APEX Close", "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Closed position {ticket} @ {result.price:.5f}")
            return True
        logger.error(f"Close failed: {result.comment if result else mt5.last_error()}")
        return False

    def close_all_positions(self) -> int:
        closed = sum(1 for p in self.get_positions() if self.close_position(p["ticket"]))
        logger.info(f"Closed {closed} positions")
        return closed

    def get_orders(self) -> List[dict]:
        if not self._ok():
            return []
        orders = self._mt5.orders_get()
        if orders is None:
            return []
        return [
            {"id": str(o.ticket), "symbol": o.symbol, "lots": o.volume_current,
             "price": o.price_open, "sl": o.sl, "tp": o.tp}
            for o in orders if o.magic == self.MAGIC
        ]

    def is_market_open(self) -> bool:
        if not self._connected:
            return False
        terminal = self._mt5.terminal_info()
        if terminal is None or not terminal.connected:
            return False
        now = datetime.utcnow()
        if now.weekday() in (5, 6):
            return False
        if now.weekday() == 4 and now.hour >= 22:
            return False
        return True

    def get_history(self, days: int = 30) -> List[dict]:
        if not self._ok():
            return []
        from_date = datetime.now() - timedelta(days=days)
        deals     = self._mt5.history_deals_get(from_date, datetime.now())
        if deals is None:
            return []
        return [
            {"ticket": d.ticket, "symbol": d.symbol, "volume": d.volume,
             "price": d.price, "profit": d.profit, "swap": d.swap,
             "commission": d.commission, "time": datetime.fromtimestamp(d.time)}
            for d in deals if d.magic == self.MAGIC and d.entry == 1
        ]


class MockBroker:
    """
    Simulation broker — no real orders.
    Caches prices to avoid Yahoo Finance rate limiting.
    """
    MAGIC = 20240101

    # Fallback prices if Yahoo Finance is unavailable
    FALLBACK_PRICES = {
        "EURUSD": 1.08500, "GBPUSD": 1.27000, "USDJPY": 149.500,
        "USDCHF": 0.90000, "AUDUSD": 0.65000, "USDCAD": 1.36000,
        "XAUUSD": 2350.00, "BTCUSD": 67000.0,
        "US500":  5200.00, "NAS100": 18200.0,
    }

    def __init__(self, starting_balance: float = 10000):
        self.balance     = starting_balance
        self._ticket     = 1000
        self._positions: Dict[int, dict] = {}
        self._orders:    Dict[int, dict] = {}
        # Price cache — avoids hitting Yahoo Finance on every tick call
        self._price_cache:    Dict[str, float] = {}
        self._price_cache_ts: Dict[str, float] = {}
        self._price_cache_ttl = 30  # seconds — refresh every 30s not every tick
        logger.warning("MockBroker active — simulation mode, no real orders placed")

    def _next(self) -> int:
        self._ticket += 1
        return self._ticket

    def get_account(self) -> dict:
        return {"balance": self.balance, "equity": self.balance,
                "free_margin": self.balance, "currency": "USD", "leverage": 200}

    def get_balance(self) -> float:
        return self.balance

    def get_free_margin(self) -> float:
        return self.balance

    def get_tick(self, symbol: str) -> Optional[dict]:
        price = self.get_current_price(symbol)
        if price:
            spread = price * 0.0002
            return {"bid": round(price - spread, 5), "ask": round(price + spread, 5), "last": price}
        return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get price with caching — only hits Yahoo Finance every 30 seconds
        per symbol to avoid rate limiting.
        """
        now = time.time()
        cached_time = self._price_cache_ts.get(symbol, 0)

        # Return cached price if fresh enough
        if symbol in self._price_cache and (now - cached_time) < self._price_cache_ttl:
            return self._price_cache[symbol]

        # Try Yahoo Finance
        try:
            from core.data import MarketDataFetcher
            price = MarketDataFetcher().get_current_price(symbol)
            if price and price > 0:
                self._price_cache[symbol]    = price
                self._price_cache_ts[symbol] = now
                return price
        except Exception:
            pass

        # Use fallback price if Yahoo Finance fails
        fallback = self.FALLBACK_PRICES.get(symbol)
        if fallback:
            logger.debug(f"Using fallback price for {symbol}: {fallback}")
            self._price_cache[symbol]    = fallback
            self._price_cache_ts[symbol] = now
            return fallback

        logger.warning(f"No price available for {symbol}")
        return None

    def get_pip_value_per_lot(self, symbol: str) -> float:
        return 10.0

    def calculate_lot_size(self, symbol: str, risk_dollars: float, stop_pips: float) -> float:
        if stop_pips <= 0:
            return 0.01
        lots = risk_dollars / (stop_pips * 10.0)
        return max(0.01, min(round(lots, 2), 10.0))

    def place_market_order(self, symbol, lots, side, stop_loss=0.0,
                           take_profit=0.0, comment="APEX") -> dict:
        ticket = self._next()
        tick   = self.get_tick(symbol)
        price  = (tick["ask"] if side == "buy" else tick["bid"]) if tick else self.FALLBACK_PRICES.get(symbol, 1.0)
        self._positions[ticket] = {
            "id": str(ticket), "ticket": ticket, "symbol": symbol,
            "lots": lots, "side": side, "open_price": price,
            "current_price": price, "sl": stop_loss, "tp": take_profit,
            "profit": 0.0, "swap": 0.0, "open_time": datetime.now(),
        }
        logger.info(f"[MOCK] {side.upper()} {lots} lots {symbol} @ {price:.5f} | Ticket: {ticket}")
        return {"id": str(ticket), "ticket": ticket, "volume": lots, "price": price,
                "status": "filled", "filled_qty": lots, "filled_avg_price": price}

    def modify_sl_tp(self, ticket, stop_loss, take_profit) -> bool:
        if ticket in self._positions:
            self._positions[ticket]["sl"] = stop_loss
            self._positions[ticket]["tp"] = take_profit
            return True
        return False

    def cancel_order(self, order_id) -> bool:
        t = int(order_id)
        if t in self._orders:
            del self._orders[t]
            return True
        return False

    def get_positions(self) -> List[dict]:
        return list(self._positions.values())

    def get_orders(self) -> List[dict]:
        return list(self._orders.values())

    def close_position(self, ticket: int) -> bool:
        if ticket in self._positions:
            del self._positions[ticket]
            logger.info(f"[MOCK] Closed position {ticket}")
            return True
        return False

    def close_all_positions(self) -> int:
        count = len(self._positions)
        self._positions.clear()
        return count

    def is_market_open(self) -> bool:
        now = datetime.utcnow()
        if now.weekday() in (5, 6):
            return False
        if now.weekday() == 4 and now.hour >= 22:
            return False
        return True

    def get_history(self, days: int = 30) -> List[dict]:
        return []


def create_broker(config: BrokerConfig):
    if config.exness_login and config.exness_password and config.exness_server:
        logger.info("Connecting to Exness MT5...")
        return ExnessMT5Broker(config)
    logger.warning("No Exness credentials — running MockBroker (simulation)")
    return MockBroker()