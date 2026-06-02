---
name: a-share-tailpicker
description: Use when screening A股主板尾盘标的, 尾盘选股, 14:20/14:25 close-session candidates, or backtesting and reviewing the C 版 A-share tail-trading methodology.
---

# A股尾盘选股

## Overview

Use this skill to run the C 版 A股主板尾盘选股 methodology as a repeatable Agent workflow: fetch free data, filter non-C-version boards such as 科创板, score candidates with market/fundamental/sentiment/news 交叉验证, output buyable names with reasons, suggested price, position, and next-day actions, then backtest and improve the rules.

This is research automation only. Always preserve the disclaimer that the output is not investment advice.

## Quick Start

Run the bundled script first. The live path uses AKShare free public-data interfaces, especially `stock_zh_a_hist_min_em`, `stock_zh_a_hist`, `stock_news_em`, and related market/sector endpoints.

```bash
python3 ~/.agents/skills/a-share-tailpicker/scripts/tailpicker.py screen --asof-time 14:20 --output reports/tailpick_today.md
python3 ~/.agents/skills/a-share-tailpicker/scripts/tailpicker.py backtest --days 5 --asof-time 14:20 --output reports/tailpick_backtest.md
```

Reports default to Markdown when the output path ends in `.md` or `.markdown`. Use `--format json` or a `.json` path when another program needs structured JSON.

For deterministic validation or when free data APIs are unstable:

```bash
python3 ~/.agents/skills/a-share-tailpicker/scripts/tailpicker.py screen --fixture ~/.agents/skills/a-share-tailpicker/references/fixture_market_week.json --asof-time 14:20 --output reports/fixture_screen.md
python3 ~/.agents/skills/a-share-tailpicker/scripts/tailpicker.py backtest --fixture ~/.agents/skills/a-share-tailpicker/references/fixture_market_week.json --asof-time 14:20 --output reports/fixture_backtest.md
```

## Workflow

1. Load [references/data-sources.md](references/data-sources.md) if data coverage, API fallback, or schedule setup matters.
2. Execute `tailpicker.py screen` at 14:20 or 14:25. Treat 14:20 as a pre-close candidate scan; require a 14:45-14:50 signal recheck before actual order placement.
3. Confirm every final order includes:
   - `code`, `name`, `grade`, `score`
   - `reasons`
   - `suggested_price`
   - `position_pct`
   - `next_day_plan`
   - `cross_validation`
4. Read the three output layers separately:
   - `final_orders`: strict buyable list. Only this layer can be considered for execution.
   - `watchlist`: observation-only candidates. They have useful tail-session signals but still have blockers or need 14:45-14:50 confirmation.
   - `market_notes`: compact explanation for buyable, observation-only, or quiet days.
5. Prefer Markdown reports for human review. Use JSON only for automated parsing or backtest post-processing.
6. If `market_state.state == "halt"` or final orders are empty, report no-buy for execution but still summarize `watchlist` and `market_notes` for daily review.
7. For review or improvement, run `tailpicker.py backtest`, compare generated picks with the next trading day's actual open/intraday exit result, then update thresholds only when the retrospective points to a repeatable failure.

## C 版 Rules

Default universe: C 版沪主板 `60` 系列 only. Exclude 科创板 `688`, 创业板 `300/301`, 北交所 `8/4/43/87/92`, ST/*ST, new stocks under 60 trading days, limit-up/down, weak liquidity, extreme turnover, recent abnormal volatility, and stocks with recent material negative news.

Core flow:

1. Market state and halt check: trend, volatility, breadth, volume, index tail decline, limit-up/down ratio.
2. Seven-layer funnel: hard exclusions, adaptive volume/price filter, intraday pattern, capital-flow proxy, MA trend, sector resonance, final ranking.
3. 交叉验证: never approve using indicators alone. Require at least two non-technical confirmations from market environment, company fundamentals/liquidity, sector resonance, public sentiment, or news/F10 risk. If the result is only technical pattern + proxy capital flow, reject it.
4. Suggested price: limit price equals observed price plus 0.2%-0.5% by grade, rounded to cents; if the scan is at 14:20, label the price as conditional on 14:45-14:50 confirmation.
5. Next-day exit: use the C 版 S1-S7 matrix. Low open without quick recovery exits first; tail strategy does not intentionally hold a second night unless all exception conditions are met.
6. Live 14:20 rule: with public-data live runs, allow only strong-confirmation and non-overheated names into the buyable list. A/B-grade names can be preselected only when capital proxy is strong, sector/liquidity cross-validation is present, tail gain is not overheated, the price is not pinned near the intraday high, and the last bar volume is not excessively concentrated. Other scored or active names go to `watchlist` only until a 14:45-14:50 rerun confirms volume, sector resonance, no negative pattern, and price at least 0.8% below the intraday high before a final buy decision.

## Scheduled Run

Use a local scheduler, Codex automation, cron, launchd, or another orchestrator to invoke:

```bash
python3 ~/.agents/skills/a-share-tailpicker/scripts/tailpicker.py screen --asof-time 14:20 --output ~/tailpicker/reports/$(date +%Y%m%d)_1420.md
```

Recommended schedule:

- 09:00: update announcements/news/F10 risk cache.
- 14:20: pre-close scan and user notification.
- 14:45-14:50: re-run screen for final confirmation.
- 15:30: update adaptive parameter cache.
- Next trading day 09:25/10:00/10:30/14:50: run or apply the exit plan.

## Improvement Loop

After backtesting, modify rules only when failures cluster:

- False positives from isolated individual moves: raise sector resonance or capital-flow threshold.
- Losses in weak markets: lower `bear` position coefficient or allow only S/A grades.
- Good missed candidates: loosen early 14:20 tail-gain threshold but keep 14:45 confirmation.
- News/fundamental failures: increase negative-news penalty and blacklist duration.
- If real backtests show B-grade failures with weak sector confirmation, keep the hard rule: B级 + sector score below 8 is no-buy.
- If 14:20 backtests show A/B-grade candidates do not rise stably next day, require the early-quality gate: capital proxy >=63, tail gain <=1.5%, intraday position <=90%, price at least 0.5% below intraday high, and last-bar volume share <=30%.
- If 14:45-14:50 confirmation produces too many false positives, require the final-quality gate: price at least 0.8% below the intraday high. The 2026-06-01 90-trading-day review showed this kept enough final candidates while bringing the window win rate back to around 80%.

Validate after edits:

```bash
python3 -m unittest /Volumes/Out/codex_projects/尾盘选股系统/tests/test_tailpicker_skill.py
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py ~/.agents/skills/a-share-tailpicker
```
