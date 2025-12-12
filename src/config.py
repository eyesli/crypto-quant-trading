"""
项目级配置常量（集中管理，避免各处散落重复定义）。

说明：
- 你当前策略与数据链路会依赖 timeframe 名称（例如 "1m"/"1h"/"4h"）。
- 把它们集中成一个常量，方便后续在：
  - 拉取 K 线
  - 多周期信号汇总
  - 打印/可视化
 里保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal


TimeframeGroup = Literal["trigger", "core", "background"]


@dataclass(frozen=True)
class TimeframeSetting:
    """
    单个 timeframe 的配置。

    - limit：拉取K线条数
    - group：手动分组（触发/核心/背景）
    - weight：该周期对最终 score 的权重（⚠️不做自动归一化，请手动保证总和=1）
    """

    limit: int
    group: TimeframeGroup
    weight: float


# 每个 timeframe 的配置（集中管理）
# 说明：
# - 你要求“手动分组 + 手动权重”，所以权重不会在代码里自动归一化。
# - 请手动保证 weight 总和 = 1.0，否则 score 的尺度会变化，触发阈值也会跟着失真。
TIMEFRAME_SETTINGS: Final[dict[str, TimeframeSetting]] = {
    # —— 短周期：只负责入场触发（噪音大，权重压低）
    "1m":  TimeframeSetting(limit=500, group="trigger", weight=0.08),
    "15m": TimeframeSetting(limit=500, group="trigger", weight=0.12),

    # —— 核心周期：趋势 + 动能（绝对主力）
    "1h":  TimeframeSetting(limit=200, group="core", weight=0.35),
    "4h":  TimeframeSetting(limit=150, group="core", weight=0.35),

    # —— 背景周期：趋势过滤（存在即可）
    "1d":  TimeframeSetting(limit=120, group="background", weight=0.10),
    "1w":  TimeframeSetting(limit=104, group="background", weight=0.00),
}


# =========================
# Data fetching performance
# =========================
# 是否并发拉取多周期 OHLCV（K线）。
# - 优点：网络 I/O 并发，整体速度更快（多周期延迟不再串行叠加）
# - 风险：部分交易所/适配器对“同一个 exchange 实例多线程并发请求”不一定完全线程安全；
#         并发也可能更容易触发限频（Rate Limit）。
# 建议：先用小并发（2~3），稳定后再逐步加。
OHLCV_FETCH_MAX_WORKERS: Final[int] = 3



