"""Portfolio-level risk constraints.

``select_final_orders`` historically took the top-5 scored names with no
portfolio awareness. Because sector resonance is itself a buy signal, the top-5
can easily be 5 names from the same industry – one correlated bet. This module
caps total exposure, per-sector exposure, and per-name exposure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class PortfolioCaps:
    max_total_position_pct: float = 40.0   # max aggregate cash deployment
    max_per_name_pct: float = 12.0         # single-name cap
    max_per_sector_pct: float = 24.0       # single-industry cap
    max_names: int = 5
    enforce_sector_cap: bool = True

    @staticmethod
    def for_state(state: str) -> "PortfolioCaps":
        if state == "bull":
            return PortfolioCaps(max_total_position_pct=50.0, max_per_name_pct=12.0, max_per_sector_pct=24.0)
        if state == "bear":
            return PortfolioCaps(max_total_position_pct=20.0, max_per_name_pct=8.0, max_per_sector_pct=12.0, max_names=3)
        if state == "halt":
            return PortfolioCaps(max_total_position_pct=0.0, max_names=0)
        return PortfolioCaps()  # range


def apply_portfolio_constraints(ranked_orders: List[Dict[str, Any]],
                                state: str,
                                caps: PortfolioCaps = None) -> List[Dict[str, Any]]:
    """Walk the ranked list, admitting names until a cap binds.

    Each order carries its own ``position_pct`` suggestion (from grade * state
    coefficient). We clamp per-name to ``max_per_name_pct`` and stop admitting
    once total or sector caps are hit.
    """
    caps = caps or PortfolioCaps.for_state(state)
    if caps.max_total_position_pct <= 0 or caps.max_names <= 0:
        return []

    admitted: List[Dict[str, Any]] = []
    total = 0.0
    sector_total: Dict[str, float] = {}

    for order in ranked_orders:
        if len(admitted) >= caps.max_names:
            break
        sector = str(order.get("sector", "未知"))
        # Resolve the per-name position the strategy suggested.
        suggested = float(order.get("position_pct", 0) or 0)
        pos = min(suggested, caps.max_per_name_pct)
        if pos <= 0:
            continue
        if total + pos > caps.max_total_position_pct:
            pos = caps.max_total_position_pct - total
            if pos <= 0:
                break
        if caps.enforce_sector_cap:
            if sector_total.get(sector, 0) + pos > caps.max_per_sector_pct:
                # Try to fit a reduced slice; otherwise skip this name.
                room = caps.max_per_sector_pct - sector_total.get(sector, 0)
                if room <= 0.5:
                    continue
                pos = min(pos, room)
        admitted_order = dict(order)
        admitted_order["position_pct"] = round(pos, 2)
        admitted_order["constraints_applied"] = {
            "total_after": round(total + pos, 2),
            "sector_after": round(sector_total.get(sector, 0) + pos, 2),
            "caps": {
                "max_total": caps.max_total_position_pct,
                "max_per_name": caps.max_per_name_pct,
                "max_per_sector": caps.max_per_sector_pct,
            },
        }
        admitted.append(admitted_order)
        total += pos
        sector_total[sector] = sector_total.get(sector, 0) + pos
    return admitted
