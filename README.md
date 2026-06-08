# CPI News Trading System

A production-grade, fully automated economic news trading bot for MetaTrader 5 (MT5). The system monitors an economic calendar feed, applies an EMA-based technical strategy to 5-minute candles preceding high-impact CPI releases, evaluates custom timezone-specific currency rules (such as the AUD reversal rule), and executes trades on MT5 with advanced risk management and error resilience.

---

## 📌 Features
- **Daily Calendar Sync**: Fetches economic calendar data once daily from the Faireconomy feed (Forex Factory) and caches it locally in `data/calendar_cache.json` to prevent IP rate-limiting blocks.
- **Precision Job Scheduling**: Employs `APScheduler` to trigger trading jobs at the exact close of the Setup Candle (5 minutes before the news release + a 2-second buffer).
- **EMA Technical Filter**: Uses Pandas EWM to calculate EMA(50) over 200 candles, generating BUY/SELL signals based on setup candle direction relative to the EMA.
- **AUD Timezone Rule**: Automatically reverses signals for AUD CPI events released before `08:00` local Sydney time, recalculating Stop Loss (SL) and Take Profit (TP) bounds.
- **Dynamic Broker Adapters**: Automatically selects order filling modes (`FOK` or `IOC`) based on the symbol's properties to eliminate broker execution rejections (`TRADE_RETCODE_INVALID_FILL`).
- **Resilience Engine**: Integrates automatic MT5 reconnection, margin requirement pre-checks, duplicate trade prevention, stale price/weekend checks, and order requote retry logic.

---

## 🏗️ Project Directory Structure

```
cpi_trading_system/
├── main.py                    # System startup and keep-alive loop
├── config.yaml                # Configuration parameters (EMA, Lot size, Magic, etc.)
├── .env                       # MT5 credentials and API keys (gitignored)
├── requirements.txt           # Pinned python package dependencies
├── README.md                  # Project manual
│
├── core/
│   ├── __init__.py
│   ├── scheduler.py           # APScheduler task management
│   ├── event_monitor.py       # Calendar fetcher and filter
│   ├── signal_engine.py       # EMA strategy and AUD reversal rules
│   ├── trade_executor.py      # MT5 order sender and pre-trade validations
│   ├── mt5_connector.py       # MT5 terminal connector & reconnect engine
│   └── symbol_mapper.py       # Currency symbol resolvers with suffix support
│
├── utils/
│   ├── __init__.py
│   ├── logger.py              # Rotating file logger
│   ├── time_utils.py          # Timezone helpers & M5 flooring
│   └── validators.py          # Config structure and data type checkers
│
├── data/
│   ├── calendar_cache.json    # Weekly cached events
│   └── trade_log.csv          # CSV log of trade executions
│
└── tests/                     # Unit test suites (mocking MT5 APIs)
    ├── test_signal_engine.py
    ├── test_event_monitor.py
    └── test_trade_executor.py
```

---

## 🚀 Setup & Installation

### Prerequisites
1. **OS**: Windows (Required for MetaTrader 5 Python integration).
2. **Python**: Python 3.11+ installed.
3. **MT5 Terminal**: MetaTrader 5 desktop client installed and logged in to a demo/live broker account.
4. **Algo Trading Enabled**: In the MT5 client, ensure the **"Algo Trading"** button on the toolbar is enabled (green/active) and script execution permissions are granted in `Tools -> Options -> Expert Advisors`.

### Installation Steps
1. **Clone/Copy Project**: Place the code files into your workspace directory.
2. **Create Virtual Environment**:
   ```powershell
   python -m venv .venv
   ```
