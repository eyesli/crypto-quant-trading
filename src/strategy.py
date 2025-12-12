"""
策略模块

目标：真正做到
  K线 + 技术指标 -> 信号 -> 交易计划（TradePlan）-> 交给执行器下单
"""

from __future__ import annotations

from pprint import pformat
from typing import Any, Dict, Literal, Optional, Tuple

import pandas as pd

from src.config import TIMEFRAME_SETTINGS
from src.market_data import AccountOverview
from src.models import (
    BreakoutSignal,
    ConfidenceEvaluation,
    EdgeDecision,
    MarketDataSnapshot,
    MomentumSignal,
    OverheatSignal,
    PositionSide,
    RegimeDecision,
    RiskAssessment,
    StrategyConfig,
    StructureCostSignal,
    TechnicalLinesSnapshot,
    TradePlan,
    TrendLineSignal,
    TriggerDecision,
    VolatilitySignal,
    VolumeConfirmationSignal,
)
from src.risk import calc_amount_from_risk


def _validate_timeframe_weights(timeframes: list[str]) -> dict[str, float]:
    """
    手动分组+手动权重版本（按你的要求，不做自动归一化）。

    规则：
    - 权重从 TIMEFRAME_SETTINGS[tf].weight 读取
    - 未包含在 TIMEFRAME_SETTINGS 的周期不应该出现在 timeframes
    - 权重总和必须约等于 1.0，否则直接报错（避免 score 尺度悄悄变化）
    """
    weights = {tf: float(TIMEFRAME_SETTINGS[tf].weight) for tf in timeframes}
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(
            f"TIMEFRAME_SETTINGS 权重总和必须为 1.0，当前为 {total:.6f}。"
            f"请在 src/config.py 里手动调整 weight。"
        )
    return weights


