"""
市场数据获取函数
负责获取实时价格、K线数据等市场信息

性能说明：
- 多周期 OHLCV 拉取是典型网络 I/O，可用线程池并发加速。
- 但并发也可能触发限频或暴露交易所适配的“线程不安全”问题，默认使用小并发。
"""
import math
from dataclasses import dataclass
from typing import List, Literal, Dict
from typing import Optional
from decimal import Decimal
from typing import Dict, Any


from decimal import Decimal
from typing import Dict, Any, Optional, Iterable

import ccxt
import pandas as pd
import pandas_ta as ta
from ccxt import hyperliquid
from ccxt.base.types import Position, Balances
from hyperliquid.info import Info

from src.models import OrderBookInfo, MarketRegime, TimingState, SlopeState, Slope, PerpAssetInfo
import pandas as pd
import pandas_ta as ta

from src.tools.system_config import measure_time


@dataclass
class AccountOverview:
    balances: Balances
    positions: List[Position]

@measure_time
def compute_technical_factors(df: pd.DataFrame) -> pd.DataFrame:

    # 基础数据
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    # ==========================================
    # 1. 趋势与动量 (Trend & Momentum)
    # ==========================================
    # 均线组
    df["ema_20"] = ta.ema(close, length=20)  # [原有策略核心依赖]
    df["sma_50"] = ta.sma(close, length=50)
    df["ema_50"] = ta.ema(close, length=50)
    df["wma_50"] = ta.wma(close, length=50)

    # MACD [新增: 动能判断]
    macd = ta.macd(close)
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    df["macd_hist"] = macd["MACDh_12_26_9"]

    # 其他动能
    df["roc_10"] = ta.roc(close, length=10)
    df["mom_10"] = ta.mom(close, length=10)
    df["rsi_14"] = ta.rsi(close, length=14)

    # ADX [原有策略核心依赖]
    adx_df = ta.adx(high, low, close, length=14)
    df["adx_14"] = adx_df["ADX_14"]
    df["dmp_14"] = adx_df["DMP_14"]
    df["dmn_14"] = adx_df["DMN_14"]

    # ==========================================
    # 2. 均值回归 (Mean Reversion)
    # ==========================================
    # 布林带 [原有策略核心依赖]
    bbands = ta.bbands(close, length=20, lower_std=2.0, upper_std=2.0)
    df["bb_mid"] = bbands["BBM_20_2.0_2.0"]
    df["bb_upper"] = bbands["BBU_20_2.0_2.0"]
    df["bb_lower"] = bbands["BBL_20_2.0_2.0"]
    df["bb_width"] = bbands["BBB_20_2.0_2.0"]   # 带宽
    df["bb_percent"] = bbands["BBP_20_2.0_2.0"] # %B

    # 肯特纳通道 (Keltner Channel)
    kelt = ta.kc(high, low, close, length=20)
    df["kc_mid"] = kelt["KCBe_20_2"]
    df["kc_upper"] = kelt["KCUe_20_2"]
    df["kc_lower"] = kelt["KCLe_20_2"]

    # VWAP & AVWAP
    df["vwap"] = ta.vwap(high, low, close, vol)
    
    # AVWAP: 全局成交量加权均价 (简单的全历史版本)
    cum_pv = (close * vol).cumsum()
    cum_vol = vol.cumsum()
    df["avwap_full"] = cum_pv / cum_vol

    # Z-Score (价格相对20日均线的标准差倍数)
    mean_20 = close.rolling(20).mean()
    std_20 = close.rolling(20).std()
    df["zscore_20"] = (close - mean_20) / std_20

    # Williams %R
    df["williams_r"] = ta.willr(high, low, close, length=14)

    # ==========================================
    # 3. 波动率 (Volatility)
    # ==========================================
    df["atr_14"] = ta.atr(high, low, close, length=14)
    df["natr_14"] = df["atr_14"] / close  # 标准化ATR

    # [原有策略依赖] NATR平滑
    df["natr_ema"] = ta.ema(df["natr_14"], length=10)

    # 历史波动率 (Historical Volatility)
    log_ret = (close / close.shift(1)).apply(lambda x: math.log(x) if x > 0 else 0)
    df["hv_20"] = log_ret.rolling(20).std()
    df["hv_100"] = log_ret.rolling(100).std()
    df["hv_ratio"] = df["hv_20"] / df["hv_100"]

    # 分布特征 (Skew/Kurt)
    df["ret_skew_50"] = log_ret.rolling(50).skew()
    df["ret_kurt_50"] = log_ret.rolling(50).kurt()

    # ==========================================
    # 4. 结构与形态 (Structure & Pattern)
    # ==========================================
    # [原有策略依赖] 10日高低点 (用于结构止损)
    df["swing_low_10"] = low.rolling(10).min()
    df["swing_high_10"] = high.rolling(10).max()
    # 20日高低点 (用于突破判断)
    df["n_high"] = close.rolling(20).max()
    df["n_low"] = close.rolling(20).min()
    df["breakout_up"] = close.ge(df["n_high"]).astype(int)  # >=
    df["breakout_down"] = close.le(df["n_low"]).astype(int)  # <=

    # 分形高低点 (Swing High/Low 独立K线形态)
    df["swing_high_fractal"] = high[(high.shift(1) < high) & (high.shift(-1) < high)]
    df["swing_low_fractal"] = low[(low.shift(1) > low) & (low.shift(-1) > low)]

    # ==========================================
    # 5. 价量分析 (Volume)
    # ==========================================
    # [原有策略依赖] 放量判断
    df["vol_sma_20"] = ta.sma(vol, length=20)
    df["vol_ratio"] = vol / df["vol_sma_20"]  # 兼容旧名
    df["vol_spike_ratio"] = df["vol_ratio"]   # 兼容新名

    # 突破+放量
    df["breakout_up_with_vol"] = (
        (df["breakout_up"] == 1) & (df["vol_spike_ratio"] > 2.0)
    ).astype(int)

    # OBV
    df["obv"] = ta.obv(close, vol)

    # 简易 POC (Point of Control)
    price_min = close.min()
    price_max = close.max()
    if price_max > price_min:
        bins = 30
        bin_size = (price_max - price_min) / bins
        bin_index = ((close - price_min) / bin_size).astype(int).clip(0, bins - 1)
        vol_profile = vol.groupby(bin_index).sum()
        poc_bin = vol_profile.idxmax()
        poc_price = float(price_min + (poc_bin + 0.5) * bin_size)
        df["poc_full"] = poc_price
        df["price_to_poc_pct"] = (close - poc_price) / poc_price
    else:
        df["poc_full"] = float("nan")
        df["price_to_poc_pct"] = float("nan")

    # ==========================================
    # 6. 环境斜率判定 (Timing Logic - Critical)
    # ==========================================
    # [原有策略核心依赖] 必须做EMA平滑后再求Diff，否则噪音太大
    ema_len = 10
    df["adx_ema"] = ta.ema(df["adx_14"], length=ema_len)
    df["bbw_ema"] = ta.ema(df["bb_width"], length=ema_len)

    # 计算斜率
    df["adx_slope"] = df["adx_ema"].diff()
    df["bbw_slope"] = df["bbw_ema"].diff()

    return df

