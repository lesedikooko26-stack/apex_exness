"""
Strategy: Trend Continuation — Maximum signal generation
Uses MACD as primary direction signal with EMA and price as confirmation.
Verified against real diagnostic data from Exness-MT5Trial9.
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


class TrendContinuationStrategy:
    NAME = "Trend Continuation"

    def __init__(self, config: AppConfig, data_fetcher: MarketDataFetcher):
        self.config  = config
        self.risk    = config.risk
        self.fetcher = data_fetcher

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
        prev2  = df.iloc[-3] if len(df) > 3 else prev

        close      = self._v(latest, "close")
        ema9       = self._v(latest, "ema9")
        ema21      = self._v(latest, "ema21")
        ema50      = self._v(latest, "ema50")
        rsi        = self._v(latest, "rsi",      50.0)
        atr        = self._v(latest, "atr")
        vol_ratio  = self._v(latest, "vol_ratio", 1.0)
        macd_hist  = self._v(latest, "macd_hist")
        prev_close = self._v(prev,   "close")
        prev2_cl   = self._v(prev2,  "close")

        if close <= 0 or ema9 <= 0 or ema21 <= 0 or ema50 <= 0:
            return None
        if atr <= 0:
            atr = close * 0.001

        # RSI extreme filter — only skip truly extreme values
        if rsi <= 15 or rsi >= 85:
            return None

        pip_size = float(get_pip_size(ticker))
        atr_stop = float(getattr(self.config.strategy, 'atr_stop_multiplier',   1.5))
        atr_tgt  = float(getattr(self.config.strategy, 'atr_target_multiplier', 3.5))
        min_stop = float(getattr(self.config.strategy, 'min_stop_pips',         15.0))
        min_rr   = float(self.config.risk.min_reward_risk_ratio)

        price_rising  = close >= prev_close or close >= prev2_cl
        price_falling = close <= prev_close or close <= prev2_cl

        # ── LONG signal logic ─────────────────────────────────
        # Primary: MACD positive + RSI not overbought
        # Secondary: EMA fast above slow OR price rising
        macd_bullish = macd_hist > 0 and rsi < 72
        ema_bullish  = ema9 > ema21
        long_ok = (
            macd_bullish and
            (ema_bullish or price_rising) and
            28 < rsi < 72
        )

        # ── SHORT signal logic ────────────────────────────────
        macd_bearish = macd_hist < 0 and rsi > 28
        ema_bearish  = ema9 < ema21
        short_ok = (
            macd_bearish and
            (ema_bearish or price_falling) and
            28 < rsi < 72 and
            not long_ok
        )

        if not long_ok and not short_ok:
            return None

        direction = Direction.LONG if long_ok else Direction.SHORT

        # ── Build SL/TP ───────────────────────────────────────
        # Stop = max(1.5x ATR, 15 pips minimum)
        stop_dist = max(atr * atr_stop, min_stop * pip_size)
        # Target = stop_dist x min_rr (guarantees R/R always met)
        # e.g. stop=15 pips, min_rr=2.0 → target=30 pips minimum
        tgt_dist  = stop_dist * min_rr

        if direction == Direction.LONG:
            stop_price  = round(close - stop_dist, 5)
            take_profit = round(close + tgt_dist,  5)
        else:
            stop_price  = round(close + stop_dist, 5)
            take_profit = round(close - tgt_dist,  5)

        # Sanity check
        if direction == Direction.LONG:
            if stop_price >= close:
                stop_price  = round(close - min_stop * pip_size, 5)
            if take_profit <= close:
                take_profit = round(close + min_stop * 2.5 * pip_size, 5)
        else:
            if stop_price <= close:
                stop_price  = round(close + min_stop * pip_size, 5)
            if take_profit >= close:
                take_profit = round(close - min_stop * 2.5 * pip_size, 5)

        stop_pips   = price_to_pips(ticker, abs(close - stop_price))
        target_pips = price_to_pips(ticker, abs(take_profit - close))
        rr_ratio    = (target_pips / stop_pips) if stop_pips > 0 else 0.0

        if rr_ratio < min_rr:
            return None

        # ── Lot size ──────────────────────────────────────────
        risk_dollars = float(self.config.risk.account_size) * (float(self.config.risk.fixed_risk_pct) / 100.0)
        raw_lots     = risk_dollars / (stop_pips * 10.0) if stop_pips > 0 else 0.01
        max_lots     = float(getattr(self.config.risk, 'max_lot_size', 0.5))
        lot_size     = max(0.01, min(round(raw_lots, 2), max_lots))

        # ── Confidence ────────────────────────────────────────
        confidence = 0.30  # base — passed MACD + RSI + direction check

        # EMA alignment bonus
        full_stack = (ema9 > ema21 > ema50) if long_ok else (ema9 < ema21 < ema50)
        if full_stack:          confidence += 0.18
        elif ema_bullish and long_ok: confidence += 0.08
        elif ema_bearish and short_ok: confidence += 0.08

        # MACD strength
        if macd_hist > 0 and long_ok:  confidence += 0.10
        if macd_hist < 0 and short_ok: confidence += 0.10

        # RSI in ideal zone
        if 40 < rsi < 60:       confidence += 0.08

        # Volume
        if vol_ratio > 1.3:     confidence += 0.07

        # Price confirmation
        if price_rising  and long_ok:  confidence += 0.05
        if price_falling and short_ok: confidence += 0.05

        confidence = min(confidence, 1.0)

        dir_str  = direction.value
        atr_pips = atr / pip_size

        rationale = (
            f"Trend {dir_str} | "
            f"MACD {macd_hist:+.6f} | "
            f"EMA9 {ema9:.5f}/{ema21:.5f} | "
            f"RSI {rsi:.0f} | "
            f"ATR {atr_pips:.1f}p | "
            f"Stop {stop_pips:.1f}p | "
            f"TP {target_pips:.1f}p | "
            f"R/R {rr_ratio:.2f}"
        )

        logger.info(
            f"SIGNAL: {ticker} {dir_str} @ {close:.5f} | "
            f"SL:{stop_price:.5f} ({stop_pips:.1f}p) "
            f"TP:{take_profit:.5f} ({target_pips:.1f}p) | "
            f"Lots:{lot_size} | Conf:{confidence:.0%}"
        )

        return TradeSignal(
            ticker            = ticker,
            strategy          = self.NAME,
            direction         = direction,
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
            expires_at        = datetime.now() + timedelta(minutes=10),
            indicators        = {
                "rsi":       round(rsi, 1),
                "ema9":      round(ema9, 5),
                "ema21":     round(ema21, 5),
                "ema50":     round(ema50, 5),
                "atr":       round(atr, 5),
                "atr_pips":  round(atr_pips, 1),
                "vol_ratio": round(vol_ratio, 2),
                "macd_hist": round(macd_hist, 6),
                "stop_pips": round(stop_pips, 1),
            }
        )

    def scan_all(self, tickers: list) -> list:
        signals = []
        for ticker in tickers:
            try:
                sig = self.scan(ticker)
                if sig:
                    signals.append(sig)
                    logger.info(
                        f"Signal: {ticker} {sig.direction.value} "
                        f"Conf:{sig.confidence:.0%}"
                    )
                else:
                    logger.debug(f"No signal: {ticker}")
            except Exception as e:
                logger.error(f"Error scanning {ticker}: {e}", exc_info=True)
            time.sleep(0.3)
        return signals