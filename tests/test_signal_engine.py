import pytest
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from core.signal_engine import (
    generate_signal,
    calculate_ema,
    calculate_pips,
    find_setup_candle
)

# Mock MT5 module for testing
import sys
sys.modules['MetaTrader5'] = MagicMock()

@pytest.fixture
def base_config():
    return {
        "trading": {
            "timeframe": "M5",
            "ema_period": 50,
            "ema_lookback_candles": 200,
            "risk_reward_ratio": 3,
            "lot_size": 0.01,
            "magic_number": 20240101,
            "slippage": 10,
            "deviation": 20
        },
        "aud_special_rule": {
            "enabled": True,
            "reversal_before_hour": 8,
            "local_timezone": "Australia/Sydney"
        }
    }

def create_mock_candles(setup_open, setup_close, setup_high, setup_low, setup_time_unix, rest_close=1.0):
    """
    Helper to create 200 mock candles. The candle at index -2 (second to last)
    is the setup candle. Other candles have a close price of `rest_close` to make
    EMA50 calculations simple and predictable.
    """
    records = []
    base_time = setup_time_unix - 198 * 300  # Generate 198 candles prior
    
    # 1. Fill first 198 candles
    for i in range(198):
        records.append({
            "time": base_time + i * 300,
            "open": rest_close,
            "high": rest_close,
            "low": rest_close,
            "close": rest_close,
            "tick_volume": 10,
            "spread": 1,
            "real_volume": 0
        })
        
    # 2. Add the setup candle at index -2 (199th element)
    records.append({
        "time": setup_time_unix,
        "open": setup_open,
        "high": setup_high,
        "low": setup_low,
        "close": setup_close,
        "tick_volume": 15,
        "spread": 1,
        "real_volume": 0
    })
    
    # 3. Add the active/forming candle at index -1 (200th element)
    records.append({
        "time": setup_time_unix + 300,
        "open": setup_close,
        "high": setup_close * 1.01,
        "low": setup_close * 0.99,
        "close": setup_close,
        "tick_volume": 2,
        "spread": 1,
        "real_volume": 0
    })
    
    return pd.DataFrame(records)

def test_calculate_ema():
    closes = pd.Series([1.0] * 100)
    ema = calculate_ema(closes, period=50)
    assert ema.iloc[-1] == 1.0

def test_calculate_pips():
    # JPY pair
    assert calculate_pips("USDJPY", 0.05) == 5.0
    # Standard pair
    assert calculate_pips("EURUSD", 0.0005) == 5.0

@patch("core.signal_engine.find_setup_candle")
def test_buy_signal_below_ema(mock_find, base_config):
    # Mock find_setup_candle to return a specific setup candle and EMA50
    # Setup candle: Open = 0.90, Close = 0.95 (Bullish), Low = 0.88, High = 0.96
    # EMA50 = 1.00 (Price 0.95 is below EMA50)
    setup_candle = pd.Series({
        "open": 0.90, "close": 0.95, "high": 0.96, "low": 0.943
    })
    mock_find.return_value = (setup_candle, 1.00, 0.0)
    
    event_time = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    df = pd.DataFrame() # dummy
    
    signal = generate_signal(df, event_time, "EURUSD", "EUR", base_config)
    
    assert signal.direction == "BUY"
    assert signal.entry == 0.95
    assert signal.sl == 0.943
    # TP = Entry + 3 * Risk = 0.95 + 3 * (0.95 - 0.943) = 0.95 + 0.021 = 0.971
    assert abs(signal.tp - 0.971) < 1e-6
    assert signal.risk_pips == 70.0
    assert signal.reward_pips == 210.0
    assert signal.reversed is False

@patch("core.signal_engine.find_setup_candle")
def test_sell_signal_above_ema(mock_find, base_config):
    # Setup candle: Open = 1.10, Close = 1.05 (Bearish), Low = 1.02, High = 1.12
    # EMA50 = 1.00 (Price 1.05 is above EMA50)
    setup_candle = pd.Series({
        "open": 1.10, "close": 1.05, "high": 1.057, "low": 1.02
    })
    mock_find.return_value = (setup_candle, 1.00, 0.0)
    
    event_time = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    df = pd.DataFrame()
    
    signal = generate_signal(df, event_time, "EURUSD", "EUR", base_config)
    
    assert signal.direction == "SELL"
    assert signal.entry == 1.05
    assert signal.sl == 1.057
    # TP = Entry - 3 * Risk = 1.05 - 3 * (1.057 - 1.05) = 1.05 - 0.021 = 1.029
    assert abs(signal.tp - 1.029) < 1e-6
    assert signal.risk_pips == 70.0
    assert signal.reward_pips == 210.0
    assert signal.reversed is False

