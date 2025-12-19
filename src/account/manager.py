"""
账户管理模块
负责获取账户信息、仓位信息等
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional, List, Tuple

from src.data.models import Side, AccountOverview, PerpPosition, NormalOrder, TriggerOrder, PositionTpsl, PositionOrders
from src.tools.utils import _to_float


def _extract_trigger_price(order: Dict[str, Any]) -> Optional[float]:
    # 兼容不同字段命名
    for k in ("triggerPx", "triggerPrice", "stopPx", "stopPrice"):
        v = order.get(k)
        if v is not None:
            return _to_float(v)

    # 有的返回会把触发信息放在 trigger / orderType 里
    trig = order.get("trigger") or order.get("orderType") or {}
    if isinstance(trig, dict):
        for k in ("triggerPx", "triggerPrice", "stopPx", "stopPrice"):
            v = trig.get(k)
            if v is not None:
                return _to_float(v)

    return None
def account_total_usdc(account: AccountOverview) -> float:
    """获取账户总权益（USDC）"""
    # 优先使用强类型的 state.margin_summary.account_value
    if account.state is not None:
        if account.state.margin_summary and account.state.margin_summary.account_value is not None:
            return float(account.state.margin_summary.account_value)
    
    # 兜底：使用 raw_user_state
    us = account.raw_user_state or {}
    margin = us.get("marginSummary") or {}
    v = margin.get("accountValue")
    try:
        return float(v) if v is not None else 0.0
    except Exception:
        return 0.0



def _to_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def _is_trigger_order(o: Dict[str, Any]) -> bool:
    # 你给的规则：isTrigger 或 orderType == "Trigger"
    return bool(o.get("isTrigger", False)) or (o.get("orderType") == "Trigger")


def parse_orders(all_orders: List[Dict[str, Any]]) -> Tuple[List[NormalOrder], List[TriggerOrder]]:
    normal_orders: List[NormalOrder] = []
    trigger_orders: List[TriggerOrder] = []

    for o in all_orders or []:
        coin = str(o.get("coin") or "")
        if not coin:
            continue

        side = o.get("side") or o.get("dir")  # 'B'/'A' or 'buy'/'sell'
        sz = _to_float(o.get("sz")) or 0.0

        limit_px = _to_float(o.get("limitPx") or o.get("price"))
        trig_px = _to_float(o.get("triggerPx"))  # HL 常见字段
        trig_cond = o.get("triggerCondition")

        ts = _to_int(o.get("timestamp") or o.get("time") or o.get("t"))

        if _is_trigger_order(o):
            trigger_orders.append(
                TriggerOrder(
                    coin=coin,
                    side=str(side) if side is not None else None,
                    size=sz,
                    limit_px=limit_px,
                    trigger_px=trig_px,
                    trigger_condition=str(trig_cond) if trig_cond is not None else None,
                    is_position_tpsl=bool(o.get("isPositionTpsl", False)),
                    timestamp=ts,
                    raw=o,
                )
            )
        else:
            normal_orders.append(
                NormalOrder(
                    coin=coin,
                    side=str(side) if side is not None else None,
                    size=sz,
                    limit_px=limit_px,
                    timestamp=ts,
                    raw=o,
                )
            )

    return normal_orders, trigger_orders

def _order_ref_price(o: TriggerOrder) -> Optional[float]:
    # 优先 triggerPx；没有就 fallback limitPx
    return o.trigger_px if o.trigger_px is not None else o.limit_px


def split_tpsl_for_position(
    pos: PerpPosition,
    trigger_orders_same_coin: List[TriggerOrder],
) -> PositionTpsl:
    """
    用 entry_px + side_enum 判定：
    - LONG：ref_price > entry => TP，否则 SL
    - SHORT：ref_price < entry => TP，否则 SL
    无法判定 -> others
    """
    entry = pos.entry_px
    side = pos.side_enum

    tp: List[TriggerOrder] = []
    sl: List[TriggerOrder] = []
    others: List[TriggerOrder] = []

    for o in trigger_orders_same_coin:
        ref = _order_ref_price(o)
        if entry is None or ref is None or side not in (Side.LONG, Side.SHORT):
            others.append(o)
            continue

        if side == Side.LONG:
            (tp if ref > entry else sl).append(o)
        else:  # Side.SHORT
            (tp if ref < entry else sl).append(o)

    # 你要可读一点的话，排序一下（tp 按价从低到高，sl 按价从低到高）
    tp_sorted = tuple(sorted(tp, key=lambda x: (_order_ref_price(x) or 0.0)))
    sl_sorted = tuple(sorted(sl, key=lambda x: (_order_ref_price(x) or 0.0)))
    others_sorted = tuple(others)

    return PositionTpsl(tp=tp_sorted, sl=sl_sorted, others=others_sorted)

def embed_orders_into_positions(
    positions: List[PerpPosition],
    normal_orders: List[NormalOrder],
    trigger_orders: List[TriggerOrder],
) -> List[PerpPosition]:
    # 先按 coin 分桶
    norm_by_coin: Dict[str, List[NormalOrder]] = {}
    trig_by_coin: Dict[str, List[TriggerOrder]] = {}

    for o in normal_orders:
        norm_by_coin.setdefault(o.coin, []).append(o)

    for o in trigger_orders:
        trig_by_coin.setdefault(o.coin, []).append(o)

    enriched: List[PerpPosition] = []
    for pos in positions:
        coin = pos.coin
        coin_trigs = trig_by_coin.get(coin, [])
        coin_norms = norm_by_coin.get(coin, [])

        tpsl = split_tpsl_for_position(pos, coin_trigs)

        orders = PositionOrders(
            tpsl=tpsl,
            normal=tuple(coin_norms),
            raw_trigger=tuple(coin_trigs),
        )

        enriched.append(replace(pos, orders=orders))

    return enriched


