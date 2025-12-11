"""
策略模块

基于K线数据的简单量化策略，结合参考地址的方向判断，给出做多/做空、止损、平仓建议。
"""

from statistics import mean
from typing import Dict, List, Optional

from src.market_data import AccountOverview



def run_complex_strategy( account_overview:AccountOverview,market_data):
    balances = account_overview.balances
    positions = account_overview.positions

    ohlcv_df = market_data["timeframes"]["ohlcv_df"]
    trend_summary: Dict[str, Any] = {}
    for tf, tf_df in ohlcv_df.items():
        trend_summary[tf] = analyze_trend_single_tf(tf_df)



    return trend_summary # 测试用


from typing import Dict, Any
import numpy as np
import pandas as pd


def analyze_trend_single_tf(df: pd.DataFrame) -> Dict[str, Any]:
    """
    对单一 timeframe（例如 1h 的 df）做行情趋势分析：
    返回 trend_score ∈ [-1, 1] 和文字标签。
    """

    # 为了避免 NaN，直接丢掉前面指标还没填满的行
    df = df.dropna(subset=[
        "close", "ema_50", "sma_50",
        "macd_hist", "adx_14",
        "bb_width", "bb_percent",
        "vol_spike_ratio",
    ])

    if len(df) < 10:
        return {
            "score": 0.0,
            "label": "数据不足",
            "detail": "有效K线太少，无法判断",
        }

    row = df.iloc[-1]          # 当前最新一根K
    prev = df.iloc[-2]         # 上一根（可以用来看是否刚刚突破）

    score = 0.0
    reasons = []

    close = row["close"]

    # 1) 价格相对 EMA50 / SMA50（大趋势方向）
    ema = row["ema_50"]
    sma = row["sma_50"]

    if ema and sma:
        bias_ema = (close - ema) / ema
        bias_sma = (close - sma) / sma

        if bias_ema > 0.005:
            score += 0.35
            reasons.append(f"价格在EMA50上方 ({bias_ema:.2%})，偏多")
        elif bias_ema < -0.005:
            score -= 0.35
            reasons.append(f"价格在EMA50下方 ({bias_ema:.2%})，偏空")

        if ema > sma:
            score += 0.15
            reasons.append("EMA50 在 SMA50 上方，中期趋势偏多")
        elif ema < sma:
            score -= 0.15
            reasons.append("EMA50 在 SMA50 下方，中期趋势偏空")

    # 2) MACD 动能
    macd_hist = row.get("macd_hist", 0.0)
    if macd_hist > 0:
        score += 0.2
        reasons.append("MACD 柱为正，多头动能占优")
    elif macd_hist < 0:
        score -= 0.2
        reasons.append("MACD 柱为负，空头动能占优")

    # 3) ADX 判断是否有趋势
    adx = row.get("adx_14", 0.0)
    trending = adx > 25
    if trending:
        reasons.append(f"ADX={adx:.1f}，存在较强趋势")
    else:
        reasons.append(f"ADX={adx:.1f}，趋势不强，偏震荡")
        score *= 0.6  # 没有趋势则弱化方向性判断

    # 4) 突破 + 放量
    if row.get("breakout_up", 0) == 1 and row.get("vol_spike_ratio", 1) > 1.5:
        score += 0.25
        reasons.append("价格向上突破近期新高且有放量，趋势上攻确认")
    if row.get("breakout_down", 0) == 1 and row.get("vol_spike_ratio", 1) > 1.5:
        score -= 0.25
        reasons.append("价格向下跌破近期新低且有放量，下跌趋势确认")

    # 5) 布林带位置（偏高/偏低）
    bbp = row.get("bb_percent", 0.5)  # 0~1
    if bbp > 0.9:
        reasons.append("价格接近布林上轨，短期略偏高位")
    elif bbp < 0.1:
        reasons.append("价格接近布林下轨，短期略偏低位")

    # 6) AVWAP / POC 相对位置（如果你已经加了 avwap_full / poc_full）
    avwap = row.get("avwap_full", None)
    poc = row.get("poc_full", None)

    if avwap:
        bias_avwap = (close - avwap) / avwap
        if bias_avwap > 0.01:
            score += 0.1
            reasons.append(f"价格在AVWAP上方 ({bias_avwap:.2%})，主力成本线之上，偏多")
        elif bias_avwap < -0.01:
            score -= 0.1
            reasons.append(f"价格在AVWAP下方 ({bias_avwap:.2%})，主力成本线之下，偏空")

    # 分数限制在 [-1, 1]
    score = max(min(score, 1.0), -1.0)

    # 打标签
    if score >= 0.6:
        label = "强多头趋势"
    elif score >= 0.2:
        label = "偏多震荡 / 温和多头"
    elif score > -0.2:
        label = "震荡市（多空平衡）"
    elif score > -0.6:
        label = "偏空震荡 / 温和空头"
    else:
        label = "强空头趋势"

    return {
        "score": float(score),
        "label": label,
        "detail": "；".join(reasons),
    }