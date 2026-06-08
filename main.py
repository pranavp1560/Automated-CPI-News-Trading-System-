import os
import time
import yaml
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

from utils.logger import setup_logger
from utils.validators import validate_config
from core import mt5_connector
from core.scheduler import build_scheduler, fetch_and_schedule_events

# Load environment variables from .env on startup
load_dotenv()

def load_config(config_path: str = "config.yaml") -> dict:
    """
    Loads configuration settings from YAML file.
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def main():
    # 1. Load config
    try:
        config = load_config("config.yaml")
    except Exception as e:
        print(f"CRITICAL | Failed to load config.yaml: {str(e)}")
        return

    # 2. Setup Centralized Logger
    logger = setup_logger(config.get("logging", {}))
    logger.info("=== CPI Trading System Starting ===")

    # 3. Validate Configuration and Environment
    try:
        validate_config(config)
        logger.info("SYSTEM | Configuration and environment validated successfully.")
    except Exception as e:
        logger.error(f"SYSTEM | Configuration validation failed: {str(e)}")
        return

    # 4. Initialize MT5 Connection
    mt5_cfg = config.get("mt5", {})
    if not mt5_connector.initialize(mt5_cfg):
        logger.error("MT5 | Initial connection failed. Bot will run but requires MT5 terminal to be launched for trading.")
        # We don't abort, connector's ensure_connected will try to reconnect during trade jobs,
        # but alert is logged.

    # 5. Build and Start APScheduler
    scheduler = build_scheduler()
    
    # Schedule daily calendar sync at 00:05 UTC
    scheduler.add_job(
        fetch_and_schedule_events,
        trigger='cron',
        hour=0,
        minute=5,
        args=[config, scheduler],
        id='calendar_poll_daily',
        timezone=timezone.utc
    )
    
    scheduler.start()
    logger.info("SCHEDULER | Background scheduler started successfully.")

    # 6. Run Calendar Sync immediately on startup to load events
    logger.info("SYSTEM | Executing initial calendar sync and scheduling tasks...")
    fetch_and_schedule_events(config, scheduler)

    # 7. Keep Alive Heartbeat Loop
    logger.info("=== CPI Trading System is Running (24/5) ===")
    heartbeat_counter = 0
    try:
        while True:
            time.sleep(60)
            heartbeat_counter += 1
            
            # Log heartbeat/status check every 10 minutes to avoid cluttering logs
            if heartbeat_counter >= 10:
                heartbeat_counter = 0
                is_connected = mt5_connector.check_connection()
                conn_status = "CONNECTED" if is_connected else "DISCONNECTED"
                
                # Check list of jobs
                active_jobs = [job.id for job in scheduler.get_jobs() if job.id != 'calendar_poll_daily']
                
                logger.info(
                    f"HEARTBEAT | Status check: MT5={conn_status} | "
                    f"Scheduled trade events: {active_jobs if active_jobs else 'None'}"
                )
                
                # If disconnected, attempt background reconnection
                if not is_connected:
                    logger.warning("MT5 | Heartbeat detected disconnection. Attempting reconnect...")
                    mt5_connector.ensure_connected(mt5_cfg, max_attempts=2, retry_interval=5)
                    
    except (KeyboardInterrupt, SystemExit):
        logger.info("=== CPI Trading System Stopping ===")
        scheduler.shutdown()
        mt5_connector.shutdown()
        logger.info("=== CPI Trading System Stopped ===")

if __name__ == "__main__":
    main()
