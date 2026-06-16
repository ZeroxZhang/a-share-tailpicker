# A-Share Tailpicker Skill / A股尾盘选股 Skill

[中文](#中文) | [English](#english)

Codex skill for C-version A-share Shanghai main-board close-session screening, watchlist generation, and next-day backtesting.

C 版 A 股沪主板尾盘选股 Codex skill，支持 14:20 预选、14:50 最终确认、观察池输出和次日回测。

> Research automation only. Not investment advice.
>
> 本项目仅用于策略研究、自动化筛选和复盘，不构成投资建议。

---

## 中文

### 项目简介

`a-share-tailpicker` 是一个用于 A 股沪主板尾盘选股的 Codex skill。它把 C 版尾盘交易方法论做成可重复运行的脚本流程：

- 只筛选默认 C 版沪主板 `60` 系列标的
- 排除科创板、创业板、北交所、ST、极端换手、涨跌停附近和弱流动性标的
- 使用分时形态、量价、资金代理、均线状态、板块共振和交叉验证进行评分
- 输出三层结果：正式可买、建议观察、市场说明
- 支持次日开盘/盘中退出规则回测

这个项目的核心目标不是“每天强行买入”，而是：

1. 让正式买入层尽量维持高胜率。
2. 让每天执行时仍然有观察标的和复盘信息。
3. 明确区分“可买”和“观察”，避免把弱信号混进正式交易。

### 输出结构

每次 `screen` 会输出 JSON 报告。最重要的是三层字段：

| 字段 | 含义 | 是否可买 |
| --- | --- | --- |
| `final_orders` | 严格买入清单，满足当前正式规则 | 可以按仓位和纪律考虑 |
| `watchlist` | 建议观察清单，有尾盘信号但仍有阻碍条件 | 不直接买，只复核 |
| `market_notes` | 当天市场和筛选状态摘要 | 用于理解为什么买/不买 |

实盘流程建议：

- `14:20`：只做预选和观察，不直接下单
- `14:45-14:50`：做最终确认
- 只有 `14:50` 报告里的 `final_orders` 才能作为正式可买清单
- `watchlist` 只用于继续观察或次日复盘

### 安装

把仓库内容复制到本地 Codex skills 目录：

```bash
mkdir -p ~/.agents/skills
rm -rf ~/.agents/skills/a-share-tailpicker
git clone https://github.com/ZeroxZhang/a-share-tailpicker ~/.agents/skills/a-share-tailpicker
```

确认文件存在：

```bash
ls ~/.agents/skills/a-share-tailpicker
```

你应该能看到：

```text
SKILL.md
agents/
references/
scripts/
```

### 依赖

脚本使用 Python 3，并在 live 数据路径中依赖 AKShare 和 pandas。

```bash
python3 -m pip install akshare pandas
```

如果只跑 fixture 测试，不需要真实行情接口。

### 14:20 预选

14:20 只用于找苗子。建议看 `watchlist` 和 `market_notes`，不要直接买。

```bash
mkdir -p reports

python3 scripts/tailpicker.py screen \
  --asof-time 14:20 \
  --limit 0 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_1420.md
```

### 14:50 最终确认

14:50 用于正式确认。只看 `final_orders` 决定是否有可执行标的。

```bash
mkdir -p reports

python3 scripts/tailpicker.py screen \
  --asof-time 14:50 \
  --limit 0 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_1450.md
```

### 指定股票池

只扫描少量代码：

```bash
python3 scripts/tailpicker.py screen \
  --asof-time 14:20 \
  --codes 600999,601688,600958 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_selected.md
```

使用自定义股票池文件：

```bash
python3 scripts/tailpicker.py screen \
  --asof-time 14:20 \
  --universe-file universe.txt \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_universe.md
```

`universe.txt` 每行一个代码：

```text
600999
601688
600958
```

### 回测

回测最近 30 个可验证交易日：

```bash
python3 scripts/tailpicker.py backtest \
  --days 30 \
  --end-date 2026-06-01 \
  --asof-time 14:50 \
  --limit 0 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/backtest_30d_1450.md
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `--days` | 回测交易日数量 |
| `--end-date` | 最后一个已完成交易日，格式 `YYYY-MM-DD` |
| `--asof-time` | 决策时间，例如 `14:20` 或 `14:50` |
| `--limit 0` | 使用默认完整股票池；不限制数量 |
| `--fetch-timeout` | 单次公开数据接口超时时间 |
| `--no-eastmoney-fallback` | 跳过较慢的东方财富分钟线 fallback |
| `--output` | 报告输出路径；`.md` 自动输出 Markdown，`.json` 输出 JSON |
| `--format` | 输出格式，可选 `auto`、`markdown`、`json`；默认 `auto` |

### 离线 fixture

当网络接口不稳定，或者只想验证流程时，可以使用 fixture：

```bash
python3 scripts/tailpicker.py screen \
  --fixture references/fixture_market_week.json \
  --asof-time 14:20 \
  --output reports/fixture_screen.md
```

```bash
python3 scripts/tailpicker.py backtest \
  --fixture references/fixture_market_week.json \
  --asof-time 14:20 \
  --output reports/fixture_backtest.md
```

### 规则摘要

默认规则重点：

- 默认只做沪主板 `60` 系列
- 排除科创板 `688`、创业板 `300/301`、北交所相关代码段
- 排除 ST、极端换手、成交额不足、价格数据缺失、接近涨跌停的标的
- 至少需要两个非技术交叉验证，例如市场、流动性/基本面、板块、情绪/消息
- `14:20` 是预选，不是直接买入
- `14:50` 正式确认时，价格需要至少低于日内高点 0.8%
- B 级且板块共振不足会被剔除
- 弱市下继续过滤 B 级

### 测试

```bash
python3 -m unittest tests/test_tailpicker_skill.py
```

当前测试覆盖：

- skill 元数据
- C 版股票池过滤
- 板块共振计算
- 14:20 预选与正式买入分层
- 14:50 最终确认门槛
- `watchlist` 和 `market_notes` 输出
- fixture screen/backtest
- CLI 参数可用性

### 日常使用建议

建议用自动化或定时任务每天跑两次：

```bash
# 14:20
python3 scripts/tailpicker.py screen --asof-time 14:20 --limit 0 --fetch-timeout 5 --no-eastmoney-fallback --output reports/$(date +%Y%m%d)_1420.md

# 14:50
python3 scripts/tailpicker.py screen --asof-time 14:50 --limit 0 --fetch-timeout 5 --no-eastmoney-fallback --output reports/$(date +%Y%m%d)_1450.md
```

执行纪律：

1. `final_orders` 为空时，不买。
2. `watchlist` 只观察，不买。
3. 14:20 不下单。
4. 14:50 仍没有 `final_orders` 时，当天空仓。
5. 次日严格执行 S1-S7 退出矩阵。

### 风险声明

本项目不会保证收益，也不会保证未来胜率。公开数据源可能延迟、缺失或字段变化，回测也可能受到样本数量、交易成本、滑点和成交可得性的影响。

任何由本项目输出的标的、价格、仓位或退出计划，都只应用于研究和复盘。实际交易风险由使用者自行承担。

### 开源协议

本项目使用 MIT License。详见 [LICENSE](LICENSE)。

---

## English

### Overview

`a-share-tailpicker` is a Codex skill for close-session screening of Shanghai main-board A-share stocks. It packages a C-version tail-session methodology into a repeatable command-line workflow.

It can:

- screen the default Shanghai main-board `60` universe
- exclude STAR Market, ChiNext, Beijing Stock Exchange, ST names, extreme turnover, weak liquidity, and limit-risk names
- score stocks with intraday pattern, volume-price behavior, capital-flow proxy, moving-average state, sector resonance, and cross-validation
- produce three output layers: buyable orders, observation candidates, and market notes
- backtest next-day open/intraday exit behavior

The goal is not to force a trade every day. The goal is to:

1. keep the strict buyable layer selective,
2. still provide daily observation candidates,
3. clearly separate buyable signals from watch-only signals.

### Output Layers

Every `screen` command writes a JSON report with three main layers:

| Field | Meaning | Buyable |
| --- | --- | --- |
| `final_orders` | Strict buyable list that passed the current execution rules | Yes, subject to risk controls |
| `watchlist` | Observation-only candidates with useful tail-session signals but remaining blockers | No |
| `market_notes` | Short explanation of the day's buyable, watch-only, or quiet state | Review only |

Recommended live workflow:

- `14:20`: preselect and observe only
- `14:45-14:50`: run final confirmation
- only `final_orders` from the `14:50` report can be treated as the final buyable list
- `watchlist` is for review and follow-up, not direct execution

### Installation

Copy the repository contents into your local Codex skills folder:

```bash
mkdir -p ~/.agents/skills
rm -rf ~/.agents/skills/a-share-tailpicker
git clone https://github.com/ZeroxZhang/a-share-tailpicker ~/.agents/skills/a-share-tailpicker
```

Verify the install:

```bash
ls ~/.agents/skills/a-share-tailpicker
```

Expected files:

```text
SKILL.md
agents/
references/
scripts/
```

### Dependencies

The script uses Python 3. The live data path depends on AKShare and pandas.

```bash
python3 -m pip install akshare pandas
```

Fixture-based validation does not require live market-data APIs.

### 14:20 Preselection

Use 14:20 for preselection only. Review `watchlist` and `market_notes`; do not treat this as an execution signal.

```bash
mkdir -p reports

python3 scripts/tailpicker.py screen \
  --asof-time 14:20 \
  --limit 0 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_1420.md
```

### 14:50 Final Confirmation

Use 14:50 for final confirmation. Only `final_orders` is the strict buyable layer.

```bash
mkdir -p reports

python3 scripts/tailpicker.py screen \
  --asof-time 14:50 \
  --limit 0 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_1450.md
```

### Custom Universe

Scan selected codes:

```bash
python3 scripts/tailpicker.py screen \
  --asof-time 14:20 \
  --codes 600999,601688,600958 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_selected.md
```

Use a custom universe file:

```bash
python3 scripts/tailpicker.py screen \
  --asof-time 14:20 \
  --universe-file universe.txt \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_universe.md
```

Example `universe.txt`:

```text
600999
601688
600958
```

### Backtesting

Backtest the latest 30 verifiable trading days:

```bash
python3 scripts/tailpicker.py backtest \
  --days 30 \
  --end-date 2026-06-01 \
  --asof-time 14:50 \
  --limit 0 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/backtest_30d_1450.md
```

Key options:

| Option | Meaning |
| --- | --- |
| `--days` | Number of trading days to backtest |
| `--end-date` | Last completed trading day, formatted as `YYYY-MM-DD` |
| `--asof-time` | Decision time, such as `14:20` or `14:50` |
| `--limit 0` | Use the full default universe |
| `--fetch-timeout` | Timeout for each public data fetch |
| `--no-eastmoney-fallback` | Skip slower Eastmoney minute-data fallback |
| `--output` | Report path; `.md` writes Markdown automatically, `.json` writes JSON |
| `--format` | Output format: `auto`, `markdown`, or `json`; default is `auto` |

### Offline Fixture

Use the fixture when public data APIs are unstable or when you only need deterministic validation.

```bash
python3 scripts/tailpicker.py screen \
  --fixture references/fixture_market_week.json \
  --asof-time 14:20 \
  --output reports/fixture_screen.md
```

```bash
python3 scripts/tailpicker.py backtest \
  --fixture references/fixture_market_week.json \
  --asof-time 14:20 \
  --output reports/fixture_backtest.md
```

### Rule Summary

Default rule highlights:

- default universe is Shanghai main-board `60` series only
- excludes STAR Market `688`, ChiNext `300/301`, and Beijing Stock Exchange code ranges
- excludes ST names, extreme turnover, insufficient amount, missing prices, and limit-risk names
- requires at least two non-technical confirmations, such as market environment, liquidity/fundamentals, sector resonance, or sentiment/news
- `14:20` is preselection, not execution
- `14:50` final confirmation requires the price to be at least 0.8% below the intraday high
- B-grade names with weak sector resonance are rejected
- B-grade names are filtered in weak markets

### Tests

```bash
python3 -m unittest tests/test_tailpicker_skill.py
```

The test suite covers:

- skill metadata
- C-version universe filtering
- live sector resonance
- 14:20 preselection versus final buyability
- 14:50 final confirmation gate
- `watchlist` and `market_notes`
- fixture screen/backtest
- CLI options

### Suggested Daily Routine

Run twice per trading day:

```bash
# 14:20
python3 scripts/tailpicker.py screen --asof-time 14:20 --limit 0 --fetch-timeout 5 --no-eastmoney-fallback --output reports/$(date +%Y%m%d)_1420.md

# 14:50
python3 scripts/tailpicker.py screen --asof-time 14:50 --limit 0 --fetch-timeout 5 --no-eastmoney-fallback --output reports/$(date +%Y%m%d)_1450.md
```

Execution discipline:

1. Do not buy when `final_orders` is empty.
2. Treat `watchlist` as observation-only.
3. Do not execute from the 14:20 report.
4. Stay in cash when the 14:50 report has no `final_orders`.
5. Follow the next-day S1-S7 exit matrix.

### Risk Disclaimer

This project does not guarantee returns or future win rates. Public data may be delayed, incomplete, or structurally changed. Backtests may be affected by sample size, trading cost, slippage, and real-world execution constraints.

Any symbols, prices, position sizes, and exit plans produced by this project are for research and review only. Users are solely responsible for their own trading decisions and risk.

### License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
