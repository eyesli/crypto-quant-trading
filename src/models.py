from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional, List, Dict
from typing import TYPE_CHECKING

from ccxt.base.types import Any, OrderBook

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
    # 多周期 K 线原始数据（ccxt fetch_ohlcv 的返回）。
    #
    # 结构：timeframe -> ohlcv list
    # - timeframe 示例："1m" / "1h" / "4h" / "1d" / "1w"
    # - ohlcv 每行结构：[timestamp_ms, open, high, low, close, volume]
    ohlcv: dict[str, list[list[float]]]

    # 多周期K线 DataFrame（已计算技术指标）
    # 为避免 models.py 强依赖 pandas，这里只在 type-checking 时导入 pd
    ohlcv_df: dict[str, "pd.DataFrame"]

    # 附加指标（ticker/资金费率/未平仓量/盘口微观结构等）
    metrics: "MarketMetrics"


if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

@dataclass(frozen=True)
class OrderBookInfo:

    # -------------------------
    # 盘口（Order Book）
    # -------------------------
    # order_book：订单簿快照（通常包含 bids/asks 的 [price, size] 列表）。
    #
    # - 用途：用于提取微观结构指标（点差、深度、不平衡）。
    # - 失效边界：盘口是“可撤单”的，容易被 spoofing（假挂单）干扰；且快行情中采样会滞后。
    order_book: OrderBook

    # -------------------------
    # microstructure（轻量）——从盘口提取的短线供需/交易成本指标
    # -------------------------
    # spread：最优卖价(best ask) - 最优买价(best bid)，单位是“价格”。
    #
    # - 经济逻辑：即时交易成本/摩擦，越大表示流动性越差、滑点风险越高。
    # - 失效边界：快市下 bid/ask 变化很快，单次采样可能失真。
    spread: Optional[float] = None

    # spread_bps：点差按基点(bps=万分之一)标准化：spread / mid * 10000。
    #
    #
    # ＜ 5 bps	极佳流动性	交易成本极低，如同在银行间市场交易。
    # 5 - 15 bps	正常良好	流动性高，是主要资产（如主流股票、主要外汇对）在正常交易时间的常见状态。
    # 20 - 50 bps	一般流动性	交易量较低的时段或小市值资产。
    # ＞ 50 bps	流动性差	交易成本高，滑点风险大，通常出现在非活跃时段或交易不活跃的小币种、冷门股票。
    # - 经济逻辑：跨价格水平可比的成本指标；常用于“点差过滤”（太大就不交易）。
    # - 失效边界：best_ask 缺失或异常时不可用。
    spread_bps: Optional[float] = None

    # order_book_bid_depth：买盘深度（你当前实现取前 N=20 档 bid 的 size 之和）。
    #
    # - 经济逻辑：买方挂单“厚不厚”。越厚通常意味着下方支撑更强、卖出冲击成本更低。
    # - 失效边界：挂单可撤销；N 的选择会影响结论；假单/刷量会污染深度。
    order_book_bid_depth: Optional[float] = None

    # order_book_ask_depth：卖盘深度（前 N=20 档 ask 的 size 之和）。
    #
    # - 经济逻辑：卖方挂单“厚不厚”。越厚通常意味着上方压制更强、买入冲击成本更高。
    # - 失效边界：同 bid_depth：可撤单、N敏感、spoofing 等。
    order_book_ask_depth: Optional[float] = None

    # order_book_imbalance：盘口不平衡度：(bid_depth - ask_depth) / (bid_depth + ask_depth)，范围约 [-1, 1]。
    #
    # - 经济逻辑：刻画短线供需倾斜：
    #   - 接近 +1：买盘明显更厚（短线偏多）；
    #   - 接近 -1：卖盘明显更厚（短线偏空）。
    # - 失效边界：spoofing/撤单会让该指标变成“假信号”；采样延迟会在快市里滞后。
    order_book_imbalance: Optional[float] = None


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

    # -------------------------
    # 基础行情（Ticker）
    # -------------------------
    # ticker：交易所返回的行情快照（dict 结构，常见字段包括 last/bid/ask/volume 等）。
    #
    # - 用途：获取当前价格（通常使用 last），以及粗略的买卖价（bid/ask）。
    # - 经济逻辑：价格用于策略决策与风控；bid/ask 与点差用于衡量交易成本与流动性。
    # - 失效边界：不同交易所字段名可能不一致；部分字段可能为 None；快市下采样有延迟。
    ticker: dict[str, Any]

    # -------------------------
    # 衍生品/资金面
    # -------------------------
    # funding_rate：永续资金费率（通常为每期费率的小数，例如 1.25e-05）。
    #
    # - 经济逻辑：资金费率≈持仓拥挤度与资金成本。
    #   - 正费率：多方付费，通常代表多头更拥挤/溢价偏高；
    #   - 负费率：空方付费，通常代表空头更拥挤/溢价偏低。
    # - 失效边界：各交易所口径不同；大事件时短期会失真；不能单独当成反向信号。
    funding_rate: Optional[float]

    # open_interest：未平仓量（交易所原样结构）。
    #
    # - 经济逻辑：OI 上升常代表杠杆参与增加；结合价格走势可判断趋势是否“有杠杆加成”。
    # - 失效边界：OI 上升不等于趋势必延续（也可能是对冲/套利结构）；口径差异大。
    open_interest: Optional[dict[str, Any]]

    # -------------------------
    # 盘口（Order Book）
    # -------------------------
    # order_book：订单簿快照（通常包含 bids/asks 的 [price, size] 列表）。
    #
    # - 用途：用于提取微观结构指标（点差、深度、不平衡）。
    # - 失效边界：盘口是“可撤单”的，容易被 spoofing（假挂单）干扰；且快行情中采样会滞后。
    order_book: OrderBook

    # -------------------------
    # microstructure（轻量）——从盘口提取的短线供需/交易成本指标
    # -------------------------
    # spread：最优卖价(best ask) - 最优买价(best bid)，单位是“价格”。
    #
    # - 经济逻辑：即时交易成本/摩擦，越大表示流动性越差、滑点风险越高。
    # - 失效边界：快市下 bid/ask 变化很快，单次采样可能失真。
    spread: Optional[float] = None

    # spread_bps：点差按基点(bps=万分之一)标准化：spread / best_ask * 10000。
    #
    # - 经济逻辑：跨价格水平可比的成本指标；常用于“点差过滤”（太大就不交易）。
    # - 失效边界：best_ask 缺失或异常时不可用。
    spread_bps: Optional[float] = None

    # order_book_bid_depth：买盘深度（你当前实现取前 N=20 档 bid 的 size 之和）。
    #
    # - 经济逻辑：买方挂单“厚不厚”。越厚通常意味着下方支撑更强、卖出冲击成本更低。
    # - 失效边界：挂单可撤销；N 的选择会影响结论；假单/刷量会污染深度。
    order_book_bid_depth: Optional[float] = None

    # order_book_ask_depth：卖盘深度（前 N=20 档 ask 的 size 之和）。
    #
    # - 经济逻辑：卖方挂单“厚不厚”。越厚通常意味着上方压制更强、买入冲击成本更高。
    # - 失效边界：同 bid_depth：可撤单、N敏感、spoofing 等。
    order_book_ask_depth: Optional[float] = None

    # order_book_imbalance：盘口不平衡度：(bid_depth - ask_depth) / (bid_depth + ask_depth)，范围约 [-1, 1]。
    #
    # - 经济逻辑：刻画短线供需倾斜：
    #   - 接近 +1：买盘明显更厚（短线偏多）；
    #   - 接近 -1：卖盘明显更厚（短线偏空）。
    # - 失效边界：spoofing/撤单会让该指标变成“假信号”；采样延迟会在快市里滞后。
    order_book_imbalance: Optional[float] = None

