"""
技术指标计算模块
负责计算各种技术指标（均线、MACD、RSI、ADX、布林带等）
"""
import math
from typing import Optional

import pandas as pd
import pandas_ta as ta

from src.tools.performance import measure_time


@measure_time
def compute_technical_factors(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算所有技术指标并添加到 DataFrame
    """
    # 基础数据
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    # ==========================================
    # 1. 趋势与动量 (Trend & Momentum)
    # ==========================================
    # 均线组
    df["ema_20"] = ta.ema(close, length=20)
    df["sma_50"] = ta.sma(close, length=50)
    df["ema_50"] = ta.ema(close, length=50)
    df["wma_50"] = ta.wma(close, length=50)

    # MACD
    macd = ta.macd(close)
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    df["macd_hist"] = macd["MACDh_12_26_9"]

    # 其他动能
    df["roc_10"] = ta.roc(close, length=10)
    df["mom_10"] = ta.mom(close, length=10)
    df["rsi_14"] = ta.rsi(close, length=14)

    # ADX
    adx_df = ta.adx(high, low, close, length=14)
    df["adx_14"] = adx_df["ADX_14"]
    df["dmp_14"] = adx_df["DMP_14"]
    df["dmn_14"] = adx_df["DMN_14"]

    # ==========================================
    # 2. 均值回归 (Mean Reversion)
    # ==========================================
    # 布林带
    bbands = ta.bbands(close, length=20, lower_std=2.0, upper_std=2.0)
    df["bb_mid"] = bbands["BBM_20_2.0_2.0"]
    df["bb_upper"] = bbands["BBU_20_2.0_2.0"]
    df["bb_lower"] = bbands["BBL_20_2.0_2.0"]
    df["bb_width"] = bbands["BBB_20_2.0_2.0"]
    df["bb_percent"] = bbands["BBP_20_2.0_2.0"]

    # 肯特纳通道
    kelt = ta.kc(high, low, close, length=20)
    df["kc_mid"] = kelt["KCBe_20_2"]
    df["kc_upper"] = kelt["KCUe_20_2"]
    df["kc_lower"] = kelt["KCLe_20_2"]

    # VWAP & AVWAP
    df["vwap"] = ta.vwap(high, low, close, vol)
    
    # AVWAP: 全局成交量加权均价
    cum_pv = (close * vol).cumsum()
    cum_vol = vol.cumsum()
    df["avwap_full"] = cum_pv / cum_vol

    # Z-Score
    mean_20 = close.rolling(20).mean()
    std_20 = close.rolling(20).std()
    df["zscore_20"] = (close - mean_20) / std_20

    # Williams %R
    df["williams_r"] = ta.willr(high, low, close, length=14)

    # ==========================================
    # 3. 波动率 (Volatility)
    # ==========================================
    df["atr_14"] = ta.atr(high, low, close, length=14)
    df["natr_14"] = df["atr_14"] / close  # 标准化ATR

    # NATR平滑
    df["natr_ema"] = ta.ema(df["natr_14"], length=10)

    # 历史波动率
    log_ret = (close / close.shift(1)).apply(lambda x: math.log(x) if x > 0 else 0)
    df["hv_20"] = log_ret.rolling(20).std()
    df["hv_100"] = log_ret.rolling(100).std()
    df["hv_ratio"] = df["hv_20"] / df["hv_100"]

    # 分布特征
    df["ret_skew_50"] = log_ret.rolling(50).skew()
    df["ret_kurt_50"] = log_ret.rolling(50).kurt()

    # ==========================================
    # 4. 结构与形态 (Structure & Pattern)
    # ==========================================
    # 10日高低点
    df["swing_low_10"] = low.rolling(10).min()
    df["swing_high_10"] = high.rolling(10).max()
    # 20日高低点
    df["n_high"] = close.rolling(20).max()
    df["n_low"] = close.rolling(20).min()

    # 突破判断
    df["breakout_up"] = close.gt(df["n_high"].shift(1)).astype("int8")
    df["breakout_down"] = close.lt(df["n_low"].shift(1)).astype("int8")
    
    # 分形高低点
    df["swing_high_fractal"] = high[(high.shift(1) < high) & (high.shift(-1) < high)]
    df["swing_low_fractal"] = low[(low.shift(1) > low) & (low.shift(-1) > low)]

    # ==========================================
    # 5. 价量分析 (Volume)
    # ==========================================
    # 放量判断
    df["vol_sma_20"] = ta.sma(vol, length=20)
    df["vol_ratio"] = vol / df["vol_sma_20"]
    df["vol_spike_ratio"] = df["vol_ratio"]

    # 突破+放量
    df["breakout_up_with_vol"] = (
        (df["breakout_up"] == 1) & (df["vol_spike_ratio"] > 2.0)
    ).astype(int)

    # OBV
    df["obv"] = ta.obv(close, vol)

    # 简易 POC (Point of Control)
    price_min = close.min()
    price_max = close.max()
    if price_max > price_min:
        bins = 30
        bin_size = (price_max - price_min) / bins
        bin_index = ((close - price_min) / bin_size).astype(int).clip(0, bins - 1)
        vol_profile = vol.groupby(bin_index).sum()
        poc_bin = vol_profile.idxmax()
        poc_price = float(price_min + (poc_bin + 0.5) * bin_size)
        df["poc_full"] = poc_price
        df["price_to_poc_pct"] = (close - poc_price) / poc_price
    else:
        df["poc_full"] = float("nan")
        df["price_to_poc_pct"] = float("nan")

    # ==========================================
    # 6. 环境斜率判定 (Timing Logic)
    # ==========================================
    # EMA平滑后再求Diff，否则噪音太大
    ema_len = 10
    df["adx_ema"] = ta.ema(df["adx_14"], length=ema_len)
    df["bbw_ema"] = ta.ema(df["bb_width"], length=ema_len)

    # 计算斜率
    df["adx_slope"] = df["adx_ema"].diff()
    df["bbw_slope"] = df["bbw_ema"].diff()

    return df
