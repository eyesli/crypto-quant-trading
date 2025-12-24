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

from src.data.exchange_manager import create_okx_exchange, create_hyperliquid_exchange
from src.data.models import RegimeState
from src.core.engine import start_trade
from dotenv import load_dotenv


def main() -> None:

    load_dotenv()
    try:
        okx_exchange = create_okx_exchange()
        okx_exchange.load_markets()
        hyperliquid = create_hyperliquid_exchange()
        while True:

            state = RegimeState()
            start_trade(hyperliquid,okx_exchange,state)
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


