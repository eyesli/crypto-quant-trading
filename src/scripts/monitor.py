#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
实时监控 Hyperliquid 账户状态（含止盈止损监控版）
功能：
- 获取账户权益、保证金
- 获取仓位详情
- ✅ 新增：区分展示普通挂单（Limit）和止盈止损单（TP/SL/Trigger）
- ✅ 修复：使用 frontendOpenOrders 获取更全的订单信息
"""

import time
from datetime import datetime
import requests

# Hyperliquid Info API
API_URL = "https://api.hyperliquid.xyz/info"

# 替换为你要监控的地址
ADDRESS = "0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"

# 轮询间隔
POLL_INTERVAL = 5
RECENT_FILLS_LIMIT = 10


def format_chinese_number(num: float) -> str:
    abs_num = abs(num)
    if abs_num >= 1_0000_0000:
        return f"{num / 1_0000_0000:.2f}亿"
    elif abs_num >= 10_000:
        return f"{num / 10_000:.2f}万"
    else:
        return f"{num:,.2f}"


def fetch_state(address: str):
    payload = {"type": "clearinghouseState", "user": address}
    try:
        resp = requests.post(API_URL, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if not isinstance(data, list) else data[0]
    except Exception as e:
        print(f"获取状态失败: {e}")
        return {}


# ----------------------------------------------------------
# 🔍 修改：使用 frontendOpenOrders 获取所有订单（含TP/SL）
# ----------------------------------------------------------
def fetch_all_open_orders(address: str):
    """
    frontendOpenOrders 能获取到：
    1. 普通限价单 (Limit)
    2. 止盈止损/触发单 (Stop/Take Profit) -> 带有 isTrigger: True 字段
    """
    payload = {
        "type": "frontendOpenOrders",
        "user": address,
    }
    try:
        resp = requests.post(API_URL, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"获取挂单失败: {e}")
        return []


def fetch_recent_fills(address: str, limit: int = RECENT_FILLS_LIMIT):
    payload = {"type": "userFills", "user": address}
    try:
        resp = requests.post(API_URL, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        fills = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])

        # 按时间倒序
        fills_sorted = sorted(fills, key=lambda x: int(x.get("time", 0)), reverse=True)
        return fills_sorted[:limit]
    except Exception:
        return []


def summarize(state: dict, all_orders: list, fills: list) -> dict:
    margin = state.get("marginSummary", {})
    account_value = float(margin.get("accountValue", 0))
    margin_used = float(margin.get("totalMarginUsed", 0))

    positions = []
    for ap in state.get("assetPositions", []):
        pos = ap.get("position", {})
        szi = float(pos.get("szi", 0))
        if szi == 0: continue  # 忽略空仓位

        positions.append({
            "coin": pos.get("coin"),
            "side": "做多" if szi > 0 else "做空",
            "size": abs(szi),
            "entry": float(pos.get("entryPx", 0)),
            "leverage": pos.get("leverage", {}).get("value"),
            "upnl": float(pos.get("unrealizedPnl", 0)),
            "roe": float(pos.get("returnOnEquity", 0)),
            "pos_value": float(pos.get("positionValue", 0))
        })

    # -----------------------------------------------
    # 拆分订单：普通挂单 vs 触发单(TP/SL)
    # -----------------------------------------------
    normal_orders = []
    trigger_orders = []

    for o in all_orders:
        # 判断是否为触发单
        is_trigger = o.get("isTrigger", False) or o.get("orderType") == "Trigger"

        # 提取关键信息
        order_info = {
            "coin": o.get("coin"),
            "side": o.get("side"),  # 'B' or 'A'
            "size": float(o.get("sz", 0)),
            "limit_px": float(o.get("limitPx", 0)),
            "trigger_px": float(o.get("triggerPx", 0)),  # 触发价格
            "trigger_cond": o.get("triggerCondition", ""),  # 触发条件
            "is_tpsl": o.get("isPositionTpsl", False),  # 是否为仓位附带的止盈止损
            "timestamp": int(o.get("timestamp", 0))
        }

        if is_trigger:
            trigger_orders.append(order_info)
        else:
            normal_orders.append(order_info)

    return {
        "account_value": account_value,
        "margin_used": margin_used,
        "positions": positions,
        "normal_orders": normal_orders,  # 普通限价单
        "trigger_orders": trigger_orders,  # 止盈止损单
        "fills": fills,
    }


def print_summary(summary: dict):
    print("\n" + "=" * 80)
    print(f"📍 监控地址：{ADDRESS}  |  🕒 {datetime.now().strftime('%H:%M:%S')}")

    # 1. 账户概况
    print(
        f"💰 权益：{format_chinese_number(summary['account_value'])} U   📌 保证金：{format_chinese_number(summary['margin_used'])} U")

    # 2. 持仓信息
    positions = summary["positions"]
    if positions:
        print("-" * 40)
        for p in positions:
            print(f"🪙 {p['coin']} {p['side']} {p['leverage']}x")
            print(f"   数量: {format_chinese_number(p['size'])} ({format_chinese_number(p['pos_value'])}U)")
            print(f"   均价: {p['entry']:.4f}")
            pnl_icon = "🟢" if p['upnl'] >= 0 else "🔴"
            print(f"   盈亏: {pnl_icon} {format_chinese_number(p['upnl'])} U (ROE: {p['roe'] * 100:.2f}%)")
    else:
        print("⚪ 无持仓")

    # 3. 止盈止损 / 触发单 (新增)
    trigger_orders = summary["trigger_orders"]
    print(f"\n⚡ 止盈止损/触发单 ({len(trigger_orders)})")
    if trigger_orders:
        for t in trigger_orders:
            side_str = "买入平空" if t['side'] == 'B' else "卖出平多"
            cond_str = t['trigger_cond']  # "Above" or "Below" 等

            # 尝试推断是止盈还是止损
            # (这只是简单推断，准确判断需要结合持仓方向，这里仅作展示)
            type_label = "触发单"
            if t['is_tpsl']:
                type_label = "仓位TP/SL"

            print(f"   🎯 {t['coin']} | {side_str} | {type_label}")
            print(f"      触发价: {t['trigger_px']} ({cond_str})")
            print(f"      数量: {format_chinese_number(t['size'])}")
    else:
        print("   ⚪ 无")

    # 4. 普通挂单
    normal_orders = summary["normal_orders"]
    print(f"\n📋 普通限价挂单 ({len(normal_orders)})")
    if normal_orders:
        for o in normal_orders[:5]:  # 只显示前5个
            side_str = "买入" if o['side'] == 'B' else "卖出"
            print(f"   📝 {o['coin']} {side_str} | 价格: {o['limit_px']} | 数量: {format_chinese_number(o['size'])}")
    else:
        print("   ⚪ 无")

    # 5. 成交记录
    fills = summary["fills"]
    print(f"\n📒 最新成交")
    if fills:
        for f in fills[:3]:
            side = "买入" if f['side'] == 'B' else "卖出"
            ts = datetime.fromtimestamp(int(f['time']) / 1000).strftime("%H:%M:%S")
            print(
                f"   ✅ {ts} | {f['coin']} {side} | 价: {float(f['px']):.4f} | 量: {format_chinese_number(float(f['sz']))}")

    print("=" * 80 + "\n")


def main():
    print(f"🚀 开始监控，按 Ctrl+C 退出...")
    prev_summary = None

    while True:
        try:
            state = fetch_state(ADDRESS)
            all_orders = fetch_all_open_orders(ADDRESS)
            fills = fetch_recent_fills(ADDRESS)

            summary = summarize(state, all_orders, fills)

            # 简单去重：如果数据和上次完全一样就不打印，避免刷屏
            # 这里为了演示效果，每次轮询如果不报错就打印，或者你可以把下面这行注释掉来强制刷新
            # if summary != prev_summary:
            print_summary(summary)
            prev_summary = summary

        except KeyboardInterrupt:
            print("\n退出监控")
            break
        except Exception as e:
            print(f"⚠️ 发生错误: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()