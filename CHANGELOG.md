# Changelog — a-share-tailpicker

This file is the **anti-overfitting ledger**. Every change to strategy
thresholds or scoring weights must be recorded here, with: the params-hash bump,
the reason, the sample it was validated on, and the OOS result. See
`overfitting_guardrail` in the backtest retrospective.

The rules (mirrored in `tailpicker.py::overfitting_guardrail`):

1. Bump `PARAMS_HASH` in `scripts/tailpicker.py` on any threshold/weight change.
2. Add an entry here describing what changed and why (which failure cluster).
3. Validate on **new** samples (not the sample the change was derived from).
4. Report train/OOS via `--walk-forward` before merging.

| params_hash | date | change | reason | train avg | oos avg | validated-on |
|---|---|---|---|---|---|---|
| v3.1-2026-06-16 | 2026-06-16 | 10 threshold changes (tail_gain 0.8-2.5, vr 0.8-3.0, day_pos<75, price_to_high≤-0.8, capital≥60, day 1%-4%, prev -2%~2%); conditional exit replaces S1-S7 | in-sample backtest 50 names / 68 days | +0.27% | not done | same 68-day window (in-sample only — overfit risk) |
| v4.0-2026-06-17 | 2026-06-17 | Architecture rewrite: multi-source DataBackend (iFinD preferred on Kimi Code, AKShare multi-endpoint, Eastmoney HTTP); enrichment cache layer; conservative fill model (limit fills on bar-close confirm); explicit costs (佣金+印花税+沪市过户费+滑点); portfolio caps; standard 量比; pre_close from prev-day daily close; walk-forward; Wilson CI / t-stat / drawdown / attribution. **No threshold values changed — only measurement fixed.** | P0 correctness: old win-rate 76.5% measured under optimistic fills + stubbed live signals + wrong pre_close, so it was not comparable to live. v4 re-measures under realistic assumptions. | pending re-run on ≥200 trades | pending | pending |
| v4.1-2026-06-17 | 2026-06-17 | (a) `daily_history` 加新浪 `stock_zh_a_daily` fallback(东财日线被远端掐断时 prev_close 不再全 None);(b) `daily_prev_close` 统一两源语义(取 date<trade_date 最后一行);(c) 缓存隐患修复:`_call` 仅缓存原始拉取方法,派生方法不缓存;(d) **全大盘替代手挑池**:新增 `all_codes()` 全量源 + `build_full_universe`(沪主板60排除688/ST/银行/农林牧渔),spot 不可用时走名称关键词兜底,默认 `--limit 0`=全大盘(~1600);(e) `--workers` 并发拉取。**未改阈值。** | 真实环境验证:东财 spot/hist 被限流,新浪 minute/daily 可用;手挑100只池有生存者偏差。 | pending | pending | 真实环境 2026-06-17 14:50 全大盘 screen |
