import os
import json
import logging
import requests
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from typing import List, Optional

from utils.time_utils import parse_iso_datetime

logger = logging.getLogger("cpi_trading_system")

@dataclass
class CPIEvent:
    id: str                # Unique event identifier: e.g. "AUD_CPI_202606071800"
    currency: str          # e.g. "AUD"
    event_name: str        # e.g. "CPI q/q"
    event_time: datetime   # UTC datetime
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None

class CalendarCache:
    """
    Manages loading and saving the local event calendar cache.
    """
    def __init__(self, cache_file: str = "data/calendar_cache.json"):
        self.cache_file = cache_file
        
    def exists(self) -> bool:
        return os.path.exists(self.cache_file)
        
    def get_last_modified_time(self) -> datetime:
        """
        Returns the UTC last modified time of the cache file.
        """
        if not self.exists():
            return datetime.min.replace(tzinfo=timezone.utc)
        mtime = os.path.getmtime(self.cache_file)
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
        
    def is_expired(self, expiry_hours: int = 12) -> bool:
        """
        Returns True if the cache is older than the expiry duration.
        """
        if not self.exists():
            return True
        age = datetime.now(timezone.utc) - self.get_last_modified_time()
        return age > timedelta(hours=expiry_hours)
        
    def save(self, events: List[CPIEvent]):
        """
        Saves the list of CPIEvents as JSON to the cache file.
        """
        dir_name = os.path.dirname(self.cache_file)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
            
        data = []
        for e in events:
            e_dict = asdict(e)
            # Serialize datetime to ISO string
            e_dict["event_time"] = e.event_time.isoformat()
            data.append(e_dict)
            
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        logger.info(f"CALENDAR | Cached {len(events)} events to {self.cache_file}")

    def load(self) -> List[CPIEvent]:
        """
        Loads the list of CPIEvents from the JSON cache file.
        """
        if not self.exists():
            return []
            
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            events = []
            for item in data:
                # Deserialize ISO string back to datetime
                item["event_time"] = datetime.fromisoformat(item["event_time"])
                events.append(CPIEvent(**item))
            return events
        except Exception as e:
            logger.error(f"CALENDAR | Failed to load cache: {str(e)}")
            return []

class EventMonitor:
    """
    Handles calendar feed downloads, caching, and currency filtering.
    """
    def __init__(self, config: dict):
        self.config = config
        self.allowed_currencies = set(config.get("allowed_currencies", []))
        self.ignored_currencies = set(config.get("ignored_currencies", ["USD"]))
        
        calendar_cfg = config.get("calendar", {})
        self.cache_file = "data/calendar_cache.json"
        self.cache = CalendarCache(self.cache_file)
        self.cache_expiry_hours = calendar_cfg.get("cache_expiry_hours", 12)
        
    def fetch_upcoming_cpi_events(self, force_refresh: bool = False) -> List[CPIEvent]:
        """
        Gets list of upcoming CPI events. Uses cache if valid, otherwise fetches from API.
        """
        # If cache exists and is fresh, use it (unless forced refresh)
        if not force_refresh and not self.cache.is_expired(self.cache_expiry_hours):
            logger.info("CALENDAR | Loading calendar from local cache...")
            events = self.cache.load()
            # Filter for events occurring in the future (next 24h)
            return self._filter_active_events(events)
            
        logger.info("CALENDAR | Cache expired or refresh requested. Fetching latest calendar feed...")
        try:
            raw_events = self._download_faireconomy_feed()
            cpi_events = self._parse_and_filter_events(raw_events)
            
            # Save all CPI events to cache
            self.cache.save(cpi_events)
            
            return self._filter_active_events(cpi_events)
        except Exception as e:
            logger.error(f"CALENDAR | API fetch failed: {str(e)}. Falling back to local cache.")
            if self.cache.exists():
                cached_events = self.cache.load()
                return self._filter_active_events(cached_events)
            else:
                logger.error("CALENDAR | Cache is missing. No calendar data available.")
                raise e

    def _download_faireconomy_feed(self) -> list:
        """
        Downloads the Faireconomy weekly news feed.
        """
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        
        # Simple retry mechanism with backoff
        retries = 3
        backoff = 2
        for attempt in range(1, retries + 1):
            try:
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                if attempt == retries:
                    raise e
                sleep_time = backoff ** attempt
                logger.warning(f"CALENDAR | Fetch failed on attempt {attempt}: {str(e)}. Retrying in {sleep_time}s...")
                import time
                time.sleep(sleep_time)
        return []

    def _parse_and_filter_events(self, raw_events: list) -> List[CPIEvent]:
        """
        Parses raw event data, filters by CPI keywords and currency rules.
        """
        cpi_events = []
        
        for item in raw_events:
            title = item.get("title", "")
            country = item.get("country", "")
            impact = item.get("impact", "")
            date_str = item.get("date", "")
            
            # Check for high impact and CPI in title
            if "CPI" not in title.upper():
                continue
            if impact.upper() != "HIGH":
                continue
                
            currency = country.upper()
            
            # Exclude USD (silently ignored per prompt requirements)
            if currency in self.ignored_currencies:
                continue
                
            # Warn if CPI currency is not in allowed list
            if currency not in self.allowed_currencies:
                logger.warning(f"CALENDAR | SKIP | CPI news event found for currency {currency} ({title}) but it is not in allowed list.")
                continue
                
            try:
                event_time = parse_iso_datetime(date_str)
            except Exception as e:
                logger.error(f"CALENDAR | Error parsing event time for {title}: {str(e)}")
                continue
                
            # Create a unique ID for the event
            # Event format: CPI_{CURRENCY}_{YYYYMMDD_HHMM}
            event_id = f"CPI_{currency}_{event_time.strftime('%Y%m%d_%H%M')}"
            
            event = CPIEvent(
                id=event_id,
                currency=currency,
                event_name=title,
                event_time=event_time,
                forecast=item.get("forecast"),
                previous=item.get("previous"),
                actual=item.get("actual")
            )
            cpi_events.append(event)
            
        return cpi_events

    def _filter_active_events(self, events: List[CPIEvent]) -> List[CPIEvent]:
        """
        Returns events scheduled in the next 24 hours.
        """
        now = datetime.now(timezone.utc)
        one_day_later = now + timedelta(hours=24)
        
        # Include events occurring in the window [now - 15m, now + 24h] 
        # (allowing a buffer for events that might have just happened/about to happen)
        buffer_start = now - timedelta(minutes=15)
        
        active_events = []
        for e in events:
            if buffer_start <= e.event_time <= one_day_later:
                active_events.append(e)
                
        return sorted(active_events, key=lambda x: x.event_time)
