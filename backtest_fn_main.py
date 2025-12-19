"""backtest_fn_main.py

纯函数式回测一键运行：

  py backtest_fn_main.py

修改本文件顶部参数即可。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL

from src.backtest_fn import bt_export_trades_csv, bt_metrics, bt_report, run_backtest


def main() -> None:
    symbol = "ETH"
    initial_equity = 10_000.0

    # 默认：最近 30 天
    end = datetime.now()
    start = end - timedelta(days=30)

    fee_rate = 0.0006
    slippage = 0.001

    info = Info(base_url=MAINNET_API_URL, skip_ws=True)

    account = run_backtest(
        info,
        symbol=symbol,
        start=start,
        end=end,
        initial_equity=initial_equity,
        fee_rate=fee_rate,
        slippage=slippage,
    )

    m = bt_metrics(account)
    print(bt_report(m))

    # 导出 CSV
    out = f"backtest_trades_{symbol}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
    bt_export_trades_csv(account, out)
    print(f"trades exported: {out}")


if __name__ == "__main__":
    main()
