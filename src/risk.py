from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionSizing:
    amount: float
    notional: float
    max_loss: float


def calc_amount_from_risk(
    *,
    equity: float,
    risk_pct: float,
    entry_price: float,
    stop_loss: float,
    leverage: float,
    max_notional_buffer: float = 0.95,
) -> PositionSizing:
    """
    用“止损距离”做风险定仓：
      max_loss = equity * risk_pct
      amount = max_loss / abs(entry - stop)

    同时加一个“最大名义仓位”约束：
      notional <= equity * leverage * max_notional_buffer

    说明：线性合约（BTC/USDC）PnL 与 qty*价差近似线性，这个公式成立。
    """
    if equity <= 0:
        return PositionSizing(amount=0.0, notional=0.0, max_loss=0.0)

    max_loss = float(equity) * float(risk_pct)
    stop_dist = abs(float(entry_price) - float(stop_loss))
    if stop_dist <= 0 or max_loss <= 0:
        return PositionSizing(amount=0.0, notional=0.0, max_loss=max_loss)

    raw_amount = max_loss / stop_dist
    raw_notional = raw_amount * float(entry_price)

    max_notional = float(equity) * float(leverage) * float(max_notional_buffer)
    if max_notional > 0 and raw_notional > max_notional:
        capped_amount = max_notional / float(entry_price)
        return PositionSizing(amount=capped_amount, notional=max_notional, max_loss=max_loss)

    return PositionSizing(amount=raw_amount, notional=raw_notional, max_loss=max_loss)