BaseRegime = Literal["trend", "range", "mixed", "unknown"]

@measure_time
def classify_trend_range(
    df: pd.DataFrame,
    prev: MarketRegime = MarketRegime.UNKNOWN,
) -> tuple[MarketRegime, Optional[float]]:
    """
    Regime: TREND / RANGE / MIXED (with hysteresis)

    - Enter TREND: ADX >= 26
    - Exit  TREND: ADX < 23
    - Enter RANGE: ADX <= 17
    - Exit  RANGE: ADX > 19

    prev: 上一次的 regime，用于迟滞（防抖）
    """
    if df is None or "adx_14" not in df.columns:
        return MarketRegime.UNKNOWN, None

    s = df["adx_14"].dropna()
    if len(s) < 50:
        return MarketRegime.UNKNOWN, None

    adx = float(s.iloc[-1])

    # ---------- Hysteresis ----------
    # 如果上一状态是 TREND：只有明显走弱才退出
    if prev == MarketRegime.TREND:
        if adx < 23:
            return MarketRegime.MIXED, adx
        return MarketRegime.TREND, adx

    # 如果上一状态是 RANGE：只有明显增强才退出
    if prev == MarketRegime.RANGE:
        if adx > 19:
            return MarketRegime.MIXED, adx
        return MarketRegime.RANGE, adx

    # ---------- From MIXED / UNKNOWN ----------
    if adx >= 26:
        return MarketRegime.TREND, adx
    if adx <= 17:
        return MarketRegime.RANGE, adx

    return MarketRegime.MIXED, adx


