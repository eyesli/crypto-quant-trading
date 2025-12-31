import os
import time
from solana.rpc.api import Client
from solders.pubkey import Pubkey

# --- 1. 代理设置 (保持你刚才成功的设置) ---
# 如果你的 VPN 端口不是 7890，记得改！
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"

# --- 2. 连接主网 ---
# 建议用 Helius 的地址，如果还没申请，先用官方的顶一下
url = "https://api.mainnet-beta.solana.com"
client = Client(url, timeout=30)  # 增加超时时间防止波动

print(f"🔗 正在连接: {url} ...")

try:
    # --- 3. 锁定目标：Raydium Liquidity Pool V4 ---
    # 我们来看看这个交易所地址最近干了啥
    raydium_prog_id = Pubkey.from_string("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8")

    # --- 4. 获取它最近的一笔交易签名 (Signatures) ---
    print("🕵️ 正在查询 Raydium 的最新交易签名...")
    # limit=1 表示只拿最新的一条
    sigs_resp = client.get_signatures_for_address(raydium_prog_id, limit=1)

    if not sigs_resp.value:
        print("❌ 没找到交易记录？不应该啊。")
        exit()

    recent_sig = sigs_resp.value[0].signature
    print(f"✅ 捕获到最新交易哈希: {recent_sig}")
    print(f"   (你可以去 https://solscan.io/tx/{recent_sig} 对比着看)")

    # --- 5. 获取这笔交易的详细数据 (Transaction Details) ---
    print("📦 正在下载交易详情 (解析比较慢，请耐心等待)...")
    # max_supported_transaction_version=0 是必须的，否则解析不了新版交易
    tx_resp = client.get_transaction(recent_sig, max_supported_transaction_version=0)

    if tx_resp.value:
        # 打印日志 (Logs) - 这是量化最喜欢看的部分
        logs = tx_resp.value.transaction.meta.log_messages
        print("\n📜 --- 交易日志 (部分) ---")
        for i, log in enumerate(logs[:5]):  # 只打印前5行避免刷屏
            print(f"[{i}] {log}")
        print("...")

        # 简单判断发生了什么
        log_str = str(logs)
        if "Swap" in log_str:
            print("\n💡 这是一个 [Swap/交易] 操作！有人在买卖币。")
        elif "Initialize" in log_str:
            print("\n💡 这是一个 [建池子] 操作！可能有新币上线。")
        else:
            print("\n💡 其他类型的复杂交互。")

    else:
        print("❌ 交易详情获取失败（可能是节点索引还没更新）。")

except Exception as e:
    print(f"\n❌ 报错了: {e}")