from __future__ import annotations

import ccxt


from src.market_data import ohlcv_to_df, add_regime_indicators, \
    classify_trend_range, fetch_order_book_info
from src.models import ExecutionConfig, StrategyConfig
from src.strategy import classify_vol_state, decide_regime_with_no_trade

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

    # account_overview = fetch_account_overview(exchange)
    # market_data:MarketDataSnapshot = fetch_market_data(exchange, SYMBOL)

    data = exchange.fetch_ohlcv(SYMBOL, "1h", limit=500)
    df = ohlcv_to_df(data)
    indicators = add_regime_indicators(df)
    base, adx = classify_trend_range(indicators)
    vol_state, vol_dbg = classify_vol_state(indicators)
    print(vol_dbg)
    order_book = fetch_order_book_info(exchange,SYMBOL)
    regime = decide_regime_with_no_trade(base, adx, vol_state, order_book.spread_bps,12)
    # todo 还缺的 3 个关键点 缺一个“流动性/成交量”或“盘口稳定性”维度 Soft No-Trade 现在“过于一刀切” 你传入了 adx，但完全没用到

    # funding_info = exchange.fetch_funding_rate(SYMBOL)
    # funding_rate = funding_info.get("fundingRate")
    # interest = exchange.fetch_open_interest(SYMBOL)

    print("🧭 regime:", regime)

    # decide_regime();
    # plan:TradePlan = generate_trade_plan(account_overview, market_data, cfg=strategy_cfg)
    # print(plan.score)
    # execute_trade_plan(exchange, plan, cfg=exec_cfg)
