"""
交易所连接管理函数
负责创建交易所实例和检查连接
"""

import ccxt
import sys
from typing import Optional


PROXY = "http://127.0.0.1:7890"

def create_exchange() -> ccxt.hyperliquid:
    """
    创建交易所实例

    Args:
        api_key: API密钥
        api_secret: API密钥
        api_password: API密码（OKX特有）
        proxy: 代理地址

    Returns:
        ccxt.okx: 交易所实例
    """
    try:

        exchange = ccxt.hyperliquid({
            "walletAddress": "0xc49390C1856502E7eC6A31a72f1bE31F5760D96D",  # /!\ Public address of your account/wallet
            "privateKey": "0xfe707e4e91e8ffdb1df1996ccd667e4bdf68c7b92a828c391551e582cfc056c0",  # Private key from the API wallet
        })

        # exchange = ccxt.okx({
        #     "apiKey": API_KEY,
        #     "secret": API_SECRET,
        #     "password": API_PASSWORD,  # OKX 特有
        #     "enableRateLimit": True,  # 启用速率限制，避免请求过快
        #     "timeout": 30000,  # 30秒超时
        #     "proxies": {
        #         "http": PROXY,
        #         "https": PROXY,
        #     },
        #     "options": {
        #         "defaultType": "spot",  # 默认现货交易
        #     }
        # })

        # 测试连接
        if not check_connection(exchange):
            print("\n❌ 连接失败，程序退出")
            sys.exit(1)

        return exchange
    except Exception as e:
        print(f"❌ 创建交易所实例失败: {e}")
        sys.exit(1)


def check_connection(exchange: ccxt.hyperliquid) -> bool:
    """
    测试交易所连接

    Args:
        exchange: 交易所实例
        proxy: 代理地址

    Returns:
        bool: 连接是否成功
    """
    if exchange is None:
        print("❌ 交易所实例未创建")
        return False

    try:
        print("🔍 正在测试连接...")
        exchange.load_markets()
        print("✅ 连接成功！")
        return True
    except ccxt.NetworkError as e:
        print(f"❌ 网络错误: {e}")
        print("💡 请检查：")
        print("   1. 网络连接是否正常")
        print(f"   2. 代理服务器是否运行（{PROXY}）")
        print("   3. API 密钥是否正确")
        return False
    except ccxt.ExchangeError as e:
        print(f"❌ 交易所错误: {e}")
        return False
    except Exception as e:
        print(f"❌ 未知错误: {e}")
        return False

