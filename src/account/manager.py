"""
账户管理模块
负责获取账户信息、仓位信息等
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.data.models import  Side, AccountOverview, PerpPosition


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



#
