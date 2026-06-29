#!/usr/bin/env python3
"""C-version A-share close-session screener and backtester.

Refactored architecture (v4):

- ``databackend`` – multi-source data access (iFinD preferred on Kimi Code,
  AKShare multi-endpoint fallback, direct Eastmoney HTTP), all disk-cached.
- ``enrichment`` – 09:00 daily cache bundle (market state, full-market sector
  resonance, fundamentals, news sentiment, hot rank) consumed by the live path.
- ``execution`` – unified conservative fill/cost model shared by fixture & live
  backtests (limit fills on bar-close confirmation, explicit costs + slippage).
- ``stats`` – distribution / drawdown / Sharpe / Wilson CI / t-stat / attribution.
- ``risk`` – portfolio caps (total / per-name / per-sector) replacing bare top-5.

The fixture path stays deterministic and dependency-free for unit tests.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Make sibling modules importable whether run as a script or loaded via
# importlib (the test harness uses spec_from_file_location).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from databackend import get_backend, DataBackend  # noqa: E402
from enrichment import (  # noqa: E402
    build_enrichment, load_enrichment, save_enrichment, enrichment_path,
)
from execution import CostModel, simulate_entry, simulate_exit, gap_stats  # noqa: E402
from stats import summarize, attribution  # noqa: E402
from risk import PortfolioCaps, apply_portfolio_constraints  # noqa: E402


DEFAULT_UNIVERSE = [
    "600000", "600009", "600010", "600011", "600015", "600016", "600018",
    "600019", "600025", "600028", "600029", "600030", "600031", "600036",
    "600048", "600050", "600061", "600085", "600089", "600104", "600111",
    "600115", "600150", "600183", "600196", "600276", "600309", "600346",
    "600406", "600415", "600438", "600489", "600519", "600522", "600547",
    "600570", "600584", "600600", "600660", "600674", "600690", "600703",
    "600741", "600745", "600760", "600795", "600809", "600837", "600845",
    "600875", "600886", "600893", "600900", "600905", "600919", "600926",
    "600941", "600958", "600989", "600999", "601006", "601012", "601066",
    "601088", "601111", "601138", "601166", "601169", "601186", "601211",
    "601225", "601288", "601318", "601328", "601336", "601398", "601601",
    "601628", "601668", "601688", "601728", "601766", "601788", "601800",
    "601816", "601857", "601888", "601899", "601919", "601939", "601988",
    "601989", "601995", "601998", "603259", "603288", "603501", "603799",
    "603986", "605499",
]

STATIC_SECTOR_MAP = {
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

NEGATIVE_NEWS_KEYWORDS = [
    "减持", "预亏", "预减", "问询函", "监管", "立案", "处罚", "解禁",
    "退市", "风险提示", "业绩下滑", "诉讼", "冻结",
]

STATE_COEF = {"bull": 1.0, "range": 0.7, "bear": 0.3, "halt": 0.0}
_DEFAULT_SKILL_ROOT: Optional[Path] = None

def _find_skill_root() -> Path:
    """Detect the actual skill root directory regardless of whether it lives
    under ~/.claude/skills/ or ~/.agents/skills/.
    """
    global _DEFAULT_SKILL_ROOT
    if _DEFAULT_SKILL_ROOT is not None:
        return _DEFAULT_SKILL_ROOT
    candidates = [
        Path.home() / ".agents" / "skills" / "a-share-tailpicker",
        Path.home() / ".claude" / "skills" / "a-share-tailpicker",
    ]
    for p in candidates:
        if p.is_dir() and (p / "scripts" / "tailpicker.py").exists():
            _DEFAULT_SKILL_ROOT = p
            return p
    # Fallback: derive from __file__ if this module is inside the skill tree
    try:
        this_dir = Path(__file__).resolve().parent.parent
        if (this_dir / "scripts" / "tailpicker.py").exists():
            _DEFAULT_SKILL_ROOT = this_dir
            return this_dir
    except NameError:
        pass
    # Ultimate fallback: prefer ~/.agents if it exists, else ~/.claude
    fallback = Path.home() / ".agents" / "skills" / "a-share-tailpicker"
    fallback.mkdir(parents=True, exist_ok=True)
    _DEFAULT_SKILL_ROOT = fallback
    return fallback


DEFAULT_CACHE_DIR = _find_skill_root() / "cache"
PARAMS_HASH = "v4.0-2026-06-17"  # bump when thresholds change; recorded for OOS guardrail

# C 版尾盘不做的板块。银行:低波、政策/指数驱动,尾盘形态失效且易被权重操纵;
# 农林牧渔:投机与季节性强、消息驱动、易被资金短线操控。ST 风险由 hard_filter 处理。
# 板块名优先用富化行业数据;行业数据缺失时用名称关键词兜底。
EXCLUDED_SECTORS = {"银行", "农林牧渔"}
EXCLUDED_NAME_KEYWORDS = ["银行", "农商", "农业", "牧业", "渔业", "种业", "林业", "生猪", "养殖"]


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------

@dataclass
class StockDecision:
    code: str
    name: str
    sector: str
    price: float
    pre_close: float
    score: float
    grade: str
    pattern: str
    suggested_price: float
    position_pct: float
    reasons: List[str]
    warnings: List[str]
    cross_validation: Dict[str, Any]
    next_day_plan: Dict[str, str]
    market_state: str = "range"
    source: str = "fixture"
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_order(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "sector": self.sector,
            "grade": self.grade,
            "score": round(self.score, 1),
            "pattern": self.pattern,
            "current_price": round(self.price, 3),
            "suggested_price": self.suggested_price,
            "position_pct": round(self.position_pct, 2),
            "reasons": self.reasons,
            "warnings": self.warnings,
            "cross_validation": self.cross_validation,
            "next_day_plan": self.next_day_plan,
            "market_state": self.market_state,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def is_c_version_universe(code: str) -> bool:
    """C-version default universe: Shanghai main board 60-series only."""
    code = normalize_code(code)
    if code.startswith(("688", "300", "301", "8", "4", "43", "87", "92")):
        return False
    return code.startswith("60")


def normalize_code(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6)


def round_up_cent(value: float) -> float:
    import math
    return math.ceil(value * 100 - 1e-9) / 100


def grade_from_score(score: float) -> str:
    if score >= 80:
        return "S"
    if score >= 65:
        return "A"
    if score >= 50:
        return "B"
    if score >= 35:
        return "C"
    return "D"


def time_to_minutes(value: str) -> int:
    hour, minute = value.split(":")[:2]
    return int(hour) * 60 + int(minute)


def trading_minutes_elapsed(asof_time: str) -> int:
    """A-share elapsed trading minutes from 09:30 to asof (lunch excluded)."""
    asof = time_to_minutes(asof_time)
    open_m = 9 * 60 + 30
    lunch_start = 11 * 60 + 30
    lunch_end = 13 * 60
    close = 15 * 60
    asof = min(asof, close)
    if asof <= lunch_start:
        return max(0, asof - open_m)
    return (lunch_start - open_m) + max(0, asof - lunch_end)


def passes_early_live_quality(stock: Dict[str, Any]) -> bool:
    tail_gain = float(stock.get("tail_gain_pct", 0))
    volume_ratio = float(stock.get("volume_ratio", 0))
    return (
        float(stock.get("capital_flow_score", 0)) >= 60
        and 0.8 <= tail_gain <= 2.5
        and float(stock.get("day_position_pct", 100)) < 75
        and float(stock.get("price_to_day_high_pct", 0)) <= -0.80
        and 0.8 <= volume_ratio <= 3.0
        and float(stock.get("last_bar_vol_share_tail_pct", 100)) <= 30
    )


def passes_final_live_quality(stock: Dict[str, Any]) -> bool:
    tail_gain = float(stock.get("tail_gain_pct", 0))
    volume_ratio = float(stock.get("volume_ratio", 0))
    return (
        float(stock.get("price_to_day_high_pct", 0)) <= -0.80
        and 0.8 <= tail_gain <= 2.5
        and float(stock.get("capital_flow_score", 0)) >= 60
        and 0.8 <= volume_ratio <= 3.0
    )


def next_day_plan() -> Dict[str, str]:
    """v3.1 conditional exit (conservative fill model lives in execution.py)."""
    return {
        "open_ge_0.5pct": "开盘≥+0.5%，开盘获利了结(跳空按开盘成交)",
        "open_le_minus0.5pct": "开盘≤-0.5%，开盘止损(跳空按开盘成交)",
        "intraday_tp": "盘中bar收盘≥+0.5%止盈(收盘确认才成交)",
        "time_exit": "未触发则14:50前时间止损",
        "force_exit": "除涨停封单稳定外，次日14:50前强制平仓",
    }


def load_fixture(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def pick_fixture_day(fixture: Dict[str, Any], trade_date: Optional[str]) -> Dict[str, Any]:
    days = fixture["days"]
    if trade_date:
        for day_obj in days:
            if day_obj["trade_date"] == trade_date:
                return day_obj
        raise SystemExit(f"fixture has no trade date: {trade_date}")
    return days[-1]


def market_report(day_obj: Dict[str, Any]) -> Dict[str, Any]:
    market = dict(day_obj.get("market", {}))
    state = market.get("state", "range")
    reason = None
    if market.get("index_tail_return_pct", 0) <= -1.5:
        state, reason = "halt", "上证尾盘跌幅超过1.5%"
    if market.get("limit_down", 0) > market.get("limit_up", 0) * 2:
        state, reason = "halt", "跌停家数超过涨停家数2倍"
    if state == "range" and market.get("breadth_up_ratio") is not None:
        bu = float(market["breadth_up_ratio"])
        avg_ret = market.get("avg_return")
        if avg_ret is not None and float(avg_ret) > 0.005 and bu > 0.60:
            state = "bull"
        elif avg_ret is not None and float(avg_ret) < -0.005 and bu < 0.40:
            state = "bear"
    market["state"] = state
    market["halt_reason"] = reason
    return market


def hard_filter(stock: Dict[str, Any]) -> Optional[str]:
    code = normalize_code(stock.get("code", ""))
    name = str(stock.get("name", ""))
    if not is_c_version_universe(code):
        return "非C版默认沪主板60系列或属于科创/创业/北交所"
    if "ST" in name.upper():
        return "ST/*ST风险"
    if float(stock.get("amount_mn", 0)) < 300:
        return "14:20累计成交额不足"
    if float(stock.get("turnover_rate", 0)) > 25:
        return "换手率极端，疑似对倒"
    if float(stock.get("price", 0)) <= 0 or float(stock.get("pre_close", 0)) <= 0:
        return "价格数据缺失"
    day_ret = float(stock["price"]) / float(stock["pre_close"]) - 1
    if not (0.01 <= day_ret < 0.04):
        return "当日涨幅不在1%-4%范围内"
    prev_ret = stock.get("prev_day_return")
    if prev_ret is not None:
        prev_ret = float(prev_ret)
        if not (-0.02 <= prev_ret <= 0.02):
            return "前日涨幅不在-2%-2%范围内"
    volume_ratio = float(stock.get("volume_ratio", 0))
    if not (0.8 <= volume_ratio <= 3.0):
        return "量比不在0.8-3.0范围内"
    if float(stock.get("market_cap_bn", 9999)) < 30:
        return "流通市值过小"
    return None


def score_stock(stock: Dict[str, Any], market: Dict[str, Any], asof_time: str, source: str) -> Optional[StockDecision]:
    reject = hard_filter(stock)
    if reject:
        return None

    warnings: List[str] = []
    reasons: List[str] = []
    pattern = str(stock.get("pattern", "none"))
    tail_gain = float(stock.get("tail_gain_pct", 0))
    volume_ratio = float(stock.get("volume_ratio", 0))
    tail_vol_ratio = float(stock.get("tail_vol_ratio", 0))
    capital_flow = float(stock.get("capital_flow_score", 0))
    sector_score = float(stock.get("sector_score", 0))
    news_sentiment = float(stock.get("news_sentiment", 0))
    ma_state = str(stock.get("ma_state", "range"))
    pe = stock.get("pe")
    hot_rank = stock.get("hot_rank")

    if tail_gain < 0.8 or tail_gain > 2.5:
        return None
    if not (0.8 <= volume_ratio <= 3.0):
        return None
    if not (0.10 <= tail_vol_ratio <= 0.45):
        return None
    if pattern in {"negative", "none", ""}:
        return None
    price_to_day_high = float(stock.get("price_to_day_high_pct", 0))
    if price_to_day_high > -0.8:
        return None
    day_position = float(stock.get("day_position_pct", 100))
    if day_position >= 75:
        return None
    if capital_flow < 60:
        return None
    prev_ret = stock.get("prev_day_return")
    if prev_ret is not None:
        prev_ret = float(prev_ret)
        if not (-0.02 <= prev_ret <= 0.02):
            return None
    state = market.get("state", "range")
    if state == "halt":
        return None

    pattern_score = {
        "breakout": 24, "pullback": 20, "strong_accel": 21,
        "v_reversal": 17, "auction_grab": 14,
    }.get(pattern, 10)

    if ma_state == "bull":
        ma_score = 15
    elif ma_state == "range":
        ma_score = 9
    else:
        ma_score = 2

    score = 0.0
    score += min(25, pattern_score)
    score += min(20, capital_flow * 0.20)
    score += min(15, ma_score)
    score += min(20, sector_score)
    score += min(8, max(0, tail_gain) * 2.4)
    score += min(6, max(0, volume_ratio - 1) * 3)
    score += min(4, max(0, news_sentiment) * 4)
    if hot_rank is not None and float(hot_rank) <= 100:
        score += 3
    prev_ret = stock.get("prev_day_return")
    if prev_ret is not None:
        prev_ret = float(prev_ret)
        if -0.02 <= prev_ret <= 0.02:
            score += 3
            reasons.append(f"前日温和波动{prev_ret:.1%}，momentum平稳")
    sm = stock.get("sector_momentum")
    if sm is not None:
        sm = float(sm)
        if sm > 0.02:
            score += min(10, sm * 500)
            reasons.append(f"板块动量{sm:.2%}，强势共振")
        elif sm < -0.01:
            score -= 5
            warnings.append("板块动量为负，弱势共振")

    if state == "bear":
        score -= 12
        warnings.append("弱市状态，仓位系数下调")
    elif state == "bull":
        score += 3

    if news_sentiment < 0:
        score -= 18
        warnings.append("新闻/F10交叉验证出现负面信号")
    if pe is not None:
        try:
            pe_float = float(pe)
            if pe_float <= 0 or pe_float > 80:
                score -= 6
                warnings.append("估值或盈利质量不理想")
        except (TypeError, ValueError):
            warnings.append("PE数据缺失")
    else:
        warnings.append("PE数据缺失")

    cross_checks = []
    if market.get("breadth_up_ratio") is not None or market.get("index_tail_return_pct") is not None:
        cross_checks.append("market")
    if float(stock.get("amount_mn", 0)) >= 500 and float(stock.get("market_cap_bn", 0) or 0) >= 30:
        cross_checks.append("fundamental_liquidity")
    if sector_score >= 8:
        cross_checks.append("sector")
    if news_sentiment > 0 or (hot_rank is not None and float(hot_rank) <= 100):
        cross_checks.append("sentiment_news")
    sm = stock.get("sector_momentum")
    if sm is not None and float(sm) > 0.01:
        cross_checks.append("sector_momentum")
    prev_ret = stock.get("prev_day_return")
    if prev_ret is not None and -0.02 <= float(prev_ret) <= 0.02:
        cross_checks.append("prev_momentum")

    if len(cross_checks) < 2:
        return None

    grade = grade_from_score(score)
    # v4: when enrichment confirms fundamentals are genuinely unavailable, cap
    # confidence at B (per data-sources.md). Only triggers when the screen sets
    # fundamentals_status="missing"; direct unit-test calls leave it unset.
    if stock.get("fundamentals_status") == "missing" and grade in {"S", "A"}:
        grade = "B"
        warnings.append("基本面/新闻富化缺失，等级封顶B")
    if grade not in {"S", "A", "B"}:
        return None
    if state == "bear" and grade == "B":
        return None
    if grade == "B" and sector_score < 8:
        return None

    price = float(stock["price"])
    premium = {"S": 0.005, "A": 0.003, "B": 0.002}[grade]
    suggested_price = round_up_cent(price * (1 + premium))
    base_pos = {"S": 12.0, "A": 10.0, "B": 5.0}[grade]
    position_pct = base_pos * STATE_COEF.get(state, 0.7)

    reasons.append(f"{asof_time}早尾盘涨幅{tail_gain:.2f}%，量比{volume_ratio:.2f}")
    reasons.append(f"命中C版分时形态：{pattern}")
    reasons.append(f"资金流/主动买入代理分{capital_flow:.0f}，板块共振分{sector_score:.0f}")
    if ma_state == "bull":
        reasons.append("均线状态偏多，趋势过滤通过")
    if market.get("breadth_up_ratio") is not None:
        reasons.append(f"市场赚钱效应{float(market['breadth_up_ratio']):.0%}，状态{state}")

    decision_note = "14:20为预选时点，必须在14:45-14:50复核尾盘放量和负向形态后再执行。"
    if source == "akshare" and time_to_minutes(asof_time) >= time_to_minutes("14:45"):
        decision_note = "14:45-14:50为最终确认时点，正式买入需确认价格至少低于日内高点0.8%。"

    cross_validation = {
        "market_environment": {
            "state": state,
            "breadth_up_ratio": market.get("breadth_up_ratio"),
            "index_tail_return_pct": market.get("index_tail_return_pct"),
        },
        "fundamentals": {
            "pe": pe,
            "market_cap_bn": stock.get("market_cap_bn"),
            "liquidity_amount_mn": stock.get("amount_mn"),
            "fundamentals_status": stock.get("fundamentals_status", "available"),
        },
        "sentiment": {"hot_rank": hot_rank, "news_sentiment": news_sentiment},
        "sector": {"sector": stock.get("sector", "未知"), "sector_score": sector_score},
        "intraday_heat": {
            "day_position_pct": stock.get("day_position_pct"),
            "price_to_day_high_pct": stock.get("price_to_day_high_pct"),
            "last_bar_vol_share_tail_pct": stock.get("last_bar_vol_share_tail_pct"),
            "volume_ratio_source": stock.get("volume_ratio_source", "provided"),
        },
        "non_technical_confirmations": cross_checks,
        "decision_note": decision_note,
    }

    return StockDecision(
        code=normalize_code(stock["code"]),
        name=str(stock.get("name", "")),
        sector=str(stock.get("sector", "未知")),
        price=price,
        pre_close=float(stock["pre_close"]),
        score=score,
        grade=grade,
        pattern=pattern,
        suggested_price=suggested_price,
        position_pct=round(position_pct, 2),
        reasons=reasons,
        warnings=warnings,
        cross_validation=cross_validation,
        next_day_plan=next_day_plan(),
        market_state=state,
        source=source,
        raw=stock,
    )


def select_final_orders(decisions: List[StockDecision], asof_time: str, source: str,
                        state: str = "range", apply_caps: bool = True) -> List[Dict[str, Any]]:
    ranked = sorted(decisions, key=lambda item: item.score, reverse=True)[:10]
    if source == "akshare":
        asof_minutes = time_to_minutes(asof_time)
        if asof_minutes <= time_to_minutes("14:20"):
            ranked = [d for d in ranked if passes_early_live_quality(d.raw)]
        elif asof_minutes >= time_to_minutes("14:45"):
            ranked = [d for d in ranked if passes_final_live_quality(d.raw)]
    orders = [d.to_order() for d in ranked]
    if apply_caps:
        orders = apply_portfolio_constraints(orders, state)
    else:
        orders = orders[:5]
    return orders


def watch_blockers(stock: Dict[str, Any], asof_time: str, source: str, rank: Optional[int], scored: bool) -> List[str]:
    blockers: List[str] = []
    asof_minutes = time_to_minutes(asof_time)
    if source == "akshare" and asof_minutes <= time_to_minutes("14:20"):
        if float(stock.get("capital_flow_score", 0)) < 60:
            blockers.append("资金代理未达正式买入门槛60")
        tail_gain = float(stock.get("tail_gain_pct", 0))
        if not (0.8 <= tail_gain <= 2.5):
            blockers.append("尾盘涨幅不在0.8%-2.5%范围内")
        if float(stock.get("day_position_pct", 100)) >= 75:
            blockers.append("日内位置≥75%")
        if float(stock.get("price_to_day_high_pct", 0)) > -0.80:
            blockers.append("价格距离日内高点不足0.8%")
        volume_ratio = float(stock.get("volume_ratio", 0))
        if not (0.8 <= volume_ratio <= 3.0):
            blockers.append("量比不在0.8-3.0范围内")
        if float(stock.get("last_bar_vol_share_tail_pct", 100)) > 30:
            blockers.append("最后一根K线量能占比偏高")
    elif source == "akshare" and asof_minutes >= time_to_minutes("14:45"):
        if float(stock.get("price_to_day_high_pct", 0)) > -0.80:
            blockers.append("距离日内高点不足0.8%，不进入正式买入")
        tail_gain = float(stock.get("tail_gain_pct", 0))
        if not (0.8 <= tail_gain <= 2.5):
            blockers.append("尾盘涨幅不在0.8%-2.5%范围内")
        if float(stock.get("capital_flow_score", 0)) < 60:
            blockers.append("资金代理分<60")

    if rank is not None and rank > 5:
        blockers.append("评分排名未进入正式买入前5")
    if not scored:
        blockers.append("未达到C版正式评分/交叉验证，观察不买")
    return blockers or ["未进入正式买入清单，仅观察"]


def watch_upgrade_triggers(asof_time: str, source: str) -> List[str]:
    if source == "akshare" and time_to_minutes(asof_time) <= time_to_minutes("14:20"):
        return [
            "14:45-14:50复核仍保持分时形态和板块共振",
            "资金代理分>=60且尾盘涨幅0.8%-2.5%",
            "价格距离日内高点至少0.8%后才考虑正式买入",
            "日内位置<75%且量比0.8-3.0",
        ]
    if source == "akshare" and time_to_minutes(asof_time) >= time_to_minutes("14:45"):
        return [
            "仅当进入评分前5且满足所有v3.1硬条件、组合风控caps通过时升级为final_orders",
            "未升级则不追价，次日重新评估",
        ]
    return ["满足正式评分、交叉验证、最终排序和组合风控后才升级为final_orders"]


def watch_item_from_decision(decision: StockDecision, asof_time: str, source: str, rank: int) -> Dict[str, Any]:
    return {
        "code": decision.code, "name": decision.name, "sector": decision.sector,
        "status": "observe", "grade": decision.grade, "score": round(decision.score, 1),
        "pattern": decision.pattern, "current_price": round(decision.price, 3),
        "reasons": decision.reasons,
        "blockers": watch_blockers(decision.raw, asof_time, source, rank, scored=True),
        "upgrade_triggers": watch_upgrade_triggers(asof_time, source),
        "cross_validation": decision.cross_validation, "source": source,
    }


def is_watchlist_feature_candidate(stock: Dict[str, Any]) -> bool:
    return (
        hard_filter(stock) is None
        and 0.8 <= float(stock.get("tail_gain_pct", 0)) <= 2.5
        and 0.8 <= float(stock.get("volume_ratio", 0)) <= 3.0
        and float(stock.get("capital_flow_score", 0)) >= 60
        and str(stock.get("pattern", "none")) != "negative"
    )


def watch_score_from_features(stock: Dict[str, Any]) -> float:
    score = 0.0
    tail_gain = float(stock.get("tail_gain_pct", 0))
    volume_ratio = float(stock.get("volume_ratio", 0))
    capital_flow = float(stock.get("capital_flow_score", 0))
    sector_score = float(stock.get("sector_score", 0))
    if 0.8 <= tail_gain <= 2.5:
        score += min(24, tail_gain * 8)
    if 0.8 <= volume_ratio <= 3.0:
        score += min(18, (volume_ratio - 1) * 6)
    score += min(20, capital_flow * 0.20)
    score += min(20, sector_score)
    if str(stock.get("pattern", "none")) not in {"none", "", "negative"}:
        score += 10
    return score


def watch_item_from_features(stock: Dict[str, Any], asof_time: str, source: str) -> Dict[str, Any]:
    code = normalize_code(stock.get("code", ""))
    tail_gain = float(stock.get("tail_gain_pct", 0))
    volume_ratio = float(stock.get("volume_ratio", 0))
    capital_flow = float(stock.get("capital_flow_score", 0))
    sector_score = float(stock.get("sector_score", 0))
    return {
        "code": code, "name": str(stock.get("name", code)),
        "sector": str(stock.get("sector", "未知")), "status": "observe",
        "grade": "WATCH", "score": round(watch_score_from_features(stock), 1),
        "pattern": str(stock.get("pattern", "none")),
        "current_price": round(float(stock.get("price", 0) or 0), 3),
        "reasons": [
            f"{asof_time}观察：尾盘涨幅{tail_gain:.2f}%，量比{volume_ratio:.2f}",
            f"资金代理分{capital_flow:.0f}，板块共振分{sector_score:.0f}",
        ],
        "blockers": watch_blockers(stock, asof_time, source, rank=None, scored=False),
        "upgrade_triggers": watch_upgrade_triggers(asof_time, source),
        "cross_validation": {
            "sector": {"sector": stock.get("sector", "未知"), "sector_score": sector_score},
            "intraday_heat": {
                "day_position_pct": stock.get("day_position_pct"),
                "price_to_day_high_pct": stock.get("price_to_day_high_pct"),
                "last_bar_vol_share_tail_pct": stock.get("last_bar_vol_share_tail_pct"),
            },
            "decision_note": "观察池只用于复核，不代表可买入。",
        },
        "source": source,
    }


def build_watchlist(decisions, feature_rows, final_orders, asof_time, source, limit=8):
    final_codes = {normalize_code(o.get("code", "")) for o in final_orders}
    ranked_decisions = sorted(decisions, key=lambda item: item.score, reverse=True)
    seen = set(final_codes)
    watchlist: List[Dict[str, Any]] = []
    for rank, decision in enumerate(ranked_decisions, start=1):
        if decision.code in seen:
            continue
        watchlist.append(watch_item_from_decision(decision, asof_time, source, rank))
        seen.add(decision.code)
        if len(watchlist) >= limit:
            return watchlist
    feature_candidates = [
        s for s in feature_rows
        if normalize_code(s.get("code", "")) not in seen and is_watchlist_feature_candidate(s)
    ]
    feature_candidates.sort(key=watch_score_from_features, reverse=True)
    for stock in feature_candidates:
        watchlist.append(watch_item_from_features(stock, asof_time, source))
        seen.add(normalize_code(stock.get("code", "")))
        if len(watchlist) >= limit:
            break
    return watchlist


def build_market_notes(final_orders, watchlist, rejects, decisions, feature_rows, asof_time):
    if final_orders:
        action_note = f"{asof_time}有{len(final_orders)}个正式可买标的，仍需按仓位和次日条件退出矩阵执行。"
    elif watchlist:
        action_note = f"{asof_time}无正式可买标的，给出{len(watchlist)}个观察标的；观察不等于买入。"
    else:
        action_note = f"{asof_time}无正式可买标的，观察池也为空；当天以空仓和复盘为主。"
    notes = [
        action_note,
        f"本次扫描{len(feature_rows)}只，正式评分候选{len(decisions)}只，过滤/缺失{len(rejects)}只。",
    ]
    if not final_orders and watchlist:
        blocker_counts: Dict[str, int] = {}
        for item in watchlist:
            for blocker in item.get("blockers", []):
                blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        if blocker_counts:
            top_blocker = sorted(blocker_counts.items(), key=lambda i: i[1], reverse=True)[0][0]
            notes.append(f"主要未买原因：{top_blocker}。")
    return notes


def is_intraday_active(stock: Dict[str, Any]) -> bool:
    return (
        float(stock.get("tail_gain_pct", 0)) >= 0.25
        and float(stock.get("volume_ratio", 0)) >= 1.05
        and str(stock.get("pattern", "none")) not in {"negative", "none", ""}
    )


def apply_live_sector_resonance(stocks: List[Dict[str, Any]]) -> None:
    """Pool-based sector resonance (fixture/test path).

    Kept for backward compatibility and unit tests. The live path additionally
    merges full-market industry scores from the enrichment bundle via
    ``merge_enrichment_sector_scores``.
    """
    active_counts: Dict[str, int] = {}
    for stock in stocks:
        sector = str(stock.get("sector") or "未知")
        if sector != "未知" and is_intraday_active(stock):
            active_counts[sector] = active_counts.get(sector, 0) + 1
    for stock in stocks:
        sector = str(stock.get("sector") or "未知")
        count = active_counts.get(sector, 0)
        if sector == "未知":
            score = 3
        elif count >= 3:
            score = 20
        elif count >= 2:
            score = 14
        elif count == 1 and is_intraday_active(stock):
            score = 3
        else:
            score = 3
        stock["sector_score"] = score
        stock["sector_resonance_count"] = count


def merge_enrichment_sector_scores(stocks: List[Dict[str, Any]], enrichment: Dict[str, Any]) -> None:
    """Override pool-based sector scores with full-market industry data when available."""
    sectors = enrichment.get("sectors", {}) if enrichment else {}
    for stock in stocks:
        sector = str(stock.get("sector") or "未知")
        info = sectors.get(sector)
        if not info:
            continue
        active = info.get("active_count")
        ret = info.get("return_pct", 0) or 0
        if active is not None and active >= 3:
            stock["sector_score"] = 20
        elif active is not None and active >= 2:
            stock["sector_score"] = 14
        elif active is not None and active == 1:
            stock["sector_score"] = 6
        # sector momentum from industry return
        if ret:
            stock["sector_momentum"] = ret / 100.0
        stock["sector_resonance_count"] = active if active is not None else stock.get("sector_resonance_count", 0)


# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

def screen_fixture(fixture: Dict[str, Any], trade_date: Optional[str], asof_time: str) -> Dict[str, Any]:
    day_obj = pick_fixture_day(fixture, trade_date)
    market = market_report(day_obj)
    decisions: List[StockDecision] = []
    rejects: List[Dict[str, str]] = []
    if market["state"] != "halt":
        for stock in day_obj.get("stocks", []):
            reject = hard_filter(stock)
            if reject:
                rejects.append({"code": normalize_code(stock.get("code", "")), "name": stock.get("name", ""), "reason": reject})
                continue
            decision = score_stock(stock, market, asof_time, "fixture")
            if decision:
                decisions.append(decision)
            else:
                rejects.append({"code": normalize_code(stock.get("code", "")), "name": stock.get("name", ""), "reason": "分数/量价/形态未达C版阈值"})

    decisions.sort(key=lambda item: item.score, reverse=True)
    orders = select_final_orders(decisions, asof_time, "fixture", market.get("state", "range"))
    watchlist = build_watchlist(decisions, list(day_obj.get("stocks", [])), orders, asof_time, "fixture")
    market_notes = build_market_notes(orders, watchlist, rejects, decisions, list(day_obj.get("stocks", [])), asof_time)
    return {
        "mode": "screen", "trade_date": day_obj["trade_date"], "asof_time": asof_time,
        "market_state": market, "final_orders": orders, "watchlist": watchlist,
        "market_notes": market_notes, "rejects": rejects,
        "params_hash": PARAMS_HASH,
        "disclaimer": "仅供研究和复盘，不构成投资建议。",
    }


def _fixture_bars(stock: Dict[str, Any], trade_date: str) -> List[Dict[str, Any]]:
    """Build a coarse 3-point bar list from fixture intraday fields."""
    next_open = float(stock.get("next_open", 0) or 0)
    next_1000 = float(stock.get("next_1000", 0) or 0)
    next_1450 = float(stock.get("next_1450", 0) or 0)
    bars = []
    if next_open:
        bars.append({"时间": f"{trade_date} 09:30:00", "开盘": next_open, "收盘": next_open,
                     "最高": next_open, "最低": next_open, "成交量": 0, "成交额": 0})
    if next_1000:
        bars.append({"时间": f"{trade_date} 10:00:00", "开盘": next_1000, "收盘": next_1000,
                     "最高": next_1000, "最低": next_1000, "成交量": 0, "成交额": 0})
    if next_1450:
        bars.append({"时间": f"{trade_date} 14:50:00", "开盘": next_1450, "收盘": next_1450,
                     "最高": next_1450, "最低": next_1450, "成交量": 0, "成交额": 0})
    return bars


def verify_decision(decision: StockDecision, cost: CostModel) -> Dict[str, Any]:
    stock = decision.raw
    entry = decision.suggested_price
    bars = _fixture_bars(stock, stock.get("trade_date", ""))
    if not bars or not float(stock.get("next_open", 0) or 0):
        return {"verification": "missing", "actual_return_pct": None, "exit_reason": "缺少次日真实价格"}
    first_open = float(bars[0]["开盘"])
    open_return = first_open / entry - 1
    return simulate_exit(bars, entry, open_return, cost)


def backtest_fixture(fixture: Dict[str, Any], asof_time: str, days: Optional[int],
                     cost: Optional[CostModel] = None, walk_forward: bool = False) -> Dict[str, Any]:
    cost = cost or CostModel()
    day_objs = fixture["days"][-days:] if days else fixture["days"]
    daily_results: List[Dict[str, Any]] = []
    all_returns: List[float] = []
    all_trades: List[Dict[str, Any]] = []
    failed_notes: List[str] = []

    for day_obj in day_objs:
        screen = screen_fixture({"days": [day_obj]}, day_obj["trade_date"], asof_time)
        picks = []
        for order in screen["final_orders"]:
            raw = next(s for s in day_obj["stocks"] if normalize_code(s["code"]) == order["code"])
            raw = dict(raw)
            raw["trade_date"] = day_obj["trade_date"]
            decision = score_stock(raw, screen["market_state"], asof_time, "fixture")
            if not decision:
                continue
            verification = verify_decision(decision, cost)
            if verification.get("actual_return_pct") is not None:
                all_returns.append(float(verification["actual_return_pct"]))
                trade = {**order, **verification, "market_state": screen["market_state"].get("state")}
                all_trades.append(trade)
                if verification["verification"] == "failed":
                    failed_notes.append(f"{day_obj['trade_date']} {decision.code} {decision.name}: {verification['exit_reason']}")
            picks.append({**order, **verification})
        avg_ret = statistics.mean([p["actual_return_pct"] for p in picks if p["actual_return_pct"] is not None]) if picks else 0.0
        daily_results.append({
            "trade_date": day_obj["trade_date"], "next_trade_date": day_obj.get("next_trade_date"),
            "pick_count": len(picks), "actual_return_pct": round(avg_ret, 2),
            "verification": "success" if avg_ret > 0 else ("no_trade" if not picks else "failed"),
            "picks": picks,
        })

    return _assemble_backtest_report(daily_results, all_returns, all_trades, failed_notes,
                                     asof_time, walk_forward, "fixture",
                                     "小样本模拟仅用于验证流程，不代表未来收益。")


# ---------------------------------------------------------------------------
# Live path
# ---------------------------------------------------------------------------

def parse_bar_time(value: Any) -> datetime:
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")


def _live_minute_bars(backend: DataBackend, code: str, trade_date: str, asof_time: str):
    start = f"{trade_date} 09:30:00"
    end = f"{trade_date} {asof_time}:00"
    rows = backend.minute_bars(code, start, end, period=5)
    return rows


def live_stock_features(code: str, trade_date: str, asof_time: str,
                        backend: DataBackend, enrichment: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    rows = _live_minute_bars(backend, code, trade_date, asof_time)
    if not rows or len(rows) < 12:
        return None

    # Normalize to floats.
    bars = []
    for r in rows:
        bars.append({
            "时间": r["时间"], "开盘": float(r.get("开盘", 0) or 0),
            "收盘": float(r.get("收盘", 0) or 0), "最高": float(r.get("最高", 0) or 0),
            "最低": float(r.get("最低", 0) or 0), "成交量": float(r.get("成交量", 0) or 0),
            "成交额": float(r.get("成交额", 0) or 0),
        })

    price = float(bars[-1]["收盘"])
    # FIX (P0-1): pre_close must be the previous trading day's close, not the
    # first 5-minute bar's open. The "当日涨幅 1%-4%" filter is defined relative
    # to昨收, so this corrects the signal semantics end-to-end.
    pre_close = backend.daily_prev_close(code, trade_date)
    if pre_close is None or pre_close <= 0:
        pre_close = float(bars[0]["开盘"])  # graceful fallback, flagged below

    tail_start_dt = datetime.strptime(f"{trade_date} 14:00:00", "%Y-%m-%d %H:%M:%S")
    tail = [b for b in bars if parse_bar_time(b["时间"]) >= tail_start_dt]
    if not tail:
        tail = bars[-4:]
    tail_open = float(tail[0]["开盘"])
    tail_gain_pct = (price / tail_open - 1) * 100 if tail_open else 0
    tail_vol = sum(b["成交量"] for b in tail)
    total_vol = sum(b["成交量"] for b in bars) or 1
    amount_mn = sum(b["成交额"] for b in bars) / 1_000_000
    early = [b for b in bars if parse_bar_time(b["时间"]) < tail_start_dt]
    early_avg = statistics.mean([b["成交量"] for b in early[-12:]]) if early else statistics.mean([b["成交量"] for b in bars])
    tail_avg = statistics.mean([b["成交量"] for b in tail]) if tail else 0
    intraday_tail_vol_ratio = tail_avg / early_avg if early_avg else 0

    # FIX (P0-9): standard 量比 = today's avg per-minute vol / past 5-day avg
    # per-minute vol. Falls back to the intraday tail/early proxy (renamed) when
    # daily history is unavailable, and records which definition was used.
    volume_ratio, volume_ratio_source = _compute_standard_volume_ratio(
        backend, code, trade_date, asof_time, total_vol, intraday_tail_vol_ratio)

    vwap = sum(b["收盘"] * b["成交量"] for b in bars) / total_vol
    above_avg = statistics.mean([1.0 if b["收盘"] >= vwap else 0.0 for b in bars])
    day_high = max(b["最高"] for b in bars)
    before_tail_high = max((b["最高"] for b in early), default=day_high)
    day_low = min(b["最低"] for b in bars)
    day_position_pct = (price - day_low) / max(day_high - day_low, 1e-9) * 100
    price_to_day_high_pct = (price / day_high - 1) * 100 if day_high else 0
    ma_state = "bull" if price >= vwap and above_avg >= 0.60 else ("bear" if price < vwap * 0.985 else "range")

    pattern = "none"
    if price >= before_tail_high * 0.998 and tail_gain_pct > 0.35 and intraday_tail_vol_ratio > 1.15:
        pattern = "breakout"
    elif above_avg >= 0.68 and price >= day_high * 0.995 and intraday_tail_vol_ratio > 1.05:
        pattern = "strong_accel"
    elif tail_gain_pct > 0.3 and price > vwap and ma_state != "bear":
        pattern = "pullback"
    if price < max(b["收盘"] for b in tail) * 0.985:
        pattern = "negative"

    signed_amount = 0.0
    for b in tail:
        direction = 1 if b["收盘"] >= b["开盘"] else -1
        signed_amount += direction * b["成交额"]
    capital_score = max(0, min(100, 45 + signed_amount / max(sum(b["成交额"] for b in tail), 1) * 45))

    # --- enrichment merge (P0-2): real name/sector/pe/mktcap/st/news/hot ---
    smap = (enrichment or {}).get("stock_map", {}).get(code, {})
    news_map = (enrichment or {}).get("news_sentiment", {}) or {}
    hot_map = (enrichment or {}).get("hot_rank", {}) or {}
    gaps = (enrichment or {}).get("gaps", []) or []
    fundamentals_status = "missing" if "spot_snapshot_unavailable_fundamentals_capped" in gaps else "available"

    name = smap.get("name", code)
    sector = smap.get("sector") or STATIC_SECTOR_MAP.get(code, "未知")
    pe = smap.get("pe")
    market_cap_bn = smap.get("market_cap_bn")
    if market_cap_bn is None:
        market_cap_bn = 9999
    turnover_rate = smap.get("turnover_rate", 0) or 0
    news_sentiment = news_map.get(code, 0)
    hot_rank = hot_map.get(code)

    last_bar_vol_share = (bars[-1]["成交量"] / max(sum(b["成交量"] for b in tail), 1) * 100) if tail else 100

    return {
        "code": code, "name": name, "sector": sector, "price": price, "pre_close": pre_close,
        "tail_gain_pct": tail_gain_pct, "volume_ratio": volume_ratio,
        "tail_vol_ratio": tail_vol / total_vol,
        "turnover_rate": turnover_rate, "amount_mn": amount_mn,
        "market_cap_bn": market_cap_bn, "pe": pe, "ma_state": ma_state, "pattern": pattern,
        "capital_flow_score": capital_score, "sector_score": 3,
        "news_sentiment": news_sentiment, "hot_rank": hot_rank,
        "day_position_pct": day_position_pct, "price_to_day_high_pct": price_to_day_high_pct,
        "last_bar_vol_share_tail_pct": last_bar_vol_share,
        "fundamentals_status": fundamentals_status,
        "volume_ratio_source": volume_ratio_source,
        "intraday_tail_vol_ratio": intraday_tail_vol_ratio,
    }


def _compute_standard_volume_ratio(backend: DataBackend, code: str, trade_date: str,
                                   asof_time: str, today_total_vol: float,
                                   intraday_proxy: float) -> Tuple[float, str]:
    try:
        hist = backend.daily_history(code, trade_date, 7)
        if hist and len(hist) >= 2:
            past5 = hist[-6:-1] if len(hist) >= 6 else hist[:-1]
            past5 = [h for h in past5 if h.get("volume")]
            if past5:
                avg_daily = statistics.mean(h["volume"] for h in past5)
                elapsed = trading_minutes_elapsed(asof_time) or 1
                today_avg_per_min = today_total_vol / elapsed
                past_avg_per_min = avg_daily / 240.0
                if past_avg_per_min > 0:
                    return round(today_avg_per_min / past_avg_per_min, 3), "standard_5d"
    except Exception:
        pass
    return round(intraday_proxy, 3), "proxy_tail_early"


def live_market_state(trade_date: str, enrichment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """FIX (P0-3): read real market state from enrichment instead of恒定 range."""
    if enrichment and enrichment.get("market"):
        m = dict(enrichment["market"])
        m["data_note"] = "live market state from enrichment cache"
        return m
    return {
        "state": "range", "index_tail_return_pct": None, "breadth_up_ratio": None,
        "limit_up": None, "limit_down": None, "halt_reason": None,
        "data_note": "enrichment unavailable; live path defaults to range — build enrichment first",
    }


def build_full_universe(backend: DataBackend, enrichment: Optional[Dict[str, Any]]) -> List[str]:
    """Full Shanghai main-board universe (60-series, excl. 688/ST/banks/agriculture).

    Replaces the old hand-picked ~100-name DEFAULT_UNIVERSE (which had selection
    / survivorship bias). Priority:
      1. spot snapshot (via enrichment stock_map) -> point-in-time, with real
         name/sector/ST/liquidity filters.
      2. all_codes() (stock_info_a_code_name) -> full code list, name-based ST
         + sector-keyword fallback when industry data is unavailable.
    Both paths exclude 银行/农林牧渔/ST per C 版 methodology.
    """
    smap = (enrichment or {}).get("stock_map", {}) or {}
    if smap:
        codes = []
        for code, info in smap.items():
            if not code.startswith("60") or code.startswith("688"):
                continue
            if info.get("is_st"):
                continue
            if str(info.get("sector", "")) in EXCLUDED_SECTORS:
                continue
            if (info.get("market_cap_bn") or 0) and info.get("market_cap_bn") < 30:
                continue
            if (info.get("amount_mn") or 0) and info.get("amount_mn") < 300:
                continue
            codes.append(code)
        if codes:
            return codes

    # Fallback: full code list from all_codes, name-based exclusions.
    all_codes = backend.all_codes() or []
    if not all_codes:
        # Last resort: use the DEFAULT_UNIVERSE to avoid a completely empty scan
        return [c for c in DEFAULT_UNIVERSE if is_c_version_universe(c)]
    codes = []
    for item in all_codes:
        code = normalize_code(item.get("code", ""))
        name = str(item.get("name", ""))
        if not code.startswith("60") or code.startswith("688"):
            continue
        if "ST" in name.upper():
            continue
        if any(kw in name for kw in EXCLUDED_NAME_KEYWORDS):
            continue
        codes.append(code)
    return codes


def _default_workers(universe_size: int) -> int:
    """Concurrency for parallel feature fetch. Scale with universe but cap to
    be polite to free endpoints; small universes run serially (test/simple)."""
    if universe_size <= 30:
        return 1
    return min(16, max(4, universe_size // 40))


def screen_live(args: argparse.Namespace) -> Dict[str, Any]:
    backend = get_backend(DEFAULT_CACHE_DIR, args.fetch_timeout, not args.no_eastmoney_fallback)
    trade_date = args.trade_date or _infer_today_trading_day(backend)
    codes = load_codes(args.codes, Path(args.universe_file) if args.universe_file else None, args.limit)

    # Build or load enrichment. This is the slow step; for live it should be
    # pre-built at 09:00 via the `enrich` subcommand, but we build on demand.
    enrichment = load_enrichment(DEFAULT_CACHE_DIR, trade_date)
    if enrichment is None:
        enrichment = build_enrichment(trade_date, backend, args.asof_time, codes)
        save_enrichment(DEFAULT_CACHE_DIR, enrichment)

    # If no explicit universe was given, use the full main-board universe
    # (excl. 688/ST/银行/农林牧渔) — replaces the old hand-picked 100-name list.
    # For speed and reliability with free public endpoints, when no explicit
    # universe is given and the limit is small (or not set), stay with the
    # DEFAULT_UNIVERSE (100 liquid names) rather than expanding to the full
    # ~1700 main board which would trigger thousands of serial API calls and
    # likely timeout.
    if not args.codes and not args.universe_file:
        if (args.limit or 0) <= 100:
            # Keep the 100-name DEFAULT_UNIVERSE already loaded by load_codes
            pass
        else:
            full = build_full_universe(backend, enrichment)
            if full:
                codes = full[:args.limit]

    market = live_market_state(trade_date, enrichment)
    decisions: List[StockDecision] = []
    rejects: List[Dict[str, str]] = []
    feature_rows: List[Dict[str, Any]] = []
    # Parallel fetch: full main board (~1700) is infeasible serially. Each
    # live_stock_features call is I/O-bound (network), so a thread pool gives
    # near-linear speedup. Cache hits (minute/daily already fetched) skip the
    # network entirely. Cap workers to be polite to free public endpoints.
    workers = min(getattr(args, "workers", 0) or _default_workers(len(codes)), 32)
    if workers > 1 and len(codes) > workers:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(live_stock_features, code, trade_date, args.asof_time, backend, enrichment): code
                       for code in codes}
            for fut in concurrent.futures.as_completed(futures):
                code = futures[fut]
                try:
                    features = fut.result()
                except Exception:
                    features = None
                if not features:
                    rejects.append({"code": code, "name": code, "reason": "免费数据接口未返回完整分钟/日线数据"})
                    continue
                feature_rows.append(features)
    else:
        for code in codes:
            features = live_stock_features(code, trade_date, args.asof_time, backend, enrichment)
            if not features:
                rejects.append({"code": code, "name": code, "reason": "免费数据接口未返回完整分钟/日线数据"})
                continue
            feature_rows.append(features)

    apply_live_sector_resonance(feature_rows)
    merge_enrichment_sector_scores(feature_rows, enrichment)

    for features in feature_rows:
        code = normalize_code(features.get("code", ""))
        reject = hard_filter(features)
        if reject:
            rejects.append({"code": code, "name": features.get("name", code), "reason": reject})
            continue
        decision = score_stock(features, market, args.asof_time, "akshare")
        if decision:
            decisions.append(decision)
        else:
            rejects.append({"code": code, "name": features.get("name", code), "reason": "分数/量价/形态未达C版阈值"})
    decisions.sort(key=lambda item: item.score, reverse=True)
    final_orders = select_final_orders(decisions, args.asof_time, "akshare", market.get("state", "range"),
                                       apply_caps=not args.no_portfolio_caps)
    watchlist = build_watchlist(decisions, feature_rows, final_orders, args.asof_time, "akshare")
    market_notes = build_market_notes(final_orders, watchlist, rejects, decisions, feature_rows, args.asof_time)
    return {
        "mode": "screen", "trade_date": trade_date, "asof_time": args.asof_time,
        "market_state": market, "final_orders": final_orders, "watchlist": watchlist,
        "market_notes": market_notes, "rejects": rejects[:200],
        "enrichment_gaps": enrichment.get("gaps", []),
        "params_hash": PARAMS_HASH,
        "data_note": "Multi-source (iFinD preferred on Kimi Code → AKShare multi-endpoint → Eastmoney HTTP), disk-cached. Level-2/F10 gaps proxied and confidence-capped.",
        "disclaimer": "仅供研究和复盘，不构成投资建议。",
    }


def verify_live_order(order: Dict[str, Any], next_trade_date: str, backend: DataBackend,
                      cost: CostModel) -> Dict[str, Any]:
    code = normalize_code(order["code"])
    start = f"{next_trade_date} 09:30:00"
    end = f"{next_trade_date} 15:00:00"
    rows = None
    try:
        rows = backend.minute_bars(code, start, end, period=5)
    except Exception as exc:
        return {"entry_price": order.get("suggested_price"), "actual_return_pct": None,
                "actual_open_return_pct": None, "verification": "missing",
                "exit_reason": f"次日分钟线获取失败: {exc}"}
    if not rows:
        return {"entry_price": order.get("suggested_price"), "actual_return_pct": None,
                "actual_open_return_pct": None, "verification": "missing",
                "exit_reason": "次日分钟线为空"}

    bars = [{"时间": r["时间"], "开盘": float(r.get("开盘", 0) or 0), "收盘": float(r.get("收盘", 0) or 0),
             "最高": float(r.get("最高", 0) or 0), "最低": float(r.get("最低", 0) or 0),
             "成交量": float(r.get("成交量", 0) or 0), "成交额": float(r.get("成交额", 0) or 0)} for r in rows]
    entry = float(order["suggested_price"])
    next_open = float(bars[0]["开盘"])
    open_return = next_open / entry - 1
    return simulate_exit(bars, entry, open_return, cost)


def backtest_live(args: argparse.Namespace) -> Dict[str, Any]:
    cost = _cost_from_args(args)
    backend = get_backend(DEFAULT_CACHE_DIR, args.fetch_timeout, not args.no_eastmoney_fallback)
    end_day = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()
    calendar = backend.trade_calendar(end_day, 120) or _fallback_calendar(end_day, 120)
    completed_days = [d for d in calendar if d <= end_day.isoformat()]
    original_trade_date = args.trade_date
    if args.trade_date:
        trade_days = [args.trade_date]
    else:
        trade_days = completed_days[-(args.days + 1):-1] if len(completed_days) > args.days else completed_days[:-1]

    daily_results: List[Dict[str, Any]] = []
    returns: List[float] = []
    trades: List[Dict[str, Any]] = []
    failures: List[str] = []
    try:
        for trade_day in trade_days:
            args.trade_date = trade_day
            screen = screen_live(args)
            next_day = _next_trade_date(trade_day, calendar)
            picks: List[Dict[str, Any]] = []
            if next_day and next_day <= end_day.isoformat():
                for order in screen["final_orders"]:
                    verification = verify_live_order(order, next_day, backend, cost)
                    merged = {**order, **verification, "market_state": screen["market_state"].get("state")}
                    picks.append(merged)
                    if verification.get("actual_return_pct") is not None:
                        returns.append(float(verification["actual_return_pct"]))
                        trades.append(merged)
                        if verification["verification"] == "failed":
                            failures.append(f"{trade_day} {order['code']} {order['name']}: {verification['exit_reason']}")
                day_returns = [p["actual_return_pct"] for p in picks if p["actual_return_pct"] is not None]
                actual_return = round(statistics.mean(day_returns), 2) if day_returns else None
                verification_state = "success" if day_returns and actual_return and actual_return > 0 else ("no_trade" if not picks else "failed")
            else:
                actual_return = None
                verification_state = "missing_next_day"
            daily_results.append({
                "trade_date": trade_day, "next_trade_date": next_day,
                "pick_count": len(picks), "actual_return_pct": actual_return,
                "verification": verification_state, "picks": picks,
                "screen_reject_count": len(screen.get("rejects", [])),
            })
    finally:
        args.trade_date = original_trade_date  # FIX (P2-13): no state leakage on exception

    return _assemble_backtest_report(daily_results, returns, trades, failures, args.asof_time,
                                     args.walk_forward, "live",
                                     "Free multi-source data; Level-2 and F10 gaps are proxied and confidence-capped.")


# ---------------------------------------------------------------------------
# Backtest assembly + stats (shared by fixture & live)
# ---------------------------------------------------------------------------

def _assemble_backtest_report(daily_results, returns, trades, failed_notes, asof_time,
                              walk_forward, mode, disclaimer) -> Dict[str, Any]:
    full_stats = summarize(returns, trades)
    attribution_stats = attribution(trades) if trades else {}
    gap = gap_stats(trades)

    retrospective: Dict[str, Any] = {
        "sample_trading_days": len(daily_results),
        "trade_count": full_stats.get("trade_count", 0),
        "win_rate": full_stats.get("win_rate", 0),
        "win_rate_ci": full_stats.get("win_rate_ci"),
        "average_return_pct": full_stats.get("average_return_pct", 0),
        "stats": full_stats,
        "gap_exit_stats": gap,
        "attribution": attribution_stats,
        "params_hash": PARAMS_HASH,
        "execution_model": "conservative: limit fills on bar-close confirmation; costs=佣金+印花税(卖)+沪市过户费+滑点",
        "review": _review_text(full_stats, gap),
        "rule_improvements": _rule_improvement_text(full_stats, attribution_stats),
        "failed_notes": failed_notes[:8],
        "overfitting_guardrail": (
            "阈值变更必须: 1)更新PARAMS_HASH; 2)在CHANGELOG记录原因; "
            "3)在新增样本上OOS验证后再合入。禁止看全样本回测失败簇就地改阈值。"
        ),
    }

    if walk_forward and len(daily_results) >= 4:
        split = max(2, int(len(daily_results) * 0.6))
        train_returns = [r for d in daily_results[:split] for r in
                         [p.get("actual_return_pct") for p in d.get("picks", [])] if r is not None]
        oos_returns = [r for d in daily_results[split:] for r in
                       [p.get("actual_return_pct") for p in d.get("picks", [])] if r is not None]
        retrospective["walk_forward"] = {
            "split_index": split,
            "train": summarize(train_returns),
            "oos": summarize(oos_returns),
            "overfit_warning": bool(train_returns and oos_returns and
                                    summarize(train_returns)["average_return_pct"] -
                                    summarize(oos_returns)["average_return_pct"] > 0.3),
        }

    return {
        "mode": "backtest", "asof_time": asof_time,
        "daily_results": daily_results, "retrospective": retrospective,
        "data_note": "Multi-source free data; conservative fill model; explicit costs.",
        "disclaimer": disclaimer,
    }


def _review_text(stats: Dict[str, Any], gap: Dict[str, Any]) -> str:
    n = stats.get("trade_count", 0)
    ci = stats.get("win_rate_ci") or {}
    t = stats.get("t_stat", 0)
    parts = [
        f"样本量{n}笔，胜率点估计{stats.get('win_rate', 0)*100:.1f}%"
        f"(Wilson 95% CI {int((ci.get('lower') or 0)*100)}%-{int((ci.get('upper') or 0)*100)}%)，"
        f"均收益{stats.get('average_return_pct', 0)}%，t统计量{t}。"
    ]
    if n < 30:
        parts.append("样本量不足以做统计显著结论，数字仅供流程验证。")
    if abs(stats.get("average_return_pct", 0)) < 0.5 and t < 2:
        parts.append("净期望接近成本，edge未通过显著性检验，需扩样本或OOS验证。")
    if gap.get("gap_exit_count", 0) > 0:
        parts.append(f"跳空止损{gap['gap_exit_count']}次，平均{gap['gap_exit_avg_return_pct']}%，尾部风险需关注。")
    return "".join(parts)


def _rule_improvement_text(stats: Dict[str, Any], attr: Dict[str, Any]) -> List[str]:
    improvements = [
        "bear状态下过滤B级候选(脚本已执行);弱市组合仓位cap已下调至20%。",
        "新闻负面分从提示升级为显著扣分(-18)，避免基本面事件风险。",
        "14:20结果标记为条件买入，14:45-14:50复核尾盘量能和板块共振。",
        "组合风控:总仓位/单行业/单票caps生效，避免板块共振信号导致同行业扎堆。",
        "成交模型:限价单bar收盘确认才成交，成本含印花税/过户费/滑点。",
    ]
    by_grade = attr.get("by_grade", {}) if attr else {}
    if by_grade.get("B", {}).get("win_rate", 1) < 0.5:
        improvements.append("B级回测胜率偏低，考虑弱市/弱板块下直接剔除B级。")
    return improvements


# ---------------------------------------------------------------------------
# Calendar / universe helpers
# ---------------------------------------------------------------------------

def _infer_today_trading_day(backend: DataBackend) -> str:
    cal = backend.trade_calendar(date.today(), 1)
    if cal:
        return cal[-1]
    today = date.today()
    return today.isoformat() if today.weekday() < 5 else (today - timedelta(days=today.weekday() - 4)).isoformat()


def _fallback_calendar(end_day: date, lookback: int) -> List[str]:
    days: List[str] = []
    current = end_day - timedelta(days=lookback * 2)
    while current <= end_day + timedelta(days=5):
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _next_trade_date(trade_day: str, calendar: Sequence[str]) -> Optional[str]:
    for idx, d in enumerate(calendar):
        if d == trade_day and idx + 1 < len(calendar):
            return calendar[idx + 1]
    return None


def load_codes(codes_arg: Optional[str], universe_file: Optional[Path], limit: Optional[int]) -> List[str]:
    if codes_arg:
        codes = [normalize_code(c) for c in codes_arg.split(",") if c.strip()]
    elif universe_file:
        codes = [normalize_code(line.strip()) for line in universe_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        codes = list(DEFAULT_UNIVERSE)
    codes = [c for c in codes if is_c_version_universe(c)]
    return codes[:limit] if limit else codes


def _cost_from_args(args: argparse.Namespace) -> CostModel:
    return CostModel(slippage_bps=getattr(args, "cost_slippage_bps", 5.0e-4))


# ---------------------------------------------------------------------------
# CLI: enrich subcommand
# ---------------------------------------------------------------------------

def cmd_enrich(args: argparse.Namespace) -> Dict[str, Any]:
    backend = get_backend(DEFAULT_CACHE_DIR, args.fetch_timeout, not args.no_eastmoney_fallback)
    codes = load_codes(args.codes, Path(args.universe_file) if args.universe_file else None, args.limit)
    bundle = build_enrichment(args.trade_date, backend, args.asof_time, codes)
    path = save_enrichment(DEFAULT_CACHE_DIR, bundle)
    bundle["saved_to"] = str(path)
    return bundle


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def markdown_value(value: Any, default: str = "-") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def markdown_list(items: Sequence[Any]) -> List[str]:
    return [f"- {markdown_value(item)}" for item in items] if items else ["- 无"]


def render_orders_markdown(title: str, orders: Sequence[Dict[str, Any]], empty_note: str) -> List[str]:
    lines = [f"## {title}", ""]
    if not orders:
        lines.extend([empty_note, ""])
        return lines
    for idx, order in enumerate(orders, start=1):
        lines.extend([
            f"### {idx}. {order.get('code')} {order.get('name', '')}", "",
            f"- 板块/sector: {markdown_value(order.get('sector'))}",
            f"- 等级/grade: {markdown_value(order.get('grade'))}",
            f"- 分数/score: {markdown_value(order.get('score'))}",
            f"- 形态/pattern: {markdown_value(order.get('pattern'))}",
            f"- 当前价/current price: {markdown_value(order.get('current_price'))}",
        ])
        if "suggested_price" in order:
            lines.append(f"- 建议限价/suggested price: {markdown_value(order.get('suggested_price'))}")
        if "position_pct" in order:
            lines.append(f"- 建议仓位/position: {markdown_value(order.get('position_pct'))}%")
        ca = order.get("constraints_applied")
        if ca:
            lines.append(f"- 组合风控/portfolio caps: 总{ca.get('total_after')}% / 行业{ca.get('sector_after')}% (cap {ca['caps']['max_total']}/{ca['caps']['max_per_sector']}/{ca['caps']['max_per_name']})")
        blockers = order.get("blockers")
        if blockers:
            lines.append("- 阻碍/blockers:")
            lines.extend(markdown_list(blockers))
        triggers = order.get("upgrade_triggers")
        if triggers:
            lines.append("- 升级条件/upgrade triggers:")
            lines.extend(markdown_list(triggers))
        reasons = order.get("reasons", [])
        lines.append("- 理由/reasons:")
        lines.extend(markdown_list(reasons))
        lines.append("")
    return lines


def render_screen_markdown(report: Dict[str, Any]) -> str:
    market = report.get("market_state", {})
    lines = [
        "# A股尾盘选股报告", "",
        f"- 日期/trade date: {markdown_value(report.get('trade_date'))}",
        f"- 时间/asof time: {markdown_value(report.get('asof_time'))}",
        f"- 市场状态/market state: {markdown_value(market.get('state'))}",
        f"- 模式/mode: {markdown_value(report.get('mode'))}",
        f"- 参数哈希/params hash: {markdown_value(report.get('params_hash'))}",
        "",
        "## 市场说明 market_notes", "",
    ]
    lines.extend(markdown_list(report.get("market_notes", [])))
    lines.append("")
    if report.get("enrichment_gaps"):
        lines.append("## 数据缺口 enrichment gaps")
        lines.extend(markdown_list(report["enrichment_gaps"]))
        lines.append("")
    lines.extend(render_orders_markdown("正式可买 final_orders", report.get("final_orders", []), "无正式可买标的。"))
    lines.extend(render_orders_markdown("观察池 watchlist", report.get("watchlist", []), "无观察标的。"))
    rejects = report.get("rejects", [])
    lines.extend(["## 过滤统计 rejects", "", f"- 过滤/缺失数量: {len(rejects)}"])
    for item in rejects[:10]:
        lines.append(f"- {item.get('code')} {item.get('name', '')}: {item.get('reason')}")
    if len(rejects) > 10:
        lines.append(f"- ... 其余 {len(rejects) - 10} 条略")
    lines.extend(["", "## 声明 disclaimer", "",
                  markdown_value(report.get("disclaimer", "仅供研究和复盘，不构成投资建议。"))])
    return "\n".join(lines).rstrip() + "\n"


def render_backtest_markdown(report: Dict[str, Any]) -> str:
    retro = report.get("retrospective", {})
    stats = retro.get("stats", {})
    ci = retro.get("win_rate_ci") or {}
    gap = retro.get("gap_exit_stats", {}) or {}
    lines = [
        "# A股尾盘回测报告", "",
        f"- 时间/asof time: {markdown_value(report.get('asof_time'))}",
        f"- 样本交易日/sample days: {markdown_value(retro.get('sample_trading_days'))}",
        f"- 交易数/trades: {markdown_value(retro.get('trade_count'))}",
        f"- 胜率/win rate: {markdown_value(retro.get('win_rate'))} (95% CI {markdown_value(ci.get('lower'))}-{markdown_value(ci.get('upper'))})",
        f"- 平均收益/avg return pct: {markdown_value(retro.get('average_return_pct'))}",
        f"- t统计量/t-stat: {markdown_value(stats.get('t_stat'))}",
        f"- 最大回撤/max drawdown: {markdown_value(stats.get('max_drawdown_pct'))}",
        f"- 夏普/Sharpe: {markdown_value(stats.get('sharpe_annualized'))}",
        f"- 跳空止损/gap exits: {markdown_value(gap.get('gap_exit_count'))}次，均{markdown_value(gap.get('gap_exit_avg_return_pct'))}%",
        f"- 成交模型/execution: {markdown_value(retro.get('execution_model'))}",
        f"- 参数哈希/params hash: {markdown_value(retro.get('params_hash'))}",
        "",
        "## 复盘结论 review", "",
        markdown_value(retro.get("review")),
        "",
        "## 规则改进 rule_improvements", "",
    ]
    lines.extend(markdown_list(retro.get("rule_improvements", [])))
    if retro.get("walk_forward"):
        wf = retro["walk_forward"]
        lines.extend(["", "## Walk-Forward (train/OOS)", "",
                      f"- split index: {wf.get('split_index')}",
                      f"- train avg return: {wf.get('train', {}).get('average_return_pct')}%",
                      f"- oos avg return: {wf.get('oos', {}).get('average_return_pct')}%",
                      f"- overfit warning: {wf.get('overfit_warning')}"])
    if retro.get("attribution"):
        lines.extend(["", "## 归因 attribution", ""])
        for dim, buckets in retro["attribution"].items():
            lines.append(f"- {dim}:")
            for k, v in buckets.items():
                lines.append(f"  - {k}: n={v.get('n')}, 胜率={v.get('win_rate')}, 均收益={v.get('avg_return_pct')}%")
    lines.extend(["", "## 失败记录 failed_notes", ""])
    lines.extend(markdown_list(retro.get("failed_notes", [])))
    lines.extend(["", "## 每日结果 daily_results", ""])
    for row in report.get("daily_results", []):
        lines.append(
            f"- {row.get('trade_date')} -> {row.get('next_trade_date')}: "
            f"{row.get('verification')}，pick_count={row.get('pick_count')}，return={markdown_value(row.get('actual_return_pct'))}"
        )
    lines.extend(["", "## 反过拟合护栏 overfitting guardrail", "",
                  markdown_value(retro.get("overfitting_guardrail"))])
    lines.extend(["", "## 声明 disclaimer", "",
                  markdown_value(report.get("disclaimer", "仅供研究和复盘，不构成投资建议。"))])
    return "\n".join(lines).rstrip() + "\n"


def render_markdown_report(report: Dict[str, Any]) -> str:
    if report.get("mode") == "screen":
        return render_screen_markdown(report)
    if report.get("mode") == "backtest":
        return render_backtest_markdown(report)
    if report.get("mode") == "enrich":
        return "# 富化缓存 enrichment\n\n```json\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n"
    return "# A股尾盘报告\n\n```json\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n"


def resolve_output_format(output: Optional[str], output_format: str) -> str:
    if output_format != "auto":
        return output_format
    if output and Path(output).suffix.lower() in {".md", ".markdown"}:
        return "markdown"
    return "json"


def write_report(report: Dict[str, Any], output: Optional[str], output_format: str = "auto") -> None:
    resolved = resolve_output_format(output, output_format)
    text = render_markdown_report(report) if resolved == "markdown" else json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output:
        path = Path(output).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(str(path))
    else:
        print(text)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C版A股主板尾盘选股与回测 (v4 multi-source)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("screen", "backtest"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--fixture", type=Path, help="Use deterministic fixture JSON instead of live data")
        cmd.add_argument("--trade-date", help="YYYY-MM-DD decision date")
        cmd.add_argument("--asof-time", default="14:20", help="Decision time, e.g. 14:20 or 14:50")
        cmd.add_argument("--output", help="Write report to this path")
        cmd.add_argument("--format", choices=["auto", "json", "markdown"], default="auto", help="Report format")
        cmd.add_argument("--fetch-timeout", type=int, default=8, help="Seconds before skipping a slow data fetch; 0 disables")
        cmd.add_argument("--no-eastmoney-fallback", action="store_true", help="Skip Eastmoney HTTP fallback")
        cmd.add_argument("--cost-slippage-bps", type=float, default=5.0e-4, help="Per-side slippage in basis points (fraction)")
        cmd.add_argument("--no-portfolio-caps", action="store_true", help="Disable portfolio risk caps (top-5 only)")
        cmd.add_argument("--workers", type=int, default=0, help="Parallel fetch workers; 0 = auto-scale")
    sub.choices["screen"].add_argument("--codes", help="Comma-separated stock codes")
    sub.choices["screen"].add_argument("--universe-file", help="One stock code per line")
    sub.choices["screen"].add_argument("--limit", type=int, default=0, help="Universe size cap; 0 = full main board (~1700, excl. 688/ST/银行/农林牧渔)")
    sub.choices["backtest"].add_argument("--days", type=int, default=5, help="Number of trading days")
    sub.choices["backtest"].add_argument("--codes", help="Comma-separated stock codes for live backtest")
    sub.choices["backtest"].add_argument("--universe-file", help="One stock code per line for live backtest")
    sub.choices["backtest"].add_argument("--limit", type=int, default=0, help="Universe size cap; 0 = full main board")
    sub.choices["backtest"].add_argument("--end-date", help="YYYY-MM-DD last completed date")
    sub.choices["backtest"].add_argument("--walk-forward", action="store_true", help="Split train/OOS and report overfit warning")

    enrich = sub.add_parser("enrich", help="Build and save the daily enrichment cache bundle")
    enrich.add_argument("--trade-date", required=True, help="YYYY-MM-DD")
    enrich.add_argument("--asof-time", default="14:50")
    enrich.add_argument("--codes", help="Comma-separated codes to news-enrich")
    enrich.add_argument("--universe-file", help="One stock code per line")
    enrich.add_argument("--limit", type=int, default=0)
    enrich.add_argument("--fetch-timeout", type=int, default=8)
    enrich.add_argument("--no-eastmoney-fallback", action="store_true")
    enrich.add_argument("--output", help="Write bundle JSON to this path")
    enrich.add_argument("--format", choices=["auto", "json", "markdown"], default="auto")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, 'fixture') and args.fixture:
        fixture = load_fixture(args.fixture.expanduser())
        cost = _cost_from_args(args)
        if args.command == "screen":
            report = screen_fixture(fixture, args.trade_date, args.asof_time)
        else:
            report = backtest_fixture(fixture, args.asof_time, args.days, cost, getattr(args, "walk_forward", False))
        write_report(report, args.output, args.format)
        return 0

    if args.command == "enrich":
        report = cmd_enrich(args)
        write_report(report, args.output, args.format)
        return 0
    if args.command == "screen":
        report = screen_live(args)
    else:
        report = backtest_live(args)
    write_report(report, args.output, args.format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
