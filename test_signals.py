"""
Signal Diagnostic — matches exact logic in trend_continuation.py
Run: python test_signals.py
"""
import os, sys, logging, warnings
warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.WARNING)
from dotenv import load_dotenv
load_dotenv()

print("=" * 65)
print("APEX — SIGNAL SCORING DIAGNOSTIC")
print("=" * 65)

from config import config
from core.data import MarketDataFetcher, TechnicalIndicators
import pandas as pd

fetcher = MarketDataFetcher()

total_signals = 0
for ticker in config.data.tickers:
    print(f"\n{ticker}")
    print("-" * 40)

    df = fetcher.get_ohlcv(ticker, period="2d", interval="1m")
    if df is None or len(df) < 60:
        print(f"  No data ({len(df) if df is not None else 0} bars)")
        continue

    df = TechnicalIndicators.add_all(df)
    if df is None or df.empty:
        print("  Indicators failed"); continue

    latest = df.iloc[-1]
    prev   = df.iloc[-2]
    prev2  = df.iloc[-3] if len(df) > 3 else prev

    def v(key, default=0.0):
        val = latest.get(key)
        try:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return default
            return float(val)
        except: return default

    close     = v("close")
    ema9      = v("ema9");  ema21 = v("ema21"); ema50 = v("ema50")
    rsi       = v("rsi", 50.0)
    atr       = v("atr")
    vol_ratio = v("vol_ratio", 1.0)
    macd_hist = v("macd_hist")
    prev_cl   = float(prev.get("close") or close)
    prev2_cl  = float(prev2.get("close") or close)

    if atr <= 0: atr = close * 0.001

    print(f"  Close:      {close:.5f}")
    print(f"  EMA9/21/50: {ema9:.5f} / {ema21:.5f} / {ema50:.5f}")
    print(f"  RSI:        {rsi:.1f}")
    print(f"  MACD hist:  {macd_hist:+.6f}")
    print(f"  ATR:        {atr:.5f} ({atr/0.0001:.1f} pips)")
    print(f"  Vol ratio:  {vol_ratio:.2f}x")

    # RSI extreme check
    if rsi <= 15 or rsi >= 85:
        print(f"  NO SIGNAL — RSI {rsi:.0f} too extreme (blocked < 15 or > 85)")
        continue

    price_rising  = close >= prev_cl or close >= prev2_cl
    price_falling = close <= prev_cl or close <= prev2_cl
    ema_bullish   = ema9 > ema21
    ema_bearish   = ema9 < ema21

    macd_bullish = macd_hist > 0 and rsi < 72
    macd_bearish = macd_hist < 0 and rsi > 28

    long_ok  = macd_bullish and (ema_bullish or price_rising)  and 28 < rsi < 72
    short_ok = macd_bearish and (ema_bearish or price_falling) and 28 < rsi < 72 and not long_ok

    print(f"\n  LONG:   MACD+={macd_bullish} | EMA_bull={ema_bullish} | Price_rising={price_rising} | RSI_ok={28<rsi<72} → {long_ok}")
    print(f"  SHORT:  MACD-={macd_bearish} | EMA_bear={ema_bearish} | Price_fall={price_falling} | RSI_ok={28<rsi<72} → {short_ok}")

    if not long_ok and not short_ok:
        print(f"  NO SIGNAL")
        if macd_hist == 0:
            print(f"  FIX: MACD histogram is 0 — no momentum")
        elif not macd_bullish and not macd_bearish:
            print(f"  FIX: RSI out of range or MACD neutral")
        continue

    direction = "LONG" if long_ok else "SHORT"

    # Score
    conf = 0.30
    full_stack = (ema9>ema21>ema50) if long_ok else (ema9<ema21<ema50)
    if full_stack:                     conf += 0.18
    elif ema_bullish and long_ok:      conf += 0.08
    elif ema_bearish and short_ok:     conf += 0.08
    if macd_hist > 0 and long_ok:      conf += 0.10
    if macd_hist < 0 and short_ok:     conf += 0.10
    if 40 < rsi < 60:                  conf += 0.08
    if vol_ratio > 1.3:                conf += 0.07
    if price_rising  and long_ok:      conf += 0.05
    if price_falling and short_ok:     conf += 0.05
    conf = min(conf, 1.0)

    # Calc stop/target
    min_stop = float(config.strategy.min_stop_pips)
    atr_stop = float(getattr(config.strategy, 'atr_stop_multiplier', 1.5))
    atr_tgt  = float(getattr(config.strategy, 'atr_target_multiplier', 3.5))
    # Correct pip sizes per symbol
    pip_map = {
        'XAU': 0.1, 'XAG': 0.001, 'BTC': 1.0, 'ETH': 0.1,
        'JPY': 0.01, 'US500': 0.25, 'US30': 1.0, 'NAS': 0.25,
    }
    pip_size = 0.0001  # default forex
    for key, val in pip_map.items():
        if key in ticker.upper():
            pip_size = val
            break

    stop_dist   = max(atr * atr_stop, min_stop * pip_size)
    rr          = config.risk.min_reward_risk_ratio
    tgt_dist    = stop_dist * rr
    stop_pips   = stop_dist / pip_size
    target_pips = tgt_dist  / pip_size

    risk_dollars = config.risk.account_size * (config.risk.fixed_risk_pct / 100)
    lots = max(0.01, min(round(risk_dollars / (stop_pips * 10), 2), config.risk.max_lot_size))

    threshold = 0.48
    fires     = conf >= threshold
    total_signals += 1 if fires else 0

    print(f"\n  SIGNAL: {direction}")
    print(f"  Stop:   {stop_pips:.1f} pips ({atr_stop}x ATR)")
    print(f"  Target: {target_pips:.1f} pips | R/R: {rr:.2f}")
    print(f"  Lots:   {lots:.2f} (risk ${risk_dollars:.0f})")
    print(f"  Conf:   {conf:.0%}  {'✅ FIRES' if fires else '❌ BLOCKED (< '+str(int(threshold*100))+'%)'}")

print(f"\n{'='*65}")
print(f"Total signals that would fire: {total_signals} / {len(config.data.tickers)}")
print(f"Confidence threshold: 48%")
print("=" * 65)