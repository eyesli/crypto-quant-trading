"""
交易所连接管理函数
负责创建交易所实例和检查连接
"""
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils.constants import MAINNET_API_URL
import os
import ccxt
import sys
from typing import Optional

# 配置信息

def create_okx_exchange() -> ccxt.okx:
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
        exchange = ccxt.okx({
            "apiKey": must_env("API_KEY"),
            "secret": must_env("API_SECRET"),
            "password": must_env("API_PASSWORD"),  # OKX 特有
            "enableRateLimit": True,  # 启用速率限制，避免请求过快
            "timeout": 30000,  # 30秒超时
            "options": {
                "defaultType": "swap",
            }
        })

        PROXY = "http://127.0.0.1:7890"
        exchange.proxies={
            "http": PROXY,
            "https": PROXY,
        }

        return exchange
    except Exception as e:
        print(f"❌ 创建交易所实例失败: {e}")
        sys.exit(1)


def create_hyperliquid_exchange() -> "Exchange":
    """
    创建 Hyperliquid 交易所实例

    Returns:
        Exchange: Hyperliquid 交易所实例
    """

    exchange = Exchange(
            wallet=Account.from_key(must_env("HL_PRIVATE_KEY")),
            base_url=MAINNET_API_URL,
            account_address=must_env("HL_WALLET_ADDRESS"),
            timeout=20.0,
        )
    return exchange

def must_env(key: str) -> str:
    v = os.environ.get(key)
    if not v:
        raise RuntimeError(f"Missing required env var: {key}")
    return v
