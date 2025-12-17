# 专业单标的量化交易系统 · 决策流水线（Decision Pipeline）

本项目实现了一套专业级、单标的量化交易决策系统，采用分层架构：
环境判定（Regime）→ 信号生成（Signal）→ 动作规划（Action）→ 风险控制（Risk）→ 执行（Execution）。

系统目标：
- 可解释（Explainable）
- 可回测（Backtest-ready）
- 可扩展（Extensible）
- 风险优先（Risk-first）

---

## 核心设计原则

1. 不是任何时候都交易
2. 不是有信号就交易
3. 不是亏了就反手
4. 风险控制永远高于信号质量

---

## 系统整体架构（单标的）

Market Data  
→ Regime Decision  
→ Account Risk Guard  
→ Position State Machine  
→ Signal Engine  
→ Action Planner（旧仓维护在这里）  
→ Risk & Position Sizing  
→ Order Builder  
→ Execution & Reconciliation  
→ State Update

---

# 单轮交易决策完整流程

以下流程每个周期（如 1h）执行一次。

---

## Step 0｜构建统一交易上下文（Trade Context）

目的：将所有原始数据封装成只读上下文对象，避免模块之间直接访问外部数据。

Context 包含：
- 账户信息：equity / available_margin / current_position / avg_entry / leverage
- 市场信息：mark_price / mid_price / funding / open_interest
- 合约规则：size_decimals / max_leverage / only_isolated
- K线与指标：ATR / ADX / BBW / MA / timing_state
- 当前 Regime 决策结果

输出：TradeContext

---

## Step 1｜市场环境判定（Regime Decision）

模块：decide_regime(...)

职责：判断当前市场环境是否允许交易，以及允许哪种交易行为。

只回答以下问题：
- 是否允许新风险（allow_new_entry）
- 是否允许趋势 / 均值策略
- 是否进入严格入场模式（strict_entry）
- 风险缩放比例（risk_scale）
- 冷却缩放比例（cooldown_scale）

重要说明：
- 不生成交易信号
- 不管理仓位
- 不执行下单

decide_regime 是“交通灯”，不是司机。

---

## Step 2｜账户级硬风控（Account Risk Guard）

目的：即使 Regime 允许交易，账户层仍可强制熔断。

典型规则：
- 日内最大回撤触发
- 连续止损次数超限
- 可用保证金不足
- 当前杠杆过高

可能结果：
- 覆盖为 STOP_ALL
- 或降级为 NO_NEW_ENTRY

---

## Step 3｜仓位状态机（Position State Machine）

目的：明确当前仓位所处行为状态，防止频繁反复操作。

常见状态：
- FLAT（空仓）
- LONG_HOLDING
- SHORT_HOLDING
- REDUCE_ONLY
- COOLDOWN

---

## Step 4｜信号生成（Signal Engine）

目的：判断是否存在逻辑上值得下注的交易机会。

4.1 方向层（Direction）
- LONG / SHORT / NONE

4.2 触发层（Trigger）
- entry_ok（是否允许入场）
- add_ok（是否允许加仓）
- reverse_entry_ok（是否允许反手）

4.3 逻辑有效性（Thesis）
- original_thesis_invalidated（原逻辑是否被证伪）
- trend_exhausted（趋势是否衰竭）

输出：SignalSnapshot

---

## Step 5｜动作规划（Action Planner）

Action Planner 是系统核心模块，也是“旧仓维护”的唯一合法位置。

行为优先级（严格顺序）：

5.1 STOP_ALL
- 强制平仓
- 撤销所有挂单
- 禁止任何新动作

5.2 NO_NEW_ENTRY（旧仓维护模式）
- 禁止：ENTER / ADD / FLIP
- 允许：MOVE_SL（移动止损）、REDUCE（减仓）、EXIT（平仓）

5.3 OK
- 空仓：允许 ENTER
- 持仓：允许 MOVE_SL / REDUCE / ADD / FLIP

说明：
- 旧仓维护 = 只允许降低风险
- 加仓 / 反手 = 新风险，只能在 OK 下发生

输出：PlannedActions[]

---

## Step 6｜仓位与风险计算（Risk & Position Sizing）

核心思想：用风险预算算仓位，而不是拍脑袋。

标准公式：
risk_budget = equity × base_risk_pct × regime.risk_scale
qty = risk_budget / stop_distance

约束条件：
- 按 size_decimals 向下取整
- 不超过 max_leverage
- 不超过最大名义仓位限制
- strict_entry 为 True 时进一步降仓

输出：SizingResult

---

## Step 7｜订单构造（Order Builder）

目的：将动作转换为交易所可执行订单。

包含：
- 入场订单（limit / market，考虑 impact）
- 止损单（stop-market / stop-limit）
- 止盈单（分批）
- 订单 TTL / 自动撤单

输出：OrderIntent[]

---

## Step 8｜执行与对账（Execution & Reconciliation）

流程：
- 下单
- 获取成交回报
- 更新本地仓位 / 账户快照
- 记录完整日志（regime / signal / action / reason）

---

## Step 9｜状态更新（State Update）

更新系统状态：
- prev_base
- last_trade_ts
- cooldown_until
- position_state

目的：确保下一轮决策不会重复下单或状态错乱。

---

# 系统级铁律（必须遵守）

1. Regime 不碰仓位
2. 旧仓维护 ≠ 加仓
3. 反手 = 原逻辑失效 + 新逻辑成立
4. 任何增加最坏风险的动作都不属于管理

---

## 一句话总结

专业量化系统的核心不是“找到信号”，而是“在对的环境，用对的权限，做对的动作”。