def generate_trade_plan(
        account_overview: AccountOverview,
        market_data: MarketDataSnapshot,
        cfg: StrategyConfig,
) -> TradePlan:
    symbol = market_data.symbol or cfg.symbol
    df_map: Dict[str, pd.DataFrame] = market_data.ohlcv_df

    # =========================
    # 0) 多周期“全量”技术线分析
    # =========================
    #
    # 说明：
    # - analyze_technical_lines_single_tf：只产出技术线 signals（不算分）
    # - summarize_technical_lines_to_score：把 signals 汇总成 score/label/regime（统一出口）
    timeframes = list(TIMEFRAME_SETTINGS.keys())  # 由配置决定顺序/分组/权重

    signals_by_tf: Dict[str, TechnicalLinesSnapshot] = {}
    summary_by_tf: Dict[str, Dict[str, Any]] = {}
    score_by_tf: Dict[str, float] = {}

    for tf in timeframes:
        # 根据指标 进一步分析 经济逻辑
        sig: TechnicalLinesSnapshot = analyze_technical_lines_single_tf(df_map.get(tf))
        signals_by_tf[tf] = sig

        summ = summarize_technical_lines_to_score(sig)
        summary_by_tf[tf] = summ
        # summ["score"] 始终存在（数据不足时为 0），这里统一转换成 float
        score_by_tf[tf] = float(summ.get("score") or 0.0)

    # =========================
    # Debug：输出每个 timeframe 的汇总结果（summary_by_tf）
    # =========================
    # 你要求“输出 summary_by_tf 的内容”：这里把每个周期的 score/label/regime/components/detail 打印出来。
    # 如果你后续觉得太吵，可以把这段改成写日志文件或增加一个开关。
    print("\n" + "=" * 100)
    print(f"📌 summary_by_tf ({symbol})")
    for tf in timeframes:
        summ = summary_by_tf.get(tf) or {}
        brief = {
            "score": summ.get("score"),
            "label": summ.get("label"),
            "regime": summ.get("regime"),
            "components": summ.get("components"),
            "detail": summ.get("detail"),
        }
        print(f"\n--- {tf} ---")
        print(pformat(brief, width=120, compact=True))
    print("=" * 100 + "\n")

    # =========================
    # 1) 多周期汇总 score（核心+背景）
    # =========================
    tf_weights = _validate_timeframe_weights(timeframes)
    score = sum(tf_weights[tf] * score_by_tf.get(tf, 0.0) for tf in timeframes)

    tf_score_str = ", ".join([f"{tf}={score_by_tf.get(tf, 0.0):.2f}" for tf in timeframes])
    tf_weight_str = ", ".join([f"{tf}={tf_weights.get(tf, 0.0):.2f}" for tf in timeframes])

    ticker = market_data.metrics.ticker or {}
    last = ticker.get("last")
    last_px = float(last) if last is not None else _last_close(df_map.get("1m")) or _last_close(df_map.get("1h"))
    if last_px is None:
        return TradePlan(symbol=symbol, action="HOLD", reason="无法获取当前价格", score=score)

    atr = _last_atr(df_map.get("1h")) or _last_atr(df_map.get("4h")) or _last_atr(df_map.get("1d"))
    spread_bps = float(market_data.metrics.spread_bps or 0.0)
    ob_imb = float(market_data.metrics.order_book_imbalance or 0.0)
    pos_side, pos_size = _current_position(account_overview, symbol)
    trigger_long, trigger_short = _entry_trigger_1m(df_map.get("1m"))
    equity = _equity_usdc(account_overview)

    regime = _evaluate_regime(signals_by_tf, summary_by_tf, tf_weights)
    edge = _evaluate_edge(score, score_by_tf, tf_weights, regime)
    confidence = _evaluate_confidence(signals_by_tf, score_by_tf, edge)
    risk_assessment = _assess_risk(
        cfg=cfg,
        equity=equity,
        edge=edge,
        atr=atr,
        last_px=last_px,
        spread_bps=spread_bps,
        ob_imb=ob_imb,
    )
    trigger = _decide_trigger(
        pos_side=pos_side,
        pos_size=pos_size,
        edge=edge,
        confidence=confidence,
        risk=risk_assessment,
        desired_size=risk_assessment.position_size,
        trigger_long=trigger_long,
        trigger_short=trigger_short,
        cfg=cfg,
    )

    # 最终行动
    if not trigger.ready:
        return TradePlan(symbol=symbol, action="HOLD", reason=trigger.reason, score=score)

    def build_open(direction: PositionSide, amount: float | None = None) -> TradePlan:
        sl = risk_assessment.stop_loss
        tp = risk_assessment.take_profit
        return TradePlan(
            symbol=symbol,
            action="OPEN",
            direction=direction,
            order_type="market",
            entry_price=None,
            open_amount=float(amount if amount is not None else risk_assessment.position_size),
            stop_loss=sl,
            take_profit=tp,
            reason=(
                f"Regime={regime.regime}({regime.confidence:.2f}); "
                f"Edge={edge.direction}({edge.edge_score:.2f}); "
                f"Confidence={confidence.quality}({confidence.confidence_score:.2f}); "
                f"Risk={risk_assessment.reason}; scores[{tf_score_str}]; weights[{tf_weight_str}]"
            ),
            score=float(score),
        )

    def build_add(direction: PositionSide, add_amount: float, reason: str) -> TradePlan:
        return TradePlan(
            symbol=symbol,
            action="ADD",
            direction=direction,
            order_type="market",
            open_amount=float(max(add_amount, 0.0)),
            stop_loss=risk_assessment.stop_loss,
            take_profit=risk_assessment.take_profit,
            reason=reason,
            score=float(score),
        )

    def build_close(reason: str) -> TradePlan:
        return TradePlan(
            symbol=symbol,
            action="CLOSE",
            direction=pos_side if pos_side in ("long", "short") else None,
            close_amount=float(pos_size or 0.0),
            reason=reason,
            score=float(score),
        )

    def build_flip(new_dir: PositionSide) -> TradePlan:
        open_plan = build_open(new_dir)
        return TradePlan(
            symbol=symbol,
            action="FLIP",
            close_direction=pos_side,
            direction=new_dir,
            order_type=open_plan.order_type,
            entry_price=open_plan.entry_price,
            close_amount=float(pos_size or 0.0),
            open_amount=open_plan.open_amount,
            stop_loss=open_plan.stop_loss,
            take_profit=open_plan.take_profit,
            reason=f"反手：{trigger.reason}; " + open_plan.reason,
            score=open_plan.score,
        )

    # 核心规模参考
    target_size = float(risk_assessment.position_size)
    min_gap = max(target_size * cfg.scale_in_min_gap_pct, 0.0)
    scale_step = max(target_size * cfg.scale_in_step_pct, 0.0)
    over_target_line = target_size * (1 + cfg.reduce_over_target_pct)
    reduce_step = max(target_size * cfg.reduce_step_pct, 0.0)

    if pos_side == "flat":
        if edge.direction == "long" and trigger_long:
            return build_open("long")
        if edge.direction == "short" and trigger_short:
            return build_open("short")
        return TradePlan(symbol=symbol, action="HOLD", reason="无有效入场触发", score=score)

    if pos_side == "long" and edge.direction == "short" and trigger_short:
        return build_flip("short")
    if pos_side == "short" and edge.direction == "long" and trigger_long:
        return build_flip("long")

    if pos_side == edge.direction:
        # 分步加仓：只有当“目标仓位-现有仓位”达到缺口阈值，且分数/质量达标
        if (
            target_size > 0
            and target_size - pos_size > min_gap
            and edge.edge_score >= cfg.min_score_to_add
            and confidence.quality != "low"
        ):
            add_amt = min(target_size - pos_size, scale_step)
            return build_add(
                direction=edge.direction,
                add_amount=add_amt,
                reason=(
                    f"分步加仓：目标仓位={target_size:.4f}, 现有={pos_size:.4f}, 缺口={target_size - pos_size:.4f}; "
                    f"Edge={edge.edge_score:.2f}, Confidence={confidence.confidence_score:.2f}"
                ),
            )

        # 减仓：当实际仓位明显超出风险建议仓位时，先砍掉超额的一半
        if pos_size > over_target_line and reduce_step > 0:
            reduce_amt = min(pos_size - target_size, reduce_step)
            return TradePlan(
                symbol=symbol,
                action="REDUCE",
                direction=pos_side,
                close_amount=float(max(reduce_amt, 0.0)),
                stop_loss=risk_assessment.stop_loss,
                take_profit=risk_assessment.take_profit,
                reason=(
                    f"仓位超出风险预算：当前={pos_size:.4f} > 目标={target_size:.4f}，减仓 {reduce_amt:.4f}"
                ),
                score=float(score),
            )

    if pos_side == "long" and score < -0.2:
        return build_close("多头衰减，执行平仓")
    if pos_side == "short" and score > 0.2:
        return build_close("空头衰减，执行平仓")

    return TradePlan(symbol=symbol, action="HOLD", reason="持仓中，信号不足以调整", score=score)


