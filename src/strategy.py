"""
策略模块

目标：真正做到
  K线 + 技术指标 -> 信号 -> 交易计划（TradePlan）-> 交给执行器下单
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from src.config import TIMEFRAME_SETTINGS
from src.market_data import AccountOverview
from src.models import (
    BreakoutSignal,
    MomentumSignal,
    OverheatSignal,
    PositionSide,
    StructureCostSignal,
    TechnicalLinesSnapshot,
    TrendLineSignal,
    VolatilitySignal,
    VolumeConfirmationSignal, OrderBookInfo, Decision, Action, MarketRegime, VolState, TimingState, Slope,
    PerpAssetInfo, DirectionResult, Side, TriggerResult, ValidityResult, SignalSnapshot,
)
from src.tools.system_config import measure_time





def _q_state(cur: float, p20: float, p80: float) -> VolState:
    if cur <= p20:
        return VolState.LOW
    if cur >= p80:
        return VolState.HIGH
    return VolState.NORMAL


def classify_vol_state(df: pd.DataFrame) -> Tuple[VolState, Dict]:
    """
    波动状态（Regime 子模块）：
    - 用 NATR + BB Width 两个“独立波动视角”做一致性判定
    - 输出 low/normal/high，用于策略许可与风险缩放
    todo 有没有必要分细一点
    """
    if df is None or "natr_14" not in df.columns or "bb_width" not in df.columns:
        return VolState.UNKNOWN, {}

    natr = df["natr_14"].dropna()
    bbw = df["bb_width"].dropna()
    if len(natr) < 200 or len(bbw) < 200:
        return VolState.UNKNOWN, {}

    # 统一取近端窗口（大约一周+）
    # 现在的波动，是处在自己历史里的“偏低 / 正常 / 偏高”哪个档位
    w_natr = natr.iloc[-200:]
    w_bbw = bbw.iloc[-200:]

    # 价格实际振幅
    # 在最近 200 根里，找出“最安静的 20% 波动水平”
    n_cur = float(w_natr.iloc[-1])
    n_p20 = float(w_natr.quantile(0.2))
    # 在最近 200 根里，找出“最吵的 20% 波动水平”
    n_p80 = float(w_natr.quantile(0.8))
    n_state = _q_state(n_cur, n_p20, n_p80)

    # 价格分布是不是已经被撑开
    b_cur = float(w_bbw.iloc[-1])
    # 布林带“非常收紧”的历史水平
    b_p20 = float(w_bbw.quantile(0.2))
    # 布林带“明显张开”的历史水平
    b_p80 = float(w_bbw.quantile(0.8))
    # 判断当前布林结构是低 / 中 / 高波动
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


from typing import Optional, List


@measure_time
def decide_regime(
        base: "MarketRegime",
        adx: Optional[float],
        vol_state: "VolState",
        order_book: Optional["OrderBookInfo"],
        timing: Optional["TimingState"],
        max_spread_bps: float,
        # 新增可选参数，默认值保持原样，方便针对不同币种调整
        min_depth: float = 200_000,
        imbalance_limit: float = 0.8
) -> "Decision":
    """
        HIGH vol：禁 mean
        LOW vol：不禁 mean，只 strict_entry
        BBW 扩张：禁 mean
        ADX slope 下行：趋势降权
    """
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
        # 修正点：只有当 allow_mean 原本为 True 时，才记录“被波动率关了”
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
    # Step 4) Risk Calculation & Final Check
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
            allow_new_entry=False, allow_manage=True,  # 允许管理旧仓位
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



def compute_direction(df_1h, regime) -> DirectionResult:
    """
    1h 方向层：用 EMA 结构 + 价格位置 + ADX 强度加权
    """
    reasons: List[str] = []

    close = float(df_1h["close"].iloc[-1])
    ema_fast = float(df_1h["ema_20"].iloc[-1])
    ema_slow = float(df_1h["ema_50"].iloc[-1])
    adx = float(df_1h["adx_14"].iloc[-1])

    # 基础方向：EMA + 价格站位
    if ema_slow < ema_fast < close:
        side = Side.LONG
        conf = 0.55
        reasons.append("1h: close>ema_20 and ema_20>ema_50 (bull bias)")
    elif ema_slow > ema_fast > close:
        side = Side.SHORT
        conf = 0.55
        reasons.append("1h: close<ema_20 and ema_20<ema_50 (bear bias)")
    else:
        side = Side.NONE
        conf = 0.25
        reasons.append("1h: mixed alignment (no clear direction)")

    # ADX 强度调整置信度
    if adx >= 25:
        conf += 0.20
        reasons.append(f"1h: ADX strong ({adx:.1f}>=25)")
    elif adx <= 18:
        conf -= 0.10
        reasons.append(f"1h: ADX weak ({adx:.1f}<=18)")

    # 与 regime 对齐：如果趋势不允许，降低方向置信度
    if getattr(regime, "allow_trend", True) is False:
        conf *= 0.70
        reasons.append("regime: trend not allowed -> reduce confidence")

    conf = max(0.0, min(1.0, conf))
    if side == Side.NONE:
        conf = min(conf, 0.40)

    return DirectionResult(side=side, confidence=conf, reasons=reasons)


def compute_trigger(df_15m, dir_res: DirectionResult, regime) -> TriggerResult:
    """
    15m 触发层：优先回调入场，其次突破入场
    strict_entry 时：突破更苛刻、回调更贴均线、EMA 纠缠直接拒绝
    """
    reasons: List[str] = []

    if dir_res.side == Side.NONE:
        return TriggerResult(False, None, 0.0, ["no direction -> no trigger"])

    strict = bool(getattr(regime, "strict_entry", False))

    close = float(df_15m["close"].iloc[-1])
    ema_fast = float(df_15m["ema_20"].iloc[-1])
    ema_slow = float(df_15m["ema_50"].iloc[-1])
    atr = float(df_15m["atr_14"].iloc[-1])

    # 最近 N 根高低点用于突破触发
    N = 20
    hh = float(df_15m["high"].iloc[-N:].max())
    ll = float(df_15m["low"].iloc[-N:].min())

    breakout_pad = (0.20 * atr) if strict else (0.05 * atr)
    pullback_band = (0.25 * atr) if strict else (0.35 * atr)

    entry_ok = False
    entry_hint = None
    strength = 0.0

    # A) 回调入场（更稳）
    if dir_res.side == Side.LONG:
        if abs(close - ema_fast) <= pullback_band and close >= ema_fast >= ema_slow:
            entry_ok = True
            entry_hint = close
            strength = 0.55
            reasons.append("15m: pullback to ema_20 and reclaimed (long)")
    else:  # SHORT
        if abs(close - ema_fast) <= pullback_band and close <= ema_fast <= ema_slow:
            entry_ok = True
            entry_hint = close
            strength = 0.55
            reasons.append("15m: pullback to ema_20 and rejected (short)")

    # B) 突破入场（更快）
    if not entry_ok:
        if dir_res.side == Side.LONG and close >= (hh + breakout_pad) and ema_fast >= ema_slow:
            entry_ok = True
            entry_hint = hh + breakout_pad
            strength = 0.65 if strict else 0.60
            reasons.append(f"15m: breakout above {N}-bar HH + pad (long)")
        elif dir_res.side == Side.SHORT and close <= (ll - breakout_pad) and ema_fast <= ema_slow:
            entry_ok = True
            entry_hint = ll - breakout_pad
            strength = 0.65 if strict else 0.60
            reasons.append(f"15m: breakdown below {N}-bar LL - pad (short)")

    # strict_entry：EMA 纠缠过滤（避免震荡里假触发）
    if entry_ok and strict:
        ema_gap = abs(ema_fast - ema_slow)
        if ema_gap < 0.20 * atr:
            entry_ok = False
            strength *= 0.5
            reasons.append("strict: ema_20/ema_50 too close -> reject")

    return TriggerResult(entry_ok, entry_hint, strength, reasons)


def compute_validity_and_risk(df_15m, df_5m, dir_res, trg_res, regime, asset_info) -> ValidityResult:
    """
    有效性/风险层：
    - 初始止损：ATR 止损 + 结构止损（取更保守）
    - 5m 反向漂移过滤（防假突破/插针）
    - exit_ok：均线反穿作为最小退出信号（可扩展为结构破坏/动能衰竭）
    flip_ok：默认关闭（反手需要更严格的反向触发）
    """
    reasons: List[str] = []

    if dir_res.side == Side.NONE:
        return ValidityResult(None, False, False, 0.0, ["no direction -> no validity"])

    strict = bool(getattr(regime, "strict_entry", False))
    close15 = float(df_15m["close"].iloc[-1])
    atr15 = float(df_15m["atr_14"].iloc[-1])

    # 1) 止损：ATR + 结构
    k = 1.3 if strict else 1.6
    atr_stop_dist = k * atr15

    N = 10
    swing_low = float(df_15m["low"].iloc[-N:].min())
    swing_high = float(df_15m["high"].iloc[-N:].max())

    if dir_res.side == Side.LONG:
        atr_sl = close15 - atr_stop_dist
        struct_sl = swing_low - 0.10 * atr15
        stop_price = max(atr_sl, struct_sl)  # 更紧的那个
        reasons.append(f"stop: long max(ATR, struct) -> {stop_price:.2f}")
    else:
        atr_sl = close15 + atr_stop_dist
        struct_sl = swing_high + 0.10 * atr15
        stop_price = min(atr_sl, struct_sl)
        reasons.append(f"stop: short min(ATR, struct) -> {stop_price:.2f}")

    # 2) 质量：5m 防反向漂移（可替换成更严格的形态）
    quality = 0.60
    if df_5m is not None and len(df_5m) >= 5:
        last3 = df_5m["close"].iloc[-3:]
        drift = float(last3.iloc[-1] - last3.iloc[0])
        atr5 = float(df_5m["atr_14"].iloc[-1])

        if dir_res.side == Side.LONG and drift < -0.30 * atr5:
            quality -= 0.25
            reasons.append("5m: adverse drift after trigger -> lower quality")
        if dir_res.side == Side.SHORT and drift > 0.30 * atr5:
            quality -= 0.25
            reasons.append("5m: adverse drift after trigger -> lower quality")

    # 3) exit_ok：均线反穿（最小版本）
    ema_fast15 = float(df_15m["ema_20"].iloc[-1])
    ema_slow15 = float(df_15m["ema_50"].iloc[-1])
    exit_ok = False
    if dir_res.side == Side.LONG and ema_fast15 < ema_slow15:
        exit_ok = True
        reasons.append("exit: 15m ema_20 crossed below ema_50")
    if dir_res.side == Side.SHORT and ema_fast15 > ema_slow15:
        exit_ok = True
        reasons.append("exit: 15m ema_20 crossed above ema_50")

    # 4) flip_ok：默认严格关闭（你要做反手必须加反向触发+thesis invalidated）
    flip_ok = False

    quality = max(0.0, min(1.0, quality))
    return ValidityResult(stop_price, exit_ok, flip_ok, quality, reasons)


def score_signal(dir_res, trg_res, val_res, regime) -> Tuple[float, List[str]]:
    """
    打分：Direction(40) + Trigger(40) + Quality(20)
    """
    reasons: List[str] = []
    score = 0.0

    score += 40.0 * dir_res.confidence
    reasons += dir_res.reasons

    score += 40.0 * (trg_res.strength if trg_res.entry_ok else 0.0)
    reasons += trg_res.reasons

    score += 20.0 * val_res.quality
    reasons += val_res.reasons

    # regime 惩罚
    if getattr(regime, "allow_trend", True) is False:
        score *= 0.70
        reasons.append("penalty: trend not allowed")

    if getattr(regime, "strict_entry", False):
        reasons.append("strict_entry enabled")

    return score, reasons

@measure_time
def build_signal(df_1h, df_15m, df_5m, regime, asset_info, now_ts: float) -> SignalSnapshot:
    dir_res = compute_direction(df_1h, regime)
    trg_res = compute_trigger(df_15m, dir_res, regime)
    val_res = compute_validity_and_risk(df_15m, df_5m, dir_res, trg_res, regime, asset_info)

    score, reasons = score_signal(dir_res, trg_res, val_res, regime)
    entry_threshold = 80.0 if getattr(regime, "strict_entry", False) else 70.0

    entry_ok = trg_res.entry_ok and (score >= entry_threshold) and (dir_res.side != Side.NONE)

    return SignalSnapshot(
        side=dir_res.side,
        entry_ok=entry_ok,
        add_ok=False,  # 先禁用
        exit_ok=val_res.exit_ok,
        flip_ok=val_res.flip_ok,
        entry_price_hint=trg_res.entry_price_hint,
        stop_price=val_res.stop_price,
        score=score,
        reasons=reasons,
        ttl_seconds=(45 if getattr(regime, "strict_entry", False) else 120),
        created_ts=now_ts
    )