def classify_timing_state(df: pd.DataFrame, window: int = 200, k: float = 0.2) -> TimingState:
    def _state(series: Optional[pd.Series]) -> SlopeState:
        if series is None:
            return SlopeState(state=Slope.UNKNOWN, cur=None, eps=None)
        #指标还没算出来的那些 K 线”全部丢掉
        s = series.dropna()
        if len(s) < window:
            return SlopeState(state=Slope.UNKNOWN, cur=None, eps=None)
        #window=200（在 1h 下 ≈ 8.3 天
        w = s.iloc[-window:]

        cur = float(w.iloc[-1])
        #std = 0.8 意味着在过去的 200 根 K 线里，ADX 的变化速度（斜率）大部分时间（约 68% 的概率）在 -0.8 到 +0.8 这个范围内波动。
        '''
        想象 ADX 从 20 涨到 50（一个非常标准的趋势行情）：如果这个过程花了 30 根 K 线（30小时）。
        平均每根 K 线涨：$(50 - 20) / 30 = 1.0$。你看，1.0 的斜率是典型趋势行情的速度。
        所以，你的统计结果 std = 0.8 能够涵盖这种典型的波动，说明它准确地捕捉到了市场的脉搏。
        '''
        std = float(w.std())
        eps = std * k if std > 0 else 0.0
        if cur > eps:
            st = Slope.UP
        elif cur < -eps:
            st = Slope.DOWN
        else:
            st = Slope.FLAT
        return SlopeState(state=st, cur=cur, eps=eps)

    return TimingState(
        adx_slope=_state(df.get("adx_slope")),
        bbw_slope=_state(df.get("bbw_slope")),
    )

def ohlcv_to_df(ohlcv: List[List[float]]) -> pd.DataFrame:
    """
    将 ccxt 返回的 ohlcv 列表转换为 pandas DataFrame：
    columns = [timestamp, open, high, low, close, volume]
    """
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert('Asia/Shanghai')
    df.set_index("timestamp", inplace=True)
    return df


from typing import Optional


# 假设 OrderBookInfo 和 Info 类已经定义好了
@measure_time
def fetch_order_book_info(info:Info, symbol: str, depth_pct: float = 0.005) -> Optional[OrderBookInfo]:
    """
    获取盘口微观数据 (适配 Hyperliquid l2_snapshot 原生结构)
    :param info: hyperliquid.info.Info 实例
    :param symbol: 交易对 (例如 "BTC")
    :param depth_pct: 深度计算范围
    """
    try:
        # 1. 获取快照
        # 返回结构: {'coin': 'BTC', 'levels': [[bids...], [asks...]], 'time': 1765877408954}
        snapshot = info.l2_snapshot(symbol)

        if not snapshot or 'levels' not in snapshot:
            return None

        levels = snapshot['levels']
        if len(levels) < 2:
            return None

        # Hyperliquid 的 levels[0] 是 Bids (降序), levels[1] 是 Asks (升序)
        # 数据项格式: {'px': '86230.0', 'sz': '12.65384', 'n': 33}
        raw_bids = levels[0]
        raw_asks = levels[1]
        timestamp = snapshot.get('time', 0)

        # 2. 基础检查
        if not raw_bids or not raw_asks:
            return None

        # 提取最优报价 (注意 px 是字符串，需要转 float)
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
                break  # bids 是降序的，价格太低就不用算了

            current_bid_depth_val += p * q

        # --- 计算卖盘深度 ---
        current_ask_depth_val = 0.0
        for item in raw_asks:
            p = float(item['px'])
            q = float(item['sz'])

            if p > max_ask_threshold:
                break  # asks 是升序的，价格太高就不用算了

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
        # 建议打印详细的 error 以便调试结构变化
        print(f"⚠️ Error fetching orderbook for {symbol}: {e}")
        return None



