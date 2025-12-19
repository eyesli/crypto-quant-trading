"""
市场体制判断模块
负责判断市场波动状态和交易体制
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.data.models import Action, Decision, MarketRegime, OrderBookInfo, Slope, TimingState, VolState
from src.tools.performance import measure_time


def _q_state(cur: float, p20: float, p80: float) -> VolState:
    """辅助函数：根据当前值和分位数判断波动状态"""
    if cur <= p20:
        return VolState.LOW
    if cur >= p80:
        return VolState.HIGH
    return VolState.NORMAL


def classify_vol_state(df: pd.DataFrame) -> Tuple[VolState, Dict]:
    """
    波动状态（Regime 子模块）：
    - 用 NATR + BB Width 两个"独立波动视角"做一致性判定
    - 输出 low/normal/high，用于策略许可与风险缩放
    """
    if df is None or "natr_14" not in df.columns or "bb_width" not in df.columns:
        return VolState.UNKNOWN, {}

    natr = df["natr_14"].dropna()
    bbw = df["bb_width"].dropna()
    if len(natr) < 200 or len(bbw) < 200:
        return VolState.UNKNOWN, {}

    # 统一取近端窗口（大约一周+）
    w_natr = natr.iloc[-200:]
    w_bbw = bbw.iloc[-200:]

    # 价格实际振幅
    n_cur = float(w_natr.iloc[-1])
    n_p20 = float(w_natr.quantile(0.2))
    n_p80 = float(w_natr.quantile(0.8))
    n_state = _q_state(n_cur, n_p20, n_p80)

    # 价格分布是不是已经被撑开
    b_cur = float(w_bbw.iloc[-1])
    b_p20 = float(w_bbw.quantile(0.2))
    b_p80 = float(w_bbw.quantile(0.8))
    b_state = _q_state(b_cur, b_p20, b_p80)

    # 一致性判定：两者一致 → 置信度高
    if n_state == b_state:
        final = n_state
        conf = "high"
    else:
        # 冲突时：保守策略 —— 视为 normal/mixed（不要极端化）
        final = VolState.NORMAL
        conf = "low"

    dbg = {
        "final": final,
        "confidence": conf,
        "natr": {"cur": n_cur, "p20": n_p20, "p80": n_p80, "state": n_state},
        "bbw": {"cur": b_cur, "p20": b_p20, "p80": b_p80, "state": b_state},
    }
    return final, dbg


@measure_time
def decide_regime(
    base: MarketRegime,
    adx: Optional[float],
    vol_state: VolState,
    order_book: Optional[OrderBookInfo],
    timing: Optional[TimingState],
    max_spread_bps: float,
    min_depth: float = 200_000,
    imbalance_limit: float = 0.8
) -> Decision:
    """
    决定交易体制：
    HIGH vol：禁 mean
    LOW vol：不禁 mean，只 strict_entry
    BBW 扩张：禁 mean
    ADX slope 下行：趋势降权
    """
    from src.data.models import TimingState
    
    timing = timing or TimingState()
    adx_slope_state = timing.adx_slope.state
    bbw_slope_state = timing.bbw_slope.state

    if order_book is None:
        spread_bps = None
        bid_depth = 0.0
        ask_depth = 0.0
        imbalance = None
    else:
        spread_bps = order_book.spread_bps
        bid_depth = order_book.bid_depth_value or 0.0
        ask_depth = order_book.ask_depth_value or 0.0
        imbalance = order_book.imbalance
    depth = bid_depth + ask_depth

    # --- 1. 初始意图 ---
    allow_trend = base in (MarketRegime.TREND, MarketRegime.MIXED)
    allow_mean = base in (MarketRegime.RANGE, MarketRegime.MIXED)

    hard_reasons: List[str] = []
    soft_reasons: List[str] = []

    # =========================================================
    # Step 1) HARD STOP (熔断机制)
    # =========================================================
    if base == MarketRegime.UNKNOWN or vol_state == VolState.UNKNOWN:
        hard_reasons.append("regime or vol_state unknown")

    if spread_bps is not None and spread_bps > max_spread_bps:
        hard_reasons.append(f"spread too wide ({spread_bps:.2f}bps)")

    # 关键：ADX 缺失直接硬阻断
    if adx is None:
        hard_reasons.append("ADX missing")

    if hard_reasons:
        return Decision(
            action=Action.NO_NEW_ENTRY,
            regime=base,
            allow_trend=False, allow_mean=False,
            allow_new_entry=False, allow_manage=True,
            risk_scale=0.0, cooldown_scale=2.0,
            reasons=hard_reasons,
            adx=adx, vol_state=vol_state, order_book=order_book
        )

    # =========================================================
    # Step 2) Strategy Gates (策略逻辑裁剪)
    # =========================================================
    adx_val = float(adx)
    strict_entry = False
    gate_logs: List[str] = []

    # --- 波动率逻辑 ---
    if vol_state == VolState.HIGH:
        if allow_mean:
            allow_mean = False
            gate_logs.append("gate: high vol disables mean")

    elif vol_state == VolState.LOW:
        if bbw_slope_state != Slope.UP:
            strict_entry = True
            gate_logs.append("gate: low vol -> strict entry (no expansion)")
        else:
            gate_logs.append("gate: low vol but bbw expanding -> ok")

    # --- ADX 强度过滤 ---
    if adx_val < 20:
        if allow_trend:
            allow_trend = False
            gate_logs.append(f"gate: adx too weak ({adx_val:.1f}<20)")

    # --- Timing 过滤 ---
    if adx_slope_state == Slope.DOWN and allow_trend:
        # A) 强趋势回调 (ADX > 25) -> 放行
        if adx_val > 25:
            pass
        # B) 弱势下跌 -> 趋势结束
        else:
            allow_trend = False
            gate_logs.append(f"gate: adx fading ({adx_val:.1f}) & slope down")

    # 布林带开口保护
    if bbw_slope_state == Slope.UP and base in (MarketRegime.RANGE, MarketRegime.MIXED):
        if allow_mean:
            allow_mean = False
            gate_logs.append("gate: bbw expanding disables mean")

    # =========================================================
    # Step 3) SOFT STOP (环境过滤)
    # =========================================================

    if order_book is not None and 0 < depth < min_depth:
        soft_reasons.append(f"order book thin (depth={depth:.0f})")

    if imbalance is not None and abs(imbalance) > imbalance_limit:
        soft_reasons.append(f"extreme imbalance ({imbalance:.2f})")

    if vol_state == VolState.HIGH and base in (MarketRegime.RANGE, MarketRegime.MIXED):
        soft_reasons.append("high vol + range: whipsaw risk")

    if order_book is None:
        soft_reasons.append("order book missing")

    # =========================================================
    # Step 4) Risk Calculation & Final Check
    # =========================================================
    if vol_state == VolState.HIGH:
        risk_scale, cooldown_scale = 0.6, 2.0
    elif vol_state == VolState.LOW:
        risk_scale, cooldown_scale = 0.8, 1.5
    else:
        risk_scale, cooldown_scale = 1.0, 1.0

    # 2) 动态修正
    # A) 趋势动能减弱 -> 降仓
    if adx_slope_state == Slope.DOWN and allow_trend and adx_val > 25:
        risk_scale *= 0.75

    # B) 狙击模式 -> 试错仓
    if vol_state == VolState.LOW and strict_entry:
        risk_scale *= 0.7

    # 软拒绝检查
    if soft_reasons:
        all_reasons = soft_reasons + gate_logs
        return Decision(
            action=Action.NO_NEW_ENTRY,
            regime=base,
            strict_entry=strict_entry,
            allow_trend=allow_trend, allow_mean=allow_mean,
            allow_new_entry=False, allow_manage=True,
            risk_scale=risk_scale, cooldown_scale=cooldown_scale,
            reasons=all_reasons,
            adx=adx, vol_state=vol_state, order_book=order_book
        )

    # 僵尸状态检查 (Logic Filter 导致无策略可用)
    if not allow_trend and not allow_mean:
        failure_reasons = gate_logs if gate_logs else ["logic gap: no strategy fits"]
        return Decision(
            action=Action.NO_NEW_ENTRY,
            strict_entry=False,
            regime=base,
            allow_trend=False, allow_mean=False,
            allow_new_entry=False, allow_manage=True,
            risk_scale=risk_scale, cooldown_scale=cooldown_scale,
            reasons=failure_reasons,
            adx=adx, vol_state=vol_state, order_book=order_book
        )

    # =========================================================
    # Step 5) GREEN LIGHT
    # =========================================================
    return Decision(
        action=Action.OK,
        regime=base,
        strict_entry=strict_entry,
        allow_trend=allow_trend,
        allow_mean=allow_mean,
        allow_new_entry=True,
        allow_manage=True,
        risk_scale=risk_scale,
        cooldown_scale=cooldown_scale,
        reasons=[f"ok: regime={base.name}"],
        adx=adx,
        vol_state=vol_state,
        order_book=order_book
    )