@patch("core.signal_engine.find_setup_candle")
def test_no_signal_bullish_above_ema(mock_find, base_config):
    # Setup candle: Bullish (close > open) but close is ABOVE EMA50 (1.05 > 1.00)
    setup_candle = pd.Series({
        "open": 1.02, "close": 1.05, "high": 1.08, "low": 1.00
    })
    mock_find.return_value = (setup_candle, 1.00, 0.0)
    
    event_time = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    df = pd.DataFrame()
    
    signal = generate_signal(df, event_time, "EURUSD", "EUR", base_config)
    assert signal.direction == "NO_SIGNAL"

@patch("core.signal_engine.find_setup_candle")
def test_no_signal_bearish_below_ema(mock_find, base_config):
    # Setup candle: Bearish (close < open) but close is BELOW EMA50 (0.95 < 1.00)
    setup_candle = pd.Series({
        "open": 0.98, "close": 0.95, "high": 1.00, "low": 0.92
    })
    mock_find.return_value = (setup_candle, 1.00, 0.0)
    
    event_time = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    df = pd.DataFrame()
    
    signal = generate_signal(df, event_time, "EURUSD", "EUR", base_config)
    assert signal.direction == "NO_SIGNAL"

@patch("core.signal_engine.find_setup_candle")
def test_no_signal_doji(mock_find, base_config):
    # Setup candle: Open == Close (Doji)
    setup_candle = pd.Series({
        "open": 1.00, "close": 1.00, "high": 1.05, "low": 0.95
    })
    mock_find.return_value = (setup_candle, 1.00, 0.0)
    
    event_time = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    df = pd.DataFrame()
    
    signal = generate_signal(df, event_time, "EURUSD", "EUR", base_config)
    assert signal.direction == "NO_SIGNAL"

@patch("core.signal_engine.find_setup_candle")
def test_aud_reversal_before_0800(mock_find, base_config):
    # Event local time (Sydney) is 06:00 AEST (which is before 08:00)
    # Event time: 2026-06-07 20:00 UTC -> June 8, 2026 06:00 Sydney (AEST is UTC+10)
    event_time_utc = datetime(2026, 6, 7, 20, 0, tzinfo=timezone.utc)
    
    # Original direction would be BUY (Bullish candle 0.95 < EMA 1.00)
    setup_candle = pd.Series({
        "open": 0.90, "close": 0.95, "high": 0.952, "low": 0.88
    })
    mock_find.return_value = (setup_candle, 1.00, 0.0)
    df = pd.DataFrame()
    
    signal = generate_signal(df, event_time_utc, "AUDUSD", "AUD", base_config)
    
    # Assert reversed direction to SELL
    assert signal.direction == "SELL"
    assert signal.reversed is True
    assert signal.original_direction == "BUY"
    # Recalculated SL for SELL should be candle High = 0.952
    assert signal.sl == 0.952
    # Recalculated risk = SL - Entry = 0.952 - 0.95 = 0.002
    # Recalculated TP = Entry - 3 * Risk = 0.95 - 0.006 = 0.944
    assert abs(signal.tp - 0.944) < 1e-6
    assert signal.risk_pips == 20.0
    assert signal.reward_pips == 60.0

@patch("core.signal_engine.find_setup_candle")
def test_aud_no_reversal_after_0800(mock_find, base_config):
    # Event local time (Sydney) is 11:30 AEST (after 08:00)
    # Event time: 2026-06-07 01:30 UTC -> 11:30 Sydney
    event_time_utc = datetime(2026, 6, 7, 1, 30, tzinfo=timezone.utc)
    
    # Original direction is BUY
    setup_candle = pd.Series({
        "open": 0.90, "close": 0.95, "high": 0.97, "low": 0.88
    })
    mock_find.return_value = (setup_candle, 1.00, 0.0)
    df = pd.DataFrame()
    
    signal = generate_signal(df, event_time_utc, "AUDUSD", "AUD", base_config)
    
    # Normal execution (BUY)
    assert signal.direction == "BUY"
    assert signal.reversed is False
    assert signal.sl == 0.88
    assert abs(signal.tp - 1.16) < 1e-6
