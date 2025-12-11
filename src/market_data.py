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

from typing import Any, Optional, Dict

import pandas as pd
import pandas_ta as ta

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


def ohlcv_to_df(ohlcv: List[List[float]]) -> pd.DataFrame:
    """
    将 ccxt 返回的 ohlcv 列表转换为 pandas DataFrame：
    columns = [timestamp, open, high, low, close, volume]
    """
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df
def compute_technical_factors(df: pd.DataFrame) -> pd.DataFrame:
    """
    在 df 上追加各种技术指标列，使用 pandas_ta。
    你可以按需删减或扩展。
    """

    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    # ===== 1. 趋势与动量因子 =====
    df["sma_50"] = ta.sma(close, length=50)
    df["ema_50"] = ta.ema(close, length=50)
    df["wma_50"] = ta.wma(close, length=50)

    macd = ta.macd(close)
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    df["macd_hist"] = macd["MACDh_12_26_9"]

    df["roc_10"] = ta.roc(close, length=10)
    df["mom_10"] = ta.mom(close, length=10)
    df["rsi_14"] = ta.rsi(close, length=14)
    df["adx_14"] = ta.adx(high, low, close, length=14)["ADX_14"]

    # Breakout 简单标记：收盘价创新 N 日新高/新低
    lookback = 20
    df["n_high"] = close.rolling(lookback).max()
    df["n_low"] = close.rolling(lookback).min()
    df["breakout_up"] = (close >= df["n_high"]).astype(int)
    df["breakout_down"] = (close <= df["n_low"]).astype(int)

    # ===== 2. 均值回归因子 =====
    bbands = ta.bbands(close, length=20, std=2.0)
    df["bb_mid"] = bbands["BBM_20_2.0"]
    df["bb_upper"] = bbands["BBU_20_2.0"]
    df["bb_lower"] = bbands["BBL_20_2.0"]
    df["bb_width"] = bbands["BBB_20_2.0"]  # 同时给波动率用

    # Keltner Channel
    kelt = ta.kc(high, low, close, length=20)
    df["kc_mid"] = kelt["KCM_20_2.0"]
    df["kc_upper"] = kelt["KCU_20_2.0"]
    df["kc_lower"] = kelt["KCL_20_2.0"]

    # VWAP（通常用在 intraday，这里直接算一版）
    df["vwap"] = ta.vwap(high, low, close, vol)

    # Z-Score（价格相对滚动均值的偏离）
    mean_20 = close.rolling(20).mean()
    std_20 = close.rolling(20).std()
    df["zscore_20"] = (close - mean_20) / std_20

    # Williams %R
    df["williams_r"] = ta.willr(high, low, close, length=14)

    # RSI 也可以作为均值回归信号：高于 70/低于 30
    # 这里就复用 rsi_14，不重复建列

    # ===== 3. 波动率因子 =====
    df["atr_14"] = ta.atr(high, low, close, length=14)
    # NATR = ATR / close
    df["natr_14"] = df["atr_14"] / close

    # Historical Vol（简单用 log_return 的 std）
    log_ret = (close / close.shift(1)).apply(lambda x: math.log(x) if x > 0 else 0)
    df["hv_20"] = log_ret.rolling(20).std()

    # HV Ratio：当前 HV vs 长周期 HV
    df["hv_100"] = log_ret.rolling(100).std()
    df["hv_ratio"] = df["hv_20"] / df["hv_100"]

    # Skew / Kurtosis（滚动）
    df["ret_skew_50"] = log_ret.rolling(50).skew()
    df["ret_kurt_50"] = log_ret.rolling(50).kurt()

    # ===== 4. 价量结构因子 =====
    # Volume Spike：相对过去 N 根的倍数
    vol_ma_20 = vol.rolling(20).mean()
    df["vol_spike_ratio"] = vol / vol_ma_20

    # OBV
    df["obv"] = ta.obv(close, vol)

    # HH/HL 结构简单判断：当前高点是否超过前 N 高点
    swing_lookback = 5
    df["swing_high"] = high[(high.shift(1) < high) & (high.shift(-1) < high)]
    df["swing_low"] = low[(low.shift(1) > low) & (low.shift(-1) > low)]

    # Breakout + Volume：同时突破 + 放量
    df["breakout_up_with_vol"] = (
        (df["breakout_up"] == 1) & (df["vol_spike_ratio"] > 2.0)
    ).astype(int)

    return df

def fetch_market_data(exchange: ccxt.hyperliquid,symbol: str) -> Dict[str, Any]:
    """
    获取指定交易对的多周期（1m / 1h / 4h / 1d / 1w）K线、行情、资金费率、盘口等信息，供策略分析使用。
    """
    #
    # snapshot: Dict[str, Any] = {"symbol": symbol, "timeframe": "1h"}
    #
    # # ticker = fetch_ticker(exchange, symbol)
    # snapshot["ticker"] = ticker or {}

    timeframe_settings = {
        "1m": 500,
        "1h": 200,
        "4h": 150,
        "1d": 120,
        "1w": 104,
    }

    ohlcv_map: Dict[str, List[List[float]]] = {}

    for timeframe, limit in timeframe_settings.items():
        data = fetch_ohlcv(exchange, symbol, timeframe, limit)
        if data:
            ohlcv_map[timeframe] = data

    funding_info = exchange.fetch_funding_rate(symbol)
    funding_rate = funding_info.get("fundingRate")
    interest = exchange.fetch_open_interest(symbol)
    order_book = exchange.fetch_order_book(symbol, limit=100)


    return None
def fetch_account_overview(exchange: ccxt.hyperliquid) -> AccountOverview:
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
                    print(f"    名义价值:     {float(notional)} USDC")
                if entry_price is not None:
                    print(f"    开仓均价:     {entry_price:.2f}")

                if upnl is not None:
                    # 根据正负添加颜色 (可选)
                    print(f"    未实现盈亏:   {float(upnl)} USDC")
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
        raise
