#!/usr/bin/env python3
"""Quick test with small sample."""

from __future__ import annotations

import json
import time
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import agent_gw

DATA_DIR = Path("/Users/zerox/.agents/skills/a-share-tailpicker/data_cache")
TRADE_DATES_FILE = DATA_DIR / "trade_dates.json"

# Quick test: 3 stocks (max for realtime_price), 10 days
QUICK_CODES = ["600519", "600036", "600030"]

class DataFetcher:
    def __init__(self):
        self.client = agent_gw.AgentGwClient()
        self._call_count = 0

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
                return {"data": data["data_preview"]}
            return data or {}
        except Exception as e:
            return {"error": str(e)}

    def get_daily_bars(self, codes: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        tickers = ",".join([f"{c}.SH" if c.startswith('6') else f"{c}.SZ" for c in codes])
        result = self._call("stock_finance_data_get_price", {
            "ticker": tickers,
            "start_date": start_date,
            "end_date": end_date,
            "interval": "D",
            "file_path": str(DATA_DIR / "dummy.csv"),  # required but may not be created
        })
        if "error" in result or "data" not in result:
            print(f"  Error: {result.get('error', 'no data')}")
            return pd.DataFrame()
        try:
            df = pd.read_csv(StringIO(result["data"]))
            df.columns = [c.strip().lower() for c in df.columns]
            df["code"] = df["thscode"].str.replace(".SH", "").str.replace(".SZ", "").str.replace(".BJ", "")
            df["time"] = pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d")
            return df
        except Exception as e:
            print(f"  Parse error: {e}")
            return pd.DataFrame()

    def get_intraday_1430(self, codes: List[str], trade_date: str) -> pd.DataFrame:
        tickers = ",".join([f"{c}.SH" if c.startswith('6') else f"{c}.SZ" for c in codes])
        result = self._call("stock_finance_data_get_stock_realtime_price", {
            "ticker": tickers,
            "time": f"{trade_date} 14:30:00",
            "type": "realtime_price",
            "file_path": str(DATA_DIR / "dummy.csv"),
        })
        if "error" in result or "data" not in result:
            print(f"  Error: {result.get('error', 'no data')}")
            return pd.DataFrame()
        try:
            df = pd.read_csv(StringIO(result["data"]))
            df.columns = [c.strip().lower() for c in df.columns]
            df["code"] = df["ts_code"].str.replace(".SH", "").str.replace(".SZ", "").str.replace(".BJ", "")
            return df
        except Exception as e:
            print(f"  Parse error: {e}")
            return pd.DataFrame()

def main():
    with open(TRADE_DATES_FILE, "r") as f:
        cal_data = json.load(f)
    trade_dates = cal_data["trade_dates"][:10]  # 10 days
    print(f"Testing {len(trade_dates)} dates: {trade_dates[0]} to {trade_dates[-1]}")
    
    fetcher = DataFetcher()
    
    # Step 1: Get daily bars
    print("\nStep 1: Getting daily bars...")
    df = fetcher.get_daily_bars(QUICK_CODES, trade_dates[0], trade_dates[-1])
    print(f"Got {len(df)} records")
    if not df.empty:
        print(df[["code", "time", "open", "high", "low", "close"]].head())
    
    # Step 2: Get 14:30 data for each day
    print("\nStep 2: Getting 14:30 data...")
    for trade_date in trade_dates:
        idf = fetcher.get_intraday_1430(QUICK_CODES, trade_date)
        print(f"  {trade_date}: {len(idf)} records")
        if not idf.empty:
            print(idf[["code", "close", "high", "low"]].to_string(index=False))
        time.sleep(0.5)
    
    print(f"\nTotal API calls: {fetcher._call_count}")

if __name__ == "__main__":
    main()
