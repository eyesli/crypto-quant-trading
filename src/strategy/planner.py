"""
交易计划生成模块
负责将信号转换为具体的交易计划（TradePlan）
"""
from __future__ import annotations

from typing import Literal


from src.account.manager import account_total_usdc, find_position, position_to_state
from src.data.models import Decision, PerpAssetInfo, Side, SignalSnapshot, TradePlan, AccountOverview
from src.tools.utils import estimate_qty_from_notional, max_notional_by_equity, round_qty_by_decimals

OrderType = Literal["MARKET", "LIMIT"]


def signal_to_trade_plan(
    *,
    signal: SignalSnapshot,
    regime: Decision,
    account: AccountOverview,
    asset: PerpAssetInfo,
    symbol: str,
    risk_pct: float,
    leverage: float,
    post_only: bool,
    slippage: float = 0.001,
    rr: float = 1.8,   # take-profit = rr * R
) -> TradePlan:
    """
    将信号转换为交易计划
    """
    # 0) 没信号 / 环境禁止
    if not signal.entry_ok:
        return TradePlan("NONE", symbol, None, 0.0, "MARKET", None, None, None, False, post_only,
                         ["signal.entry_ok = False"])

    if not regime.allow_new_entry:
        return TradePlan(
            "NONE", symbol, None, 0.0, "MARKET",
            None, None, None, False, post_only,
            ["regime disallows new entry"]
        )

    if signal.side == Side.NONE:
        return TradePlan("NONE", symbol, None, 0.0, "MARKET", None, None, None, False, post_only,
                         ["signal.side = NONE"])

    # 1) 仓位冲突：默认不加仓、不反手（你以后再加）
    pos_raw = find_position(account, symbol)
    if pos_raw:
        pos = position_to_state(pos_raw)
        if pos.size > 0 and pos.side == signal.side:
            return TradePlan("NONE", symbol, None, 0.0, "MARKET", None, None, None, False, post_only,
                             ["existing same-side position -> skip"])

    # 2) 风险预算
    account_value = account.state.margin_summary.account_value
    risk_budget = account_value * risk_pct * regime.risk_scale

    entry_ref = signal.entry_price_hint
    stop = signal.stop_price
    if entry_ref is None or stop is None:
        return TradePlan("NONE", symbol, None, 0.0, "MARKET", None, None, None, False, post_only,
                         ["missing entry_ref or stop_price"])

    R = abs(entry_ref - stop)
    if R <= 0:
        return TradePlan("NONE", symbol, None, 0.0, "MARKET", None, None, None, False, post_only,
                         ["invalid R (entry-stop <= 0)"])

    # 3) 用风险算 qty（核心）
    raw_qty = risk_budget / R

    # 4) 再加一个"名义上限"兜底（防止风控参数异常）
    max_notional = max_notional_by_equity(account_value, leverage)
    max_qty_by_notional = estimate_qty_from_notional(max_notional, entry_ref)
    qty = min(raw_qty, max_qty_by_notional)

    # 5) 数量精度
    qty = round_qty_by_decimals(qty, int(asset.size_decimals or 0))
    if qty <= 0:
        return TradePlan("NONE", symbol, None, 0.0, "MARKET", None, None, None, False, post_only,
                         ["qty too small after rounding"])

    # 6) 下单类型：分数高 -> 市价，分数一般 -> 限价（更省滑点）
    if signal.score >= 90:
        entry_type: OrderType = "MARKET"
        entry_price = None
    else:
        entry_type: OrderType = "LIMIT"
        if signal.side == Side.LONG:
            entry_price = entry_ref * (1 - slippage)
        else:
            entry_price = entry_ref * (1 + slippage)

    # 7) 止盈：按 RR
    if signal.side == Side.LONG:
        tp = entry_ref + rr * R
    else:
        tp = entry_ref - rr * R

    return TradePlan(
        action="OPEN",
        symbol=symbol,
        side=signal.side,
        qty=qty,
        entry_type=entry_type,
        entry_price=entry_price,
        stop_price=stop,
        take_profit=tp,
        reduce_only=False,
        post_only=post_only,
        reasons=signal.reasons,
    )
