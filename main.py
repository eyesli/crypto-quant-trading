"""
主入口文件
启动 service 服务
"""

import sys
import time
from src.exchange_manager import create_exchange
from src.models import RegimeState
from src.service import start_trade

LOOP_SECONDS = 99960


def main() -> None:
    """
    CLI 入口（pyproject.toml 的 [project.scripts] 会调用这里）。
    """
    try:
        exchange = create_exchange()
        state = RegimeState()
        while True:
            start_trade(exchange,state)
            time.sleep(LOOP_SECONDS)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断程序")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 程序执行出错: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()


