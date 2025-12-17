from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, List, Dict, TYPE_CHECKING
from pydantic import BaseModel

if TYPE_CHECKING:
    import pandas as pd

# =============================================================================
# 1. Enums (基础枚举)
# =============================================================================

Side = Literal["buy", "sell"]
PositionSide = Literal["long", "short", "flat"]
PlanAction = Literal["OPEN", "CLOSE", "HOLD", "FLIP"]
OrderType = Literal["market", "limit"]


class Action(str, Enum):
    STOP_ALL = "STOP_ALL"  # 禁新开仓/加仓（一般仍允许平/减/止损）
    NO_NEW_ENTRY = "NO_NEW_ENTRY"  # 禁新开仓（允许管理仓位） 但别乱砍已有仓位。
    OK = "OK"  # 正常运行


class MarketRegime(str, Enum):
    TREND = "trend"  # 明确趋势
    RANGE = "range"  # 明确震荡
    MIXED = "mixed"  # 方向不稳定 / 切换中
    UNKNOWN = "unknown"  # 数据不足 / 不判断


class VolState(str, Enum):
    """
    VolState（Volatility State）— 市场「波动环境」枚举
    用于决定策略许可 / 风险缩放 / 禁交易判断
    """
    LOW = "low"  # 低波动 / 压缩期 -> 狙击模式 (Strict Entry)
    NORMAL = "normal"  # 正常波动 -> 正常模式
    HIGH = "high"  # 高波动 / 扩张期 -> 降权或停止均值回归
    UNKNOWN = "unknown"


# =============================================================================
# 2. Market Data Structures (行情数据结构)
# =============================================================================

@dataclass(frozen=True)
class OrderBookInfo:
    """
    订单簿微观结构快照 (Market Microstructure Snapshot)
    统一替代之前的 MarketMetrics
    """
    symbol: str
    best_bid: float
    best_ask: float
    mid_price: float
    spread_bps: float

    # 深度信息 (USDT Value)
    bid_depth_value: float
    ask_depth_value: float
    imbalance: float  # -1.0 ~ +1.0

    timestamp: int

    def __repr__(self):
        return (f"OrderBook({self.symbol} | Spread: {self.spread_bps:.2f} bps | "
                f"Imbalance: {self.imbalance:.2f} | "
                f"BidDepth: ${self.bid_depth_value / 1000:.1f}k | "
                f"AskDepth: ${self.ask_depth_value / 1000:.1f}k)")


@dataclass(frozen=True)
class MarketDataSnapshot:
    """
    市场数据快照 (Data Layer Output)
    """
    symbol: str
    ohlcv: dict[str, list[list[float]]]
    ohlcv_df: dict[str, "pd.DataFrame"]

    # ✅ 修改：统一使用 OrderBookInfo
    metrics: OrderBookInfo


# =============================================================================
# 3. Technical Signals (技术信号)
# =============================================================================

@dataclass(frozen=True)
class TrendLineSignal:
    ema_50: Optional[float] = None
    sma_50: Optional[float] = None
    bias_to_ema: Optional[float] = None
    ema_gt_sma: Optional[bool] = None
    ema_slope_5: Optional[float] = None


@dataclass(frozen=True)
class MomentumSignal:
    macd_hist: Optional[float] = None
    macd_hist_prev: Optional[float] = None
    direction: int = 0
    strengthening: bool = False
    weakening: bool = False


@dataclass(frozen=True)
class BreakoutSignal:
    breakout_up: int = 0
    breakout_down: int = 0
    fresh_up: bool = False
    fresh_down: bool = False
    vol_spike_ratio: Optional[float] = None


@dataclass(frozen=True)
class VolatilitySignal:
    bb_width: Optional[float] = None
    p20: Optional[float] = None
    p80: Optional[float] = None
    squeeze: bool = False
    expansion: bool = False


@dataclass(frozen=True)
class OverheatSignal:
    rsi_14: Optional[float] = None
    overbought: bool = False
    oversold: bool = False


@dataclass(frozen=True)
class VolumeConfirmationSignal:
    obv_now: Optional[float] = None
    obv_prev5: Optional[float] = None
    obv_delta_5: Optional[float] = None
    direction: int = 0


@dataclass(frozen=True)
class StructureCostSignal:
    avwap_full: Optional[float] = None
    bias_to_avwap: Optional[float] = None
    price_to_poc_pct: Optional[float] = None


@dataclass(frozen=True)
class TechnicalLinesSnapshot:
    ok: bool
    close: Optional[float] = None
    adx: Optional[float] = None
    trend: TrendLineSignal = field(default_factory=TrendLineSignal)
    momentum: MomentumSignal = field(default_factory=MomentumSignal)
    breakout: BreakoutSignal = field(default_factory=BreakoutSignal)
    volatility: VolatilitySignal = field(default_factory=VolatilitySignal)
    overheat: OverheatSignal = field(default_factory=OverheatSignal)
    volume: VolumeConfirmationSignal = field(default_factory=VolumeConfirmationSignal)
    structure: StructureCostSignal = field(default_factory=StructureCostSignal)
    notes: tuple[str, ...] = ()
    debug: Optional[dict[str, Any]] = None


# =============================================================================
# 4. Strategy & Decision (策略决策核心)
# =============================================================================

@dataclass
class Decision:
    """
    策略决策结果对象：由 decide_regime 生成
    """
    # 核心指令
    action: Action
    regime: MarketRegime

    # 权限开关
    allow_trend: bool
    allow_mean: bool

    # ✅ 模式修饰符 (新增)
    strict_entry: bool = False

    # 执行权限
    allow_new_entry: bool = True
    allow_manage: bool = True

    # 动态风控
    risk_scale: float = 1.0
    cooldown_scale: float = 1.0

    # 日志
    reasons: List[str] = field(default_factory=list)

    # 上下文快照
    adx: Optional[float] = None
    vol_state: VolState = VolState.UNKNOWN
    order_book: Optional[OrderBookInfo] = None


@dataclass(frozen=True)
class TradePlan:
    """
    最终交易计划：由 generate_trade_plan 生成
    """
    symbol: str
    action: PlanAction
    direction: Optional[PositionSide] = None
    close_direction: Optional[PositionSide] = None
    order_type: OrderType = "market"
    entry_price: Optional[float] = None
    open_amount: float = 0.0
    close_amount: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    score: float = 0.0


# =============================================================================
# 5. Configs (配置)
# =============================================================================

@dataclass(frozen=True)
class StrategyConfig:
    symbol: str = "BTC/USDC:USDC"
    risk_pct: float = 0.01
    leverage: float = 5.0
    min_score_to_open: float = 0.35
    min_score_to_flip: float = 0.55
    atr_stop_mult: float = 1.5
    atr_tp_mult: float = 3.0
    cooldown_bars_1m: int = 3


@dataclass(frozen=True)
class ExecutionConfig:
    dry_run: bool = True
    slippage: float = 0.01
    post_only: bool = False


class RegimeState(BaseModel):
    prev_base: MarketRegime = MarketRegime.UNKNOWN