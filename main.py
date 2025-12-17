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

from src.models import RegimeState
from src.service import start_trade
from dotenv import load_dotenv

from src.tools.api import call_deepseek


def main() -> None:
    """
    CLI 入口（pyproject.toml 的 [project.scripts] 会调用这里）。
    """
    load_dotenv()
    deepseek = call_deepseek("你是量化交易助手，只能输出符合 JSON Schema 的结果。",
                             "ADX=32, NATR=1.1%, spread=5bps，给交易决策。")
    print(deepseek)
    sys.exit(0)
    try:
        wallet = Account.from_key(os.environ.get("HL_PRIVATE_KEY"))

        exchange = Exchange(
            wallet=wallet,
            base_url=MAINNET_API_URL,
            account_address=os.environ.get("HL_WALLET_ADDRESS"),
            timeout=10.0,
        )
        state = RegimeState()
        while True:
            start_trade(exchange,state)
            time.sleep(120)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断程序")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 程序执行出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()