def analyze_technical_lines_single_tf(df: Optional[pd.DataFrame]) -> TechnicalLinesSnapshot:
    """
    ✅ 只做“技术线分析”，不在这里做总分计算。
    这里产出的是“结构化信号/特征”，方便你：
    - 把每条技术线单独可视化/打印
    - 在汇总器里统一调权重/加规则
    - 回测时逐条分析哪条技术线贡献最大
    """
    if df is None or len(df) == 0:
        return TechnicalLinesSnapshot(ok=False, notes=("df 为空",))

    # 只要求 close 必须存在；其他列按“有就用、没有就跳过”
    if "close" not in df.columns:
        return TechnicalLinesSnapshot(ok=False, notes=("缺少 close 列",))

    df2 = df.copy()
    df2 = df2.dropna(subset=["close"])  # 把 close 列为 NaN（缺失值）的那些行删掉
    if len(df2) < 30:
        return TechnicalLinesSnapshot(ok=False, notes=("有效K线太少",))

    row = df2.iloc[-1]  # 最后一行（最新一根 K 线/最新一条记录）
    prev = df2.iloc[-2]  # 倒数第二行（上一根 K 线/上一条记录）

    close = float(row["close"])

    notes: list[str] = []

    def has(col: str) -> bool:
        return col in df2.columns and pd.notna(row.get(col))

    # -------------------------
    # 1) 趋势方向：均线位置 + 斜率
    # 均线≈一段时间的“平均成交成本/共识价格”
    # EMA50 对最近价格权重更大，反映“近期市场共识成本线”；SMA50 更平滑，反映“中期平均”。
    # 价格在均线上方：说明市场愿意以高于“平均成本线”的价格成交，买盘更强，常对应上升趋势或至少偏多结构。
    # 价格在均线下方：说明市场成交价格低于平均成本线，卖盘更强，常对应下降趋势。
    # 偏离比例（bias）：相当于把“离均线多远”标准化成百分比，偏离越大通常意味着趋势越强，但也可能更“过热”（所以后面会配合 RSI/波动等做过滤或惩罚）。
    # EMA50 vs SMA50：EMA 更敏感，如果 EMA50 长期在 SMA50 上方，往往意味着“近期价格持续高于中期平均”，是一种趋势确认；反之亦然。
    # -------------------------
    trend = TrendLineSignal()
    if has("ema_50") and has("sma_50"):
        ema = float(row["ema_50"])
        sma = float(row["sma_50"])
        bias_ema = (close - ema) / ema if ema else 0.0
        ema_gt_sma = ema > sma

        # 均线斜率（近 5 根）：用来判断“趋势是否在加速/衰减”
        ema_slope_5 = None
        if "ema_50" in df2.columns and len(df2) >= 6 and pd.notna(df2["ema_50"].iloc[-6]):
            ema_prev5 = float(df2["ema_50"].iloc[-6])
            ema_slope_5 = (ema - ema_prev5) / ema_prev5 if ema_prev5 else 0.0

        trend = TrendLineSignal(
            ema_50=ema,
            sma_50=sma,
            bias_to_ema=bias_ema,  # close 相对 EMA50 的偏离比例
            ema_gt_sma=ema_gt_sma,
            ema_slope_5=ema_slope_5,
        )
        if bias_ema > 0.004:
            notes.append(f"价格在EMA50上方({bias_ema:.2%})")
        elif bias_ema < -0.004:
            notes.append(f"价格在EMA50下方({bias_ema:.2%})")
        notes.append("EMA50 > SMA50" if ema_gt_sma else "EMA50 < SMA50")
        if ema_slope_5 is not None:
            if ema_slope_5 > 0.002:
                notes.append("EMA50 上行")
            elif ema_slope_5 < -0.002:
                notes.append("EMA50 下行")

    # -------------------------
    # 2) 动能：MACD 柱体方向 + 变化
    # MACD 柱体可以粗略理解为“短周期动能 - 长周期动能”，柱体越大代表动能越强。
    # 这里不算分，只输出方向与是否增强/衰减。
    # -------------------------
    momentum = MomentumSignal()
    if has("macd_hist") and pd.notna(prev.get("macd_hist")):
        macd = float(row.get("macd_hist") or 0.0)
        macd_prev = float(prev.get("macd_hist") or 0.0)
        momentum = MomentumSignal(
            macd_hist=macd,
            macd_hist_prev=macd_prev,
            direction=1 if macd > 0 else -1 if macd < 0 else 0,
            strengthening=abs(macd) > abs(macd_prev),
            weakening=abs(macd) < abs(macd_prev),
        )
        notes.append("MACD柱>0" if macd > 0 else "MACD柱<0" if macd < 0 else "MACD柱=0")
        if abs(macd) > abs(macd_prev) and abs(macd) > 0:
            notes.append("动能增强")
        elif abs(macd) < abs(macd_prev) and abs(macd_prev) > 0:
            notes.append("动能衰减")

    # -------------------------
    # 3) 趋势强度：ADX
    # ADX 不看多空方向，只看“有没有趋势”。ADX 高：更适合趋势策略；ADX 低：更像震荡/均值回归。
    # -------------------------
    adx = float(row.get("adx_14") or 0.0) if "adx_14" in df2.columns else 0.0
    if adx:
        if adx >= 28:
            notes.append(f"ADX={adx:.1f} 强趋势")
        elif adx <= 18:
            notes.append(f"ADX={adx:.1f} 偏震荡")
        else:
            notes.append(f"ADX={adx:.1f} 中性")

    # -------------------------
    # 4) 突破质量：新鲜度 + 放量
    # 突破“新鲜度”很重要：prev 没突破、row 才突破 = 新事件；否则只是延续，不该重复当成“突破信号”。
    # -------------------------
    breakout = BreakoutSignal()
    if "breakout_up" in df2.columns and "breakout_down" in df2.columns and has("vol_spike_ratio"):
        bu = int(row.get("breakout_up") or 0)
        bd = int(row.get("breakout_down") or 0)
        bu_prev = int(prev.get("breakout_up") or 0)
        bd_prev = int(prev.get("breakout_down") or 0)
        vol = float(row.get("vol_spike_ratio") or 0.0)
        fresh_up = bu == 1 and bu_prev == 0
        fresh_down = bd == 1 and bd_prev == 0
        breakout = BreakoutSignal(
            breakout_up=bu,
            breakout_down=bd,
            fresh_up=fresh_up,
            fresh_down=fresh_down,
            vol_spike_ratio=vol,
        )
        if fresh_up and vol >= 1.5:
            notes.append(f"新突破向上+放量({vol:.2f}x)")
        elif fresh_down and vol >= 1.5:
            notes.append(f"新跌破向下+放量({vol:.2f}x)")
        else:
            if bu == 1 and vol >= 1.5:
                notes.append(f"突破后延续({vol:.2f}x)")
            if bd == 1 and vol >= 1.5:
                notes.append(f"跌破后延续({vol:.2f}x)")

    # -------------------------
    # 5) 波动状态：布林带宽度（挤压/扩张）
    # 交易逻辑：挤压期更容易“假信号/来回打脸”，扩张期更容易“顺势走一段”。
    # -------------------------
    volatility = VolatilitySignal()
    if "bb_width" in df2.columns and pd.notna(row.get("bb_width")):
        w = df2["bb_width"].dropna()
        if len(w) >= 50:
            window = w.iloc[-120:] if len(w) >= 120 else w
            cur = float(row["bb_width"])
            p20 = float(window.quantile(0.2))
            p80 = float(window.quantile(0.8))
            squeeze = cur <= p20
            expansion = cur >= p80
            volatility = VolatilitySignal(bb_width=cur, p20=p20, p80=p80, squeeze=squeeze, expansion=expansion)
            if squeeze:
                notes.append("布林带挤压")
            elif expansion:
                notes.append("布林带扩张")

    # -------------------------
    # 6) 过热：RSI 极端（这里只输出，不在这里惩罚分数）
    # 交易逻辑：趋势里 RSI 可以长期高/低；但极端值往往意味着“追单风险上升”。
    # -------------------------
    overheat = OverheatSignal()
    if "rsi_14" in df2.columns and pd.notna(row.get("rsi_14")):
        rsi = float(row.get("rsi_14") or 50.0)
        overheat = OverheatSignal(rsi_14=rsi, overbought=rsi >= 72, oversold=rsi <= 28)
        if rsi >= 72:
            notes.append(f"RSI={rsi:.0f} 过热")
        elif rsi <= 28:
            notes.append(f"RSI={rsi:.0f} 极弱")

    # -------------------------
    # 7) 价量确认：OBV 方向（如果有）
    # OBV 上行≈资金净流入偏多；OBV 下行≈资金净流出偏空（非常粗糙，但能做确认项）。
    # -------------------------
    volume = VolumeConfirmationSignal()
    if "obv" in df2.columns and pd.notna(row.get("obv")) and len(df2["obv"].dropna()) >= 10:
        obv = df2["obv"].dropna()
        obv_now = float(obv.iloc[-1])
        obv_prev = float(obv.iloc[-6]) if len(obv) >= 6 else float(obv.iloc[0])
        delta = obv_now - obv_prev
        volume = VolumeConfirmationSignal(
            obv_now=obv_now,
            obv_prev5=obv_prev,
            obv_delta_5=delta,
            direction=1 if delta > 0 else -1 if delta < 0 else 0,
        )
        notes.append("OBV 上行" if delta > 0 else "OBV 下行" if delta < 0 else "OBV 走平")

    # -------------------------
    # 8) 成本/结构：AVWAP、POC（如果有）
    # AVWAP≈整段数据锚定的成交量加权成本线；POC≈成交最密集的价格区域（筹码密集区）。
    # -------------------------
    structure = StructureCostSignal()
    if "avwap_full" in df2.columns and pd.notna(row.get("avwap_full")):
        avwap = float(row.get("avwap_full"))
        if avwap:
            bias = (close - avwap) / avwap
            structure = StructureCostSignal(
                avwap_full=avwap,
                bias_to_avwap=bias,
                price_to_poc_pct=structure.price_to_poc_pct,
            )
            if bias > 0.008:
                notes.append(f"高于AVWAP({bias:.2%})")
            elif bias < -0.008:
                notes.append(f"低于AVWAP({bias:.2%})")

    if "price_to_poc_pct" in df2.columns and pd.notna(row.get("price_to_poc_pct")):
        d = float(row.get("price_to_poc_pct") or 0.0)
        structure = StructureCostSignal(
            avwap_full=structure.avwap_full,
            bias_to_avwap=structure.bias_to_avwap,
            price_to_poc_pct=d,
        )
        if abs(d) > 0.02:
            notes.append(f"偏离POC较大({d:.2%})")

    return TechnicalLinesSnapshot(
        ok=True,
        close=close,
        adx=adx,
        trend=trend,
        momentum=momentum,
        breakout=breakout,
        volatility=volatility,
        overheat=overheat,
        volume=volume,
        structure=structure,
        notes=tuple(notes),
    )