class Action(str, Enum):
    STOP_ALL = "STOP_ALL"         # 禁新开仓/加仓（一般仍允许平/减/止损）
    NO_NEW_ENTRY = "NO_NEW_ENTRY" # 禁新开仓（允许管理仓位） 但别乱砍已有仓位。
    OK = "OK"                     # 正常运行

@dataclass(frozen=True)
class Decision:
    action: Action
    regime: MarketRegime                 # trend/range/mixed/unknown —— 纯环境标签
    adx: Optional[float]
    vol_state: str
    order_book:OrderBookInfo

    '''
    是否允许你做“顺着价格当前方向继续下注”的交易行为
    '''
    allow_trend: bool

    ''' 是不是可以博反弹
        allow_mean=True
        | 允许类型       | 行为                 
        | -------- | ------------------ |
        | 抄底       | 价格快速下跌后在支撑位接多      
        | 摸顶       | 价格急涨后在阻力位做空        
        | BB 反向    | 碰上轨卖 / 碰下轨买        
        | VWAP 回归  | 偏离 VWAP 过大时反向      
        | RSI 超买超卖 | RSI>70 做空 / <30 做多 

        allow_mean=False
        | 禁止行为      | 原因           
        | ------- | ------------ |
        | 下跌中抄底   | 高波动时容易“越抄越低” 
        | 上涨中摸顶   | 趋势行情容易被一路打爆  
        | BB 反向   | 布林带张开时反向是送命  
        | VWAP 反向 | 强趋势中价格可能不回   
        | RSI 反向  | RSI 会长时间钝化   

    '''
    allow_mean: bool

    risk_scale: float
    cooldown_scale: float

    reasons: List[str]

