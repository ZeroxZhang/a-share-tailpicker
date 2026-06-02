# A-Share Tailpicker Skill

Codex skill for C-version A-share Shanghai main-board close-session screening, watchlist generation, and next-day backtesting.

This repository contains the skill under `skill/` plus a small test suite under `tests/`.

## Install

Copy the skill directory into your local Codex skills folder:

```bash
mkdir -p ~/.agents/skills
cp -R skill ~/.agents/skills/a-share-tailpicker
```

## Screen

Run a 14:20 pre-close scan:

```bash
python3 skill/scripts/tailpicker.py screen \
  --asof-time 14:20 \
  --limit 0 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_1420.json
```

Run a 14:50 final confirmation scan:

```bash
python3 skill/scripts/tailpicker.py screen \
  --asof-time 14:50 \
  --limit 0 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/tailpick_1450.json
```

Read outputs as three layers:

- `final_orders`: strict buyable list.
- `watchlist`: observation-only candidates.
- `market_notes`: compact explanation for buyable, observation-only, or quiet days.

## Backtest

```bash
python3 skill/scripts/tailpicker.py backtest \
  --days 30 \
  --end-date 2026-06-01 \
  --asof-time 14:50 \
  --limit 0 \
  --fetch-timeout 5 \
  --no-eastmoney-fallback \
  --output reports/backtest_30d_1450.json
```

## Test

```bash
python3 -m unittest tests/test_tailpicker_skill.py
```

## Disclaimer

This project is for strategy research and review automation only. It is not investment advice.
