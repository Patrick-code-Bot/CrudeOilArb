"""
Live trading configuration for PAXG-XAUT Grid Strategy on Bybit
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

from paxg_xaut_grid_strategy import PaxgXautGridConfig


def create_live_config() -> TradingNodeConfig:
    """
    Create live trading configuration for PAXG-XAUT grid strategy.

    Returns
    -------
    TradingNodeConfig
        The configured trading node for live trading.
    """

    # Strategy configuration
    strategy_config = PaxgXautGridConfig(
        # Instrument IDs (Bybit perpetual swaps)
        paxg_instrument_id="PAXGUSDT-LINEAR.BYBIT",
        xaut_instrument_id="XAUTUSDT-LINEAR.BYBIT",

        # Grid levels (price spread as percentage of XAUT price)
        # Redesigned 2026-02-22: tighter spacing around the observed 0.3%-1.0% spread range.
        # Observed live spread: ~0.70% (PAXG 5126, XAUT 5091).
        # Old design had only 2 levels in 0.6%-1.0% range causing near-zero trade frequency.
        # New design: 12 levels with uniform 0.10% steps from 0.10% to 1.20%, then wider
        # safety levels at 1.50%, 2.00%, 3.00% for tail-risk protection.
        # At 0.70% spread: ~6 levels open (0.10-0.60%), level 0.70% triggers new trade,
        # levels above 0.70% wait, and any reversion below opens close cycles.
        grid_levels=[
            0.0010,  # 0.10% - Step 1
            0.0020,  # 0.20% - Step 2
            0.0030,  # 0.30% - Step 3
            0.0040,  # 0.40% - Step 4
            0.0050,  # 0.50% - Step 5
            0.0060,  # 0.60% - Step 6
            0.0070,  # 0.70% - Step 7  ← near current spread
            0.0080,  # 0.80% - Step 8
            0.0090,  # 0.90% - Step 9
            0.0100,  # 1.00% - Step 10
            0.0120,  # 1.20% - Step 11
            0.0150,  # 1.50% - Safety 1
            0.0200,  # 2.00% - Safety 2
            0.0300,  # 3.00% - Safety 3
        ],

        # Risk management - $2500 capital, 10x leverage, 30% safety reserve
        # Base unit: 88.5 USDT per side (adjustable by position weights)
        # Max total: 3000 USDT (all 14 levels fully open = 2867 USDT + 5% buffer)
        base_notional_per_level=88.5,    # USDT per side (base unit, scaled by weights)
        max_total_notional=3000.0,       # Maximum total exposure (covers all 14 levels)
        target_leverage=10.0,            # Target leverage (set on Bybit exchange)

        # Position weights for different grid levels (multiplier for base_notional_per_level)
        # Redesigned 2026-02-22: uniform weighting for the active range, larger for tail levels.
        # base_notional_per_level=88.5 USDT per side.
        position_weights={
            0.0010: 0.5,  #  44.3 USDT per side →  88.6 USDT total per grid
            0.0020: 0.6,  #  53.1 USDT per side → 106.2 USDT total per grid
            0.0030: 0.7,  #  61.9 USDT per side → 123.8 USDT total per grid
            0.0040: 0.8,  #  70.8 USDT per side → 141.6 USDT total per grid
            0.0050: 0.9,  #  79.7 USDT per side → 159.3 USDT total per grid
            0.0060: 1.0,  #  88.5 USDT per side → 177.0 USDT total per grid
            0.0070: 1.0,  #  88.5 USDT per side → 177.0 USDT total per grid
            0.0080: 1.1,  #  97.4 USDT per side → 194.7 USDT total per grid
            0.0090: 1.1,  #  97.4 USDT per side → 194.7 USDT total per grid
            0.0100: 1.2,  # 106.2 USDT per side → 212.4 USDT total per grid
            0.0120: 1.3,  # 115.1 USDT per side → 230.1 USDT total per grid
            0.0150: 1.5,  # 132.8 USDT per side → 265.5 USDT total per grid
            0.0200: 2.0,  # 177.0 USDT per side → 354.0 USDT total per grid
            0.0300: 2.5,  # 221.3 USDT per side → 442.5 USDT total per grid
        },

        # Trading parameters
        maker_offset_bps=1.0,            # 0.01% offset from mid price (tighter for fine grids)
        order_timeout_sec=60.0,          # Order timeout in seconds
        rebalance_threshold_bps=10.0,   # 0.10% rebalance threshold (matches grid spacing)
        extreme_spread_stop=0.035,       # 3.5% extreme spread stop (above highest grid at 3.0%)

        # Features
        enable_high_levels=True,
        auto_subscribe=True,

        # Startup settings
        startup_delay_sec=10.0,  # Wait 10s for NautilusTrader position reconciliation

        # IMPORTANT: Set this when restarting with existing positions!
        # Bybit doesn't report external positions to NautilusTrader.
        # Check Bybit position page and set to actual exposure.
        # Set to 0.0 when starting fresh with no positions.
        # UPDATED 2026-02-22: Set to 0.0 - no positions on Bybit, starting fresh.
        initial_notional_override=0.0,

        # Strategy identification (required for multiple strategy instances)
        order_id_tag="001",
    )

    # Wrap strategy config in ImportableStrategyConfig
    importable_config = ImportableStrategyConfig(
        strategy_path="paxg_xaut_grid_strategy:PaxgXautGridStrategy",
        config_path="paxg_xaut_grid_strategy:PaxgXautGridConfig",
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
        log_file_name="paxg_xaut_grid",
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
    print("PAXG-XAUT Grid Strategy - Live Trading Configuration")
    print("=" * 80)
    print(f"Trader ID: {config.trader_id}")
    print(f"Data Clients: {list(config.data_clients.keys())}")
    print(f"Exec Clients: {list(config.exec_clients.keys())}")
    print(f"Strategies: {len(config.strategies)}")
    print("=" * 80)