def summarize_technical_lines_to_score(signals: TechnicalLinesSnapshot) -> Dict[str, Any]:
    """
    ✅ 汇总器：把“技术线分析结果”统一转换成 score/label/regime。

    注意：这一步才做“打分”，方便你把所有技术线都看完后，统一调整权重与规则。
    """
    if not signals.ok:
        return {
            "score": 0.0,
            "label": "数据不足",
            "regime": "mixed",
            "detail": "；".join(signals.notes or ("数据不足",)),
            "components": {},
        }

    close = float(signals.close or 0.0)
    _ = close  # close 目前只用于解释/扩展；保留变量方便你未来加规则

    notes = list(signals.notes or ())
    components: Dict[str, float] = {}

    # 先从各条技术线提取“方向/强弱”的结构化信息
    trend = signals.trend
    momentum = signals.momentum
    breakout = signals.breakout
    volatility = signals.volatility
    overheat = signals.overheat
    volume = signals.volume
    structure = signals.structure
    adx = float(signals.adx or 0.0)

    score = 0.0

    # -------------------------
    # 1) 趋势线（均线）
    # -------------------------
    c = 0.0
    bias = float(trend.bias_to_ema or 0.0)
    ema_gt_sma = trend.ema_gt_sma
    ema_slope_5 = trend.ema_slope_5

    if bias > 0.004:
        c += 0.25
    elif bias < -0.004:
        c -= 0.25

    if ema_gt_sma is True:
        c += 0.12
    elif ema_gt_sma is False:
        c -= 0.12

    if isinstance(ema_slope_5, (int, float)):
        if ema_slope_5 > 0.002:
            c += 0.10
        elif ema_slope_5 < -0.002:
            c -= 0.10

    components["trend"] = c
    score += c

    # -------------------------
    # 2) 动能线（MACD）
    # -------------------------
    c = 0.0
    macd_dir = int(momentum.direction or 0)
    strengthening = bool(momentum.strengthening)
    weakening = bool(momentum.weakening)
    if macd_dir > 0:
        c += 0.16
    elif macd_dir < 0:
        c -= 0.16
    if strengthening and macd_dir != 0:
        c += 0.05 if macd_dir > 0 else -0.05
    if weakening and macd_dir != 0:
        c -= 0.03 if macd_dir > 0 else -0.03

    components["momentum"] = c
    score += c

    # -------------------------
    # 3) 趋势强度线（ADX）——用于 regime，同时对 score 做轻微校准
    # -------------------------
    if adx >= 28:
        components["trend_strength"] = 0.06
        score += 0.06
    elif 0 < adx <= 18:
        components["trend_strength"] = -0.06
        score -= 0.06
    else:
        components["trend_strength"] = 0.0

    # -------------------------
    # 4) 突破线（新鲜度+放量）
    # -------------------------
    c = 0.0
    fresh_up = bool(breakout.fresh_up)
    fresh_down = bool(breakout.fresh_down)
    vol = float(breakout.vol_spike_ratio or 0.0)
    bu = int(breakout.breakout_up or 0)
    bd = int(breakout.breakout_down or 0)

    if fresh_up and vol >= 1.5:
        c += 0.18
    elif fresh_down and vol >= 1.5:
        c -= 0.18
    else:
        if bu == 1 and vol >= 1.5:
            c += 0.06
        if bd == 1 and vol >= 1.5:
            c -= 0.06

    components["breakout"] = c
    score += c

    # -------------------------
    # 5) 波动线（布林带宽度）
    # -------------------------
    c = 0.0
    squeeze = bool(volatility.squeeze)
    expansion = bool(volatility.expansion)
    if squeeze:
        # 挤压期更容易“来回打脸”，这里弱化整体分数（相当于降低置信度）
        c -= 0.05
        score *= 0.85
    elif expansion:
        c += 0.05
    components["vol_regime"] = c
    score += c

    # -------------------------
    # 6) 过热线（RSI）——只做“追单风险”矫正
    # -------------------------
    c = 0.0
    if overheat.rsi_14 is not None:
        overbought = bool(overheat.overbought)
        oversold = bool(overheat.oversold)
        # 只在 score 指向同方向时惩罚（防追涨/追跌）
        if score > 0.15 and overbought:
            c -= 0.08
        if score < -0.15 and oversold:
            c += 0.08  # 做空时 RSI 极低，意味着追空风险↑，因此往 0 拉回
    components["overheat"] = c
    score += c

    # -------------------------
    # 7) 价量线（OBV）——确认项
    # -------------------------
    c = 0.0
    obv_dir = int(volume.direction or 0)
    if obv_dir > 0:
        c += 0.04
    elif obv_dir < 0:
        c -= 0.04
    components["obv"] = c
    score += c

    # -------------------------
    # 8) 成本/结构线（AVWAP / POC）——确认+风险提示
    # -------------------------
    c = 0.0
    bias_avwap = structure.bias_to_avwap
    if isinstance(bias_avwap, (int, float)):
        if bias_avwap > 0.008:
            c += 0.06
        elif bias_avwap < -0.008:
            c -= 0.06
    components["avwap"] = c
    score += c

    poc_d = structure.price_to_poc_pct
    if isinstance(poc_d, (int, float)) and abs(poc_d) > 0.02:
        # 距离 POC 太远时，追单风险上升，轻微往 0 拉回
        score *= 0.92

    # clamp
    score = max(min(float(score), 1.0), -1.0)

    # regime + label
    if adx >= 25:
        regime = "trend"
    elif 0 < adx <= 18:
        regime = "range"
    else:
        regime = "mixed"

    if score >= 0.6:
        label = "强多头趋势"
    elif score >= 0.2:
        label = "偏多 / 弱趋势"
    elif score > -0.2:
        label = "震荡 / 中性"
    elif score > -0.6:
        label = "偏空 / 弱趋势"
    else:
        label = "强空头趋势"

    return {"score": score, "label": label, "regime": regime, "detail": "；".join(notes), "components": components}


