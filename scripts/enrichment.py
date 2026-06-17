"""Daily enrichment cache for the tailpicker live path.

The live screen must not depend on slow F10/news endpoints during the 14:20
window. This module builds a per-trade-date enrichment bundle once (suggested
09:00) and persists it to JSON. The screen then reads the bundle instead of
stubbing fundamentals/news/market-state to defaults.

Bundle contents::

    {
      "trade_date": "2026-06-17",
      "market": {"index_tail_return_pct": ..., "breadth_up_ratio": ...,
                 "limit_up": ..., "limit_down": ..., "state": ..., "halt_reason": ...},
      "sectors": {"有色金属": {"return_pct": 2.1, "active_count": 18}, ...},
      "stock_map": {<code>: {"name", "sector", "pe", "market_cap_bn",
                             "turnover_rate", "volume_ratio", "is_st", "amount_mn"}},
      "news_sentiment": {<code>: -1|0|1},
      "hot_rank": {<code>: <rank>},
      "built_at": "...",
      "gaps": ["news_sentiment unavailable", ...]
    }

Missing fields are recorded explicitly in ``gaps`` so the strategy layer can
cap confidence (see data-sources.md: missing fundamentals -> grade cap B).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from databackend import DataBackend, get_backend

# Static fallback industry map – used only when the live industry-constituent
# endpoint is unavailable. Copied from tailpicker.py to keep this module
# self-contained.
STATIC_SECTOR_MAP: Dict[str, str] = {
    "600000": "银行", "600015": "银行", "600016": "银行", "600036": "银行",
    "601166": "银行", "601169": "银行", "601288": "银行", "601328": "银行",
    "601398": "银行", "601939": "银行", "601988": "银行", "601998": "银行",
    "600030": "证券", "600061": "证券", "600837": "证券", "600958": "证券",
    "600999": "证券", "601066": "证券", "601211": "证券", "601688": "证券",
    "601788": "证券", "601318": "保险", "601336": "保险", "601601": "保险",
    "601628": "保险", "600028": "石油石化", "601857": "石油石化",
    "600019": "钢铁", "600010": "钢铁", "600111": "有色金属", "600489": "有色金属",
    "600547": "有色金属", "601899": "有色金属", "600519": "白酒",
    "600600": "食品饮料", "600809": "白酒", "600276": "医药", "600196": "医药",
    "603259": "医药", "600309": "化工", "600438": "电力设备", "600406": "电网设备",
    "600089": "电力设备", "601012": "光伏设备", "600900": "电力", "600905": "电力",
    "600011": "电力", "600025": "电力", "600795": "电力", "600050": "通信",
    "600941": "通信", "600745": "计算机", "600570": "计算机", "600584": "半导体",
    "600703": "半导体", "603501": "半导体", "603986": "半导体", "600760": "国防军工",
    "600893": "国防军工", "600150": "国防军工", "601989": "国防军工", "600031": "工程机械",
    "600104": "汽车", "600660": "汽车", "601888": "旅游零售", "600690": "家电",
    "603288": "食品饮料", "601006": "铁路公路", "601111": "航空机场", "600009": "航空机场",
    "600029": "航空机场", "600115": "航空机场", "600018": "港口航运", "601919": "港口航运",
    "601668": "建筑工程", "601800": "建筑工程", "601186": "建筑工程", "601225": "煤炭",
    "601088": "煤炭", "600989": "煤炭", "600048": "房地产", "600741": "商贸零售",
    "600845": "计算机", "600886": "环保",
}

# Industry name normalization – Eastmoney board names often carry suffixes.
def _normalize_industry(name: str) -> str:
    for suffix in ("行业", "指数", "(申万)", "-BK"):
        name = name.replace(suffix, "")
    return name.strip()


def build_enrichment(trade_date: str, backend: Optional[DataBackend] = None,
                     asof_time: str = "14:50", codes: Optional[List[str]] = None) -> Dict[str, Any]:
    backend = backend or get_backend()
    gaps: List[str] = []
    bundle: Dict[str, Any] = {"trade_date": trade_date, "built_at": datetime.now().isoformat()}

    # --- market state ----------------------------------------------------
    index_ret = backend.index_tail_return(trade_date, asof_time)
    limit_counts = backend.limit_counts(trade_date)
    snap = backend.spot_snapshot(trade_date)
    breadth = None
    if snap:
        ups = sum(1 for s in snap.values() if (s.get("pct_change") or 0) > 0)
        breadth = ups / max(len(snap), 1)
    limit_up = limit_counts[0] if limit_counts else None
    limit_down = limit_counts[1] if limit_counts else None

    state, halt_reason = _classify_market(index_ret, breadth, limit_up, limit_down)
    bundle["market"] = {
        "index_tail_return_pct": index_ret,
        "breadth_up_ratio": breadth,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "state": state,
        "halt_reason": halt_reason,
    }
    if index_ret is None and breadth is None:
        gaps.append("market_state_unavailable_defaulting_to_range")
        bundle["market"]["state"] = "range"

    # --- sectors (full-market industry returns) --------------------------
    ind_returns = backend.industry_returns(trade_date)
    ind_cons = backend.industry_constituents()
    sectors: Dict[str, Any] = {}
    if ind_returns:
        for name, ret in ind_returns.items():
            sectors[_normalize_industry(name)] = {"return_pct": float(ret or 0)}
    if ind_cons:
        # active count per industry from spot
        if snap:
            for name, members in ind_cons.items():
                nm = _normalize_industry(name)
                active = sum(1 for c in members if c in snap and (snap[c].get("pct_change") or 0) > 1.0)
                sectors.setdefault(nm, {})["active_count"] = active
                sectors.setdefault(nm, {})["members"] = members
    if not sectors:
        gaps.append("sector_industry_unavailable_using_static_map")
    bundle["sectors"] = sectors

    # --- per-stock map (name / sector / pe / mktcap / st / volume_ratio) -
    stock_map: Dict[str, Dict[str, Any]] = {}
    target_codes = codes or list(snap.keys()) if snap else (codes or [])
    code_to_industry = _build_code_industry_map(ind_cons) if ind_cons else {}
    if snap:
        for code, s in snap.items():
            if not code.startswith("60") or code.startswith("688"):
                continue
            sector = code_to_industry.get(code) or STATIC_SECTOR_MAP.get(code, "未知")
            stock_map[code] = {
                "name": s.get("name", code),
                "sector": sector,
                "pe": s.get("pe"),
                "market_cap_bn": s.get("market_cap_bn"),
                "turnover_rate": s.get("turnover_rate", 0),
                "volume_ratio": s.get("volume_ratio"),
                "amount_mn": s.get("amount_mn", 0),
                "is_st": bool(s.get("is_st")),
            }
    else:
        gaps.append("spot_snapshot_unavailable_fundamentals_capped")
    bundle["stock_map"] = stock_map

    # --- news sentiment + hot rank (best-effort, may be partial) ---------
    news: Dict[str, int] = {}
    hot: Dict[str, int] = backend.hot_rank() or {}
    if hot:
        bundle["hot_rank"] = hot
    else:
        gaps.append("hot_rank_unavailable")
    # News is expensive (per-code call). Only enrich the candidate universe
    # passed in ``codes``; the screen enriches scored candidates lazily.
    if codes:
        for code in codes:
            ns = backend.news_sentiment(code)
            if ns is not None:
                news[code] = ns
        if not news:
            gaps.append("news_sentiment_unavailable")
    bundle["news_sentiment"] = news
    bundle["gaps"] = gaps
    return bundle


def _classify_market(index_ret, breadth, limit_up, limit_down) -> tuple:
    if index_ret is not None and index_ret <= -1.5:
        return "halt", "上证尾盘跌幅超过1.5%"
    if limit_up is not None and limit_down is not None and limit_down > limit_up * 2:
        return "halt", "跌停家数超过涨停家数2倍"
    if breadth is not None:
        if breadth > 0.60 and (index_ret or 0) > 0.2:
            return "bull", None
        if breadth < 0.40 and (index_ret or 0) < -0.2:
            return "bear", None
    return "range", None


def _build_code_industry_map(ind_cons: Dict[str, List[str]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for name, members in ind_cons.items():
        nm = _normalize_industry(name)
        for code in members:
            mapping.setdefault(code, nm)
    return mapping


def enrichment_path(cache_dir: Path, trade_date: str) -> Path:
    return Path(cache_dir) / f"enrichment_{trade_date}.json"


def load_enrichment(cache_dir: Path, trade_date: str) -> Optional[Dict[str, Any]]:
    path = enrichment_path(cache_dir, trade_date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_enrichment(cache_dir: Path, bundle: Dict[str, Any]) -> Path:
    path = enrichment_path(cache_dir, bundle["trade_date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, default=str), encoding="utf-8")
    return path
