# 项目重构总结

## 新的目录结构

```
src/
├── core/              # 核心业务逻辑
│   ├── __init__.py
│   └── engine.py      # 交易引擎（原 service.py）
│
├── data/              # 数据层
│   ├── __init__.py
│   ├── fetcher.py     # 数据获取（OHLCV、订单簿、资产信息）
│   ├── indicators.py  # 技术指标计算
│   └── analyzer.py    # 市场分析（regime、timing等）
│
├── strategy/          # 策略层
│   ├── __init__.py
│   ├── regime.py      # 市场体制判断（classify_vol_state, decide_regime）
│   ├── signals.py     # 信号生成（direction, trigger, validity, score, build_signal）
│   └── planner.py     # 交易计划生成（signal_to_trade_plan）
│
├── account/           # 账户管理
│   ├── __init__.py
│   └── manager.py     # 账户辅助函数（account_total_usdc, find_position, position_to_state）
│
├── models/            # 数据模型（保持不变）
│   └── types.py       # 所有数据类（原 models.py）
│
├── tools/             # 工具函数
│   ├── utils.py
│   ├── api.py
│   ├── performance.py # 性能监控（原 system_config.py）
│   └── system_config.py # 向后兼容（已废弃）
│
└── scripts/           # 脚本
    └── monitor.py     # 监控脚本（原 monitor_hl_address.py）
```

## 文件重命名

- `service.py` → `core/engine.py`
- `account.py` → 保留（AccountOverview 定义），辅助函数移到 `account/manager.py`
- `monitor_hl_address.py` → `scripts/monitor.py`
- `market_data.py` → 拆分为 `data/fetcher.py`, `data/indicators.py`, `data/analyzer.py`
- `strategy.py` → 拆分为 `strategy/regime.py`, `strategy/signals.py`, `strategy/planner.py`
- `trading.py` → 辅助函数移到 `account/manager.py` 或删除
- `tools/system_config.py` → `tools/performance.py`（性能监控工具）

## 导入路径更新

### 主要变更：

1. **数据获取**：
   - `from src.market_data import ohlcv_to_df` → `from src.data.fetcher import ohlcv_to_df`
   - `from src.market_data import compute_technical_factors` → `from src.data.indicators import compute_technical_factors`
   - `from src.market_data import classify_trend_range` → `from src.data.analyzer import classify_trend_range`

2. **策略**：
   - `from src.strategy import classify_vol_state` → `from src.strategy.regime import classify_vol_state`
   - `from src.strategy import build_signal` → `from src.strategy.signals import build_signal`
   - `from src.strategy import signal_to_trade_plan` → `from src.strategy.planner import signal_to_trade_plan`

3. **账户**：
   - `from src.account import fetch_account_overview` → 保持不变（仍在 account.py）
   - `from src.trading import account_total_usdc` → `from src.account.manager import account_total_usdc`

4. **核心引擎**：
   - `from src.service import start_trade` → `from src.core.engine import start_trade`

5. **工具函数**：
   - `from src.tools.system_config import measure_time` → `from src.tools.performance import measure_time`

## 函数归类

### data/fetcher.py
- `ohlcv_to_df()`
- `fetch_order_book_info()`
- `build_perp_asset_map()`
- `safe_decimal()`

### data/indicators.py
- `compute_technical_factors()`

### data/analyzer.py
- `classify_trend_range()`
- `classify_timing_state()`

### strategy/regime.py
- `classify_vol_state()`
- `decide_regime()`
- `_q_state()` (内部辅助函数)

### strategy/signals.py
- `compute_direction()`
- `compute_trigger()`
- `compute_validity_and_risk()`
- `score_signal()`
- `build_signal()`

### strategy/planner.py
- `signal_to_trade_plan()`

### account/manager.py
- `account_total_usdc()`
- `find_position()`
- `position_to_state()`

### core/engine.py
- `start_trade()` (原 service.py)
