"""
APEX Trading Engine - Exness MT5 Configuration
Optimised based on 26-trade analysis:
- Removed EURUSDm (33% WR) and USDJPYm (0% WR)
- Widened min stop from 8 to 15 pips
- Disabled Momentum Breakout (33% WR) — Trend Continuation only (55% WR)
- Kept best pairs: XAUUSDm (67% WR), GBPUSDm (50% WR)
"""
from dataclasses import dataclass, field
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BrokerConfig:
    exness_login:    str = os.getenv("EXNESS_LOGIN", "")
    exness_password: str = os.getenv("EXNESS_PASSWORD", "")
    exness_server:   str = os.getenv("EXNESS_SERVER", "Exness-MT5Trial9")
    mt5_path:        str = os.getenv("MT5_PATH", "")
    magic_number:    int = 20240101


@dataclass
class RiskConfig:
    account_size:           float = float(os.getenv("ACCOUNT_SIZE", "8197").strip())
    max_risk_per_trade_pct: float = 1.0
    max_daily_loss_pct:     float = 3.0
    max_open_positions:     int   = 3
    min_reward_risk_ratio:  float = 2.5    # Increased from 2.0 — need bigger wins vs losses
    fixed_risk_pct:         float = 1.0
    max_lot_size:           float = 0.5
    min_lot_size:           float = 0.01
    use_kelly_criterion:    bool  = False
    kelly_fraction:         float = 0.25


@dataclass
class DataConfig:
    tickers: List[str] = field(default_factory=lambda: [
        # Removed: EURUSDm (33% WR), USDJPYm (0% WR)
        # Kept: best performers from 26-trade analysis
        "XAUUSDm",   # Best: 67% WR, +$427 profit
        "GBPUSDm",   # Good: 50% WR, +$64 profit
        "USDCHFm",   # Neutral — keeping for diversification
        "AUDUSDm",   # Neutral — keeping for diversification
        "USDCADm",   # Neutral — keeping for diversification
    ])
    historical_period:   str = "2d"
    historical_interval: str = "1m"


@dataclass
class StrategyConfig:
    # Momentum Breakout disabled — 33% WR, only 2 wins from 6 trades
    # Only using Trend Continuation — 55% WR, +$391 profit
    use_momentum_breakout:      bool  = False
    use_trend_continuation:     bool  = True

    breakout_lookback_bars:     int   = 15
    breakout_volume_multiplier: float = 1.5
    ema_alignment_required:     bool  = True
    rsi_oversold:               float = 30.0
    rsi_overbought:             float = 70.0
    scan_interval_seconds:      int   = 30

    # Widened from 8 to 15 pips — stops were too tight, getting hit 85% of the time
    min_stop_pips:              float = 15.0

    # Use ATR multiplier for dynamic stops instead of fixed pips
    # Stop = 1.5 x ATR from entry — gives price room to breathe
    atr_stop_multiplier:        float = 1.5
    atr_target_multiplier:      float = 3.5    # Target = 3.5 x ATR (R/R ~2.3)

    # 24/5 trading — forex runs around the clock
    trading_hours_start:        int   = 0
    trading_hours_end:          int   = 23


@dataclass
class AppConfig:
    broker:   BrokerConfig   = field(default_factory=BrokerConfig)
    risk:     RiskConfig     = field(default_factory=RiskConfig)
    data:     DataConfig     = field(default_factory=DataConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    db_path:      str = "data/trades.db"
    csv_log_path: str = "logs/trade_log.csv"
    web_port:     int = 8080


config = AppConfig()