---
name: a-share-tailpicker
description: Use when screening A股主板尾盘标的, 尾盘选股, 14:20/14:25 close-session candidates, or backtesting and reviewing the C 版 A-share tail-trading methodology.
---

# A股尾盘选股 (v4)

## Overview

Use this skill to run the C 版 A股主板尾盘选股 methodology as a repeatable Agent workflow: fetch free data (multi-source), filter non-C-version boards, score candidates with market/fundamental/sentiment/news 交叉验证, output buyable names with reasons, suggested price, position, and next-day actions, then backtest under a realistic fill/cost model and improve the rules under an anti-overfit guardrail.

This is research automation only. Always preserve the disclaimer that the output is not investment advice.

## v4 架构 (2026-06-17 重构)

策略层不再直接接触数据源,改为分层:

- `scripts/databackend.py` — 多源数据抽象。优先级:iFinD(运行在 Kimi Code 上时自动启用)→ AKShare 多端点(东财/新浪/腾讯/雪球)→ 直接东财 HTTP。结果落盘缓存,带 `source` 标签。
- `scripts/enrichment.py` — 09:00 富化缓存 bundle(市场状态、全市场板块共振、基本面、新闻情绪、热度),实盘 screen 只读缓存,缺失字段显式标 `missing` 并按规则封顶 grade 到 B。
- `scripts/execution.py` — fixture/live 共用的保守成交模型:限价单触及且当根 5min K 收盘在成交侧才成交;成本显式化(佣金+印花税卖出+沪市过户费+滑点)。
- `scripts/stats.py` — 分布/最大回撤/Sharpe/Calmar/胜率 Wilson CI/t 统计量/跳空止损频次/归因。
- `scripts/risk.py` — 组合风控 caps(总仓位/单行业/单票),替代裸 top-5。
- `scripts/tailpicker.py` — 策略主体、评分、CLI(`screen`/`backtest`/`enrich`)。

## Quick Start

```bash
# 1. 09:00 构建当日富化缓存(市场/板块/基本面/新闻/热度)
~/.claude/skills/a-share-tailpicker/.venv/bin/python ~/.claude/skills/a-share-tailpicker/scripts/tailpicker.py enrich --trade-date 2026-06-17

# 2. 14:20 预选 / 14:50 最终确认
~/.claude/skills/a-share-tailpicker/.venv/bin/python ~/.claude/skills/a-share-tailpicker/scripts/tailpicker.py screen --asof-time 14:20 --output reports/tailpick_today.md
~/.claude/skills/a-share-tailpicker/.venv/bin/python ~/.claude/skills/a-share-tailpicker/scripts/tailpicker.py backtest --days 5 --asof-time 14:50 --output reports/tailpick_backtest.md

# 3. walk-forward 回测(训练/OOS 分割,检测过拟合)
~/.claude/skills/a-share-tailpicker/.venv/bin/python ~/.claude/skills/a-share-tailpicker/scripts/tailpicker.py backtest --days 30 --asof-time 14:50 --walk-forward --output reports/bt_wf.md
```

报告默认 Markdown(`.md`/`.markdown`),需要结构化 JSON 用 `--format json` 或 `.json` 路径。

离线 fixture(无需网络,用于确定性验证):

```bash
~/.claude/skills/a-share-tailpicker/.venv/bin/python ~/.claude/skills/a-share-tailpicker/scripts/tailpicker.py screen --fixture ~/.claude/skills/a-share-tailpicker/references/fixture_market_week.json --asof-time 14:20 --output reports/fixture_screen.md
~/.claude/skills/a-share-tailpicker/.venv/bin/python ~/.claude/skills/a-share-tailpicker/scripts/tailpicker.py backtest --fixture ~/.claude/skills/a-share-tailpicker/references/fixture_market_week.json --asof-time 14:20 --walk-forward --output reports/fixture_backtest.md
```

## Workflow

