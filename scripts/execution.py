"""Unified execution / cost model for backtests.

Both the fixture and live backtests route through ``simulate_exit`` and
``simulate_entry`` so that the same fill assumptions apply everywhere. The model
is intentionally **conservative**:

- Entry limit order fills only if a bar's *close* is at or below the limit price
  (i.e. the limit was reachable and the bar closed on the buyable side). If no
  bar qualifies, the order is treated as unfilled (no trade).
- Exit limit order (take-profit) fills only if a bar's *close* is at or above
  the take-profit price. A mere intrabar spike that closes back below does not
  fill – this kills the optimistic "touch = fill" bias of the old live path.
- Stop-loss fills at the bar's open if the bar gaps below the stop, else at the
  stop price (gap-through model).
- Costs are explicit: commission (both sides), stamp duty (sell only, A-share),
  Shanghai transfer fee (both sides), and a slippage buffer in basis points.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CostModel:
    commission_bps: float = 2.5e-4   # per side, ~0.025% (万2.5 commission)
    stamp_duty_bps: float = 5.0e-4   # sell only, 0.05%
    transfer_fee_bps: float = 1.0e-5  # Shanghai only, 0.001% per side
    slippage_bps: float = 5.0e-4     # 5bp conservative slippage per side

    def buy_cost(self, price: float) -> float:
        return price * (self.commission_bps + self.transfer_fee_bps + self.slippage_bps)

    def sell_cost(self, price: float) -> float:
        return price * (self.commission_bps + self.stamp_duty_bps + self.transfer_fee_bps + self.slippage_bps)


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def simulate_entry(bars: List[Dict[str, Any]], limit_price: float) -> Tuple[bool, Optional[float], str]:
    """Conservative entry fill.

    Returns (filled, fill_price, reason). Fills at ``limit_price`` if any bar's
    close <= limit_price (limit reachable and bar closed buyable). Otherwise the
    order is unfilled.
    """
    if not bars or limit_price <= 0:
        return False, None, "no_bars_or_invalid_limit"
    for bar in bars:
        close = float(bar.get("收盘", 0) or 0)
        if close > 0 and close <= limit_price:
            return True, limit_price, "limit_filled_on_bar_close"
    return False, None, "limit_not_reached_no_fill"


def simulate_exit(bars: List[Dict[str, Any]], entry: float,
                  open_return: float, cost: CostModel,
                  take_profit_pct: float = 0.005,
                  stop_loss_pct: float = -0.005,
                  time_exit_clock: str = "14:50") -> Dict[str, Any]:
    """Conservative next-day exit.

    Decision tree (mirrors the v3.1 conditional exit, but with realistic fills):

    - If the open itself is >= +take_profit_pct or <= +stop_loss_pct (a gap),
      exit at the open (gap-through).
    - Otherwise walk intraday bars: if a bar closes >= entry*(1+tp), fill
      take-profit at that level. If a bar opens <= entry*(1+sl), stop out at the
      bar open (gap-down within the day). If neither triggers, time-exit at the
      last close at/before ``time_exit_clock``.
    """
    if not bars:
        return _missing(entry, "次日分钟线为空")
    first = bars[0]
    next_open = float(first.get("开盘", 0) or 0)
    if next_open <= 0:
        return _missing(entry, "次日开盘价缺失")

    tp_price = entry * (1 + take_profit_pct)
    sl_price = entry * (1 + stop_loss_pct)
    open_ret = next_open / entry - 1

    # Gap exit at the open.
    if open_ret >= take_profit_pct:
        return _result(entry, next_open, next_open, open_ret, cost,
                       "高开≥+0.5%，开盘获利了结", gap=True)
    if open_ret <= stop_loss_pct:
        return _result(entry, next_open, next_open, open_ret, cost,
                       "低开≤-0.5%，开盘止损(跳空)", gap=True)

    # Intraday walk.
    time_exit_dt = None
    last_close = next_open
    for bar in bars[1:]:
        dt = _parse_dt(str(bar.get("时间", "")))
        bar_open = float(bar.get("开盘", 0) or 0)
        bar_close = float(bar.get("收盘", 0) or 0)
        # Stop-loss: bar opens at/below stop (intraday gap-down).
        if bar_open > 0 and bar_open <= sl_price:
            return _result(entry, next_open, bar_open, bar_open / entry - 1, cost,
                           "盘中跳空止损", gap=True)
        # Take-profit: bar closes at/above target (conservative: need close confirm).
        if bar_close > 0 and bar_close >= tp_price:
            return _result(entry, next_open, tp_price, tp_price / entry - 1, cost,
                           "盘中达到+0.5%止盈(bar收盘确认)", gap=False)
        last_close = bar_close or last_close
        if dt is not None and dt.strftime("%H:%M") >= time_exit_clock:
            time_exit_dt = bar_close or last_close
            break

    exit_price = time_exit_dt or last_close or next_open
    return _result(entry, next_open, exit_price, exit_price / entry - 1, cost,
                   "未达止盈/止损，收盘时间止损", gap=False)


def _result(entry, next_open, exit_price, gross_ret, cost: CostModel, reason, gap) -> Dict[str, Any]:
    net = exit_price / entry - 1 - (cost.buy_cost(entry) + cost.sell_cost(exit_price)) / entry
    return {
        "entry_price": round(entry, 3),
        "next_open": round(next_open, 3),
        "exit_price": round(exit_price, 3),
        "actual_open_return_pct": round((next_open / entry - 1) * 100, 2),
        "actual_return_pct": round(net * 100, 2),
        "gross_return_pct": round(gross_ret * 100, 2),
        "verification": "success" if net > 0 else "failed",
        "exit_reason": reason,
        "gap_exit": gap,
    }


def _missing(entry, reason) -> Dict[str, Any]:
    return {
        "entry_price": round(entry, 3) if entry else None,
        "next_open": None,
        "exit_price": None,
        "actual_open_return_pct": None,
        "actual_return_pct": None,
        "gross_return_pct": None,
        "verification": "missing",
        "exit_reason": reason,
        "gap_exit": None,
    }


# Gap-stop statistics: how often did the open itself blow through the stop,
# and how much did those gaps cost. Critical for a +0.27%-edge strategy.
def gap_stats(verifications: List[Dict[str, Any]]) -> Dict[str, Any]:
    gaps = [v for v in verifications if v.get("gap_exit") and v.get("actual_open_return_pct") is not None]
    opens = [v["actual_open_return_pct"] for v in verifications if v.get("actual_open_return_pct") is not None]
    gap_opens = [v["actual_open_return_pct"] for v in gaps]
    return {
        "gap_exit_count": len(gaps),
        "gap_exit_share": round(len(gaps) / len(verifications), 3) if verifications else 0,
        "gap_exit_avg_return_pct": round(sum(gap_opens) / len(gap_opens), 2) if gap_opens else 0,
        "worst_open_return_pct": round(min(opens), 2) if opens else 0,
        "open_return_p95_pct": round(sorted(opens)[int(0.95 * len(opens))] if opens else 0, 2),
    }