def _evaluate_regime(
        signals_by_tf: Dict[str, TechnicalLinesSnapshot],
        summary_by_tf: Dict[str, Dict[str, Any]],
        tf_weights: Dict[str, float],
) -> RegimeDecision:
    regime_map = {"trend": 1.0, "range": -1.0, "mixed": 0.0}
    weighted = 0.0
    total_weight = 0.0
    drivers: list[str] = []

    for tf, summ in summary_by_tf.items():
        weight = tf_weights.get(tf, 0.0)
        regime = summ.get("regime")
        val = regime_map.get(regime, 0.0)
        weighted += val * weight
        total_weight += weight
        sig = signals_by_tf.get(tf)
        if sig and isinstance(sig.adx, (int, float)):
            drivers.append(f"{tf}:ADX={sig.adx:.1f}->{regime}")

    norm = weighted / total_weight if total_weight else 0.0
    confidence = abs(norm)
    if norm > 0.05:
        regime_label = "trend"
    elif norm < -0.05:
        regime_label = "range"
    else:
        regime_label = "mixed"

    return RegimeDecision(regime=regime_label, confidence=float(confidence), drivers="; ".join(drivers))


def _evaluate_edge(
        score: float,
        score_by_tf: Dict[str, float],
        tf_weights: Dict[str, float],
        regime: RegimeDecision,
) -> EdgeDecision:
    direction: PositionSide = "flat"
    if score >= 0.2:
        direction = "long"
    elif score <= -0.2:
        direction = "short"

    align_weight = 0.0
    total_weight = 0.0
    for tf, tf_score in score_by_tf.items():
        w = tf_weights.get(tf, 0.0)
        total_weight += w
        if direction == "long" and tf_score > 0:
            align_weight += w
        elif direction == "short" and tf_score < 0:
            align_weight += w
        elif direction == "flat" and abs(tf_score) < 0.1:
            align_weight += w * 0.5
    alignment = align_weight / total_weight if total_weight else 0.0

    rationale_parts = [f"总分={score:.2f}", f"Regime={regime.regime}({regime.confidence:.2f})"]
    rationale_parts.append(f"多空一致性={alignment:.2f}")
    return EdgeDecision(direction=direction, edge_score=float(score), alignment=float(alignment), rationale="; ".join(rationale_parts))


