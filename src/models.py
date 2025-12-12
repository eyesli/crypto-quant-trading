from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional
from typing import TYPE_CHECKING


Side = Literal["buy", "sell"]
PositionSide = Literal["long", "short", "flat"]
PlanAction = Literal["OPEN", "CLOSE", "HOLD", "FLIP"]
OrderType = Literal["market", "limit"]


@dataclass(frozen=True)
class TradePlan:
    """
    策略输出的“可执行计划”，执行器只负责忠实落地，不做“二次判断”。
    """

    symbol: str
    action: PlanAction

    # 方向语义：
    # - OPEN: direction = 开仓方向
    # - CLOSE: direction = 当前持仓方向（将被平掉）
    # - FLIP:  close_direction = 当前持仓方向；direction = 新开仓方向
    direction: Optional[PositionSide] = None  # "long" or "short"
    close_direction: Optional[PositionSide] = None

    order_type: OrderType = "market"
    entry_price: Optional[float] = None  # limit 单使用；market 单可为空

    # 数量语义：
    # - OPEN: open_amount
    # - CLOSE: close_amount
    # - FLIP: close_amount + open_amount
    open_amount: float = 0.0  # base 数量（例如 BTC）
    close_amount: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    # 解释给人看的
    reason: str = ""
    score: float = 0.0


@dataclass(frozen=True)
class StrategyConfig:
    symbol: str = "BTC/USDC:USDC"
    risk_pct: float = 0.01  # 单笔最大风险 = equity * risk_pct
    leverage: float = 5.0  # 只用于“最大名义仓位”约束（不直接放大 PnL 计算）
    min_score_to_open: float = 0.35
    min_score_to_flip: float = 0.55
    atr_stop_mult: float = 1.5
    atr_tp_mult: float = 3.0
    cooldown_bars_1m: int = 3  # 防止 1m 上过度频繁交易


@dataclass(frozen=True)
class ExecutionConfig:
    dry_run: bool = True
    slippage: float = 0.01  # 市价/触发单允许滑点（交易所支持时）
    post_only: bool = False


# =========================
# Technical lines (signals)
# =========================
# 说明：这些 dataclass 的目标是让“技术线分析结果”结构化、可解释、可维护。
# - analyze_technical_lines_single_tf 只负责产出这些 signals（不算总分）
# - summarize_technical_lines_to_score 负责把 signals 汇总成 score/label/regime


@dataclass(frozen=True)
class TrendLineSignal:
    """
    均线类信号（趋势线）。

    经济逻辑：
    - 均线可近似理解为一段时间的“市场共识成本/平均成交价格”。
    - 价格持续在均线上方 → 买方愿意用更高价格成交 → 趋势偏多；反之偏空。
    - EMA（指数均线）更敏感，SMA 更平滑；EMA > SMA 常被当作“近期走势强于中期平均”的趋势确认。

    失效边界：
    - 震荡市：价格频繁穿越均线，产生大量“假信号”。
    - 单边极端行情：偏离过大时追单风险上升（应由 RSI/波动线做约束）。
    """

    ema_50: Optional[float] = None
    sma_50: Optional[float] = None
    bias_to_ema: Optional[float] = None  # (close-ema)/ema，百分比偏离
    ema_gt_sma: Optional[bool] = None
    ema_slope_5: Optional[float] = None  # EMA50 近 5 根变化率（趋势“加速/衰减”）


@dataclass(frozen=True)
class MomentumSignal:
    """
    动能类信号（动能线）。

    经济逻辑：
    - MACD 柱体可粗略理解为“短周期动能 - 长周期动能”。
    - 柱体>0 代表上行动能占优；柱体<0 代表下行动能占优。
    - 柱体绝对值扩大：动能增强（趋势更可能延续）；收敛：动能衰减（趋势可能放缓/反转）。

    失效边界：
    - 高频噪声周期：在 1m 这种极短周期，MACD 很容易被噪声打穿。
    - 大事件跳空/插针：柱体会滞后，且可能给出“追高追低”的危险信号。
    """

    macd_hist: Optional[float] = None
    macd_hist_prev: Optional[float] = None
    direction: int = 0  # 1=偏多, -1=偏空, 0=中性
    strengthening: bool = False
    weakening: bool = False


@dataclass(frozen=True)
class BreakoutSignal:
    """
    突破类信号（突破线）。

    经济逻辑：
    - 突破常对应“供需失衡被激活”，会触发止损/追单，形成惯性。
    - “新鲜度”很重要：上一根没突破、这一根突破，才是“新事件”；
      如果已经突破很多根，再把它当突破信号会重复计入。
    - 放量（vol_spike_ratio）用来确认突破质量：低量突破更像诱多/诱空。

    失效边界：
    - 假突破：流动性差/拉盘砸盘/扫止损会制造大量假突破。
    - 刷量：成交量被机器人/刷量污染时，放量确认会失真。
    """

    breakout_up: int = 0
    breakout_down: int = 0
    fresh_up: bool = False
    fresh_down: bool = False
    vol_spike_ratio: Optional[float] = None


