import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import json

cal = ak.tool_trade_date_hist_sina()
print("Calendar loaded, shape:", cal.shape)
print(cal.head(10))

today = datetime(2026, 6, 16)
start = today - timedelta(days=100)

dates = [pd.to_datetime(x).strftime("%Y-%m-%d") for x in cal["trade_date"].tolist()]
trade_dates = [d for d in dates if start.strftime("%Y-%m-%d") <= d <= today.strftime("%Y-%m-%d")]
print(f"\nTrading dates from {start.date()} to {today.date()}: {len(trade_dates)}")
print(trade_dates[:5], "...", trade_dates[-5:])

# Save to JSON
output = {"start_date": start.strftime("%Y-%m-%d"), "end_date": today.strftime("%Y-%m-%d"), "trade_dates": trade_dates}
with open("/Users/zerox/.agents/skills/a-share-tailpicker/data_cache/trade_dates.json", "w") as f:
    json.dump(output, f, indent=2)
print("Saved to data_cache/trade_dates.json")