def _evaluate_confidence(
        signals_by_tf: Dict[str, TechnicalLinesSnapshot],
        score_by_tf: Dict[str, float],
        edge: EdgeDecision,
) -> ConfidenceEvaluation:
    if edge.direction == "flat":
        return ConfidenceEvaluation(quality="low", confidence_score=0.25, notes="无明显方向优势")

    ok_tfs = [tf for tf, sig in signals_by_tf.items() if sig.ok]
    data_quality = len(ok_tfs) / max(len(signals_by_tf), 1)

    sign_match = 0.0
    for tf, val in score_by_tf.items():
        if edge.direction == "long" and val > 0:
            sign_match += 1
        elif edge.direction == "short" and val < 0:
            sign_match += 1
    alignment = sign_match / max(len(score_by_tf), 1)

    momentum_confirm = 0.0
    for sig in signals_by_tf.values():
        if sig.momentum and sig.momentum.direction:
            if edge.direction == "long" and sig.momentum.direction > 0:
                momentum_confirm += 1
            if edge.direction == "short" and sig.momentum.direction < 0:
                momentum_confirm += 1
    momentum_factor = momentum_confirm / max(len(signals_by_tf), 1)

    confidence_score = 0.4 * data_quality + 0.35 * alignment + 0.25 * momentum_factor
    if confidence_score >= 0.65:
        quality: Literal["high", "medium", "low"] = "high"
    elif confidence_score >= 0.45:
        quality = "medium"
    else:
        quality = "low"

    notes = (
        f"数据覆盖={data_quality:.2f}; 多空一致性={alignment:.2f}; "
        f"动能确认={momentum_factor:.2f}"
    )
    return ConfidenceEvaluation(quality=quality, confidence_score=float(confidence_score), notes=notes)


