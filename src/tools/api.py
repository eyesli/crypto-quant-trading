import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytz
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import (
    ChatCompletionMessageParam, ChatCompletionUserMessageParam, ChatCompletionSystemMessageParam,
)
from openai.types.chat.completion_create_params import ResponseFormatJSONObject

proxies = {
    'http': 'http://127.0.0.1:7890',
    'https': 'https://127.0.0.1:7890',
}

http_client = httpx.Client(
    proxy="http://127.0.0.1:7890",
    timeout=30.0,
    verify=False,
)


def is_in_discount_period():
    # 获取当前 UTC 时间
    utc_now = datetime.now(timezone.utc)
    # 定义北京时间时区
    beijing_tz = pytz.timezone('Asia/Shanghai')

    # 将 UTC 时间转换为北京时间
    beijing_now = utc_now.astimezone(beijing_tz)

    # 获取今天的日期
    today = beijing_now.date()

    # 定义时间范围
    start_time = beijing_tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=0, minutes=30))
    end_time = beijing_tz.localize(datetime.combine(today, datetime.min.time()) + timedelta(hours=8, minutes=30))

    # 判断当前时间是否在范围内
    return start_time <= beijing_now <= end_time


def call_deepseek(
        prompt: str,
        user_message: str,

) -> Any:
    load_dotenv()
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")

    model = "deepseek-chat"
    if is_in_discount_period():
        model = "deepseek-reasoner"
        print(f"\033[32m当前时间在折扣期内，使用模型：{model}\033[0m")

    system_msg: ChatCompletionSystemMessageParam = {
        "role": "system",
        "content": prompt,
    }

    user_msg: ChatCompletionUserMessageParam = {
        "role": "user",
        "content": user_message,
    }

    response_format: ResponseFormatJSONObject = {"type": "json_object"}

    resp = client.chat.completions.create(
        model=model,
        messages=[system_msg, user_msg],
        response_format=response_format,
        max_tokens=800,
        temperature=0.2,
    )

    return resp


if __name__ == '__main__':
    prompt = """你是一个加密货币交易专家，专注于分析市场趋势并提供交易建议。根据用户提供的市场数据和信息，做出以下决策：
                1. 如果市场显示出明显的上涨趋势，建议 "long"（做多）。
                2. 如果市场显示出明显的下跌趋势，建议 "short"（做空）。
                3. 如果市场趋势不明确或波动较大，建议 "flat"（观望）。
                在做出决策时，请考虑以下因素：
                - 技术指标（如移动平均线、相对强弱指数等）
                - 市场情绪和新闻事件
                - 历史价格走势和模式
                请确保你的建议基于合理的分析，并提供一个信心评分（0到1之间的数字）来表示你对该建议的信心程度。此外，请简要说明你的      
                决策依据。结果返回json格式
            """
    user_message = """ 当前市场数据显示，比特币价格在过去24小时内经历了显著的上涨，突破了多个关键阻力位。技术指标如相对强弱指数（RSI）显示出超买状态，但成交量也在增加，表明市场情绪积极。此外，近期的新闻报道强调了机构投资者对加密货币的兴趣增加，这可能进一步推动价格上涨。然而，考虑到市场的波动性，仍需谨慎行事。"""
    response = call_deepseek(prompt, user_message)
    print(response)
#         print(f"获取成交记录失败: {e}")
#         return []
