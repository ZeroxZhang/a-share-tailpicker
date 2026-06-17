"""Backtest statistics: distribution, drawdown, risk ratios, confidence intervals.

A +0.27% average return is dominated by tail risk, so the summary reports the
full distribution plus Wilson CI on the win rate rather than a point estimate.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence


def wilson_ci(wins: int, n: int, z: float = 1.96) -> Optional[Dict[str, float]]:
    """Two-sided Wilson score interval for a binomial proportion."""
    if n <= 0:
        return None
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {
        "point": round(p, 4),
        "lower": round(max(0.0, center - half), 4),
        "upper": round(min(1.0, center + half), 4),
        "n": n,
    }


def summarize(returns_pct: Sequence[float], trades: Optional[List[Dict[str, Any]]] = None,
              daily_returns_pct: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """returns_pct: per-trade net returns in PERCENT (e.g. 0.27 for +0.27%)."""
    rets = [float(r) for r in returns_pct if r is not None]
    n = len(rets)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    def _pct(xs, p):
        if not xs:
            return 0.0
        xs = sorted(xs)
        k = max(0, min(len(xs) - 1, int(round(p * (len(xs) - 1)))))
        return xs[k]

    summary: Dict[str, Any] = {
        "trade_count": n,
        "win_rate": round(len(wins) / n, 4) if n else 0,
        "win_rate_ci": wilson_ci(len(wins), n),
        "average_return_pct": round(statistics.mean(rets), 3) if rets else 0,
        "median_return_pct": round(statistics.median(rets), 3) if rets else 0,
        "stdev_return_pct": round(statistics.pstdev(rets), 3) if len(rets) > 1 else 0,
        "skew_return": round(_skew(rets), 3) if len(rets) > 2 else 0,
        "best_return_pct": round(max(rets), 2) if rets else 0,
        "worst_return_pct": round(min(rets), 2) if rets else 0,
        "p05_return_pct": round(_pct(rets, 0.05), 2),
        "p95_return_pct": round(_pct(rets, 0.95), 2),
        "avg_win_pct": round(statistics.mean(wins), 2) if wins else 0,
        "avg_loss_pct": round(statistics.mean(losses), 2) if losses else 0,
        "payoff_ratio": round(abs(statistics.mean(wins) / statistics.mean(losses)), 3) if wins and losses else 0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) != 0 else 0,
    }

    # Equity curve / drawdown (per-trade, equally weighted – proxy).
    if rets:
        equity = [0.0]
        for r in rets:
            equity.append(equity[-1] + r)
        peak = equity[0]
        max_dd = 0.0
        for v in equity:
            peak = max(peak, v)
            max_dd = min(max_dd, v - peak)
        summary["cumulative_return_pct"] = round(equity[-1], 2)
        summary["max_drawdown_pct"] = round(max_dd, 2)
        # Sharpe-like: mean / stdev * sqrt(trades/year proxy ~252). Use simple
        # per-trade Sharpe annualized assuming ~1 trade / 4 days => ~63/yr.
        if summary["stdev_return_pct"] > 0:
            summary["sharpe_annualized"] = round(
                summary["average_return_pct"] / summary["stdev_return_pct"] * math.sqrt(63), 3)
            summary["calmar"] = round(
                summary["cumulative_return_pct"] / abs(max_dd), 3) if max_dd < 0 else 0
        else:
            summary["sharpe_annualized"] = 0
            summary["calmar"] = 0

    # Worst-N trades for tail-risk disclosure.
    summary["worst_trades"] = [
        {"return_pct": round(r, 2)} for r in sorted(rets)[:5]
    ]

    # t-stat on mean vs 0 (is the edge statistically distinguishable from noise?).
    if n > 1 and summary["stdev_return_pct"] > 0:
        summary["t_stat"] = round(
            summary["average_return_pct"] / (summary["stdev_return_pct"] / math.sqrt(n)), 3)
    else:
        summary["t_stat"] = 0
    return summary


def _skew(xs: List[float]) -> float:
    if len(xs) < 3:
        return 0.0
    mean = statistics.mean(xs)
    sd = statistics.pstdev(xs)
    if sd == 0:
        return 0.0
    return sum((x - mean) ** 3 for x in xs) / len(xs) / (sd ** 3)


def attribution(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Group performance by grade / pattern / sector / market_state to show
    where the edge actually comes from (or doesn't)."""
    def _group(field: str) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, List[float]] = {}
        for t in trades:
            key = str(t.get(field, "unknown"))
            r = t.get("actual_return_pct")
            if r is None:
                continue
            buckets.setdefault(key, []).append(float(r))
        out = {}
        for k, rs in buckets.items():
            wins = [r for r in rs if r > 0]
            out[k] = {
                "n": len(rs),
                "win_rate": round(len(wins) / len(rs), 3) if rs else 0,
                "avg_return_pct": round(statistics.mean(rs), 2) if rs else 0,
            }
        return out

    return {
        "by_grade": _group("grade"),
        "by_pattern": _group("pattern"),
        "by_sector": _group("sector"),
        "by_market_state": _group("market_state"),
    }