def safe_decimal(x: Any, default: str = "0") -> Decimal:
    """
    安全转换为 Decimal：
    - None / 空值 → default
    - str / int / float → Decimal
    说明：Decimal 不能吃 None，所以统一走这里。
    """
    if x is None:
        return Decimal(default)
    # 避免 float 精度问题：统一转 str 再进 Decimal
    return Decimal(str(x))

@measure_time
def build_perp_asset_map(
    exchange,
    allowed_symbols: Optional[Iterable[str]] = None
) -> Dict[str, PerpAssetInfo]:
    """
    一次 metaAndAssetCtxs 请求，构建全市场永续合约状态快照（带完整注释 & 安全兜底）

    参数：
    - exchange: 你的交易所对象（需要有 exchange.info.meta_and_asset_ctxs()）
    - allowed_symbols: 允许的 symbol 白名单（例如 {"BTC","ETH"}）
        - None 表示不过滤，返回全市场
        - 传入 set/list/tuple 均可

    返回：
    {
      "BTC": {
        # ===== 静态元信息（合约规则）=====
        "symbol": "BTC",                     # 合约名称（universe.name）
        "size_decimals": 5,                  # 下单数量精度（最小下单单位的小数位）
        "max_leverage": 50,                  # 最大杠杆
        "only_isolated": False,              # 是否只支持逐仓

        # ===== 价格基准（估值 / 风控）=====
        "mark_price": Decimal(...),          # 标记价格：PnL/强平等风控基准
        "mid_price": Decimal(...),           # 盘口中间价：(bestBid+bestAsk)/2；可能为 None
        "oracle_price": Decimal(...),        # 预言机价格（外部参考）；可能缺失
        "prev_day_price": Decimal(...),      # 前一日参考价；可能缺失

        # ===== 资金费率（Funding）=====
        "funding_rate": Decimal(...),        # 当前 funding；可能为 None（极端/空市场）
        "premium": Decimal(...),             # 溢价（mark/oracle 偏离相关）；可能缺失

        # ===== 市场参与度（资金/活跃度）=====
        "open_interest": Decimal(...),       # 未平仓量 OI；可能为 None
        "day_notional_volume": Decimal(...), # 24h 名义成交额；可能缺失

        # ===== 微结构 / 滑点（冲击价）=====
        "impact_bid": Decimal(...),          # 冲击买价（impactPxs[0]）；可能缺失/为空
        "impact_ask": Decimal(...),          # 冲击卖价（impactPxs[1]）；可能缺失/为空

        # ===== 原始上下文（调试 / 回溯）=====
        "raw": {...}                         # 原始 ctx（不改动）
      }
    }
    """

    # -------------------------
    # 1) 一次性请求交易所快照
    # -------------------------
    meta, asset_ctxs = exchange.info.meta_and_asset_ctxs()
    universe = meta.get("universe", [])

    # 安全校验：官方保证 index 对齐，这里工程上仍建议 assert
    assert len(universe) == len(asset_ctxs), "universe 与 asset_ctxs 长度不一致（不应发生）"

    # 允许列表：统一转成 set，O(1) 判断
    allowed_set = set(allowed_symbols) if allowed_symbols is not None else None

    asset_map: Dict[str, PerpAssetInfo] = {}

    # -------------------------
    # 2) 按 index 对齐构建资产字典
    # -------------------------
    for u, ctx in zip(universe, asset_ctxs):
        symbol = u.get("name")  # BTC / ETH / ...

        # 防御：跳过异常数据
        if not symbol:
            continue

        # 白名单过滤（如果提供）
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