class MarketRegime(str, Enum):
    TREND = "trend"       # 明确趋势
    RANGE = "range"       # 明确震荡
    MIXED = "mixed"       # 方向不稳定 / 切换中
    UNKNOWN = "unknown"   # 数据不足 / 不判断


from enum import Enum

class VolState(str, Enum):
    """
    VolState（Volatility State）— 市场「波动环境」枚举

    语义说明（非常重要）：
    - VolState 描述的是“当前市场有多吵 / 风险有多大”
    - 它不是方向判断（不代表涨跌）
    - 它不是交易信号（不直接触发买卖）
    - 它只用于【策略许可 / 风险缩放 / 禁交易判断】

    使用位置：
    - 与 MarketRegime（TREND / RANGE / MIXED）一起，
      决定 allow_trend / allow_mean / risk_scale / action
    """

    # 低波动 / 压缩期
    # 含义：
    # - 市场振幅处于自身历史低位（NATR 低）
    # - 价格结构被压缩（BB Width 收口）
    # 行为后果：
    # - 假突破多，追单成功率低
    # - 禁止趋势策略（allow_trend = False）
    # - 均值策略可做，但需更严格触发
    # - 降低仓位，延长冷却期
    LOW = "low"

    # 正常波动 / 健康环境
    # 含义：
    # - 波动水平处于历史正常区间
    # - 价格结构展开合理
    # 行为后果：
    # - 趋势策略和均值策略均可参与
    # - 使用标准仓位与冷却期
    NORMAL = "normal"

    # 高波动 / 扩张期
    # 含义：
    # - 市场振幅明显放大（NATR 高）
    # - 价格结构被快速拉开（BB Width 扩张）
    # 行为后果：
    # - 扫损和滑点风险显著上升
    # - 禁止均值回归策略（allow_mean = False）
    # - 趋势策略可做但需降仓
    # - 冷却期延长，防止频繁交易
    HIGH = "high"

    # 未知 / 不可判定
    # 含义：
    # - 数据不足（指标未预热 / K 线不足）
    # - 或数据异常（缺失 / 中断）
    # 行为后果：
    # - 视为系统信息不足
    # - 直接进入 Hard No-Trade（STOP_ALL）
    UNKNOWN = "unknown"

@dataclass
class RegimeState:
    prev_base: MarketRegime = MarketRegime.UNKNOWN
