"""
信号生成模块
负责生成交易信号（方向、触发、有效性、评分）
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from src.account.manager import position_stop_price
from src.data.models import Decision, DirectionResult, PerpAssetInfo, Side, SignalSnapshot, \
    TriggerResult, ValidityResult, PerpPosition
from src.tools.performance import measure_time


def compute_direction(df_1h: pd.DataFrame, regime: Decision) -> DirectionResult:
    """
    1h 方向层（Bias, not Entry）：
    - 结构：ema20 vs ema50 决定 bull/bear 结构
    - 价格位置：Momentum / Pullback / Breakdown 三段定级
    - 修正项：ADX 强弱、EMA20 slope、extension(乖离率)、regime.allow_trend gate
    目标：宽容地保持方向偏置，不在回调区丢方向，但在"走平/追高/环境不允许"时降权。
    """
    reasons: List[str] = []

    # ---- Guard (避免 NaN/缺列崩溃) ----
    need_cols = ("close", "ema_20", "ema_50", "adx_14")
    for c in need_cols:
        if c not in df_1h.columns:
            return DirectionResult(side=Side.NONE, confidence=0.0, reasons=[f"1h: missing {c}"])
    if len(df_1h) < 2:
        return DirectionResult(side=Side.NONE, confidence=0.0, reasons=["1h: insufficient bars (<2)"])

    close = float(df_1h["close"].iloc[-1])
    ema20 = float(df_1h["ema_20"].iloc[-1])
    ema50 = float(df_1h["ema_50"].iloc[-1])
    adx = float(df_1h["adx_14"].iloc[-1])

    # slope (safe)
    ema20_prev = float(df_1h["ema_20"].iloc[-2])
    slope_pct = (ema20 - ema20_prev) / ema20_prev if ema20_prev not in (0.0, None) else 0.0

    # extension (safe)
    ext = (close - ema20) / ema20 if ema20 != 0 else 0.0  # + means above ema20

    # ---- Tunables（你后面可以放进 config） ----
    min_slope = 0.0002  # 0.02%/bar：过滤走平假趋势（按品种可调）
    ext_limit = 0.02  # 2%：开始认为"追高/追低风险上升"
    ext_hard = 0.035  # 3.5%：更重惩罚
    ext_penalty_max = 0.15  # 最大扣分
    slope_penalty = 0.12  # 走平扣分
    pullback_slope_floor = -min_slope  # 回调区允许轻微走平，但不允许明显拐头

    # ---- 1) Structure decides side (宽容) ----
    bull_struct = ema20 > ema50
    bear_struct = ema20 < ema50

    side = Side.NONE
    conf = 0.0

    if bull_struct:
        side = Side.LONG

        # A) Momentum：close > ema20
        if close > ema20:
            conf = 0.60
            reasons.append("1h: Bull Momentum (close>ema20)")

            # slope bonus / penalty
            if slope_pct >= min_slope:
                conf += 0.05
                reasons.append(f"1h: Slope Up (+) {slope_pct * 100:.3f}%")
            elif slope_pct < min_slope:
                conf -= slope_penalty
                reasons.append(f"1h: Slope too flat (-) {slope_pct * 100:.3f}%<{min_slope * 100:.3f}%")

            # extension penalty (anti-chase)
            if ext > ext_limit:
                t = max(0.0, min(1.0, (ext - ext_limit) / max(1e-9, (ext_hard - ext_limit))))
                pen = ext_penalty_max * t
                conf -= pen
                reasons.append(f"1h: Extension high (+{ext * 100:.2f}%) -> -{pen:.2f}")

        # B) Pullback：ema50 < close <= ema20
        elif close > ema50:
            conf = 0.48
            reasons.append("1h: Bull Pullback (ema50<close<=ema20)")

            # 回调区：如果 slope 明显转负，降权
            if slope_pct < pullback_slope_floor:
                conf -= 0.10
                reasons.append(f"1h: Pullback but slope turning down ({slope_pct * 100:.3f}%)")

        # C) Breakdown：close <= ema50（不立刻归零，给低置信度等待确认）
        else:
            side = Side.NONE
            conf = 0.15
            reasons.append("1h: Below ema50 -> possible bull breakdown (wait confirm)")

    elif bear_struct:
        side = Side.SHORT

        # A) Momentum：close < ema20
        if close < ema20:
            conf = 0.60
            reasons.append("1h: Bear Momentum (close<ema20)")

            if slope_pct <= -min_slope:
                conf += 0.05
                reasons.append(f"1h: Slope Down (+) {slope_pct * 100:.3f}%")
            elif slope_pct > -min_slope:
                conf -= slope_penalty
                reasons.append(f"1h: Slope too flat (-) {slope_pct * 100:.3f}%>-{min_slope * 100:.3f}%")

            # extension penalty (anti-chase)
            if ext < -ext_limit:  # far below ema20
                t = max(0.0, min(1.0, ((-ext) - ext_limit) / max(1e-9, (ext_hard - ext_limit))))
                pen = ext_penalty_max * t
                conf -= pen
                reasons.append(f"1h: Extension high ({ext * 100:.2f}%) -> -{pen:.2f}")

        # B) Pullback：ema50 > close >= ema20
        elif close < ema50:
            conf = 0.48
            reasons.append("1h: Bear Pullback (ema50>close>=ema20)")

            if slope_pct > -pullback_slope_floor:  # 即 slope_pct > +min_slope
                conf -= 0.10
                reasons.append(f"1h: Pullback but slope turning up ({slope_pct * 100:.3f}%)")

        # C) Breakdown：close >= ema50
        else:
            side = Side.NONE
            conf = 0.15
            reasons.append("1h: Above ema50 -> possible bear breakdown (wait confirm)")

    else:
        side = Side.NONE
        conf = 0.25
        reasons.append("1h: EMAs tangled -> no clear bias")

    # ---- 2) ADX strength weighting (按数值，不依赖 regime.regime 字段) ----
    if side != Side.NONE:
        if adx >= 25:
            conf += 0.15
            reasons.append(f"1h: ADX strong (+) {adx:.1f}")
        elif adx <= 18:
            conf -= 0.10
            reasons.append(f"1h: ADX weak (-) {adx:.1f}")

    # ---- 3) Regime gate (只降权，不封杀) ----
    if side != Side.NONE and not regime.allow_trend:
        conf *= 0.60
        reasons.append("regime: trend not allowed -> confidence *0.60")

    # ---- 4) Final clamp ----
    conf = max(0.0, min(1.0, conf))
    if side == Side.NONE:
        conf = min(conf, 0.40)  # NONE 不要太高

    return DirectionResult(side=side, confidence=conf, reasons=reasons)


def compute_trigger(df_15m: pd.DataFrame, dir_res: DirectionResult, regime: Decision) -> TriggerResult:
    """
    15m Trigger（入场触发器，负责"现在能不能上车"）

    触发形态：
    A) Pullback（优先）：位置靠近 EMA20 + "动作确认"（reclaim/reject + 反转K线）
    B) Breakout（其次）：突破前N根HH/LL（不含当前K）+ close确认 + 事件触发(跨越) + 结构过滤

    strict_entry：
    - Pullback：要求 reclaim AND green/red（更硬）
    - Breakout：pad 更大；EMA纠缠拒绝；低波动拒绝突破
    """
    reasons: List[str] = []

    # -------- 0) No bias -> no trigger --------
    if dir_res.side == Side.NONE:
        return TriggerResult(False, None, 0.0, False, ["no direction -> no trigger"])

    # -------- 1) Guard --------
    need_cols = ("open", "high", "low", "close", "ema_20", "ema_50", "atr_14")
    for c in need_cols:
        if c not in df_15m.columns:
            return TriggerResult(False, None, 0.0, False, [f"15m: missing {c}"])
    if len(df_15m) < 3:
        return TriggerResult(False, None, 0.0, False, ["15m: insufficient bars (<3)"])

    strict_entry = regime.strict_entry

    # -------- 2) Latest bar data --------
    open_ = float(df_15m["open"].iloc[-1])
    high = float(df_15m["high"].iloc[-1])
    low = float(df_15m["low"].iloc[-1])
    close = float(df_15m["close"].iloc[-1])

    prev_close = float(df_15m["close"].iloc[-2])

    ema20 = float(df_15m["ema_20"].iloc[-1])
    ema50 = float(df_15m["ema_50"].iloc[-1])
    atr = float(df_15m["atr_14"].iloc[-1])

    if atr <= 0:
        return TriggerResult(False, None, 0.0, False, ["15m: ATR invalid (<=0)"])

    # -------- 3) Tunables (建议后续放 config) --------
    N = 20
    breakout_pad = (0.20 * atr) if strict_entry else (0.05 * atr)
    pullback_band = (0.25 * atr) if strict_entry else (0.35 * atr)

    ema_tangle_gap = 0.20 * atr  # strict 下：ema20/ema50 太近认为纠缠
    min_breakout_natr = 0.005  # strict 下：低波动不追突破（0.5%）

    # -------- 4) Helpers / flags --------
    is_green = close > open_
    is_red = close < open_

    in_bull_struct = (ema20 >= ema50)
    in_bear_struct = (ema20 <= ema50)

    near_ema20 = abs(close - ema20) <= pullback_band

    entry_ok = False
    entry_hint = None
    strength = 0.0

    # -------- 5) A) 回调入场 Pullback trigger with ACTION confirmation --------
    if dir_res.side == Side.LONG and in_bull_struct:
        # reclaim: 本K曾触及/刺破EMA20，但收盘收回到EMA20之上
        reclaim = (low <= ema20) and (close >= ema20)
        # 动作确认：非 strict 要 (reclaim OR green)，strict 要 (reclaim AND green)
        action_ok = (reclaim and is_green) if strict_entry else (reclaim or is_green)

        # 位置 + 动作 + 收盘不在ema20下方（避免阴跌）
        if near_ema20 and close >= ema20 and action_ok:
            entry_ok = True
            entry_hint = close
            strength = 0.62 if strict_entry else 0.58
            reasons.append("15m: pullback confirmed long")
            if near_ema20: reasons.append("15m: near ema20")
            if reclaim:    reasons.append("15m: reclaim (low<=ema20 & close>=ema20)")
            if is_green:   reasons.append("15m: green candle")

    elif dir_res.side == Side.SHORT and in_bear_struct:
        reject = (high >= ema20) and (close <= ema20)
        action_ok = (reject and is_red) if strict_entry else (reject or is_red)

        if near_ema20 and close <= ema20 and action_ok:
            entry_ok = True
            entry_hint = close
            strength = 0.62 if strict_entry else 0.58
            reasons.append("15m: pullback confirmed short")
            if near_ema20: reasons.append("15m: near ema20")
            if reject:     reasons.append("15m: reject (high>=ema20 & close<=ema20)")
            if is_red:     reasons.append("15m: red candle")

    # -------- 6) B) 突破前 N 根高点/低点 入场 Breakout trigger --------
    is_breakout = False
    if not entry_ok:
        # 用前 N 根（不含当前K）来算 HH/LL；样本不足则自动缩短窗口
        win_len = min(N, len(df_15m) - 1)
        window = df_15m.iloc[-(win_len + 1):-1]  # exclude current bar
        hh = float(window["high"].max())
        ll = float(window["low"].min())

        up_level = hh + breakout_pad
        dn_level = ll - breakout_pad

        if dir_res.side == Side.LONG and in_bull_struct:
            # 事件触发：上一根没站上，当前收盘站上
            if (prev_close < up_level) and (close >= up_level):
                entry_ok = True
                is_breakout = True
                entry_hint = up_level
                strength = 0.65 if strict_entry else 0.60
                reasons.append(f"15m: breakout close-confirmed above {win_len}-bar HH + pad (long)")

        elif dir_res.side == Side.SHORT and in_bear_struct:
            if (prev_close > dn_level) and (close <= dn_level):
                entry_ok = True
                is_breakout = True
                entry_hint = dn_level
                strength = 0.65 if strict_entry else 0.60
                reasons.append(f"15m: breakdown close-confirmed below {win_len}-bar LL - pad (short)")

    # -------- 7) strict filters --------
    if entry_ok and strict_entry:
        # 7.1 EMA 纠缠过滤（震荡不做）
        ema_gap = abs(ema20 - ema50)
        if ema_gap < ema_tangle_gap:
            return TriggerResult(False, None, 0.0, False, reasons + ["strict: ema20/ema50 too tight -> reject"])

        # 7.2 突破低波动过滤（死鱼盘不追突破）
        if is_breakout:
            if (atr / close) < min_breakout_natr:
                return TriggerResult(False, None, 0.0, False,
                                     reasons + ["strict: volatility too low for breakout -> reject"])

    return TriggerResult(entry_ok, entry_hint, strength, is_breakout, reasons)


def compute_validity_and_risk(
        df_15m: pd.DataFrame,
        df_5m: Optional[pd.DataFrame],
        dir_res: DirectionResult,
        trg_res: TriggerResult,
        regime: Decision,
        position: Optional["PerpPosition"] = None,
) -> ValidityResult:
    reasons: List[str] = []

    if df_15m is None or len(df_15m) < 30:
        return ValidityResult(None, False, False, 0.0, ["15m: insufficient bars"])

    strict = bool(regime.strict_entry)

    close15 = float(df_15m["close"].iloc[-1])
    ema20_15 = float(df_15m["ema_20"].iloc[-1])
    ema50_15 = float(df_15m["ema_50"].iloc[-1])
    atr15 = float(df_15m["atr_14"].iloc[-1])
    if atr15 <= 0:
        return ValidityResult(None, False, False, 0.0, ["15m: ATR invalid (<=0)"])

    # ✅ 有仓位：必须 position != None 且 szi != 0
    has_pos = bool(position is not None and (position.szi is not None) and abs(position.szi) > 0)

    # ----------------------------------------------------------------------
    # A) FLAT
    # ----------------------------------------------------------------------
    if not has_pos:
        if not trg_res.entry_ok:
            return ValidityResult(None, False, False, 0.0, ["flat: no entry -> skip validity"])
        if dir_res.side is None or dir_res.side == Side.NONE:
            return ValidityResult(None, False, False, 0.0, ["flat: no direction"])

        entry_ref = float(trg_res.entry_price_hint or close15)

        k_atr = 1.25 if strict else 1.55
        atr_dist = k_atr * atr15

        # is_breakout：优先字段，否则从 reasons 推断
        if trg_res.is_breakout is not None:
            is_breakout = bool(trg_res.is_breakout)
        else:
            rs = " ".join(getattr(trg_res, "reasons", [])).lower()
            is_breakout = ("breakout" in rs) or ("breakdown" in rs)

        N = 10
        swing_low = float(df_15m["low"].iloc[-N:].min())
        swing_high = float(df_15m["high"].iloc[-N:].max())

        if dir_res.side == Side.LONG:
            atr_sl = entry_ref - atr_dist
            if is_breakout:
                breakout_level = float(trg_res.entry_price_hint or entry_ref)
                struct_sl = max(breakout_level - 0.25 * atr15, ema20_15 - 0.25 * atr15)
            else:
                struct_sl = swing_low - 0.10 * atr15
            stop_price = max(atr_sl, struct_sl)
        else:
            atr_sl = entry_ref + atr_dist
            if is_breakout:
                breakout_level = float(trg_res.entry_price_hint or entry_ref)
                struct_sl = min(breakout_level + 0.25 * atr15, ema20_15 + 0.25 * atr15)
            else:
                struct_sl = swing_high + 0.10 * atr15
            stop_price = min(atr_sl, struct_sl)

        quality = 0.70
        if df_5m is not None and len(df_5m) >= 6 and "atr_14" in df_5m.columns:
            last = df_5m.iloc[-3:]
            drift = float(last["close"].iloc[-1] - last["close"].iloc[0])
            atr5 = float(df_5m["atr_14"].iloc[-1])
            if atr5 > 0:
                if dir_res.side == Side.LONG and drift < -0.35 * atr5:
                    quality -= 0.30
                elif dir_res.side == Side.SHORT and drift > 0.35 * atr5:
                    quality -= 0.30

        if strict and quality < 0.45:
            reasons.append("strict: quality too low -> veto entry")

        quality = max(0.0, min(1.0, quality))
        return ValidityResult(stop_price, False, False, quality, reasons)

    # ----------------------------------------------------------------------
    # B) IN-POSITION
    # ----------------------------------------------------------------------
    assert position is not None
    pos_side = position.side_enum
    # entry_px = float(position.entry_px or 0.0)

    k_trail = 1.10 if strict else 1.35
    trail_dist = k_trail * atr15

    # ✅ 旧 stop 从“内嵌 SL 触发单”读
    old_stop = position_stop_price(position)

    if pos_side == Side.LONG:
        cand1 = ema20_15 - 0.25 * atr15
        cand2 = close15 - trail_dist
        new_stop = max(cand1, cand2)
        stop_price = max(old_stop, new_stop) if old_stop is not None else new_stop
    else:
        cand1 = ema20_15 + 0.25 * atr15
        cand2 = close15 + trail_dist
        new_stop = min(cand1, cand2)
        stop_price = min(old_stop, new_stop) if old_stop is not None else new_stop

    exit_ok = False
    N = 10
    swing_low = float(df_15m["low"].iloc[-N:].min())
    swing_high = float(df_15m["high"].iloc[-N:].max())

    if pos_side == Side.LONG:
        if close15 < (swing_low - 0.05 * atr15): exit_ok = True
        if ema20_15 < ema50_15: exit_ok = True
        if close15 <= stop_price: exit_ok = True
    else:
        if close15 > (swing_high + 0.05 * atr15): exit_ok = True
        if ema20_15 > ema50_15: exit_ok = True
        if close15 >= stop_price: exit_ok = True

    flip_ok = False
    allow_flip = bool(getattr(regime, "allow_flip", False))
    if strict or allow_flip:
        win_len = min(20, len(df_15m) - 1)
        window = df_15m.iloc[-(win_len + 1):-1]
        hh = float(window["high"].max())
        ll = float(window["low"].min())
        pad = 0.15 * atr15
        prev_close = float(df_15m["close"].iloc[-2])

        if pos_side == Side.LONG:
            if exit_ok and (ema20_15 <= ema50_15) and (prev_close > (ll - pad)) and (close15 <= (ll - pad)):
                flip_ok = True
        else:
            if exit_ok and (ema20_15 >= ema50_15) and (prev_close < (hh + pad)) and (close15 >= (hh + pad)):
                flip_ok = True

    quality = 0.65
    if df_5m is not None and len(df_5m) >= 6 and "atr_14" in df_5m.columns:
        last = df_5m.iloc[-3:]
        drift = float(last["close"].iloc[-1] - last["close"].iloc[0])
        atr5 = float(df_5m["atr_14"].iloc[-1])
        if atr5 > 0:
            if pos_side == Side.LONG and drift < -0.40 * atr5:
                quality -= 0.20
            elif pos_side == Side.SHORT and drift > 0.40 * atr5:
                quality -= 0.20

    quality = max(0.0, min(1.0, quality))
    return ValidityResult(stop_price, exit_ok, flip_ok, quality, reasons)


def score_signal(dir_res: DirectionResult, trg_res: TriggerResult, val_res: ValidityResult, regime: Decision) -> Tuple[
    float, List[str]]:
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
    if not regime.allow_trend:
        score *= 0.70
        reasons.append("penalty: trend not allowed")

    if regime.strict_entry:
        reasons.append("strict_entry enabled")

    return score, reasons


@measure_time
def build_signal(
        df_1h: pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m: pd.DataFrame,
        regime: Decision,
        asset_info: PerpAssetInfo,
        position: Optional["PerpPosition"],
        now_ts: float
) -> SignalSnapshot:
    """构建完整的交易信号"""

    dir_res = compute_direction(df_1h, regime)
    trg_res = compute_trigger(df_15m, dir_res, regime)
    val_res = compute_validity_and_risk(df_15m, df_5m, dir_res, trg_res, regime, position)

    score, reasons = score_signal(dir_res, trg_res, val_res, regime)
    entry_threshold = 80.0 if regime.strict_entry else 70.0

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
        ttl_seconds=(45 if regime.strict_entry else 120),
        created_ts=now_ts
    )
