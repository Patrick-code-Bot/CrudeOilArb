# 🛢️ CrudeOilArb - BZ/CL Grid Arbitrage Strategy

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![NautilusTrader](https://img.shields.io/badge/NautilusTrader-1.221.0-green.svg)](https://nautilustrader.io/)
[![License](https://img.shields.io/badge/License-LGPL--3.0-orange.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](Dockerfile)

A high-frequency market-neutral spread arbitrage trading strategy for BZ/USDT (Brent Crude) and CL/USDT (WTI Crude) perpetual swaps on Bybit, built with the NautilusTrader algorithmic trading framework.

---

## 📖 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Strategy Logic](#strategy-logic)
- [Architecture](#architecture)
- [Installation](#installation)
  - [Docker Deployment (Recommended)](#docker-deployment-recommended)
  - [Local Development Setup](#local-development-setup)
- [Configuration](#configuration)
- [Usage](#usage)
- [Monitoring](#monitoring)
- [Risk Management](#risk-management)
- [Performance](#performance)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## 🎯 Overview

CrudeOilArb is an automated trading strategy that exploits price spreads between two highly correlated crude oil futures:

- **BZ (Brent Crude)**: Brent crude oil futures perpetual swap
- **CL (WTI Crude)**: West Texas Intermediate crude oil futures perpetual swap

By trading perpetual swap contracts on Bybit, the strategy captures arbitrage opportunities while maintaining a market-neutral hedged position.

### Key Characteristics

- **Market Neutral**: Long one asset, short the other - no directional exposure
- **Grid-Based**: Multiple price levels with predefined entry/exit points
- **High Frequency**: Real-time quote monitoring and rapid order execution
- **Risk Controlled**: Built-in position limits, notional caps, and extreme spread stops
- **Maker Orders**: Uses limit orders to earn maker rebates and minimize fees

---

## ✨ Features

- ✅ **Real-time market data streaming** from Bybit WebSocket
- ✅ **Automated quote tick subscription** for accurate spread calculation
- ✅ **Multi-level grid trading** with configurable spread thresholds
- ✅ **Position reconciliation** on startup and periodic snapshots
- ✅ **Comprehensive logging** (JSON format, DEBUG/INFO levels)
- ✅ **Docker containerization** for easy deployment
- ✅ **Testnet support** for risk-free testing
- ✅ **Emergency stop mechanisms** for extreme market conditions
- ✅ **Graceful shutdown** with order cancellation and position management

---

## 📊 Strategy Logic

### Core Mechanism

1. **Spread Calculation**
   ```
   spread = (BZ_price - CL_price) / CL_price
   ```

2. **Grid Entry Logic**
   - When `spread > grid_level`: Open hedged position
     - SELL BZ (expensive asset)
     - BUY CL (cheap asset)
   - Each grid level represents a specific spread threshold (e.g., 0.10%, 0.20%, etc.)

3. **Grid Exit Logic**
   - When spread reverts below the grid level: Close position
   - Profit is locked in from spread convergence

4. **Order Execution**
   - Opening orders are **market orders** (fast execution)
   - Closing orders are **limit orders** (maker rebates)
   - 60-second timeout for unfilled orders (cancel and resubmit)

### Example Trade Flow

```
1. Initial State:
   BZ = $72.00
   CL = $68.80
   Spread = 4.65% (above grid levels)

2. Entry:
   → SELL 1.5 BZ @ $72.00
   → BUY  1.5 CL @ $68.80
   → Position opened at grid level

3. Spread Converges:
   BZ = $71.00
   CL = $68.50
   Spread = 3.65% (below previous level)

4. Exit:
   → BUY  1.5 BZ @ $71.00 (close short)
   → SELL 1.5 CL @ $68.50 (close long)
   → Profit realized from spread convergence
```

---

## 🏗️ Architecture

### Technology Stack

- **Framework**: [NautilusTrader](https://nautilustrader.io/) v1.221.0
- **Exchange**: Bybit (Unified Trading Account)
- **Language**: Python 3.12
- **Containerization**: Docker with multi-stage builds
- **Logging**: Structured JSON logging with rotation

### Project Structure

```
CrudeOilArb/
├── bz_cl_grid_strategy.py        # Core strategy implementation
├── config_live.py                 # Live trading configuration
├── run_live.py                    # Main entry point
├── check_spread.py                # Utility to check current spread
├── close_all_positions.py         # Emergency position closer
├── requirements.txt               # Python dependencies
├── Dockerfile                     # Docker image definition
├── .env.example                   # Environment template
├── .dockerignore                  # Docker ignore patterns
├── logs/                          # Trading logs (auto-created)
│   └── bz_cl_grid.json
├── data/                          # Historical data (optional)
└── README.md                      # This file
```

### System Requirements

- **CPU**: 2+ cores (ARM64 or x86_64)
- **RAM**: 1GB minimum, 2GB recommended
- **Storage**: 5GB for Docker images and logs
- **Network**: Stable internet connection (low latency preferred)
- **OS**: Linux (Ubuntu 20.04+), macOS, or Windows with WSL2

---

## 🚀 Installation

### Docker Deployment (Recommended)

Docker provides the easiest and most reliable deployment method.

#### Prerequisites

- Docker 20.10+
- Docker Compose 1.29+
- Bybit API credentials

#### Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/Patrick-code-Bot/CrudeOilArb.git
   cd CrudeOilArb
   ```

2. **Configure environment variables**
   ```bash
   cp .env.example .env
   nano .env  # Edit with your API credentials
   ```

   Required variables:
   ```bash
   BYBIT_API_KEY=your_api_key_here
   BYBIT_API_SECRET=your_api_secret_here
   BYBIT_TESTNET=false  # Set to 'true' for testnet
   ```

3. **Build and run with Docker Compose** (if using orchestration)
   ```bash
   cd ../trading-deployment
   docker-compose up -d crudeoilarb
   ```

   Or **run standalone**:
   ```bash
   docker build -t crudeoilarb .
   docker run -d \
     --name crudeoilarb \
     --env-file .env \
     -v $(pwd)/logs:/app/logs \
     --restart unless-stopped \
     crudeoilarb
   ```

4. **Monitor logs**
   ```bash
   docker logs -f crudeoilarb
   ```

#### Docker Features

- ✅ **Multi-stage builds** for optimized image size
- ✅ **Non-root user** for security
- ✅ **Health checks** for container monitoring
- ✅ **Automatic restarts** on failure
- ✅ **Volume mounts** for persistent logs

---

### Local Development Setup

For development or testing without Docker:

1. **Install Python 3.10+**
   ```bash
   python3 --version  # Should be 3.10 or higher
   ```

2. **Clone and install dependencies**
   ```bash
   git clone https://github.com/Patrick-code-Bot/CrudeOilArb.git
   cd CrudeOilArb
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   nano .env  # Add your API credentials
   ```

4. **Run the strategy**
   ```bash
   python run_live.py
   ```

---

## ⚙️ Configuration

### Strategy Parameters

Edit `config_live.py` to customize the trading parameters:

```python
strategy_config = BzClGridConfig(
    # Instruments (Bybit LINEAR perpetual swaps)
    bz_instrument_id="BZUSDT-LINEAR.BYBIT",
    cl_instrument_id="CLUSDT-LINEAR.BYBIT",

    # Grid levels (spread as decimal percentage)
    grid_levels=[
        0.0010,  # 0.10% spread
        0.0020,  # 0.20% spread
        0.0030,  # 0.30% spread
        0.0040,  # 0.40% spread
        0.0050,  # 0.50% spread
        0.0060,  # 0.60% spread
        0.0070,  # 0.70% spread
        0.0080,  # 0.80% spread
        0.0090,  # 0.90% spread
        0.0100,  # 1.00% spread
        0.0120,  # 1.20% spread
        0.0150,  # 1.50% spread
        0.0200,  # 2.00% spread
        0.0300,  # 3.00% spread
    ],

    # Risk management
    base_notional_per_level=88.5,    # USDT per grid level
    max_total_notional=3000.0,       # Maximum total exposure (USDT)
    target_leverage=10.0,            # Target leverage (for reference)

    # Trading parameters
    maker_offset_bps=1.0,            # 0.01% price offset for limit orders
    order_timeout_sec=60.0,          # Cancel and resubmit after 60s
    rebalance_threshold_bps=10.0,    # 0.10% imbalance triggers rebalance
    extreme_spread_stop=0.035,       # 3.5% spread triggers emergency stop

    # Features
    enable_high_levels=True,         # Allow upper grid levels
    auto_subscribe=True,             # Auto-subscribe to market data
    order_id_tag="001",              # Unique strategy identifier
)
```

### Risk Profiles

#### 🟢 Conservative (Beginners)
```python
base_notional_per_level=50.0    # $50 per level
max_total_notional=500.0        # $500 max exposure
target_leverage=5.0             # 5x leverage
```
**Recommended Capital**: $1,000+ USDT

#### 🟡 Moderate (Default)
```python
base_notional_per_level=88.5    # $88.5 per level
max_total_notional=3000.0       # $3,000 max exposure
target_leverage=10.0            # 10x leverage
```
**Recommended Capital**: $2,500+ USDT

#### 🔴 Aggressive (Experienced)
```python
base_notional_per_level=500.0   # $500 per level
max_total_notional=10000.0      # $10,000 max exposure
target_leverage=15.0            # 15x leverage
```
**Recommended Capital**: $15,000+ USDT

---

## 🎮 Usage

### Starting the Strategy

#### Live Trading Mode
```bash
# Using Docker
docker start crudeoilarb

# Using Python
python run_live.py
```

Expected output:
```
================================================================================
BZ-CL Grid Strategy - Live Trading
================================================================================
✅ Running in LIVE mode
================================================================================

[1/5] Loading configuration...
[2/5] Building trading node...
[3/5] Registering Bybit adapters...
[4/5] Initializing trading node...
[5/5] Starting trading node...

================================================================================
🚀 Trading node started successfully!
================================================================================

Strategy: BZ-CL Grid Arbitrage
Venue: Bybit (Live)
Instruments:
  - BZUSDT-LINEAR
  - CLUSDT-LINEAR

Press Ctrl+C to stop the trading node...
================================================================================
```

#### Check Current Spread
```bash
python check_spread.py
```

Output:
```
Fetching BZ and CL ticker data from Bybit...

BZ: Bid=72.07, Ask=72.09, Last=72.07
CL: Bid=68.83, Ask=68.84, Last=68.84

BZ Mid: 72.08
CL Mid: 68.84
Spread: 0.047142 (4.7142%)
Abs Spread: 0.047142 (4.7142%)

Grid Level Analysis:
------------------------------------------------------------
Level  0.10% (prev= 0.00%): SHOULD OPEN
Level  0.20% (prev= 0.10%): SHOULD OPEN
...
```

#### Testnet Mode

For risk-free testing:

1. Create testnet API keys at [https://testnet.bybit.com](https://testnet.bybit.com)
2. Update `.env`:
   ```bash
   BYBIT_TESTNET=true
   BYBIT_API_KEY=testnet_api_key
   BYBIT_API_SECRET=testnet_api_secret
   ```
3. Restart the strategy

### Stopping the Strategy

**Graceful Shutdown**:
```bash
# Docker
docker stop crudeoilarb

# Python (Press Ctrl+C in terminal)
```

The strategy will:
- Cancel all pending orders
- Log final positions
- Save state snapshots
- Shut down cleanly

---

## 📈 Monitoring

### Real-Time Logs

#### Docker Logs
```bash
# Follow live logs
docker logs -f crudeoilarb

# Last 100 lines
docker logs --tail 100 crudeoilarb

# Search for errors
docker logs crudeoilarb 2>&1 | grep ERROR
```

#### Log Files

Location: `logs/bz_cl_grid.json`

Format: Structured JSON with fields:
- `timestamp`: ISO 8601 timestamp
- `trader_id`: TRADER-001
- `level`: DEBUG, INFO, WARNING, ERROR
- `component`: Strategy, ExecEngine, DataClient, etc.
- `message`: Log message

### Key Metrics

Monitor these critical metrics:

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| **Spread** | Current BZ-CL price difference | > 3.5% (extreme) |
| **Active Grids** | Number of open grid positions | Approaching max |
| **Total Notional** | Current exposure vs max limit | > 90% of max |
| **Order Fill Rate** | % of orders filled | < 80% |
| **Unrealized PnL** | Open position P&L | Large negative |
| **API Latency** | Bybit API response time | > 500ms |

### Bybit Web Interface

Monitor positions and orders:
- **Live**: [https://www.bybit.com/trade/usdt/BZUSDT](https://www.bybit.com/trade/usdt/BZUSDT)
- **Testnet**: [https://testnet.bybit.com/trade/usdt/BZUSDT](https://testnet.bybit.com/trade/usdt/BZUSDT)

---

## 🛡️ Risk Management

### Built-in Safety Features

1. **Position Limits**
   - `max_total_notional`: Hard cap on total exposure
   - Prevents over-leveraging

2. **Extreme Spread Stop**
   - `extreme_spread_stop = 0.035` (3.5%)
   - Pauses strategy if spread becomes abnormal
   - Prevents trading during market dislocation

3. **Order Timeouts**
   - `order_timeout_sec = 60.0`
   - Cancels stale orders
   - Ensures fresh pricing

4. **Position Reconciliation**
   - On startup: Reconciles local state with exchange
   - Periodic snapshots every 5 minutes
   - Prevents state drift

5. **Paired Order Tracking**
   - Detects imbalanced fills (one leg fills, other doesn't)
   - Automatically closes naked positions
   - Prevents directional exposure

### Operational Best Practices

#### Before Going Live

- [ ] Test on testnet for 24+ hours
- [ ] Verify API keys have correct permissions
- [ ] Set Bybit account leverage (10x recommended)
- [ ] Fund account with sufficient margin
- [ ] Configure position size alerts on Bybit mobile app
- [ ] Document emergency procedures

#### Emergency Procedures

If something goes wrong:

1. **Stop the Strategy**
   ```bash
   docker stop crudeoilarb
   ```

2. **Close All Positions**
   ```bash
   python close_all_positions.py
   ```

3. **Review Logs**
   ```bash
   tail -1000 logs/bz_cl_grid.json | grep ERROR
   ```

---

## 🔧 Troubleshooting

### Common Issues

#### Issue: "Max total notional reached, skip new grid"

**Cause**: Strategy has reached maximum exposure limit.

**Solution**:
- This is **expected behavior** when positions are at max
- Wait for positions to close before new grids open
- Or increase `max_total_notional` in config (higher risk)

---

#### Issue: No orders being placed

**Symptoms**: Strategy running but no OrderSubmitted logs.

**Diagnosis**:
```bash
# Check if quote data is flowing
docker logs crudeoilarb 2>&1 | grep QuoteTick

# Check spread warnings
docker logs crudeoilarb 2>&1 | grep spread
```

**Solutions**:
1. Verify instruments are correct: `BZUSDT-LINEAR.BYBIT`
2. Check quote tick subscription is active
3. Ensure spread is crossing grid levels
4. Review `extreme_spread_stop` threshold

---

#### Issue: Orders rejected by exchange

**Symptoms**: OrderRejected events in logs.

**Common Causes**:
- Insufficient margin/balance
- Incorrect leverage settings
- Position limits exceeded
- Price too far from market (stale)

**Solutions**:
1. Check Bybit account balance
2. Verify leverage is set correctly on Bybit
3. Review `maker_offset_bps` in config
4. Check API rate limits

---

### Getting Help

#### Resources

- **NautilusTrader Docs**: [https://nautilustrader.io/docs](https://nautilustrader.io/docs)
- **NautilusTrader Discord**: [Join Community](https://discord.gg/AUNMNnNDwP)
- **Bybit API Docs**: [https://bybit-exchange.github.io/docs](https://bybit-exchange.github.io/docs)
- **Bybit Support**: [https://www.bybit.com/en-US/help-center](https://www.bybit.com/en-US/help-center)

---

## ⚠️ Disclaimer

**IMPORTANT RISK DISCLOSURE**

- **Trading involves substantial risk of loss**
- **This strategy is for educational purposes only**
- **Past performance does not guarantee future results**
- **The authors are not responsible for any financial losses**
- **This is not financial advice**

### Risks

1. **Market Risk**: Spread may widen unexpectedly
2. **Liquidity Risk**: Positions may be difficult to exit
3. **Technical Risk**: Software bugs, API failures
4. **Execution Risk**: Slippage, partial fills
5. **Funding Risk**: Negative funding rates on perpetual swaps
6. **Correlation Risk**: BZ and CL correlation may break down

### Recommendations

- ✅ **Only trade with capital you can afford to lose**
- ✅ **Understand the strategy before deploying**
- ✅ **Start with small position sizes**
- ✅ **Monitor actively, especially initially**
- ✅ **Have a plan for extreme scenarios**

Use at your own risk.

---

## 📄 License

This project is licensed under the GNU Lesser General Public License v3.0 (LGPL-3.0).

See the [LICENSE](LICENSE) file for full license text.

---

## 📞 Contact

- **GitHub**: [@Patrick-code-Bot](https://github.com/Patrick-code-Bot)
- **Repository**: [CrudeOilArb](https://github.com/Patrick-code-Bot/CrudeOilArb)
- **Issues**: [Report a bug](https://github.com/Patrick-code-Bot/CrudeOilArb/issues)

---

## 🙏 Acknowledgments

Built with:
- [NautilusTrader](https://nautilustrader.io/) - High-performance algorithmic trading platform
- [Bybit](https://www.bybit.com/) - Cryptocurrency derivatives exchange

Special thanks to the NautilusTrader community for their excellent framework and support.

---

<div align="center">

**⭐ If this project helps you, consider giving it a star! ⭐**

Made with ❤️ for the algorithmic trading community

</div>
