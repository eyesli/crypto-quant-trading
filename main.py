"""
主入口文件
启动 service 服务
"""

import sys
import time

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils.constants import MAINNET_API_URL

from src.models import RegimeState
from src.service import start_trade

HL_WALLET_ADDRESS = "0xc49390C1856502E7eC6A31a72f1bE31F5760D96D"
HL_PRIVATE_KEY = "0xfe707e4e91e8ffdb1df1996ccd667e4bdf68c7b92a828c391551e582cfc056c0"


def main() -> None:
    """
    CLI 入口（pyproject.toml 的 [project.scripts] 会调用这里）。
    """


    try:
        wallet = Account.from_key(HL_PRIVATE_KEY)

        exchange = Exchange(
            wallet=wallet,
            base_url=MAINNET_API_URL,
            account_address=HL_WALLET_ADDRESS,
            timeout=10.0,
        )
        state = RegimeState()
        while True:
            start_trade(exchange,state)
            time.sleep(99960)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断程序")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 程序执行出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()


