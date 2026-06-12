"""
Execution Diagnostic Script
Tests the full signal → order pipeline step by step.
Run from apex_exness folder: python test_execution.py
"""
import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

print("=" * 65)
print("APEX EXNESS — EXECUTION DIAGNOSTIC")
print("=" * 65)

# ── Step 1: Config ─────────────────────────────────────────────
print("\n[1] Loading config...")
try:
    from config import config
    print(f"    Account size:    ${config.risk.account_size:,.0f}")
    print(f"    Risk per trade:  {config.risk.fixed_risk_pct}%")
    print(f"    Max lot size:    {config.risk.max_lot_size}")
    print(f"    Min R/R:         {config.risk.min_reward_risk_ratio}")
    print(f"    Max positions:   {config.risk.max_open_positions}")
    print(f"    Min stop pips:   {config.strategy.min_stop_pips}")
    print(f"    Tickers:         {config.data.tickers}")
    print("    OK")
except Exception as e:
    print(f"    FAIL: {e}")
    sys.exit(1)

# ── Step 2: Broker connection ──────────────────────────────────
print("\n[2] Connecting to broker...")
try:
    from execution.broker import create_broker
    broker = create_broker(config.broker)
    acct   = broker.get_account()
    print(f"    Broker type:  {type(broker).__name__}")
    print(f"    Balance:      ${acct.get('balance', 0):,.2f}")
    print(f"    Free margin:  ${acct.get('free_margin', 0):,.2f}")
    print("    OK")
except Exception as e:
    print(f"    FAIL: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# ── Step 3: Test tick + lot size for each ticker ───────────────
print("\n[3] Testing tick + lot sizing for all tickers...")
TICKER_TEST = config.data.tickers
for ticker in TICKER_TEST:
    try:
        tick = broker.get_tick(ticker)
        if not tick:
            print(f"    {ticker:14} FAIL — no tick returned")
            continue
        bid = float(tick.get("bid", 0))
        ask = float(tick.get("ask", 0))
        if bid <= 0 or ask <= 0:
            print(f"    {ticker:14} FAIL — invalid bid/ask: bid={bid} ask={ask}")
            continue

        # Test pip value
        pip_val = broker.get_pip_value_per_lot(ticker)

        # Test lot size calculation
        risk_dollars = config.risk.account_size * (config.risk.fixed_risk_pct / 100)
        stop_pips    = 10.0
        lots         = broker.calculate_lot_size(ticker, risk_dollars, stop_pips)

        print(
            f"    {ticker:14} bid={bid:.5f} ask={ask:.5f} | "
            f"pip_val=${pip_val:.2f}/lot | "
            f"lots={lots:.2f} for ${risk_dollars:.0f} risk / {stop_pips} pips"
        )
    except Exception as e:
        print(f"    {ticker:14} FAIL — {e}")

# ── Step 4: Simulate a full signal execution ───────────────────
print("\n[4] Simulating signal execution for EURUSDm...")
try:
    from core.models import TradeSignal, Direction, SignalStatus
    from risk.risk_manager import RiskManager
    from execution.order_manager import OrderManager
    from datetime import datetime, timedelta

    risk_mgr = RiskManager(config.risk)
    order_mgr = OrderManager(broker, risk_mgr, config)

    # Get live price
    tick  = broker.get_tick("EURUSDm")
    if not tick:
        print("    FAIL — no tick for EURUSDm")
    else:
        price = float(tick["ask"])
        from core.data import get_pip_size
        pip_size  = get_pip_size("EURUSDm")
        min_stop  = float(config.strategy.min_stop_pips)
        stop_pips = max(min_stop, 10.0)

        stop_price  = round(price - stop_pips * pip_size, 5)
        take_profit = round(price + stop_pips * 2.0 * pip_size, 5)
        risk_dollars = config.risk.account_size * (config.risk.fixed_risk_pct / 100)
        lots = broker.calculate_lot_size("EURUSDm", risk_dollars, stop_pips)
        lots = max(0.01, min(lots, config.risk.max_lot_size))

        print(f"    Price:      {price:.5f}")
        print(f"    Stop:       {stop_price:.5f} ({stop_pips:.1f} pips below)")
        print(f"    Target:     {take_profit:.5f}")
        print(f"    Risk $:     ${risk_dollars:.2f}")
        print(f"    Lots:       {lots:.2f}")

        sig = TradeSignal(
            ticker            = "EURUSDm",
            strategy          = "Test",
            direction         = Direction.LONG,
            entry_price       = round(price, 5),
            target_price      = round(take_profit, 5),
            stop_price        = round(stop_price, 5),
            current_price     = round(price, 5),
            lot_size          = lots,
            stop_pips         = stop_pips,
            target_pips       = stop_pips * 2.0,
            risk_dollars      = round(risk_dollars, 2),
            risk_pct          = config.risk.fixed_risk_pct,
            reward_risk_ratio = 2.0,
            confidence        = 0.80,
            rationale         = "Diagnostic test signal",
            status            = SignalStatus.PENDING,
            expires_at        = datetime.now() + timedelta(minutes=5),
        )

        print("\n    Running risk validation...")
        ok, reason = risk_mgr.validate_signal(sig, [])
        print(f"    Risk check: {'PASSED' if ok else 'FAILED — ' + reason}")

        if ok:
            print("\n    Attempting order placement...")
            pos = order_mgr.execute_signal(sig)
            if pos:
                print(f"    SUCCESS — Position opened | Ticket: {pos.mt5_ticket}")
                print(f"    Entry: {pos.entry_price:.5f} | SL: {pos.stop_price:.5f} | TP: {pos.target_price:.5f}")
                print(f"    Lots: {pos.lot_size}")
                # Close it immediately
                print("\n    Closing test position...")
                order_mgr.close_position(sig.id, price, "diagnostic_test")
                print("    Test position closed")
            else:
                print("    FAIL — execute_signal returned None")
                print("    Check the ERROR lines above for the exact cause")

except Exception as e:
    print(f"    FAIL: {e}")
    import traceback; traceback.print_exc()

# ── Step 5: Check trading hours ────────────────────────────────
print("\n[5] Checking trading hours filter...")
from datetime import datetime
hour = datetime.utcnow().hour
start = config.strategy.trading_hours_start
end   = config.strategy.trading_hours_end
in_hours = start <= hour < end
print(f"    Current UTC hour: {hour:02d}:00")
print(f"    Trading window:   {start:02d}:00 – {end:02d}:00 UTC")
print(f"    In trading hours: {'YES' if in_hours else 'NO — orders blocked outside this window'}")

print("\n" + "=" * 65)
print("Diagnostic complete — check results above")
print("=" * 65)
