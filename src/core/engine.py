"""
交易引擎模块
负责协调数据获取、策略决策和交易执行
"""
from __future__ import annotations

import os
import time
from typing import Dict

from hyperliquid.exchange import Exchange

from src.account.account import fetch_account_overview
from src.data.fetcher import ohlcv_to_df, fetch_order_book_info, build_perp_asset_map
from src.data.indicators import compute_technical_factors
from src.data.analyzer import classify_trend_range, classify_timing_state
from src.data.models import RegimeState, PerpAssetInfo, Decision, TimingState
from src.strategy.regime import classify_vol_state, decide_regime
from src.strategy.signals import build_signal
from src.strategy.planner import signal_to_trade_plan
from src.tools.performance import measure_time
from src.tools.utils import candles_last_n_closed, hl_candles_to_ohlcv_list

SYMBOL = "ETH"
DRY_RUN = True
LOOP_SLIPPAGE = 0.01
POST_ONLY = False
RISK_PCT = 0.01
LEVERAGE = 5.0

MAX_SPREAD_BPS = 2.0


@measure_time
def start_trade(exchange: Exchange, state: RegimeState) -> None:

    account_overview = fetch_account_overview(exchange.info, os.environ.get("HL_WALLET_ADDRESS"), SYMBOL)

    df_1h = ohlcv_to_df(hl_candles_to_ohlcv_list(
        candles_last_n_closed(exchange.info, SYMBOL, "1h", limit=500)
    ))
    df_15m = ohlcv_to_df(hl_candles_to_ohlcv_list(
        candles_last_n_closed(exchange.info, SYMBOL, "15m", limit=500, safety_ms=30000)
    ))
    df_5m = ohlcv_to_df(hl_candles_to_ohlcv_list(
        candles_last_n_closed(exchange.info, SYMBOL, "5m", limit=500, safety_ms=30000)
    ))

    # 计算技术指标
    indicators_1h = compute_technical_factors(df_1h)
    indicators_15m = compute_technical_factors(df_15m)
    indicators_5m = compute_technical_factors(df_5m)

    # 1h：环境/方向/权限
    base, adx = classify_trend_range(df=indicators_1h, prev=state.prev_base)
    vol_state, vol_dbg = classify_vol_state(indicators_1h)
    timing:TimingState = classify_timing_state(indicators_1h)

    order_book = fetch_order_book_info(exchange.info, SYMBOL)
    regime: Decision = decide_regime(
        base, adx, vol_state, order_book,
        timing=timing,
        max_spread_bps=MAX_SPREAD_BPS
    )
    perp_asset_map: Dict[str, PerpAssetInfo] = build_perp_asset_map(exchange, ["ETH", "BTC", "SOL"])
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
        position=account_overview.primary_position,
        now_ts=now_ts
    )

    plan = signal_to_trade_plan(
        signal=signal,
        regime=regime,
        account=account_overview,
        asset=asset_info,
        symbol=SYMBOL,
        risk_pct=RISK_PCT,
        leverage=LEVERAGE,
        post_only=POST_ONLY,
        slippage=LOOP_SLIPPAGE,
    )

    print("🧭 plan:", plan)
    state.prev_base = base
