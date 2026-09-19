import agent_gw
import json

# Try to get data source desc
try:
    result = agent_gw.call_data_source_tool({
        "name": "stock_finance_data"
    })
    print("Success! Got data source desc")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:500])
except Exception as e:
    print(f"Error: {e}")
    print(f"Type: {type(e)}")
