from datetime import datetime, timedelta, timezone
import pytz

def floor_to_m5(dt: datetime) -> datetime:
    """
    Floors a datetime to the nearest preceding 5-minute mark.
    Example: 06:03 -> 06:00, 06:07 -> 06:05.
    """
    minutes = (dt.minute // 5) * 5
    return dt.replace(minute=minutes, second=0, microsecond=0)

def get_setup_candle_times(event_time: datetime) -> tuple[datetime, datetime]:
    """
    Calculates the setup candle open and close times for an event.
    The setup candle closes exactly 5 minutes before the event.
    Example: For event at 06:00, Setup Candle Open = 05:50, Setup Candle Close = 05:55.
    """
    floored_event_time = floor_to_m5(event_time)
    setup_open = floored_event_time - timedelta(minutes=10)
    setup_close = floored_event_time - timedelta(minutes=5)
    return setup_open, setup_close

def parse_iso_datetime(date_str: str) -> datetime:
    """
    Parses an ISO 8601 datetime string from the API and converts it to UTC.
    Supports Z, timezone offsets, and space separators.
    """
    try:
        # standard ISO format parsing (Python 3.11+ supports 'Z' and offset parsing out-of-the-box)
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except ValueError:
        # Fallback for older formats or alternative separators (e.g. replacing space with T)
        cleaned_str = date_str.strip()
        if " " in cleaned_str and "T" not in cleaned_str:
            cleaned_str = cleaned_str.replace(" ", "T")
        try:
            dt = datetime.fromisoformat(cleaned_str)
            return dt.astimezone(timezone.utc)
        except ValueError as e:
            # Let's try dateutil as final fallback
            try:
                import dateutil.parser as dp
                return dp.parse(date_str).astimezone(timezone.utc)
            except ImportError:
                raise ValueError(f"Could not parse datetime '{date_str}': {e}")

def get_local_hour(utc_dt: datetime, tz_name: str) -> int:
    """
    Converts a UTC datetime to a specified local timezone and returns the hour.
    Used for the AUD special rule.
    """
    try:
        local_tz = pytz.timezone(tz_name)
    except Exception:
        local_tz = pytz.timezone("Australia/Sydney")
        
    local_dt = utc_dt.astimezone(local_tz)
    return local_dt.hour
