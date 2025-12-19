"""
主入口文件
启动 service 服务
"""
import os
import sys
import time

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils.constants import MAINNET_API_URL

from src.data.models import RegimeState
from src.core.engine import start_trade
from dotenv import load_dotenv


def main() -> None:

    load_dotenv()
    try:

        while True:
            exchange = Exchange(
                wallet=Account.from_key(os.environ.get("HL_PRIVATE_KEY")),
                base_url=MAINNET_API_URL,
                account_address=os.environ.get("HL_WALLET_ADDRESS"),
                timeout=10.0,
            )
            state = RegimeState()
            start_trade(exchange, state)
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断程序")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 程序执行出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()


