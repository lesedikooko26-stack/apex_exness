"""
MT5 Connection Diagnostic — updated with verified Exness symbol names.
    python test_mt5.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

import MetaTrader5 as mt5

login    = int(os.getenv("EXNESS_LOGIN"))
password = os.getenv("EXNESS_PASSWORD")
server   = os.getenv("EXNESS_SERVER")

print("=" * 60)
print("APEX EXNESS — MT5 CONNECTION DIAGNOSTIC")
print("=" * 60)

mt5.initialize()
mt5.login(login=login, password=password, server=server)

info = mt5.account_info()
print(f"\nAccount: {info.login} | Balance: {info.currency} {info.balance:,.2f}")
print(f"Server:  {info.server}")

# Test all symbols in the updated config.py tickers list
SYMBOLS_TO_TEST = [
    "EURUSDm", "GBPUSDm", "USDJPYm", "USDCHFm",
    "AUDUSDm", "USDCADm", "XAUUSDm", "XAGUSDm",
    "BTCUSDm", "ETHUSDm", "US500m",  "US30m",
]

print(f"\nTesting {len(SYMBOLS_TO_TEST)} symbols...")
print()
ok_count = 0
for sym in SYMBOLS_TO_TEST:
    mt5.symbol_select(sym, True)
    tick = mt5.symbol_info_tick(sym)
    if tick and tick.bid > 0:
        print(f"  OK  {sym:15} bid={tick.bid}  ask={tick.ask}")
        ok_count += 1
    else:
        print(f"  FAIL {sym:15} no tick")

print(f"\nResult: {ok_count}/{len(SYMBOLS_TO_TEST)} symbols OK")
if ok_count == len(SYMBOLS_TO_TEST):
    print("All symbols working — engine is ready to run!")
else:
    print("Some symbols failed — run find_symbols.py to check exact names")

mt5.shutdown()