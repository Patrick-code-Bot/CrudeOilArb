"""
PAXG-XAUT Grid Spread Arbitrage Strategy (NautilusTrader, Bybit single-venue)

逻辑概要：
- 在 Bybit 上订阅 PAXG/USDT-LINEAR 与 XAUT/USDT-LINEAR 两个合约
- 实时计算价差 spread = (PAXG - XAUT) / XAUT
- 使用预设的网格 levels（例如 [0.001, 0.002, ...]）
- 当 spread 超过某一档 level：高卖贵的、低买便宜的（成对开仓）
  * 开仓使用限价单（limit orders, GTC）作为 maker，降低手续费；
    若超时未同时成交，自动取消未成交腿并平掉已成交腿（防止单腿风险）
- 当 spread 回落到上一个 level 以下：平掉该档位的对冲仓位
  * 平仓使用限价单（limit orders）以更好的价格捕获利润
- 杠杆建议在 Bybit 侧设置为约 10x，本策略通过 max_total_notional 控制整体风险敞口
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple, Any

from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.enums import OrderSide, TimeInForce, OrderType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.orders import LimitOrder, MarketOrder


# ==========================
# 配置 Config
# ==========================

class PaxgXautGridConfig(StrategyConfig, frozen=True):
    # 交易标的（请用你实际环境里的 InstrumentId 字符串）
    # 例如："PAXGUSDT-LINEAR.BYBIT" 和 "XAUTUSDT-LINEAR.BYBIT"
    paxg_instrument_id: str = "PAXGUSDT-LINEAR.BYBIT"
    xaut_instrument_id: str = "XAUTUSDT-LINEAR.BYBIT"

    # 网格档位（价差：相对 XAUT 的百分比）
    # 例如 0.001 = 0.10%，0.01 = 1%
    grid_levels: List[float] = field(
        default_factory=lambda: [0.0010, 0.0020, 0.0030, 0.0040,
                                 0.0050, 0.0060, 0.0080, 0.0100]
    )

    # 每档网格对应的名义价值（USDT），真实下单数量 = notional / price
    base_notional_per_level: float = 100.0

    # 各档位权重映射（用于调整不同价差水平的仓位大小）
    # Key: grid level, Value: weight multiplier (applied to base_notional_per_level)
    # If not specified for a level, default weight is 1.0
    position_weights: Dict[float, float] = field(
        default_factory=lambda: {
            0.0010: 0.4,  0.0015: 0.5,  0.0020: 0.6,
            0.0025: 0.7,  0.0030: 0.8,  0.0040: 1.0,
            0.0050: 1.0,  0.0060: 1.0,  0.0080: 1.2,
            0.0100: 1.5,  0.0150: 1.8,  0.0200: 2.0,
            0.0300: 2.5,  0.0500: 3.0,  0.0800: 3.5
        }
    )

    # 最大总名义风险（两条腿合计）
    # 如果你打算 10x 杠杆，可以设置为：账户权益的 5~8 倍（视风险偏好）
    max_total_notional: float = 1000.0

    # 目标杠杆（仅用于日志与风险思路说明；实际杠杆通过 Bybit 账户/仓位配置控制）
    target_leverage: float = 10.0

    # 是否启用“增强区”（更高档位）——你可以以后接参数调整风险
    enable_high_levels: bool = True

    # maker 挂单相对中间价偏移（bps = 万分之一）
    maker_offset_bps: float = 1.0  # 0.01%

    # 挂单超时秒数（没成交就撤单重挂）
    order_timeout_sec: float = 60.0

    # rebalance 阈值：两腿名义不平衡多少 bps 时，自动微调
    rebalance_threshold_bps: float = 20.0  # 0.20%

    # 极端价差风控：超过该值（例如 1.5%）全平并暂停策略
    extreme_spread_stop: float = 0.015  # 1.5%

    # Minimum net profit (USDT) required before closing a grid level.
    # Net P&L is calculated as: gross unrealized P&L - round-trip taker fees.
    # Set to 0.0 to disable the check (close whenever spread reverts).
    min_profit_usdt: float = 0.0

    # Bybit taker fee rate used for net P&L estimation (default 0.055%).
    # Used in: round-trip fee = open_notional * taker_fee_rate * 2
    taker_fee_rate: float = 0.00055

    # Bybit minimum order notional value (USDT). Orders below this are rejected by
    # the exchange.  Used to pre-screen rebalance correction orders before submission.
    min_order_notional_usdt: float = 5.0

    # 是否在启动时自动订阅行情
    auto_subscribe: bool = True

    # Startup delay in seconds before processing grids (allows position reconciliation to complete)
    startup_delay_sec: float = 10.0

    # Manual initial notional override (set this when restarting with existing positions)
    # Bybit doesn't report external positions to NautilusTrader, so manual override is needed
    # Set to 0.0 to disable, or set to actual exposure (e.g., 6000.0) when positions exist
    initial_notional_override: float = 0.0


# ==========================
# 内部数据结构
# ==========================

@dataclass
class GridPositionState:
    level: float
    paxg_pos_id: Optional[Any] = None  # PositionId type
    xaut_pos_id: Optional[Any] = None  # PositionId type
    # 可以扩展记录：建仓价、建仓时间等


@dataclass
class PairedOrderTracker:
    """Track paired PAXG+XAUT orders to detect partial fills"""
    level: float
    paxg_order_id: Any
    xaut_order_id: Any
    paxg_filled: bool = False
    xaut_filled: bool = False
    submit_time: int = 0  # timestamp in nanoseconds
    paxg_leg: str = ""  # "PAXG_LONG" or "PAXG_SHORT"
    xaut_leg: str = ""  # "XAUT_LONG" or "XAUT_SHORT"


@dataclass
class PairedCloseTracker:
    """Track paired PAXG+XAUT close orders to detect partial closes"""
    level: float
    paxg_order_id: Optional[Any] = None
    xaut_order_id: Optional[Any] = None
    paxg_filled: bool = False
    xaut_filled: bool = False
    submit_time: int = 0  # timestamp in nanoseconds


# ==========================
# 策略主体
# ==========================

class PaxgXautGridStrategy(Strategy):
    def __init__(self, config: PaxgXautGridConfig) -> None:
        super().__init__(config)

        # Instruments
        self.paxg_id = InstrumentId.from_str(config.paxg_instrument_id)
        self.xaut_id = InstrumentId.from_str(config.xaut_instrument_id)

        self.paxg = None
        self.xaut = None

        # 最新报价
        self.paxg_bid: Optional[float] = None
        self.paxg_ask: Optional[float] = None
        self.xaut_bid: Optional[float] = None
        self.xaut_ask: Optional[float] = None

        # 网格状态：level -> GridPositionState
        self.grid_state: Dict[float, GridPositionState] = {}

        # 在途订单追踪：order_id -> (level, leg)
        # leg: "PAXG_LONG", "PAXG_SHORT", "XAUT_LONG", "XAUT_SHORT"
        self.working_orders: Dict[Any, tuple[float, str]] = {}  # OrderId type

        # 配对订单追踪：用于检测不平衡成交
        # Key: 使用唯一标识符 (submit_time + level) 来追踪每对订单
        self.paired_orders: Dict[int, PairedOrderTracker] = {}

        # 配对平仓订单追踪：用于检测不平衡平仓
        # Key: submit_time, Value: PairedCloseTracker
        self.paired_close_orders: Dict[int, PairedCloseTracker] = {}

        # 累计名义风险（仅计算实际成交的持仓）
        self.total_notional: float = 0.0
        # 待确认名义风险（已提交但未成交的订单）
        self.pending_notional: float = 0.0

        # Flag to track if we've synced existing positions (done after startup delay)
        self._positions_synced: bool = False
        # Timestamp when strategy started (for startup delay calculation)
        self._start_time_ns: int = 0
        # Last reconciliation timestamp (for periodic position reconciliation)
        self._last_reconciliation_ns: int = 0
        # Reconciliation interval (60 seconds)
        self._reconciliation_interval_ns: int = 60_000_000_000

        # Last rebalance timestamp and cooldown (prevent rebalancing on every tick)
        self._last_rebalance_ns: int = 0
        self._rebalance_cooldown_ns: int = 60_000_000_000  # 60 seconds between rebalances

        # Track rebalance correction order IDs so fill/reject/cancel events are handled
        self._rebalance_order_ids: set = set()

        # Per-level retry cooldown: after a pair failure/rejection, block re-opening
        # the same level for 30 seconds to prevent the infinite re-open loop.
        # Key: grid level, Value: earliest timestamp (ns) at which the level may reopen.
        self._level_retry_after: Dict[float, int] = {}
        self._level_retry_cooldown_ns: int = 30_000_000_000  # 30 seconds

    # ========== 生命周期 ==========
    def on_start(self) -> None:
        self.log.info(
            f"PaxgXautGridStrategy starting on Bybit, target_leverage={self.config.target_leverage}x, "
            f"max_total_notional={self.config.max_total_notional}."
        )

        # 获取 instrument
        self.paxg = self.cache.instrument(self.paxg_id)
        self.xaut = self.cache.instrument(self.xaut_id)

        if self.paxg is None or self.xaut is None:
            raise RuntimeError(
                f"Instruments not found in cache. Check instruments config: "
                f"paxg={self.paxg_id}, xaut={self.xaut_id}"
            )

        # 初始化网格状态
        for level in self.config.grid_levels:
            self.grid_state[level] = GridPositionState(level=level)

        # Note: Position sync is done on first quote tick (after NautilusTrader reconciliation completes)
        # See _sync_existing_positions() called in on_quote_tick()

        if self.config.auto_subscribe:
            # Subscribe to quote ticks for both instruments
            self.subscribe_quote_ticks(instrument_id=self.paxg_id)
            self.subscribe_quote_ticks(instrument_id=self.xaut_id)
            self.log.info(
                f"Subscribed to quote ticks: PAXG={self.paxg_id}, XAUT={self.xaut_id}"
            )

        # Record start time for startup delay calculation
        self._start_time_ns = self.clock.timestamp_ns()

        self.log.info(
            f"Strategy initialized with grid_levels={self.config.grid_levels}, "
            f"startup_delay={self.config.startup_delay_sec}s"
        )

    def on_stop(self) -> None:
        self.log.info("PaxgXautGridStrategy stopping, cancelling working orders and closing positions...")
        # 撤销所有挂单
        for order_id in list(self.working_orders.keys()):
            self.cancel_order(order_id)
        # 可以按需选择是否强平所有持仓
        # self.flatten_all()

    def _sync_existing_positions(self) -> None:
        """
        Sync existing positions from exchange on startup.

        FIXED VERSION: Properly accounts for position weights and marks the CORRECT
        grid levels (positions open at HIGH spreads, not low ones).

        Uses multiple methods to detect existing positions:
        1. Manual override (initial_notional_override) - most reliable for Bybit
        2. cache.positions_open() - positions tracked by NautilusTrader
        3. portfolio.net_exposure() - net exposure by instrument
        4. cache.positions() - fallback

        Note: Bybit doesn't report external positions to NautilusTrader's reconciliation,
        so manual override is the most reliable method for restarts with existing positions.
        """
        # Method 0: Check for manual override first (most reliable for Bybit)
        if self.config.initial_notional_override > 0:
            self.total_notional = self.config.initial_notional_override

            # FIX: Mark grid levels from HIGHEST to LOWEST (positions open at high spreads)
            # and account for position weights
            levels_reverse = sorted(self.config.grid_levels, reverse=True)
            remaining_notional = self.total_notional
            marked_levels = []

            for level in levels_reverse:
                weight = self.config.position_weights.get(level, 1.0)
                notional_for_level = 2 * self.config.base_notional_per_level * weight

                if remaining_notional >= notional_for_level * 0.8:  # 80% threshold for matching
                    state = self.grid_state[level]
                    state.paxg_pos_id = "MANUAL_OVERRIDE"
                    state.xaut_pos_id = "MANUAL_OVERRIDE"
                    marked_levels.append(level)
                    remaining_notional -= notional_for_level
                    self.log.info(
                        f"Marked grid level={level:.4f} ({level*100:.2f}%) as occupied "
                        f"(notional={notional_for_level:.2f}, remaining={remaining_notional:.2f})"
                    )

                if remaining_notional < 50:  # Stop when remaining is negligible
                    break

            self.log.warning(
                f"⚠️ STARTUP SYNC (MANUAL): initial_notional_override={self.total_notional:.2f}. "
                f"Marked {len(marked_levels)} grid level(s) as occupied: "
                f"{[f'{l*100:.2f}%' for l in sorted(marked_levels)]}. "
                f"Unaccounted notional: {remaining_notional:.2f}"
            )
            return

        paxg_pos = None
        xaut_pos = None
        paxg_notional = 0.0
        xaut_notional = 0.0

        # Method 1: Check cache.positions_open() for open positions
        for pos in self.cache.positions_open():
            if pos.instrument_id == self.paxg_id:
                paxg_pos = pos
                paxg_notional = abs(float(pos.quantity) * float(pos.avg_px_open))
                self.log.info(
                    f"[cache.positions_open] Found PAXG position: qty={pos.quantity}, "
                    f"side={'LONG' if pos.is_long else 'SHORT'}, notional={paxg_notional:.2f}"
                )
            elif pos.instrument_id == self.xaut_id:
                xaut_pos = pos
                xaut_notional = abs(float(pos.quantity) * float(pos.avg_px_open))
                self.log.info(
                    f"[cache.positions_open] Found XAUT position: qty={pos.quantity}, "
                    f"side={'LONG' if pos.is_long else 'SHORT'}, notional={xaut_notional:.2f}"
                )

        # Method 2: If no positions found, try portfolio.net_exposure()
        # This uses current market prices and may detect positions not in cache
        if paxg_pos is None and xaut_pos is None:
            try:
                # Get net exposure from portfolio (uses current prices)
                paxg_exposure = self.portfolio.net_exposure(self.paxg_id)
                xaut_exposure = self.portfolio.net_exposure(self.xaut_id)

                if paxg_exposure is not None:
                    paxg_notional = abs(float(paxg_exposure))
                    if paxg_notional > 0:
                        self.log.info(f"[portfolio.net_exposure] PAXG exposure: {paxg_notional:.2f}")

                if xaut_exposure is not None:
                    xaut_notional = abs(float(xaut_exposure))
                    if xaut_notional > 0:
                        self.log.info(f"[portfolio.net_exposure] XAUT exposure: {xaut_notional:.2f}")

            except Exception as e:
                self.log.warning(f"Error checking portfolio.net_exposure: {e}")

        # Method 3: Also check cache.positions() as fallback
        if paxg_pos is None and xaut_pos is None and paxg_notional == 0 and xaut_notional == 0:
            for pos in self.cache.positions():
                if pos.instrument_id == self.paxg_id and pos.is_open:
                    paxg_pos = pos
                    paxg_notional = abs(float(pos.quantity) * float(pos.avg_px_open))
                    self.log.info(f"[cache.positions] Found PAXG: notional={paxg_notional:.2f}")
                elif pos.instrument_id == self.xaut_id and pos.is_open:
                    xaut_pos = pos
                    xaut_notional = abs(float(pos.quantity) * float(pos.avg_px_open))
                    self.log.info(f"[cache.positions] Found XAUT: notional={xaut_notional:.2f}")

        # Calculate total and sync state
        total_detected = paxg_notional + xaut_notional

        if total_detected > 0:
            self.total_notional = total_detected

            # FIX: Mark grid levels from HIGHEST to LOWEST (positions open at high spreads)
            # and account for position weights
            levels_reverse = sorted(self.config.grid_levels, reverse=True)
            remaining_notional = self.total_notional
            marked_levels = []

            for level in levels_reverse:
                weight = self.config.position_weights.get(level, 1.0)
                notional_for_level = 2 * self.config.base_notional_per_level * weight

                if remaining_notional >= notional_for_level * 0.8:  # 80% threshold for matching
                    state = self.grid_state[level]
                    # Use actual position IDs if available
                    if paxg_pos is not None:
                        state.paxg_pos_id = paxg_pos.id
                    else:
                        state.paxg_pos_id = "DETECTED"  # Marker for detected but not tracked
                    if xaut_pos is not None:
                        state.xaut_pos_id = xaut_pos.id
                    else:
                        state.xaut_pos_id = "DETECTED"

                    marked_levels.append(level)
                    remaining_notional -= notional_for_level
                    self.log.info(
                        f"Marked grid level={level:.4f} ({level*100:.2f}%) as occupied "
                        f"(notional={notional_for_level:.2f}, remaining={remaining_notional:.2f})"
                    )

                if remaining_notional < 50:  # Stop when remaining is negligible
                    break

            self.log.warning(
                f"⚠️ STARTUP SYNC: Detected {len(marked_levels)} grid level(s) of existing exposure. "
                f"total_notional={self.total_notional:.2f} (PAXG={paxg_notional:.2f}, XAUT={xaut_notional:.2f}). "
                f"Marked levels: {[f'{l*100:.2f}%' for l in sorted(marked_levels)]}. "
                f"Unaccounted notional: {remaining_notional:.2f}"
            )
        else:
            self.log.info("No existing positions detected via cache or portfolio, starting fresh.")

    # ========== 行情处理 ==========
    def on_quote_tick(self, tick: QuoteTick) -> None:
        # 更新最新报价
        if tick.instrument_id == self.paxg_id:
            self.paxg_bid = float(tick.bid_price)
            self.paxg_ask = float(tick.ask_price)
        elif tick.instrument_id == self.xaut_id:
            self.xaut_bid = float(tick.bid_price)
            self.xaut_ask = float(tick.ask_price)

        if not self._has_valid_quotes():
            return

        # Wait for startup delay before processing (allows position reconciliation to complete)
        elapsed_ns = self.clock.timestamp_ns() - self._start_time_ns
        startup_delay_ns = int(self.config.startup_delay_sec * 1_000_000_000)
        if elapsed_ns < startup_delay_ns:
            return  # Still in startup delay period

        # Sync existing positions after startup delay
        # This ensures NautilusTrader has finished reconciling positions from exchange
        if not self._positions_synced:
            self._sync_existing_positions()
            self._positions_synced = True

        spread = self._calc_spread()
        if spread is None:
            return

        # 极端风控
        if abs(spread) > self.config.extreme_spread_stop:
            self.log.warning(f"Extreme spread detected {spread:.4%}, closing all and pausing.")
            self._close_all_grids()
            # 这里可以选择 disable 策略 / raise 等
            return

        # 网格开仓 / 平仓逻辑
        self._process_grids(spread)

        # FIX #4: Periodic position reconciliation (every 60 seconds)
        if self._should_reconcile():
            self._reconcile_positions()

        # 持仓 rebalance
        self._rebalance_if_needed()

        # 检查挂单是否超时（可根据你需求补充完整实现）
        self._check_order_timeouts()

    # ========== 订单回报 ==========
    def on_order_accepted(self, event) -> None:
        self.log.debug(f"Order accepted: {event.client_order_id}")

    def on_order_rejected(self, event) -> None:
        self.log.warning(f"Order rejected: {event.client_order_id}, reason: {event.reason}")
        self.working_orders.pop(event.client_order_id, None)

        # Rebalance correction orders are tracked separately
        if event.client_order_id in self._rebalance_order_ids:
            self._rebalance_order_ids.discard(event.client_order_id)
            self.log.warning(
                f"Rebalance correction order rejected: {event.client_order_id}, "
                f"reason: {event.reason}. Enforcing full cooldown before retry."
            )
            # Enforce the full 60-second cooldown so the loop doesn't
            # hammer Bybit with a rejected order on every quote tick.
            self._last_rebalance_ns = self.clock.timestamp_ns()
            return

        # 检查是否是配对订单中的一个被拒绝
        # 如果是，需要检查另一侧是否已成交，如果成交了需要平仓
        self._handle_order_failure(event.client_order_id, "rejected")

    def on_order_canceled(self, event) -> None:
        self.log.debug(f"Order canceled: {event.client_order_id}")
        self.working_orders.pop(event.client_order_id, None)
        if event.client_order_id in self._rebalance_order_ids:
            self._rebalance_order_ids.discard(event.client_order_id)
            self.log.warning(f"Rebalance correction order canceled: {event.client_order_id}")

    # ========== 仓位事件处理 (NautilusTrader内置) ==========
    def on_position_opened(self, event) -> None:
        """Handle position opened events - updates total_notional tracking"""
        self.log.info(
            f"Position opened: {event.instrument_id}, "
            f"qty={event.quantity}, side={event.entry}, "
            f"avg_px={event.avg_px_open}"
        )
        # Update notional tracking based on actual positions
        self._update_notional_from_portfolio()

    def on_position_changed(self, event) -> None:
        """Handle position changed events - updates total_notional tracking"""
        self.log.debug(
            f"Position changed: {event.instrument_id}, "
            f"qty={event.quantity}, unrealized_pnl={event.unrealized_pnl}"
        )

    def on_position_closed(self, event) -> None:
        """Handle position closed events - reduces total_notional tracking"""
        self.log.info(
            f"Position closed: {event.instrument_id}, "
            f"realized_pnl={event.realized_pnl}"
        )
        # Update notional tracking based on actual positions
        self._update_notional_from_portfolio()

    def _update_notional_from_portfolio(self) -> None:
        """Update total_notional based on actual portfolio positions.

        Uses portfolio.net_exposure() which correctly aggregates EXTERNAL
        (reconciled) and internal positions for the same instrument, avoiding
        the double-counting that occurs when both coexist during reconciliation.
        """
        try:
            paxg_notional = 0.0
            xaut_notional = 0.0

            # portfolio.net_exposure() returns the true aggregate signed exposure
            # (negative = short, positive = long) across ALL positions including EXTERNAL.
            try:
                paxg_exp = self.portfolio.net_exposure(self.paxg_id)
                xaut_exp = self.portfolio.net_exposure(self.xaut_id)
                if paxg_exp is not None:
                    paxg_notional = abs(float(paxg_exp))
                if xaut_exp is not None:
                    xaut_notional = abs(float(xaut_exp))
            except Exception as e:
                self.log.warning(
                    f"net_exposure() failed ({e}), falling back to position iteration"
                )
                # Fallback: sum all open positions per instrument
                for pos in self.cache.positions_open():
                    if pos.instrument_id == self.paxg_id:
                        paxg_notional += float(pos.quantity) * float(pos.avg_px_open)
                    elif pos.instrument_id == self.xaut_id:
                        xaut_notional += float(pos.quantity) * float(pos.avg_px_open)

            new_total = paxg_notional + xaut_notional
            if abs(new_total - self.total_notional) > 1.0:  # Only log if significant change
                self.log.info(
                    f"Updated total_notional: {self.total_notional:.2f} -> {new_total:.2f} "
                    f"(PAXG={paxg_notional:.2f}, XAUT={xaut_notional:.2f})"
                )
            self.total_notional = new_total
        except Exception as e:
            self.log.warning(f"Error updating notional from portfolio: {e}")

    def _handle_order_failure(self, order_id: Any, reason: str) -> None:
        """处理订单失败（拒绝/超时）时的配对订单清理"""
        for submit_time, tracker in list(self.paired_orders.items()):
            if tracker.paxg_order_id == order_id:
                notional = self._get_level_notional(tracker.level)
                self.log.warning(f"PAXG order {reason} for level={tracker.level}")
                # 如果 XAUT 已经成交，需要平掉
                if tracker.xaut_filled:
                    self.log.warning(f"XAUT already filled, closing XAUT position for level={tracker.level}")
                    state = self.grid_state.get(tracker.level)
                    if state and state.xaut_pos_id:
                        correction_order = self._close_position(state.xaut_pos_id)
                        if correction_order is not None:
                            self._rebalance_order_ids.add(correction_order.client_order_id)
                        state.xaut_pos_id = None
                else:
                    # XAUT 还没成交，取消它
                    self._safe_cancel_order(tracker.xaut_order_id)
                # 清理pending_notional
                self.pending_notional = max(0.0, self.pending_notional - 2 * notional)
                self.log.info(f"Order failure cleanup, pending_notional={self.pending_notional:.2f}")
                del self.paired_orders[submit_time]
                # Block this level from re-opening immediately (prevents tight retry loop)
                self._level_retry_after[tracker.level] = (
                    self.clock.timestamp_ns() + self._level_retry_cooldown_ns
                )
                self.log.info(
                    f"Level={tracker.level} retry cooldown set for 30s after order failure"
                )
                break
            elif tracker.xaut_order_id == order_id:
                notional = self._get_level_notional(tracker.level)
                self.log.warning(f"XAUT order {reason} for level={tracker.level}")
                # 如果 PAXG 已经成交，需要平掉
                if tracker.paxg_filled:
                    self.log.warning(f"PAXG already filled, closing PAXG position for level={tracker.level}")
                    state = self.grid_state.get(tracker.level)
                    if state and state.paxg_pos_id:
                        correction_order = self._close_position(state.paxg_pos_id)
                        if correction_order is not None:
                            self._rebalance_order_ids.add(correction_order.client_order_id)
                        state.paxg_pos_id = None
                else:
                    # PAXG 还没成交，取消它
                    self._safe_cancel_order(tracker.paxg_order_id)
                # 清理pending_notional
                self.pending_notional = max(0.0, self.pending_notional - 2 * notional)
                self.log.info(f"Order failure cleanup, pending_notional={self.pending_notional:.2f}")
                del self.paired_orders[submit_time]
                # Block this level from re-opening immediately (prevents tight retry loop)
                self._level_retry_after[tracker.level] = (
                    self.clock.timestamp_ns() + self._level_retry_cooldown_ns
                )
                self.log.info(
                    f"Level={tracker.level} retry cooldown set for 30s after order failure"
                )
                break

    def on_order_filled(self, event) -> None:
        self.log.info(f"Order filled: {event.client_order_id}")
        level, leg = self.working_orders.pop(event.client_order_id, (None, None))

        if level is None:
            # Check if this is a rebalance correction order fill
            if event.client_order_id in self._rebalance_order_ids:
                self._rebalance_order_ids.discard(event.client_order_id)
                self.log.info(
                    f"Rebalance correction order filled: {event.client_order_id}. "
                    f"Updating notional from portfolio."
                )
                self._update_notional_from_portfolio()
            else:
                # Close orders are NOT added to working_orders, so they also reach here.
                # Without this call the paired_close_orders tracker is never updated,
                # grid state is never cleared, and _check_close_order_timeouts() retries
                # indefinitely every 5 seconds.
                self._handle_close_order_fill(event)
            return

        # 更新配对订单追踪器，检查是否两边都成交了
        both_filled = False
        tracker_submit_time = None  # Track which tracker to potentially remove

        for submit_time, tracker in self.paired_orders.items():
            notional = self._get_level_notional(tracker.level)
            if tracker.paxg_order_id == event.client_order_id:
                tracker.paxg_filled = True
                tracker_submit_time = submit_time
                self.log.debug(f"PAXG order filled for level={tracker.level}")
                # 检查是否两边都成交
                if tracker.xaut_filled:
                    both_filled = True
                    # 从待确认转移到已确认
                    self.pending_notional = max(0.0, self.pending_notional - 2 * notional)
                    self.total_notional += 2 * notional
                    self.log.info(
                        f"Both orders filled for level={tracker.level}, "
                        f"moved {2*notional:.2f} from pending to total. "
                        f"Total={self.total_notional:.2f}, Pending={self.pending_notional:.2f}"
                    )
                break
            elif tracker.xaut_order_id == event.client_order_id:
                tracker.xaut_filled = True
                tracker_submit_time = submit_time
                self.log.debug(f"XAUT order filled for level={tracker.level}")
                # 检查是否两边都成交
                if tracker.paxg_filled:
                    both_filled = True
                    # 从待确认转移到已确认
                    self.pending_notional = max(0.0, self.pending_notional - 2 * notional)
                    self.total_notional += 2 * notional
                    self.log.info(
                        f"Both orders filled for level={tracker.level}, "
                        f"moved {2*notional:.2f} from pending to total. "
                        f"Total={self.total_notional:.2f}, Pending={self.pending_notional:.2f}"
                    )
                break

        # 更新持仓状态
        pos = self.cache.position_for_order(event.client_order_id)
        state = self.grid_state.get(level)

        if state is None:
            return

        if leg == "PAXG_LONG":
            state.paxg_pos_id = pos.id if pos is not None else "FILLED"
        elif leg == "PAXG_SHORT":
            state.paxg_pos_id = pos.id if pos is not None else "FILLED"
        elif leg == "XAUT_LONG":
            state.xaut_pos_id = pos.id if pos is not None else "FILLED"
        elif leg == "XAUT_SHORT":
            state.xaut_pos_id = pos.id if pos is not None else "FILLED"

        # Clean up tracker immediately when both orders filled AND positions are set
        # This prevents the race condition while ensuring positions are tracked
        if both_filled and tracker_submit_time is not None:
            if state.paxg_pos_id is not None and state.xaut_pos_id is not None:
                del self.paired_orders[tracker_submit_time]
                self.log.debug(
                    f"Removed tracker for level={level} after both fills confirmed "
                    f"and positions set (PAXG={state.paxg_pos_id}, XAUT={state.xaut_pos_id})"
                )

        # FIX #3: Handle close order fills
        # Check if this is a close order
        self._handle_close_order_fill(event)

    def _handle_close_order_fill(self, event) -> None:
        """
        Handle close order fills and only clear position state when both legs fill.

        FIX #3: Prevents imbalanced closes by tracking close orders separately.
        """
        order_id = event.client_order_id

        # Find the close order tracker for this order
        for submit_time, tracker in list(self.paired_close_orders.items()):
            notional = self._get_level_notional(tracker.level)

            if tracker.paxg_order_id == order_id:
                tracker.paxg_filled = True
                self.log.info(f"PAXG close order filled for level={tracker.level}")

                # Check if both filled
                if tracker.xaut_filled:
                    # Both legs closed successfully
                    state = self.grid_state.get(tracker.level)
                    if state:
                        state.paxg_pos_id = None
                        state.xaut_pos_id = None
                    self.total_notional = max(0.0, self.total_notional - 2 * notional)
                    del self.paired_close_orders[submit_time]
                    self.log.info(
                        f"✓ Grid level {tracker.level} fully closed. "
                        f"Reduced notional by {2*notional:.2f}. "
                        f"Total={self.total_notional:.2f}"
                    )
                break

            elif tracker.xaut_order_id == order_id:
                tracker.xaut_filled = True
                self.log.info(f"XAUT close order filled for level={tracker.level}")

                # Check if both filled
                if tracker.paxg_filled:
                    # Both legs closed successfully
                    state = self.grid_state.get(tracker.level)
                    if state:
                        state.paxg_pos_id = None
                        state.xaut_pos_id = None
                    self.total_notional = max(0.0, self.total_notional - 2 * notional)
                    del self.paired_close_orders[submit_time]
                    self.log.info(
                        f"✓ Grid level {tracker.level} fully closed. "
                        f"Reduced notional by {2*notional:.2f}. "
                        f"Total={self.total_notional:.2f}"
                    )
                break

    # ========== 网格逻辑 ==========
    def _process_grids(self, spread: float) -> None:
        """
        spread = (PAXG - XAUT) / XAUT
        > 0 : PAXG 贵，做空 PAXG & 做多 XAUT
        < 0 : XAUT 贵，做空 XAUT & 做多 PAXG
        """
        abs_spread = abs(spread)

        # 1) 先处理"平仓条件"：spread 回到前一档以内 -> 平该档位
        # Limit to 1 NEW close pair per tick to avoid Bybit API rate-limit bursts.
        # On restart, many levels may need closing simultaneously; without this guard
        # all 14+ orders land in the same millisecond and most are rejected with
        # "Too many visits. Exceeded the API Rate Limit."
        levels_sorted = sorted(self.config.grid_levels)
        new_grids_closed = 0
        for i, level in enumerate(levels_sorted):
            state = self.grid_state[level]
            if not self._grid_has_position(state):
                continue

            # For the first grid level there is no lower neighbour, so close when the
            # spread retracts to half of that level's own threshold (e.g. 0.05% for the
            # 0.10% level).  Using 0.0 as the floor was a bug: abs_spread < 0.0 is
            # mathematically impossible and the first level would never close.
            if i == 0:
                prev_level = levels_sorted[0] / 2.0
            else:
                prev_level = levels_sorted[i - 1]
            if abs_spread < prev_level:
                # Check if this level is already being closed (tracker exists)
                already_closing = any(t.level == level for t in self.paired_close_orders.values())
                if not already_closing:
                    # Rate-limit: only submit 1 new close pair per tick
                    if new_grids_closed >= 1:
                        continue  # Will be picked up on the next tick
                    self.log.info(f"Closing grid level={level}, spread={spread:.4%}")
                    new_grids_closed += 1
                self._close_grid(level, state)

        # 2) 再处理"开仓条件"：spread 超过某档且该档没有持仓/待处理订单 -> 开新对冲
        # Limit to 1 new grid open per tick to prevent rate-limit bursts when the
        # strategy starts and spread already exceeds many levels simultaneously.
        new_grids_opened = 0
        for i, level in enumerate(levels_sorted):
            state = self.grid_state[level]
            # 检查是否已有持仓
            if self._grid_has_position(state):
                continue

            # 检查是否已有pending订单 - 防止重复提交
            if self._grid_has_pending_orders(level):
                continue

            # Respect per-level retry cooldown after a pair failure/rejection
            retry_after = self._level_retry_after.get(level, 0)
            if self.clock.timestamp_ns() < retry_after:
                continue

            if abs_spread > level:
                # Only open 1 new grid per tick; subsequent levels wait for the next tick
                if new_grids_opened >= 1:
                    break

                # 检查总风险（包括已成交和待成交的订单）
                notional = self._get_level_notional(level)
                total_exposure = self.total_notional + self.pending_notional + 2 * notional
                if total_exposure > self.config.max_total_notional:
                    self.log.warning(
                        f"Max total notional reached (total={self.total_notional:.2f}, "
                        f"pending={self.pending_notional:.2f}, would_add={2*notional:.2f}), skip new grid."
                    )
                    continue

                self.log.info(f"Opening grid level={level}, spread={spread:.4%}")
                self._open_grid(level, spread)
                new_grids_opened += 1

    def _grid_has_position(self, state: GridPositionState) -> bool:
        return (state.paxg_pos_id is not None) or (state.xaut_pos_id is not None)

    def _grid_has_pending_orders(self, level: float) -> bool:
        """Check if there are pending or recently-filled orders for the specified grid level.
        
        Returns True if ANY tracker exists for this level, regardless of fill status.
        This prevents race conditions where both orders fill but position IDs haven't
        been updated yet in grid_state, which could cause duplicate grid openings.
        """
        for tracker in self.paired_orders.values():
            if tracker.level == level:
                return True  # Any tracker for this level = don't open new grid
        return False

    # ========== Grid 开仓 / 平仓 ==========
    def _get_level_notional(self, level: float) -> float:
        """Get position size for a specific grid level using weight mapping"""
        weight = self.config.position_weights.get(level, 1.0)
        return self.config.base_notional_per_level * weight

    def _open_grid(self, level: float, spread: float) -> None:
        """
        spread > 0: PAXG 贵 → 空 PAXG，多 XAUT
        spread < 0: XAUT 贵 → 空 XAUT，多 PAXG

        使用限价单（maker）建立对冲仓位，降低手续费并避免滑点。

        Imbalance protection (both legs):
        - Both legs are submitted simultaneously as GTC limit orders.
        - The PairedOrderTracker records their order IDs.
        - _check_order_timeouts() fires every tick and inspects each tracker
          after order_timeout_sec seconds:
            * One leg filled, other not → cancel unfilled leg, close filled
              leg via market IOC order to restore market-neutral state.
            * Neither filled → cancel both orders and free pending_notional.
        - This logic is identical for market and limit orders, so no changes
          to the imbalance mechanism are required.

        Price calculation:
        - BUY  leg: min(ask, mid - maker_offset_bps) — passive bid inside spread
        - SELL leg: max(bid, mid + maker_offset_bps) — passive offer inside spread
        """
        if (self.paxg_bid is None or self.paxg_ask is None or
                self.xaut_bid is None or self.xaut_ask is None):
            return

        if spread > 0:
            # 空 PAXG，多 XAUT
            paxg_side = OrderSide.SELL
            xaut_side = OrderSide.BUY
            paxg_leg_tag = "PAXG_SHORT"
            xaut_leg_tag = "XAUT_LONG"
        else:
            # 空 XAUT，多 PAXG
            paxg_side = OrderSide.BUY
            xaut_side = OrderSide.SELL
            paxg_leg_tag = "PAXG_LONG"
            xaut_leg_tag = "XAUT_SHORT"

        # Use mid price for quantity calculation (market orders fill at best available)
        paxg_mid = self._mid_price(self.paxg_bid, self.paxg_ask)
        xaut_mid = self._mid_price(self.xaut_bid, self.xaut_ask)

        notional = self._get_level_notional(level)
        paxg_qty = notional / paxg_mid
        xaut_qty = notional / xaut_mid

        # Submit IOC market orders — both legs fill immediately, preventing imbalanced fills.
        # Using market orders guarantees simultaneous execution on both legs, which is critical
        # for a market-neutral strategy. GTC limit orders caused chronic one-leg fills because
        # PAXG has lower liquidity than XAUT and the limit price rarely crossed.
        paxg_order = self.order_factory.market(
            instrument_id=self.paxg_id,
            order_side=paxg_side,
            quantity=self.paxg.make_qty(paxg_qty),
            time_in_force=TimeInForce.IOC,
        )

        xaut_order = self.order_factory.market(
            instrument_id=self.xaut_id,
            order_side=xaut_side,
            quantity=self.xaut.make_qty(xaut_qty),
            time_in_force=TimeInForce.IOC,
        )

        # 提交订单
        self.submit_order(paxg_order)
        self.submit_order(xaut_order)

        self.log.info(
            f"Submitted MARKET orders for grid level={level}: "
            f"{paxg_leg_tag} qty={paxg_qty:.6f} (mid={paxg_mid:.4f}), "
            f"{xaut_leg_tag} qty={xaut_qty:.6f} (mid={xaut_mid:.4f})"
        )

        # 记录在途订单
        self.working_orders[paxg_order.client_order_id] = (level, paxg_leg_tag)
        self.working_orders[xaut_order.client_order_id] = (level, xaut_leg_tag)

        # 创建配对订单追踪器
        submit_time = self.clock.timestamp_ns()
        tracker = PairedOrderTracker(
            level=level,
            paxg_order_id=paxg_order.client_order_id,
            xaut_order_id=xaut_order.client_order_id,
            submit_time=submit_time,
            paxg_leg=paxg_leg_tag,
            xaut_leg=xaut_leg_tag,
        )
        self.paired_orders[submit_time] = tracker
        self.log.debug(f"Created paired order tracker for level={level}, submit_time={submit_time}")

        # 更新待确认名义风险（两腿）- 等待订单成交后再计入total_notional
        self.pending_notional += 2 * notional
        self.log.debug(f"Added {2*notional:.2f} to pending_notional, now pending={self.pending_notional:.2f}")

    def _close_grid(self, level: float, state: GridPositionState, force: bool = False) -> None:
        """
        Close grid position with paired order tracking.

        FIX #2: Track close orders to detect partial closes and prevent imbalance.
        Don't clear position IDs or reduce notional until both orders fill.

        Args:
            level: Grid level to close.
            state: Current GridPositionState for this level.
            force: When True, bypass the min_profit_usdt check.
                   Used for emergency closes (_close_all_grids) and close retries
                   (_check_close_order_timeouts) where the close must proceed
                   regardless of profitability.
        """
        # Check if position exists
        if state.paxg_pos_id is None and state.xaut_pos_id is None:
            self.log.debug(f"No positions to close at level={level}")
            return

        # Check if already closing this grid
        for tracker in self.paired_close_orders.values():
            if tracker.level == level:
                self.log.debug(f"Already closing grid level={level}, skipping")
                return

        # Minimum net profit check (skipped when force=True, e.g. emergency stop / retry)
        if not force and self.config.min_profit_usdt > 0:
            net_pnl = self._estimate_net_pnl(level, state)
            if net_pnl is None:
                # Could not determine P&L (position not in cache) — allow close
                self.log.debug(
                    f"Grid level={level:.4f}: P&L estimate unavailable, proceeding with close"
                )
            elif net_pnl < self.config.min_profit_usdt:
                self.log.info(
                    f"Grid level={level:.4f} ({level*100:.2f}%) close SKIPPED: "
                    f"net_pnl={net_pnl:.4f} USDT < min_profit={self.config.min_profit_usdt} USDT"
                )
                return
            else:
                self.log.info(
                    f"Grid level={level:.4f} ({level*100:.2f}%) close APPROVED: "
                    f"net_pnl={net_pnl:.4f} USDT >= min_profit={self.config.min_profit_usdt} USDT"
                )

        # Submit close orders
        paxg_order = None
        xaut_order = None

        if state.paxg_pos_id is not None:
            paxg_order = self._close_position(state.paxg_pos_id, instrument_id=self.paxg_id)
            if paxg_order is None:
                self.log.warning(f"Failed to submit PAXG close order for level={level}")

        if state.xaut_pos_id is not None:
            xaut_order = self._close_position(state.xaut_pos_id, instrument_id=self.xaut_id)
            if xaut_order is None:
                self.log.warning(f"Failed to submit XAUT close order for level={level}")

        # If no orders were submitted, clear the state
        if paxg_order is None and xaut_order is None:
            self.log.warning(f"No close orders submitted for level={level}, clearing state")
            state.paxg_pos_id = None
            state.xaut_pos_id = None
            return

        # Create close order tracker
        submit_time = self.clock.timestamp_ns()
        tracker = PairedCloseTracker(
            level=level,
            paxg_order_id=paxg_order.client_order_id if paxg_order else None,
            xaut_order_id=xaut_order.client_order_id if xaut_order else None,
            submit_time=submit_time,
            paxg_filled=paxg_order is None,  # If no order, mark as "filled"
            xaut_filled=xaut_order is None,
        )
        self.paired_close_orders[submit_time] = tracker

        self.log.info(
            f"Submitted close orders for grid level={level}: "
            f"PAXG={paxg_order.client_order_id if paxg_order else 'N/A'}, "
            f"XAUT={xaut_order.client_order_id if xaut_order else 'N/A'}"
        )

        # DON'T clear position IDs or reduce notional yet!
        # Wait for both orders to fill (handled in on_order_filled)

    def _close_all_grids(self) -> None:
        # force=True: extreme-spread emergency stop must close regardless of P&L
        for level, state in self.grid_state.items():
            self._close_grid(level, state, force=True)

    def _close_position(self, pos_id: Any, instrument_id=None) -> Optional[Any]:  # PositionId type -> Optional[Order]
        """
        Close positions with GTC LIMIT (maker) orders to earn maker rebates.

        Using limit orders for closes is safe because:
        - reduce_only=True guarantees the order only closes the existing position
        - The position is already hedged (market-neutral), so waiting for fill
          does not create directional risk
        - Maker rebate (-0.01%) vs taker fee (+0.055%) saves ~0.065% per leg,
          or ~0.13% round-trip, which exceeds the 0.10% grid step profit

        If an order fails to fill within order_timeout_sec, the timeout handler
        (_check_close_order_timeouts) cancels and resubmits at the new market price.

        Handle string markers (FILLED, DETECTED) from position sync:
        These are placeholders and don't correspond to actual PositionId objects.
        When pos_id is a string, find and close the first open position for the instrument.
        """
        # Check if pos_id is a string marker (not a real PositionId)
        if isinstance(pos_id, str):
            if instrument_id is None:
                self.log.warning(f"Cannot close placeholder position without instrument_id: {pos_id}")
                return None

            # Find first open position for this instrument
            pos = None
            for p in self.cache.positions_open():
                if p.instrument_id == instrument_id:
                    pos = p
                    break

            if pos is None:
                self.log.warning(f"No open position found for {instrument_id} (placeholder={pos_id})")
                return None

            self.log.info(f"Found open position {pos.id} for {instrument_id} (was placeholder={pos_id})")
        else:
            pos = self.cache.position(pos_id)
            if pos is None:
                self.log.warning(f"Position not found in cache: {pos_id}")
                return None

            if not pos.is_open:
                self.log.warning(f"Position already closed: {pos_id}")
                return None

        inst = pos.instrument_id
        instrument = self.cache.instrument(inst)
        if instrument is None:
            self.log.error(f"Instrument not found: {inst}")
            return None

        side = OrderSide.SELL if pos.is_long else OrderSide.BUY
        qty = pos.quantity

        # Get current bid/ask for this instrument to compute maker price
        bid, ask = self._get_bid_ask(inst)
        if bid is None or ask is None:
            # Fallback to market order if quotes unavailable
            self.log.warning(f"No quotes for {inst}, falling back to MARKET close order")
            close_order = self.order_factory.market(
                instrument_id=inst,
                order_side=side,
                quantity=instrument.make_qty(float(qty)),
                time_in_force=TimeInForce.IOC,
                reduce_only=True,
            )
            self.submit_order(close_order)
            self.log.info(f"Submitted MARKET close order for {inst}, side={side}, qty={qty}")
            return close_order

        # Limit maker price: passive inside the spread to earn maker rebate
        limit_price = self._maker_price(bid, ask, side)

        close_order = self.order_factory.limit(
            instrument_id=inst,
            order_side=side,
            quantity=instrument.make_qty(float(qty)),
            price=instrument.make_price(limit_price),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,  # Only reduce existing position, never reverse
        )
        self.submit_order(close_order)
        self.log.info(
            f"Submitted LIMIT close order for {inst}, side={side}, "
            f"qty={qty}, price={limit_price:.4f} (bid={bid:.4f}, ask={ask:.4f})"
        )

        return close_order

    # ========== Rebalance ==========
    def _rebalance_if_needed(self) -> None:
        """
        Corrects leg imbalance between PAXG and XAUT positions.

        When one leg has more notional exposure than the other (beyond
        rebalance_threshold_bps), submits a single market order to add to the
        lagging leg, bringing both legs back to equal notional.

        A 60-second cooldown prevents submitting correction orders on every tick.
        """
        # --- Guard: skip while any open-pair orders are still in flight ---
        # During the initial fill window one leg fills before the other, making the
        # portfolio look like a 100% imbalance (e.g. paxg_notional=0, xaut_notional=145).
        # Without this guard the rebalancer submits a correction market order immediately,
        # creating an unintended directional position on top of the still-pending leg.
        # We wait until ALL open pairs have settled (both legs filled or timed out).
        #
        # Also skip while any imbalance-correction or rebalancer market orders are in flight.
        # Race window: _check_order_timeouts removes a pair from paired_orders and then
        # calls _close_position(). Between that removal and the close-order fill the
        # portfolio still shows an imbalanced position but paired_orders is already empty.
        # Tracking those close orders in _rebalance_order_ids plugs this race window.
        if self.paired_orders or self.working_orders or self._rebalance_order_ids:
            self.log.debug(
                f"Rebalance skipped: {len(self.paired_orders)} open pair(s) / "
                f"{len(self.working_orders)} working order(s) / "
                f"{len(self._rebalance_order_ids)} correction order(s) still pending"
            )
            return

        # --- Measure current leg notionals via portfolio.net_exposure() ---
        # Using net_exposure() avoids double-counting when NautilusTrader holds both
        # EXTERNAL (reconciled) and internal positions simultaneously for the same instrument.
        # The sign of net_exposure tells us the direction: positive = long, negative = short.
        paxg_side: Optional[OrderSide] = None
        xaut_side: Optional[OrderSide] = None

        paxg_price = self._mid_price(self.paxg_bid, self.paxg_ask)
        xaut_price = self._mid_price(self.xaut_bid, self.xaut_ask)

        try:
            paxg_exp_raw = self.portfolio.net_exposure(self.paxg_id)
            xaut_exp_raw = self.portfolio.net_exposure(self.xaut_id)
            paxg_exp_val = float(paxg_exp_raw) if paxg_exp_raw is not None else 0.0
            xaut_exp_val = float(xaut_exp_raw) if xaut_exp_raw is not None else 0.0
        except Exception as e:
            self.log.warning(f"Rebalance: net_exposure() failed ({e}), skipping")
            return

        # Fallback: if portfolio.net_exposure() returned 0 for both legs,
        # scan cache.positions_open() directly.  Bybit external positions are
        # not always reported through the NautilusTrader portfolio API, which
        # would cause the rebalance to silently skip even when real imbalance exists.
        if paxg_exp_val == 0.0 and xaut_exp_val == 0.0:
            self.log.debug(
                "portfolio.net_exposure() returned 0 for both legs; "
                "falling back to cache.positions_open() scan"
            )
            for pos in self.cache.positions_open():
                sign = 1.0 if pos.is_long else -1.0
                if pos.instrument_id == self.paxg_id and paxg_price is not None:
                    paxg_exp_val += sign * float(pos.quantity) * paxg_price
                elif pos.instrument_id == self.xaut_id and xaut_price is not None:
                    xaut_exp_val += sign * float(pos.quantity) * xaut_price

        paxg_notional = abs(paxg_exp_val)
        xaut_notional = abs(xaut_exp_val)

        # Infer direction from exposure sign
        if paxg_exp_val > 0:
            paxg_side = OrderSide.BUY   # long PAXG
        elif paxg_exp_val < 0:
            paxg_side = OrderSide.SELL  # short PAXG
        if xaut_exp_val > 0:
            xaut_side = OrderSide.BUY   # long XAUT
        elif xaut_exp_val < 0:
            xaut_side = OrderSide.SELL  # short XAUT

        if paxg_notional == 0.0 and xaut_notional == 0.0:
            return

        delta = paxg_notional - xaut_notional
        base = max(abs(paxg_notional), abs(xaut_notional), 1.0)
        imbalance = abs(delta) / base

        if imbalance < self.config.rebalance_threshold_bps / 10_000.0:
            return

        self.log.info(
            f"Rebalancing, paxg_notional={paxg_notional:.2f}, "
            f"xaut_notional={xaut_notional:.2f}, imbalance={imbalance:.4%}"
        )

        # --- Cooldown: only act once per 60 seconds ---
        current_time = self.clock.timestamp_ns()
        if current_time - self._last_rebalance_ns < self._rebalance_cooldown_ns:
            self.log.debug("Rebalance cooldown active, skipping order submission")
            return

        # --- Identify the lagging leg and build correction order ---
        correction_notional = abs(delta)

        if paxg_notional > xaut_notional:
            # XAUT is the lagging leg: add to XAUT in the same direction as existing XAUT.
            # Infer direction from PAXG side when no XAUT position exists yet
            # (spread > 0 → SHORT PAXG / LONG XAUT → XAUT correction is BUY).
            if xaut_side is not None:
                correction_side = xaut_side
            elif paxg_side == OrderSide.SELL:
                correction_side = OrderSide.BUY   # PAXG short → XAUT should be long
            else:
                correction_side = OrderSide.SELL  # PAXG long → XAUT should be short
            if xaut_price is None:
                return
            qty = correction_notional / xaut_price
            # Pre-check BEFORE make_qty: it raises ValueError when qty rounds to zero
            if qty < float(self.xaut.size_increment):
                self.log.info(
                    f"Rebalance correction too small ({correction_notional:.2f} USDT "
                    f"→ {qty:.8f} XAUT < min {float(self.xaut.size_increment):.4f}), skipping"
                )
                self._last_rebalance_ns = current_time
                return
            order = self.order_factory.market(
                instrument_id=self.xaut_id,
                order_side=correction_side,
                quantity=self.xaut.make_qty(qty),
            )
            leg_name = "XAUT"
        else:
            # PAXG is the lagging leg: add to PAXG in the same direction as existing PAXG.
            if paxg_side is not None:
                correction_side = paxg_side
            elif xaut_side == OrderSide.BUY:
                correction_side = OrderSide.SELL  # XAUT long → PAXG should be short
            else:
                correction_side = OrderSide.BUY   # XAUT short → PAXG should be long
            if paxg_price is None:
                return
            qty = correction_notional / paxg_price
            # Pre-check BEFORE make_qty: it raises ValueError when qty rounds to zero
            if qty < float(self.paxg.size_increment):
                self.log.info(
                    f"Rebalance correction too small ({correction_notional:.2f} USDT "
                    f"→ {qty:.8f} PAXG < min {float(self.paxg.size_increment):.4f}), skipping"
                )
                self._last_rebalance_ns = current_time
                return
            order = self.order_factory.market(
                instrument_id=self.paxg_id,
                order_side=correction_side,
                quantity=self.paxg.make_qty(qty),
            )
            leg_name = "PAXG"

        # Safety guard (make_qty already validated; this handles any remaining edge cases)
        if float(order.quantity) == 0.0:
            self.log.warning(
                f"Rebalance correction too small to trade "
                f"({correction_notional:.2f} USDT rounds to 0 qty), skipping"
            )
            self._last_rebalance_ns = current_time
            return

        # Bybit minimum order notional check: actual notional = rounded_qty × current_price.
        # This catches cases where qty > size_increment but value < 5 USDT (e.g. 0.001 XAUT
        # at $2900 = $2.90 which passes the size_increment check but is rejected by Bybit).
        ref_price = xaut_price if leg_name == "XAUT" else paxg_price
        actual_order_notional = float(order.quantity) * ref_price
        if actual_order_notional < self.config.min_order_notional_usdt:
            self.log.info(
                f"Rebalance correction notional too small "
                f"({actual_order_notional:.2f} USDT < min {self.config.min_order_notional_usdt:.2f} USDT), "
                f"skipping and setting full cooldown"
            )
            self._last_rebalance_ns = current_time
            return

        self.submit_order(order)
        self._rebalance_order_ids.add(order.client_order_id)  # Track for fill/reject/cancel handling
        self._last_rebalance_ns = current_time
        self.log.info(
            f"Submitted rebalance order: {leg_name} {correction_side.name} "
            f"qty={float(order.quantity):.6f} "
            f"(correction={correction_notional:.2f} USDT, imbalance={imbalance:.4%})"
        )

    # ========== Position Reconciliation (FIX #4) ==========
    def _should_reconcile(self) -> bool:
        """Check if it's time to reconcile positions."""
        current_time = self.clock.timestamp_ns()
        elapsed = current_time - self._last_reconciliation_ns
        return elapsed >= self._reconciliation_interval_ns

    def _reconcile_positions(self) -> None:
        """
        Reconcile strategy's tracked positions with actual exchange positions.

        FIX #4: Periodic reconciliation to detect and correct position drift.
        Runs every 60 seconds to catch discrepancies from unfilled closes.
        """
        current_time = self.clock.timestamp_ns()
        self._last_reconciliation_ns = current_time

        # Get actual positions from cache
        actual_paxg_notional = self._get_actual_position_notional(self.paxg_id)
        actual_xaut_notional = self._get_actual_position_notional(self.xaut_id)
        actual_total = actual_paxg_notional + actual_xaut_notional

        # Compare with tracked notional
        tracked_total = self.total_notional
        diff = abs(actual_total - tracked_total)

        # Log reconciliation
        self.log.info(
            f"Position Reconciliation: "
            f"tracked={tracked_total:.2f}, "
            f"actual={actual_total:.2f} (PAXG={actual_paxg_notional:.2f}, XAUT={actual_xaut_notional:.2f}), "
            f"diff={diff:.2f}"
        )

        # If significant difference, update tracked notional
        if diff > 100:  # 100 USDT threshold
            self.log.warning(
                f"⚠️ POSITION DRIFT DETECTED: {diff:.2f} USDT difference! "
                f"Updating tracked notional from {tracked_total:.2f} to {actual_total:.2f}"
            )
            self.total_notional = actual_total

        # Always check per-leg imbalance, even when the total notional is not drifting.
        # A stable total with one oversized leg (e.g. PAXG=600, XAUT=300) would never
        # be caught by the diff > 100 check above, so we test independently here.
        if actual_total > 0:
            imbalance = abs(actual_paxg_notional - actual_xaut_notional) / actual_total
            if imbalance > 0.20:  # 20% imbalance
                self.log.error(
                    f"🚨 CRITICAL IMBALANCE: {imbalance*100:.2f}% "
                    f"(PAXG={actual_paxg_notional:.2f}, XAUT={actual_xaut_notional:.2f}). "
                    f"Forcing immediate rebalance correction."
                )
                # Reset the rebalance cooldown so _rebalance_if_needed() fires on
                # the very next call (it is invoked right after this method returns
                # in on_quote_tick(), so the correction order is submitted this tick).
                self._last_rebalance_ns = 0

    def _get_actual_position_notional(self, instrument_id: InstrumentId) -> float:
        """Get actual position notional from cache for a specific instrument."""
        total_notional = 0.0

        # Check all open positions
        for pos in self.cache.positions_open():
            if pos.instrument_id == instrument_id:
                # Use current mid price for valuation
                if instrument_id == self.paxg_id:
                    price = self._mid_price(self.paxg_bid, self.paxg_ask)
                else:
                    price = self._mid_price(self.xaut_bid, self.xaut_ask)

                if price is not None:
                    notional = abs(float(pos.quantity)) * price
                    total_notional += notional

        return total_notional

    # ========== 行情辅助函数 ==========
    def _has_valid_quotes(self) -> bool:
        return all([
            self.paxg_bid is not None,
            self.paxg_ask is not None,
            self.xaut_bid is not None,
            self.xaut_ask is not None,
        ])

    def _calc_spread(self) -> Optional[float]:
        # 用中间价来算
        paxg_mid = self._mid_price(self.paxg_bid, self.paxg_ask)
        xaut_mid = self._mid_price(self.xaut_bid, self.xaut_ask)
        if paxg_mid is None or xaut_mid is None:
            return None
        return (paxg_mid - xaut_mid) / xaut_mid

    @staticmethod
    def _mid_price(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _maker_price(self, bid: float, ask: float, side: OrderSide) -> float:
        """
        Compute a passive maker limit price that stays inside (or at) the bid-ask spread.

        maker_offset_bps > 0:
        - BUY  leg: mid - offset, floored at bid  → at worst equals the best bid
        - SELL leg: mid + offset, capped  at ask  → at worst equals the best ask

        The bounds are critical: the previous formula used min(ask, ...) for BUY
        and max(bid, ...) for SELL, which placed orders OUTSIDE the spread
        (BUY below bid, SELL above ask) in tight markets.  That caused the SELL
        leg to fill while the BUY leg sat unfilled indefinitely, producing a
        one-directional (short-only) position.
        """
        mid = (bid + ask) / 2.0
        offset = self.config.maker_offset_bps / 10_000.0 * mid

        if side == OrderSide.BUY:
            return max(bid, mid - offset)   # never below best bid
        else:
            return min(ask, mid + offset)   # never above best ask

    def _get_bid_ask(self, inst: InstrumentId) -> Tuple[Optional[float], Optional[float]]:
        if inst == self.paxg_id:
            return self.paxg_bid, self.paxg_ask
        else:
            return self.xaut_bid, self.xaut_ask

    # ========== 挂单超时检查 ==========
    def _check_order_timeouts(self) -> None:
        """
        Primary imbalance protection for limit open orders.

        Since opening positions now uses GTC limit orders, this method is the
        main guard against one-sided (naked) exposure:

        - Called on every quote tick via on_quote_tick().
        - After order_timeout_sec seconds have elapsed since submission,
          inspects each PairedOrderTracker for fill status:

            PAXG filled, XAUT not filled:
                Cancel XAUT limit order.
                Close PAXG position via market IOC (restore market neutrality).

            XAUT filled, PAXG not filled:
                Cancel PAXG limit order.
                Close XAUT position via market IOC (restore market neutrality).

            Neither filled (price moved away from both limit prices):
                Cancel both limit orders; release pending_notional.
                Grid level is free to re-trigger on the next tick.

        The strategy will re-attempt the grid on the next tick where
        abs_spread > level, using refreshed bid/ask prices.
        """
        if not self.paired_orders:
            return

        current_time = self.clock.timestamp_ns()
        timeout_ns = int(self.config.order_timeout_sec * 1_000_000_000)

        for submit_time, tracker in list(self.paired_orders.items()):
            elapsed_time = current_time - tracker.submit_time

            # 检查是否超时
            if elapsed_time < timeout_ns:
                continue

            # 检查是否两边都成交了
            if tracker.paxg_filled and tracker.xaut_filled:
                # 两边都成交，正常情况，清理追踪器
                del self.paired_orders[submit_time]
                self.log.debug(f"Both orders filled for level={tracker.level}, removing tracker")
                continue

            # 检查是否只有一侧成交（不平衡成交）
            notional = self._get_level_notional(tracker.level)

            if tracker.paxg_filled and not tracker.xaut_filled:
                self.log.warning(
                    f"IMBALANCED FILL DETECTED: PAXG filled but XAUT not filled for level={tracker.level}. "
                    f"Canceling XAUT order and closing PAXG position to prevent directional exposure."
                )
                # 取消未成交的 XAUT 订单
                self._safe_cancel_order(tracker.xaut_order_id)
                # 平掉已成交的 PAXG 仓位
                state = self.grid_state.get(tracker.level)
                if state and state.paxg_pos_id:
                    correction_order = self._close_position(state.paxg_pos_id)
                    if correction_order is not None:
                        # Track this close order so the rebalancer guard blocks
                        # until the market close order fills (plugs the race window
                        # between paired_orders removal and the close-order fill).
                        self._rebalance_order_ids.add(correction_order.client_order_id)
                    state.paxg_pos_id = None
                # PAXG成交了，但配对失败，需要从pending中扣除全部（因为XAUT没成交，不会进入total）
                # 注意：PAXG成交时已经在on_order_filled中等待配对，这里只需要清理pending
                self.pending_notional = max(0.0, self.pending_notional - 2 * notional)
                self.log.info(f"Cleaned up imbalanced pair, pending_notional={self.pending_notional:.2f}")
                # 清理追踪器
                del self.paired_orders[submit_time]

            elif tracker.xaut_filled and not tracker.paxg_filled:
                self.log.warning(
                    f"IMBALANCED FILL DETECTED: XAUT filled but PAXG not filled for level={tracker.level}. "
                    f"Canceling PAXG order and closing XAUT position to prevent directional exposure."
                )
                # 取消未成交的 PAXG 订单
                self._safe_cancel_order(tracker.paxg_order_id)
                # 平掉已成交的 XAUT 仓位
                state = self.grid_state.get(tracker.level)
                if state and state.xaut_pos_id:
                    correction_order = self._close_position(state.xaut_pos_id)
                    if correction_order is not None:
                        self._rebalance_order_ids.add(correction_order.client_order_id)
                    state.xaut_pos_id = None
                # XAUT成交了，但配对失败，需要从pending中扣除全部
                self.pending_notional = max(0.0, self.pending_notional - 2 * notional)
                self.log.info(f"Cleaned up imbalanced pair, pending_notional={self.pending_notional:.2f}")
                # 清理追踪器
                del self.paired_orders[submit_time]

            elif not tracker.paxg_filled and not tracker.xaut_filled:
                # 两边都没成交，只是超时了，取消两个订单并清理pending
                self.log.info(f"Both orders timed out for level={tracker.level}, canceling both")
                self._safe_cancel_order(tracker.paxg_order_id)
                self._safe_cancel_order(tracker.xaut_order_id)
                # 从pending中扣除
                self.pending_notional = max(0.0, self.pending_notional - 2 * notional)
                self.log.debug(f"Removed {2*notional:.2f} from pending_notional, now pending={self.pending_notional:.2f}")
                del self.paired_orders[submit_time]

        # Also check close order timeouts
        self._check_close_order_timeouts()

    def _check_close_order_timeouts(self) -> None:
        """
        Check for imbalanced close order fills.

        If one close order fills but the other doesn't within timeout:
        1. Log warning
        2. Re-submit the unfilled close order
        3. Update position tracking
        """
        if not self.paired_close_orders:
            return

        current_time = self.clock.timestamp_ns()
        timeout_ns = int(self.config.order_timeout_sec * 1_000_000_000)

        for submit_time, tracker in list(self.paired_close_orders.items()):
            elapsed_time = current_time - tracker.submit_time

            # Check if timeout reached
            if elapsed_time < timeout_ns:
                continue

            # Check fill status
            if tracker.paxg_filled and tracker.xaut_filled:
                # Both filled - should have been cleaned up already, but clean up just in case
                state = self.grid_state.get(tracker.level)
                if state:
                    state.paxg_pos_id = None
                    state.xaut_pos_id = None
                notional = self._get_level_notional(tracker.level)
                self.total_notional = max(0.0, self.total_notional - 2 * notional)
                del self.paired_close_orders[submit_time]
                self.log.debug(f"Cleaned up completed close tracker for level={tracker.level}")

            elif tracker.paxg_filled and not tracker.xaut_filled:
                # PAXG closed but XAUT didn't - CRITICAL imbalance!
                self.log.error(
                    f"🚨 IMBALANCED CLOSE: PAXG closed but XAUT still open at level={tracker.level}! "
                    f"Will retry closing XAUT."
                )
                # Cancel old order and retry
                if tracker.xaut_order_id:
                    self._safe_cancel_order(tracker.xaut_order_id)
                # Retry closing XAUT
                state = self.grid_state.get(tracker.level)
                if state and state.xaut_pos_id:
                    new_order = self._close_position(state.xaut_pos_id)
                    if new_order:
                        # Update tracker with new order
                        tracker.xaut_order_id = new_order.client_order_id
                        tracker.submit_time = current_time
                        self.log.info(f"Resubmitted XAUT close order: {new_order.client_order_id}")
                else:
                    # Position already gone? Clear state
                    if state:
                        state.paxg_pos_id = None
                        state.xaut_pos_id = None
                    notional = self._get_level_notional(tracker.level)
                    self.total_notional = max(0.0, self.total_notional - 2 * notional)
                    del self.paired_close_orders[submit_time]

            elif tracker.xaut_filled and not tracker.paxg_filled:
                # XAUT closed but PAXG didn't - CRITICAL imbalance!
                self.log.error(
                    f"🚨 IMBALANCED CLOSE: XAUT closed but PAXG still open at level={tracker.level}! "
                    f"Will retry closing PAXG."
                )
                # Cancel old order and retry
                if tracker.paxg_order_id:
                    self._safe_cancel_order(tracker.paxg_order_id)
                # Retry closing PAXG
                state = self.grid_state.get(tracker.level)
                if state and state.paxg_pos_id:
                    new_order = self._close_position(state.paxg_pos_id)
                    if new_order:
                        # Update tracker with new order
                        tracker.paxg_order_id = new_order.client_order_id
                        tracker.submit_time = current_time
                        self.log.info(f"Resubmitted PAXG close order: {new_order.client_order_id}")
                else:
                    # Position already gone? Clear state
                    if state:
                        state.paxg_pos_id = None
                        state.xaut_pos_id = None
                    notional = self._get_level_notional(tracker.level)
                    self.total_notional = max(0.0, self.total_notional - 2 * notional)
                    del self.paired_close_orders[submit_time]

            else:
                # Neither filled - both close orders failed
                self.log.warning(
                    f"Both close orders timed out for level={tracker.level}. "
                    f"Will retry closing both positions."
                )
                # Cancel old orders
                if tracker.paxg_order_id:
                    self._safe_cancel_order(tracker.paxg_order_id)
                if tracker.xaut_order_id:
                    self._safe_cancel_order(tracker.xaut_order_id)

                # Retry closing the grid — force=True because this close was already
                # approved once; we must not block the retry on P&L grounds.
                del self.paired_close_orders[submit_time]
                state = self.grid_state.get(tracker.level)
                if state:
                    self._close_grid(tracker.level, state, force=True)

    def _safe_cancel_order(self, order_id: Any) -> None:
        """安全地取消订单（检查订单状态）"""
        try:
            order = self.cache.order(order_id)
            if order and order.is_open:
                self.cancel_order(order)
                self.log.debug(f"Canceled order: {order_id}")
        except Exception as e:
            self.log.error(f"Error canceling order {order_id}: {e}")

    def _find_position(self, pos_id: Any, instrument_id: InstrumentId):
        """Resolve a position ID (real PositionId or string marker) to an open Position.

        String markers ('MANUAL_OVERRIDE', 'DETECTED') are produced by
        _sync_existing_positions() on restart.  For those cases we fall back to
        the first open position found for the given instrument.

        Returns None when no matching open position can be located.
        """
        if isinstance(pos_id, str):
            for pos in self.cache.positions_open():
                if pos.instrument_id == instrument_id:
                    return pos
            return None
        pos = self.cache.position(pos_id)
        return pos if pos and pos.is_open else None

    def _estimate_net_pnl(self, level: float, state: GridPositionState) -> Optional[float]:
        """Estimate the net P&L (USDT) for a grid level after all round-trip fees.

        Formula:
            gross_pnl  = Σ unrealised P&L per leg (using avg_px_open vs current mid)
            total_fees = open_notional * taker_fee_rate * 2
                         (open fees already paid  +  close fees about to be paid)
            net_pnl    = gross_pnl - total_fees

        Returns None when the P&L cannot be determined (e.g. position not in
        cache).  Callers should allow the close to proceed when None is returned
        rather than blocking it indefinitely.
        """
        paxg_mid = self._mid_price(self.paxg_bid, self.paxg_ask)
        xaut_mid = self._mid_price(self.xaut_bid, self.xaut_ask)
        if paxg_mid is None or xaut_mid is None:
            return None

        gross_pnl = 0.0
        open_notional = 0.0  # sum of entry_price × qty across both legs

        # PAXG leg
        if state.paxg_pos_id is not None:
            pos = self._find_position(state.paxg_pos_id, self.paxg_id)
            if pos is None:
                self.log.debug(f"_estimate_net_pnl: PAXG position not found for level={level}")
                return None
            entry = float(pos.avg_px_open)
            qty   = float(pos.quantity)
            gross_pnl    += (entry - paxg_mid) * qty if pos.is_short else (paxg_mid - entry) * qty
            open_notional += entry * qty

        # XAUT leg
        if state.xaut_pos_id is not None:
            pos = self._find_position(state.xaut_pos_id, self.xaut_id)
            if pos is None:
                self.log.debug(f"_estimate_net_pnl: XAUT position not found for level={level}")
                return None
            entry = float(pos.avg_px_open)
            qty   = float(pos.quantity)
            gross_pnl    += (entry - xaut_mid) * qty if pos.is_short else (xaut_mid - entry) * qty
            open_notional += entry * qty

        # Round-trip taker fees:
        #   open fees  (already paid) = open_notional * taker_fee_rate
        #   close fees (to be paid)   ≈ open_notional * taker_fee_rate  (prices move little)
        total_fees = open_notional * self.config.taker_fee_rate * 2

        return gross_pnl - total_fees


# ==========================
# 工厂函数（方便在 YAML/JSON 中引用）
# ==========================

def create_strategy(config: PaxgXautGridConfig) -> PaxgXautGridStrategy:
    return PaxgXautGridStrategy(config=config)