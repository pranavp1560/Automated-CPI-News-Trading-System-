import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from core.signal_engine import Signal

# Mock MetaTrader5 module globally
import sys
mock_mt5 = MagicMock()
mock_mt5.TRADE_ACTION_DEAL = 1
mock_mt5.ORDER_TYPE_BUY = 0
mock_mt5.ORDER_TYPE_SELL = 1
mock_mt5.TRADE_RETCODE_DONE = 10009
mock_mt5.TRADE_RETCODE_REQUOTE = 10004
mock_mt5.TRADE_RETCODE_REJECT = 10006
mock_mt5.ORDER_TIME_GTC = 0
mock_mt5.ORDER_FILLING_FOK = 0
mock_mt5.ORDER_FILLING_IOC = 1
mock_mt5.ORDER_FILLING_RETURN = 2
mock_mt5.SYMBOL_TRADE_MODE_DISABLED = 0
mock_mt5.SYMBOL_TRADE_MODE_FULL = 4

sys.modules['MetaTrader5'] = mock_mt5

# Now import trade_executor
from core.trade_executor import execute_trade, is_market_open

@pytest.fixture(autouse=True)
def reset_mock_mt5():
    mock_mt5.reset_mock()
    # Setup default mock values
    mock_mt5.terminal_info.return_value = MagicMock(connected=True)
    
    mock_sym = MagicMock()
    mock_sym.visible = True
    mock_sym.trade_mode = 4 # full access
    mock_sym.filling_mode = 3 # FOK and IOC supported
    mock_mt5.symbol_info.return_value = mock_sym
    
    mock_mt5.positions_get.return_value = [] # no open positions
    
    mock_tick = MagicMock()
    mock_tick.ask = 1.0005
    mock_tick.bid = 1.0004
    mock_mt5.symbol_info_tick.return_value = mock_tick
    
    mock_mt5.order_calc_margin.return_value = 10.0 # margin required
    mock_mt5.account_info.return_value = MagicMock(margin_free=1000.0) # free margin
    
    # Default success response
    mock_res = MagicMock()
    mock_res.retcode = mock_mt5.TRADE_RETCODE_DONE
    mock_res.order = 999999
    mock_res.price = 1.0005
    mock_mt5.order_send.return_value = mock_res

@pytest.fixture
def mock_signal():
    return Signal(
        direction="BUY",
        symbol="EURUSD",
        currency="EUR",
        entry=1.0000,
        sl=0.9990,
        tp=1.0030,
        risk_pips=10.0,
        reward_pips=30.0,
        event_time=datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc),
        original_direction="BUY"
    )

@pytest.fixture
def base_config():
    return {
        "trading": {
            "magic_number": 20240101,
            "lot_size": 0.01,
            "deviation": 20
        }
    }

def test_market_hours_checker():
    # Test market open hours logic (standard Forex Sunday 22:00 to Friday 22:00 UTC)
    # Wednesday 12:00 UTC -> Open
    with patch("core.trade_executor.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        assert is_market_open() is True
        
    # Saturday 12:00 UTC -> Closed
    with patch("core.trade_executor.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
        assert is_market_open() is False

@patch("core.trade_executor.is_market_open", return_value=True)
@patch("core.trade_executor.write_trade_to_log")
def test_order_request_structure(mock_log, mock_open, mock_signal, base_config):
    # Execute buy order
    ticket = execute_trade(mock_signal, base_config)
    
    assert ticket == 999999
    mock_mt5.order_send.assert_called_once()
    
    # Verify request payload
    req = mock_mt5.order_send.call_args[0][0]
    assert req["symbol"] == "EURUSD"
    assert req["volume"] == 0.01
    assert req["type"] == mock_mt5.ORDER_TYPE_BUY
    assert req["price"] == 1.0005
    assert req["sl"] == 0.9990
    assert req["tp"] == 1.0030
    assert req["magic"] == 20240101
    assert req["deviation"] == 20
    # filling_mode = 3 (3 & 1 = 1 -> ORDER_FILLING_FOK)
    assert req["type_filling"] == mock_mt5.ORDER_FILLING_FOK

@patch("core.trade_executor.is_market_open", return_value=True)
def test_duplicate_trade_prevention(mock_open, mock_signal, base_config):
    # Mock positions_get to return an active position (preventing duplicate trade)
    mock_mt5.positions_get.return_value = [MagicMock()]
    
    ticket = execute_trade(mock_signal, base_config)
    
    assert ticket == -1
    mock_mt5.order_send.assert_not_called()

@patch("core.trade_executor.is_market_open", return_value=True)
@patch("core.trade_executor.write_trade_to_log")
def test_retry_on_requote(mock_log, mock_open, mock_signal, base_config):
    # Mock first order send to fail with REQUOTE, and second send to complete
    first_res = MagicMock()
    first_res.retcode = mock_mt5.TRADE_RETCODE_REQUOTE
    
    second_res = MagicMock()
    second_res.retcode = mock_mt5.TRADE_RETCODE_DONE
    second_res.order = 777777
    second_res.price = 1.0006
    
    mock_mt5.order_send.side_effect = [first_res, second_res]
    
    # Tick ask rises slightly on requote
    mock_tick_new = MagicMock(ask=1.0006, bid=1.0005)
    mock_mt5.symbol_info_tick.side_effect = [
        MagicMock(ask=1.0005), # First call
        mock_tick_new          # Second call (requote retry)
    ]
    
    ticket = execute_trade(mock_signal, base_config)
    
    # Verify retry succeeded
    assert ticket == 777777
    assert mock_mt5.order_send.call_count == 2
    
    # Check that price was updated in second request
    second_req = mock_mt5.order_send.call_args_list[1][0][0]
    assert second_req["price"] == 1.0006
