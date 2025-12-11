"""
策略模块

基于K线数据的简单量化策略，结合参考地址的方向判断，给出做多/做空、止损、平仓建议。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from statistics import mean
from typing import Dict, List, Optional

from src.market_data import AccountOverview


class TradeAction(str, Enum):
    """交易动作枚举"""
    BUY = "buy"  # 做多/买入
    SELL = "sell"  # 做空/卖出
    HOLD = "hold"  # 持有/观望


class RiskLevel(str, Enum):
    """风险等级枚举"""
    VERY_LOW = "very_low"  # 极低风险
    LOW = "low"  # 低风险
    MEDIUM = "medium"  # 中等风险
    HIGH = "high"  # 高风险
    VERY_HIGH = "very_high"  # 极高风险


class SentimentGrade(str, Enum):
    """情绪等级枚举"""
    EXTREMELY_BEARISH = "extremely_bearish"  # 极度看空 (-100 到 -80)
    VERY_BEARISH = "very_bearish"  # 非常看空 (-80 到 -60)
    BEARISH = "bearish"  # 看空 (-60 到 -40)
    SLIGHTLY_BEARISH = "slightly_bearish"  # 略微看空 (-40 到 -20)
    NEUTRAL = "neutral"  # 中性 (-20 到 20)
    SLIGHTLY_BULLISH = "slightly_bullish"  # 略微看多 (20 到 40)
    BULLISH = "bullish"  # 看多 (40 到 60)
    VERY_BULLISH = "very_bullish"  # 非常看多 (60 到 80)
    EXTREMELY_BULLISH = "extremely_bullish"  # 极度看多 (80 到 100)


@dataclass
class TradingSignal:
    """
    交易信号对象
    
    包含完整的交易决策信息，用于指导开仓、止盈、止损等操作。
    """
    # 核心交易信息
    action: TradeAction  # 交易动作：buy(做多) / sell(做空) / hold(观望)
    entry_price: float  # 建议开仓价格
    stop_loss: float  # 止损价格
    take_profit: float  # 止盈价格
    
    # 情绪与信心评估
    sentiment_score: float  # 情绪评分：-100(极度看空) 到 +100(极度看多)
    sentiment_grade: SentimentGrade = field(init=False)  # 情绪等级（基于 sentiment_score 自动计算）
    confidence: float  # 信心度：0.0 到 1.0，表示信号可靠性
    
    # 风险与仓位建议
    risk_level: RiskLevel  # 风险等级
    suggested_position_size_pct: float  # 建议仓位大小（占总权益的百分比，0.0 到 1.0）
    risk_reward_ratio: float  # 风险收益比（止盈距离 / 止损距离）
    
    # 分析依据
    reasons: List[str]  # 决策理由列表
    technical_indicators: Dict[str, float]  # 技术指标值（RSI, MACD, MA等）
    
    # 元数据
    timestamp: datetime  # 信号生成时间
    symbol: Optional[str] = None  # 交易对符号（如 "BTC/USDC:USDC"）
    
    def __post_init__(self):
        """初始化后处理：自动计算 sentiment_grade"""
        self.sentiment_grade = self._calculate_sentiment_grade(self.sentiment_score)
    
    @staticmethod
    def _calculate_sentiment_grade(score: float) -> SentimentGrade:
        """根据情绪评分计算情绪等级"""
        if score <= -80:
            return SentimentGrade.EXTREMELY_BEARISH
        elif score <= -60:
            return SentimentGrade.VERY_BEARISH
        elif score <= -40:
            return SentimentGrade.BEARISH
        elif score <= -20:
            return SentimentGrade.SLIGHTLY_BEARISH
        elif score < 20:
            return SentimentGrade.NEUTRAL
        elif score < 40:
            return SentimentGrade.SLIGHTLY_BULLISH
        elif score < 60:
            return SentimentGrade.BULLISH
        elif score < 80:
            return SentimentGrade.VERY_BULLISH
        else:
            return SentimentGrade.EXTREMELY_BULLISH
    
    def to_dict(self) -> Dict:
        """转换为字典格式，便于序列化和打印"""
        return {
            "action": self.action.value,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "sentiment_score": self.sentiment_score,
            "sentiment_grade": self.sentiment_grade.value,
            "confidence": self.confidence,
            "risk_level": self.risk_level.value,
            "suggested_position_size_pct": self.suggested_position_size_pct,
            "risk_reward_ratio": self.risk_reward_ratio,
            "reasons": self.reasons,
            "technical_indicators": self.technical_indicators,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
        }


def _get_closes(ohlcv: List[List[float]]) -> List[float]:
    return [candle[4] for candle in ohlcv]


def _simple_moving_average(values: List[float], period: int) -> Optional[float]:
    if len(values) < period or period <= 0:
        return None
    return mean(values[-period:])


def _relative_strength_index(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None

    gains = []
    losses = []
    for prev, curr in zip(values[-period - 1 : -1], values[-period:]):
        change = curr - prev
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    average_gain = mean(gains)
    average_loss = mean(losses)
    if average_loss == 0:
        return 100.0

    rs = average_gain / average_loss
    return 100 - (100 / (1 + rs))


def _macd(values: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, float]:
    def ema(series: List[float], period: int) -> List[float]:
        if len(series) < period or period <= 0:
            return []
        k = 2 / (period + 1)
        ema_values = [mean(series[:period])]
        for price in series[period:]:
            ema_values.append(price * k + ema_values[-1] * (1 - k))
        return ema_values

    slow_ema = ema(values, slow)
    fast_ema = ema(values, fast)
    if not slow_ema or not fast_ema:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}

    macd_line = [f - s for f, s in zip(fast_ema[-len(slow_ema) :], slow_ema)]
    if len(macd_line) < signal:
        macd_value = macd_line[-1]
        return {"macd": macd_value, "signal": 0.0, "histogram": macd_value}

    signal_line = ema(macd_line, signal)
    macd_value = macd_line[-1]
    signal_value = signal_line[-1]
    histogram = macd_value - signal_value
    return {"macd": macd_value, "signal": signal_value, "histogram": histogram}


def _swing_levels(ohlcv: List[List[float]]) -> Dict[str, float]:
    lows = [candle[3] for candle in ohlcv]
    highs = [candle[2] for candle in ohlcv]
    return {"recent_low": min(lows), "recent_high": max(highs)}

def run_complex_strategy(
    account_overview: AccountOverview,
    market_data: Optional[Dict] = None,
    symbol: str = "BTC/USDC:USDC",
) -> TradingSignal:
    """
    复杂策略主函数
    
    综合技术分析、情绪分析、巨鲸分析等多种因素，生成交易信号。
    
    Args:
        account_overview: 账户概览信息
        market_data: 市场数据（包含K线、ticker等）
        symbol: 交易对符号
    
    Returns:
        TradingSignal: 完整的交易信号对象
    """
    balances = account_overview.balances
    positions = account_overview.positions
    
    # 初始化变量
    reasons = []
    technical_indicators: Dict[str, float] = {}
    sentiment_score = 0.0  # 从 -100 到 +100
    confidence = 0.0  # 从 0.0 到 1.0
    action = TradeAction.HOLD
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    
    # TODO: 技术线分析 market_data
    # TODO: 强化学习分析(未来加强)
    # TODO: 新闻情绪分析
    # TODO: 巨鲸分析
    # TODO: 经济政策分析
    
    # 示例：如果 market_data 包含 OHLCV 数据，进行技术分析
    if market_data and isinstance(market_data, dict):
        ohlcv = market_data.get("ohlcv")
        if ohlcv and len(ohlcv) > 0:
            closes = _get_closes(ohlcv)
            latest_close = closes[-1] if closes else 0.0
            
            # 计算技术指标
            short_ma = _simple_moving_average(closes, 9)
            long_ma = _simple_moving_average(closes, 21)
            rsi = _relative_strength_index(closes)
            macd_values = _macd(closes)
            swings = _swing_levels(ohlcv)
            
            # 存储技术指标
            if short_ma:
                technical_indicators["MA_9"] = short_ma
            if long_ma:
                technical_indicators["MA_21"] = long_ma
            if rsi is not None:
                technical_indicators["RSI"] = rsi
            if macd_values:
                technical_indicators.update(macd_values)
            
            # 基于技术指标计算情绪和方向
            signal_count = 0
            bullish_signals = 0
            bearish_signals = 0
            
            # 均线分析
            if short_ma and long_ma:
                if short_ma > long_ma:
                    bullish_signals += 1
                    sentiment_score += 20
                    reasons.append("短期均线上穿长期均线，偏多")
                elif short_ma < long_ma:
                    bearish_signals += 1
                    sentiment_score -= 20
                    reasons.append("短期均线下穿长期均线，偏空")
                signal_count += 1
            
            # RSI 分析
            if rsi is not None:
                if rsi < 35:
                    bullish_signals += 1
                    sentiment_score += 15
                    reasons.append(f"RSI={rsi:.2f} 超卖，存在反弹机会")
                elif rsi > 65:
                    bearish_signals += 1
                    sentiment_score -= 15
                    reasons.append(f"RSI={rsi:.2f} 超买，下行风险增大")
                signal_count += 1
            
            # MACD 分析
            macd_hist = macd_values.get("histogram", 0.0)
            if macd_hist > 0:
                bullish_signals += 1
                sentiment_score += 15
                reasons.append("MACD 柱线为正，动能偏多")
            elif macd_hist < 0:
                bearish_signals += 1
                sentiment_score -= 15
                reasons.append("MACD 柱线为负，动能偏空")
            signal_count += 1
            
            # 确定交易方向
            if bullish_signals > bearish_signals:
                action = TradeAction.BUY
            elif bearish_signals > bullish_signals:
                action = TradeAction.SELL
            else:
                action = TradeAction.HOLD
                reasons.append("多空信号平衡，建议观望")
            
            # 计算信心度（基于信号一致性）
            if signal_count > 0:
                confidence = max(bullish_signals, bearish_signals) / signal_count
            else:
                confidence = 0.0
            
            # 设置开仓价格（使用最新收盘价）
            entry_price = latest_close
            
            # 计算止盈止损
            risk_buffer = 0.003  # 0.3% 波动缓冲
            if action == TradeAction.BUY:
                stop_loss = min(swings.get("recent_low", latest_close * 0.99), 
                              latest_close * (1 - risk_buffer))
                take_profit = latest_close + (latest_close - stop_loss) * 2.0  # 风险收益比 1:2
            elif action == TradeAction.SELL:
                stop_loss = max(swings.get("recent_high", latest_close * 1.01), 
                              latest_close * (1 + risk_buffer))
                take_profit = latest_close - (stop_loss - latest_close) * 2.0
            else:
                stop_loss = latest_close
                take_profit = latest_close
        else:
            # 无市场数据时的默认值
            reasons.append("缺少市场数据，无法生成有效信号")
            action = TradeAction.HOLD
    else:
        reasons.append("市场数据为空，建议观望")
        action = TradeAction.HOLD
    
    # 计算风险收益比
    if action != TradeAction.HOLD and entry_price > 0:
        if action == TradeAction.BUY:
            risk = entry_price - stop_loss
            reward = take_profit - entry_price
        else:  # SELL
            risk = stop_loss - entry_price
            reward = entry_price - take_profit
        
        if risk > 0:
            risk_reward_ratio = reward / risk
        else:
            risk_reward_ratio = 0.0
    else:
        risk_reward_ratio = 0.0
    
    # 根据信心度和风险收益比确定风险等级
    if confidence >= 0.8 and risk_reward_ratio >= 2.0:
        risk_level = RiskLevel.LOW
    elif confidence >= 0.6 and risk_reward_ratio >= 1.5:
        risk_level = RiskLevel.MEDIUM
    elif confidence >= 0.4:
        risk_level = RiskLevel.HIGH
    else:
        risk_level = RiskLevel.VERY_HIGH
    
    # 根据信心度和风险等级建议仓位大小
    if action == TradeAction.HOLD:
        suggested_position_size_pct = 0.0
    elif risk_level == RiskLevel.LOW and confidence >= 0.8:
        suggested_position_size_pct = 0.15  # 15% 仓位
    elif risk_level == RiskLevel.MEDIUM and confidence >= 0.6:
        suggested_position_size_pct = 0.10  # 10% 仓位
    elif confidence >= 0.4:
        suggested_position_size_pct = 0.05  # 5% 仓位
    else:
        suggested_position_size_pct = 0.02  # 2% 仓位（保守）
    
    # 确保 sentiment_score 在合理范围内
    sentiment_score = max(-100.0, min(100.0, sentiment_score))
    
    # 创建并返回交易信号对象
    return TradingSignal(
        action=action,
        entry_price=round(entry_price, 2),
        stop_loss=round(stop_loss, 2),
        take_profit=round(take_profit, 2),
        sentiment_score=round(sentiment_score, 2),
        confidence=round(confidence, 3),
        risk_level=risk_level,
        suggested_position_size_pct=round(suggested_position_size_pct, 4),
        risk_reward_ratio=round(risk_reward_ratio, 2),
        reasons=reasons,
        technical_indicators=technical_indicators,
        timestamp=datetime.now(),
        symbol=symbol,
    )


def determine_trade_plan(
    ohlcv: List[List[float]],
    reference_direction: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """
    根据K线数据输出交易计划。

    Args:
        ohlcv: K线列表 [timestamp, open, high, low, close, volume]
        reference_direction: 参考地址的方向（"long"/"short"），无记录则传入 None

    Returns:
        包含方向、止损、止盈和理由的字典
    """

    if not ohlcv:
        return {"direction": None, "stop_loss": None, "take_profit": None, "reason": "无K线数据"}

    closes = _get_closes(ohlcv)
    short_ma = _simple_moving_average(closes, 9)
    long_ma = _simple_moving_average(closes, 21)
    rsi = _relative_strength_index(closes)
    macd_values = _macd(closes)
    swings = _swing_levels(ohlcv)
    latest_close = closes[-1]

    direction = None
    confidence = 0
    reasons = []

    if short_ma and long_ma:
        if short_ma > long_ma:
            direction = "long"
            confidence += 1
            reasons.append("短期均线上穿长期均线，偏多")
        elif short_ma < long_ma:
            direction = "short"
            confidence += 1
            reasons.append("短期均线下穿长期均线，偏空")

    if rsi is not None:
        if rsi < 35:
            reasons.append("RSI 超卖，存在反弹机会")
            confidence += 1
            direction = direction or "long"
        elif rsi > 65:
            reasons.append("RSI 超买，下行风险增大")
            confidence += 1
            direction = direction or "short"

    macd_hist = macd_values.get("histogram", 0.0)
    if macd_hist > 0:
        reasons.append("MACD 柱线为正，动能偏多")
        confidence += 1
        direction = direction or "long"
    elif macd_hist < 0:
        reasons.append("MACD 柱线为负，动能偏空")
        confidence += 1
        direction = direction or "short"

    if reference_direction:
        if reference_direction == direction:
            confidence += 1
            reasons.append("参考地址方向一致，增加把握度")
        else:
            reasons.append("参考地址方向相反，需谨慎")
            confidence = max(confidence - 1, 0)
    else:
        reasons.append("参考地址暂无操作，忽略该因素")

    if direction is None:
        reasons.append("指标信号不一致，暂不下单")
        return {"direction": None, "stop_loss": None, "take_profit": None, "reason": "; ".join(reasons)}

    risk_buffer = 0.003  # 0.3% 波动缓冲
    if direction == "long":
        stop_loss = min(swings["recent_low"], latest_close * (1 - risk_buffer))
        take_profit = latest_close + (latest_close - stop_loss) * 2
    else:
        stop_loss = max(swings["recent_high"], latest_close * (1 + risk_buffer))
        take_profit = latest_close - (stop_loss - latest_close) * 2

    reasons.append(f"信心值 {confidence}（值越高越可靠）")

    return {
        "direction": direction,
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "reason": "; ".join(reasons),
    }
