"""
市场数据获取函数
负责获取实时价格、K线数据等市场信息
"""

import ccxt
from datetime import datetime
from typing import Optional, Dict, List


def fetch_ticker(exchange: ccxt.okx, symbol: str = "BTC/USDT") -> Optional[Dict]:
    """
    获取交易对的最新行情

    Args:
        exchange: 交易所实例
        symbol: 交易对符号，如 "BTC/USDT"

    Returns:
        dict: 行情数据，失败返回 None
    """
    try:
        print(f"\n📊 正在获取 {symbol} 行情...")
        ticker = exchange.fetch_ticker(symbol)
        if not ticker:
            print("⚠️  获取行情失败，继续尝试获取K线数据...")
        print(f"\n{'=' * 60}")
        print(f"📈 {symbol} 实时行情")
        print(f"{'=' * 60}")
        print(f"最新价格:     ${ticker['last']:,.2f}")
        print(f"24h 最高价:   ${ticker['high']:,.2f}")
        print(f"24h 最低价:   ${ticker['low']:,.2f}")
        print(f"24h 开盘价:   ${ticker['open']:,.2f}")
        print(f"24h 成交量:   {ticker['quoteVolume']:,.2f} USDT")
        print(f"24h 涨跌幅:   {ticker['percentage']:.2f}%")
        print(f"更新时间:     {datetime.fromtimestamp(ticker['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 60}\n")

        return ticker
    except ccxt.NetworkError as e:
        print(f"❌ 网络错误: {e}")
        return None
    except ccxt.ExchangeError as e:
        print(f"❌ 交易所错误: {e}")
        return None
    except Exception as e:
        print(f"❌ 获取行情失败: {e}")
        return None


def fetch_ohlcv(exchange: ccxt.okx, symbol: str = "BTC/USDT",
                timeframe: str = "1m", limit: int = 10) -> Optional[List]:
    """
    获取K线数据

    Args:
        exchange: 交易所实例
        symbol: 交易对符号
        timeframe: 时间周期，如 "1m", "5m", "1h", "1d"
        limit: 获取的K线数量

    Returns:
        list: K线数据列表，失败返回 None
    """
    try:
        print(f"\n📉 正在获取 {symbol} {timeframe} K线数据（最近 {limit} 根）...")
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

        if not ohlcv:
            print("⚠️  未获取到K线数据")
            return None

        print(f"\n{'=' * 80}")
        print(f"📊 {symbol} {timeframe} K线数据")
        print(f"{'=' * 80}")
        print(f"{'时间':<20} {'开盘':<12} {'最高':<12} {'最低':<12} {'收盘':<12} {'成交量':<15}")
        print("-" * 80)

        for candle in ohlcv:
            timestamp = datetime.fromtimestamp(candle[0] / 1000).strftime('%Y-%m-%d %H:%M:%S')
            open_price = candle[1]
            high_price = candle[2]
            low_price = candle[3]
            close_price = candle[4]
            volume = candle[5]

            print(f"{timestamp:<20} ${open_price:<11,.2f} ${high_price:<11,.2f} "
                  f"${low_price:<11,.2f} ${close_price:<11,.2f} {volume:<15,.2f}")

        print(f"{'=' * 80}\n")

        return ohlcv
    except ccxt.NetworkError as e:
        print(f"❌ 网络错误: {e}")
        return None
    except ccxt.ExchangeError as e:
        print(f"❌ 交易所错误: {e}")
        return None
    except Exception as e:
        print(f"❌ 获取K线数据失败: {e}")
        return None

