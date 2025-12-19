from typing import Any, Dict, List, Optional, Iterable

from hyperliquid.info import Info

from src.account.manager import _to_float, parse_orders, embed_orders_into_positions
from src.data.models import AccountOverview, AccountState, MarginSummary, PerpPosition
from src.tools.performance import measure_time





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
