from typing import Optional

from src.exchange_manager import create_exchange
from src.market_data import fetch_account_overview, fetch_market_data
from src.strategy import determine_trade_plan, run_complex_strategy

SYMBOL = "BTC/USDC:USDC"

REFERENCE_ADDRESS = "0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"
import ccxt

def reference_direction_from_address() -> Optional[str]:
    """
    获取参考地址的方向。

    这里先占位，未来可以接入链上交易记录，提取该地址最近的做多/做空方向。
    当前返回 None，表示无参考信号。
    """
    return None


def start(exchange:ccxt.hyperliquid):
    # 创建交易所实例并初始化连接

    # 获取账户概览
    account_overview = fetch_account_overview(exchange)
    market_data = fetch_market_data(exchange)

    # 获取交易决策
    plan = run_complex_strategy(
        account_overview,market_data
    )

    # fetch_market_data();

    # ticker = exchange.fetch_ticker(SYMBOL)
    # current_price = ticker['last']
    # exchange.create_order(
    #     symbol=SYMBOL,
    #     type="market",
    #     side="sell",
    #     amount=0.00043,
    #     price=current_price,
    #     params={"reduceOnly": True,  # 只平仓，不反向开新仓
    #             "slippage": 0.01,  # 可选：控制滑点，比如 1%（默认是 5%）
    #             },
    # )
    # 获取实时行情
    ticker = exchange.fetch_ticker(SYMBOL)
    last = ticker.get("last")
    limit_px = last
    # open_perp_limit_position(
    #     exchange=exchange,
    #     symbol=SYMBOL,
    #     direction="LONG",
    #     stop_loss=88000,
    #     limit_price=limit_px,
    #     risk_pct=0.01,
    #     leverage=5.0,
    #     post_only=False,  # 想强制只做挂单，就改 True
    # )

    # print("\n✅ 数据获取与策略计算完成！")
