import logging
import MetaTrader5 as mt5

logger = logging.getLogger("cpi_trading_system")

SYMBOL_MAP = {
    "EUR": "EURUSD",
    "GBP": "GBPUSD",
    "AUD": "AUDUSD",
    "NZD": "NZDUSD",
    "CAD": "USDCAD",
    "CHF": "USDCHF",
    "JPY": "USDJPY",
}

def get_symbol(currency: str, config: dict) -> str:
    """
    Resolves the currency to the broker-specific trading symbol name.
    Applies optional broker-specific prefix and suffix from the config.
    """
    currency_upper = currency.upper()
    base_symbol = SYMBOL_MAP.get(currency_upper)
    if not base_symbol:
        raise ValueError(f"No symbol mapping for currency: {currency}")
        
    trading_cfg = config.get("trading", {})
    prefix = trading_cfg.get("symbol_prefix", "")
    suffix = trading_cfg.get("symbol_suffix", "")
    
    return f"{prefix}{base_symbol}{suffix}"

def validate_symbol_tradeable(symbol: str) -> bool:
    """
    Validates that a symbol exists on the broker and is currently tradeable.
    Attempts to select it in the Market Watch if not already selected.
    """
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        logger.error(f"MT5 | Symbol {symbol} not found on broker.")
        return False
        
    # Check if the symbol is visible in Market Watch; if not, try to select it
    if not symbol_info.visible:
        selected = mt5.symbol_select(symbol, True)
        if not selected:
            logger.error(f"MT5 | Symbol {symbol} could not be selected in Market Watch.")
            return False
        # Re-fetch info after selection
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.error(f"MT5 | Failed to re-fetch info for symbol {symbol} after selection.")
            return False
            
    # Check broker trade mode for this symbol
    # trade_mode: 0 = disabled, 1 = long only, 2 = short only, 3 = close only, 4 = full access
    if symbol_info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
        logger.error(f"MT5 | Trading is disabled for symbol {symbol}.")
        return False
        
    return True
