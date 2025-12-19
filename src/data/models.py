from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Dict, TYPE_CHECKING, Tuple
from pydantic import BaseModel

from src.tools.utils import _to_float

if TYPE_CHECKING:
    import pandas as pd

# =============================================================================
# 1. Enums (基础枚举)
# =============================================================================

# Side = Literal["buy", "sell"]
PositionSide = Literal["long", "short", "flat"]
PlanAction = Literal["OPEN", "CLOSE", "HOLD", "FLIP"]
OrderType = Literal["market", "limit"]

@dataclass
class AccountOverview:
    """账户概览（旧版本，保留用于向后兼容）"""
    raw_user_state: Dict[str, Any]  # 原始用户状态字典
    positions: List[Dict[str, Any]]  # 仓位列表（字典格式）
    open_orders: List[Dict[str, Any]]  # 挂单列表（字典格式）

class Action(str, Enum):
    """交易动作枚举"""
    STOP_ALL = "STOP_ALL"  # 禁新开仓/加仓（一般仍允许平/减/止损）
    NO_NEW_ENTRY = "NO_NEW_ENTRY"  # 禁新开仓（允许管理仓位） 但别乱砍已有仓位。
    OK = "OK"  # 正常运行


class MarketRegime(str, Enum):
    """市场体制枚举"""
    TREND = "trend"
    # 一般ADX ≥ 26 (高信噪比)
    # 状态：惯性与共识
    # 策略：趋势跟踪策略满仓/加仓，持仓容忍度高
    # 风险：在趋势末端过度贪婪 (虽有风险但统计期望仍为正)

    MIXED = "mixed"
    # 一般 ADX 19 - 26 (中信噪比)
    # 状态：混乱、摩擦、趋势的生与死
    # 描述：旧趋势正在衰竭，或新趋势试图爆发但遭遇强阻力
    # 策略：降低仓位，收紧止损，极易遭遇假突破 (Whipsaw)

    RANGE = "range"
    # ADX ≤ 17 (低信噪比)
    # 状态：噪音主导
    # 描述：价格做布朗运动，多空相互抵消
    # 策略：趋势策略严禁入场 (必死)，可转为均值回归 (Mean Reversion) 或空仓休息

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


class Slope(str, Enum):
    """
    趋势/波动率斜率方向枚举
    用于平滑斜率序列的当前方向判断
    """
    UP = "UP"  # 上升
    DOWN = "DOWN"  # 下降
    FLAT = "FLAT"  # 平坦
    UNKNOWN = "UNKNOWN"  # 未知


# =============================================================================
# 2. Market Data Structures (行情数据结构)
# =============================================================================


@dataclass(frozen=True)
class SlopeState:
    """
    斜率状态快照
    记录斜率序列的当前方向和阈值信息
    """
    state: Slope = Slope.UNKNOWN  # 斜率方向状态
    cur: Optional[float] = None  # 当前斜率值
    eps: Optional[float] = None  # 阈值（epsilon），用于判断斜率是否显著


@dataclass(frozen=True)
class TimingState:
    """
    时机状态
    用于策略决策的时机判断状态
    """
    adx_slope: SlopeState = field(default_factory=SlopeState)  # ADX 斜率状态
    bbw_slope: SlopeState = field(default_factory=SlopeState)  # 布林带宽度（BBW）斜率状态

@dataclass(frozen=True)
class OrderBookInfo:
    """
    订单簿微观结构快照 (Market Microstructure Snapshot)
    统一替代之前的 MarketMetrics
    """
    symbol: str  # 交易对符号
    best_bid: float  # 最优买价
    best_ask: float  # 最优卖价
    mid_price: float  # 中间价（(best_bid + best_ask) / 2）
    spread_bps: float  # 价差（基点，basis points）

    # 深度信息 (USDT Value)
    bid_depth_value: float  # 买盘深度价值（USDT）
    ask_depth_value: float  # 卖盘深度价值（USDT）
    imbalance: float  # 订单簿不平衡度，范围 -1.0 ~ +1.0（负值表示卖压，正值表示买压）

    timestamp: int  # 时间戳（毫秒）

    def __repr__(self):
        return (f"OrderBook({self.symbol} | Spread: {self.spread_bps:.2f} bps | "
                f"Imbalance: {self.imbalance:.2f} | "
                f"BidDepth: ${self.bid_depth_value / 1000:.1f}k | "
                f"AskDepth: ${self.ask_depth_value / 1000:.1f}k)")


