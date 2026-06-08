import os
import time
import logging
import MetaTrader5 as mt5

logger = logging.getLogger("cpi_trading_system")

def get_credentials(mt5_config: dict) -> tuple[int, str, str, int]:
    """
    Retrieves login, password, server, and timeout, allowing environment variable overrides.
    """
    login_val = os.getenv("MT5_LOGIN", mt5_config.get("login"))
    password = os.getenv("MT5_PASSWORD", mt5_config.get("password", ""))
    server = os.getenv("MT5_SERVER", mt5_config.get("server", ""))
    timeout = mt5_config.get("timeout", 10000)

    if login_val is None:
        raise ValueError("MT5 login ID is missing")
    
    return int(login_val), str(password), str(server), int(timeout)

def initialize(mt5_config: dict) -> bool:
    """
    Establishes connection to the MetaTrader 5 terminal.
    """
    try:
        login, password, server, timeout = get_credentials(mt5_config)
        logger.info(f"MT5 | Initializing connection to server: {server}, login: {login}...")
        
        # Initialize MT5 connection
        initialized = mt5.initialize(
            login=login,
            password=password,
            server=server,
            timeout=timeout
        )
        
        if not initialized:
            error_code = mt5.last_error()
            logger.error(f"MT5 | Connection failed. Error code: {error_code}")
            return False
            
        # Verify terminal info
        terminal_info = mt5.terminal_info()
        if terminal_info is None:
            logger.error("MT5 | Failed to get terminal info after initialization.")
            mt5.shutdown()
            return False
            
        logger.info(f"MT5 | Connection established. Connected to: {terminal_info.name} ({terminal_info.company})")
        return True
    except Exception as e:
        logger.error(f"MT5 | Error initializing MT5 connection: {str(e)}")
        return False

def check_connection() -> bool:
    """
    Checks if the MT5 terminal is currently connected to the broker.
    """
    try:
        terminal_info = mt5.terminal_info()
        if terminal_info is None:
            return False
        # terminal_info.connected is a boolean representing connection to trade server
        return getattr(terminal_info, "connected", False)
    except Exception:
        return False

def ensure_connected(mt5_config: dict, max_attempts: int = 5, retry_interval: int = 30) -> bool:
    """
    Verifies connection and attempts to reconnect if disconnected.
    Returns True if connected, False if all reconnection attempts fail.
    """
    if check_connection():
        return True
        
    logger.warning("MT5 | Connection lost or not initialized. Attempting auto-reconnect...")
    for attempt in range(1, max_attempts + 1):
        logger.info(f"MT5 | Connection lost — attempting reconnect (attempt {attempt}/{max_attempts})")
        
        # Shutdown before re-initializing
        try:
            mt5.shutdown()
        except Exception:
            pass
            
        if initialize(mt5_config):
            if check_connection():
                logger.info("MT5 | Reconnection successful.")
                return True
                
        logger.warning(f"MT5 | Reconnect attempt {attempt} failed. Waiting {retry_interval}s...")
        time.sleep(retry_interval)
        
    logger.error("MT5 | Critical: All MT5 reconnection attempts failed.")
    return False

def shutdown():
    """
    Closes the connection to MetaTrader 5 terminal.
    """
    logger.info("MT5 | Shutting down connection...")
    try:
        mt5.shutdown()
    except Exception as e:
        logger.error(f"MT5 | Error during shutdown: {str(e)}")
