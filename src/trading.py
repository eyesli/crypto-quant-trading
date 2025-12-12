from __future__ import annotations

from typing import Any, Dict, Literal, Optional

import ccxt

def open_perp_limit_position(
    exchange: ccxt.hyperliquid,
    symbol: str,
    direction: str,
    stop_loss: float,
    limit_price: Optional[float] = None,
    risk_pct: float = 0.01,
    leverage: float = 5.0,
    post_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    使用「限价单」开一个 Hyperliquid 永续合约仓位。

    Args:
        exchange:    交易所实例（ccxt.hyperliquid）
        symbol:      交易对，例如 "BTC/USDC"
        direction:   "LONG" 或 "SHORT"
        stop_loss:   止损价格，用于计算仓位大小（可为 None）
        limit_price: 限价价格；为 None 时默认用当前最新价 last
        risk_pct:    单笔最大风险占总权益比例（默认 1%）
        leverage:    杠杆倍数（假定你在网页端已经设置好）
        post_only:   是否只做挂单（Maker），防止吃单

    Returns:
        下单返回的 order dict，失败返回 None
    """
    try:
        if direction not in ("LONG", "SHORT"):
            print("⚠️ 未提供有效方向（必须是 LONG 或 SHORT），不下单。")
            return None

        # 1️⃣ 获取账户总权益（假设 USDC 做保证金）
        balance = exchange.fetch_balance()
        total = balance.get("total", {}) or {}
        equity = float(total.get("USDC") or total.get("USDT") or 0.0)

        if equity <= 0:
            print("⚠️ 账户总权益为 0，无法开仓。")
            return None

        # 2️⃣ 获取当前价格 & 处理限价价格
        ticker = exchange.fetch_ticker(symbol)
        last = ticker.get("last")
        if last is None:
            print("⚠️ 无法获取最新价格，取消开仓。")
            return None

        market_price = float(last)
        if limit_price is None:
            # 如果你不传，就默认用当前 last 作为限价（相当于稍微挂在现在这个价位）
            limit_price = market_price

        print(f"\n📌 当前 {symbol} 市价: {market_price:,.2f} USDC")
        print(f"📌 本次下单限价: {limit_price:,.2f} USDC")

        # 3️⃣ 计算仓位大小（根据最大可承受亏损 = equity * risk_pct）
        max_loss = equity * risk_pct  # 单笔亏损上限

        if direction == "LONG":
            price_diff = limit_price - stop_loss
        else:  # SHORT
            price_diff = stop_loss - limit_price

        amount: float
        if price_diff <= 0:
            print("⚠️ 止损价与限价不合理：无法用风险定仓。请提供合理 stop_loss。")
            return None

        # 假设按止损价离限价这么多空间来计算：亏损 = 仓位数量 * 价格差
        amount = max_loss / price_diff
        if amount <= 0:
            print("⚠️ 计算得到的仓位数量 <= 0，取消开仓。")
            return None

        # 4️⃣ 映射方向到 side
        side: Literal["buy", "sell"] = "buy" if direction == "LONG" else "sell"

        print("\n🧮 开仓参数预览（限价单）")
        print(f"方向:        {direction} ({side})")
        print(f"限价:        {limit_price:,.2f} USDC")
        print(f"下单数量:    {amount:.6f} {symbol.split('/')[0]}")
        print(f"账户总权益:  {equity:,.2f} USDC")
        print(f"单笔风险:    {risk_pct * 100:.2f}% ≈ {max_loss:,.2f} USDC")
        if stop_loss:
            print(f"止损价格:    {stop_loss:,.2f} USDC")
        print(f"杠杆(假定):  {leverage}x")
        print(f"只做挂单:    {post_only}")
        print("-" * 60)

        # 5️⃣ 真正下单：限价单
        order = exchange.create_order(
            symbol=symbol,
            type="limit",
            side=side,
            amount=amount,
            price=limit_price,
            params={
                # 有些 ccxt 交易所支持：
                # "timeInForce": "GTC",
                # hyperliquid 这边 ccxt 适配一般也会透传
                "postOnly": post_only,
            },
        )

        print("\n✅ 限价单已提交，订单信息：")
        print(order)
        return order

    except ccxt.NetworkError as e:
        print(f"❌ 网络错误（限价开仓失败）: {e}")
    except ccxt.ExchangeError as e:
        print(f"❌ 交易所错误（限价开仓失败）: {e}")
    except Exception as e:
        print(f"❌ 限价开仓过程中出现未知错误: {e}")

    return None
