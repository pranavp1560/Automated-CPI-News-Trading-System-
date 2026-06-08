import os

def validate_config(config: dict) -> bool:
    """
    Validates the parsed configuration dictionary to ensure all required fields
    and correct data types are present.
    """
    try:
        # 1. MT5 Validation
        mt5_cfg = config.get("mt5", {})
        if not mt5_cfg:
            raise ValueError("Missing 'mt5' section in config")
        
        # Check login (overridden by env if present, but must be integer if in yaml)
        login = os.getenv("MT5_LOGIN", mt5_cfg.get("login"))
        if login is None:
            raise ValueError("Missing MT5 login ID (must set in config.yaml or .env)")
        try:
            int(login)
        except ValueError:
            raise ValueError(f"MT5 login must be a valid integer, got: {login}")

        server = os.getenv("MT5_SERVER", mt5_cfg.get("server"))
        if not server:
            raise ValueError("Missing MT5 server name")

        # 2. Trading Parameters Validation
        trading_cfg = config.get("trading", {})
        if not trading_cfg:
            raise ValueError("Missing 'trading' section in config")

        timeframe = trading_cfg.get("timeframe", "M5")
        if timeframe != "M5":
            raise ValueError("Strategy is hardcoded to M5 timeframe; timeframe must be 'M5'")

        ema_period = trading_cfg.get("ema_period")
        if not isinstance(ema_period, int) or ema_period <= 0:
            raise ValueError(f"Invalid ema_period: {ema_period}. Must be positive integer.")

        ema_lookback = trading_cfg.get("ema_lookback_candles")
        if not isinstance(ema_lookback, int) or ema_lookback < ema_period:
            raise ValueError(f"ema_lookback_candles ({ema_lookback}) must be greater than ema_period ({ema_period})")

        rr = trading_cfg.get("risk_reward_ratio")
        if not isinstance(rr, (int, float)) or rr <= 0:
            raise ValueError(f"Invalid risk_reward_ratio: {rr}")

        lot_size = trading_cfg.get("lot_size")
        if not isinstance(lot_size, (int, float)) or lot_size <= 0:
            raise ValueError(f"Invalid lot_size: {lot_size}")

        magic = trading_cfg.get("magic_number")
        if not isinstance(magic, int) or magic <= 0:
            raise ValueError(f"Invalid magic_number: {magic}")

        # 3. Calendar Settings
        calendar_cfg = config.get("calendar", {})
        if not calendar_cfg:
            raise ValueError("Missing 'calendar' section in config")

        provider = calendar_cfg.get("api_provider")
        if provider not in ["forex_factory", "trading_economics", "fmp"]:
            raise ValueError(f"Unsupported calendar api_provider: {provider}")

        # 4. Currency Filters
        allowed = config.get("allowed_currencies", [])
        if not allowed or not isinstance(allowed, list):
            raise ValueError("allowed_currencies must be a non-empty list")

        return True
    except Exception as e:
        # Return False or let it raise
        raise ValueError(f"Configuration validation failed: {str(e)}")
