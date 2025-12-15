"""
交易所连接管理函数
负责创建交易所实例和检查连接

⚠️ 安全说明：
- 你要求“直接硬编码”，我按需实现了。
- 强烈建议：不要把真实私钥提交到 git 仓库；最好只在本机/私有环境使用。
"""

from __future__ import annotations

import ccxt
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils.constants import MAINNET_API_URL
