import functools
import time


def measure_time(func):
    """
    一个简单的装饰器，用于打印函数执行耗时
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.perf_counter()  # 比 time.time() 精度更高
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            end_time = time.perf_counter()
            elapsed = end_time - start_time
            # 只有耗时超过 0.1秒 才打印，避免刷屏（可选）
            if elapsed > 0:
                print(f"⏱️ [{func.__name__}] 耗时: {elapsed:.4f} 秒")
    return wrapper