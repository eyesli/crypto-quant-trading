from __future__ import annotations

import os
from typing import Dict

from hyperliquid.exchange import Exchange

from src.account import fetch_account_overview
from src.market_data import ohlcv_to_df, add_regime_indicators, classify_trend_range, classify_timing_state, \
    fetch_order_book_info, build_perp_asset_map
from src.models import RegimeState, Action, PerpAssetInfo, Decision
from src.strategy import classify_vol_state, decide_regime
from src.tools.utils import candles_last_n_closed, hl_candles_to_ohlcv_list

SYMBOL = "ETH"
DRY_RUN = True
LOOP_SLIPPAGE = 0.01
POST_ONLY = False
RISK_PCT = 0.01
LEVERAGE = 5.0

MAX_SPREAD_BPS = 2.0

def start_trade(exchange: Exchange, state: RegimeState) -> None:
    """
    单轮运行：
    - 拉取账户 + 市场数据
    - 策略生成 TradePlan
    - 执行器（可 DRY_RUN）
    """

    account_overview = fetch_account_overview(exchange.info, os.environ.get("HL_WALLET_ADDRESS"))

    candles = candles_last_n_closed(exchange.info, SYMBOL, "1h", limit=500)
    ohlcv = hl_candles_to_ohlcv_list(candles)
    df = ohlcv_to_df(ohlcv)
    #
    indicators = add_regime_indicators(df)
    base, adx = classify_trend_range(df=indicators, prev=state.prev_base)
    #
    vol_state, vol_dbg = classify_vol_state(indicators)
    timing = classify_timing_state(indicators)

    order_book = fetch_order_book_info(exchange.info, SYMBOL)
    regime:Decision = decide_regime(base, adx, vol_state, order_book, timing=timing, max_spread_bps=MAX_SPREAD_BPS)

    perp_asset_map:Dict[str, PerpAssetInfo] = build_perp_asset_map(exchange, ["ETH", "BTC", "SOL"])

    print("🧭 regime:", regime)
    print("🧭 regime:", perp_asset_map)
    state.prev_base = base
    # decide_regime();
    # plan:TradePlan = generate_trade_plan(account_overview, market_data, cfg=strategy_cfg)
    # print(plan.score)
    # execute_trade_plan(exchange, plan, cfg=exec_cfg)
# def save_regime_state(state: RegimeState, path="regime_state.json"):
#     with open(path, "w") as f:
#         f.write(state.model_dump_json())
#
# def load_regime_state() -> RegimeState:
#     try:
#         with open("regime_state.json", "r") as f:
#             return RegimeState.model_validate_json(f.read())
#     except FileNotFoundError:
#         raise