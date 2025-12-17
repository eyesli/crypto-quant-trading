"""
Shared helper utilities.

This module holds small, reusable helpers that are not strategy-specific.
Keep it dependency-light to avoid circular imports.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

# Timeframe -> milliseconds mapping
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


def candles_last_n_closed(
    info: Any,
    name: str,
    interval: str,
    limit: int = 500,
    safety_ms: int = 120_000,
        timeframe_ms=None,
):
    """
    Fetch the last N *closed* candles from Hyperliquid.

    safety_ms: shift endTime backwards to avoid partially formed candle.
               Typical: 60_000~120_000 (1~2 minutes).
    """
    if timeframe_ms is None:
        timeframe_ms = TIMEFRAME_MS
    if interval not in timeframe_ms:
        raise ValueError(f"Unknown interval={interval!r}. Known: {sorted(timeframe_ms.keys())}")

    now_ms = int(time.time() * 1000)
    end_ms = now_ms - int(safety_ms)
    start_ms = end_ms - timeframe_ms[interval] * int(limit)
    return info.candles_snapshot(name=name, interval=interval, startTime=start_ms, endTime=end_ms)


def hl_candles_to_ohlcv_list(candles: Iterable[dict[str, Any]]) -> list[list[float]]:
    """
    Convert Hyperliquid candle objects to ccxt-compatible OHLCV list.

    Output row format: [timestamp_ms, open, high, low, close, volume]
    timestamp uses candle["t"] (open time).
    """
    out: list[list[float]] = []
    for x in candles:
        out.append(
            [
                int(x["t"]),
                float(x["o"]),
                float(x["h"]),
                float(x["l"]),
                float(x["c"]),
                float(x["v"]),
            ]
        )
    return out

