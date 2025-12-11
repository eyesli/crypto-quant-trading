"""
市场数据获取函数
负责获取实时价格、K线数据等市场信息
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List
from typing import Optional, Dict
from typing import Dict, Any
from ccxt.base.types import Position, Balances
import ccxt


## balance = exchange.fetch_balance()
@dataclass
class AccountOverview:
    balances: Balances
    positions: List[Position]

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


def fetch_account_overview(exchange: ccxt.hyperliquid) -> Optional[AccountOverview]:
    """
    获取账户整体信息：余额 + 详细仓位信息 + 关联的止盈止损单
    """
    try:
        # 1. 获取余额
        print("\n💼 正在获取账户余额信息...")
        balances = exchange.fetch_balance()

        # 提取 USDC 余额
        total_usdc = balances.get("total", {}).get("USDC", 0)
        free_usdc = balances.get("free", {}).get("USDC", 0)
        used_usdc = balances.get("used", {}).get("USDC", 0)

        print("\n" + "=" * 60)
        print("💰 账户余额概览")
        print("=" * 60)
        print(f"总权益:      {_format_chinese_number(total_usdc)} USDC")
        print(f"可用余额:    {_format_chinese_number(free_usdc)} USDC")
        print(f"已用保证金:  {_format_chinese_number(used_usdc)} USDC")
        print("=" * 60 + "\n")

        # 2. 获取仓位
        print("📌 正在获取当前持仓、止盈止损挂单列表...")
        positions = exchange.fetch_positions()
        open_orders = exchange.fetch_open_orders()

        if not positions:
            print("⚪ 当前无任何永续仓位。\n")
        else:
            print("\n" + "=" * 80)
            print("📊 当前持仓详情 (含止盈止损状态)")
            print("=" * 80)

            for pos in positions:
                # --- 提取基础字段 ---
                symbol = pos.get("symbol")
                side = pos.get("side")  # 'long' or 'short'
                contracts = pos.get("contracts")
                notional = pos.get("notional")
                entry_price = pos.get("entryPrice")
                leverage = pos.get("leverage")
                upnl = pos.get("unrealizedPnl")
                roe = pos.get("percentage")
                liq_price = pos.get("liquidationPrice")
                margin_mode = pos.get("marginMode")

                # --- 核心逻辑：匹配止盈止损单 ---
                tp_orders = []
                sl_orders = []

                # 只有当开仓价存在时，才能判断是止盈还是止损
                if entry_price:
                    entry_price_val = float(entry_price)

                    for order in open_orders:
                        # 1. 交易对匹配
                        if order['symbol'] != symbol: continue
                        # 2. 方向相反 (多单找卖单，空单找买单)
                        expected_close_side = 'sell' if side == 'long' else 'buy'
                        if order['side'] != expected_close_side: continue

                        # 3. 获取触发价格 (优先取 triggerPrice, 其次取 price)
                        trigger_price = order.get('triggerPrice') or order.get('stopPrice')
                        check_price = trigger_price if trigger_price else order.get('price')

                        if check_price:
                            check_price = float(check_price)
                            # 4. 判断逻辑
                            if side == 'long':
                                # 做多：价格高于入场价是止盈，低于入场价是止损
                                if check_price > entry_price_val:
                                    tp_orders.append(check_price)
                                else:
                                    sl_orders.append(check_price)
                            elif side == 'short':
                                # 做空：价格低于入场价是止盈，高于入场价是止损
                                if check_price < entry_price_val:
                                    tp_orders.append(check_price)
                                else:
                                    sl_orders.append(check_price)

                # --- 打印部分 (您要求的字段全部保留) ---
                print(f"🪙  交易对:     {symbol or '-'}")
                print(f"    方向:         {side.upper() if side else '-'}--{leverage} 倍")

                if contracts is not None:
                    print(f"    仓位数量:     {float(contracts)}")
                if notional is not None:
                    print(f"    名义价值:     {_format_chinese_number(float(notional))} USDC")
                if entry_price is not None:
                    print(f"    开仓均价:     {entry_price:.2f}")

                if upnl is not None:
                    # 根据正负添加颜色 (可选)
                    print(f"    未实现盈亏:   {_format_chinese_number(float(upnl))} USDC")
                if roe is not None:
                    print(f"    收益率(ROE):  {roe:.2f}%")
                if liq_price is not None:
                    print(f"    预估强平价:   {liq_price:.2f}")
                if margin_mode is not None:
                    print(f"    保证金模式:   {margin_mode}")

                # --- 新增：打印止盈止损状态 ---
                print(f"    {'-' * 30}")  # 以此分隔线区分基础信息和挂单信息

                if tp_orders:
                    tp_str = ", ".join([f"${p:.2f}" for p in tp_orders])
                    print(f"    🎯 止盈挂单:   {tp_str}")
                else:
                    print(f"    🎯 止盈挂单:   -- 未设置 --")

                if sl_orders:
                    sl_str = ", ".join([f"${p:.2f}" for p in sl_orders])
                    print(f"    🛡️ 止损挂单:   {sl_str}")
                else:
                    print(f"    🛡️ 止损挂单:   -- 未设置 --")
            print("=" * 80 + "\n")

        return AccountOverview(balances=balances, positions=positions)

    except Exception as e:
        print(f"❌ 获取账户信息时发生未知错误: {e}")
        # import traceback; traceback.print_exc() # 调试时可打开
        return None