import agent_gw
import pandas as pd
from io import StringIO

client = agent_gw.AgentGwClient()

for date in ["2026-03-09", "2026-03-20", "2026-04-01", "2026-04-15", "2026-05-01", "2026-05-15", "2026-06-01", "2026-06-15"]:
    result = client.tools.call_data_source_tool({
        "data_source_name": "stock_finance_data",
        "api_name": "stock_finance_data_get_stock_realtime_price",
        "params": {
            "ticker": "600519.SH",
            "time": f"{date} 15:00:00",
            "type": "close_summary",
            "file_path": "/Users/zerox/.agents/skills/a-share-tailpicker/data_cache/dummy.csv"
        }
    })
    print(f"{date}: success={result.is_success}")
    if result.is_success and result.json():
        data = result.json()
        if "data_preview" in data:
            print(f"  data: {data['data_preview'][:80]}")
