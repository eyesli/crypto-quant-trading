"""
主入口文件
启动 service 服务
"""

import sys

from src.exchange_manager import create_exchange
from src.service import start


if __name__ == "__main__":
    try:
        exchange = create_exchange()

        start(exchange)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断程序")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 程序执行出错: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

