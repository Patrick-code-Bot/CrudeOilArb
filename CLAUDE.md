# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GoldArb is a market-neutral spread arbitrage trading strategy for PAXG/USDT and XAUT/USDT perpetual swaps on Bybit. It uses NautilusTrader (v1.221.0+) to exploit price spreads between two gold-backed tokens through multi-level grid trading with maker orders.

**Strategy Type**: High-frequency market-neutral spread arbitrage
**Framework**: NautilusTrader
**Exchange**: Bybit (Unified Trading Account)
**Language**: Python 3.10+

## Core Architecture

### Strategy Flow

1. **Market Data**: Real-time quote ticks from Bybit WebSocket for `PAXGUSDT-LINEAR` and `XAUTUSDT-LINEAR`
2. **Spread Calculation**: `spread = (PAXG_mid - XAUT_mid) / XAUT_mid`
3. **Grid Logic**:
   - When `|spread| > grid_level`: Open hedged position (short expensive, long cheap) using **market orders**
   - When `|spread| < previous_level`: Close position using **limit orders**
4. **Position Management**: Market-neutral hedge via paired long/short positions

### Key Files

**paxg_xaut_grid_strategy.py** (Main strategy implementation)
- `PaxgXautGridConfig`: Configuration dataclass (frozen) with grid levels, risk limits, position weights
- `GridPositionState`: Tracks PAXG/XAUT position IDs per grid level
- `PairedOrderTracker` / `PairedCloseTracker`: Detect partial fills on paired orders
- `PaxgXautGridStrategy`: Main strategy class
  - Key methods: `on_quote_tick()`, `on_order_filled()`, `_process_grids()`, `_open_grid()`, `_close_grid()`
  - Position sync: `_sync_existing_positions()` (called after startup delay)

**config_live.py** (Live trading configuration)
- Creates `TradingNodeConfig` with Bybit data/exec clients
- Default: 15 grid levels (0.10% to 8.00%) with position weights (0.4x to 3.5x)
- Risk: `base_notional_per_level=88.5 USDT`, `max_total_notional=3500 USDT`
- Logging: JSON format at INFO level, 10MB rotation with 3 backups
- Execution engine: Reconciliation enabled (24h lookback), position snapshots every 5 minutes

**run_live.py** (Entry point)
- Async event loop with graceful shutdown
- Log cleanup on startup (`cleanup_old_logs()`: max 50MB, 10 files)
- Environment validation for API keys
- Signal handlers (SIGINT, SIGTERM)

### Critical Implementation Details

**Position Reconciliation**
- NautilusTrader auto-reconciles positions on startup (24h lookback via `LiveExecEngineConfig`)
- **CRITICAL**: `initial_notional_override` in `config_live.py` MUST be set manually when restarting with existing Bybit positions
  - Bybit doesn't report external positions to NautilusTrader's reconciliation
  - Set to `0.0` when starting fresh
  - Set to actual total exposure (e.g., `1770.0`) when positions exist
- 10-second startup delay (`startup_delay_sec=10.0`) allows reconciliation to complete before grid processing
- Periodic reconciliation every 60 seconds in `_sync_existing_positions()`

**Order Execution Strategy**
- **Opening positions**: Market orders for fast execution and guaranteed fills
- **Closing positions**: Limit orders with maker offset (default 0.01%) to capture maker rebates
- All orders use `TimeInForce.GTC` (Good Till Cancel)
- Order timeout: 5 seconds (cancel and resubmit if unfilled)

**Grid State Management**
- `grid_state: Dict[float, GridPositionState]` - Maps grid levels to position IDs
- `working_orders: Dict[OrderId, tuple[float, str]]` - Tracks pending orders
- `paired_orders: Dict[int, PairedOrderTracker]` - Detects imbalanced fills on opens
- `paired_close_orders: Dict[int, PairedCloseTracker]` - Detects imbalanced fills on closes
- `total_notional` - Actual filled positions
- `pending_notional` - Submitted but unfilled orders

**Risk Controls**
- `max_total_notional`: Hard cap on total exposure (prevents over-leveraging)
- `extreme_spread_stop=0.010` (1.0%): Emergency close-all trigger
- Position weights: Scale with spread levels (0.4x at 0.10%, 3.5x at 8.00%)
- Notional calculation: `notional = base_notional_per_level * position_weights.get(level, 1.0)`

## Common Commands

### Running the Strategy

**Local Development**
```bash
# Setup
cd GoldArb
cp .env.example .env
# Edit .env with your Bybit API credentials
pip install -r requirements.txt

# Run
python run_live.py
```

**Docker Deployment**
```bash
cd GoldArb

# Build
docker build -t goldarb .

# Run standalone
docker run -d \
  --name goldarb \
  --env-file .env \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  goldarb

# Monitor
docker logs -f goldarb
```

**Docker Compose** (from parent directory)
```bash
cd ../trading-deployment
docker-compose up -d goldarb
docker-compose logs -f goldarb
```

### Configuration

**Edit strategy parameters**
```bash
# Modify grid levels, position sizes, risk limits
nano config_live.py  # Update PaxgXautGridConfig

# Key parameters:
# - grid_levels: List of spread thresholds
# - base_notional_per_level: Base USDT per grid (default: 88.5)
# - position_weights: Multipliers per grid level
# - max_total_notional: Total exposure cap (default: 3500)
# - initial_notional_override: Manual position sync (SET THIS when restarting!)
```

**Switch to testnet**
```bash
# In .env file
BYBIT_TESTNET=true
```

### Utility Scripts

**Check current spread**
```bash
python check_spread.py
```

