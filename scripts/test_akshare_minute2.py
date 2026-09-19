import akshare as ak
import time

# Try with different parameters
for symbol in ["600519", "000001"]:
    for date in ["2026-06-15", "2026-06-12"]:
        try:
            df = ak.stock_zh_a_hist_min_em(symbol=symbol, start_date=f"{date} 14:20", end_date=f"{date} 14:30", period="5")
            print(f"{symbol} {date}: {len(df)} records")
            if not df.empty:
                print(df.tail(2))
            time.sleep(1)
        except Exception as e:
            print(f"{symbol} {date}: Error - {e}")
