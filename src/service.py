from __future__ import annotations

import os
import time
from typing import Dict

from hyperliquid.exchange import Exchange

from src.account import fetch_account_overview
from src.market_data import ohlcv_to_df, add_regime_indicators, classify_trend_range, classify_timing_state, \
    fetch_order_book_info, build_perp_asset_map
from src.models import RegimeState, Action, PerpAssetInfo, Decision
from src.strategy import classify_vol_state, decide_regime, build_signal
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

    df_1h = ohlcv_to_df(hl_candles_to_ohlcv_list(
        candles_last_n_closed(exchange.info, SYMBOL, "1h", limit=500)
    ))
    df_15m = ohlcv_to_df(hl_candles_to_ohlcv_list(
        candles_last_n_closed(exchange.info, SYMBOL, "15m", limit=500, safety_ms=30000)
    ))
    df_5m = ohlcv_to_df(hl_candles_to_ohlcv_list(
        candles_last_n_closed(exchange.info, SYMBOL, "5m", limit=500, safety_ms=30000)
    ))
    #
    # --- 2) 同一套指标，分别计算（正确） ---
    indicators_1h = add_regime_indicators(df_1h)
    indicators_15m = add_regime_indicators(df_15m)
    indicators_5m = add_regime_indicators(df_5m)

    # --- 3) 1h：环境/方向/权限 ---
    base, adx = classify_trend_range(df=indicators_1h, prev=state.prev_base)
    vol_state, vol_dbg = classify_vol_state(indicators_1h)
    timing = classify_timing_state(indicators_1h)

    order_book = fetch_order_book_info(exchange.info, SYMBOL)
    regime: Decision = decide_regime(
        base, adx, vol_state, order_book,
        timing=timing,
        max_spread_bps=MAX_SPREAD_BPS
    )
    perp_asset_map:Dict[str, PerpAssetInfo] = build_perp_asset_map(exchange, ["ETH", "BTC", "SOL"])
    asset_info = perp_asset_map.get(SYMBOL)
    if asset_info is None:
        print(f"⚠️ asset_info missing for {SYMBOL}")
        state.prev_base = base
        return

    now_ts = time.time()
    signal = build_signal(
        df_1h=indicators_1h,
        df_15m=indicators_15m,
        df_5m=indicators_5m,
        regime=regime,
        asset_info=asset_info,
        now_ts=now_ts
    )

    print("🧭 regime:", regime)
    print("🧭 regime:", signal)
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