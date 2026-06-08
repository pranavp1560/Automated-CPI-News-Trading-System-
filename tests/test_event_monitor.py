import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from core.event_monitor import EventMonitor, CPIEvent, CalendarCache
from utils.time_utils import get_setup_candle_times, parse_iso_datetime

# Mock MT5
import sys
sys.modules['MetaTrader5'] = MagicMock()

@pytest.fixture
def base_config():
    return {
        "allowed_currencies": ["AUD", "GBP", "EUR", "NZD", "CAD", "CHF", "JPY"],
        "ignored_currencies": ["USD"],
        "calendar": {
            "cache_expiry_hours": 12
        }
    }

@pytest.fixture
def mock_faireconomy_data():
    return [
        {
            "title": "CPI y/y",
            "country": "USD",  # Ignored
            "impact": "High",
            "date": "2026-06-07T10:00:00-04:00",
            "forecast": "3.1%",
            "previous": "3.0%"
        },
        {
            "title": "CPI q/q",
            "country": "AUD",  # Allowed
            "impact": "High",
            "date": "2026-06-07T11:30:00-04:00",
            "forecast": "0.8%",
            "previous": "0.6%"
        },
        {
            "title": "CPI m/m",
            "country": "EUR",  # Allowed
            "impact": "High",
            "date": "2026-06-07T12:00:00-04:00",
            "forecast": "0.2%",
            "previous": "0.1%"
        },
        {
            "title": "CPI m/m",
            "country": "INR",  # Disallowed currency
            "impact": "High",
            "date": "2026-06-07T13:00:00-04:00",
            "forecast": "0.5%",
            "previous": "0.4%"
        },
        {
            "title": "Unemployment Rate",  # Non-CPI event
            "country": "GBP",
            "impact": "High",
            "date": "2026-06-07T09:30:00-04:00",
            "forecast": "4.2%",
            "previous": "4.1%"
        },
        {
            "title": "CPI y/y",
            "country": "GBP",  # Allowed but Medium impact (Filtered out)
            "impact": "Medium",
            "date": "2026-06-07T14:30:00-04:00",
            "forecast": "2.1%",
            "previous": "2.2%"
        }
    ]

def test_setup_candle_time_calculation():
    # Test M5 flooring and offset of 5/10 minutes
    event_time = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    setup_open, setup_close = get_setup_candle_times(event_time)
    
    assert setup_open == datetime(2026, 6, 7, 5, 50, tzinfo=timezone.utc)
    assert setup_close == datetime(2026, 6, 7, 5, 55, tzinfo=timezone.utc)
    
    # Non-floored event time
    event_time2 = datetime(2026, 6, 7, 8, 33, tzinfo=timezone.utc)
    setup_open2, setup_close2 = get_setup_candle_times(event_time2)
    # 08:33 floors to 08:30. setup_open = 08:20, setup_close = 08:25
    assert setup_open2 == datetime(2026, 6, 7, 8, 20, tzinfo=timezone.utc)
    assert setup_close2 == datetime(2026, 6, 7, 8, 25, tzinfo=timezone.utc)

def test_parse_iso_datetime():
    dt = parse_iso_datetime("2026-06-07T10:00:00-04:00")
    # -4 offset means 14:00 UTC
    assert dt.astimezone(timezone.utc).hour == 14
    
    dt2 = parse_iso_datetime("2026-06-07T12:00:00Z")
    assert dt2.astimezone(timezone.utc).hour == 12

@patch("core.event_monitor.requests.get")
def test_usd_cpi_filtered_out_and_allowed_currencies_pass(mock_get, base_config, mock_faireconomy_data):
    # Setup mock request
    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_faireconomy_data
    mock_resp.status_code = 200
    mock_get.return_value = mock_resp
    
    # We will patch out caching so it doesn't write to disk
    with patch("core.event_monitor.CalendarCache.save") as mock_save, \
         patch("core.event_monitor.CalendarCache.load") as mock_load:
        
        # Override datetime now to be Junes 7, 2026 00:00 UTC
        fake_now = datetime(2026, 6, 7, 0, 0, tzinfo=timezone.utc)
        
        with patch("core.event_monitor.datetime") as mock_datetime:
            mock_datetime.now.return_value = fake_now
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            
            monitor = EventMonitor(base_config)
            # Force refresh to make API call
            events = monitor.fetch_upcoming_cpi_events(force_refresh=True)
            
            # Assert only AUD CPI and EUR CPI passed
            # - USD is filtered out
            # - INR is disallowed and filtered out
            # - GBP Unemployment is filtered out (no CPI)
            # - GBP CPI Medium impact is filtered out (only High impact)
            assert len(events) == 2
            
            currencies = [e.currency for e in events]
            assert "AUD" in currencies
            assert "EUR" in currencies
            assert "USD" not in currencies
            assert "INR" not in currencies

@patch("core.event_monitor.requests.get")
def test_api_failure_fallback_to_cache(mock_get, base_config):
    # Mock requests to raise connection error
    mock_get.side_effect = Exception("API Connection Failed")
    
    monitor = EventMonitor(base_config)
    
    # Mock cache existence and loading
    cached_event = CPIEvent(
        id="CPI_EUR_20260607_1200",
        currency="EUR",
        event_name="CPI m/m",
        event_time=datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc)
    )
    
    with patch("core.event_monitor.CalendarCache.exists") as mock_exists, \
         patch("core.event_monitor.CalendarCache.load") as mock_load:
         
        mock_exists.return_value = True
        mock_load.return_value = [cached_event]
        
        fake_now = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
        with patch("core.event_monitor.datetime") as mock_datetime:
            mock_datetime.now.return_value = fake_now
            
            events = monitor.fetch_upcoming_cpi_events(force_refresh=True)
            
            assert len(events) == 1
            assert events[0].currency == "EUR"
            assert events[0].id == "CPI_EUR_20260607_1200"
