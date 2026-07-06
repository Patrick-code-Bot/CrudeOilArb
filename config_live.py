"""
Live trading configuration for BZ-CL Grid Strategy on Bybit
"""

from decimal import Decimal
from pathlib import Path

from nautilus_trader.adapters.bybit.config import BybitDataClientConfig, BybitExecClientConfig
from nautilus_trader.adapters.bybit.factories import BybitLiveDataClientFactory, BybitLiveExecClientFactory
# from nautilus_trader.core.nautilus_pyo3 import BybitProductType  # REMOVED: Not compatible with current version
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.trading.config import ImportableStrategyConfig

from bz_cl_grid_strategy import BzClGridConfig


def create_live_config() -> TradingNodeConfig:
    """
    Create live trading configuration for BZ-CL grid strategy.

    Returns
    -------
    TradingNodeConfig
        The configured trading node for live trading.
    """

    # Strategy configuration
    strategy_config = BzClGridConfig(
        # Instrument IDs (Bybit perpetual swaps)
        bz_instrument_id="BZUSDT-LINEAR.BYBIT",
        cl_instrument_id="CLUSDT-LINEAR.BYBIT",

        # Grid levels (price spread as percentage of CL price)
        # Recalibrated 2026-07-06 from 53 days of hourly Bybit data (May 13 - Jul 6):
        # BZ (Brent) carries a STRUCTURAL premium over CL (WTI) - spread ranged
        # 1.69%-5.71% with mean 3.94%. The old 0.10%-3.00% levels (inherited from
        # PAXG/XAUT) sat entirely below the spread and could never close.
        # Active reversion zone is 4.0%-5.5%; most down-crossings at 4.50%
        # (52 in 53 days). Spacing 0.20-0.25% clears the P90 hourly move (0.24%).
        # See BZ_CL_SPREAD_ANALYSIS.pdf. Re-run the analysis monthly - the monthly
        # mean drifted 3.85% -> 4.64% (May -> Jul), so these levels go stale.
        grid_levels=[
            0.0400,  # 4.00% - Step 1 (spread above this ~53% of the time)
            0.0420,  # 4.20% - Step 2
            0.0440,  # 4.40% - Step 3
            0.0460,  # 4.60% - Step 4 (most active reversion zone 4.3%-4.8%)
            0.0480,  # 4.80% - Step 5
            0.0500,  # 5.00% - Step 6
            0.0525,  # 5.25% - Tail level (P99 = 5.17%)
        ],

        # Risk management - $2500 capital, 10x leverage, 30% safety reserve
        # Base unit: 88.5 USDT per side (adjustable by position weights)
        # Max total: 1700 USDT (all 7 levels fully open = 1539.9 USDT + ~10% buffer)
        base_notional_per_level=88.5,    # USDT per side (base unit, scaled by weights)
        max_total_notional=1700.0,       # Maximum total exposure (covers all 7 levels)
        target_leverage=10.0,            # Target leverage (set on Bybit exchange)

        # Position weights for different grid levels (multiplier for base_notional_per_level)
        # Light at the bottom of the range, heavier at wider (rarer) spreads.
        # base_notional_per_level=88.5 USDT per side.
        # All 7 levels fully open = 2 x 88.5 x 8.7 = 1539.9 USDT total notional.
        position_weights={
            0.0400: 0.6,  #  53.1 USDT per side → 106.2 USDT total per grid
            0.0420: 0.8,  #  70.8 USDT per side → 141.6 USDT total per grid
            0.0440: 1.0,  #  88.5 USDT per side → 177.0 USDT total per grid
            0.0460: 1.2,  # 106.2 USDT per side → 212.4 USDT total per grid
            0.0480: 1.4,  # 123.9 USDT per side → 247.8 USDT total per grid
            0.0500: 1.7,  # 150.5 USDT per side → 301.0 USDT total per grid
            0.0525: 2.0,  # 177.0 USDT per side → 354.0 USDT total per grid
        },

        # Trading parameters
        maker_offset_bps=1.0,            # 0.01% offset from mid price (tighter for fine grids)
        order_timeout_sec=60.0,          # Order timeout in seconds
        rebalance_threshold_bps=10.0,   # 0.10% rebalance threshold
        extreme_spread_stop=0.060,       # 6.0% extreme spread stop (53-day max was 5.71%)

        # Features
        enable_high_levels=True,
        auto_subscribe=True,

        # Startup settings
        startup_delay_sec=10.0,  # Wait 10s for NautilusTrader position reconciliation

        # IMPORTANT: Set this when restarting with existing positions!
        # Bybit doesn't report external positions to NautilusTrader.
        # Check Bybit position page and set to actual exposure.
        # Set to 0.0 when starting fresh with no positions.
        initial_notional_override=0.0,

        # Strategy identification (required for multiple strategy instances)
        order_id_tag="001",
    )

    # Wrap strategy config in ImportableStrategyConfig
    importable_config = ImportableStrategyConfig(
        strategy_path="bz_cl_grid_strategy:BzClGridStrategy",
        config_path="bz_cl_grid_strategy:BzClGridConfig",
        config=strategy_config.dict(),
    )

    # Bybit data client configuration
    bybit_data_config = BybitDataClientConfig(
        api_key=None,  # Will use BYBIT_API_KEY env var
        api_secret=None,  # Will use BYBIT_API_SECRET env var
        base_url_http=None,  # Uses default Bybit endpoint
        instrument_provider=InstrumentProviderConfig(
            load_all=True,
            load_ids=None,
        ),
        testnet=False,  # Set to True for testnet
    )

    # Bybit execution client configuration
    bybit_exec_config = BybitExecClientConfig(
        api_key=None,  # Will use BYBIT_API_KEY env var
        api_secret=None,  # Will use BYBIT_API_SECRET env var
        base_url_http=None,  # Uses default Bybit endpoint
        instrument_provider=InstrumentProviderConfig(
            load_all=True,
            load_ids=None,
        ),
        # product_types=[BybitProductType.LINEAR],  # REMOVED: Causing TypeError with current NautilusTrader version
        testnet=False,  # Set to True for testnet
        # Using One-Way Mode (default) - position mode must be set on Bybit exchange to match
    )

    # Logging configuration
    # NOTE: NautilusTrader automatically adds timestamps to log filenames on each restart.
    # This prevents overwriting but means old files accumulate.
    # Use the cleanup_old_logs() function in run_live.py to remove old files on startup.
    logging_config = LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",  # Changed from DEBUG to INFO to reduce log size
        log_directory="logs",
        log_file_name="bz_cl_grid",
        log_file_format="json",
        log_colors=True,
        bypass_logging=False,
        log_file_max_size=10_485_760,  # 10MB per file (rotation within single series)
        log_file_max_backup_count=3,   # Keep 3 backup files per series (applies to current series only)
    )

    # Execution engine configuration
    exec_engine_config = LiveExecEngineConfig(
        reconciliation=True,  # Enable position reconciliation
        reconciliation_lookback_mins=30,  # 30 minutes — avoid phantom positions from messy prior-run history
        snapshot_orders=True,
        snapshot_positions=True,
        snapshot_positions_interval_secs=300.0,  # 5 minutes
    )

    # Trading node configuration
    config = TradingNodeConfig(
        trader_id=TraderId("TRADER-001"),
        logging=logging_config,
        exec_engine=exec_engine_config,

        # Data clients
        data_clients={
            "BYBIT": bybit_data_config,
        },

        # Execution clients
        exec_clients={
            "BYBIT": bybit_exec_config,
        },

        # Strategy configurations
        strategies=[importable_config],

        # Timeout settings
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
    )

    return config


if __name__ == "__main__":
    # Print configuration for verification
    config = create_live_config()
    print("=" * 80)
    print("BZ-CL Grid Strategy - Live Trading Configuration")
    print("=" * 80)
    print(f"Trader ID: {config.trader_id}")
    print(f"Data Clients: {list(config.data_clients.keys())}")
    print(f"Exec Clients: {list(config.exec_clients.keys())}")
    print(f"Strategies: {len(config.strategies)}")
    print("=" * 80)
