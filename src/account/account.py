from typing import Any, Dict, List, Optional, Iterable

from hyperliquid.info import Info

from src.account.manager import _to_float, parse_orders, embed_orders_into_positions
from src.data.models import (
    AccountOverview,
    AccountState,
    MarginSummary,
    NormalOrder,
    PerpPosition,
    PositionOrders,
    Side,
    TriggerOrder,
)
from src.tools.performance import measure_time
from datetime import datetime, timezone





'''
{
  // =========================
  // 当前账户所有永续仓位
  // =========================
  "assetPositions": [
    {
      // 仓位模式：oneWay = 单向持仓（非对冲）
      "type": "oneWay",

      "position": {
        // 交易币种
        "coin": "BTC",

        // ===== 资金费率相关 =====
        "cumFunding": {
          // 历史累计资金费（从账户创建开始）
          "allTime": "1.623507",

          // 最近一次 funding 变化带来的盈亏
          "sinceChange": "0.299935",

          // 自本仓位开仓以来累计 funding
          "sinceOpen": "0.299935"
        },

        // ===== 开仓信息 =====
        // 平均开仓价
        "entryPx": "92499.0",

        // ===== 杠杆信息 =====
        "leverage": {
          // cross = 全仓
          // isolated = 逐仓
          "type": "cross",

          // 实际使用的杠杆倍数
          "value": 24
        },

        // ===== 强平价格 =====
        // 预估爆仓价（随保证金、资金费实时变化）
        "liquidationPx": "86405.373661149",

        // ===== 保证金 =====
        // 当前仓位占用的保证金（USDC）
        "marginUsed": "15.4986",

        // 该币种允许的最大杠杆
        "maxLeverage": 40,

        // ===== 仓位规模 =====
        // 仓位名义价值（USDC）
        "positionValue": "371.9664",

        // ===== 收益 =====
        // ROE = 未实现盈亏 / 保证金
        // -0.80 = 亏损 80%
        "returnOnEquity": "-0.8001816236",

        // ===== 仓位数量 =====
        // szi = size（张数 / 币数）
        // 正数 = 多头
        // 负数 = 空头
        "szi": "0.00416",

        // ===== 未实现盈亏 =====
        // mark price - entry price 计算
        "unrealizedPnl": "-12.82944"
      }
    }
  ],

  // =========================
  // 全仓维护保证金占用
  // =========================
  // 用于判断是否触发强平
  "crossMaintenanceMarginUsed": "4.64958",

  // =========================
  // 全仓保证金汇总（最重要）
  // =========================
  "crossMarginSummary": {
    // 账户总价值（权益）
    "accountValue": "17.013125",

    // 所有仓位占用的保证金
    "totalMarginUsed": "15.4986",

    // 所有仓位名义价值总和
    "totalNtlPos": "371.9664",

    // 原始盈亏（包含未实现 + funding）
    "totalRawUsd": "-354.953275"
  },

  // =========================
  // marginSummary（通常等同 cross）
  // =========================
  "marginSummary": {
    "accountValue": "17.013125",
    "totalMarginUsed": "15.4986",
    "totalNtlPos": "371.9664",
    "totalRawUsd": "-354.953275"
  },

  // =========================
  // 服务器时间戳（毫秒）
  // =========================
  "time": 1765805561239,

  // =========================
  // 可提 / 可用余额
  // =========================
  // ⚠️ 注意：
  // - 全仓下，很多时候为 0
  // - 即使有余额，也不代表能安全开新仓
  "withdrawable": "0.0"
}

'''
@measure_time
def fetch_account_overview(info: Info, address: str,primary_symbol: Optional[str] = None,) -> AccountOverview:
    """
    返回强类型：
    - AccountState（权益/保证金/时间戳/可提）
    - List[PerpPosition]（永续仓位）
    - open_orders（暂保留 dict）
    """
    print("\n💼 正在获取账户状态...")

    us = info.user_state(address)

    # --- summary ---
    cross_margin_summary = MarginSummary.from_dict(us.get("crossMarginSummary"))
    margin_summary = MarginSummary.from_dict(us.get("marginSummary"))

    state = AccountState(
        time_ms=int(us["time"]) if isinstance(us.get("time"), (int, float)) else None,
        withdrawable=_to_float(us.get("withdrawable")),
        cross_maintenance_margin_used=_to_float(us.get("crossMaintenanceMarginUsed")),
        cross_margin_summary=cross_margin_summary,
        margin_summary=margin_summary,
    )

    # --- positions ---
    asset_positions = us.get("assetPositions") or []
    positions: List[PerpPosition] = []
    primary_position: Optional[PerpPosition] = None
    for ap in asset_positions:
        pos_dict = ap.get("position")
        coin = pos_dict.get("coin")
        if not coin:
            continue
        pos = PerpPosition.from_dict(pos_dict)
        positions.append(pos)

        if primary_symbol is not None and coin == primary_symbol:
           primary_position = pos

    # --- orders ---
    frontend_open_orders = info.frontend_open_orders(address) or []
    if not isinstance(frontend_open_orders, list):
        frontend_open_orders = []

    # ✅ 强类型拆分
    normal_orders, trigger_orders = parse_orders(frontend_open_orders)

    # ✅ 内嵌到仓位对象里
    positions = embed_orders_into_positions(positions, normal_orders, trigger_orders)

    # ✅ primary_position 如果需要也要从 enriched 里重新拿（否则它是老对象）
    if primary_symbol is not None:
        for p in positions:
            if p.coin == primary_symbol:
                primary_position = p
                break

    return AccountOverview(
        state=state,
        positions=positions,
        open_orders=frontend_open_orders,
        primary_position=primary_position,
        raw_user_state=us,
    )


