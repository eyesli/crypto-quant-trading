from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


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

