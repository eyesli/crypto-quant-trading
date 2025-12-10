"""
OKX 交易所数据获取服务
"""

import time
from src.exchange_manager import create_exchange
from src.market_data import fetch_ticker, fetch_ohlcv

SYMBOL = "BTC/USDT"
def start():


    # 创建交易所实例并初始化连接
    exchange = create_exchange()
    # 获取实时行情
    ticker = fetch_ticker(exchange, SYMBOL)
    print(ticker)

    # 等待一下，避免请求过快
    time.sleep(1)

    # 获取1分钟K线数据
    ohlcv_1m = fetch_ohlcv(exchange, SYMBOL, timeframe="1m", limit=5)

    # 等待一下
    time.sleep(1)

    # 获取5分钟K线数据
    ohlcv_5m = fetch_ohlcv(exchange, SYMBOL, timeframe="5m", limit=5)

    print("✅ 数据获取完成！")


