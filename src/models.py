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
    作用：作为策略“大脑”的输出，封装了所有下一步操作需要的指令和参数。
    """

    # ==========================================
    # 1. 核心指令 (Core Instructions)
    # ==========================================
    action: Action
    # 最终的动作指令。
    # 例如：Action.BUY (买入), Action.SELL (卖出), Action.HOLD (观望/持仓不动)。

    regime: MarketRegime
    # 当前判定出的市场体制/状态。
    # 例如：MarketRegime.TRENDing (趋势), MarketRegime.CHOOPY (震荡), MarketRegime.CRASH (暴跌)。
    # 下游逻辑会根据这个状态选择不同的参数集（如趋势市用大止损，震荡市用小止盈）。

    # ==========================================
    # 2. 策略逻辑开关 (Logic Permissons)
    # ==========================================
    allow_trend: bool
    # 是否激活趋势策略逻辑。
    # True：允许追涨杀跌、突破交易；False：在震荡市中禁用趋势逻辑以防来回打脸。

    allow_mean: bool
    # 是否激活均值回归策略逻辑。
    # True：允许高抛低吸；False：在强趋势中禁用回归逻辑以防逆势抄底被套。

    # ==========================================
    # 3. 模式修饰符 (Modifiers)
    # ==========================================
    strict_entry: bool = False
    # ✅ 严格入场模式开关。
    # True：提高开仓门槛（例如要求更多指标共振），用于市场方向不明朗时减少误操作。
    # False：使用标准门槛，追求灵敏度。

    # ==========================================
    # 4. 执行权限控制 (Execution Control)
    # ==========================================
    allow_new_entry: bool = True
    # 全局开仓锁。
    # True：允许开启新的仓位。
    # False：禁止开新仓（即使信号触发也不执行），通常用于重大风险事件前或亏损达到上限时。

    allow_manage: bool = True
    # 仓位管理锁。
    # True：允许调整现有持仓（如移动止损、减仓）。
    # False：冻结现有持仓操作（极少使用，可能用于系统维护模式）。

    # ==========================================
    # 5. 动态风控参数 (Dynamic Risk)
    # ==========================================
    risk_scale: float = 1.0
    # 仓位大小缩放系数（Position Sizing）。
    # 1.0 = 标准仓位。
    # < 1.0 (如 0.5)：降低风险，半仓操作（常用于高波动或信号弱时）。
    # > 1.0：激进加仓（用于确定性极高时）。

    cooldown_scale: float = 1.0
    # 交易冷却时间倍率。
    # 用于调整两次交易之间的最小间隔。
    # > 1.0：延长休息时间（防止在剧烈震荡中频繁磨损手续费）。

    # ==========================================
    # 6. 可解释性与日志 (Logging)
    # ==========================================
    reasons: List[str] = field(default_factory=list)
    # 决策依据记录。
    # 存储触发该决策的文本描述，例如 ["RSI超卖", "突破布林带上轨"]。
    # 这里的 default_factory=list 是为了避免所有实例共享同一个列表。

    # ==========================================
    # 7. 上下文快照 (Context Snapshots)
    # ==========================================
    # 用于记录决策生成那一刻的市场数据，便于后续复盘分析（Review）

    adx: Optional[float] = None
    # 趋势强度指标 (Average Directional Index) 快照。
    # 用于判断当时趋势是否强劲。

    vol_state: VolState = VolState.UNKNOWN
    # 波动率状态快照。
    # 例如：Low (低波酝酿), High (高波剧烈), Extreme (极端行情)。

    order_book: Optional[OrderBookInfo] = None
    # 订单簿（盘口）快照。
    # 包含买一卖一价、挂单深度等，用于分析当时的流动性是否充足。

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