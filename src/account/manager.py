"""
账户管理模块
负责获取账户信息、仓位信息等
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional, List, Tuple

from src.data.models import Side, PerpPosition, NormalOrder, TriggerOrder, PositionTpsl, PositionOrders
from src.tools.utils import _to_float


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

from typing import Optional, List

def order_ref_price(o: "TriggerOrder") -> Optional[float]:
    return o.trigger_px if o.trigger_px is not None else o.limit_px

def position_stop_price(pos: "PerpPosition") -> Optional[float]:
    """从内嵌 tpsl.sl 里取当前有效止损价（最紧的那条）"""
    if pos.orders is None or pos.orders.tpsl is None:
        return None
    sl_orders = pos.orders.tpsl.sl or ()
    prices: List[float] = []
    for o in sl_orders:
        px = order_ref_price(o)
        if px is not None:
            prices.append(float(px))
    if not prices:
        return None

    if pos.side_enum == Side.LONG:
        return max(prices)   # 多单：最紧止损=最高那条
    if pos.side_enum == Side.SHORT:
        return min(prices)   # 空单：最紧止损=最低那条
    return None


