"""
市场数据获取函数
负责获取实时价格、K线数据等市场信息

性能说明：
- 多周期 OHLCV 拉取是典型网络 I/O，可用线程池并发加速。
- 但并发也可能触发限频或暴露交易所适配的“线程不安全”问题，默认使用小并发。
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List
from ccxt.base.types import Position, Balances
import ccxt

from typing import Any, Optional, Dict

import math
import pandas as pd
import pandas_ta as ta

from concurrent.futures import ThreadPoolExecutor, as_completed

from src.config import OHLCV_FETCH_MAX_WORKERS, TIMEFRAME_SETTINGS
from src.models import MarketDataSnapshot, MarketMetrics

@dataclass
class AccountOverview:
    balances: Balances
    positions: List[Position]


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

    df["bb_mid"] = bbands["BBM_20_2.0_2.0"]
    df["bb_upper"] = bbands["BBU_20_2.0_2.0"]
    df["bb_lower"] = bbands["BBL_20_2.0_2.0"]
    df["bb_width"] = bbands["BBB_20_2.0_2.0"]   # 带宽，可用于波动率指标
    df["bb_percent"] = bbands["BBP_20_2.0_2.0"] # 价格在布林带中的百分位

    # Keltner Channel
    kelt = ta.kc(high, low, close, length=20)
    df["kc_mid"] = kelt["KCBe_20_2"]
    df["kc_upper"] = kelt["KCUe_20_2"]
    df["kc_lower"] = kelt["KCLe_20_2"]

    # VWAP（通常用在 intraday，这里直接算一版）
    df["vwap"] = ta.vwap(high, low, close, vol)

    # ---- AVWAP：从整段数据起点锚定的成交量加权成本线 ----
    cum_pv = (close * vol).cumsum()
    cum_vol = vol.cumsum()
    df["avwap_full"] = cum_pv / cum_vol   # 越靠后越稳定，可看作“大资金平均成本”

    # Z-Score（价格相对滚动均值的偏离）
    mean_20 = close.rolling(20).mean()
    std_20 = close.rolling(20).std()
    df["zscore_20"] = (close - mean_20) / std_20

    # Williams %R
    df["williams_r"] = ta.willr(high, low, close, length=14)

    # ===== 3. 波动率因子 =====
    # atr_mean = df["atr_14"].rolling(20).mean().iloc[-1]
    # atr_now = df["atr_14"].iloc[-1]
    #
    # if atr_now > atr_mean:
    #     print("ATR 高于平均 → 当前波动偏强")
    # else:
    #     print("ATR 低于平均 → 当前波动偏弱")
    #

    df["atr_14"] = ta.atr(high, low, close, length=14)
    # NATR = ATR / close
    #
    # natr = df["atr_14"] / df["close"]  # 标准化后波动率更真实
    # natr_now = natr.iloc[-1]
    # natr_ma = natr.rolling(100).mean().iloc[-1]
    #
    # if natr_now < natr_ma * 0.6:
    #     print("波动率明显压缩（squeeze），可能要爆发趋势")

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

    # ---- Volume Profile + POC（简单整段版）----
    # 1) 选择价格范围
    price_min = close.min()
    price_max = close.max()
    if price_max > price_min:
        bins = 30  # 划分 30 档价格区间，你可以按需要改
        bin_size = (price_max - price_min) / bins

        # 每一根K线属于哪个价格档
        bin_index = ((close - price_min) / bin_size).astype(int).clip(0, bins - 1)

        # 2) 统计每个价格档的累计成交量
        vol_profile = vol.groupby(bin_index).sum()

        # 3) 找出成交量最多的那个档位 = POC
        poc_bin = vol_profile.idxmax()
        poc_price = float(price_min + (poc_bin + 0.5) * bin_size)  # 档位中点价格

        df["poc_full"] = poc_price
        df["price_to_poc_pct"] = (close - poc_price) / poc_price
    else:
        # 价格完全没波动（极端情况），直接置空
        df["poc_full"] = float("nan")
        df["price_to_poc_pct"] = float("nan")

    return df

def fetch_market_data(exchange: ccxt.hyperliquid, symbol: str) -> MarketDataSnapshot:
    """
    获取指定交易对的多周期（1m / 1h / 4h / 1d / 1w）K线、行情、资金费率、盘口等信息，供策略分析使用。
    """
    #
    # snapshot: Dict[str, Any] = {"symbol": symbol, "timeframe": "1h"}
    #
    # # ticker = fetch_ticker(exchange, symbol)
    # snapshot["ticker"] = ticker or {}

    ticker: Dict[str, Any]
    try:
        ticker = exchange.fetch_ticker(symbol)
    except Exception:
        ticker = {}
    ohlcv_map: Dict[str, List[List[float]]] = {}
    df_map: Dict[str, pd.DataFrame] = {}

    # 多周期拉取（timeframe -> K线条数）
    #
    # 为什么适合并发：
    # - fetch_ohlcv 是网络 I/O（等待交易所响应），线程并发能显著降低总耗时。
    # 为什么限制并发：
    # - 交易所可能限频；
    # - 部分 ccxt 交易所适配器对同一个 exchange 实例并发调用不一定完全线程安全。
    def _fetch_one(tf: str, limit: int) -> tuple[str, Optional[List[List[float]]]]:
        try:
            data = exchange.fetch_ohlcv(symbol, tf, limit=limit)
            return tf, data
        except Exception:
            return tf, None

    with ThreadPoolExecutor(max_workers=OHLCV_FETCH_MAX_WORKERS) as pool:
        futures = [
            pool.submit(_fetch_one, timeframe, cfg.limit) for timeframe, cfg in TIMEFRAME_SETTINGS.items()
        ]
        for fut in as_completed(futures):
            timeframe, data = fut.result()
            if not data:
                continue
            ohlcv_map[timeframe] = data
            df = ohlcv_to_df(data)
            df = compute_technical_factors(df)
            df_map[timeframe] = df
    # --- derivatives metrics ---
    funding_info = exchange.fetch_funding_rate(symbol)
    funding_rate = funding_info.get("fundingRate")
    # {'baseVolume': None, 'datetime': None,
    #  'info': {'baseId': 0, 'dayBaseVlm': '49885.98866', 'dayNtlVlm': '4574544356.8517580032', 'funding': '0.0000125',
    #           'impactPxs': ['89804.0', '89833.0'], 'marginTableId': '56', 'markPx': '89842.0', 'maxLeverage': '40',
    #           'midPx': '89818.5', 'name': 'BTC', 'openInterest': '21528.52084', 'oraclePx': '89875.0',
    #           'premium': '-0.0004673157', 'prevDayPx': '92026.0', 'szDecimals': '5'}, 'openInterestAmount': 21528.52084,
    #  'openInterestValue': None, 'quoteVolume': None, 'symbol': 'BTC/USDC:USDC', 'timestamp': None}
    interest = exchange.fetch_open_interest(symbol)

    # {'asks': [[89768.0, 14.26363], [89769.0, 1.80097], [89770.0, 1.72654], [89771.0, 2.22226], [89772.0, 2.57124],
    #           [89773.0, 2.1163], [89774.0, 0.41978], [89775.0, 3.66434], [89776.0, 0.62151], [89777.0, 0.2443],
    #           [89778.0, 2.33257], [89779.0, 1.99476], [89780.0, 1.27619], [89781.0, 0.24205], [89782.0, 0.26265],
    #           [89783.0, 4.10493], [89784.0, 3.88559], [89785.0, 6.32247], [89786.0, 1.48073], [89787.0, 7.17681]],
    #  'bids': [[89767.0, 1.11978], [89766.0, 0.00026], [89765.0, 0.0336], [89764.0, 0.00013], [89763.0, 0.00027],
    #           [89762.0, 0.11225], [89761.0, 0.2229], [89760.0, 0.00013], [89759.0, 0.44587], [89758.0, 1.15676],
    #           [89757.0, 0.25449], [89756.0, 0.45221], [89755.0, 0.27143], [89754.0, 3.71832], [89753.0, 0.93806],
    #           [89752.0, 0.9379], [89751.0, 2.0736], [89750.0, 1.20233], [89749.0, 1.34698], [89748.0, 1.03358]],
    #  'datetime': '2025-12-11T15:57:56.815Z', 'nonce': None, 'symbol': 'BTC/USDC:USDC', 'timestamp': 1765468676815}
    order_book = exchange.fetch_order_book(symbol, limit=100)

    spread = None
    spread_bps = None
    bid_depth = None
    ask_depth = None
    imbalance = None

    # --- microstructure (lightweight) ---
    try:
        bids = order_book.get("bids") or []
        asks = order_book.get("asks") or []
        best_bid = float(bids[0][0]) if bids else None
        best_ask = float(asks[0][0]) if asks else None
        if best_bid and best_ask and best_ask > 0:
            spread = best_ask - best_bid
            spread_bps = (best_ask - best_bid) / best_ask * 10_000

        depth_levels = 20
        bid_depth = sum(float(px_qty[1]) for px_qty in bids[:depth_levels]) if bids else 0.0
        ask_depth = sum(float(px_qty[1]) for px_qty in asks[:depth_levels]) if asks else 0.0
        denom = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / denom if denom else 0.0
    except Exception:
        # 盘口数据是“锦上添花”，不让它影响主流程
        pass

    metrics_obj = MarketMetrics(
        ticker=ticker,
        funding_rate=float(funding_rate) if funding_rate is not None else None,
        open_interest=interest,
        order_book=order_book,
        spread=float(spread) if spread is not None else None,
        spread_bps=float(spread_bps) if spread_bps is not None else None,
        order_book_bid_depth=float(bid_depth) if bid_depth is not None else None,
        order_book_ask_depth=float(ask_depth) if ask_depth is not None else None,
        order_book_imbalance=float(imbalance) if imbalance is not None else None,
    )

    return MarketDataSnapshot(symbol=symbol, ohlcv=ohlcv_map, ohlcv_df=df_map, metrics=metrics_obj)


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
        print(f"总权益:      {total_usdc} USDC")
        print(f"可用余额:    {free_usdc} USDC")
        print(f"已用保证金:  {used_usdc} USDC")
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
