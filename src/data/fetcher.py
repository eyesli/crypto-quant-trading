"""
数据获取模块
负责从交易所获取 OHLCV、订单簿、资产信息等原始数据
"""
from __future__ import annotations

from typing import List, Optional, Dict, Iterable, Any
from decimal import Decimal

import pandas as pd
from hyperliquid.info import Info

from src.data.models import OrderBookInfo, PerpAssetInfo
from src.tools.performance import measure_time


def ohlcv_to_df(ohlcv: List[List[float]]) -> pd.DataFrame:
    """
    将 ccxt 返回的 ohlcv 列表转换为 pandas DataFrame：
    columns = [timestamp, open, high, low, close, volume]
    """
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert('Asia/Shanghai')
    df.set_index("timestamp", inplace=True)
    return df


@measure_time
def fetch_order_book_info(info: Info, symbol: str, depth_pct: float = 0.005) -> Optional[OrderBookInfo]:
    """
    获取盘口微观数据 (适配 Hyperliquid l2_snapshot 原生结构)
    :param info: hyperliquid.info.Info 实例
    :param symbol: 交易对 (例如 "BTC")
    :param depth_pct: 深度计算范围
    """
    try:
        # 1. 获取快照
        snapshot = info.l2_snapshot(symbol)

        if not snapshot or 'levels' not in snapshot:
            return None

        levels = snapshot['levels']
        if len(levels) < 2:
            return None

        # Hyperliquid 的 levels[0] 是 Bids (降序), levels[1] 是 Asks (升序)
        raw_bids = levels[0]
        raw_asks = levels[1]
        timestamp = snapshot.get('time', 0)

        # 2. 基础检查
        if not raw_bids or not raw_asks:
            return None

        # 提取最优报价
        best_bid = float(raw_bids[0]['px'])
        best_ask = float(raw_asks[0]['px'])

        # 防御：防止出现负价格或0价格
        if best_bid <= 0 or best_ask <= 0:
            return None

        # 3. 计算 Spread (使用 Mid Price)
        mid_price = (best_ask + best_bid) / 2
        spread = best_ask - best_bid

        # 转换 bps
        spread_bps = (spread / mid_price) * 10_000 if mid_price > 0 else 0.0

        # 4. 计算有效深度 (Weighted Depth by Price Range)
        min_bid_threshold = mid_price * (1 - depth_pct)
        max_ask_threshold = mid_price * (1 + depth_pct)

        # --- 计算买盘深度 ---
        current_bid_depth_val = 0.0
        for item in raw_bids:
            p = float(item['px'])
            q = float(item['sz'])

            if p < min_bid_threshold:
                break

            current_bid_depth_val += p * q

        # --- 计算卖盘深度 ---
        current_ask_depth_val = 0.0
        for item in raw_asks:
            p = float(item['px'])
            q = float(item['sz'])

            if p > max_ask_threshold:
                break

            current_ask_depth_val += p * q

        # 5. 计算不平衡度 (Imbalance)
        total_depth = current_bid_depth_val + current_ask_depth_val
        imbalance = 0.0
        if total_depth > 0:
            imbalance = (current_bid_depth_val - current_ask_depth_val) / total_depth

        return OrderBookInfo(
            symbol=symbol,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid_price,
            spread_bps=spread_bps,
            bid_depth_value=current_bid_depth_val,
            ask_depth_value=current_ask_depth_val,
            imbalance=imbalance,
            timestamp=timestamp
        )

    except Exception as e:
        print(f"⚠️ Error fetching orderbook for {symbol}: {e}")
        return None


def safe_decimal(x: Any, default: str = "0") -> Decimal:
    """
    安全转换为 Decimal：
    - None / 空值 → default
    - str / int / float → Decimal
    """
    if x is None:
        return Decimal(default)
    return Decimal(str(x))


@measure_time
def build_perp_asset_map(
    exchange,
    allowed_symbols: Optional[Iterable[str]] = None
) -> Dict[str, PerpAssetInfo]:
    """
    一次 metaAndAssetCtxs 请求，构建全市场永续合约状态快照

    参数：
    - exchange: 交易所对象（需要有 exchange.info.meta_and_asset_ctxs()）
    - allowed_symbols: 允许的 symbol 白名单（例如 {"BTC","ETH"}）

    返回：
    {
      "BTC": PerpAssetInfo(...),
      "ETH": PerpAssetInfo(...),
      ...
    }
    """
    # 1) 一次性请求交易所快照
    meta, asset_ctxs = exchange.info.meta_and_asset_ctxs()
    universe = meta.get("universe", [])

    assert len(universe) == len(asset_ctxs), "universe 与 asset_ctxs 长度不一致（不应发生）"

    # 允许列表：统一转成 set，O(1) 判断
    allowed_set = set(allowed_symbols) if allowed_symbols is not None else None

    asset_map: Dict[str, PerpAssetInfo] = {}

    # 2) 按 index 对齐构建资产字典
    for u, ctx in zip(universe, asset_ctxs):
        symbol = u.get("name")

        if not symbol:
            continue

        if allowed_set is not None and symbol not in allowed_set:
            continue

        # impactPxs 可能缺失/为空，做兜底
        impact_pxs = ctx.get("impactPxs") or [None, None]
        impact_bid_raw = impact_pxs[0] if len(impact_pxs) > 0 else None
        impact_ask_raw = impact_pxs[1] if len(impact_pxs) > 1 else None

        asset_map[symbol] = PerpAssetInfo(
            # Static metadata
            symbol=symbol,
            size_decimals=u.get("szDecimals"),
            max_leverage=u.get("maxLeverage"),
            only_isolated=u.get("onlyIsolated", False),

            # Pricing / risk anchors
            mark_price=safe_decimal(ctx.get("markPx")),
            mid_price=safe_decimal(ctx.get("midPx")),
            oracle_price=safe_decimal(ctx.get("oraclePx")),
            prev_day_price=safe_decimal(ctx.get("prevDayPx")),

            # Funding
            funding_rate=safe_decimal(ctx.get("funding")),
            premium=safe_decimal(ctx.get("premium")),

            # Participation / activity
            open_interest=safe_decimal(ctx.get("openInterest")),
            day_notional_volume=safe_decimal(ctx.get("dayNtlVlm")),

            # Impact
            impact_bid=safe_decimal(impact_bid_raw),
            impact_ask=safe_decimal(impact_ask_raw),

            # Raw ctx
            raw=ctx,
        )

    return asset_map
