import os
import csv
import logging
from datetime import datetime, timezone
import MetaTrader5 as mt5

from core.signal_engine import Signal
from core.symbol_mapper import validate_symbol_tradeable

logger = logging.getLogger("cpi_trading_system")

def is_market_open() -> bool:
    """
    Checks if the Forex market is open based on standard UTC market hours:
    Sunday 22:00 UTC to Friday 22:00 UTC.
    """
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()  # Monday=0, ..., Sunday=6
    hour = now_utc.hour
    
    # Closed all Saturday
    if weekday == 5:
        return False
    # Closed Friday night after 22:00 UTC
    if weekday == 4 and hour >= 22:
        return False
    # Closed Sunday before 22:00 UTC
    if weekday == 6 and hour < 22:
        return False
        
    return True

def has_open_positions(symbol: str, magic_number: int) -> bool:
    """
    Checks if there are any open positions for the given symbol and magic number.
    """
    positions = mt5.positions_get(symbol=symbol, magic=magic_number)
    if positions is None:
        logger.error(f"MT5 | Failed to fetch positions for {symbol}.")
        return True  # Block trade as a precaution if query fails
    return len(positions) > 0

def has_sufficient_margin(symbol: str, order_type: int, lot_size: float, price: float) -> bool:
    """
    Calculates the margin required for the trade and checks if the account has enough free margin.
    """
    margin = mt5.order_calc_margin(order_type, symbol, lot_size, price)
    if margin is None:
        logger.error(f"MT5 | Failed to calculate margin for {symbol}.")
        return False
        
    account = mt5.account_info()
    if account is None:
        logger.error("MT5 | Failed to retrieve account info for margin check.")
        return False
        
    logger.info(f"TRADE | Margin check: Required={margin:.2f}, Free={account.margin_free:.2f}")
    return account.margin_free >= margin

def write_trade_to_log(signal: Signal, ticket: int, lot_size: float, result_str: str, csv_path: str = "data/trade_log.csv"):
    """
    Writes the trade details to a persistent CSV log.
    """
    dir_name = os.path.dirname(csv_path)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name, exist_ok=True)
        
    file_exists = os.path.exists(csv_path)
    
    headers = [
        "timestamp", "currency", "symbol", "direction", "entry", "sl", "tp",
        "risk_pips", "reward_pips", "lot", "ticket", "aud_reversed", "result"
    ]
    
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "currency": signal.currency,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "entry": signal.entry,
        "sl": signal.sl,
        "tp": signal.tp,
        "risk_pips": signal.risk_pips,
        "reward_pips": signal.reward_pips,
        "lot": lot_size,
        "ticket": ticket,
        "aud_reversed": "YES" if signal.reversed else "NO",
        "result": result_str
    }
    
    try:
        with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
        logger.info(f"TRADE | Logged trade results for ticket {ticket} to CSV.")
    except Exception as e:
        logger.error(f"TRADE | Failed to write trade to CSV log: {str(e)}")