3. **Activate Environment and Install Dependencies**:
   ```powershell
   # Windows PowerShell
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
4. **Configure Credentials**:
   - Copy `.env.example` to `.env`
   - Edit `.env` to specify your MT5 login, password, and broker server:
     ```env
     MT5_LOGIN=12345678
     MT5_PASSWORD=your_secure_password
     MT5_SERVER=BrokerName-Demo
     ```

---

## ⚙️ Configuration Guide (`config.yaml`)

Configure settings in `config.yaml`:
- **`mt5`**: Connection settings (overridden by `.env` if provided).
- **`trading`**:
  - `timeframe`: Must remain `"M5"`.
  - `ema_period`: Period for EMA filter (default `50`).
  - `ema_lookback_candles`: Historical candles fetched to calculate EMA (default `200`).
  - `risk_reward_ratio`: Multiplier for SL distance to calculate TP (default `3`).
  - `lot_size`: Order volume (default `0.01`).
  - `magic_number`: Unique identifier for the bot's orders (e.g. `20240101`).
  - `symbol_prefix` / `symbol_suffix`: Prefix/suffix used by your broker (e.g., `symbol_suffix: ".ecn"` or `symbol_suffix: "m"`).
- **`calendar`**: Specify allowed currencies (e.g., EUR, AUD, GBP) and ignore USD.
- **`aud_special_rule`**: Toggle and specify timezone (default `"Australia/Sydney"`) for AUD reversal rules.

---

## 📈 Trading Strategy & Workflow

### 1. Daily Ingestion
Every day at `00:05 UTC` (and on bot startup), the scheduler runs a calendar sync:
- Downloads the calendar JSON from `faireconomy.media`.
- Filters high-impact economic releases containing `"CPI"` in the description.
- Filters events for allowed currencies and discards `USD` events.
- Stores events in `data/calendar_cache.json`.

### 2. Setup Candle Scheduling
For each identified CPI event (e.g. AUD CPI at `06:00 UTC`), the bot schedules a date job to trigger at **Setup Candle Close**:
- Setup Candle Open = `event_time - 10 minutes` (e.g. `05:50 UTC`)
- Setup Candle Close = `event_time - 5 minutes` (e.g. `05:55 UTC`)
- Trigger Time = `05:55:02 UTC` (includes a 2-second buffer to guarantee candle completion).

### 3. Signal Evaluation
Upon trigger, the bot fetches the last 200 candles and calculates EMA(50) at Setup Candle Close:
- **BUY Signal**: Setup candle is Bullish (`close > open`) AND setup candle close is below EMA50 (`close < ema50`).
  - Entry = `close`
  - SL = `low`
  - TP = `entry + 3 * (entry - SL)`
- **SELL Signal**: Setup candle is Bearish (`close < open`) AND setup candle close is above EMA50 (`close > ema50`).
  - Entry = `close`
  - SL = `high`
  - TP = `entry - 3 * (SL - entry)`
- **No Signal**: If candle closes on the wrong side of the EMA, is a Doji (`close == open`), or risk is <= 0, the trade is skipped.

### 4. AUD Special Reversal Rule
If the currency is `AUD` and the CPI release local time (Sydney) is **before 08:00 AM**:
- The strategy direction is reversed: BUY becomes SELL, and SELL becomes BUY.
- SL and TP parameters are **fully recalculated** based on the reversed direction (e.g. if reversed to BUY, SL becomes setup candle low and TP is calculated above entry).

### 5. Execution & Checks
Before order submission, the bot checks:
- MT5 terminal connection.
- Market session state (not weekends).
- Symbol availability on broker.
- No existing position with the bot's magic number.
- Account has sufficient free margin.
- SL/TP inputs are valid (not negative or inverted).
If checks pass, a market order is placed using the correct filling mode, logged to `logs/cpi_system.log`, and recorded in `data/trade_log.csv`.

---

## 🧪 Running Unit Tests

To run the full suite of unit tests verifying all signals, caching, and execution rules (using mocks, so no live MT5 client is needed):
```bash
python -m pytest -v
```

---

## ⚠️ Important Live Trading Warnings
1. **Spread Widening**: Around major CPI releases, brokers typically widen spreads significantly. Make sure you use a broker with tight spreads, and test on a demo account.
2. **Slippage**: Even though we enter 5 minutes before the news release, extreme news volatility can cause slippage on execution or when target prices (SL/TP) are hit.
3. **Execution Fee/Commission**: Account for broker commission fees in your risk-reward calculations.
4. **Terminal Keep-Alive**: Ensure your computer does not go to sleep, and remains connected to the internet during market hours. For live environments, running on a Windows VPS (Virtual Private Server) is recommended.
