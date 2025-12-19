from typing import Any, Dict, List, Optional

from hyperliquid.info import Info

from src.data.models import AccountOverview, AccountState, MarginSummary, PerpPosition
from src.tools.performance import measure_time




def _to_float(x) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except Exception:
        return None

def _extract_trigger_price(order: Dict[str, Any]) -> Optional[float]:
    # 兼容不同字段命名
    for k in ("triggerPx", "triggerPrice", "stopPx", "stopPrice"):
        v = order.get(k)
        if v is not None:
            return _to_float(v)

    # 有的返回会把触发信息放在 trigger / orderType 里
    trig = order.get("trigger") or order.get("orderType") or {}
    if isinstance(trig, dict):
        for k in ("triggerPx", "triggerPrice", "stopPx", "stopPrice"):
            v = trig.get(k)
            if v is not None:
                return _to_float(v)

    return None
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
def fetch_account_overview(info: Info, address: str) -> AccountOverview:
    """
    返回强类型：
    - AccountState（权益/保证金/时间戳/可提）
    - List[PerpPosition]（永续仓位）
    - open_orders（暂保留 dict）
    """
    print("\n💼 正在获取账户状态...")
    us: Dict[str, Any] = info.user_state(address) or {}

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
    for ap in asset_positions:
        # 兼容：ap 可能是 {type, position:{...}} 或直接就是 position dict
        pos_dict = ap.get("position") if isinstance(ap, dict) else None
        pos_dict = pos_dict if isinstance(pos_dict, dict) else (ap if isinstance(ap, dict) else None)
        if not isinstance(pos_dict, dict):
            continue

        # coin 必须有，否则跳过
        coin = pos_dict.get("coin") or pos_dict.get("symbol") or pos_dict.get("asset")
        if not coin:
            continue

        positions.append(PerpPosition.from_dict(pos_dict))

    # --- orders ---
    print("📌 正在获取挂单(open_orders)...")
    frontend_open_orders = info.frontend_open_orders(address) or []
    if not isinstance(frontend_open_orders, list):
        frontend_open_orders = []

    # ---（可选）保持你原来的打印行为，但不要影响返回强类型 ---
    print("💰 账户余额概览")
    total_usdc = state.margin_summary.account_value
    used_usdc = state.margin_summary.total_margin_used
    free_usdc = state.withdrawable

    print(f"总权益:      {total_usdc if total_usdc is not None else '-'} USDC")
    print(f"可用余额:    {free_usdc if free_usdc is not None else '-'} USDC")
    print(f"已用保证金:  {used_usdc if used_usdc is not None else '-'} USDC")
    print("=" * 60 + "\n")

    return AccountOverview(
        state=state,
        positions=positions,
        open_orders=frontend_open_orders,
        raw_user_state=us,
    )