@dataclass(frozen=True)
class VolatilitySignal:
    """
    波动率/波动状态信号（波动线）。

    经济逻辑：
    - 布林带宽度反映波动扩张/收缩：
      - 挤压（低分位）阶段更容易来回震荡（假信号更多），适合等待方向确认；
      - 扩张（高分位）阶段更容易顺势走一段（趋势策略更友好）。

    失效边界：
    - 波动结构突变（宏观事件/消息面）会让分位判断短暂失真。
    - 不同币种/不同周期的分位分布差异很大，必须用滚动分位自适应。
    """

    bb_width: Optional[float] = None
    p20: Optional[float] = None
    p80: Optional[float] = None
    squeeze: bool = False
    expansion: bool = False


@dataclass(frozen=True)
class OverheatSignal:
    """
    过热/超买超卖信号（风险线）。

    经济逻辑：
    - RSI 极端通常代表“单边情绪过度”，追单风险上升。
    - 在强趋势里 RSI 可以长期高/低，所以 RSI 适合做“追单风险矫正”，
      而不是做“必然反转”的强结论。

    失效边界：
    - 强趋势：超买并不代表立刻下跌；超卖并不代表立刻上涨。
    - 低流动性：RSI 会被少数成交影响，极端值更常见。
    """

    rsi_14: Optional[float] = None
    overbought: bool = False
    oversold: bool = False


@dataclass(frozen=True)
class VolumeConfirmationSignal:
    """
    价量确认信号（确认线）。

    经济逻辑：
    - OBV 把涨跌方向与成交量合成一个“净量”指标，试图刻画资金偏向。
    - OBV 上行≈净买盘占优；OBV 下行≈净卖盘占优（非常粗糙，仅作确认项）。

    失效边界：
    - OBV 本质仍是价格派生指标，对“刷量/机器人交易”非常敏感。
    - 在剧烈波动/插针行情，OBV 会被单根K线扭曲。
    """

    obv_now: Optional[float] = None
    obv_prev5: Optional[float] = None
    obv_delta_5: Optional[float] = None
    direction: int = 0  # 1=偏多, -1=偏空, 0=中性


@dataclass(frozen=True)
class StructureCostSignal:
    """
    成本/筹码结构信号（结构线）。

    经济逻辑：
    - AVWAP（锚定成交量加权价格）可近似看作“这段时间的成交成本线”。
      价格长期在 AVWAP 上方，说明买方愿意在高于主力成本的位置成交。
    - POC（成交量密集区）代表筹码集中区域，价格远离 POC 时追单风险更高，
      因为回归“筹码密集区”的吸引力会变大（但不代表必然回归）。

    失效边界：
    - 你这里的 POC 是“整段数据的简化版 volume profile”，对窗口选择敏感。
    - 在趋势强扩张阶段，价格可以长期远离 POC，不应机械做反向交易。
    """

    avwap_full: Optional[float] = None
    bias_to_avwap: Optional[float] = None  # (close-avwap)/avwap
    price_to_poc_pct: Optional[float] = None


@dataclass(frozen=True)
class TechnicalLinesSnapshot:
    """
    单周期“技术线分析快照”（不含总分）。

    字段说明：
    - ok/notes：用于容错与可解释输出
    - close：本周期最新收盘价（很多信号最终都围绕它解释）
    - adx：趋势强度（也用于决定 regime）
    - trend/momentum/breakout/volatility/overheat/volume/structure：各条技术线的结构化结果
    """

    ok: bool
    close: Optional[float] = None
    adx: Optional[float] = None

    trend: TrendLineSignal = TrendLineSignal()
    momentum: MomentumSignal = MomentumSignal()
    breakout: BreakoutSignal = BreakoutSignal()
    volatility: VolatilitySignal = VolatilitySignal()
    overheat: OverheatSignal = OverheatSignal()
    volume: VolumeConfirmationSignal = VolumeConfirmationSignal()
    structure: StructureCostSignal = StructureCostSignal()

    # 人类可读解释（不含总分）
    notes: tuple[str, ...] = ()

    # 预留扩展：如果你后续想把原始 row/prev 也保留下来做 debug
    debug: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class MarketDataSnapshot:
    """
    市场数据快照（fetch_market_data 的返回值对象）。

    设计目标：
    - 让“数据获取层”的输出结构稳定、强类型、可复用
    - 避免业务层到处写 magic string：market_data["timeframes"]["ohlcv_df"] 之类
    """

    symbol: str
    # 多周期K线原始数据（ccxt fetch_ohlcv 的返回）
    ohlcv: dict[str, list[list[float]]]

    # 多周期K线 DataFrame（已计算技术指标）
    # 为避免 models.py 强依赖 pandas，这里只在 type-checking 时导入 pd
    ohlcv_df: dict[str, "pd.DataFrame"]

    metrics: "MarketMetrics"


if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd


@dataclass(frozen=True)
class MarketMetrics:
    """
    市场附加指标（fetch_market_data 里除K线以外的一切“杂项指标”）。

    设计目标：
    - 替换原先的 metrics dict（避免到处用 metrics["xxx"]）
    - 字段可为空（不同交易所/不同品种可能拿不到某些数据）

    备注：
    - ticker/open_interest/order_book 这些结构各交易所差异较大，这里用 dict[str, Any] 保持兼容。
    """

    ticker: dict[str, Any]
    funding_rate: Optional[float] = None
    open_interest: Optional[dict[str, Any]] = None
    order_book: Optional[dict[str, Any]] = None

    # microstructure（轻量）
    spread: Optional[float] = None
    spread_bps: Optional[float] = None
    order_book_bid_depth: Optional[float] = None
    order_book_ask_depth: Optional[float] = None
    order_book_imbalance: Optional[float] = None

