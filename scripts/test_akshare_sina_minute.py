import akshare as ak
import time

# Test Sina minute interface
for symbol in ["sh600519", "sh600036"]:
    for date in ["2026-05-25", "2026-06-15"]:
        try:
            # stock_zh_a_minute uses Sina data
            df = ak.stock_zh_a_minute(symbol=symbol, period="5", adjust="qfq")
            print(f"{symbol} {date}: Got {len(df)} records total")
            # Filter for the specific date
            date_str = date.replace("-", "")
            day_df = df[df["时间"].str.startswith(date_str)]
            print(f"  Filtered for {date}: {len(day_df)} records")
            if not day_df.empty:
                print(day_df.tail(3))
            time.sleep(1)
        except Exception as e:
            print(f"{symbol} {date}: Error - {e}")
