import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(logging_config: dict) -> logging.Logger:
    """
    Sets up a rotating file logger and console logger with custom formatting.
    """
    log_file = logging_config.get("log_file", "logs/cpi_system.log")
    
    # Ensure directory for log file exists
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        
    logger = logging.getLogger("cpi_trading_system")
    level_name = logging_config.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    
    # Clear existing handlers to prevent duplicate logs
    if logger.handlers:
        logger.handlers.clear()
        
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Rotating File Handler
    max_bytes = logging_config.get("max_bytes", 10485760)
    backup_count = logging_config.get("backup_count", 5)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger
