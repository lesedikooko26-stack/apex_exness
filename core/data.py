"""
Data Layer: OHLCV + indicators for Forex.
Symbol map verified against Exness-MT5Trial9.
"""
import logging
import time
import warnings
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# MT5 symbol → Yahoo Finance symbol
# Covers all confirmed Exness 'm' suffix symbols
YF_SYMBOL_MAP = {
    # Forex majors
    "EURUSDm": "EURUSD=X", "GBPUSDm": "GBPUSD=X", "USDJPYm": "USDJPY=X",
    "USDCHFm": "USDCHF=X", "AUDUSDm": "AUDUSD=X", "NZDUSDm": "NZDUSD=X",
    "USDCADm": "USDCAD=X",
    # Forex crosses
    "EURGBPm": "EURGBP=X", "EURJPYm": "EURJPY=X", "EURCADm": "EURCAD=X",
    "EURCHFm": "EURCHF=X", "EURAUDm": "EURAUD=X", "EURNZDm": "EURNZD=X",
    "GBPJPYm": "GBPJPY=X", "GBPCADm": "GBPCAD=X", "GBPCHFm": "GBPCHF=X",
    "GBPAUDm": "GBPAUD=X", "GBPNZDm": "GBPNZD=X",
    "AUDJPYm": "AUDJPY=X", "AUDCADm": "AUDCAD=X", "AUDCHFm": "AUDCHF=X",
    "AUDNZDm": "AUDNZD=X",
    "NZDJPYm": "NZDJPY=X", "NZDCADm": "NZDCAD=X", "NZDCHFm": "NZDCHF=X",
    "CADJPYm": "CADJPY=X", "CADCHFm": "CADCHF=X",
    "CHFJPYm": "CHFJPY=X",
    "USDNOKm": "USDNOK=X", "USDSEKm": "USDSEK=X", "USDDKKm": "USDDKK=X",
    "USDPLNm": "USDPLN=X", "USDHUFm": "USDHUF=X", "USDCZKm": "USDCZK=X",
    "USDZARm": "USDZAR=X", "USDTRYm": "USDTRY=X", "USDMXNm": "USDMXN=X",
    "USDSGDm": "USDSGD=X", "USDHKDm": "USDHKD=X", "USDCNHm": "USDCNH=X",
    # Metals
    "XAUUSDm": "GC=F",    # Gold futures
    "XAGUSDm": "SI=F",    # Silver futures
    "XAUEURm": "GC=F",    # Gold in EUR (use gold futures)
    "XAUGBPm": "GC=F",
    "XAUAUDm": "GC=F",
    "XAGEURm": "SI=F",
    "XAGGBPm": "SI=F",
    "XAGJPYm": "SI=F",
    # Other metals
    "XPTUSDm": "PL=F",    # Platinum
    "XPDUSDm": "PA=F",    # Palladium
    # Crypto
    "BTCUSDm": "BTC-USD",
    "ETHUSDm": "ETH-USD",
    "LTCUSDm": "LTC-USD",
    "XRPUSDm": "XRP-USD",
    "BTCAUDm": "BTC-USD",
    "BTCJPYm": "BTC-USD",
    "BTCZARm": "BTC-USD",
    # Indices
    "US500m":      "ES=F",    # S&P 500 futures
    "US500_x100m": "ES=F",
    "US30m":       "YM=F",    # Dow Jones futures
    "US30_x10m":   "YM=F",
    "UK100m":      "^FTSE",   # FTSE 100
    "AUS200m":     "^AXJO",   # ASX 200
    # Stocks (some available on Exness)
    "AAPLm":  "AAPL",
    "AMZNm":  "AMZN",
    "AMDm":   "AMD",
    "BABAm":  "BABA",
    # Standard names without suffix (fallback)
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X", "AUDUSD": "AUDUSD=X", "NZDUSD": "NZDUSD=X",
    "USDCAD": "USDCAD=X", "XAUUSD": "GC=F",     "XAGUSD": "SI=F",
    "BTCUSD": "BTC-USD",  "ETHUSD": "ETH-USD",
    "US500":  "ES=F",     "US30":   "YM=F",
}

# Pip sizes — keyed by base symbol (no 'm' suffix)
PIP_SIZE_MAP = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001, "NZDUSD": 0.0001,
    "USDCAD": 0.0001, "USDCHF": 0.0001, "EURGBP": 0.0001, "EURCAD": 0.0001,
    "EURAUD": 0.0001, "EURNZD": 0.0001, "EURCHF": 0.0001,
    "AUDCAD": 0.0001, "AUDCHF": 0.0001, "AUDNZD": 0.0001,
    "GBPCAD": 0.0001, "GBPCHF": 0.0001, "GBPAUD": 0.0001, "GBPNZD": 0.0001,
    "NZDCAD": 0.0001, "NZDCHF": 0.0001, "CADCHF": 0.0001,
    "USDNOK": 0.0001, "USDSEK": 0.0001, "USDDKK": 0.0001,
    "USDPLN": 0.0001, "USDCZK": 0.0001, "USDZAR": 0.0001,
    "EURJPY": 0.01,   "USDJPY": 0.01,   "GBPJPY": 0.01,
    "AUDJPY": 0.01,   "NZDJPY": 0.01,   "CADJPY": 0.01,
    "CHFJPY": 0.01,   "EURJPY": 0.01,
    "XAUUSD": 0.1,    "XAGUSD": 0.001,
    "XPTUSD": 0.1,    "XPDUSD": 0.1,
    "BTCUSD": 1.0,    "ETHUSD": 0.1,    "LTCUSD": 0.01,   "XRPUSD": 0.0001,
    "US500":  0.25,   "US30":   1.0,
}


