# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CrudeOilArb is a market-neutral spread arbitrage trading strategy for BZUSDT (Brent Crude) and CLUSDT (WTI Crude) perpetual swaps on Bybit, built with NautilusTrader (v1.221.0+).

**Strategy Type**: High-frequency market-neutral spread arbitrage
**Exchange**: Bybit (Unified Trading Account)

## Core Architecture

### Strategy Flow

1. **Market Data**: Real-time quote ticks from Bybit WebSocket for `BZUSDT-LINEAR` and `CLUSDT-LINEAR`
2. **Spread Calculation**: `spread = (BZ_mid - CL_mid) / CL_mid`
3. **Grid Logic**:
   - When `spread > grid_level`: Open hedged position (short BZ, long CL) using **market orders**
   - When `spread < previous_level`: Close position using **limit orders** (maker rebates)
4. **Position Management**: Market-neutral hedge via paired long/short positions

### Key Files

| File | Purpose |
|------|---------|
| `bz_cl_grid_strategy.py` | Main strategy implementation |
| `config_live.py` | Live trading configuration (creates `TradingNodeConfig`) |
| `run_live.py` | Entry point with graceful shutdown and log cleanup |
| `check_spread.py` | Check current BZ/CL spread via Bybit REST API |
| `close_all_positions.py` | Emergency position closer |
| `diagnose_positions.py` | Debug position state |
| `restart_strategy.sh` | Restart with position sync prompt |

### Strategy Classes

**`BzClGridConfig`** (frozen StrategyConfig):
- `grid_levels`: List of spread thresholds (e.g., 0.001 = 0.10%)
- `position_weights`: Dict mapping grid level → size multiplier
- `base_notional_per_level`: Base USDT per grid (default: 88.5)
- `max_total_notional`: Total exposure cap (default: 3000)
- `initial_notional_override`: Manual position sync on restart

**`GridPositionState`**: Tracks BZ/CL position IDs per grid level

**`PairedOrderTracker` / `PairedCloseTracker`**: Detect partial fills on paired orders

### Critical Implementation Details

**Position Reconciliation on Restart**:
- NautilusTrader auto-reconciles positions (30-min lookback)
- **CRITICAL**: Bybit doesn't report external positions to NautilusTrader
- Set `initial_notional_override` in `config_live.py` to actual Bybit exposure
- 10-second startup delay allows reconciliation before grid processing

**Order Execution**:
- **Opens**: Market orders (fast execution, guaranteed fill)
- **Closes**: Limit orders with maker offset (0.01%) for rebates
- Order timeout: 60 seconds (cancel and resubmit)

**Grid Level Allocation**:
- Positions open at **HIGH spreads** (far from 0)
- Positions close when spread **reverts BELOW previous level**
- Sync marks grids from **highest to lowest**

## Commands

### Running the Strategy

```bash
# Setup
cp .env.example .env
# Edit .env with BYBIT_API_KEY and BYBIT_API_SECRET
pip install -r requirements.txt

# Run
python run_live.py
```

### Docker

```bash
docker build -t crudeoilarb .
docker run -d --name crudeoilarb --env-file .env -v $(pwd)/logs:/app/logs crudeoilarb
docker logs -f crudeoilarb
```

### Utility Scripts

```bash
python check_spread.py              # Check current BZ/CL spread
python close_all_positions.py       # Emergency close all
python diagnose_positions.py        # Debug position state
python cleanup_logs.py              # Remove old log files
./restart_strategy.sh               # Restart with position sync prompt
```

### Log Analysis

```bash
# Monitor live (JSON logs)
tail -f logs/bz_cl_grid*.json | jq -r '[.timestamp, .level, .component, .message] | @tsv'

# Find errors
cat logs/*.json | jq 'select(.level == "ERROR")'

# Track grid activity
grep "Opening grid\|Closing grid" logs/*.json | tail -20

# Check position sync
grep "STARTUP SYNC\|Marked grid level" logs/*.json | tail -20
```

## Configuration

Edit `config_live.py` to modify:

```python
strategy_config = BzClGridConfig(
    # Instruments
    bz_instrument_id="BZUSDT-LINEAR.BYBIT",
    cl_instrument_id="CLUSDT-LINEAR.BYBIT",
    
    # Grid levels (spread thresholds)
    grid_levels=[0.0010, 0.0020, ...],  # 0.10%, 0.20%, ...
    
    # Position sizing
    base_notional_per_level=88.5,       # USDT per side
    position_weights={0.0010: 0.5, ...}, # Level → multiplier
    max_total_notional=3000.0,           # Total exposure cap
    
    # IMPORTANT: Set when restarting with positions!
    initial_notional_override=0.0,       # Actual Bybit exposure
)
```

### Testnet Mode

```bash
# In .env
BYBIT_TESTNET=true
```

## Constraints & Gotchas

### NautilusTrader Compatibility
- `product_types=[BybitProductType.LINEAR]` causes TypeError (commented out in config)
- Locked to `nautilus_trader>=1.200.0`

### Bybit Configuration
- Instrument IDs: `BZUSDT-LINEAR.BYBIT` (NOT `-PERP` suffix)
- Leverage: Set manually on Bybit exchange (recommended 10x)
- Position Mode: Must use **One-Way Mode** (NOT Hedge Mode)

### Position Sync on Restart
1. Check actual exposure on Bybit position page
2. Calculate total notional: `sum(position_values)`
3. Set `initial_notional_override` in `config_live.py`
4. Restart strategy
5. Verify: `grep "STARTUP SYNC" logs/*.json`

After positions close, reset to `initial_notional_override=0.0`.

### Log File Management
- NautilusTrader creates timestamped log files on each restart
- `cleanup_old_logs()` runs on startup (keeps 10 files, max 50MB)

## Risk Controls

- `max_total_notional`: Hard cap on total exposure (3000 USDT)
- `extreme_spread_stop`: Emergency close-all at 3.5% spread
- `position_weights`: Scale positions with spread levels (0.5x to 2.5x)
- Paired order tracking: Detects imbalanced fills
- Level retry cooldown: 30-second block after pair failure
