from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from hyperliquid.info import Info

from src.tools.system_config import measure_time


@dataclass
class AccountOverview:
    raw_user_state: Dict[str, Any]
    positions: List[Dict[str, Any]]
    open_orders: List[Dict[str, Any]]

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
    用官方 SDK 的 Info 接口获取：
    - 账户权益/保证金
    - 永续仓位
    - 挂单（含止盈止损触发单）
    并打印类似你原 ccxt 版本的输出。
    """
    try:
        print("\n💼 正在获取账户状态...")
        us = info.user_state(address)  # Dict

        # ===== 余额/权益（USDC 维度）=====
        # Hyperliquid perp 的“权益”主要在 marginSummary / withdrawable 等字段里
        margin = us.get("marginSummary") or {}
        total_usdc = _to_float(margin.get("accountValue"))
        used_usdc  = _to_float(margin.get("totalMarginUsed"))
        free_usdc  = _to_float(us.get("withdrawable"))

        print("💰 账户余额概览")
        print(f"总权益:      {total_usdc if total_usdc is not None else '-'} USDC")
        print(f"可用余额:    {free_usdc if free_usdc is not None else '-'} USDC")
        print(f"已用保证金:  {used_usdc if used_usdc is not None else '-'} USDC")
        print("=" * 60 + "\n")

        # ===== 仓位（永续）=====
        # 常见字段：assetPositions -> [{ position: {...}, type: "oneWay" }]
        asset_positions = us.get("assetPositions") or []
        positions: List[Dict[str, Any]] = []
        for ap in asset_positions:
            pos = ap.get("position") or ap
            if isinstance(pos, dict):
                positions.append(pos)

        print("📌 正在获取挂单(open_orders)...")
        frontend_open_orders = info.frontend_open_orders(address) or []

        if not positions:
            print("⚪ 当前无任何永续仓位。\n")
        else:
            print("\n" + "=" * 80)
            print("📊 当前持仓详情 (含止盈止损状态)")
            print("=" * 80)

            for pos in positions:
                # 你原来 ccxt 的字段，这里做“尽量映射”
                coin = pos.get("coin") or pos.get("symbol") or pos.get("asset")
                szi  = _to_float(pos.get("szi") or pos.get("size") or pos.get("contracts"))
                entry_price = _to_float(pos.get("entryPx") or pos.get("entryPrice"))
                liq_price   = _to_float(pos.get("liquidationPx") or pos.get("liquidationPrice"))
                upnl        = _to_float(pos.get("unrealizedPnl") or pos.get("upnl"))
                leverage    = _to_float(pos.get("leverage"))
                notional    = _to_float(pos.get("positionValue") or pos.get("notional"))
                roe         = _to_float(pos.get("returnOnEquity") or pos.get("roe") or pos.get("percentage"))

                # side：Hyperliquid 常用 szi 正负表示方向
                side = None
                if szi is not None:
                    side = "long" if szi > 0 else ("short" if szi < 0 else None)

                # ===== 匹配 TP/SL（用方向 + 入场价判断）=====
                tp_orders: List[float] = []
                sl_orders: List[float] = []

                if entry_price is not None and side is not None:
                    for o in frontend_open_orders:
                        o_coin = o.get("coin") or o.get("symbol") or o.get("asset")
                        if o_coin != coin:
                            continue

                        # Hyperliquid order side 常见是 "B"/"A" 或 "buy"/"sell"
                        o_side = o.get("side") or o.get("dir")
                        # 多单平仓期望卖；空单平仓期望买
                        expected = "sell" if side == "long" else "buy"

                        def _norm_side(x):
                            if x is None: return None
                            x = str(x).lower()
                            if x in ("b", "buy", "long"): return "buy"
                            if x in ("a", "sell", "short"): return "sell"
                            return x

                        if _norm_side(o_side) != expected:
                            continue

                        trig = _extract_trigger_price(o)
                        px   = _to_float(o.get("limitPx") or o.get("price"))
                        check_price = trig if trig is not None else px
                        if check_price is None:
                            continue

                        if side == "long":
                            (tp_orders if check_price > entry_price else sl_orders).append(check_price)
                        else:  # short
                            (tp_orders if check_price < entry_price else sl_orders).append(check_price)

                # ===== 打印 =====
                print(f"🪙  交易对:     {coin or '-'}")
                print(f"    方向:         {side.upper() if side else '-'} -- {leverage if leverage is not None else '-'} 倍")

                if szi is not None:
                    print(f"    仓位数量:     {abs(szi)}")
                if notional is not None:
                    print(f"    名义价值:     {notional} USDC")
                if entry_price is not None:
                    print(f"    开仓均价:     {entry_price:.2f}")

                if upnl is not None:
                    print(f"    未实现盈亏:   {upnl} USDC")
                if roe is not None:
                    print(f"    收益率(ROE):  {roe:.2f}%")
                if liq_price is not None:
                    print(f"    预估强平价:   {liq_price:.2f}")

                print(f"    {'-' * 30}")
                if tp_orders:
                    tp_str = ", ".join([f"${p:.2f}" for p in sorted(tp_orders)])
                    print(f"    🎯 止盈挂单:   {tp_str}")
                else:
                    print(f"    🎯 止盈挂单:   -- 未设置 --")

                if sl_orders:
                    sl_str = ", ".join([f"${p:.2f}" for p in sorted(sl_orders)])
                    print(f"    🛡️ 止损挂单:   {sl_str}")
                else:
                    print(f"    🛡️ 止损挂单:   -- 未设置 --")

            print("=" * 80 + "\n")

        return AccountOverview(
            raw_user_state=us,
            positions=positions,
            open_orders=frontend_open_orders,
        )

    except Exception as e:
        print(f"❌ 获取账户信息时发生未知错误: {e}")
        raise