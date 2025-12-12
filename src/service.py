from __future__ import annotations

import ccxt

from src.execution import execute_trade_plan
from src.market_data import fetch_account_overview, fetch_market_data
from src.models import ExecutionConfig, StrategyConfig, MarketDataSnapshot
from src.strategy import generate_trade_plan


# =========================
# 直接硬编码配置（按你的要求）
# =========================
SYMBOL = "BTC/USDC:USDC"
DRY_RUN = True
LOOP_SLIPPAGE = 0.01
POST_ONLY = False
RISK_PCT = 0.01
LEVERAGE = 5.0


def start_trade(exchange: ccxt.hyperliquid) -> None:
    """
    单轮运行：
    - 拉取账户 + 市场数据
    - 策略生成 TradePlan
    - 执行器（可 DRY_RUN）
    """
    strategy_cfg = StrategyConfig(
        symbol=SYMBOL,
        risk_pct=RISK_PCT,
        leverage=LEVERAGE,
    )
    exec_cfg = ExecutionConfig(
        dry_run=DRY_RUN,
        slippage=LOOP_SLIPPAGE,
        post_only=POST_ONLY,
    )

    account_overview = fetch_account_overview(exchange)
    market_data:MarketDataSnapshot = fetch_market_data(exchange, SYMBOL)

    plan = generate_trade_plan(account_overview, market_data, cfg=strategy_cfg)
    print(plan.score)
    # execute_trade_plan(exchange, plan, cfg=exec_cfg)
