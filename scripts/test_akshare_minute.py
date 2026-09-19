import akshare as ak
from datetime import datetime

# Test getting historical minute data for 600519 on 2026-05-25
print("Testing AKShare minute data...")
try:
    # stock_zh_a_hist_min_em can get historical minute data
    df = ak.stock_zh_a_hist_min_em(symbol="600519", start_date="2026-05-25 14:00", end_date="2026-05-25 14:30", period="5")
    print(f"Got {len(df)} records")
    print(df.head())
except Exception as e:
    print(f"Error: {e}")

# Also try 1-minute data
try:
    df = ak.stock_zh_a_hist_min_em(symbol="600519", start_date="2026-05-25 14:25", end_date="2026-05-25 14:30", period="1")
    print(f"\n1-minute data: {len(df)} records")
    print(df.head())
except Exception as e:
    print(f"Error: {e}")
