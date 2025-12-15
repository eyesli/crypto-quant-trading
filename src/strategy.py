"""
策略模块

目标：真正做到
  K线 + 技术指标 -> 信号 -> 交易计划（TradePlan）-> 交给执行器下单
"""

from __future__ import annotations

from pprint import pformat
from typing import Any, Dict, Optional, Tuple, Literal, List

import pandas as pd

from src.config import TIMEFRAME_SETTINGS
from src.market_data import AccountOverview, BaseRegime
from src.models import (
    MarketDataSnapshot,
    BreakoutSignal,
    MomentumSignal,
    OverheatSignal,
    PositionSide,
    StrategyConfig,
    StructureCostSignal,
    TechnicalLinesSnapshot,
    TradePlan,
    TrendLineSignal,
    VolatilitySignal,
    VolumeConfirmationSignal, OrderBookInfo, Decision, Action, MarketRegime, VolState,
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

    # 方便你调试：把每个周期的分数串起来（证明“确实拿全了”）
    tf_score_str = ", ".join([f"{tf}={score_by_tf.get(tf, 0.0):.2f}" for tf in timeframes])
    tf_weight_str = ", ".join([f"{tf}={tf_weights.get(tf, 0.0):.2f}" for tf in timeframes])

    ticker = market_data.metrics.ticker or {}
    last = ticker.get("last")
    last_px = float(last) if last is not None else _last_close(df_map.get("1m")) or _last_close(df_map.get("1h"))
    if last_px is None:
        return TradePlan(symbol=symbol, action="HOLD", reason="无法获取当前价格", score=score)

    pos_side, pos_size = _current_position(account_overview, symbol)

    # 1m 触发：避免每根 bar 都重复触发
    trigger_long, trigger_short = _entry_trigger_1m(df_map.get("1m"))

    # 盘口微观结构（可选加分/过滤）
    ob_imb = float(market_data.metrics.order_book_imbalance or 0.0)
    spread_bps = float(market_data.metrics.spread_bps or 0.0)

    # 基础过滤：点差太大直接不做（防止流动性差时误触发）
    if spread_bps and spread_bps > 12:
        return TradePlan(symbol=symbol, action="HOLD", reason=f"点差过大({spread_bps:.1f}bps)，跳过", score=score)

    # todo 初始止损 = 1.0 × ATR(1h)
    #
    # 浮盈 > 1.0 × ATR → 止损上移至 breakeven
    # 浮盈 > 2.0 × ATR → Trailing = 1.0 × ATR

    # 用 1h ATR 设定止损止盈（如果缺失则降级用 4h/1d）
    atr = _last_atr(df_map.get("1h")) or _last_atr(df_map.get("4h")) or _last_atr(df_map.get("1d"))
    if atr is None or atr <= 0:
        return TradePlan(symbol=symbol, action="HOLD", reason="ATR 不足，无法设置风控", score=score)

    # --- 决策：开仓 / 平仓 / 反手 ---
    long_bias = score >= cfg.min_score_to_open and trigger_long
    short_bias = score <= -cfg.min_score_to_open and trigger_short

    # 盘口倾斜可作为“加分确认”
    if long_bias and ob_imb < -0.15:
        long_bias = False
    if short_bias and ob_imb > 0.15:
        short_bias = False

    # 计算账户权益
    equity = _equity_usdc(account_overview)
    if equity <= 0:
        return TradePlan(symbol=symbol, action="HOLD", reason="权益为 0，跳过", score=score)

    # 目标：开仓数量（根据 stop 距离风险定仓）
    def build_open(direction: PositionSide) -> TradePlan:
        if direction == "long":
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
        # 这里把“全周期得分 + 权重”放进 reason，方便你复盘为什么会开仓
        reason = f"score={score:.2f}"
        reason += f" [scores: {tf_score_str}]"
        reason += f" [weights: {tf_weight_str}]"
        reason += f"，ATR={atr:.2f}，OB_imb={ob_imb:.2f}"
        return TradePlan(
            symbol=symbol,
            action="OPEN",
            direction=direction,
            order_type="market",
            entry_price=None,
            open_amount=float(sizing.amount),
            stop_loss=float(sl),
            take_profit=float(tp),
            reason=reason,
            score=float(score),
        )

    # 平仓计划：数量用当前仓位数量（如果拿不到就用 0，执行器会跳过）
    def build_close() -> TradePlan:
        return TradePlan(
            symbol=symbol,
            action="CLOSE",
            direction=pos_side if pos_side in ("long", "short") else None,
            close_amount=float(pos_size or 0.0),
            reason=f"趋势反转/衰减：score={score:.2f} [{tf_score_str}]",
            score=float(score),
        )

    # 反手：先平后开（执行器会先发 reduceOnly 市价再开仓）
    def build_flip(new_dir: PositionSide) -> TradePlan:
        open_plan = build_open(new_dir)
        return TradePlan(
            symbol=symbol,
            action="FLIP",
            close_direction=pos_side,  # 先平旧方向
            direction=new_dir,  # 再开新方向
            order_type=open_plan.order_type,
            entry_price=open_plan.entry_price,
            close_amount=float(pos_size or 0.0),
            open_amount=open_plan.open_amount,
            stop_loss=open_plan.stop_loss,
            take_profit=open_plan.take_profit,
            reason=f"反手：pos={pos_side} -> {new_dir}，" + open_plan.reason,
            score=open_plan.score,
        )

    if pos_side == "flat":
        if long_bias:
            return build_open("long")
        if short_bias:
            return build_open("short")
        return TradePlan(symbol=symbol, action="HOLD", reason="无有效入场触发", score=score)

    # 已持仓：反手优先
    if pos_side == "long" and score <= -cfg.min_score_to_flip and trigger_short:
        return build_flip("short")
    if pos_side == "short" and score >= cfg.min_score_to_flip and trigger_long:
        return build_flip("long")

    # 趋势明显走坏则平仓
    if pos_side == "long" and score < -0.2:
        return build_close()
    if pos_side == "short" and score > 0.2:
        return build_close()

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


from typing import List, Optional

def decide_regime(
    base: MarketRegime,
    adx: Optional[float],
    vol_state: VolState,
    order_book: OrderBookInfo,
    timing: Dict,
    max_spread_bps: float,
) -> Decision:
    """
    根据市场环境（regime + vol）与执行风险（order book），
    产出唯一可执行决策（Decision）。
    """

    # =========================================================
    # Step 1) Hard Stop —— 系统级禁止（直接 STOP_ALL）
    # =========================================================
    hard_reasons: List[str] = []

    if base == MarketRegime.UNKNOWN or vol_state == VolState.UNKNOWN:
        hard_reasons.append("regime or vol_state unknown (insufficient data)")

    if (
        order_book.spread_bps is not None
        and order_book.spread_bps > max_spread_bps
    ):
        hard_reasons.append(
            f"spread too wide ({order_book.spread_bps:.2f}bps > {max_spread_bps:.2f}bps)"
        )

    if hard_reasons:
        return Decision(
            action=Action.STOP_ALL,
            regime=base,
            adx=adx,
            vol_state=vol_state,
            order_book=order_book,
            allow_trend=False,
            allow_mean=False,
            risk_scale=0.0,
            cooldown_scale=2.0,
            reasons=hard_reasons,
        )

    # =========================================================
    # Step 2) 策略类型许可（环境层）
    # =========================================================
    allow_trend = base in (MarketRegime.TREND, MarketRegime.MIXED)
    allow_mean  = base in (MarketRegime.RANGE, MarketRegime.MIXED)

    # 波动状态对策略类型的覆盖
    if vol_state == VolState.LOW:
        allow_trend = False      # 低波动：禁止追趋势
    elif vol_state == VolState.HIGH:
        allow_mean = False       # 高波动：禁止均值回归

    # =========================================================
    # Step 3) Soft Stop —— 禁新开仓，但允许管理仓位
    # =========================================================
    soft_reasons: List[str] = []

    if vol_state == VolState.HIGH and base in (MarketRegime.RANGE, MarketRegime.MIXED):
        soft_reasons.append(
            "high vol + range/mixed: whipsaw risk (avoid new entries)"
           " 在一个没有方向的市场里，波动又特别大 新开仓很容易被下一根反向 K 线扫掉已有仓位仍然需要：止损 减仓 平仓")

    if vol_state == VolState.LOW and base in (MarketRegime.TREND, MarketRegime.MIXED):
        soft_reasons.append(
            "low vol + trend/mixed: breakout failure risk (avoid new entries) "
            "结构像趋势，但市场没有动能 极容易是假突破"
        )

    # =========================================================
    # Step 4) 风险缩放（只有在非 STOP_ALL 下才有意义）
    # =========================================================
    if vol_state == VolState.HIGH:
        risk_scale, cooldown_scale = 0.6, 2.0
    elif vol_state == VolState.LOW:
        risk_scale, cooldown_scale = 0.8, 1.5
    else:
        risk_scale, cooldown_scale = 1.0, 1.0

    if soft_reasons:
        return Decision(
            action=Action.NO_NEW_ENTRY,
            regime=base,
            adx=adx,
            vol_state=vol_state,
            order_book=order_book,

            allow_trend=allow_trend,
            allow_mean=allow_mean,

            risk_scale=risk_scale,
            cooldown_scale=cooldown_scale,

            reasons=soft_reasons,
        )

    # =========================================================
    # Step 5) OK —— 正常可交易
    # =========================================================
    return Decision(
        action=Action.OK,
        regime=base,
        adx=adx,
        vol_state=vol_state,
        order_book=order_book,

        allow_trend=allow_trend,
        allow_mean=allow_mean,

        risk_scale=risk_scale,
        cooldown_scale=cooldown_scale,

        reasons=[f"ok: regime={base.value}, adx={adx}, vol={vol_state.value}"],
    )


def is_no_trade_high_cost(vol_state: str, spread_bps: float, max_spread_bps=15) -> tuple[bool, str]:
    if vol_state == "high" and spread_bps > max_spread_bps:
        return True, f"NO_TRADE: high vol + spread {spread_bps:.1f}bps"
    return False, ""


def is_no_trade_chop(base: str, vol_state: str) -> tuple[bool, str]:
    if base == "range" and vol_state == "low":
        return True, "NO_TRADE: range + low vol (chop)"
    return False, ""