def _base_symbol(symbol: str) -> str:
    """Remove Exness 'm' suffix and return uppercase base."""
    s = symbol.upper()
    # Handle _x10m, _x100m suffixes
    if "_X" in s:
        s = s.split("_")[0]
    # Remove trailing M if it looks like a suffix
    if s.endswith("M") and len(s) > 6:
        return s[:-1]
    return s


def get_pip_size(symbol: str) -> float:
    base = _base_symbol(symbol)
    return PIP_SIZE_MAP.get(base, 0.0001)


def price_to_pips(symbol: str, price_diff: float) -> float:
    return abs(price_diff) / get_pip_size(symbol)


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Fix yfinance >= 0.2.x MultiIndex columns."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower().strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    return df


class MarketDataFetcher:

    def __init__(self):
        self._cache:    Dict[str, pd.DataFrame] = {}
        self._cache_ts: Dict[str, float]        = {}
        self._cache_ttl = 60

    def _yf_symbol(self, mt5_symbol: str) -> Optional[str]:
        # Direct lookup first
        if mt5_symbol in YF_SYMBOL_MAP:
            return YF_SYMBOL_MAP[mt5_symbol]
        # Try base symbol without suffix
        base = _base_symbol(mt5_symbol)
        if base in YF_SYMBOL_MAP:
            return YF_SYMBOL_MAP[base]
        # Last resort: try as-is
        logger.warning(f"No Yahoo Finance mapping for {mt5_symbol} — skipping")
        return None

    def get_ohlcv(self, ticker: str, period: str = "2d", interval: str = "1m") -> Optional[pd.DataFrame]:
        key = f"{ticker}:{period}:{interval}"
        now = time.time()
        if key in self._cache and (now - self._cache_ts.get(key, 0)) < self._cache_ttl:
            return self._cache[key]
        try:
            yf_sym = self._yf_symbol(ticker)
            if not yf_sym:
                return None
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = yf.download(yf_sym, period=period, interval=interval,
                                 progress=False, auto_adjust=True)
            if df is None or df.empty:
                logger.warning(f"No data for {ticker} ({yf_sym})")
                return None
            df = _flatten_columns(df)
            required = {"open", "high", "low", "close", "volume"}
            missing  = required - set(df.columns)
            if missing:
                logger.error(f"Missing columns for {ticker}: {missing} | Got: {list(df.columns)}")
                return None
            df.index = pd.to_datetime(df.index)
            df.dropna(inplace=True)
            if len(df) < 30:
                logger.warning(f"Too few bars for {ticker}: {len(df)}")
                return None
            self._cache[key]    = df
            self._cache_ts[key] = now
            return df
        except Exception as e:
            logger.error(f"Error fetching {ticker}: {e}")
            return None

    def get_current_price(self, ticker: str) -> Optional[float]:
        try:
            yf_sym = self._yf_symbol(ticker)
            if not yf_sym:
                return None
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                t     = yf.Ticker(yf_sym)
                price = t.fast_info.last_price
            return float(price) if price else None
        except Exception:
            return None

    def get_quote(self, ticker: str) -> Optional[Dict]:
        price = self.get_current_price(ticker)
        return {"ticker": ticker, "price": price} if price else None


class TechnicalIndicators:

    @staticmethod
    def add_all(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or len(df) < 50:
            return df
        df = df.copy()
        df["ema9"]  = TechnicalIndicators.ema(df["close"], 9)
        df["ema21"] = TechnicalIndicators.ema(df["close"], 21)
        df["ema50"] = TechnicalIndicators.ema(df["close"], 50)
        df["rsi"]   = TechnicalIndicators.rsi(df["close"], 14)
        df["vwap"]  = TechnicalIndicators.vwap(df)
        bb = TechnicalIndicators.bollinger_bands(df["close"])
        df["bb_upper"] = bb["upper"]
        df["bb_mid"]   = bb["mid"]
        df["bb_lower"] = bb["lower"]
        df["bb_width"] = (bb["upper"] - bb["lower"]) / bb["mid"].replace(0, np.nan)
        df["atr"] = TechnicalIndicators.atr(df, 14)
        macd = TechnicalIndicators.macd(df["close"])
        df["macd"]        = macd["macd"]
        df["macd_signal"] = macd["signal"]
        df["macd_hist"]   = macd["hist"]
        df["vol_sma20"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_sma20"].replace(0, np.nan)
        df["resistance"] = df["high"].rolling(20).max().shift(1)
        df["support"]    = df["low"].rolling(20).min().shift(1)
        return df

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta    = series.diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs       = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.Series:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        tp_vol  = typical * df["volume"]
        vwap    = pd.Series(index=df.index, dtype=float)
        try:
            dates = df.index.date if hasattr(df.index, "date") else [0] * len(df)
            for d in pd.unique(dates):
                mask       = np.array([dt == d for dt in dates])
                cum_tpv    = tp_vol[mask].cumsum()
                cum_vol    = df["volume"][mask].cumsum()
                vwap[mask] = cum_tpv / cum_vol.replace(0, np.nan)
        except Exception:
            pass
        return vwap

    @staticmethod
    def bollinger_bands(series: pd.Series, period: int = 20, std: float = 2.0) -> dict:
        mid   = series.rolling(period).mean()
        sigma = series.rolling(period).std()
        return {"upper": mid + std * sigma, "mid": mid, "lower": mid - std * sigma}

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(com=period - 1, adjust=False).mean()

    @staticmethod
    def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        ema_fast    = series.ewm(span=fast,   adjust=False).mean()
        ema_slow    = series.ewm(span=slow,   adjust=False).mean()
        macd_line   = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return {"macd": macd_line, "signal": signal_line, "hist": macd_line - signal_line}