def _fmt(x: Optional[float], nd: int = 4) -> str:
    if x is None:
        return "-"
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)

def _fmt_pct(x: Optional[float], nd: int = 2) -> str:
    if x is None:
        return "-"
    try:
        return f"{float(x) * 100:.{nd}f}%"
    except Exception:
        return str(x)

def _fmt_ts_ms(ts_ms: Optional[int]) -> str:
    if ts_ms is None:
        return "-"
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
        return dt.strftime("%m-%d %H:%M:%S")
    except Exception:
        return str(ts_ms)


def format_account_overview(
    overview: AccountOverview,
    *,
    max_positions: int = 20,
    max_orders: int = 10,
) -> str:
    """
    将 fetch_account_overview() 的结果格式化成可读文本。
    ⚠️ 不会打印 overview.raw_user_state（按你的要求）。
    """
    lines: List[str] = []

    st = overview.state
    ts = st.time_ms
    if ts is not None:
        # HL time_ms 是毫秒时间戳（UTC）
        dt_utc = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        lines.append(f"账户概览（UTC 时间）：{dt_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        lines.append("账户概览")

    cms = st.cross_margin_summary
    acct = cms.account_value
    used = cms.total_margin_used
    util = (used / acct) if (acct is not None and used is not None and acct != 0) else None

    lines.append("========== 账户/保证金 ==========")
    lines.append(f"- 账户权益(USDC)          ：{_fmt(acct, 4)}")
    lines.append(f"- 总保证金占用(USDC)      ：{_fmt(used, 4)}")
    lines.append(f"- 保证金占用率            ：{_fmt_pct(util, 2)}")
    lines.append(f"- 总名义仓位价值(USDC)    ：{_fmt(cms.total_ntl_pos, 4)}")
    lines.append(f"- 总原始盈亏(USDC)        ：{_fmt(cms.total_raw_usd, 4)}")
    lines.append(f"- 可提余额(USDC)          ：{_fmt(st.withdrawable, 4)}")
    lines.append(f"- 全仓维持保证金占用(USDC)：{_fmt(st.cross_maintenance_margin_used, 4)}")

    # positions
    positions = [p for p in (overview.positions or []) if (p.szi is None or abs(p.szi) > 0)]
    lines.append("")
    lines.append(f"========== 仓位列表（非零）: {len(positions)} ==========")
    if not positions:
        lines.append("- （无持仓）")

    for i, p in enumerate(positions[:max_positions]):
        lev = p.leverage
        orders: Optional[PositionOrders] = p.orders
        tp_n = len(orders.tpsl.tp) if orders is not None else 0
        sl_n = len(orders.tpsl.sl) if orders is not None else 0
        norm_n = len(orders.normal) if orders is not None else 0

        if p.side_enum == Side.LONG:
            side_cn = "多"
        elif p.side_enum == Side.SHORT:
            side_cn = "空"
        else:
            side_cn = "无"
        lev_type_cn = "全仓" if lev.type == "cross" else ("逐仓" if lev.type == "isolated" else "-")

        lines.append(f"- [{i+1:02d}] 币种：{p.coin}")
        lines.append(f"    方向：{side_cn}    数量(szi)：{_fmt(p.szi, 6)}")
        lines.append(f"    开仓均价：{_fmt(p.entry_px, 2)}    预估强平价：{_fmt(p.liquidation_px, 2)}")
        lines.append(
            f"    保证金占用：{_fmt(p.margin_used, 4)}    名义价值：{_fmt(p.position_value, 2)}"
        )
        lines.append(
            f"    未实现盈亏：{_fmt(p.unrealized_pnl, 4)}    ROE：{_fmt(p.return_on_equity, 4)}"
        )
        lines.append(
            f"    杠杆：{_fmt(lev.value, 2)}x（{lev_type_cn}）    关联订单：TP/SL/普通 = {tp_n}/{sl_n}/{norm_n}"
        )

        # 打印“仓位内嵌的订单”（而不是 overview.open_orders 原始 dict 列表）
        pos_orders: Optional[PositionOrders] = p.orders
        if pos_orders is None:
            continue

        # 每个仓位最多展示多少条订单（避免刷屏）
        per_pos_cap = 13

        # --- TP / SL（TriggerOrder）---
        tp_list: List[TriggerOrder] = list(pos_orders.tpsl.tp)
        sl_list: List[TriggerOrder] = list(pos_orders.tpsl.sl)

        if tp_list:
            lines.append(f"    止盈单(TP)：{len(tp_list)}（最多展示 {per_pos_cap}）")
            for j, o in enumerate(tp_list[:per_pos_cap]):
                lines.append(
                    f"      - [{j+1:02d}] 方向={o.side} 数量={_fmt(o.size, 6)} "
                    f"触发价={_fmt(o.trigger_px, 2)} 执行限价={_fmt(o.limit_px, 2)} "
                    f"时间={_fmt_ts_ms(o.timestamp)}"
                )
        if sl_list:
            lines.append(f"    止损单(SL)：{len(sl_list)}（最多展示 {per_pos_cap}）")
            for j, o in enumerate(sl_list[:per_pos_cap]):
                lines.append(
                    f"      - [{j+1:02d}] 方向={o.side} 数量={_fmt(o.size, 6)} "
                    f"触发价={_fmt(o.trigger_px, 2)} 执行限价={_fmt(o.limit_px, 2)} "
                    f"时间={_fmt_ts_ms(o.timestamp)}"
                )
        # others 不一定是 tp/sl，先不打印（需要再加）

        # --- 普通单（NormalOrder）---
        normal_list: List[NormalOrder] = list(pos_orders.normal)
        if normal_list:
            lines.append(f"    普通挂单：{len(normal_list)}（最多展示 {per_pos_cap}）")
            for j, o in enumerate(normal_list[:per_pos_cap]):
                lines.append(
                    f"      - [{j+1:02d}] 方向={o.side} 数量={_fmt(o.size, 6)} "
                    f"限价={_fmt(o.limit_px, 2)} 时间={_fmt_ts_ms(o.timestamp)}"
                )
    if len(positions) > max_positions:
        lines.append(f"- ...（还有 {len(positions) - max_positions} 个仓位未展示）")

    # 注意：按要求不使用 overview.open_orders（避免 raw dict 刷屏），
    # 挂单/止盈止损统一从 positions[i].orders / positions[i].orders.tpsl 读取并打印。

    return "\n".join(lines)


def print_account_overview(
    overview: AccountOverview,
    *,
    max_positions: int = 20,
    max_orders: int = 10,
) -> None:
    """
    打印 fetch_account_overview() 的结果（不打印 raw_user_state）。
    """
    print(format_account_overview(overview, max_positions=max_positions, max_orders=max_orders))
