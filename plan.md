# A-Share Tailpicker Strategy Refactoring & Backtesting Plan

## Objective
重构A股尾盘选股策略，使其满足：
- **胜率目标：70%**（次日最高价 > 买入价的比例）
- **收益率目标：正收益**（次日平均收益率为正）
- **使用场景：尾盘14:30买入，次日卖出（做T）**

## Current Problems
1. 策略过于复杂，7层漏斗+交叉验证，实际回测胜率不达标
2. 回测退出逻辑粗糙（基于S1-S7固定规则），没有精确利用次日最高价/最低价/VWAP
3. 选股条件可能过度过滤，错失有效信号
4. 没有针对"次日冲高概率"做优化

## Strategy Refactoring (v3.0)
### Core Philosophy: 胜率优先，简化信号，聚焦次日冲高概率

#### 选股条件（精简）
1. **尾盘涨幅**: 0.5% ≤ tail_gain_pct ≤ 2.5%（有拉升但不过度）
2. **量比**: 1.2 ≤ volume_ratio ≤ 4.0（有放量配合）
3. **日内位置**: day_position_pct ≤ 85%（不在最高点）
4. **价格距离日内高点**: price_to_day_high_pct ≤ -0.5%（有空间）
5. **资金流向**: capital_flow_score ≥ 55（有资金流入）
6. **当日涨跌幅**: 1% ≤ day_return ≤ 7%（强势但不过度）
7. **排除**: ST、极端换手(>20%)、前日大涨(>5%)、前日大跌(<-5%)
8. **市场状态**: 非halt
9. **板块共振**: sector_score ≥ 8（同板块有呼应）

#### 评分体系（简化）
- 尾盘涨幅: 0-20分
- 量比: 0-15分
- 资金流向: 0-20分
- 板块共振: 0-15分
- MA状态: 0-10分
- 日内位置: 0-10分
- 市场状态: ±5分

#### 买入/观察分层
- 总分 ≥ 70: A级（可买入）
- 总分 ≥ 80: S级（可买入）
- 50 ≤ 总分 < 70: B级（观察）
- 弱市（bear）只保留S/A级

#### 回测指标（新增）
- **胜率**: 次日最高价 > 买入价的比例
- **隔夜胜率**: 次日开盘价 > 买入价的比例
- **理论最大收益**: 次日最高价 - 买入价
- **理论最大亏损**: 次日最低价 - 买入价
- **平均收益**: 次日VWAP - 买入价
- **实际收益（开盘卖）**: 次日开盘价 - 买入价
- **实际收益（收盘卖）**: 次日收盘价 - 买入价

## Stage 1: Strategy Refactoring
- Rewrite `tailpicker.py` with simplified scoring and backtesting logic
- Update `SKILL.md` with new v3.0 rules
- Update test cases

## Stage 2: Data Collection & Backtesting
- Use `stock_finance_data` (同花顺) for historical data:
  - `get_price` for daily OHLCVWAP (次日数据)
  - `realtime_price` for 14:30 intraday snapshots (买入时点)
- Backtest period: past 3 months (2026-03-16 to 2026-06-16)
- Stock universe: 沪主板 60-series (100 stocks)

## Stage 3: Iterative Optimization
- Analyze backtest results
- Adjust thresholds if win rate < 70%
- Iterate until target is met

## Stage 4: Git Push
- Commit changes
- Push to origin

## Data Source Plan
### Primary: stock_finance_data (同花顺)
- `get_price`: daily OHLCVWAP for next-day analysis (max 10 tickers per call)
- `realtime_price`: 14:30 intraday snapshot for entry price (max 3 tickers per call)

### Fallback: AKShare (free)
- `stock_zh_a_hist_min_em`: 5-minute bars for 14:30 price extraction
- `stock_zh_a_hist`: daily bars for next-day OHLC

## Execution Steps
1. ✅ Read current codebase and understand existing logic
2. 🔄 Write plan.md (this file)
3. ⏳ Refactor strategy in `tailpicker.py`
4. ⏳ Build backtest engine with new metrics
5. ⏳ Collect 3-month historical data
6. ⏳ Run backtest and analyze results
7. ⏳ Iterate if needed
8. ⏳ Update tests and documentation
9. ⏳ Git commit and push
