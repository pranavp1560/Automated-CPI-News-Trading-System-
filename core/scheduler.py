import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict
import pandas as pd
import MetaTrader5 as mt5

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.executors.pool import ThreadPoolExecutor

from core.event_monitor import EventMonitor, CPIEvent
from core.symbol_mapper import get_symbol, validate_symbol_tradeable
from core.signal_engine import generate_signal, Signal
from core.trade_executor import execute_trade
from core.mt5_connector import ensure_connected
from utils.time_utils import get_setup_candle_times

logger = logging.getLogger("cpi_trading_system")

# Global dict to track scheduled jobs: { event_id: job_id }
scheduled_jobs: Dict[str, str] = {}

def analyze_and_trade(event: CPIEvent, config: dict):
    """
    Job task triggered at setup candle close.
    Fetches M5 historical candles, calculates strategy signals, and places trades.
    """
    currency = event.currency
    event_time = event.event_time
    symbol = "UNKNOWN"
    
    logger.info(f"SIGNAL | Running analysis job for {currency} CPI event (Scheduled time: {event_time:%Y-%m-%d %H:%M} UTC)...")
    
    try:
        # 1. MT5 connection check
        mt5_cfg = config.get("mt5", {})
        if not ensure_connected(mt5_cfg, max_attempts=3, retry_interval=5):
            logger.error("MT5 | Reconnection failed. Cannot proceed with trade analysis.")
            return

        # 2. Get symbol and validate
        symbol = get_symbol(currency, config)
        if not validate_symbol_tradeable(symbol):
            logger.error(f"MT5 | Symbol {symbol} not active or tradeable. Skipping event.")
            return
            
        # 3. Fetch candle rates (200 M5 candles)
        trading_cfg = config.get("trading", {})
        lookback = trading_cfg.get("ema_lookback_candles", 200)
        
        # Retry mechanism for fetching rates (Retry once after 3s)
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, lookback)
        if rates is None or len(rates) < lookback:
            logger.warning("MT5 | Candle data unavailable. Retrying once in 3s...")
            time.sleep(3)
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, lookback)
            if rates is None or len(rates) < lookback:
                logger.error(f"MT5 | Error fetching rates for {symbol} after retry. skipping trade analysis.")
                return

        # Convert to DataFrame
        df = pd.DataFrame(rates)
        
        # 4. Generate Strategy Signal
        signal = generate_signal(df, event_time, symbol, currency, config)
        
        # 5. Handle Signal Execution
        if signal.direction == "NO_SIGNAL":
            # Determine candle open/close direction for specific logging matching spec
            # Setup candle is second to last
            setup_c = df.iloc[-2]
            op, cl = setup_c["open"], setup_c["close"]
            direction_desc = "Bullish" if cl > op else ("Bearish" if cl < op else "Doji")
            
            # Retrieve EMA value for logging
            # To be safe, calculate EMA series on closing prices
            closes_ema = df["close"].ewm(span=50, adjust=False).mean()
            ema_val = closes_ema.iloc[-2]
            
            rel_desc = "ABOVE" if cl > ema_val else "BELOW"
            logger.info(f"NO_SIGNAL | {symbol} | {direction_desc} candle {rel_desc} EMA50 — no valid setup")
            return
            
        # Log signal parameters
        logger.info(f"SIGNAL | {symbol} | {signal.direction} | Entry: {signal.entry:.5f} | SL: {signal.sl:.5f} | TP: {signal.tp:.5f}")
        
        # 6. Place Order
        ticket = execute_trade(signal, config)
        if ticket != -1:
            logger.info(f"TRADE | Execution completed. Order placed for {symbol} | Ticket: {ticket}")
        else:
            logger.error(f"TRADE | Execution failed for {symbol}.")
            
    except Exception as e:
        logger.error(f"SYSTEM | Exception occurred during analysis and trade execution for {symbol}: {str(e)}", exc_info=True)

def fetch_and_schedule_events(config: dict, scheduler: BackgroundScheduler):
    """
    Syncs calendar events and schedules trade tasks.
    Runs daily (or on startup).
    """
    logger.info("CALENDAR | Starting calendar events check...")
    
    try:
        monitor = EventMonitor(config)
        events = monitor.fetch_upcoming_cpi_events()
        
        now = datetime.now(timezone.utc)
        
        for event in events:
            # Check if already scheduled
            if event.id in scheduled_jobs:
                # Double check if job still exists in scheduler
                existing_job = scheduler.get_job(scheduled_jobs[event.id])
                if existing_job:
                    continue
            
            # Identify setup candle close time (event_time - 5 minutes)
            _, setup_close_time = get_setup_candle_times(event.event_time)
            
            # Trigger exactly at setup candle close + 2 second buffer
            trigger_time = setup_close_time + timedelta(seconds=2)
            
            # If trigger time is in the past, skip scheduling
            if trigger_time <= now:
                logger.info(f"CALENDAR | SKIP | Event {event.event_name} ({event.currency}) setup candle close has already passed: {trigger_time:%Y-%m-%d %H:%M:%S} UTC.")
                continue
                
            # Schedule one-shot trade job
            symbol = get_symbol(event.currency, config)
            job = scheduler.add_job(
                analyze_and_trade,
                trigger=DateTrigger(run_date=trigger_time, timezone=timezone.utc),
                args=[event, config],
                id=event.id
            )
            
            # Track job
            scheduled_jobs[event.id] = job.id
            logger.info(f"EVENT     | {event.event_name} scheduled for {event.event_time:%Y-%m-%d %H:%M} UTC | Symbol: {symbol} | Trigger: {trigger_time:%Y-%m-%d %H:%M:%S} UTC")
            
        # Clean up stale jobs in tracking dict (older than 2 hours)
        cleanup_scheduled_jobs()
        
    except Exception as e:
        logger.error(f"CALENDAR | Error fetching and scheduling events: {str(e)}", exc_info=True)

def cleanup_scheduled_jobs():
    """
    Cleans up expired job tracking entries from memory.
    """
    now = datetime.now(timezone.utc)
    expired_ids = []
    
    for event_id, job_id in list(scheduled_jobs.items()):
        # Event ID contains time in format YYYYMMDD_HHMM: e.g. "CPI_AUD_20260607_1800"
        try:
            parts = event_id.split("_")
            if len(parts) >= 4:
                date_str = f"{parts[-2]}_{parts[-1]}"
                event_time = datetime.strptime(date_str, "%Y%m%d_%H%M").replace(tzinfo=timezone.utc)
                # If event was more than 2 hours ago, clean it up
                if now - event_time > timedelta(hours=2):
                    expired_ids.append(event_id)
        except Exception:
            # Fallback if ID structure changes: skip cleanup
            pass
            
    for eid in expired_ids:
        del scheduled_jobs[eid]
        
    if expired_ids:
        logger.info(f"SCHEDULER | Cleaned up {len(expired_ids)} expired job registrations from memory.")

def build_scheduler() -> BackgroundScheduler:
    """
    Builds the background scheduler.
    """
    executors = {
        'default': ThreadPoolExecutor(max_workers=10)
    }
    scheduler = BackgroundScheduler(executors=executors, timezone=timezone.utc)
    return scheduler
