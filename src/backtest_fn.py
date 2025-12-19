"""src.backtest_fn

纯函数式回测（不使用面向对象的 Engine/Account 类）。

特点（尽量“齐全”）：
- 拉取/加载历史多周期 K 线（1h/15m/5m）
- 决策频率回测循环（默认 1h）
- 限价成交模拟：在 TTL 窗口内用 5m high/low 判断是否触达
- 止损/止盈触发：逐根 5m 扫描（并处理同一根同时触达的保守规则）
- 手续费 + 滑点
- 权益曲线、最大回撤、胜率、盈亏比、夏普（基于逐周期收益）
- 导出交易 CSV

说明：
- 订单簿无法历史回放，这里用“合成盘口”代替（mid=close，spread_bps 固定）。
- funding/爆仓等高级细节未模拟（可后续扩展）。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from hyperliquid.info import Info

from src.data.fetcher import ohlcv_to_df
from src.data.indicators import compute_technical_factors
from src.data.analyzer import classify_timing_state, classify_trend_range
from src.data.models import (
    AccountOverview,
    AccountState,
    CumFunding,
    Decision,
    LeverageInfo,
    MarginSummary,
    NormalOrder,
    OrderBookInfo,
    PerpAssetInfo,
    PerpPosition,
    PositionOrders,
    PositionTpsl,
    RegimeState,
    Side,
    TimingState,
    TriggerOrder,
)
from src.strategy.planner import signal_to_trade_plan
from src.strategy.regime import classify_vol_state, decide_regime
from src.strategy.signals import build_signal
from src.tools.utils import TIMEFRAME_MS, hl_candles_to_ohlcv_list


# -------------------------
# Data utilities
# -------------------------

def _dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def fetch_ohlcv_range(
    info: Info,
    *,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    limit: int = 2000,
    sleep_s: float = 0.06,
) -> List[List[float]]:
    """从 Hyperliquid 拉取指定区间 OHLCV。

    返回 ccxt 风格：[[ts_ms, open, high, low, close, volume], ...]
    """
    if interval not in TIMEFRAME_MS:
        raise ValueError(f"Unknown interval={interval!r}")

    start_ms = _dt_to_ms(start)
    end_ms = _dt_to_ms(end)

    all_rows: List[dict[str, Any]] = []
    cur = start_ms

    step = TIMEFRAME_MS[interval] * int(limit)

    while cur < end_ms:
        cur_end = min(cur + step, end_ms)
        candles = info.candles_snapshot(name=symbol, interval=interval, startTime=cur, endTime=cur_end)
        if not candles:
            break
        all_rows.extend(candles)

        # 推进到最后一根之后
        last_t = int(candles[-1]["t"])
        nxt = last_t + TIMEFRAME_MS[interval]
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(sleep_s)

    return hl_candles_to_ohlcv_list(all_rows)


def load_history(
    info: Info,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    intervals: Tuple[str, ...] = ("1h", "15m", "5m"),
) -> Dict[str, pd.DataFrame]:
    """拉取多周期历史并转为 DataFrame（index 为时间戳）。"""
    out: Dict[str, pd.DataFrame] = {}
    for itv in intervals:
        rows = fetch_ohlcv_range(info, symbol=symbol, interval=itv, start=start, end=end)
        if not rows:
            continue
        df = ohlcv_to_df(rows)
        out[itv] = df
    return out


def slice_lookback(df: pd.DataFrame, *, t: pd.Timestamp, lookback: int) -> Optional[pd.DataFrame]:
    mask = df.index <= t
    if not mask.any():
        return None
    return df.loc[mask].tail(lookback)


def window_5m(df_5m: pd.DataFrame, *, t0: pd.Timestamp, t1: pd.Timestamp) -> pd.DataFrame:
    """取 (t0, t1] 的 5m 窗口（用于成交/止损止盈扫描）。"""
    return df_5m[(df_5m.index > t0) & (df_5m.index <= t1)]


# -------------------------
# Account / position state (dict-based)
# -------------------------

def bt_new_account(*, initial_equity: float) -> Dict[str, Any]:
    return {
        "initial_equity": float(initial_equity),
        "cash": float(initial_equity),
        "positions": {},  # symbol -> dict
        "trades": [],  # list[dict]
        "equity_curve": [],  # list[dict{ts,equity}]
        "peak_equity": float(initial_equity),
        "max_drawdown": 0.0,
    }


def bt_has_position(account: Dict[str, Any], symbol: str) -> bool:
    p = account["positions"].get(symbol)
    return bool(p and p.get("qty", 0.0) != 0.0)


def bt_position_side(account: Dict[str, Any], symbol: str) -> Optional[Side]:
    p = account["positions"].get(symbol)
    if not p:
        return None
    return p.get("side")


def _norm_side(x: Any) -> Optional[Side]:
    """Normalize various side representations to Side enum."""
    if x is None:
        return None
    if isinstance(x, Side):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        if s in ("long", "l", "buy", "side.long", "LONG".lower()):
            return Side.LONG
        if s in ("short", "s", "sell", "side.short", "SHORT".lower()):
            return Side.SHORT
        if s in ("none", "flat", "side.none", "NONE".lower()):
            return Side.NONE
    return None


def bt_mark_to_market(account: Dict[str, Any], *, ts: pd.Timestamp, prices: Dict[str, float]) -> float:
    equity = bt_equity_now(account, prices=prices)

    account["equity_curve"].append({"ts": ts, "equity": equity})
    peak = float(account["peak_equity"])
    if equity > peak:
        account["peak_equity"] = equity
        peak = equity
    dd = (peak - equity) / peak if peak > 0 else 0.0
    if dd > float(account["max_drawdown"]):
        account["max_drawdown"] = dd
    return equity


def bt_equity_now(account: Dict[str, Any], *, prices: Dict[str, float]) -> float:
    """计算当前权益（不写 equity_curve）。"""
    if not isinstance(account, dict):
        raise TypeError(f"account must be dict, got {type(account)!r}")

    cash = account.get("cash")
    equity = float(cash) if cash is not None else 0.0

    positions = account.get("positions") or {}
    if not isinstance(positions, dict):
        raise TypeError(f"account['positions'] must be dict, got {type(positions)!r}")

    for sym, p in positions.items():
        if not isinstance(p, dict):
            continue

        side = _norm_side(p.get("side"))
        if side not in (Side.LONG, Side.SHORT):
            continue

        try:
            qty = float(p.get("qty", 0.0))
            entry = float(p.get("entry_price", 0.0))
        except Exception:
            continue

        px_raw = prices.get(sym)
        if px_raw is None:
            px_raw = p.get("last_price", entry)
        try:
            px = float(px_raw)
        except Exception:
            px = entry

        if side == Side.LONG:
            equity += (px - entry) * qty
        else:
            equity += (entry - px) * qty

    return float(equity)


def _fee(notional: float, fee_rate: float) -> float:
    return float(notional) * float(fee_rate)


def _apply_slippage(price: float, *, side: Side, action: str, slippage: float) -> float:
    """action: 'open'|'close'"""
    px = float(price)
    s = float(slippage)
    if action == "open":
        if side == Side.LONG:
            return px * (1 + s)  # buy worse
        if side == Side.SHORT:
            return px * (1 - s)  # sell worse
    else:
        if side == Side.LONG:
            return px * (1 - s)  # sell worse
        if side == Side.SHORT:
            return px * (1 + s)  # buy worse
    return px


def bt_open_position(
    account: Dict[str, Any],
    *,
    symbol: str,
    side: Side,
    qty: float,
    fill_price: float,
    ts: pd.Timestamp,
    stop_price: Optional[float],
    take_profit: Optional[float],
    fee_rate: float,
) -> bool:
    if bt_has_position(account, symbol):
        return False

    qty = float(qty)
    if qty <= 0:
        return False

    notional = float(fill_price) * qty
    fee = _fee(notional, fee_rate)

    # 这里用“全现金权益”简化：只扣 fee；不冻结保证金
    account["cash"] -= fee

    account["positions"][symbol] = {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_price": float(fill_price),
        "entry_ts": ts,
        "stop_price": float(stop_price) if stop_price is not None else None,
        "take_profit": float(take_profit) if take_profit is not None else None,
    }
    return True


def bt_close_position(
    account: Dict[str, Any],
    *,
    symbol: str,
    fill_price: float,
    ts: pd.Timestamp,
    reason: str,
    fee_rate: float,
) -> Optional[float]:
    if not bt_has_position(account, symbol):
        return None

    p = account["positions"].pop(symbol)

    qty = float(p["qty"])
    entry = float(p["entry_price"])
    side: Side = p["side"]

    if side == Side.LONG:
        pnl = (float(fill_price) - entry) * qty
    else:
        pnl = (entry - float(fill_price)) * qty

    notional = float(fill_price) * qty
    fee = _fee(notional, fee_rate)
    pnl -= fee

    account["cash"] += pnl

    trade = {
        "symbol": symbol,
        "side": side.value,
        "qty": qty,
        "entry_price": entry,
        "exit_price": float(fill_price),
        "entry_ts": p["entry_ts"],
        "exit_ts": ts,
        "pnl": pnl,
        "pnl_pct": (pnl / (entry * qty) * 100.0) if entry * qty > 0 else 0.0,
        "reason": reason,
    }
    account["trades"].append(trade)
    return pnl


def bt_update_stop_from_signal(account: Dict[str, Any], *, symbol: str, new_stop: Optional[float]) -> None:
    if not bt_has_position(account, symbol):
        return
    if new_stop is None:
        return

    p = account["positions"][symbol]
    side: Side = p["side"]
    old = p.get("stop_price")

    if old is None:
        p["stop_price"] = float(new_stop)
        return

    if side == Side.LONG:
        p["stop_price"] = max(float(old), float(new_stop))
    elif side == Side.SHORT:
        p["stop_price"] = min(float(old), float(new_stop))


# -------------------------
# Strategy interface helpers (build AccountOverview/PerpPosition)
# -------------------------

def _mk_position_orders_for_stop(*, stop_price: Optional[float]) -> PositionOrders:
    # 用 TriggerOrder 塞一条 SL，让 signals.position_stop_price() 能读到 old_stop
    if stop_price is None:
        tpsl = PositionTpsl(tp=tuple(), sl=tuple(), others=tuple())
    else:
        sl = TriggerOrder(
            coin="",
            side=None,
            size=0.0,
            limit_px=None,
            trigger_px=float(stop_price),
            trigger_condition=None,
            is_position_tpsl=True,
            timestamp=None,
            raw={},
        )
        tpsl = PositionTpsl(tp=tuple(), sl=(sl,), others=tuple())

    return PositionOrders(tpsl=tpsl, normal=tuple(), raw_trigger=tuple())


def bt_make_account_overview(
    account: Dict[str, Any],
    *,
    symbol: str,
    mark_price: float,
) -> AccountOverview:
    positions: List[PerpPosition] = []
    primary: Optional[PerpPosition] = None

    if bt_has_position(account, symbol):
        p = account["positions"][symbol]
        side: Side = p["side"]
        qty = float(p["qty"])
        entry = float(p["entry_price"])
        stop = p.get("stop_price")

        szi = qty if side == Side.LONG else -qty
        upnl = (mark_price - entry) * qty if side == Side.LONG else (entry - mark_price) * qty

        primary = PerpPosition(
            coin=symbol,
            orders=_mk_position_orders_for_stop(stop_price=stop),
            cum_funding=CumFunding(all_time=None, since_change=None, since_open=None),
            entry_px=entry,
            liquidation_px=None,
            margin_used=None,
            max_leverage=None,
            szi=szi,
            position_value=mark_price * qty,
            unrealized_pnl=upnl,
            return_on_equity=None,
            leverage=LeverageInfo(type=None, value=None),
            raw={},
        )
        positions.append(primary)

    equity = bt_equity_now(account, prices={symbol: mark_price})

    state = AccountState(
        time_ms=None,
        withdrawable=None,
        cross_maintenance_margin_used=None,
        cross_margin_summary=MarginSummary(
            account_value=equity,
            total_margin_used=None,
            total_ntl_pos=None,
            total_raw_usd=None,
        ),
        margin_summary=MarginSummary(
            account_value=equity,
            total_margin_used=None,
            total_ntl_pos=None,
            total_raw_usd=None,
        ),
    )

    return AccountOverview(
        state=state,
        positions=positions,
        primary_position=primary,
        open_orders=[],
        raw_user_state={},
    )


def bt_make_asset_info(
    *,
    symbol: str,
    price: float,
    size_decimals: int = 3,
    max_leverage: int = 50,
) -> PerpAssetInfo:
    px = Decimal(str(price))
    return PerpAssetInfo(
        symbol=symbol,
        size_decimals=size_decimals,
        max_leverage=max_leverage,
        only_isolated=False,
        mark_price=px,
        mid_price=px,
        oracle_price=px,
        prev_day_price=px,
        funding_rate=Decimal("0"),
        premium=Decimal("0"),
        open_interest=Decimal("0"),
        day_notional_volume=Decimal("0"),
        impact_bid=Decimal("0"),
        impact_ask=Decimal("0"),
        raw={},
    )


def bt_synth_order_book(*, symbol: str, mid: float, spread_bps: float, ts: pd.Timestamp) -> OrderBookInfo:
    spread = float(mid) * float(spread_bps) / 10_000.0
    best_bid = float(mid) - spread / 2
    best_ask = float(mid) + spread / 2
    return OrderBookInfo(
        symbol=symbol,
        best_bid=best_bid,
        best_ask=best_ask,
        mid_price=float(mid),
        spread_bps=float(spread_bps),
        bid_depth_value=100_000.0,
        ask_depth_value=100_000.0,
        imbalance=0.0,
        timestamp=_dt_to_ms(ts.to_pydatetime()),
    )


# -------------------------
# Execution simulation (limit fills, SL/TP)
# -------------------------

def _limit_filled_in_window(
    win: pd.DataFrame,
    *,
    side: Side,
    limit_price: float,
) -> bool:
    if win is None or len(win) == 0:
        return False
    if side == Side.LONG:
        return float(win["low"].min()) <= float(limit_price)
    if side == Side.SHORT:
        return float(win["high"].max()) >= float(limit_price)
    return False


def _scan_sl_tp_barwise(
    win: pd.DataFrame,
    *,
    side: Side,
    stop_price: Optional[float],
    take_profit: Optional[float],
) -> Optional[Tuple[str, float, pd.Timestamp]]:
    """逐根扫描 5m。

    返回 (reason, fill_price, ts)。

    同一根同时触达：保守规则
    - LONG：先触发 Stop
    - SHORT：先触发 Stop
    """
    if win is None or len(win) == 0:
        return None

    sp = float(stop_price) if stop_price is not None else None
    tp = float(take_profit) if take_profit is not None else None

    for ts, row in win.iterrows():
        lo = float(row["low"])
        hi = float(row["high"])

        hit_stop = False
        hit_tp = False

        if sp is not None:
            if side == Side.LONG and lo <= sp:
                hit_stop = True
            if side == Side.SHORT and hi >= sp:
                hit_stop = True

        if tp is not None:
            if side == Side.LONG and hi >= tp:
                hit_tp = True
            if side == Side.SHORT and lo <= tp:
                hit_tp = True

        if hit_stop and hit_tp:
            return ("Stop Loss (both hit)", sp, ts)
        if hit_stop:
            return ("Stop Loss", sp, ts)
        if hit_tp:
            return ("Take Profit", tp, ts)

    return None


# -------------------------
# Metrics
# -------------------------

def bt_metrics(account: Dict[str, Any]) -> Dict[str, Any]:
    trades = account.get("trades", [])
    eq = account.get("equity_curve", [])

    total_pnl = float(account["cash"]) - float(account["initial_equity"])
    total_return_pct = (total_pnl / float(account["initial_equity"])) * 100.0 if account["initial_equity"] else 0.0

    wins = [t for t in trades if float(t.get("pnl", 0.0)) > 0]
    losses = [t for t in trades if float(t.get("pnl", 0.0)) <= 0]

    gross_profit = sum(float(t["pnl"]) for t in wins)
    gross_loss = -sum(float(t["pnl"]) for t in losses if float(t["pnl"]) < 0)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0

    # Sharpe：用逐周期 equity return
    sharpe = 0.0
    if len(eq) >= 3:
        e = pd.Series([float(x["equity"]) for x in eq])
        r = e.pct_change().dropna()
        if r.std() and r.std() != 0:
            sharpe = float((r.mean() / r.std()) * (252**0.5))

    return {
        "initial_equity": float(account["initial_equity"]),
        "final_equity": float(account["cash"]),
        "total_pnl": total_pnl,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": float(account.get("max_drawdown", 0.0)) * 100.0,
        "trades": len(trades),
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "sharpe": sharpe,
    }


def bt_report(metrics: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "=" * 60,
            "Backtest Report",
            "=" * 60,
            f"Initial equity: {metrics['initial_equity']:.2f}",
            f"Final equity:   {metrics['final_equity']:.2f}",
            f"Total PnL:      {metrics['total_pnl']:.2f}",
            f"Return:         {metrics['total_return_pct']:.2f}%",
            f"Max DD:         {metrics['max_drawdown_pct']:.2f}%",
            f"Trades:         {metrics['trades']}",
            f"Win rate:       {metrics['win_rate_pct']:.2f}%",
            f"Profit factor:  {metrics['profit_factor']:.2f}",
            f"Sharpe:         {metrics['sharpe']:.2f}",
            "=" * 60,
        ]
    )


def bt_export_trades_csv(account: Dict[str, Any], path: str) -> None:
    df = pd.DataFrame(account.get("trades", []))
    df.to_csv(path, index=False)


# -------------------------
# Main backtest loop (function)
# -------------------------

def run_backtest(
    info: Info,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    initial_equity: float = 10_000.0,
    decision_interval: str = "1h",
    lookback: int = 500,
    fee_rate: float = 0.0006,
    slippage: float = 0.001,
    order_spread_bps: float = 2.0,
    risk_pct: float = 0.01,
    leverage: float = 5.0,
    post_only: bool = False,
    loop_slippage: float = 0.01,
) -> Dict[str, Any]:
    data = load_history(info, symbol=symbol, start=start, end=end, intervals=("1h", "15m", "5m"))
    if decision_interval not in data or "5m" not in data or "15m" not in data:
        raise ValueError("need 1h/15m/5m history for backtest")

    df_1h = data["1h"]
    df_15m = data["15m"]
    df_5m = data["5m"]

    # 决策点：用 1h 的 index
    decision_times = [t for t in df_1h.index if (t >= pd.Timestamp(start)) and (t <= pd.Timestamp(end))]
    if len(decision_times) < 50:
        raise ValueError("not enough 1h bars")

    account = bt_new_account(initial_equity=initial_equity)
    regime_state = RegimeState()

    # 静态 meta：尽量拿真实 size_decimals
    size_decimals = 3
    max_lev = 50
    try:
        meta, _ctxs = info.meta_and_asset_ctxs()
        uni = meta.get("universe") or []
        for u in uni:
            if u.get("name") == symbol:
                size_decimals = int(u.get("szDecimals") or size_decimals)
                max_lev = int(u.get("maxLeverage") or max_lev)
                break
    except Exception:
        pass

    for i in range(1, len(decision_times)):
        t = decision_times[i]
        t_prev = decision_times[i - 1]

        # 1) 先在 (t_prev, t] 区间扫描 SL/TP（逐 5m）
        if bt_has_position(account, symbol):
            p = account["positions"][symbol]
            side: Side = p["side"]
            win = window_5m(df_5m, t0=t_prev, t1=t)
            hit = _scan_sl_tp_barwise(win, side=side, stop_price=p.get("stop_price"), take_profit=p.get("take_profit"))
            if hit is not None:
                reason, fill_px, hit_ts = hit
                fill_px = _apply_slippage(fill_px, side=side, action="close", slippage=slippage)
                bt_close_position(account, symbol=symbol, fill_price=fill_px, ts=hit_ts, reason=reason, fee_rate=fee_rate)

        # 2) 构建 lookback 切片 + 指标
        s1 = slice_lookback(df_1h, t=t, lookback=lookback)
        s15 = slice_lookback(df_15m, t=t, lookback=lookback)
        s5 = slice_lookback(df_5m, t=t, lookback=lookback)
        if s1 is None or len(s1) < 60:
            continue
        if s15 is None or len(s15) < 60:
            continue
        if s5 is None or len(s5) < 60:
            continue

        ind_1h = compute_technical_factors(s1)
        ind_15m = compute_technical_factors(s15)
        ind_5m = compute_technical_factors(s5)

        mid = float(ind_1h["close"].iloc[-1])

        # 3) 环境
        base, adx = classify_trend_range(df=ind_1h, prev=regime_state.prev_base)
        vol_state, _dbg = classify_vol_state(ind_1h)
        timing: TimingState = classify_timing_state(ind_1h)

        ob = bt_synth_order_book(symbol=symbol, mid=mid, spread_bps=order_spread_bps, ts=t)
        regime: Decision = decide_regime(base, adx, vol_state, ob, timing=timing, max_spread_bps=order_spread_bps)

        asset = bt_make_asset_info(symbol=symbol, price=mid, size_decimals=size_decimals, max_leverage=max_lev)

        # 4) 给策略喂 account/position
        acct_view = bt_make_account_overview(account, symbol=symbol, mark_price=mid)

        signal = build_signal(
            df_1h=ind_1h,
            df_15m=ind_15m,
            df_5m=ind_5m,
            regime=regime,
            asset_info=asset,
            position=acct_view.primary_position,
            now_ts=float(t.timestamp()),
        )

        # 5) 有仓：更新 trailing stop（让下一轮 SL 更贴近策略）
        if bt_has_position(account, symbol):
            bt_update_stop_from_signal(account, symbol=symbol, new_stop=signal.stop_price)

        # 6) 退出信号：用当前 close 立即平
        if signal.exit_ok and bt_has_position(account, symbol):
            p = account["positions"][symbol]
            side: Side = p["side"]
            fill_px = _apply_slippage(mid, side=side, action="close", slippage=slippage)
            bt_close_position(
                account,
                symbol=symbol,
                fill_price=fill_px,
                ts=t,
                reason="Strategy Exit",
                fee_rate=fee_rate,
            )

        # 7) 生成计划（复用你现有策略）
        plan = signal_to_trade_plan(
            signal=signal,
            regime=regime,
            account=acct_view,
            asset=asset,
            symbol=symbol,
            risk_pct=risk_pct,
            leverage=leverage,
            post_only=post_only,
            slippage=loop_slippage,
        )

        # 8) 执行开仓（无仓时）
        if plan.action == "OPEN" and not bt_has_position(account, symbol) and plan.qty > 0 and plan.side is not None:
            side = plan.side
            if plan.entry_type == "MARKET":
                fill_px = _apply_slippage(mid, side=side, action="open", slippage=slippage)
                bt_open_position(
                    account,
                    symbol=symbol,
                    side=side,
                    qty=float(plan.qty),
                    fill_price=fill_px,
                    ts=t,
                    stop_price=plan.stop_price,
                    take_profit=plan.take_profit,
                    fee_rate=fee_rate,
                )
            else:
                # LIMIT：在 TTL 窗口内用 5m 判断是否触达
                limit_px = float(plan.entry_price or 0.0)
                ttl = int(getattr(signal, "ttl_seconds", 120))
                t1 = t + pd.Timedelta(seconds=ttl)
                win = window_5m(df_5m, t0=t, t1=t1)
                if limit_px > 0 and _limit_filled_in_window(win, side=side, limit_price=limit_px):
                    bt_open_position(
                        account,
                        symbol=symbol,
                        side=side,
                        qty=float(plan.qty),
                        fill_price=limit_px,
                        ts=t,
                        stop_price=plan.stop_price,
                        take_profit=plan.take_profit,
                        fee_rate=fee_rate,
                    )

        # 9) 记录权益（用决策点 close）
        bt_mark_to_market(account, ts=t, prices={symbol: mid})
        regime_state.prev_base = base

    # 收尾：若有仓，按 end 收盘价平
    end_ts = pd.Timestamp(end)
    last_px = float(df_5m[df_5m.index <= end_ts]["close"].iloc[-1])
    if bt_has_position(account, symbol):
        p = account["positions"][symbol]
        side: Side = p["side"]
        fill_px = _apply_slippage(last_px, side=side, action="close", slippage=slippage)
        bt_close_position(account, symbol=symbol, fill_price=fill_px, ts=end_ts, reason="End", fee_rate=fee_rate)

    bt_mark_to_market(account, ts=end_ts, prices={symbol: last_px})
    return account
