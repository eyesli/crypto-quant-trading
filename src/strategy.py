"""
策略模块

基于K线数据的简单量化策略，结合参考地址的方向判断，给出做多/做空、止损、平仓建议。
"""

from statistics import mean
from typing import Dict, List, Optional


def _get_closes(ohlcv: List[List[float]]) -> List[float]:
    return [candle[4] for candle in ohlcv]


def _simple_moving_average(values: List[float], period: int) -> Optional[float]:
    if len(values) < period or period <= 0:
        return None
    return mean(values[-period:])


def _relative_strength_index(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None

    gains = []
    losses = []
    for prev, curr in zip(values[-period - 1 : -1], values[-period:]):
        change = curr - prev
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    average_gain = mean(gains)
    average_loss = mean(losses)
    if average_loss == 0:
        return 100.0

    rs = average_gain / average_loss
    return 100 - (100 / (1 + rs))


def _macd(values: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, float]:
    def ema(series: List[float], period: int) -> List[float]:
        if len(series) < period or period <= 0:
            return []
        k = 2 / (period + 1)
        ema_values = [mean(series[:period])]
        for price in series[period:]:
            ema_values.append(price * k + ema_values[-1] * (1 - k))
        return ema_values

    slow_ema = ema(values, slow)
    fast_ema = ema(values, fast)
    if not slow_ema or not fast_ema:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}

    macd_line = [f - s for f, s in zip(fast_ema[-len(slow_ema) :], slow_ema)]
    if len(macd_line) < signal:
        macd_value = macd_line[-1]
        return {"macd": macd_value, "signal": 0.0, "histogram": macd_value}

    signal_line = ema(macd_line, signal)
    macd_value = macd_line[-1]
    signal_value = signal_line[-1]
    histogram = macd_value - signal_value
    return {"macd": macd_value, "signal": signal_value, "histogram": histogram}


def _swing_levels(ohlcv: List[List[float]]) -> Dict[str, float]:
    lows = [candle[3] for candle in ohlcv]
    highs = [candle[2] for candle in ohlcv]
    return {"recent_low": min(lows), "recent_high": max(highs)}


def determine_trade_plan(
    ohlcv: List[List[float]],
    reference_direction: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """
    根据K线数据输出交易计划。

    Args:
        ohlcv: K线列表 [timestamp, open, high, low, close, volume]
        reference_direction: 参考地址的方向（"long"/"short"），无记录则传入 None

    Returns:
        包含方向、止损、止盈和理由的字典
    """

    if not ohlcv:
        return {"direction": None, "stop_loss": None, "take_profit": None, "reason": "无K线数据"}

    closes = _get_closes(ohlcv)
    short_ma = _simple_moving_average(closes, 9)
    long_ma = _simple_moving_average(closes, 21)
    rsi = _relative_strength_index(closes)
    macd_values = _macd(closes)
    swings = _swing_levels(ohlcv)
    latest_close = closes[-1]

    direction = None
    confidence = 0
    reasons = []

    if short_ma and long_ma:
        if short_ma > long_ma:
            direction = "long"
            confidence += 1
            reasons.append("短期均线上穿长期均线，偏多")
        elif short_ma < long_ma:
            direction = "short"
            confidence += 1
            reasons.append("短期均线下穿长期均线，偏空")

    if rsi is not None:
        if rsi < 35:
            reasons.append("RSI 超卖，存在反弹机会")
            confidence += 1
            direction = direction or "long"
        elif rsi > 65:
            reasons.append("RSI 超买，下行风险增大")
            confidence += 1
            direction = direction or "short"

    macd_hist = macd_values.get("histogram", 0.0)
    if macd_hist > 0:
        reasons.append("MACD 柱线为正，动能偏多")
        confidence += 1
        direction = direction or "long"
    elif macd_hist < 0:
        reasons.append("MACD 柱线为负，动能偏空")
        confidence += 1
        direction = direction or "short"

    if reference_direction:
        if reference_direction == direction:
            confidence += 1
            reasons.append("参考地址方向一致，增加把握度")
        else:
            reasons.append("参考地址方向相反，需谨慎")
            confidence = max(confidence - 1, 0)
    else:
        reasons.append("参考地址暂无操作，忽略该因素")

    if direction is None:
        reasons.append("指标信号不一致，暂不下单")
        return {"direction": None, "stop_loss": None, "take_profit": None, "reason": "; ".join(reasons)}

    risk_buffer = 0.003  # 0.3% 波动缓冲
    if direction == "long":
        stop_loss = min(swings["recent_low"], latest_close * (1 - risk_buffer))
        take_profit = latest_close + (latest_close - stop_loss) * 2
    else:
        stop_loss = max(swings["recent_high"], latest_close * (1 + risk_buffer))
        take_profit = latest_close - (stop_loss - latest_close) * 2

    reasons.append(f"信心值 {confidence}（值越高越可靠）")

    return {
        "direction": direction,
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "reason": "; ".join(reasons),
    }
