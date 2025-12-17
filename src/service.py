from __future__ import annotations

import os
import time

from hyperliquid.exchange import Exchange

from src.account import fetch_account_overview
from src.market_data import ohlcv_to_df, add_regime_indicators, classify_trend_range, classify_timing_state, \
    fetch_order_book_info
from src.models import RegimeState
from src.strategy import classify_vol_state, decide_regime

SYMBOL = "ETH"
DRY_RUN = True
LOOP_SLIPPAGE = 0.01
POST_ONLY = False
RISK_PCT = 0.01
LEVERAGE = 5.0

MAX_SPREAD_BPS = 2.0

TF_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

def start_trade(exchange: Exchange,state: RegimeState) -> None:
    """
    单轮运行：
    - 拉取账户 + 市场数据
    - 策略生成 TradePlan
    - 执行器（可 DRY_RUN）
    """

    account_overview = fetch_account_overview(exchange.info,os.environ.get("HL_WALLET_ADDRESS"))

    candles = candles_last_n_closed(exchange.info, SYMBOL, "1h", limit=500)
    ohlcv = hl_candles_to_ohlcv_list(candles)
    df = ohlcv_to_df(ohlcv)
    #
    indicators = add_regime_indicators(df)
    base, adx = classify_trend_range(df=indicators, prev=state.prev_base)
    #
    vol_state, vol_dbg = classify_vol_state(indicators)
    timing = classify_timing_state(indicators)
    # print(vol_dbg)
    # print(timing)
    order_book = fetch_order_book_info(exchange.info,SYMBOL)
    regime = decide_regime(base, adx, vol_state, order_book,timing=timing,max_spread_bps=MAX_SPREAD_BPS)


    # funding_info = exchange.fetch_funding_rate(SYMBOL)
    # funding_rate = funding_info.get("fundingRate")
    # interest = exchange.fetch_open_interest(SYMBOL)

    print("🧭 regime:", regime)
    state.prev_base = base
    # decide_regime();
    # plan:TradePlan = generate_trade_plan(account_overview, market_data, cfg=strategy_cfg)
    # print(plan.score)
    # execute_trade_plan(exchange, plan, cfg=exec_cfg)
def candles_last_n_closed(info, name: str, interval: str, limit: int = 500, safety_ms: int = 120_000):
    """
    safety_ms: 往前挪的安全窗，建议 60_000~120_000（1~2分钟）
    """
    now_ms = int(time.time() * 1000)
    end_ms = now_ms - safety_ms
    start_ms = end_ms - TF_MS[interval] * limit
    return info.candles_snapshot(name=name, interval=interval, startTime=start_ms, endTime=end_ms)
def hl_candles_to_ohlcv_list(candles):
    """
    转成 ccxt 兼容的 ohlcv list
    timestamp 用 candle["t"]（开盘时间）
    """
    out = []
    for x in candles:
        out.append([
            int(x["t"]),
            float(x["o"]),
            float(x["h"]),
            float(x["l"]),
            float(x["c"]),
            float(x["v"]),
        ])
    return out