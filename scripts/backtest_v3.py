#!/usr/bin/env python3
"""v3.0 Backtest Engine - Quick test with 10 stocks, 30 days."""

from __future__ import annotations

import json
import statistics
import time
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd
import agent_gw

DATA_DIR = Path("/Users/zerox/.agents/skills/a-share-tailpicker/data_cache")
REPORTS_DIR = Path("/Users/zerox/.agents/skills/a-share-tailpicker/reports")
TRADE_DATES_FILE = DATA_DIR / "trade_dates.json"

DEFAULT_UNIVERSE = [
    "600000", "600009", "600010", "600011", "600015",
    "600016", "600018", "600019", "600025", "600028",
]

class DataFetcher:
    def __init__(self):
        self.client = agent_gw.AgentGwClient()
        self._call_count = 0
        self._success_count = 0

    def _call(self, api_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._call_count += 1
        try:
            result = self.client.tools.call_data_source_tool({
                "data_source_name": "stock_finance_data",
                "api_name": api_name,
                "params": params,
            })
            if not result.is_success:
                return {"error": result.error}
            data = result.json()
            if data and "data_preview" in data:
                self._success_count += 1
                return {"data": data["data_preview"]}
            return data or {}
        except Exception as e:
            return {"error": str(e)}

    def get_close_summary(self, codes: List[str], trade_date: str) -> pd.DataFrame:
        tickers = ",".join([f"{c}.SH" if c.startswith('6') else f"{c}.SZ" for c in codes])
        result = self._call("stock_finance_data_get_stock_realtime_price", {
            "ticker": tickers, "time": f"{trade_date} 15:00:00",
            "type": "close_summary", "file_path": str(DATA_DIR / "dummy.csv"),
        })
        if "error" in result or "data" not in result:
            return pd.DataFrame()
        try:
            df = pd.read_csv(StringIO(result["data"]))
            df.columns = [c.strip().lower() for c in df.columns]
            df["code"] = df["thscode"].str.replace(".SH", "").str.replace(".SZ", "").str.replace(".BJ", "")
            df["trade_date"] = trade_date
            return df
        except Exception:
            return pd.DataFrame()

def hard_filter(row: pd.Series) -> Optional[str]:
    code = str(row.get("code", ""))
    if not code.startswith("60"): return "非沪主板60系列"
    pre_close = float(row.get("pre_close", 0))
    close_price = float(row.get("close", 0))
    if pre_close <= 0 or close_price <= 0: return "价格数据缺失"
    day_ret = (close_price / pre_close - 1) if pre_close > 0 else 0
    if day_ret <= -0.07: return "当日跌幅过大"
    if day_ret >= 0.095: return "接近涨停，追高风险"
    turn = float(row.get("turn", 0))
    if turn > 20: return "换手率极端"
    amount = float(row.get("amt", 0))
    if amount > 0 and amount / 1e6 < 300: return "成交额不足300万"
    prev_ret = float(row.get("prev_return", 0))
    if prev_ret > 0.05: return "前日涨幅>5%"
    if prev_ret < -0.05: return "前日跌幅>5%"
    return None

def score_stock(row: pd.Series, market_state: str) -> Optional[float]:
    reject = hard_filter(row)
    if reject: return None
    close_price = float(row.get("close", 0))
    pre_close = float(row.get("pre_close", 0))
    day_high = float(row.get("high", 0))
    day_low = float(row.get("low", 0))
    day_open = float(row.get("open", 0))
    day_ret = (close_price / pre_close - 1) if pre_close > 0 else 0
    tail_gain_pct = (close_price / day_open - 1) * 100 if day_open > 0 else 0
    day_position_pct = (close_price - day_low) / (day_high - day_low) * 100 if day_high > day_low else 50
    price_to_day_high_pct = (close_price / day_high - 1) * 100 if day_high > 0 else 0
    turn = float(row.get("turn", 0))
    volume_ratio = max(1.2, min(turn / 1.5, 5.0))
    capital_flow_score = max(30, min(100, 60 + day_ret * 500))
    
    if not (0.5 <= tail_gain_pct <= 2.5): return None
    if not (1.2 <= volume_ratio <= 4.0): return None
    if day_position_pct > 85: return None
    if price_to_day_high_pct > -0.5: return None
    if capital_flow_score < 55: return None
    if not (0.01 <= day_ret <= 0.07): return None
    if market_state == "halt": return None
    
    score = 0.0
    tg = tail_gain_pct
    if 0.8 <= tg <= 1.5: score += 20
    elif 0.5 <= tg < 0.8 or 1.5 < tg <= 2.0: score += 15
    else: score += 10
    vr = volume_ratio
    if 1.5 <= vr <= 2.5: score += 15
    elif 1.2 <= vr < 1.5 or 2.5 < vr <= 3.5: score += 10
    else: score += 8
    score += min(20, capital_flow_score * 0.25)
    score += 7.5  # sector_score * 0.75 with sector_score=10
    if day_position_pct >= 60 and day_ret > 0: score += 10
    elif day_position_pct >= 40 and day_ret > 0: score += 6
    else: score += 2
    dp = day_position_pct
    if dp <= 60: score += 10
    elif dp <= 75: score += 7
    elif dp <= 85: score += 4
    else: score += 1
    if market_state == "bear": score -= 5
    elif market_state == "bull": score += 3
    pr = float(row.get("prev_return", 0))
    if 0 <= pr <= 0.03: score += 3
    elif pr > 0.05: score -= 3
    return score

def grade_from_score(score: float) -> str:
    if score >= 80: return "S"
    if score >= 70: return "A"
    if score >= 60: return "B"
    return "C"

def run_backtest(stock_codes, trade_dates):
    fetcher = DataFetcher()
    print(f"Fetching {len(stock_codes)} stocks x {len(trade_dates)} dates...")
    all_data = pd.DataFrame()
    for trade_date in trade_dates:
        for j in range(0, len(stock_codes), 3):
            batch = stock_codes[j:j+3]
            df = fetcher.get_close_summary(batch, trade_date)
            if not df.empty:
                all_data = pd.concat([all_data, df], ignore_index=True)
            # No sleep for faster execution
    print(f"API calls: {fetcher._call_count}, success: {fetcher._success_count}, records: {len(all_data)}")
    if all_data.empty:
        return [], pd.DataFrame()
    
    all_data = all_data.sort_values(["code", "trade_date"]).reset_index(drop=True)
    all_data["prev_return"] = all_data.groupby("code")["close"].pct_change(1).shift(1)
    all_data["day_return"] = (all_data["close"] / all_data["pre_close"] - 1).fillna(0)
    
    daily_results = []
    all_picks = []
    for i, trade_date in enumerate(trade_dates):
        day_df = all_data[all_data["trade_date"] == trade_date].copy()
        if day_df.empty:
            continue
        pre_screen = day_df[(day_df["day_return"] >= 0.01) & (day_df["day_return"] <= 0.07)].copy()
        if pre_screen.empty:
            daily_results.append({"trade_date": trade_date, "pick_count": 0, "picks": []})
            continue
        
        pre_screen["tail_gain_pct"] = (pre_screen["close"] / pre_screen["open"] - 1) * 100
        pre_screen["day_position_pct"] = (pre_screen["close"] - pre_screen["low"]) / (pre_screen["high"] - pre_screen["low"]) * 100
        pre_screen["price_to_day_high_pct"] = (pre_screen["close"] / pre_screen["high"] - 1) * 100
        pre_screen["volume_ratio"] = pre_screen["turn"] / 1.5
        pre_screen["capital_flow_score"] = (60 + pre_screen["day_return"] * 500).clip(30, 100)
        
        candidates = []
        for _, row in pre_screen.iterrows():
            score = score_stock(row, "range")
            if score is not None:
                candidates.append((row, score, grade_from_score(score)))
        candidates.sort(key=lambda x: x[1], reverse=True)
        candidates = candidates[:5]
        
        next_date = trade_dates[i+1] if i+1 < len(trade_dates) else None
        picks = []
        if next_date and candidates:
            next_day_df = all_data[all_data["trade_date"] == next_date].copy()
            if not next_day_df.empty:
                for row, score, grade in candidates:
                    code = row["code"]
                    entry = row["close"]
                    nr = next_day_df[next_day_df["code"] == code]
                    if nr.empty or entry <= 0: continue
                    next_open = nr.iloc[0]["open"]
                    next_high = nr.iloc[0]["high"]
                    next_low = nr.iloc[0]["low"]
                    next_close = nr.iloc[0]["close"]
                    next_vwap = nr.iloc[0].get("vwap", next_close)
                    cost = 0.0025
                    picks.append({
                        "trade_date": trade_date, "next_date": next_date, "code": code,
                        "grade": grade, "score": round(score, 1), "entry_price": round(entry, 3),
                        "next_open": round(next_open, 3), "next_high": round(next_high, 3),
                        "next_low": round(next_low, 3), "next_vwap": round(next_vwap, 3) if next_vwap > 0 else None,
                        "next_close": round(next_close, 3),
                        "ret_high_pct": round((next_high - entry) / entry * 100, 2),
                        "ret_low_pct": round((next_low - entry) / entry * 100, 2),
                        "ret_vwap_pct": round((next_vwap - entry) / entry * 100, 2) if next_vwap > 0 else 0,
                        "ret_open_pct": round((next_open - entry) / entry * 100, 2),
                        "ret_close_pct": round((next_close - entry) / entry * 100, 2),
                        "ret_open_net_pct": round(((next_open - entry) / entry - cost) * 100, 2),
                        "ret_vwap_net_pct": round(((next_vwap - entry) / entry - cost) * 100, 2) if next_vwap > 0 else 0,
                        "win_high": bool(next_high > entry), "win_open": bool(next_open > entry),
                        "win_vwap": bool(next_vwap > entry) if next_vwap > 0 else False, "win_close": bool(next_close > entry),
                    })
                    all_picks.append(picks[-1])
        
        if picks:
            total = len(picks)
            result = {"trade_date": trade_date, "pick_count": total,
                      "win_rate_high": sum(1 for p in picks if p["win_high"]) / total,
                      "win_rate_open": sum(1 for p in picks if p["win_open"]) / total,
                      "win_rate_vwap": sum(1 for p in picks if p["win_vwap"]) / total,
                      "win_rate_close": sum(1 for p in picks if p["win_close"]) / total,
                      "avg_return_high": statistics.mean([p["ret_high_pct"] for p in picks]),
                      "avg_return_low": statistics.mean([p["ret_low_pct"] for p in picks]),
                      "avg_return_vwap": statistics.mean([p["ret_vwap_pct"] for p in picks]),
                      "avg_return_open": statistics.mean([p["ret_open_pct"] for p in picks]),
                      "avg_return_close": statistics.mean([p["ret_close_pct"] for p in picks]),
                      "avg_return_open_net": statistics.mean([p["ret_open_net_pct"] for p in picks]),
                      "avg_return_vwap_net": statistics.mean([p["ret_vwap_net_pct"] for p in picks]),
                      "picks": picks}
        else:
            result = {"trade_date": trade_date, "pick_count": 0, "win_rate_high": 0, "win_rate_open": 0,
                      "win_rate_vwap": 0, "win_rate_close": 0, "avg_return_high": 0, "avg_return_low": 0,
                      "avg_return_vwap": 0, "avg_return_open": 0, "avg_return_close": 0,
                      "avg_return_open_net": 0, "avg_return_vwap_net": 0, "picks": []}
        daily_results.append(result)
    
    picks_df = pd.DataFrame(all_picks) if all_picks else pd.DataFrame()
    return daily_results, picks_df

def print_summary(daily_results, picks_df):
    if picks_df.empty:
        print("\nNo trades generated.")
        return
    total = len(picks_df)
    print("\n" + "=" * 70)
    print("BACKTEST SUMMARY (v3.0)")
    print("=" * 70)
    print(f"Total trades:          {total}")
    print(f"Win rate (high):       {picks_df['win_high'].sum() / total:.1%}  (target: 70%)")
    print(f"Win rate (open):       {picks_df['win_open'].sum() / total:.1%}")
    print(f"Win rate (vwap):       {picks_df['win_vwap'].sum() / total:.1%}")
    print(f"Win rate (close):      {picks_df['win_close'].sum() / total:.1%}")
    print(f"Avg return (high):     {picks_df['ret_high_pct'].mean():.2f}%")
    print(f"Avg return (low):      {picks_df['ret_low_pct'].mean():.2f}%")
    print(f"Avg return (vwap):     {picks_df['ret_vwap_pct'].mean():.2f}%")
    print(f"Avg return (open):     {picks_df['ret_open_pct'].mean():.2f}%")
    print(f"Avg return (close):    {picks_df['ret_close_pct'].mean():.2f}%")
    print(f"Avg return (open net): {picks_df['ret_open_net_pct'].mean():.2f}%")
    print(f"Avg return (vwap net): {picks_df['ret_vwap_net_pct'].mean():.2f}%")
    print(f"Trading days with picks: {sum(1 for r in daily_results if r['pick_count'] > 0)}/{len(daily_results)}")
    print("=" * 70)
    print("\nSample trades:")
    for _, p in picks_df.head(20).iterrows():
        print(f"  {p['trade_date']} {p['code']} {p['grade']} score={p['score']}: "
              f"high={p['ret_high_pct']:+.2f}% open={p['ret_open_pct']:+.2f}% "
              f"vwap={p['ret_vwap_pct']:+.2f}% close={p['ret_close_pct']:+.2f}%")

def save_results(daily_results, picks_df):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORTS_DIR / "backtest_v3_daily_results.json", "w", encoding="utf-8") as f:
        json.dump(daily_results, f, ensure_ascii=False, indent=2)
    if not picks_df.empty:
        picks_df.to_csv(REPORTS_DIR / "backtest_v3_all_picks.csv", index=False, encoding="utf-8")
    print(f"\nResults saved to {REPORTS_DIR}")

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRADE_DATES_FILE, "r", encoding="utf-8") as f:
        cal_data = json.load(f)
    trade_dates = cal_data["trade_dates"][-30:]  # Last 30 days only
    print(f"Loaded {len(trade_dates)} trading dates: {trade_dates[0]} to {trade_dates[-1]}")
    
    daily_results, picks_df = run_backtest(DEFAULT_UNIVERSE, trade_dates)
    print_summary(daily_results, picks_df)
    save_results(daily_results, picks_df)

if __name__ == "__main__":
    main()
