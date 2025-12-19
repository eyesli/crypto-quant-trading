"""
账户管理模块
负责获取账户信息、仓位信息等
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.data.models import PositionState, Side, AccountOverview


def account_total_usdc(account: AccountOverview) -> float:
    """获取账户总权益（USDC）"""
    us = getattr(account, "raw_user_state", {}) or {}
    margin = us.get("marginSummary") or {}
    v = margin.get("accountValue")
    try:
        return float(v)
    except Exception:
        return 0.0


def find_position(account: AccountOverview, symbol: str) -> Optional[Dict[str, Any]]:
    """查找指定 symbol 的仓位"""
    for p in (getattr(account, "positions", []) or []):
        coin = p.get("coin") or p.get("symbol") or p.get("asset")
        if coin == symbol:
            return p
    return None


def position_to_state(pos: Dict[str, Any]) -> PositionState:
    """将原始仓位数据转换为 PositionState"""
    szi = float(pos.get("szi") or 0.0)
    side = Side.LONG if szi > 0 else Side.SHORT
    return PositionState(
        symbol=pos.get("coin") or pos.get("symbol") or pos.get("asset"),
        side=side,
        size=abs(szi),
        entry_price=float(pos.get("entryPx") or pos.get("entryPrice") or 0.0),
        leverage=float(pos.get("leverage") or 1.0),
        stop_price=None,
    )
