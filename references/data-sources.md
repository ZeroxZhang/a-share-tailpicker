# 免费数据获取方式

Primary path uses AKShare, already installed locally in this environment. AKShare wraps public sources such as 东方财富、新浪、雪球、财联社. Public endpoints can change or rate-limit, so always keep fixture/backtest fallback and cache outputs.

## Required Data

| Need | Primary free API | Fallback | Notes |
|---|---|---|---|
| A股实时行情 | `ak.stock_sh_a_spot_em()` or `ak.stock_zh_a_spot_em()` | Eastmoney `qt/clist/get`, user watchlist | Gets price, turnover, amount, PE, market cap, 5-min change. |
| 5m/1m historical bars | `ak.stock_zh_a_hist_min_em(symbol, period="5")` | `ak.stock_zh_a_minute` | Use for 14:20/14:45 snapshots and backtest verification. |
| Daily bars | `ak.stock_zh_a_hist(symbol, period="daily")` | Sina daily interfaces | Compute MA, volatility, recent returns, liquidity. |
| Index bars | `ak.stock_zh_index_daily_em("sh000001")` | `ak.index_zh_a_hist` | Market state and halt checks. |
| Trading calendar | `ak.tool_trade_date_hist_sina()` | exchange calendar file | Pick past 1 week trading dates. |
| Industry/sector | `ak.stock_board_industry_name_em()` + `ak.stock_board_industry_cons_em()` | cached mapping | Needed for sector resonance. Cache daily. |
| News | `ak.stock_news_em(symbol)` | 巨潮/东财 search pages | Exclude regulatory,减持,预亏,问询,立案,解禁 risk. |
| Sentiment | `ak.stock_hot_rank_em()`, `stock_hot_up_em()` | Xueqiu hot rankings | Use only as cross-validation, never as sole buy reason. |
| Insider/holder change | `ak.stock_ggcg_em("股东减持")` | exchange announcements | Penalize recent large reduction. |
|龙虎榜复盘| `ak.stock_lhb_detail_daily_sina(date)` | exchange pages | Use after market for retrospective, not same-day 14:20 decisions. |

## Data Integrity Rules

- Never use data published after the simulated decision time. At 14:20, daily close and 14:30-15:00 bars are future data.
- For 14:20 simulation, use 14:00-14:20 as the early tail window and label recommendations as conditional.
- For 14:45-14:50 execution, use 14:30-asof as the true C 版 tail window.
- If Level-2 buy/sell data is missing, estimate capital flow by signed bar amount and lower confidence.
- If fundamentals/news/sector data are missing, do not invent them; record `missing` in `cross_validation` and cap the grade at B.
- Cache raw responses and generated reports so backtests are reproducible.

## Minimal Install

```bash
python3 -m pip install --user --upgrade akshare pandas numpy requests
```

No paid data source is required. Paid Level-2 can improve capital-flow accuracy, but the skill must still run with public data by using proxies and confidence penalties.