@dataclass(frozen=True)
class PerpAssetInfo:
    """
    Hyperliquid 永续合约资产信息快照（元数据 + 上下文）
    由 build_perp_asset_map() 返回
    """

    # Static metadata (contract rules)
    symbol: str  # 交易对符号
    size_decimals: Optional[int] = None  # 数量精度（小数位数）
    max_leverage: Optional[int] = None  # 最大杠杆倍数
    only_isolated: bool = False  # 是否仅支持逐仓模式

    # Pricing / risk anchors
    mark_price: Decimal = Decimal("0")  # 标记价格（用于计算未实现盈亏）
    mid_price: Decimal = Decimal("0")  # 中间价（(bid + ask) / 2）
    oracle_price: Decimal = Decimal("0")  # 预言机价格
    prev_day_price: Decimal = Decimal("0")  # 前一日收盘价

    # Funding
    funding_rate: Decimal = Decimal("0")  # 资金费率
    premium: Decimal = Decimal("0")  # 溢价

    # Participation / activity
    open_interest: Decimal = Decimal("0")  # 未平仓合约量（Open Interest）
    day_notional_volume: Decimal = Decimal("0")  # 当日名义交易量

    # Microstructure / impact
    impact_bid: Decimal = Decimal("0")  # 买盘冲击成本
    impact_ask: Decimal = Decimal("0")  # 卖盘冲击成本

    # Raw ctx for debugging/backfill (exclude from repr to keep logs clean)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)  # 原始上下文数据（用于调试/回填，repr 中不显示）


@dataclass(frozen=True)
class MarketDataSnapshot:
    """
    市场数据快照 (Data Layer Output)
    """
    symbol: str  # 交易对符号
    ohlcv: dict[str, list[list[float]]]  # OHLCV 原始数据，key 为时间周期（如 "1h", "15m"），value 为 OHLCV 列表
    ohlcv_df: dict[str, "pd.DataFrame"]  # OHLCV DataFrame，key 为时间周期，value 为 pandas DataFrame

    # ✅ 修改：统一使用 OrderBookInfo
    metrics: OrderBookInfo  # 订单簿信息


# =============================================================================
# 3. Technical Signals (技术信号)
# =============================================================================

@dataclass(frozen=True)
class TrendLineSignal:
    """趋势线信号"""
    ema_50: Optional[float] = None  # 50 周期指数移动平均线
    sma_50: Optional[float] = None  # 50 周期简单移动平均线
    bias_to_ema: Optional[float] = None  # 价格相对 EMA 的偏差
    ema_gt_sma: Optional[bool] = None  # EMA 是否大于 SMA（趋势向上）
    ema_slope_5: Optional[float] = None  # EMA 的 5 周期斜率


@dataclass(frozen=True)
class MomentumSignal:
    """动量信号"""
    macd_hist: Optional[float] = None  # MACD 柱状图当前值
    macd_hist_prev: Optional[float] = None  # MACD 柱状图前一个值
    direction: int = 0  # 方向：1=向上，-1=向下，0=无方向
    strengthening: bool = False  # 动量是否在加强
    weakening: bool = False  # 动量是否在减弱


@dataclass(frozen=True)
class BreakoutSignal:
    """突破信号"""
    breakout_up: int = 0  # 向上突破次数
    breakout_down: int = 0  # 向下突破次数
    fresh_up: bool = False  # 是否刚发生向上突破
    fresh_down: bool = False  # 是否刚发生向下突破
    vol_spike_ratio: Optional[float] = None  # 成交量放大倍数


@dataclass(frozen=True)
class VolatilitySignal:
    """波动率信号"""
    bb_width: Optional[float] = None  # 布林带宽度
    p20: Optional[float] = None  # 布林带下轨（20% 分位）
    p80: Optional[float] = None  # 布林带上轨（80% 分位）
    squeeze: bool = False  # 是否处于压缩状态（低波动）
    expansion: bool = False  # 是否处于扩张状态（高波动）


@dataclass(frozen=True)
class OverheatSignal:
    """过热信号（超买超卖）"""
    rsi_14: Optional[float] = None  # 14 周期 RSI 值
    overbought: bool = False  # 是否超买（RSI > 70）
    oversold: bool = False  # 是否超卖（RSI < 30）


@dataclass(frozen=True)
class VolumeConfirmationSignal:
    """成交量确认信号"""
    obv_now: Optional[float] = None  # 当前 OBV（能量潮）值
    obv_prev5: Optional[float] = None  # 5 周期前的 OBV 值
    obv_delta_5: Optional[float] = None  # OBV 的 5 周期变化量
    direction: int = 0  # 方向：1=向上，-1=向下，0=无方向