def execute_trade(signal: Signal, config: dict) -> int:
    """
    Preforms pre-trade checks and submits a market order to MT5.
    Handles requotes and rejections.
    Returns: The order ticket number if successful, or -1 if failed.
    """
    symbol = signal.symbol
    direction = signal.direction
    
    if direction == "NO_SIGNAL":
        logger.info(f"TRADE | {symbol} | No valid signal. Skipping execution.")
        return -1
        
    logger.info(f"TRADE | {symbol} | Preparing to execute {direction} order...")
    
    # 1. Pre-trade check: MT5 connection
    terminal_info = mt5.terminal_info()
    if terminal_info is None:
        logger.error("TRADE | MT5 terminal not connected. Skipping trade.")
        return -1
        
    # 2. Pre-trade check: Market open
    if not is_market_open():
        logger.warning(f"TRADE | {symbol} | Market is currently closed. Skipping trade.")
        return -1
        
    # 3. Pre-trade check: Symbol active and tradeable
    if not validate_symbol_tradeable(symbol):
        logger.error(f"TRADE | {symbol} | Symbol not available or disabled for trading. Skipping trade.")
        return -1
        
    trading_cfg = config.get("trading", {})
    magic_number = trading_cfg.get("magic_number", 20240101)
    lot_size = trading_cfg.get("lot_size", 0.01)
    
    # 4. Pre-trade check: Duplicate positions check
    if has_open_positions(symbol, magic_number):
        logger.warning(f"TRADE | {symbol} | Open position with magic number {magic_number} already exists. Skipping duplicate trade.")
        return -1
        
    # Resolve order parameters
    if direction == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price_field = "ask"
    elif direction == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        price_field = "bid"
    else:
        logger.error(f"TRADE | {symbol} | Invalid direction: {direction}")
        return -1
        
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"TRADE | {symbol} | Could not fetch symbol price tick. Skipping trade.")
        return -1
        
    price = getattr(tick, price_field)
    
    # 5. Pre-trade check: Margin check
    if not has_sufficient_margin(symbol, order_type, lot_size, price):
        logger.error(f"TRADE | {symbol} | Insufficient margin. Skipping trade.")
        return -1
        
    # 6. Pre-trade check: Valid SL/TP
    if direction == "BUY" and not (signal.sl < signal.entry < signal.tp):
        logger.error(f"TRADE | {symbol} | Inverted BUY SL/TP levels: SL={signal.sl}, Entry={signal.entry}, TP={signal.tp}")
        return -1
    elif direction == "SELL" and not (signal.sl > signal.entry > signal.tp):
        logger.error(f"TRADE | {symbol} | Inverted SELL SL/TP levels: SL={signal.sl}, Entry={signal.entry}, TP={signal.tp}")
        return -1

    # Get dynamic filling mode
    symbol_info = mt5.symbol_info(symbol)
    filling_mode = symbol_info.filling_mode
    if filling_mode & 1:  # SYMBOL_FILLING_FOK
        type_filling = mt5.ORDER_FILLING_FOK
    elif filling_mode & 2:  # SYMBOL_FILLING_IOC
        type_filling = mt5.ORDER_FILLING_IOC
    else:
        type_filling = mt5.ORDER_FILLING_RETURN
        
    # Create request
    deviation = trading_cfg.get("deviation", 20)
    comment = f"CPI_{signal.currency}_{signal.event_time:%Y%m%d%H%M}"
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": signal.sl,
        "tp": signal.tp,
        "deviation": deviation,
        "magic": magic_number,
        "comment": comment[:31],  # Limit comment length to 31 chars (MT5 constraint)
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": type_filling,
    }
    
    # Helper to send order
    def send_order_request(req):
        logger.info(f"TRADE | Sending order: {req['symbol']} {direction} {req['volume']} @ {req['price']} (SL: {req['sl']}, TP: {req['tp']})")
        res = mt5.order_send(req)
        return res

    result = send_order_request(request)
    
    # Handle response codes
    if result is None:
        logger.error("TRADE | Order send failed. No response received from MT5.")
        return -1
        
    retcode = result.retcode
    if retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"TRADE | Success | Order placed. Ticket: {result.order} | Symbol: {symbol} | Price: {result.price}")
        logger.info(f"TRADE | SL: {signal.sl} | TP: {signal.tp} | Risk: {signal.risk_pips} pips | Reward: {signal.reward_pips} pips")
        write_trade_to_log(signal, result.order, lot_size, "SUCCESS")
        return result.order
        
    elif retcode == mt5.TRADE_RETCODE_REQUOTE:
        logger.warning(f"TRADE | Requote occurred (retcode: {retcode}). Fetching updated price and retrying once...")
        # Re-fetch tick
        new_tick = mt5.symbol_info_tick(symbol)
        if new_tick is None:
            logger.error("TRADE | Failed to get updated tick for requote retry.")
            write_trade_to_log(signal, 0, lot_size, f"REJECTED_REQUOTE_FAIL")
            return -1
            
        new_price = getattr(new_tick, price_field)
        request["price"] = new_price
        
        # Retry once
        result = send_order_request(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"TRADE | Success on retry | Order placed. Ticket: {result.order}")
            write_trade_to_log(signal, result.order, lot_size, "SUCCESS_ON_RETRY")
            return result.order
        else:
            ret = result.retcode if result else "None"
            logger.error(f"TRADE | Failed retry on requote. Result code: {ret}")
            write_trade_to_log(signal, 0, lot_size, f"REJECTED_REQUOTE_FAIL_2")
            return -1
            
    elif retcode == mt5.TRADE_RETCODE_REJECT:
        logger.error(f"TRADE | Order rejected by broker (retcode: {retcode}). Full response: {result}")
        write_trade_to_log(signal, 0, lot_size, "REJECTED")
        return -1
        
    else:
        logger.error(f"TRADE | Order failed with error code: {retcode}. Response: {result}")
        write_trade_to_log(signal, 0, lot_size, f"FAILED_ERR_{retcode}")
        return -1