def _assess_risk(
        cfg: StrategyConfig,
        equity: float,
        edge: EdgeDecision,
        atr: Optional[float],
        last_px: float,
        spread_bps: float,
        ob_imb: float,
) -> RiskAssessment:
    if edge.direction == "flat":
        return RiskAssessment(allowed=False, reason="无方向优势，跳过")
    if spread_bps and spread_bps > 12:
        return RiskAssessment(allowed=False, reason=f"点差过大({spread_bps:.1f}bps)")
    if atr is None or atr <= 0:
        return RiskAssessment(allowed=False, reason="ATR 不足，无法设置风控")
    if equity <= 0:
        return RiskAssessment(allowed=False, reason="账户权益不足")

    if edge.direction == "long":
        sl = last_px - cfg.atr_stop_mult * atr
        tp = last_px + cfg.atr_tp_mult * atr
    else:
        sl = last_px + cfg.atr_stop_mult * atr
        tp = last_px - cfg.atr_tp_mult * atr

    sizing = calc_amount_from_risk(
        equity=equity,
        risk_pct=cfg.risk_pct,
        entry_price=last_px,
        stop_loss=sl,
        leverage=cfg.leverage,
    )

    reason_parts = [f"ATR={atr:.2f}", f"点差={spread_bps:.1f}bps", f"OB倾斜={ob_imb:.2f}"]
    return RiskAssessment(
        allowed=True,
        reason="; ".join(reason_parts),
        stop_loss=float(sl),
        take_profit=float(tp),
        position_size=float(sizing.amount),
    )


