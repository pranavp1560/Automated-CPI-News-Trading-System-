import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np

from utils.time_utils import get_local_hour, floor_to_m5

logger = logging.getLogger("cpi_trading_system")

@dataclass
class Signal:
    direction: str           # "BUY", "SELL", or "NO_SIGNAL"
    symbol: str
    currency: str
    entry: float
    sl: float
    tp: float
    risk_pips: float
    reward_pips: float
    event_time: datetime
    original_direction: str  # Before AUD reversal check
    reversed: bool = False

def calculate_ema(closes: pd.Series, period: int = 50) -> pd.Series:
    """
    Calculates the Exponential Moving Average (EMA) on a series of closes.
    """
    return closes.ewm(span=period, adjust=False).mean()

def calculate_pips(symbol: str, price_diff: float) -> float:
    """
    Converts a price difference into pips.
    Supports JPY pairs (0.01 = 1 pip) and others (0.0001 = 1 pip).
    """
    if "JPY" in symbol.upper():
        return round(price_diff * 100.0, 1)
    return round(price_diff * 10000.0, 1)

def find_setup_candle(df: pd.DataFrame, event_time: datetime) -> tuple[Optional[pd.Series], Optional[float], float]:
    """
    Identifies the setup candle from fetched historical candles based on timestamps.
    Returns: (setup_candle, ema50_value, broker_offset_seconds)
    """
    if df.empty:
        return None, None, 0.0
        
    # Calculate setup candle open time in UTC (10 mins before event time)
    expected_setup_open_utc = event_time - timedelta(minutes=10)
    
    # Check the timestamp of the latest candle in MT5 and compare with current time in UTC
    # to figure out the broker's timezone offset.
    now_utc_floored = floor_to_m5(datetime.now(timezone.utc))
    latest_candle_time_raw = df.iloc[-1]["time"]
    
    # naive or UTC timestamp returned by MT5
    latest_candle_dt = datetime.fromtimestamp(latest_candle_time_raw, tz=timezone.utc)
    
    broker_offset = latest_candle_dt - now_utc_floored
    
    # Adjust expected setup candle open to broker time
    expected_setup_open_broker = expected_setup_open_utc + broker_offset
    expected_setup_open_timestamp = int(expected_setup_open_broker.timestamp())
    
    # Try to find matching candle
    matched_rows = df[df["time"] == expected_setup_open_timestamp]
    
    if not matched_rows.empty:
        idx = matched_rows.index[0]
        setup_candle = df.loc[idx]
        
        # Calculate EMA
        ema_series = calculate_ema(df["close"], period=50)
        ema50_value = ema_series.loc[idx]
        
        logger.info(f"SIGNAL | Found setup candle by timestamp matching open time: {expected_setup_open_broker} (Broker time). Row index: {idx}")
        return setup_candle, ema50_value, broker_offset.total_seconds()
        
    # Fallback to index -2 if exact timestamp match is not found (e.g. in backtests or weird broker feeds)
    logger.warning("SIGNAL | Could not find setup candle by exact timestamp. Falling back to index -2.")
    setup_candle = df.iloc[-2]
    ema_series = calculate_ema(df["close"], period=50)
    ema50_value = ema_series.iloc[-2]
    
    # Calculate offset based on index -2 time vs expected setup open
    candle_time_utc = datetime.fromtimestamp(setup_candle["time"], tz=timezone.utc)
    calculated_offset = (candle_time_utc - expected_setup_open_utc).total_seconds()
    
    return setup_candle, ema50_value, calculated_offset

def generate_signal(df: pd.DataFrame, event_time: datetime, symbol: str, currency: str, config: dict) -> Signal:
    """
    Processes candle data and generates a trading signal based on the EMA strategy
    and currency-specific rules (including AUD reversal).
    """
    # 1. Identify setup candle and get EMA50 value
    setup_candle, ema50_value, offset = find_setup_candle(df, event_time)
    
    if setup_candle is None or ema50_value is None:
        logger.error(f"SIGNAL | {symbol} | Failed to identify setup candle or calculate EMA50.")
        return Signal("NO_SIGNAL", symbol, currency, 0.0, 0.0, 0.0, 0.0, 0.0, event_time, "NO_SIGNAL")

    open_price = float(setup_candle["open"])
    close_price = float(setup_candle["close"])
    high_price = float(setup_candle["high"])
    low_price = float(setup_candle["low"])
    
    direction = "NO_SIGNAL"
    entry = close_price
    sl = 0.0
    tp = 0.0
    risk = 0.0
    
    # 2. Base Strategy Rules
    if close_price > open_price:  # Bullish candle
        if close_price < ema50_value:
            direction = "BUY"
            sl = low_price
            risk = entry - sl
    elif close_price < open_price:  # Bearish candle
        if close_price > ema50_value:
            direction = "SELL"
            sl = high_price
            risk = sl - entry
            
    # Doji or direction logic check
    if close_price == open_price:
        logger.info(f"SIGNAL | {symbol} | Setup candle is a Doji (Open={open_price}, Close={close_price}) — skipping.")
        direction = "NO_SIGNAL"
        
    if direction != "NO_SIGNAL" and risk <= 0:
        logger.warning(f"SIGNAL | {symbol} | Setup candle has zero or negative risk (Entry={entry}, SL={sl}) — skipping.")
        direction = "NO_SIGNAL"

    original_direction = direction
    reversed_flag = False
    
    # 3. Apply AUD Special Rule
    aud_cfg = config.get("aud_special_rule", {})
    if direction != "NO_SIGNAL" and currency == "AUD" and aud_cfg.get("enabled", True):
        local_tz = aud_cfg.get("local_timezone", "Australia/Sydney")
        reversal_hour = aud_cfg.get("reversal_before_hour", 8)
        
        event_local_hour = get_local_hour(event_time, local_tz)
        
        if event_local_hour < reversal_hour:
            reversed_flag = True
            if direction == "BUY":
                direction = "SELL"
                sl = high_price
                risk = sl - entry
                logger.info(f"SIGNAL | AUD_RULE | Event at local hour {event_local_hour:02d}:00 (before {reversal_hour:02d}:00) -> Reversing signal BUY -> SELL.")
            elif direction == "SELL":
                direction = "BUY"
                sl = low_price
                risk = entry - sl
                logger.info(f"SIGNAL | AUD_RULE | Event at local hour {event_local_hour:02d}:00 (before {reversal_hour:02d}:00) -> Reversing signal SELL -> BUY.")

    # 4. Final calculations for TP and Pips
    rr_ratio = config.get("trading", {}).get("risk_reward_ratio", 3)
    if direction == "BUY":
        tp = entry + (risk * rr_ratio)
        risk_pips = calculate_pips(symbol, risk)
        reward_pips = calculate_pips(symbol, tp - entry)
    elif direction == "SELL":
        tp = entry - (risk * rr_ratio)
        risk_pips = calculate_pips(symbol, risk)
        reward_pips = calculate_pips(symbol, entry - tp)
    else:
        entry, sl, tp, risk_pips, reward_pips = 0.0, 0.0, 0.0, 0.0, 0.0

    return Signal(
        direction=direction,
        symbol=symbol,
        currency=currency,
        entry=entry,
        sl=sl,
        tp=tp,
        risk_pips=risk_pips,
        reward_pips=reward_pips,
        event_time=event_time,
        original_direction=original_direction,
        reversed=reversed_flag
    )
