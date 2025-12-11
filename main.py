"""
主入口文件
启动 service 服务
"""

import sys
import time
from src.exchange_manager import create_exchange
from src.service import start


if __name__ == "__main__":


        try:
            exchange = create_exchange()
            while True:
                start(exchange)
                time.sleep(60)  # 每分钟运行一次
        except KeyboardInterrupt:
            print("\n\n⚠️  用户中断程序")
            sys.exit(0)
        except Exception as e:
            print(f"\n❌ 程序执行出错: {e}")
            import traceback
            traceback.print_exc()