**Restart strategy** (with position sync)
```bash
./restart_strategy.sh
# Prompts for confirmation, shows expected behavior
```

**Emergency close all positions**
```bash
python close_all_positions.py
```

**Cleanup old logs**
```bash
python cleanup_logs.py  # Keeps 10 newest files
```

**Diagnose positions**
```bash
python diagnose_positions.py
```

**Verify fixes**
```bash
python verify_fix.py
```

### Log Analysis

**Monitor live logs** (pretty-printed JSON)
```bash
tail -f logs/paxg_xaut_grid*.json | jq -r '[.timestamp, .level, .component, .message] | @tsv'
```

**Find errors**
```bash
cat logs/*.json | jq 'select(.level == "ERROR")'
```

**Track grid activity**
```bash
grep "Opening grid\|Closing grid" logs/*.json | tail -20
```

**Check position sync**
```bash
grep "STARTUP SYNC\|Marked grid level\|total_notional" logs/*.json | tail -20
```

**Monitor spread**
```bash
grep "spread=" logs/*.json | tail -10
```

## Important Constraints & Gotchas

### NautilusTrader Compatibility
- **Version**: Locked to `nautilus_trader>=1.200.0`
- **Breaking change**: `BybitProductType` import removed (commented out in `config_live.py:140`) due to TypeError in current version
- Upgrades may break adapter configuration

### Bybit Configuration
- **Instrument IDs**: MUST use format `PAXGUSDT-LINEAR.BYBIT` (NOT `-PERP` suffix)
- **Leverage**: Set manually on Bybit exchange (recommended 10x)
  - Strategy controls exposure via `max_total_notional`, NOT leverage multiplier
- **Position Mode**: Must use **One-Way Mode** (NOT Hedge Mode)
- **Account Type**: Unified Trading Account

### Position Sync on Restart
**CRITICAL**: When restarting with existing Bybit positions:
1. Check actual exposure on Bybit position page
2. Calculate total notional: `sum(position_value_1 + position_value_2 + ...)`
3. Set `initial_notional_override` in `config_live.py` to this value
4. Restart strategy
5. Verify sync in logs: `grep "STARTUP SYNC" logs/*.json`

**Example**:
```python
# In config_live.py
initial_notional_override=1770.0  # Set to actual Bybit exposure
```

After positions close:
```python
initial_notional_override=0.0  # Reset to 0
```

### Log File Management
- NautilusTrader creates new timestamped log files on each restart
- Without cleanup, logs grow unbounded
- `cleanup_old_logs()` in `run_live.py` runs on startup
- Adjust limits: `cleanup_old_logs(max_total_size_mb=50, max_files=10)`

### Order Types
- **Opens**: Market orders (fast execution, guaranteed fill)
- **Closes**: Limit orders (better prices, maker rebates)
- Do NOT change to all-limit or all-market without understanding trade-offs

### Grid Level Allocation
- Positions open at **HIGH spreads** (far from 0)
- Positions close when spread **reverts BELOW previous level**
- When restarting, mark grids from **highest to lowest** (see `_sync_existing_positions()`)

## Recent Changes & Known Issues

### Recent Fixes (2026-01-07)
- Fixed position sync logic for HIGH spread levels (0.30%-8.00%)
- Positions now properly detected and marked at correct grid levels
- Closing logic fixed for positions that should close on spread reversion

### Known Issues
- `product_types=[BybitProductType.LINEAR]` causes TypeError (workaround: commented out)
- Bybit external positions not visible to NautilusTrader (requires manual override)
- Log files accumulate without cleanup (mitigated by `cleanup_old_logs()`)

## Architecture Decisions

**Q: Why market orders for opens?**
A: Fast execution ensures hedge is established before spread moves. Partial fills on one leg create directional risk.

**Q: Why limit orders for closes?**
A: Spread has already converged (profit locked in), so we can wait for better fill prices + earn maker rebates (-0.01% on Bybit vs +0.06% taker fee).

**Q: Why manual notional override?**
A: Bybit API doesn't expose external positions to NautilusTrader's reconciliation. Manual override prevents double-counting or missed positions.

**Q: Why position weights?**
A: Higher spreads = larger edge = justify larger position sizes for better capital efficiency. Also reduces risk at tight spreads.

**Q: Why paired order trackers?**
A: Detects imbalanced fills (one leg fills, other doesn't). Prevents naked directional exposure.

## Testing

**Testnet Testing** (recommended before live)
1. Get testnet API keys from https://testnet.bybit.com
2. Set `BYBIT_TESTNET=true` in `.env`
3. Fund testnet account with test USDT
4. Run with small `base_notional_per_level` (e.g., 10.0)
5. Monitor for 24+ hours
6. Review logs for errors/warnings

**Local Testing** (without exchange)
- No backtesting framework currently implemented
- Strategy requires live market data (quote ticks)
- Use testnet for risk-free testing

## Monitoring & Alerts

**Key Metrics to Monitor**
- Current spread vs grid levels
- Active grid positions
- Total notional vs max limit
- Order fill rate
- Unrealized PnL
- API latency

**Alert Conditions**
- `|spread| > extreme_spread_stop` (1.0%)
- `total_notional > 90% of max_total_notional`
- Repeated order rejections
- Imbalanced fills (one leg filled, other not)
- Position count mismatch between strategy and Bybit

**Bybit Web Interface**
- Live: https://www.bybit.com/trade/usdt/PAXGUSDT
- Testnet: https://testnet.bybit.com/trade/usdt/PAXGUSDT
- Monitor: positions, orders, balance, funding rates, liquidation price