@dataclass(frozen=True)
class StructureCostSignal:
    """结构成本信号"""
    avwap_full: Optional[float] = None  # 全局成交量加权平均价（AVWAP）
    bias_to_avwap: Optional[float] = None  # 价格相对 AVWAP 的偏差
    price_to_poc_pct: Optional[float] = None  # 价格相对 POC（成交量最大价位）的百分比


@dataclass(frozen=True)
class TechnicalLinesSnapshot:
    """技术指标快照"""
    ok: bool  # 数据是否有效
    close: Optional[float] = None  # 收盘价
    adx: Optional[float] = None  # ADX（平均趋向指标）值
    trend: TrendLineSignal = field(default_factory=TrendLineSignal)  # 趋势线信号
    momentum: MomentumSignal = field(default_factory=MomentumSignal)  # 动量信号
    breakout: BreakoutSignal = field(default_factory=BreakoutSignal)  # 突破信号
    volatility: VolatilitySignal = field(default_factory=VolatilitySignal)  # 波动率信号
    overheat: OverheatSignal = field(default_factory=OverheatSignal)  # 过热信号
    volume: VolumeConfirmationSignal = field(default_factory=VolumeConfirmationSignal)  # 成交量确认信号
    structure: StructureCostSignal = field(default_factory=StructureCostSignal)  # 结构成本信号
    notes: tuple[str, ...] = ()  # 备注信息
    debug: Optional[dict[str, Any]] = None  # 调试信息


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
    action: Action  # 交易动作（STOP_ALL/NO_NEW_ENTRY/OK）

    regime: MarketRegime
    # 当前判定出的市场体制/状态。
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

    allow_flip: bool = False
    # 是否允许反手操作。
    # True：允许在满足条件时反手（平仓并反向开仓）。
    # False：禁止反手操作。

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
    最终交易计划：由 generate_trade_plan 生成（旧版本，保留用于向后兼容）
    """
    symbol: str  # 交易对符号
    action: PlanAction  # 交易动作（OPEN/CLOSE/HOLD/FLIP）
    direction: Optional[PositionSide] = None  # 开仓方向（long/short/flat）
    close_direction: Optional[PositionSide] = None  # 平仓方向
    order_type: OrderType = "market"  # 订单类型（market/limit）
    entry_price: Optional[float] = None  # 入场价格（限价单时使用）
    open_amount: float = 0.0  # 开仓数量
    close_amount: float = 0.0  # 平仓数量
    stop_loss: Optional[float] = None  # 止损价格
    take_profit: Optional[float] = None  # 止盈价格
    reason: str = ""  # 交易原因说明
    score: float = 0.0  # 信号评分


# =============================================================================
# 5. Configs (配置)
# =============================================================================

@dataclass(frozen=True)
class StrategyConfig:
    """策略配置"""
    symbol: str  # 交易对符号
    risk_pct: float = 0.01  # 风险百分比（每次交易的风险资金占比）
    leverage: float = 5.0  # 杠杆倍数
    min_score_to_open: float = 0.35  # 开仓最低评分
    min_score_to_flip: float = 0.55  # 反手最低评分
    atr_stop_mult: float = 1.5  # 止损 ATR 倍数
    atr_tp_mult: float = 3.0  # 止盈 ATR 倍数
    cooldown_bars_1m: int = 3  # 冷却时间（1分钟 K 线数量）


@dataclass(frozen=True)
class ExecutionConfig:
    """执行配置"""
    dry_run: bool = True  # 是否模拟运行（不实际下单）
    slippage: float = 0.01  # 滑点（1%）
    post_only: bool = False  # 是否仅挂单（Post Only 模式）


class RegimeState(BaseModel):
    """市场体制状态（用于状态机）"""
    prev_base: MarketRegime = MarketRegime.UNKNOWN  # 上一个市场体制状态

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

class Side(str, Enum):
    """交易方向枚举"""
    LONG = "LONG"  # 做多
    SHORT = "SHORT"  # 做空
    # FLAT = "FLAT"  # 明确的空仓信号
    NONE = "NONE"  # 无方向 结构方向存在，但当前价格位置 / 动能 / 波动 / 环境不支持高性价比入场，因此选择观望

@dataclass
class DirectionResult:
    """方向判断结果"""
    side: Side  # 交易方向（LONG/SHORT/NONE）
    confidence: float  # 置信度，范围 0~1
    reasons: List[str]  # 判断依据列表

@dataclass
class TriggerResult:
    """触发判断结果"""
    entry_ok: bool  # 是否满足入场条件
    entry_price_hint: Optional[float]  # 建议入场价格
    strength: float  # 触发强度，范围 0~1
    is_breakout: Optional[bool]  # 是否为突破类型触发
    reasons: List[str]  # 判断依据列表

@dataclass
class ValidityResult:
    """有效性判断结果"""
    stop_price: Optional[float]  # 止损价格
    exit_ok: bool  # 是否满足退出条件
    flip_ok: bool  # 是否满足反手条件
    quality: float  # 信号质量，范围 0~1
    reasons: List[str]  # 判断依据列表


@dataclass
class SignalSnapshot:
    side: Side
    entry_ok: bool
    add_ok: bool
    exit_ok: bool
    flip_ok: bool
    entry_price_hint: Optional[float]
    stop_price: Optional[float]
    score: float
    reasons: List[str]
    ttl_seconds: int
    created_ts: float

@dataclass
class TradePlan:
    """交易计划（新版本）"""
    action: Literal["OPEN", "CLOSE", "FLIP", "NONE"]  # 交易动作
    symbol: str  # 交易对符号
    side: Optional[Side]  # 交易方向（OPEN/FLIP 时必填）
    qty: float  # 交易数量

    entry_type: Literal["MARKET", "LIMIT"]  # 入场订单类型
    entry_price: Optional[float]  # 入场价格（LIMIT 订单时使用）

    stop_price: Optional[float]  # 止损价格
    take_profit: Optional[float]  # 止盈价格

    reduce_only: bool  # 是否仅减仓（不允许开新仓）
    post_only: bool  # 是否仅挂单（Post Only 模式）

    reasons: List[str]  # 交易原因说明





@dataclass(frozen=True)
class CumFunding:
    """累计资金费率"""
    all_time: Optional[float]  # 历史累计资金费（从账户创建开始）
    since_change: Optional[float]  # 最近一次 funding 变化带来的盈亏
    since_open: Optional[float]  # 自本仓位开仓以来累计 funding

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> "CumFunding":
        d = d or {}
        return CumFunding(
            all_time=_to_float(d.get("allTime")),
            since_change=_to_float(d.get("sinceChange")),
            since_open=_to_float(d.get("sinceOpen")),
        )


@dataclass(frozen=True)
class LeverageInfo:
    """杠杆信息"""
    type: Optional[Literal["cross", "isolated"]]  # 杠杆类型：cross=全仓，isolated=逐仓
    value: Optional[float]  # 杠杆倍数

    @staticmethod
    def from_any(v: Any) -> "LeverageInfo":
        """
        Hyperliquid 有时是 {"type":"cross","value":24}
        你旧逻辑里也可能直接是数字 / 字符串
        """
        if isinstance(v, dict):
            t = v.get("type")
            t = t if t in ("cross", "isolated") else None
            return LeverageInfo(type=t, value=_to_float(v.get("value")))
        # fallback：把它当作 value
        return LeverageInfo(type=None, value=_to_float(v))


@dataclass(frozen=True)
class PerpPosition:
    """永续合约仓位信息"""

    coin: str  # 交易币种
    orders: Optional[PositionOrders]  # 关联的订单信息（TP/SL 等）

    # funding
    cum_funding: CumFunding  # 累计资金费率

    # entry / liq / margin
    entry_px: Optional[float]  # 平均开仓价
    liquidation_px: Optional[float]  # 预估爆仓价
    margin_used: Optional[float]  # 当前仓位占用的保证金（USDC）
    max_leverage: Optional[float]  # 该币种允许的最大杠杆

    # size / exposure / pnl
    szi: Optional[float]  # 仓位数量（正数=多头，负数=空头）
    position_value: Optional[float]  # 仓位名义价值（USDC）
    unrealized_pnl: Optional[float]  # 未实现盈亏
    return_on_equity: Optional[float]  # 权益回报率（ROE），注意：可能是比例不是百分比

    # leverage
    leverage: LeverageInfo  # 杠杆信息

    # 额外：保留原始 dict，方便你以后加字段（比如 markPx, pnl, etc.）
    raw: Dict[str, Any]  # 原始数据字典

    @property
    def side(self) -> Optional[Literal["long", "short"]]:
        if self.szi is None:
            return None
        if self.szi > 0:
            return "long"
        if self.szi < 0:
            return "short"
        return None

    @property
    def side_enum(self) -> Side:
        """
        策略 / 风控层使用的强类型 side
        """
        if self.szi is None or self.szi == 0:
            return Side.NONE
        if self.szi > 0:
            return Side.LONG
        return Side.SHORT

    @property
    def abs_size(self) -> Optional[float]:
        return abs(self.szi) if self.szi is not None else None

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "PerpPosition":
        coin = d.get("coin")
        coin = str(coin)

        return PerpPosition(
            coin=coin,
            cum_funding=CumFunding.from_dict(d.get("cumFunding")),
            entry_px=_to_float(d.get("entryPx")),
            liquidation_px=_to_float(d.get("liquidationPx") or d.get("liquidationPrice")),
            margin_used=_to_float(d.get("marginUsed")),
            max_leverage=_to_float(d.get("maxLeverage")),
            szi=_to_float(d.get("szi") or d.get("size") or d.get("contracts")),
            position_value=_to_float(d.get("positionValue") or d.get("notional")),
            unrealized_pnl=_to_float(d.get("unrealizedPnl") or d.get("upnl")),
            return_on_equity=_to_float(d.get("returnOnEquity") or d.get("roe") or d.get("percentage")),
            leverage=LeverageInfo.from_any(d.get("leverage")),
            raw=d,
        )


@dataclass(frozen=True)
class MarginSummary:
    """保证金汇总"""
    account_value: Optional[float]  # 账户总价值（权益，USDC）
    total_margin_used: Optional[float]  # 所有仓位占用的保证金总和（USDC）
    total_ntl_pos: Optional[float]  # 所有仓位名义价值总和（USDC）
    total_raw_usd: Optional[float]  # 原始盈亏（包含未实现 + funding，USDC）

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> "MarginSummary":
        d = d or {}
        return MarginSummary(
            account_value=_to_float(d.get("accountValue")),
            total_margin_used=_to_float(d.get("totalMarginUsed")),
            total_ntl_pos=_to_float(d.get("totalNtlPos")),
            total_raw_usd=_to_float(d.get("totalRawUsd")),
        )


@dataclass(frozen=True)
class AccountState:
    """账户状态"""
    time_ms: Optional[int]  # 服务器时间戳（毫秒）
    withdrawable: Optional[float]  # 可提余额（USDC）
    cross_maintenance_margin_used: Optional[float]  # 全仓维护保证金占用（USDC）
    cross_margin_summary: MarginSummary  # 全仓保证金汇总
    margin_summary: MarginSummary  # 保证金汇总（通常等同 cross_margin_summary）


@dataclass(frozen=True)
class AccountOverview:
    """
    fetch_account_overview 的强类型返回值
    """
    state: AccountState  # 账户状态
    positions: List[PerpPosition]  # 所有永续仓位列表
    primary_position: Optional[PerpPosition]  # 主要交易币种的仓位（如果存在）
    open_orders: List[Dict[str, Any]]  # 挂单列表（这里先保留 dict，因为订单结构更复杂/变化更多）
    raw_user_state: Dict[str, Any]  # 原始用户状态字典（用于调试/兼容）

@dataclass(frozen=True)
class TriggerOrder:
    """触发订单（条件单）"""
    coin: str  # 交易币种
    side: Optional[str]  # 原始 side: 'B'/'A' or 'buy'/'sell'
    size: float  # 订单数量
    limit_px: Optional[float]  # 限价（触发后的执行价格）
    trigger_px: Optional[float]  # 触发价格
    trigger_condition: Optional[str]  # 触发条件
    is_position_tpsl: bool  # 是否为仓位的止盈/止损单
    timestamp: Optional[int]  # 时间戳
    raw: Dict[str, Any]  # 原始数据字典


@dataclass(frozen=True)
class NormalOrder:
    """普通订单（限价/市价单）"""
    coin: str  # 交易币种
    side: Optional[str]  # 方向：'B'/'A' or 'buy'/'sell'
    size: float  # 订单数量
    limit_px: Optional[float]  # 限价（市价单时为 None）
    timestamp: Optional[int]  # 时间戳
    raw: Dict[str, Any]  # 原始数据字典

@dataclass(frozen=True)
class PositionTpsl:
    """内嵌到仓位对象里：按 entryPx + 方向归类后的 TP/SL"""
    tp: Tuple[TriggerOrder, ...]  # 止盈单列表
    sl: Tuple[TriggerOrder, ...]  # 止损单列表
    others: Tuple[TriggerOrder, ...]  # 同 coin 的触发单，但无法判定 TP/SL（缺价/缺entry等）


@dataclass(frozen=True)
class PositionOrders:
    """你也可以不要 normal，只放 tpsl；我这里给全一点便于调试"""
    tpsl: PositionTpsl  # 止盈止损订单信息
    normal: Tuple[NormalOrder, ...]  # 普通订单列表
    raw_trigger: Tuple[TriggerOrder, ...]  # 未归类前同 coin 的全部 trigger 单（方便排查）