def _decide_trigger(
        pos_side: PositionSide,
        pos_size: float,
        edge: EdgeDecision,
        confidence: ConfidenceEvaluation,
        risk: RiskAssessment,
        desired_size: float,
        trigger_long: bool,
        trigger_short: bool,
        cfg: StrategyConfig,
) -> TriggerDecision:
    if not risk.allowed:
        return TriggerDecision(ready=False, reason=risk.reason)
    if confidence.quality == "low":
        return TriggerDecision(ready=False, reason=f"信号质量偏低：{confidence.notes}")
    if edge.direction == "long" and not trigger_long:
        return TriggerDecision(ready=False, reason="缺少多头触发")
    if edge.direction == "short" and not trigger_short:
        return TriggerDecision(ready=False, reason="缺少空头触发")
    if pos_side == edge.direction and pos_size > 0:
        gap = max(desired_size - pos_size, 0.0)
        if gap <= max(desired_size * cfg.scale_in_min_gap_pct, 0.0):
            return TriggerDecision(ready=False, reason="已有同向仓位，未触发加仓缺口")
    if edge.edge_score < cfg.min_score_to_open:
        return TriggerDecision(ready=False, reason="总分未达到入场阈值")

    return TriggerDecision(ready=True, reason="阶段化判断全部通过")


def _equity_usdc(account: AccountOverview) -> float:
    total = (account.balances or {}).get("total", {}) or {}
    return float(total.get("USDC") or total.get("USDT") or 0.0)


def _current_position(account: AccountOverview, symbol: str) -> Tuple[PositionSide, float]:
    """
    从 ccxt.fetch_positions 里提取该 symbol 的仓位方向与数量（contracts）。
    """
    for pos in account.positions or []:
        if pos.get("symbol") != symbol:
            continue
        side = pos.get("side")  # 'long'/'short'
        contracts = pos.get("contracts") or 0
        try:
            qty = abs(float(contracts))
        except Exception:
            qty = 0.0
        if side in ("long", "short") and qty > 0:
            return side, qty
    return "flat", 0.0


def _last_close(df: Optional[pd.DataFrame]) -> Optional[float]:
    if df is None or len(df) == 0:
        return None
    x = df["close"].dropna()
    if len(x) == 0:
        return None
    return float(x.iloc[-1])


def _last_atr(df: Optional[pd.DataFrame]) -> Optional[float]:
    if df is None or len(df) == 0 or "atr_14" not in df.columns:
        return None
    x = df["atr_14"].dropna()
    if len(x) == 0:
        return None
    return float(x.iloc[-1])


def _entry_trigger_1m(df_1m: Optional[pd.DataFrame]) -> Tuple[bool, bool]:
    """
    1m 级别入场触发：
    - breakout_up_with_vol 从 0 -> 1 视为 long trigger
    - breakout_down + 放量 从 0 -> 1 视为 short trigger
    同时辅以 MACD 柱翻正/翻负。
    """
    if df_1m is None or len(df_1m) < 3:
        return False, False
    df = df_1m.dropna(subset=["close", "macd_hist", "vol_spike_ratio"]).copy()
    if len(df) < 3:
        return False, False

    row = df.iloc[-1]
    prev = df.iloc[-2]

    macd = float(row.get("macd_hist") or 0.0)
    macd_prev = float(prev.get("macd_hist") or 0.0)
    macd_up = macd > 0 and macd_prev <= 0
    macd_down = macd < 0 and macd_prev >= 0

    buv = int(row.get("breakout_up_with_vol") or 0)
    buv_prev = int(prev.get("breakout_up_with_vol") or 0)
    bdown = int(row.get("breakout_down") or 0)
    bdown_prev = int(prev.get("breakout_down") or 0)
    vol_ok = float(row.get("vol_spike_ratio") or 0.0) > 1.8

    long_trigger = (buv == 1 and buv_prev == 0) or (macd_up and vol_ok)
    short_trigger = ((bdown == 1 and bdown_prev == 0) and vol_ok) or (macd_down and vol_ok)
    return bool(long_trigger), bool(short_trigger)
