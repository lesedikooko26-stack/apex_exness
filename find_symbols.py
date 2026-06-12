"""
Find exact symbol names available on your Exness MT5 account.
Run this to see every symbol you can trade.
    python find_symbols.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

import MetaTrader5 as mt5

login    = int(os.getenv("EXNESS_LOGIN"))
password = os.getenv("EXNESS_PASSWORD")
server   = os.getenv("EXNESS_SERVER")

mt5.initialize()
mt5.login(login=login, password=password, server=server)

print("=" * 60)
print("ALL SYMBOLS AVAILABLE ON YOUR EXNESS ACCOUNT")
print("=" * 60)

symbols = mt5.symbols_get()
if not symbols:
    print("No symbols found — make sure MT5 is open and logged in")
    mt5.shutdown()
    exit()

# Group by category
forex   = []
metals  = []
crypto  = []
indices = []
other   = []

for s in symbols:
    name = s.name
    if any(x in name.upper() for x in ["BTC","ETH","LTC","XRP"]):
        crypto.append(name)
    elif any(x in name.upper() for x in ["XAU","XAG","GOLD","SILVER"]):
        metals.append(name)
    elif any(x in name.upper() for x in ["US500","US30","NAS","SPX","DOW","UK100","GER","JPN"]):
        indices.append(name)
    elif len(name.replace("m","").replace("M","")) == 6 and name.replace("m","").replace("M","").isalpha():
        forex.append(name)
    else:
        other.append(name)

print(f"\nFOREX ({len(forex)}):")
for s in sorted(forex):
    print(f"  {s}")

print(f"\nMETALS ({len(metals)}):")
for s in sorted(metals):
    print(f"  {s}")

print(f"\nCRYPTO ({len(crypto)}):")
for s in sorted(crypto):
    print(f"  {s}")

print(f"\nINDICES ({len(indices)}):")
for s in sorted(indices):
    print(f"  {s}")

if other:
    print(f"\nOTHER ({len(other)}):")
    for s in sorted(other)[:20]:
        print(f"  {s}")

print("\n" + "=" * 60)
print("Copy the exact names above into config.py tickers list")
print("=" * 60)

mt5.shutdown()
