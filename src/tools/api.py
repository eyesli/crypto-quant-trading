import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from openai.types.responses import ResponseTextConfigParam
import pytz
from openai import OpenAI
import httpx
proxies = {
    'http': 'http://127.0.0.1:7890',
    'https':'https://127.0.0.1:7890',
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

    # client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),http_client=http_client)
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"))
    model="deepseek-chat"
    if is_in_discount_period():
        model="deepseek-reasoner"
        print(f"\033[32m当前时间在折扣期内，使用模型：{model}\033[0m")

    schema = {
        "type": "object",
        "properties": {
            "signal": {"type": "string", "enum": ["long", "short", "flat"]},
            "confidence": {"type": "number"},
            "reason": {"type": "string"}
        },
        "required": ["signal", "confidence", "reason"],
        "additionalProperties": False

    }
    text_cfg: ResponseTextConfigParam = {
        "format": {
            "type": "json_schema",
            "name": "trade_decision",
            "schema": schema,
        }
    }
    response = client.responses.create(
        model=model,
        instructions=prompt,
        input=user_message,
        max_output_tokens=800,
        text= text_cfg
    )

    return response


