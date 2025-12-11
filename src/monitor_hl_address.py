#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
实时监控 Hyperliquid 上某个地址的永续合约账户状态（中文版本）
功能：
- 获取账户总权益
- 获取保证金占用
- 获取所有币种的仓位信息：方向、数量、杠杆、未实现盈亏、ROE、仓位面值
- 数字转换成人类易读的中文单位（万 / 亿）
- 监听变化自动打印
"""

import time
import requests

# Hyperliquid Info API（无需 API Key，可公开调用）
API_URL = "https://api.hyperliquid.xyz/info"

# 需要监控的钱包地址
ADDRESS = "0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"

# 轮询间隔（秒）
POLL_INTERVAL = 5


# ----------------------------------------------------------
# 🀄 中文数字格式化：把大数字转换成 万 / 亿 方便阅读
# ----------------------------------------------------------
def format_chinese_number(num: float) -> str:
    """
    数字转中文单位：
      12_345 → 1.23万
      56_000_000 → 5600万
      987_654_321 → 9.88亿
    """
    abs_num = abs(num)

    if abs_num >= 1_0000_0000:
        return f"{num / 1_0000_0000:.2f}亿"
    elif abs_num >= 10_000:
        return f"{num / 10_000:.2f}万"
    else:
        return f"{num:,.2f}"


# ----------------------------------------------------------
# 查询 Hyperliquid 永续合约的账户状态（clearinghouseState）
# ----------------------------------------------------------
def fetch_state(address: str):
    """
    请求 Hyperliquid API 获取某地址的永续账户信息
    请求体：
      {
        "type": "clearinghouseState",
        "user": <钱包地址>
      }
    """
    payload = {
        "type": "clearinghouseState",
        "user": address
    }

    resp = requests.post(API_URL, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    # 如果返回是列表（代理封装情况），取第0项
    if isinstance(data, list):
        return data[0]
    return data


# ----------------------------------------------------------
# 提取我们关心的字段：
#   - 账户总权益 accountValue
#   - 总保证金占用 totalMarginUsed
#   - 逐币种仓位详情
# ----------------------------------------------------------
def summarize(state: dict) -> dict:
    """
    把 API 原始结构拆成可读的数据结构
    """

    # marginSummary 中包括账户整体信息
    margin = state.get("marginSummary", {})
    # 账户总权益（单位：USDC）
    account_value = float(margin.get("accountValue", 0))
    # 保证金占用
    margin_used = float(margin.get("totalMarginUsed", 0))

    # 仓位列表
    positions = []
    # assetPositions 为各个币种的仓位结构
    for ap in state.get("assetPositions", []):
        pos = ap.get("position", {})

        # szi：仓位大小（正=做多，负=做空）
        szi = float(pos.get("szi", 0))

        # 多空方向
        side = "做多" if szi > 0 else "做空" if szi < 0 else "空仓"

        # entryPx：开仓均价
        entry = float(pos.get("entryPx", 0))

        # leverage：杠杆信息
        leverage = pos.get("leverage", {}).get("value", None)
        lev_type = pos.get("leverage", {}).get("type", None)

        # unrealizedPnl：未实现盈亏
        upnl = float(pos.get("unrealizedPnl", 0))

        # returnOnEquity：收益率（ROE）
        roe = float(pos.get("returnOnEquity", 0))

        # positionValue：仓位名义价值（USD）
        pos_value = float(pos.get("positionValue", 0))

        positions.append({
            "coin": pos.get("coin"),      # 币种
            "side": side,                 # 做多 / 做空 / 空仓
            "size": abs(szi),             # 仓位数量（绝对值）
            "entry": entry,               # 开仓均价
            "leverage": leverage,         # 杠杆倍数
            "lev_type": lev_type,         # cross / isolated
            "upnl": upnl,                 # 未实现盈亏（USDC）
            "roe": roe,                   # 收益率（小数，如0.12）
            "pos_value": pos_value        # 仓位面值
        })

    return {
        "account_value": account_value,
        "margin_used": margin_used,
        "positions": positions
    }


# ----------------------------------------------------------
# 输出报告（中文）
# ----------------------------------------------------------
def print_summary(summary: dict):
    print("\n" + "=" * 80)
    print(f"📍 监控地址：{ADDRESS}")

    # 账户总权益
    print(f"💰 账户总权益：{format_chinese_number(summary['account_value'])}（USDC）")

    # 总保证金占用
    print(f"📌 保证金占用：{format_chinese_number(summary['margin_used'])}（USDC）")

    positions = summary["positions"]
    print(f"📊 当前持仓：{len(positions)} 个币种")

    if not positions:
        print("⚪ 当前未持有任何永续合约仓位")
        return

    print("-" * 80)

    # 每一个币种的仓位信息
    for p in positions:
        print(f"🪙 币种：{p['coin']}   │ 方向：{p['side']}")
        print(f"📦 仓位数量：{format_chinese_number(p['size'])}")
        print(f"💼 仓位名义价值：{format_chinese_number(p['pos_value'])} USDC")
        print(f"🎯 开仓均价：{p['entry']:.2f}")

        # 杠杆信息
        if p["leverage"]:
            lev_label = f"{p['leverage']} 倍（{p['lev_type']}）"
        else:
            lev_label = "无"

        print(f"⚙️ 杠杆：{lev_label}")

        # 未实现盈亏
        print(f"📈 未实现盈亏：{format_chinese_number(p['upnl'])} USDC")

        # 收益率ROE
        print(f"📉 收益率（ROE）：{p['roe'] * 100:.2f}%")

        print("-" * 80)


# ----------------------------------------------------------
# 主程序：轮询监控
# ----------------------------------------------------------
def main():
    print(f"开始实时监控 Hyperliquid 永续账户（中文输出）")
    print(f"地址：{ADDRESS}")
    print(f"轮询间隔：{POLL_INTERVAL} 秒\n")

    prev = None

    while True:
        try:
            state = fetch_state(ADDRESS)
            summary = summarize(state)

            # 只有在数据变化时才打印
            if summary != prev:
                print_summary(summary)
                prev = summary

        except Exception as e:
            print(f"❌ 错误：{e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
