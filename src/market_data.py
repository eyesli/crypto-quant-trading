"""
市场数据获取函数
负责获取实时价格、K线数据等市场信息
"""

from datetime import datetime
from typing import List
from typing import Optional, Dict

import ccxt


## balance = exchange.fetch_balance()


def fetch_ticker(exchange: ccxt.hyperliquid, symbol: str) -> Optional[Dict]:
    """
    获取交易对的最新行情（带完整判空 + 字段保护）

    Args:
        exchange: 交易所实例
        symbol: 交易对符号，如 "BTC/USDT"

    Returns:
        dict: 行情数据，失败返回 None
    """
    try:
        print(f"\n📊 正在获取 {symbol} 行情...")
        ticker = exchange.fetch_ticker(symbol)

        # -------- 判空 --------
        if not ticker or not isinstance(ticker, dict):
            print("⚠️ 未获取到有效 ticker 数据")
            return None
        last = ticker.get("last")
        print("\n" + "=" * 60)
        print(f"📈 {symbol} 实时行情")
        print(f"最新价格:    ${last:,.2f}")
        print("=" * 60 + "\n")

        return ticker

    except ccxt.NetworkError as e:
        print(f"❌ 网络错误: {e}")
    except ccxt.ExchangeError as e:
        print(f"❌ 交易所错误: {e}")
    except Exception as e:
        print(f"❌ 获取行情失败: {e}")

    return None


def fetch_ohlcv(exchange: ccxt.hyperliquid, symbol: str, timeframe: str, limit: int) -> Optional[List]:
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

from typing import Any

def _format_chinese_number(num: float) -> str:
    """
    简单的中文数字格式化：
      12345    -> 1.23万
      12345678 -> 1234.57万
      123456789 -> 1.23亿
    用于打印余额、仓位名义价值等。
    """
    abs_num = abs(num)
    if abs_num >= 1_0000_0000:
        return f"{num / 1_0000_0000:.2f}亿"
    elif abs_num >= 10_000:
        return f"{num / 10_000:.2f}万"
    else:
        return f"{num:,.2f}"


def fetch_account_overview(exchange: ccxt.hyperliquid) -> Optional[Dict[str, Any]]:
    """
    获取账户整体信息：余额 + 仓位，并做友好的中文打印。

    返回结构大致为：
    {
        "balance_raw": <ccxt.fetch_balance() 原始数据>,
        "positions_raw": <ccxt.fetch_positions() 原始列表>,
    }
    方便后续策略模块做仓位控制。
    """
    try:
        print("\n💼 正在获取账户余额信息...")
        balance = exchange.fetch_balance()

        # Hyperliquid 永续一般是 USDC 保证金，这里优先拿 USDC，没有再退到 USDT
        total_map = balance.get("total", {}) or {}
        free_map = balance.get("free", {}) or {}
        used_map = balance.get("used", {}) or {}

        total_usdc = total_map.get("USDC") or total_map.get("USDT") or 0.0
        free_usdc = free_map.get("USDC") or free_map.get("USDT") or 0.0
        used_usdc = used_map.get("USDC") or used_map.get("USDT") or 0.0

        print("\n" + "=" * 60)
        print("💰 账户余额概览（保证金资产）")
        print("=" * 60)
        print(f"总权益:      {_format_chinese_number(total_usdc)} USDC")
        print(f"可用余额:    {_format_chinese_number(free_usdc)} USDC")
        print(f"已用保证金:  {_format_chinese_number(used_usdc)} USDC")
        print("=" * 60 + "\n")

        # ---------- 获取仓位 ----------
        print("📌 正在获取当前持仓列表...")
        try:
            positions = exchange.fetch_positions()
        except Exception as e:
            print(f"⚠️ 获取仓位失败（部分交易所未完全实现 fetch_positions）：{e}")
            positions = []

        if not positions:
            print("⚪ 当前无任何永续仓位。\n")
        else:
            print("\n" + "=" * 80)
            print("📊 当前持仓详情")
            print("=" * 80)

            for pos in positions:
                # ccxt 统一字段，可能会缺失，所以全部用 get
                symbol = pos.get("symbol")
                side = pos.get("side")              # long / short
                contracts = pos.get("contracts")    # 合约张数／数量
                notional = pos.get("notional")      # 名义价值（约等于 仓位数 * 价格）
                entry_price = pos.get("entryPrice")
                leverage = pos.get("leverage")
                upnl = pos.get("unrealizedPnl")
                roe = pos.get("percentage")         # 一般为收益率（%）
                liq_price = pos.get("liquidationPrice")
                margin_mode = pos.get("marginMode") # cross / isolated 等

                print(f"🪙 交易对:     {symbol or '-'}")
                print(f"方向:         {side or '-'}")
                if contracts is not None:
                    print(f"仓位数量:     {_format_chinese_number(float(contracts))}")
                if notional is not None:
                    print(f"名义价值:     {_format_chinese_number(float(notional))} USDC")
                if entry_price is not None:
                    print(f"开仓均价:     {entry_price:.2f}")
                if leverage is not None:
                    print(f"杠杆:         {leverage} 倍")
                if upnl is not None:
                    print(f"未实现盈亏:   {_format_chinese_number(float(upnl))} USDC")
                if roe is not None:
                    print(f"收益率(ROE):  {roe:.2f}%")
                if liq_price is not None:
                    print(f"预估强平价:   {liq_price:.2f}")
                if margin_mode is not None:
                    print(f"保证金模式:   {margin_mode}")

                print("-" * 80)

            print("=" * 80 + "\n")

        return {
            "balance_raw": balance,
            "positions_raw": positions,
        }

    except ccxt.NetworkError as e:
        print(f"❌ 网络错误（获取账户信息失败）: {e}")
    except ccxt.ExchangeError as e:
        print(f"❌ 交易所错误（获取账户信息失败）: {e}")
    except Exception as e:
        print(f"❌ 获取账户信息时发生未知错误: {e}")

    return None

