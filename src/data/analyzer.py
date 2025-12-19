"""
市场分析模块
负责分析市场状态（趋势/震荡、波动率状态、时机判断等）
"""
from typing import Optional

import pandas as pd

from src.data.models import MarketRegime, TimingState, SlopeState, Slope
from src.tools.performance import measure_time


@measure_time
def classify_trend_range(
    df: pd.DataFrame,
    prev: MarketRegime = MarketRegime.UNKNOWN,
) -> tuple[MarketRegime, Optional[float]]:
    """
    判断市场体制：TREND / RANGE / MIXED (with hysteresis)

    - Enter TREND: ADX >= 26
    - Exit  TREND: ADX < 23
    - Enter RANGE: ADX <= 17
    - Exit  RANGE: ADX > 19

    prev: 上一次的 regime，用于迟滞（防抖）
    """
    if df is None or "adx_14" not in df.columns:
        return MarketRegime.UNKNOWN, None

    s = df["adx_14"].dropna()
    if len(s) < 50:
        return MarketRegime.UNKNOWN, None

    adx = float(s.iloc[-1])

    # ---------- Hysteresis ----------
    # 如果上一状态是 TREND：只有明显走弱才退出
    if prev == MarketRegime.TREND:
        if adx < 23:
            return MarketRegime.MIXED, adx
        return MarketRegime.TREND, adx

    # 如果上一状态是 RANGE：只有明显增强才退出
    if prev == MarketRegime.RANGE:
        if adx > 19:
            return MarketRegime.MIXED, adx
        return MarketRegime.RANGE, adx

    # ---------- From MIXED / UNKNOWN ----------
    if adx >= 26:
        return MarketRegime.TREND, adx
    if adx <= 17:
        return MarketRegime.RANGE, adx

    return MarketRegime.MIXED, adx


def classify_timing_state(df: pd.DataFrame, window: int = 200, k: float = 0.2) -> TimingState:
    """
    判断时机状态（ADX斜率、BBW斜率）
    """
    def _state(series: Optional[pd.Series]) -> SlopeState:
        if series is None:
            return SlopeState(state=Slope.UNKNOWN, cur=None, eps=None)
        
        s = series.dropna()
        if len(s) < window:
            return SlopeState(state=Slope.UNKNOWN, cur=None, eps=None)
        
        w = s.iloc[-window:]
        cur = float(w.iloc[-1])
        std = float(w.std())
        eps = std * k if std > 0 else 0.0
        
        if cur > eps:
            st = Slope.UP
        elif cur < -eps:
            st = Slope.DOWN
        else:
            st = Slope.FLAT
        return SlopeState(state=st, cur=cur, eps=eps)

    return TimingState(
        adx_slope=_state(df.get("adx_slope")),
        bbw_slope=_state(df.get("bbw_slope")),
    )
