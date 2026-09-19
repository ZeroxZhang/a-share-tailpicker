import agent_gw
from io import StringIO
import pandas as pd

client = agent_gw.AgentGwClient()

for date in ["2026-05-25", "2026-05-26", "2026-05-27", "2026-06-01", "2026-06-15"]:
    result = client.tools.call_data_source_tool({
        "data_source_name": "stock_finance_data",
        "api_name": "stock_finance_data_get_stock_realtime_price",
        "params": {
            "ticker": "600519.SH",
            "time": f"{date} 14:30:00",
            "type": "realtime_price",
            "file_path": "/Users/zerox/.agents/skills/a-share-tailpicker/data_cache/dummy.csv"
        }
    })
    print(f"{date}: success={result.is_success}, error={result.error}")
    if result.is_success and result.json():
        data = result.json()
        if "data_preview" in data:
            print(f"  data: {data['data_preview'][:100]}")
