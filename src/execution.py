from __future__ import annotations

from typing import Any, Dict, Optional

import ccxt

from src.models import ExecutionConfig, PositionSide, Side, TradePlan


def _close_side(direction: PositionSide) -> Side:
    # 关闭 long 要 sell；关闭 short 要 buy
    return "sell" if direction == "long" else "buy"


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def execute_trade_plan(
    exchange: ccxt.hyperliquid,
    plan: TradePlan,
    *,
    cfg: ExecutionConfig,
) -> Dict[str, Any]:
    """
    将 TradePlan 落地为实际订单。
    - 默认 dry_run：只打印，不真下单
    - OPEN/FLIP：下 entry 单，并尝试挂 reduceOnly 的止盈/止损
    """
    print("\n" + "=" * 80)
    print("🧾 TradePlan")
    print(f"symbol:      {plan.symbol}")
    print(f"action:      {plan.action}")
    print(f"direction:   {plan.direction}")
    print(f"close_dir:   {plan.close_direction}")
    print(f"order_type:  {plan.order_type}")
    print(f"entry_price: {plan.entry_price}")
    print(f"open_amount: {plan.open_amount}")
    print(f"close_amount:{plan.close_amount}")
    print(f"stop_loss:   {plan.stop_loss}")
    print(f"take_profit: {plan.take_profit}")
    print(f"score:       {plan.score:.3f}")
    print(f"reason:      {plan.reason}")
    print(f"dry_run:     {cfg.dry_run}")
    print("=" * 80)

    if plan.action == "HOLD":
        return {"status": "skipped", "reason": "HOLD"}

    if plan.action == "OPEN" and plan.open_amount <= 0:
        return {"status": "skipped", "reason": "open_amount<=0"}
    if plan.action == "CLOSE" and plan.close_amount <= 0:
        return {"status": "skipped", "reason": "close_amount<=0"}
    if plan.action == "FLIP" and (plan.close_amount <= 0 or plan.open_amount <= 0):
        return {"status": "skipped", "reason": "close_amount<=0 or open_amount<=0"}

    if cfg.dry_run:
        return {"status": "dry_run"}

    results: Dict[str, Any] = {"orders": []}

    # 1) 先处理 CLOSE / FLIP 的平仓腿（reduceOnly 市价）
    if plan.action in ("CLOSE", "FLIP"):
        close_dir = plan.direction if plan.action == "CLOSE" else plan.close_direction
        if close_dir not in ("long", "short"):
            return {"status": "error", "reason": "CLOSE requires direction; FLIP requires close_direction"}

        close = exchange.create_order(
            symbol=plan.symbol,
            type="market",
            side=_close_side(close_dir),
            amount=abs(plan.close_amount),
            price=None,
            params={
                "reduceOnly": True,
                "slippage": cfg.slippage,
            },
        )
        results["orders"].append({"close": close})
        if plan.action == "CLOSE":
            return results

    # 2) OPEN / FLIP 的开仓腿
    if plan.action in ("OPEN", "FLIP"):
        if plan.direction not in ("long", "short"):
            return {"status": "error", "reason": "OPEN/FLIP requires plan.direction"}

        entry_side: Side = "buy" if plan.direction == "long" else "sell"
        entry_params = {"postOnly": cfg.post_only}
        if plan.order_type == "market":
            entry_params["slippage"] = cfg.slippage

        entry = exchange.create_order(
            symbol=plan.symbol,
            type=plan.order_type,
            side=entry_side,
            amount=plan.open_amount,
            price=plan.entry_price,
            params=entry_params,
        )
        results["orders"].append({"entry": entry})

        # 3) 尝试挂止盈/止损（reduceOnly）
        # 注意：各交易所/ccxt 适配差异很大，这里尽量兼容：
        # - TP：用 limit reduceOnly
        # - SL：用 market + stopPrice/triggerPrice reduceOnly（如果不支持会报错）
        tp = _safe_float(plan.take_profit)
        sl = _safe_float(plan.stop_loss)
        if tp:
            tp_order = exchange.create_order(
                symbol=plan.symbol,
                type="limit",
                side=_close_side(plan.direction),
                amount=plan.open_amount,
                price=tp,
                params={"reduceOnly": True},
            )
            results["orders"].append({"take_profit": tp_order})

        if sl:
            sl_order = exchange.create_order(
                symbol=plan.symbol,
                type="market",
                side=_close_side(plan.direction),
                amount=plan.open_amount,
                price=None,
                params={
                    "reduceOnly": True,
                    # hyperliquid / ccxt 可能使用 triggerPrice 或 stopPrice
                    "triggerPrice": sl,
                    "stopPrice": sl,
                    "slippage": cfg.slippage,
                },
            )
            results["orders"].append({"stop_loss": sl_order})

    return results


