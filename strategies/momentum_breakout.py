"""
Strategy: Momentum Breakout — Kept for reference but DISABLED by default.
Re-enable in config.py: use_momentum_breakout = True
Only 33% win rate from 26-trade analysis — not recommended until further optimisation.
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from core.models import TradeSignal, Direction, SignalStatus
from core.data import MarketDataFetcher, TechnicalIndicators, get_pip_size, price_to_pips
from config import AppConfig

logger = logging.getLogger(__name__)


class MomentumBreakoutStrategy:
    NAME = "Momentum Breakout"

    def __init__(self, config: AppConfig, data_fetcher: MarketDataFetcher):
        self.config   = config
        self.risk     = config.risk
        self.fetcher  = data_fetcher

    def _v(self, row, key, default=0.0):
        try:
            v = row.get(key)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return default
            return float(v)
        except Exception:
            return default

    def scan(self, ticker: str) -> Optional[TradeSignal]:
        df = self.fetcher.get_ohlcv(
            ticker,
            period   = self.config.data.historical_period,
            interval = self.config.data.historical_interval,
        )
        if df is None or len(df) < 60:
            return None

        df = TechnicalIndicators.add_all(df)
        if df is None or df.empty:
            return None

        latest = df.iloc[-1]
        prev   = df.iloc[-2]

        close      = self._v(latest, "close")
        ema9       = self._v(latest, "ema9")
        ema21      = self._v(latest, "ema21")
        ema50      = self._v(latest, "ema50")
        rsi        = self._v(latest, "rsi",       50.0)
        atr        = self._v(latest, "atr")
        vol_ratio  = self._v(latest, "vol_ratio",  1.0)
        resistance = self._v(latest, "resistance")
        bb_upper   = self._v(latest, "bb_upper")
        macd_hist  = self._v(latest, "macd_hist")
        prev_close = self._v(prev,   "close")
        prev_bb    = self._v(prev,   "bb_upper")

        if close <= 0 or ema9 <= 0 or ema21 <= 0 or ema50 <= 0:
            return None
        if atr <= 0:
            atr = close * 0.001

        pip_size  = float(get_pip_size(ticker))
        atr_stop  = float(getattr(self.config.strategy, 'atr_stop_multiplier',   1.5))
        atr_tgt   = float(getattr(self.config.strategy, 'atr_target_multiplier', 3.5))
        min_stop  = float(getattr(self.config.strategy, 'min_stop_pips',         15.0))
        min_rr    = float(self.config.risk.min_reward_risk_ratio)

        # Require full EMA stack for breakout trades
        if not (ema9 > ema21 > ema50):
            return None
        if rsi > 72 or rsi < 40:
            return None

        broke_above = (resistance > 0 and close > resistance and prev_close <= resistance)
        bb_breakout = (bb_upper > 0 and prev_bb > 0 and
                       close > bb_upper and prev_close <= prev_bb)

        if not (broke_above or bb_breakout):
            return None
        if vol_ratio < 1.5:  # Stricter volume for breakouts
            return None
        if macd_hist <= 0:
            return None

        stop_dist   = max(atr * atr_stop, min_stop * pip_size)
        tgt_dist    = atr * atr_tgt
        stop_price  = round(close - stop_dist, 5)
        take_profit = round(close + tgt_dist,  5)

        if stop_price >= close:
            stop_price  = round(close - min_stop * pip_size, 5)
        if take_profit <= close:
            take_profit = round(close + min_stop * 2.5 * pip_size, 5)

        stop_pips   = price_to_pips(ticker, abs(close - stop_price))
        target_pips = price_to_pips(ticker, abs(take_profit - close))
        rr_ratio    = (target_pips / stop_pips) if stop_pips > 0 else 0.0

        if rr_ratio < min_rr:
            return None

        risk_dollars = float(self.config.risk.account_size) * (float(self.config.risk.fixed_risk_pct) / 100.0)
        raw_lots     = risk_dollars / (stop_pips * 10.0) if stop_pips > 0 else 0.01
        max_lots     = float(getattr(self.config.risk, 'max_lot_size', 0.5))
        lot_size     = max(0.01, min(round(raw_lots, 2), max_lots))

        confidence = 0.30
        if broke_above:        confidence += 0.20
        if bb_breakout:        confidence += 0.10
        if vol_ratio > 2.0:    confidence += 0.15
        elif vol_ratio > 1.5:  confidence += 0.08
        if 50 < rsi < 65:      confidence += 0.10
        if macd_hist > 0:      confidence += 0.08
        confidence = min(confidence, 1.0)

        rationale = (
            f"Breakout LONG | "
            f"EMA {ema9:.5f}/{ema21:.5f}/{ema50:.5f} | "
            f"RSI {rsi:.0f} | Vol {vol_ratio:.1f}x | "
            f"Stop {stop_pips:.1f}p | R/R {rr_ratio:.2f}"
        )

        logger.info(
            f"SIGNAL: {ticker} LONG @ {close:.5f} | "
            f"SL:{stop_price:.5f} TP:{take_profit:.5f} | "
            f"Lots:{lot_size} | Conf:{confidence:.0%}"
        )

        return TradeSignal(
            ticker            = ticker,
            strategy          = self.NAME,
            direction         = Direction.LONG,
            entry_price       = round(close, 5),
            target_price      = round(take_profit, 5),
            stop_price        = round(stop_price, 5),
            current_price     = round(close, 5),
            lot_size          = lot_size,
            stop_pips         = round(stop_pips, 1),
            target_pips       = round(target_pips, 1),
            risk_dollars      = round(risk_dollars, 2),
            risk_pct          = float(self.config.risk.fixed_risk_pct),
            reward_risk_ratio = round(rr_ratio, 2),
            confidence        = round(confidence, 4),
            rationale         = rationale,
            status            = SignalStatus.PENDING,
            expires_at        = datetime.now() + timedelta(minutes=15),
            indicators        = {
                "rsi":        round(rsi, 1),
                "ema9":       round(ema9, 5),
                "ema21":      round(ema21, 5),
                "ema50":      round(ema50, 5),
                "atr":        round(atr, 5),
                "vol_ratio":  round(vol_ratio, 2),
                "resistance": round(resistance, 5) if resistance else 0.0,
                "stop_pips":  round(stop_pips, 1),
            }
        )

    def scan_all(self, tickers: list) -> list:
        signals = []
        for ticker in tickers:
            try:
                sig = self.scan(ticker)
                if sig:
                    signals.append(sig)
            except Exception as e:
                logger.error(f"Error scanning {ticker}: {e}", exc_info=True)
            time.sleep(0.3)
        return signals