1. Load [references/data-sources.md](references/data-sources.md) if data coverage, multi-source fallback, or schedule setup matters.
2. 先跑 `enrich` 构建当日富化缓存;实盘 screen 会自动加载缓存,缓存缺失时按需构建并落盘。
3. 14:20/14:25 执行 `tailpicker.py screen`。14:20 视为预选;真实下单前必须 14:45-14:50 复核。
4. 每个最终订单确认包含:`code`/`name`/`grade`/`score`/`reasons`/`suggested_price`/`position_pct`/`next_day_plan`/`cross_validation`,以及组合风控 `constraints_applied`。
5. 分层读取输出:
   - `final_orders`:严格可买层(已过组合风控 caps)。只有这一层可考虑执行。
   - `watchlist`:观察层,有尾盘信号但仍有阻碍或需 14:45-14:50 复核。
   - `market_notes`:当天可买/观察/平静的简短说明。
6. `market_state.state == "halt"` 或 final_orders 为空 → 不买,但仍汇总 watchlist/market_notes 供复盘。
7. 回测/改进:跑 `tailpicker.py backtest`(可加 `--walk-forward`),回测使用保守成交模型与显式成本;更新阈值前必须遵守 [Improvement Loop](#improvement-loop--反过拟合护栏) 护栏。

## C 版 Rules

Default universe: **全沪主板 `60` 系列**(排除科创 `688`),再按板块排除 `银行`、`农林牧渔`、ST/*ST(银行低波政策驱动、尾盘形态失效;农业投机季节性强、易被资金短线操控)。不再使用旧的手挑 100 只池(有选择/生存者偏差)。新增上市<60 交易日、涨跌停、弱流动性、极端换手、近期异常波动、近期重大负面新闻由 hard_filter 排除。

实盘默认股票池来源优先级:① 当日 spot 快照(enrichment stock_map,带真实名称/板块/ST/流动性,point-in-time)→ ② `all_codes()`(`stock_info_a_code_name` 全量代码表,板块用名称关键词兜底)。两条路都应用板块/ST 排除。`--limit 0`(默认)= 全大盘(~1600)。

Core flow:

1. Market state and halt check: trend, volatility, breadth, volume, index tail decline, limit-up/down ratio. 实盘从 enrichment 读取真实值。
2. Seven-layer funnel: hard exclusions, adaptive volume/price filter, intraday pattern, capital-flow proxy, MA trend, sector resonance, final ranking.
3. 交叉验证: never approve using indicators alone. Require at least two non-technical confirmations from market environment, company fundamentals/liquidity, sector resonance, public sentiment, or news/F10 risk. If the result is only technical pattern + proxy capital flow, reject it.
4. Suggested price: limit price equals observed price plus 0.2%-0.5% by grade, rounded to cents; if the scan is at 14:20, label the price as conditional on 14:45-14:50 confirmation.
5. Next-day exit: **v3.1 条件卖出策略**(取代旧的 S1-S7 矩阵)。次日开盘≥+0.5%立即获利了结,开盘≤-0.5%立即止损,其他情况盘中+0.5%止盈或 14:50 前时间止损。成交在回测中按保守模型执行(见 `execution.py`)。
6. Live 14:20 rule: with public-data live runs, allow only strong-confirmation and non-overheated names into the buyable list. A/B-grade names can be preselected only when capital proxy is strong, sector/liquidity cross-validation is present, tail gain is not overheated, the price is not pinned near the intraday high, and the last bar volume is not excessively concentrated. Other scored or active names go to `watchlist` only until a 14:45-14:50 rerun confirms volume, sector resonance, no negative pattern, and price at least 0.8% below the intraday high before a final buy decision.

### v3.1 阈值规则(当前生效,见 CHANGELOG)

回测口径见下方 [胜率与样本声明](#胜率与样本声明)。核心硬条件:

1. 当日涨幅(相对昨收)1%-4%
2. 前日涨幅 -2%~2%
3. 尾盘涨幅 0.8%-2.5%
4. 量比 0.8-3.0(实盘为标准 5 日量比;数据缺失时回退到尾盘/早盘代理量并标注 `volume_ratio_source`)
5. 日内位置 <75%
6. 价格距日内高点 ≤-0.8%
7. 资金流代理分 ≥60
8. 条件卖出(见上)
9. 交叉验证:市场/基本面流动性/板块/情绪新闻/板块动量/前日momentum 至少 2 项

## 胜率与样本声明

> ⚠️ 历史"76.5% 胜率 / +0.27% 均收益"是 v3.1 在 **乐观成交假设**(触及即成交)+ **实盘信号 stub**(ST/市值/PE/新闻/市场状态全写死)+ **错误的 pre_close**(用首根5min开盘当昨收)下得到的,与实盘不可比,在严格量化标准下不可信。

v4 修正了成交/成本模型与实盘信号后,**胜率与均收益必须用新口径在 ≥200 笔交易上重跑后再声明**。回测报告现已包含:

- 样本量、胜率点估计 + Wilson 95% CI、t 统计量
- 收益分布(均值/中位/标准差/偏度/p05/p95/最差N笔)
- 最大回撤、Sharpe(年化)、Calmar、profit factor
- 跳空止损频次与平均损失(尾部风险)
- 按等级/形态/板块/市场状态归因
- walk-forward 训练/OOS 分割与过拟合警告

在样本量 <30 或 t<2 时,报告会显式标注"不足以做统计显著结论"。

## Scheduled Run

建议每日调度:

```bash
PY=~/.claude/skills/a-share-tailpicker/.venv/bin/python
SCRIPT=~/.claude/skills/a-share-tailpicker/scripts/tailpicker.py

# 09:00 富化缓存(市场/板块/基本面/新闻/热度)
$PY $SCRIPT enrich --trade-date $(date +%Y-%m-%d)

# 14:20 预选
$PY $SCRIPT screen --asof-time 14:20 --output ~/tailpicker/reports/$(date +%Y%m%d)_1420.md

# 14:45-14:50 最终确认
$PY $SCRIPT screen --asof-time 14:50 --output ~/tailpicker/reports/$(date +%Y%m%d)_1450.md

# 次日 09:25/10:00/10:30/14:50 执行条件退出
```

## Improvement Loop — 反过拟合护栏

保留按失败簇迭代,但加护栏。**改阈值/权重前必须:**

1. 更新 `scripts/tailpicker.py` 的 `PARAMS_HASH`。
2. 在 [CHANGELOG.md](CHANGELOG.md) 记录:改了什么、为什么(哪个失败簇)、在哪个样本上验证、train/OOS 结果。
3. 在**新样本**(非推导出该改动的样本)上验证。
4. 跑 `--walk-forward`,确认 OOS 段没有显著劣化。

典型迭代方向(需在新样本验证后才合入):

- 个股孤立异动导致的假阳性 → 提高板块共振或资金流门槛。
- 弱市亏损 → 下调 bear 仓位系数或仅保留 S/A 级(脚本已执行;组合 caps 已在 bear 下收紧)。
- 漏掉好票 → 放宽 14:20 尾盘涨幅门槛,但保留 14:45 复核。
- 新闻/基本面失败 → 加大负面新闻扣分与黑名单时长。
- B 级 + 板块共振不足 → 维持硬规则:no-buy。
- 回测显示同行业扎堆 → 组合风控 caps(总/行业/单票)已生效。

Validate after edits:

```bash
python3 -m unittest discover -s ~/.claude/skills/a-share-tailpicker/tests
```

## 风险声明

本项目不保证收益或未来胜率。多源公开数据可能延迟、缺失或结构变化;回测受样本量、交易成本、滑点、跳空和成交可得性影响。任何标的、价格、仓位或退出计划仅供研究和复盘,实际交易风险自负。
