from account import fetch_account_overview_sdk
import os
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

HL_WALLET_ADDRESS = "0xc49390C1856502E7eC6A31a72f1bE31F5760D96D"
HL_PRIVATE_KEY = "0xfe707e4e91e8ffdb1df1996ccd667e4bdf68c7b92a828c391551e582cfc056c0"
base_url = "https://api.hyperliquid.xyz"

if __name__ == "__main__":
    wallet = Account.from_key(HL_PRIVATE_KEY)

    info = Info(base_url)
    ex = Exchange(
        wallet=wallet,
        base_url=base_url,
        account_address=HL_WALLET_ADDRESS,
        timeout=10.0,
    )
    mids = ex.info.all_mids("")
    # order = ex.order(name="BTC",
    #                  is_buy=True,
    #                  sz=0.001,
    #                  limit_px=10000,
    #                  order_type={"limit": {"tif": "Gtc"}},
    #                  reduce_only=False)

    # 3) TP
    coin = "BTC"
    close_is_buy = False  # 平多卖出
    sz = 0.001
    tp_px = 99000.0
    sl_px = 15000.0

    # •    "tp" = Take
    # Profit（止盈）
    # •    "sl" = Stop
    # Loss（止损）
    # tp = ex.order(
    #     name=coin,
    #     is_buy=close_is_buy,
    #     sz=sz,
    #     limit_px=10000,
    #     order_type={"trigger": {"triggerPx": float(tp_px), "isMarket": True, "tpsl": "tp"}},
    #     reduce_only=True,
    # )
    #
    # # 4) SL
    # sl = ex.order(
    #     name=coin,
    #     is_buy=close_is_buy,
    #     sz=sz,
    #     limit_px=10000,
    #     order_type={"trigger": {"triggerPx": float(sl_px), "isMarket": True, "tpsl": "sl"}},
    #     reduce_only=True,
    # )

    # ✅ 查询账户概览（用 Info）
    orders = fetch_account_overview_sdk(info, HL_WALLET_ADDRESS)
    print("user_state keys:", orders)
    # print("user_state keys:", sl)
