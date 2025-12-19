"""
性能监控模块
提供性能分析相关的工具函数和装饰器
"""
import functools
import time


def measure_time(func):
    """
    一个简单的装饰器，用于打印函数执行耗时
    
    只有耗时超过 10 秒的函数才会打印，避免刷屏
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
            # 只有耗时超过 10 秒才打印，避免刷屏
            if elapsed > 10:
                print(f"⏱️ [{func.__name__}] 耗时: {elapsed:.4f} 秒")
    return wrapper
