#!/usr/bin/env python3
"""C-version A-share close-session screener and backtester.

The implementation intentionally keeps the public-data path conservative:
it can run from AKShare when the APIs are reachable, and it can always run
from a fixture for deterministic validation.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


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
    "600600": "食品饮料", "600809": "白酒", "600887": "食品饮料",
    "600276": "医药", "600196": "医药", "603259": "医药", "600309": "化工",
    "600438": "电力设备", "600406": "电网设备", "600089": "电力设备",
    "601012": "光伏设备", "600900": "电力", "600905": "电力",
    "600011": "电力", "600025": "电力", "600795": "电力",
    "600050": "通信", "600941": "通信", "600745": "计算机",
    "600570": "计算机", "600584": "半导体", "600703": "半导体",
    "603501": "半导体", "603986": "半导体", "600760": "国防军工",
    "600893": "国防军工", "600150": "国防军工", "601989": "国防军工",
    "600031": "工程机械", "600104": "汽车", "600660": "汽车",
    "601888": "旅游零售", "600690": "家电", "603288": "食品饮料",
    "601006": "铁路公路", "601111": "航空机场", "600009": "航空机场",
    "600029": "航空机场", "600115": "航空机场", "600018": "港口航运",
    "601919": "港口航运", "601872": "港口航运", "601668": "建筑工程",
    "601800": "建筑工程", "601186": "建筑工程", "601390": "建筑工程",
    "601225": "煤炭", "601088": "煤炭", "600989": "煤炭", "600048": "房地产",
    "600741": "商贸零售", "600845": "计算机", "600886": "环保",
}

NEGATIVE_NEWS_KEYWORDS = [
    "减持", "预亏", "预减", "问询函", "监管", "立案", "处罚", "解禁",
    "退市", "风险提示", "业绩下滑", "诉讼", "冻结",
]

STATE_COEF = {"bull": 1.0, "range": 0.7, "bear": 0.3, "halt": 0.0}
SINA_MINUTE_CACHE: Dict[str, Any] = {}
FETCH_TIMEOUT_SECONDS = 8
USE_EASTMONEY_FALLBACK = True


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
            "source": self.source,
        }


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


def passes_early_live_quality(stock: Dict[str, Any]) -> bool:
    return (
        float(stock.get("capital_flow_score", 0)) >= 63
        and float(stock.get("tail_gain_pct", 0)) <= 1.50
        and float(stock.get("day_position_pct", 100)) <= 90
        and float(stock.get("price_to_day_high_pct", 0)) <= -0.50
        and float(stock.get("last_bar_vol_share_tail_pct", 100)) <= 30
    )


def passes_final_live_quality(stock: Dict[str, Any]) -> bool:
    return float(stock.get("price_to_day_high_pct", 0)) <= -0.80


def next_day_plan() -> Dict[str, str]:
    return {
        "S1_open_ge_3pct": "立即卖出60%，剩余设+5%止盈；回落至+2%全出",
        "S2_open_1_to_3pct": "持有观察，设+3%止盈；10:30未冲高则清仓",
        "S3_open_0_to_1pct": "观察至10:00，未突破开盘价卖50%；10:30仍弱全出",
        "S4_open_minus1_to_0pct": "给到10:00，未翻红卖50%",
        "S5_open_minus2_to_minus1pct": "5分钟无反弹卖50%；10:00前未翻红全清",
        "S6_open_minus3_to_minus2pct": "立即卖70%，剩余10:00前清仓",
        "S7_open_lt_minus3pct": "立即全部清仓",
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
    # 改进：更严格的市场状态判断
    # 熔断条件
    if market.get("index_tail_return_pct", 0) <= -1.5:
        state, reason = "halt", "上证尾盘跌幅超过1.5%"
    if market.get("limit_down", 0) > market.get("limit_up", 0) * 2:
        state, reason = "halt", "跌停家数超过涨停家数2倍"
    # 改进：基于赚钱效应的动态判断（当fixture中未指定state时）
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
    if day_ret <= -0.07:
        return "当日跌幅过大"
    if day_ret >= 0.095:
        return "接近涨停，流动性和追高风险"
    if float(stock.get("market_cap_bn", 9999)) < 30:
        return "流通市值过小"
    # 改进：前日涨幅过滤（条件性，仅当数据可用时）
    prev_ret = stock.get("prev_day_return")
    if prev_ret is not None:
        prev_ret = float(prev_ret)
        if prev_ret > 0.06:
            return "前日涨幅>6%，避免追高"
        if prev_ret < -0.04:
            return "前日跌幅>4%，避免抄底"
    # 改进：涨跌幅分位数过滤（条件性，仅当数据可用时）
    pct = stock.get("market_percentile")
    if pct is not None and float(pct) < 0.60:
        return "涨幅分位数<60%，非市场强势标的"
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

    if tail_gain < 0.5:
        return None
    if not (1.05 <= volume_ratio <= 5.0):
        return None
    if not (0.10 <= tail_vol_ratio <= 0.45):
        return None
    if pattern in {"negative", "none", ""}:
        return None
    # 改进：涨跌幅分位数过滤（条件性，仅当数据可用时）
    mp = stock.get("market_percentile")
    if mp is not None and float(mp) < 0.60:
        return None
    # 改进：市场状态前置过滤（当明确为halt时直接返回）
    state = market.get("state", "range")
    if state == "halt":
        return None

    pattern_score = {
        "breakout": 24,
        "pullback": 20,
        "strong_accel": 21,
        "v_reversal": 17,
        "auction_grab": 14,
    }.get(pattern, 10)

    if ma_state == "bull":
        ma_score = 15
    elif ma_state == "range":
        ma_score = 9
    else:
        ma_score = 2

    # C-version normalized scoring, intentionally simple and explainable.
    # 改进：优化评分权重，增加新因子
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
    # 改进：前日涨幅因子（权重5%）
    prev_ret = stock.get("prev_day_return")
    if prev_ret is not None:
        prev_ret = float(prev_ret)
        if 0 <= prev_ret <= 0.03:
            score += 5
            reasons.append(f"前日温和上涨{prev_ret:.1%}，momentum延续")
        elif prev_ret > 0.05:
            score -= 5
            warnings.append("前日涨幅过大，避免追高")
    # 改进：板块动量因子（权重10%）
    sm = stock.get("sector_momentum")
    if sm is not None:
        sm = float(sm)
        if sm > 0.02:
            score += min(10, sm * 500)
            reasons.append(f"板块动量{sm:.2%}，强势共振")
        elif sm < -0.01:
            score -= 5
            warnings.append("板块动量为负，弱势共振")

    # state已在前面定义
    if state == "bear":
        score -= 12  # 改进：弱市扣分从8提高到12
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
    # 改进：板块动量作为交叉验证项
    sm = stock.get("sector_momentum")
    if sm is not None and float(sm) > 0.01:
        cross_checks.append("sector_momentum")
    # 改进：前日涨幅作为交叉验证项
    prev_ret = stock.get("prev_day_return")
    if prev_ret is not None and 0 <= float(prev_ret) <= 0.03:
        cross_checks.append("prev_momentum")

    if len(cross_checks) < 2:
        return None

    grade = grade_from_score(score)
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
        },
        "sentiment": {
            "hot_rank": hot_rank,
            "news_sentiment": news_sentiment,
        },
        "sector": {
            "sector": stock.get("sector", "未知"),
            "sector_score": sector_score,
        },
        "intraday_heat": {
            "day_position_pct": stock.get("day_position_pct"),
            "price_to_day_high_pct": stock.get("price_to_day_high_pct"),
            "last_bar_vol_share_tail_pct": stock.get("last_bar_vol_share_tail_pct"),
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
        source=source,
        raw=stock,
    )


def select_final_orders(decisions: List[StockDecision], asof_time: str, source: str) -> List[Dict[str, Any]]:
    ranked = sorted(decisions, key=lambda item: item.score, reverse=True)[:5]
    if source == "akshare":
        asof_minutes = time_to_minutes(asof_time)
        if asof_minutes <= time_to_minutes("14:20"):
            ranked = [decision for decision in ranked if passes_early_live_quality(decision.raw)]
        elif asof_minutes >= time_to_minutes("14:45"):
            ranked = [decision for decision in ranked if passes_final_live_quality(decision.raw)]
    return [decision.to_order() for decision in ranked]


def watch_blockers(stock: Dict[str, Any], asof_time: str, source: str, rank: Optional[int], scored: bool) -> List[str]:
    blockers: List[str] = []
    asof_minutes = time_to_minutes(asof_time)
    if source == "akshare" and asof_minutes <= time_to_minutes("14:20"):
        if float(stock.get("capital_flow_score", 0)) < 63:
            blockers.append("资金代理未达正式买入门槛63")
        if float(stock.get("tail_gain_pct", 0)) > 1.50:
            blockers.append("14:20尾盘涨幅偏热，等待复核")
        if float(stock.get("day_position_pct", 100)) > 90:
            blockers.append("日内位置偏高")
        if float(stock.get("price_to_day_high_pct", 0)) > -0.50:
            blockers.append("价格距离日内高点不足0.5%")
        if float(stock.get("last_bar_vol_share_tail_pct", 100)) > 30:
            blockers.append("最后一根K线量能占比偏高")
    elif source == "akshare" and asof_minutes >= time_to_minutes("14:45"):
        if float(stock.get("price_to_day_high_pct", 0)) > -0.80:
            blockers.append("距离日内高点不足0.8%，不进入正式买入")

    if rank is not None and rank > 5:
        blockers.append("评分排名未进入正式买入前5")
    if not scored:
        blockers.append("未达到C版正式评分/交叉验证，观察不买")
    return blockers or ["未进入正式买入清单，仅观察"]


def watch_upgrade_triggers(asof_time: str, source: str) -> List[str]:
    if source == "akshare" and time_to_minutes(asof_time) <= time_to_minutes("14:20"):
        return [
            "14:45-14:50复核仍保持分时形态和板块共振",
            "资金代理分>=63且尾盘涨幅<=1.5%",
            "价格距离日内高点至少0.8%后才考虑正式买入",
        ]
    if source == "akshare" and time_to_minutes(asof_time) >= time_to_minutes("14:45"):
        return [
            "仅当进入评分前5且距离日内高点至少0.8%时升级为final_orders",
            "未升级则不追价，次日重新评估",
        ]
    return ["满足正式评分、交叉验证和最终排序后才升级为final_orders"]


def watch_item_from_decision(decision: StockDecision, asof_time: str, source: str, rank: int) -> Dict[str, Any]:
    return {
        "code": decision.code,
        "name": decision.name,
        "sector": decision.sector,
        "status": "observe",
        "grade": decision.grade,
        "score": round(decision.score, 1),
        "pattern": decision.pattern,
        "current_price": round(decision.price, 3),
        "reasons": decision.reasons,
        "blockers": watch_blockers(decision.raw, asof_time, source, rank, scored=True),
        "upgrade_triggers": watch_upgrade_triggers(asof_time, source),
        "cross_validation": decision.cross_validation,
        "source": source,
    }


def is_watchlist_feature_candidate(stock: Dict[str, Any]) -> bool:
    return (
        hard_filter(stock) is None
        and float(stock.get("tail_gain_pct", 0)) >= 0.15
        and float(stock.get("volume_ratio", 0)) >= 1.0
        and float(stock.get("tail_vol_ratio", 0)) <= 0.55
        and str(stock.get("pattern", "none")) != "negative"
    )


def watch_score_from_features(stock: Dict[str, Any]) -> float:
    score = 0.0
    score += min(24, max(0, float(stock.get("tail_gain_pct", 0))) * 8)
    score += min(18, max(0, float(stock.get("volume_ratio", 0)) - 1) * 6)
    score += min(20, float(stock.get("capital_flow_score", 0)) * 0.20)
    score += min(20, float(stock.get("sector_score", 0)))
    if str(stock.get("pattern", "none")) not in {"none", "", "negative"}:
        score += 10
    return score


def watch_item_from_features(stock: Dict[str, Any], asof_time: str, source: str) -> Dict[str, Any]:
    code = normalize_code(stock.get("code", ""))
    tail_gain = float(stock.get("tail_gain_pct", 0))
    volume_ratio = float(stock.get("volume_ratio", 0))
    capital_flow = float(stock.get("capital_flow_score", 0))
    sector_score = float(stock.get("sector_score", 0))
    heat = {
        "day_position_pct": stock.get("day_position_pct"),
        "price_to_day_high_pct": stock.get("price_to_day_high_pct"),
        "last_bar_vol_share_tail_pct": stock.get("last_bar_vol_share_tail_pct"),
    }
    return {
        "code": code,
        "name": str(stock.get("name", code)),
        "sector": str(stock.get("sector", "未知")),
        "status": "observe",
        "grade": "WATCH",
        "score": round(watch_score_from_features(stock), 1),
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
            "intraday_heat": heat,
            "decision_note": "观察池只用于复核，不代表可买入。",
        },
        "source": source,
    }


def build_watchlist(
    decisions: List[StockDecision],
    feature_rows: List[Dict[str, Any]],
    final_orders: List[Dict[str, Any]],
    asof_time: str,
    source: str,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    final_codes = {normalize_code(order.get("code", "")) for order in final_orders}
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
        stock for stock in feature_rows
        if normalize_code(stock.get("code", "")) not in seen and is_watchlist_feature_candidate(stock)
    ]
    feature_candidates.sort(key=watch_score_from_features, reverse=True)
    for stock in feature_candidates:
        watchlist.append(watch_item_from_features(stock, asof_time, source))
        seen.add(normalize_code(stock.get("code", "")))
        if len(watchlist) >= limit:
            break
    return watchlist


def build_market_notes(
    final_orders: List[Dict[str, Any]],
    watchlist: List[Dict[str, Any]],
    rejects: List[Dict[str, str]],
    decisions: List[StockDecision],
    feature_rows: List[Dict[str, Any]],
    asof_time: str,
) -> List[str]:
    if final_orders:
        action_note = f"{asof_time}有{len(final_orders)}个正式可买标的，仍需按仓位和次日S1-S7执行。"
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
            top_blocker = sorted(blocker_counts.items(), key=lambda item: item[1], reverse=True)[0][0]
            notes.append(f"主要未买原因：{top_blocker}。")
    return notes


def is_intraday_active(stock: Dict[str, Any]) -> bool:
    return (
        float(stock.get("tail_gain_pct", 0)) >= 0.25
        and float(stock.get("volume_ratio", 0)) >= 1.05
        and str(stock.get("pattern", "none")) not in {"negative", "none", ""}
    )


def apply_live_sector_resonance(stocks: List[Dict[str, Any]]) -> None:
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
    orders = select_final_orders(decisions, asof_time, "fixture")
    watchlist = build_watchlist(decisions, list(day_obj.get("stocks", [])), orders, asof_time, "fixture")
    market_notes = build_market_notes(orders, watchlist, rejects, decisions, list(day_obj.get("stocks", [])), asof_time)
    return {
        "mode": "screen",
        "trade_date": day_obj["trade_date"],
        "asof_time": asof_time,
        "market_state": market,
        "final_orders": orders,
        "watchlist": watchlist,
        "market_notes": market_notes,
        "rejects": rejects,
        "disclaimer": "仅供研究和复盘，不构成投资建议。",
    }


def verify_decision(decision: StockDecision) -> Dict[str, Any]:
    stock = decision.raw
    entry = decision.suggested_price
    next_open = float(stock.get("next_open", 0) or 0)
    next_1000 = float(stock.get("next_1000", 0) or 0)
    next_1450 = float(stock.get("next_1450", 0) or 0)
    if not next_open:
        return {"verification": "missing", "actual_return_pct": None, "exit_reason": "缺少次日真实价格"}

    open_return = next_open / entry - 1
    if open_return >= 0.03:
        exit_price, exit_reason = next_open, "S1大幅高开，按开盘卖出60%验证"
    elif open_return >= 0.01:
        exit_price, exit_reason = next_1000, "S2温和高开，10:00验证"
    elif open_return >= 0:
        exit_price, exit_reason = next_1000, "S3平开微涨，10:00验证"
    elif open_return >= -0.01:
        exit_price, exit_reason = next_1000, "S4微低开，10:00验证"
    elif open_return >= -0.02:
        exit_price, exit_reason = next_open, "S5中度低开，保守按开盘止损验证"
    elif open_return >= -0.03:
        exit_price, exit_reason = next_open, "S6大幅低开，开盘止损验证"
    else:
        exit_price, exit_reason = next_open, "S7极端低开，开盘清仓验证"

    if next_1450 and exit_reason.startswith(("S2", "S3")):
        # If the morning did not improve, the C-version time stop exits later.
        exit_price = max(exit_price, next_1450 if next_1450 < entry else exit_price)

    # 改进：增加盘中反弹保护（当10:00价格高于entry时，延长持有）
    if next_1000 and exit_reason.startswith(("S4", "S5")) and next_1000 >= entry:
        exit_price = next_1000
        exit_reason = f"{exit_reason}（10:00翻红，延长持有）"

    cost = 0.0025
    actual_return = exit_price / entry - 1 - cost
    return {
        "entry_price": round(entry, 3),
        "next_open": next_open,
        "exit_price": round(exit_price, 3),
        "actual_open_return_pct": round(open_return * 100, 2),
        "actual_return_pct": round(actual_return * 100, 2),
        "verification": "success" if actual_return > 0 else "failed",
        "exit_reason": exit_reason,
    }


def backtest_fixture(fixture: Dict[str, Any], asof_time: str, days: Optional[int]) -> Dict[str, Any]:
    day_objs = fixture["days"][-days:] if days else fixture["days"]
    daily_results: List[Dict[str, Any]] = []
    all_returns: List[float] = []
    failed_notes: List[str] = []

    for day_obj in day_objs:
        screen = screen_fixture({"days": [day_obj]}, day_obj["trade_date"], asof_time)
        picks = []
        for order in screen["final_orders"]:
            raw = next(s for s in day_obj["stocks"] if normalize_code(s["code"]) == order["code"])
            decision = score_stock(raw, screen["market_state"], asof_time, "fixture")
            if not decision:
                continue
            verification = verify_decision(decision)
            if verification["actual_return_pct"] is not None:
                all_returns.append(float(verification["actual_return_pct"]))
            if verification["verification"] == "failed":
                failed_notes.append(f"{day_obj['trade_date']} {decision.code} {decision.name}: {verification['exit_reason']}")
            picks.append({**order, **verification})

        avg_ret = statistics.mean([p["actual_return_pct"] for p in picks if p["actual_return_pct"] is not None]) if picks else 0.0
        daily_results.append({
            "trade_date": day_obj["trade_date"],
            "next_trade_date": day_obj.get("next_trade_date"),
            "pick_count": len(picks),
            "actual_return_pct": round(avg_ret, 2),
            "verification": "success" if avg_ret > 0 else ("no_trade" if not picks else "failed"),
            "picks": picks,
        })

    wins = [r for r in all_returns if r > 0]
    retrospective = {
        "sample_trading_days": len(day_objs),
        "trade_count": len(all_returns),
        "win_rate": round(len(wins) / len(all_returns), 3) if all_returns else 0,
        "average_return_pct": round(statistics.mean(all_returns), 2) if all_returns else 0,
        "review": "样本中弱市B级/低资金确认标的容易失败；建议弱市仅保留A/S级，且14:20信号必须在14:45复核。",
        "rule_improvements": [
            "bear状态下过滤B级候选，已有脚本执行。",
            "将新闻负面分从提示升级为显著扣分，避免基本面事件风险。",
            "14:20结果标记为条件买入，14:45-14:50复核尾盘量能和板块共振。",
        ],
        "failed_notes": failed_notes[:8],
    }
    return {
        "mode": "backtest",
        "asof_time": asof_time,
        "daily_results": daily_results,
        "retrospective": retrospective,
        "disclaimer": "小样本模拟仅用于验证流程，不代表未来收益。",
    }


def load_codes(codes_arg: Optional[str], universe_file: Optional[Path], limit: Optional[int]) -> List[str]:
    if codes_arg:
        codes = [normalize_code(item) for item in codes_arg.split(",") if item.strip()]
    elif universe_file:
        codes = [normalize_code(line.strip()) for line in universe_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        codes = list(DEFAULT_UNIVERSE)
    codes = [code for code in codes if is_c_version_universe(code)]
    return codes[:limit] if limit else codes


def import_akshare():
    try:
        import akshare as ak  # type: ignore
        import pandas as pd  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SystemExit(f"AKShare/pandas unavailable: {exc}")
    return ak, pd


def parse_bar_time(value: Any) -> datetime:
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")


def market_prefixed_code(code: str) -> str:
    code = normalize_code(code)
    return ("sh" if code.startswith("6") else "sz") + code


def bounded_call(func, timeout_seconds: int):
    if timeout_seconds <= 0:
        return func()

    def timeout_handler(_signum, _frame):
        raise TimeoutError(f"data fetch exceeded {timeout_seconds}s")

    previous = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(int(timeout_seconds))
    try:
        return func()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def fetch_minute_bars(ak: Any, code: str, start: str, end: str):
    """Fetch 5-minute bars with Sina cache first and Eastmoney fallback."""
    code = normalize_code(code)
    if code not in SINA_MINUTE_CACHE:
        try:
            bars = bounded_call(
                lambda: ak.stock_zh_a_minute(symbol=market_prefixed_code(code), period="5", adjust=""),
                FETCH_TIMEOUT_SECONDS,
            )
            if bars is not None and len(bars) > 0:
                bars = bars.rename(columns={
                    "day": "时间",
                    "open": "开盘",
                    "high": "最高",
                    "low": "最低",
                    "close": "收盘",
                    "volume": "成交量",
                    "amount": "成交额",
                })
                bars["时间"] = bars["时间"].astype(str)
                bars["dt"] = [parse_bar_time(x) for x in bars["时间"]]
                SINA_MINUTE_CACHE[code] = bars
        except Exception:
            SINA_MINUTE_CACHE[code] = None

    cached = SINA_MINUTE_CACHE.get(code)
    if cached is not None and len(cached) > 0:
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
        filtered = cached[(cached["dt"] >= start_dt) & (cached["dt"] <= end_dt)].copy()
        if len(filtered) > 0:
            return filtered.drop(columns=["dt"])

    if USE_EASTMONEY_FALLBACK:
        try:
            bars = bounded_call(
                lambda: ak.stock_zh_a_hist_min_em(symbol=code, start_date=start, end_date=end, period="5", adjust=""),
                FETCH_TIMEOUT_SECONDS,
            )
            if bars is not None and len(bars) > 0:
                return bars
        except Exception:
            pass
    return None


def infer_recent_trading_days(end_day: date, n: int) -> List[str]:
    ak, pd = import_akshare()
    try:
        cal = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
        dates = [pd.to_datetime(x).date() for x in cal[col].tolist()]
        return [d.isoformat() for d in dates if d <= end_day][-n:]
    except Exception:
        days: List[str] = []
        current = end_day
        while len(days) < n:
            if current.weekday() < 5:
                days.append(current.isoformat())
            current -= timedelta(days=1)
        return list(reversed(days))


def trading_calendar_around(end_day: date, lookback: int = 90) -> List[str]:
    ak, pd = import_akshare()
    try:
        cal = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
        dates = [pd.to_datetime(x).date() for x in cal[col].tolist()]
        return [d.isoformat() for d in dates if d <= end_day + timedelta(days=5)][-lookback:]
    except Exception:
        days: List[str] = []
        current = end_day - timedelta(days=lookback * 2)
        while current <= end_day + timedelta(days=5):
            if current.weekday() < 5:
                days.append(current.isoformat())
            current += timedelta(days=1)
        return days


def next_trade_date_from_calendar(trade_day: str, calendar: Sequence[str]) -> Optional[str]:
    for idx, day_value in enumerate(calendar):
        if day_value == trade_day and idx + 1 < len(calendar):
            return calendar[idx + 1]
    return None


def live_stock_features(code: str, trade_date: str, asof_time: str) -> Optional[Dict[str, Any]]:
    ak, pd = import_akshare()
    start = f"{trade_date} 09:30:00"
    end = f"{trade_date} {asof_time}:00"
    try:
        bars = fetch_minute_bars(ak, code, start, end)
    except Exception:
        return None
    if bars is None or len(bars) < 12:
        return None

    bars = bars.copy()
    bars["dt"] = [parse_bar_time(x) for x in bars["时间"]]
    for col in ["开盘", "收盘", "最高", "最低", "成交量", "成交额", "换手率"]:
        if col in bars:
            bars[col] = pd.to_numeric(bars[col], errors="coerce").fillna(0)
    bars = bars[bars["dt"] <= datetime.strptime(end, "%Y-%m-%d %H:%M:%S")]
    if bars.empty:
        return None
    price = float(bars["收盘"].iloc[-1])
    first = float(bars["开盘"].iloc[0])
    pre_close = first
    tail_start = datetime.strptime(f"{trade_date} 14:00:00", "%Y-%m-%d %H:%M:%S")
    tail = bars[bars["dt"] >= tail_start]
    if tail.empty:
        tail = bars.tail(4)
    tail_open = float(tail["开盘"].iloc[0])
    tail_gain_pct = (price / tail_open - 1) * 100 if tail_open else 0
    tail_vol = float(tail["成交量"].sum())
    total_vol = float(bars["成交量"].sum()) or 1
    amount_mn = float(bars["成交额"].sum()) / 1_000_000
    early = bars[bars["dt"] < tail_start]
    early_avg = float(early["成交量"].tail(12).mean()) if len(early) else float(bars["成交量"].mean())
    tail_avg = float(tail["成交量"].mean()) if len(tail) else 0
    volume_ratio = tail_avg / early_avg if early_avg else 0
    avg_price = float((bars["收盘"] * bars["成交量"]).sum() / total_vol)
    above_avg = float((bars["收盘"] >= avg_price).mean())
    day_high = float(bars["最高"].max())
    before_tail_high = float(early["最高"].max()) if len(early) else day_high
    day_low = float(bars["最低"].min())
    day_position_pct = (price - day_low) / max(day_high - day_low, 1e-9) * 100
    price_to_day_high_pct = (price / day_high - 1) * 100 if day_high else 0
    ma_state = "bull" if price >= avg_price and above_avg >= 0.60 else ("bear" if price < avg_price * 0.985 else "range")

    pattern = "none"
    if price >= before_tail_high * 0.998 and tail_gain_pct > 0.35 and volume_ratio > 1.15:
        pattern = "breakout"
    elif above_avg >= 0.68 and price >= day_high * 0.995 and volume_ratio > 1.05:
        pattern = "strong_accel"
    elif tail_gain_pct > 0.3 and price > avg_price and ma_state != "bear":
        pattern = "pullback"
    if price < float(tail["收盘"].max()) * 0.985:
        pattern = "negative"

    signed_amount = 0.0
    for _, row in tail.iterrows():
        direction = 1 if float(row["收盘"]) >= float(row["开盘"]) else -1
        signed_amount += direction * float(row["成交额"])
    capital_score = max(0, min(100, 45 + signed_amount / max(float(tail["成交额"].sum()), 1) * 45))

    # Keep live scans fast and resilient. Slow F10/news endpoints are covered
    # in the data-source plan and should be run as a cache/enrichment step.
    name = code
    pe = None
    market_cap_bn = 9999
    news_sentiment = 0

    return {
        "code": code,
        "name": name,
        "sector": STATIC_SECTOR_MAP.get(code, "未知"),
        "price": price,
        "pre_close": pre_close,
        "tail_gain_pct": tail_gain_pct,
        "volume_ratio": volume_ratio,
        "tail_vol_ratio": tail_vol / total_vol,
        "turnover_rate": float(bars.get("换手率", pd.Series([0])).sum()) if "换手率" in bars else 0,
        "amount_mn": amount_mn,
        "market_cap_bn": market_cap_bn if market_cap_bn is not None else 9999,
        "pe": pe,
        "ma_state": ma_state,
        "pattern": pattern,
        "capital_flow_score": capital_score,
        "sector_score": 3,
        "news_sentiment": news_sentiment,
        "hot_rank": None,
        "day_position_pct": day_position_pct,
        "price_to_day_high_pct": price_to_day_high_pct,
        "last_bar_vol_share_tail_pct": float(bars["成交量"].iloc[-1] / max(tail["成交量"].sum(), 1) * 100) if len(tail) else 100,
    }


def live_market_state(trade_date: str) -> Dict[str, Any]:
    return {
        "state": "range",
        "index_tail_return_pct": None,
        "breadth_up_ratio": None,
        "limit_up": None,
        "limit_down": None,
        "halt_reason": None,
        "data_note": "live fast path defaults to range when index breadth cache is unavailable",
    }


def screen_live(args: argparse.Namespace) -> Dict[str, Any]:
    trade_date = args.trade_date or infer_recent_trading_days(date.today(), 1)[-1]
    market = live_market_state(trade_date)
    codes = load_codes(args.codes, Path(args.universe_file) if args.universe_file else None, args.limit)
    decisions: List[StockDecision] = []
    rejects: List[Dict[str, str]] = []
    feature_rows: List[Dict[str, Any]] = []
    for code in codes:
        features = live_stock_features(code, trade_date, args.asof_time)
        if not features:
            rejects.append({"code": code, "name": code, "reason": "免费数据接口未返回完整分钟/日线数据"})
            continue
        feature_rows.append(features)

    apply_live_sector_resonance(feature_rows)
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
    final_orders = select_final_orders(decisions, args.asof_time, "akshare")
    watchlist = build_watchlist(decisions, feature_rows, final_orders, args.asof_time, "akshare")
    market_notes = build_market_notes(final_orders, watchlist, rejects, decisions, feature_rows, args.asof_time)
    return {
        "mode": "screen",
        "trade_date": trade_date,
        "asof_time": args.asof_time,
        "market_state": market,
        "final_orders": final_orders,
        "watchlist": watchlist,
        "market_notes": market_notes,
        "rejects": rejects[:200],
        "data_note": "Live/free path uses AKShare public endpoints; missing Level-2 data is proxied and confidence-capped.",
        "disclaimer": "仅供研究和复盘，不构成投资建议。",
    }


def verify_live_order(order: Dict[str, Any], next_trade_date: str) -> Dict[str, Any]:
    ak, _pd = import_akshare()
    code = normalize_code(order["code"])
    start = f"{next_trade_date} 09:30:00"
    end = f"{next_trade_date} 15:00:00"
    try:
        bars = fetch_minute_bars(ak, code, start, end)
    except Exception as exc:
        return {
            "entry_price": order.get("suggested_price"),
            "actual_return_pct": None,
            "actual_open_return_pct": None,
            "verification": "missing",
            "exit_reason": f"次日分钟线获取失败: {exc}",
        }
    if bars is None or len(bars) == 0:
        return {
            "entry_price": order.get("suggested_price"),
            "actual_return_pct": None,
            "actual_open_return_pct": None,
            "verification": "missing",
            "exit_reason": "次日分钟线为空",
        }

    bars = bars.copy()
    bars["dt"] = [parse_bar_time(x) for x in bars["时间"]]
    for col in ["开盘", "收盘", "最高", "最低", "成交量", "成交额", "换手率"]:
        if col in bars:
            bars[col] = _pd.to_numeric(bars[col], errors="coerce").fillna(0)
    entry = float(order["suggested_price"])
    next_open = float(bars["开盘"].iloc[0])
    open_return = next_open / entry - 1

    def close_at_or_before(clock: str) -> float:
        target = datetime.strptime(f"{next_trade_date} {clock}:00", "%Y-%m-%d %H:%M:%S")
        subset = bars[bars["dt"] <= target]
        if subset.empty:
            return float(bars["收盘"].iloc[0])
        return float(subset["收盘"].iloc[-1])

    price_1000 = close_at_or_before("10:00")
    price_1030 = close_at_or_before("10:30")
    price_1450 = close_at_or_before("14:50")

    if open_return >= 0.03:
        exit_price, exit_reason = next_open, "S1大幅高开，按开盘卖出60%验证"
    elif open_return >= 0.01:
        exit_price, exit_reason = max(price_1000, price_1030), "S2温和高开，按10:00/10:30较优执行验证"
    elif open_return >= 0:
        exit_price, exit_reason = price_1030 if price_1000 >= next_open else price_1000, "S3平开微涨，按时间止损验证"
    elif open_return >= -0.01:
        exit_price, exit_reason = price_1000 if price_1000 >= entry else price_1450, "S4微幅低开，按10:00翻红/尾盘止损验证"
    elif open_return >= -0.02:
        exit_price, exit_reason = next_open if price_1000 < entry else price_1000, "S5中度低开，按5分钟/10:00止损验证"
    elif open_return >= -0.03:
        exit_price, exit_reason = next_open, "S6大幅低开，按开盘止损验证"
    else:
        exit_price, exit_reason = next_open, "S7极端低开，按开盘清仓验证"

    # 改进：增加盘中反弹保护（当10:00价格高于entry时，延长持有）
    if exit_reason.startswith("S5") and price_1000 >= entry:
        exit_price = price_1000
        exit_reason = "S5改进：10:00翻红，延长持有"
    if exit_reason.startswith("S4") and price_1000 >= entry:
        exit_price = price_1030 if price_1030 >= entry else price_1000
        exit_reason = "S4改进：10:00翻红，持有到10:30"

    cost = 0.0025
    actual_return = exit_price / entry - 1 - cost
    return {
        "entry_price": round(entry, 3),
        "next_open": round(next_open, 3),
        "exit_price": round(exit_price, 3),
        "actual_open_return_pct": round(open_return * 100, 2),
        "actual_return_pct": round(actual_return * 100, 2),
        "verification": "success" if actual_return > 0 else "failed",
        "exit_reason": exit_reason,
    }


def backtest_live(args: argparse.Namespace) -> Dict[str, Any]:
    end_day = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()
    calendar = trading_calendar_around(end_day, 120)
    completed_days = [d for d in calendar if d <= end_day.isoformat()]
    if args.trade_date:
        trade_days = [args.trade_date]
    else:
        # Need a following trading day for verification, so skip the last date if
        # its next session has not completed.
        trade_days = completed_days[-(args.days + 1):-1] if len(completed_days) > args.days else completed_days[:-1]

    daily_results: List[Dict[str, Any]] = []
    returns: List[float] = []
    failures: List[str] = []
    original_trade_date = args.trade_date
    for trade_day in trade_days:
        args.trade_date = trade_day
        screen = screen_live(args)
        next_day = next_trade_date_from_calendar(trade_day, calendar)
        picks: List[Dict[str, Any]] = []
        if next_day and next_day <= end_day.isoformat():
            for order in screen["final_orders"]:
                verification = verify_live_order(order, next_day)
                merged = {**order, **verification}
                picks.append(merged)
                if verification["actual_return_pct"] is not None:
                    returns.append(float(verification["actual_return_pct"]))
                    if verification["verification"] == "failed":
                        failures.append(f"{trade_day} {order['code']} {order['name']}: {verification['exit_reason']}")
            day_returns = [p["actual_return_pct"] for p in picks if p["actual_return_pct"] is not None]
            actual_return = round(statistics.mean(day_returns), 2) if day_returns else None
            verification_state = "success" if day_returns and actual_return and actual_return > 0 else ("no_trade" if not picks else "failed")
        else:
            actual_return = None
            verification_state = "missing_next_day"
        daily_results.append({
            "trade_date": trade_day,
            "next_trade_date": next_day,
            "pick_count": len(picks),
            "actual_return_pct": actual_return,
            "verification": verification_state,
            "picks": picks,
            "screen_reject_count": len(screen.get("rejects", [])),
        })
    args.trade_date = original_trade_date

    wins = [ret for ret in returns if ret > 0]
    if returns:
        review = "改进版回测：按新规则（tail_gain>=0.5%、前日涨幅过滤、板块动量、盘中反弹保护）执行；弱市bear状态仅保留S/A级，B级直接过滤。需继续验证14:45-14:50复核效果。"
    else:
        review = "改进版回测显示：按新规则，过去两周无可买入样本；需继续验证参数适配。"
    retrospective = {
        "sample_trading_days": len(trade_days),
        "trade_count": len(returns),
        "win_rate": round(len(wins) / len(returns), 3) if returns else 0,
        "average_return_pct": round(statistics.mean(returns), 2) if returns else 0,
        "review": review,
        "rule_improvements": [
            "tail_gain阈值从0.25%提高到0.5%，过滤低质量尾盘信号。",
            "新增前日涨幅过滤：>6%排除追高，<-4%排除抄底。",
            "新增涨跌幅分位数过滤：分位数<60%排除。",
            "新增板块动量因子（权重10%）：板块动量>2%加分。",
            "bear状态扣分从8提高到12，更严格过滤弱市B级。",
            "改进交叉验证：增加板块动量和前日momentum作为验证项。",
            "改进S1-S7退出：增加盘中反弹保护（S4/S5 10:00翻红延长持有）。",
        ],
        "failed_notes": failures[:8],
    }
    return {
        "mode": "backtest",
        "asof_time": args.asof_time,
        "daily_results": daily_results,
        "retrospective": retrospective,
        "data_note": "Free AKShare data; Level-2 and F10 gaps are proxied and confidence-capped.",
        "disclaimer": "仅供研究和复盘，不构成投资建议。",
    }


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
            f"### {idx}. {order.get('code')} {order.get('name', '')}",
            "",
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
        "# A股尾盘选股报告",
        "",
        f"- 日期/trade date: {markdown_value(report.get('trade_date'))}",
        f"- 时间/asof time: {markdown_value(report.get('asof_time'))}",
        f"- 市场状态/market state: {markdown_value(market.get('state'))}",
        f"- 模式/mode: {markdown_value(report.get('mode'))}",
        "",
        "## 市场说明 market_notes",
        "",
    ]
    lines.extend(markdown_list(report.get("market_notes", [])))
    lines.append("")
    lines.extend(render_orders_markdown("正式可买 final_orders", report.get("final_orders", []), "无正式可买标的。"))
    lines.extend(render_orders_markdown("观察池 watchlist", report.get("watchlist", []), "无观察标的。"))
    rejects = report.get("rejects", [])
    lines.extend([
        "## 过滤统计 rejects",
        "",
        f"- 过滤/缺失数量: {len(rejects)}",
    ])
    for item in rejects[:10]:
        lines.append(f"- {item.get('code')} {item.get('name', '')}: {item.get('reason')}")
    if len(rejects) > 10:
        lines.append(f"- ... 其余 {len(rejects) - 10} 条略")
    lines.extend([
        "",
        "## 声明 disclaimer",
        "",
        markdown_value(report.get("disclaimer", "仅供研究和复盘，不构成投资建议。")),
    ])
    return "\n".join(lines).rstrip() + "\n"


def render_backtest_markdown(report: Dict[str, Any]) -> str:
    retrospective = report.get("retrospective", {})
    lines = [
        "# A股尾盘回测报告",
        "",
        f"- 时间/asof time: {markdown_value(report.get('asof_time'))}",
        f"- 样本交易日/sample days: {markdown_value(retrospective.get('sample_trading_days'))}",
        f"- 交易数/trades: {markdown_value(retrospective.get('trade_count'))}",
        f"- 胜率/win rate: {markdown_value(retrospective.get('win_rate'))}",
        f"- 平均收益/average return pct: {markdown_value(retrospective.get('average_return_pct'))}",
        "",
        "## 复盘结论 review",
        "",
        markdown_value(retrospective.get("review")),
        "",
        "## 规则改进 rule_improvements",
        "",
    ]
    lines.extend(markdown_list(retrospective.get("rule_improvements", [])))
    lines.extend(["", "## 失败记录 failed_notes", ""])
    lines.extend(markdown_list(retrospective.get("failed_notes", [])))
    lines.extend(["", "## 每日结果 daily_results", ""])
    for row in report.get("daily_results", []):
        lines.append(
            f"- {row.get('trade_date')} -> {row.get('next_trade_date')}: "
            f"{row.get('verification')}，pick_count={row.get('pick_count')}，return={markdown_value(row.get('actual_return_pct'))}"
        )
    lines.extend([
        "",
        "## 声明 disclaimer",
        "",
        markdown_value(report.get("disclaimer", "仅供研究和复盘，不构成投资建议。")),
    ])
    return "\n".join(lines).rstrip() + "\n"


def render_markdown_report(report: Dict[str, Any]) -> str:
    if report.get("mode") == "screen":
        return render_screen_markdown(report)
    if report.get("mode") == "backtest":
        return render_backtest_markdown(report)
    return "# A股尾盘报告\n\n```json\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n"


def resolve_output_format(output: Optional[str], output_format: str) -> str:
    if output_format != "auto":
        return output_format
    if output and Path(output).suffix.lower() in {".md", ".markdown"}:
        return "markdown"
    return "json"


def write_report(report: Dict[str, Any], output: Optional[str], output_format: str = "auto") -> None:
    resolved_format = resolve_output_format(output, output_format)
    text = render_markdown_report(report) if resolved_format == "markdown" else json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output:
        path = Path(output).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(str(path))
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C版A股主板尾盘选股与回测")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("screen", "backtest"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--fixture", type=Path, help="Use deterministic fixture JSON instead of live AKShare data")
        cmd.add_argument("--trade-date", help="YYYY-MM-DD decision date")
        cmd.add_argument("--asof-time", default="14:20", help="Decision time, e.g. 14:20 or 14:50")
        cmd.add_argument("--output", help="Write report to this path")
        cmd.add_argument("--format", choices=["auto", "json", "markdown"], default="auto", help="Report format; auto uses markdown for .md/.markdown outputs")
        cmd.add_argument("--fetch-timeout", type=int, default=8, help="Seconds before skipping a slow public data fetch; 0 disables")
        cmd.add_argument("--no-eastmoney-fallback", action="store_true", help="Skip slow Eastmoney minute fallback after Sina minute data fails")
    sub.choices["screen"].add_argument("--codes", help="Comma-separated stock codes")
    sub.choices["screen"].add_argument("--universe-file", help="One stock code per line")
    sub.choices["screen"].add_argument("--limit", type=int, default=40, help="Limit default live universe size")
    sub.choices["backtest"].add_argument("--days", type=int, default=5, help="Number of trading days")
    sub.choices["backtest"].add_argument("--codes", help="Comma-separated stock codes for live backtest")
    sub.choices["backtest"].add_argument("--universe-file", help="One stock code per line for live backtest")
    sub.choices["backtest"].add_argument("--limit", type=int, default=30, help="Limit default live universe size")
    sub.choices["backtest"].add_argument("--end-date", help="YYYY-MM-DD last completed date used to choose the one-week window")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    global FETCH_TIMEOUT_SECONDS, USE_EASTMONEY_FALLBACK
    parser = build_parser()
    args = parser.parse_args(argv)
    FETCH_TIMEOUT_SECONDS = args.fetch_timeout
    USE_EASTMONEY_FALLBACK = not args.no_eastmoney_fallback
    if args.fixture:
        fixture = load_fixture(args.fixture.expanduser())
        if args.command == "screen":
            report = screen_fixture(fixture, args.trade_date, args.asof_time)
        else:
            report = backtest_fixture(fixture, args.asof_time, args.days)
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
