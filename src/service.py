"""
OKX 交易所数据获取服务
"""

import time
from typing import Optional

from src.exchange_manager import create_exchange
from src.market_data import fetch_ticker, fetch_ohlcv, fetch_account_overview
from src.trading import open_perp_limit_position
from src.strategy import determine_trade_plan

SYMBOL = "BTC/USDC:USDC"

REFERENCE_ADDRESS = "0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"


def reference_direction_from_address() -> Optional[str]:
    """
    获取参考地址的方向。

    这里先占位，未来可以接入链上交易记录，提取该地址最近的做多/做空方向。
    当前返回 None，表示无参考信号。
    """

    return None

def debug_market(exchange, symbol: str):
    markets = exchange.load_markets()
    market = markets.get(symbol)
    print("\n🔍 市场信息调试")
    print("symbol:      ", symbol)
    print("type:        ", market.get("type"))
    print("spot:        ", market.get("spot"))
    print("swap(永续):  ", market.get("swap"))
    print("contract:    ", market.get("contract"))
def start():


    # 创建交易所实例并初始化连接
    exchange = create_exchange()
    # SYMBOL = "BTC/USDC:USDC"  # 注意这里先改成这个
    # debug_market(exchange, SYMBOL)
    # exit()



    # 获取账户概览
    fetch_account_overview(exchange)
    # 获取实时行情
    # fetch_ticker(exchange, SYMBOL)

    # 等待一下，避免请求过快
    time.sleep(1)

    ohlcv_4h = fetch_ohlcv(exchange, SYMBOL, timeframe="4h", limit=10)
    # 等待一下
    time.sleep(1)

    ohlcv_1d = fetch_ohlcv(exchange, SYMBOL, timeframe="1d", limit=5)

    reference_direction = reference_direction_from_address()
    plan = determine_trade_plan(ohlcv_4h or [], reference_direction)
    higher_timeframe_plan = determine_trade_plan(ohlcv_1d or [], reference_direction)

    if higher_timeframe_plan.get("direction"):
        if higher_timeframe_plan["direction"] == plan.get("direction"):
            plan["reason"] += "; 5分钟级别同向确认"
        else:
            plan["reason"] += "; 5分钟级别方向相反，降低信心"

    print("\n🧭 交易计划预览")
    print(f"参考地址: {REFERENCE_ADDRESS}")
    print(f"方向: {plan['direction'] or '观望'}")
    print(f"止损: {plan['stop_loss'] or '-'}")
    print(f"止盈: {plan['take_profit'] or '-'}")
    print(f"理由: {plan['reason']}")
    #获取实时行情
    ticker = exchange.fetch_ticker(SYMBOL)
    last = ticker.get("last")
    limit_px = last
    open_perp_limit_position(
        exchange=exchange,
        symbol=SYMBOL,
        direction="LONG",
        stop_loss=88000,
        limit_price=limit_px,
        risk_pct=0.01,
        leverage=5.0,
        post_only=False,  # 想强制只做挂单，就改 True
    )

    # print("\n✅ 数据获取与策略计算完成！")


