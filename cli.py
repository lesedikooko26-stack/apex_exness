"""
APEX Trading Engine — CLI Terminal Dashboard (Exness MT5)
"""
import os
import sys
import time
import logging
import argparse
from datetime import datetime

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

def clear(): os.system("cls" if os.name == "nt" else "clear")
def green(t):  return f"\033[92m{t}\033[0m"
def red(t):    return f"\033[91m{t}\033[0m"
def yellow(t): return f"\033[93m{t}\033[0m"
def blue(t):   return f"\033[94m{t}\033[0m"
def cyan(t):   return f"\033[96m{t}\033[0m"
def dim(t):    return f"\033[2m{t}\033[0m"
def bold(t):   return f"\033[1m{t}\033[0m"

def fmt_pnl(v):
    if v is None: return dim("—")
    s = f"${abs(v):,.2f}"
    return green(f"+{s}") if v >= 0 else red(f"-{s}")

def fmt_pct(v):
    if v is None: return dim("—")
    s = f"{abs(v):.2f}%"
    return green(f"+{s}") if v >= 0 else red(f"-{s}")


def render(engine):
    state     = engine.get_state()
    eng       = state["engine"]
    stats     = state["stats"]
    signals   = state["signals"]
    positions = state["positions"]
    cfg       = state["config"]

    clear()
    now = datetime.utcnow().strftime("%H:%M:%S UTC")

    print(bold(green("╔══════════════════════════════════════════════════════════════════╗")))
    print(bold(green(f"║  APEX TRADING ENGINE — EXNESS MT5                   {now}  ║")))
    print(bold(green("╚══════════════════════════════════════════════════════════════════╝")))
    print()

    engine_st = green("LIVE") if eng["running"] and not eng["shutdown"] else red("STOPPED")
    market_st = green("OPEN") if eng["market_open"] else yellow("CLOSED")
    mode_st   = yellow("[SIMULATION]") if cfg["paper_trading"] else green("[LIVE TRADING]")
    print(f"  Engine: {engine_st}   Forex Market: {market_st}   {mode_st}")
    print(f"  Scans: {cyan(str(eng['scan_count']))}   "
          f"Last: {dim(eng['last_scan'][:19] if eng['last_scan'] else 'Never')}")
    print()

    print(dim("─" * 68))
    pnl     = stats.get("realized_pnl", 0)
    pnl_pct = stats.get("daily_pnl_pct", 0)
    bal     = stats.get("current_balance", cfg["account_size"])
    unreal  = stats.get("unrealized_pnl", 0)
    wr      = stats.get("win_rate", 0)
    nopen   = len(positions.get("open", []))
    dd      = stats.get("max_drawdown", 0)
    wl      = f"{stats.get('winning_trades',0)}W/{stats.get('losing_trades',0)}L"

    print(f"  Daily P&L:  {fmt_pnl(pnl)} ({fmt_pct(pnl_pct)})")
    print(f"  Balance:    {green('$' + f'{bal:,.2f}')}")
    print(f"  Unrealized: {fmt_pnl(unreal)}")
    print(f"  Win Rate:   {green(f'{wr:.0f}%') if wr >= 50 else red(f'{wr:.0f}%')}  ({wl})")
    print(f"  Open Pos:   {cyan(str(nopen))}/{cfg['max_open_positions']}   "
          f"Max DD: {red(f'{dd:.2f}%')}")
    print(dim("─" * 68))
    print()

    if signals:
        print(bold(f"  ACTIVE SIGNALS ({len(signals)})"))
        print()
        for s in signals[-5:]:
            dir_c  = green if s["direction"] == "LONG" else red
            conf   = s.get("confidence", 0)
            bar    = green("█" * int(conf * 10)) + dim("░" * (10 - int(conf * 10)))
            print(f"  {bold(s['ticker']):8}  {dir_c(s['direction']):6}  "
                  f"Entry: {cyan(str(s['entry_price'])):12}"
                  f"TP: {green(str(s['target_price'])):12}"
                  f"SL: {red(str(s['stop_price'])):12}"
                  f"Lots: {s['lot_size']}")
            print(f"           {dim(s['strategy'][:25])}   "
                  f"Stop: {s['stop_pips']:.0f} pips   R/R: {s['reward_risk_ratio']}x   "
                  f"Conf: {bar} {int(conf*100)}%")
            print(f"           {dim(s['rationale'][:65])}")
            print()
    else:
        print(f"  {dim('No active signals — scanning...')}")
        print()

    print(dim("─" * 68))
    open_pos = positions.get("open", [])
    print(bold(f"  OPEN POSITIONS ({len(open_pos)}/{cfg['max_open_positions']})"))
    if open_pos:
        print()
        print(f"  {'Pair':10}{'Dir':8}{'Entry':14}{'Current':14}{'P&L':16}{'Lots':8}{'SL':12}{'Ticket'}")
        for p in open_pos:
            dir_c = green if p["direction"] == "LONG" else red
            print(
                f"  {bold(p['ticker']):10}{dir_c(p['direction']):8}"
                f"{str(p['entry_price']):14}{str(p['current_price']):14}"
                f"{fmt_pnl(p['unrealized_pnl']):16}"
                f"{str(p['lot_size']):8}{red(str(p['stop_price'])):12}"
                f"{str(p.get('mt5_ticket','—'))}"
            )
    else:
        print(f"  {dim('No open positions')}")
    print()

    closed = positions.get("closed", [])[-5:]
    if closed:
        print(dim("─" * 68))
        print(bold("  RECENT CLOSED"))
        print()
        for p in closed:
            pnl_v = p.get("realized_pnl", 0)
            print(f"  {p['ticker']:10}{dim(p['strategy'][:20]):22}"
                  f"Entry: {str(p['entry_price']):12}"
                  f"Exit: {str(p['current_price']):12}"
                  f"PnL: {fmt_pnl(pnl_v)}")
        print()

    print(dim("─" * 68))
    print(dim(f"  Pairs: {', '.join(eng.get('tickers', [])[:8])}"))
    if stats.get("shutoff_triggered"):
        print(red(bold("  DAILY LOSS LIMIT REACHED — TRADING SUSPENDED")))
    print(dim("  [Ctrl+C to exit]  Refreshes every 10s"))


def run_cli():
    from engine import TradingEngine
    from config import config

    print(green("Initializing APEX Exness engine..."))
    engine = TradingEngine(config)
    print(green("Starting engine (force_scan=True — scans 24/7)..."))
    engine.start(force_scan=True)
    print(green("Engine started. Loading dashboard...\n"))
    time.sleep(3)

    try:
        while True:
            render(engine)
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        engine.stop()
        print(green("Engine stopped cleanly"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="APEX Exness Trading Engine")
    parser.add_argument("--web",  action="store_true", help="Start web dashboard")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    args = parser.parse_args()

    if args.web:
        print(green(f"Starting web dashboard on http://localhost:{args.port}"))
        from server import run_server
        run_server(port=args.port)
    else:
        run_cli()
