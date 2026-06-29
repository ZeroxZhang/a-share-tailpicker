#!/usr/bin/env python3
"""Build fixture for tailpicker using stock_finance_data API."""
import csv
import io
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path('/Users/zerox/.agents/skills/a-share-tailpicker/scripts')))
import agent_gw
from tailpicker import DEFAULT_UNIVERSE, STATIC_SECTOR_MAP

DATA_DIR = Path('/Users/zerox/.agents/skills/a-share-tailpicker/data_cache')
DATA_DIR.mkdir(parents=True, exist_ok=True)

def call_api(data_source_name, api_name, params):
    client = agent_gw.AgentGwClient()
    resp = client.tools.call_data_source_tool({
        'data_source_name': data_source_name,
        'api_name': api_name,
        'params': params
    })
    return resp

def parse_csv_from_response(resp):
    if not resp or not resp.is_success:
        print(f'API failed: {resp.text[:200] if resp.text else "empty"}')
        return None
    try:
        data = json.loads(resp.text)
        preview = data.get('data_preview', '')
        if not preview:
            print('No data_preview in response')
            return None
        reader = csv.DictReader(io.StringIO(preview))
        return list(reader)
    except Exception as e:
        print(f'Parse error: {e}')
        return None

def get_price_batch(codes, start_date, end_date):
    tickers = ','.join([f'{c}.SH' for c in codes])
    resp = call_api('stock_finance_data', 'stock_finance_data_get_price', {
        'ticker': tickers,
        'start_date': start_date,
        'end_date': end_date,
        'interval': 'D',
        'file_path': str(DATA_DIR / 'dummy.csv'),
        'format': 'json'
    })
    return parse_csv_from_response(resp)

def get_index_data(index_code, end_date):
    resp = call_api('stock_finance_data', 'stock_finance_data_get_stock_realtime_price', {
        'ticker': f'{index_code}.SH',
        'type': 'close_summary',
        'time': f'{end_date} 15:00:00',
        'file_path': str(DATA_DIR / 'dummy_index.csv'),
        'format': 'json'
    })
    rows = parse_csv_from_response(resp)
    return rows[0] if rows else None

def main():
    trade_date = sys.argv[1] if len(sys.argv) > 1 else '2026-06-26'
    
    # Calculate previous dates
    dt = datetime.strptime(trade_date, '%Y-%m-%d')
    prev_dt = dt - timedelta(days=1)
    # Skip weekends
    while prev_dt.weekday() >= 5:
        prev_dt -= timedelta(days=1)
    prev_date = prev_dt.strftime('%Y-%m-%d')
    prev_prev_dt = prev_dt - timedelta(days=1)
    while prev_prev_dt.weekday() >= 5:
        prev_prev_dt -= timedelta(days=1)
    prev_prev_date = prev_prev_dt.strftime('%Y-%m-%d')
    
    print(f'Building fixture for {trade_date} (prev={prev_date}, prev_prev={prev_prev_date})')
    print(f'Fetching daily data for {len(DEFAULT_UNIVERSE)} stocks...')
    
    all_price_data = {}
    batch_size = 3
    total_batches = (len(DEFAULT_UNIVERSE) + batch_size - 1) // batch_size
    for i in range(0, len(DEFAULT_UNIVERSE), batch_size):
        batch = DEFAULT_UNIVERSE[i:i+batch_size]
        print(f'  Batch {i//batch_size + 1}/{total_batches}: {batch[:3]}...')
        rows = get_price_batch(batch, prev_prev_date, trade_date)
        if rows:
            print(f'    Got {len(rows)} rows')
            for row in rows:
                code = row.get('thscode', '').replace('.SH', '')
                if code and code not in all_price_data:
                    all_price_data[code] = []
                if code:
                    all_price_data[code].append(row)
        else:
            print(f'    No data returned')
    
    print(f'Fetched data for {len(all_price_data)} stocks')
    
    # Index data
    print('Fetching index data...')
    index_data = get_index_data('000001', trade_date)
    if index_data:
        print(f'  Index: close={index_data.get("close")}, chg={index_data.get("chg")}%')
    
    # Build fixture stocks
    print('Building fixture stocks...')
    stocks = []
    for code in DEFAULT_UNIVERSE:
        rows = all_price_data.get(code, [])
        if len(rows) < 2:
            continue
        
        rows_sorted = sorted(rows, key=lambda r: r.get('time', ''))
        prev_row = rows_sorted[-2] if len(rows_sorted) >= 2 else None
        curr_row = rows_sorted[-1] if rows_sorted else None
        
        if not curr_row or not prev_row:
            continue
        
        try:
            pre_close = float(prev_row.get('close', 0))
            close = float(curr_row.get('close', 0))
            high = float(curr_row.get('high', 0))
            low = float(curr_row.get('low', 0))
            volume = float(curr_row.get('volume', 0))
            turn = float(curr_row.get('turn', 0)) if 'turn' in curr_row else 0
        except (ValueError, TypeError) as e:
            print(f'  Skip {code}: parse error {e}')
            continue
        
        if pre_close <= 0 or close <= 0:
            continue
        
        day_ret = close / pre_close - 1
        
        if high > low:
            day_pos = (close - low) / (high - low) * 100
        else:
            day_pos = 50
        
        price_to_high = (close / high - 1) * 100 if high > 0 else 0
        volume_ratio = max(0.5, turn * 10) if turn > 0 else 1.0
        tail_gain = day_ret * 100
        
        sector = STATIC_SECTOR_MAP.get(code, '未知')
        
        stock = {
            'code': code,
            'name': curr_row.get('thsname_cn', code),
            'sector': sector,
            'price': round(close, 3),
            'pre_close': round(pre_close, 3),
            'tail_gain_pct': round(tail_gain, 2),
            'volume_ratio': round(volume_ratio, 2),
            'tail_vol_ratio': 0.25,
            'turnover_rate': round(turn, 2),
            'amount_mn': 500,
            'market_cap_bn': 500,
            'pe': None,
            'ma_state': 'bull' if day_ret > 0 else 'range',
            'pattern': 'breakout' if day_ret > 0.01 else 'pullback' if day_ret > 0 else 'none',
            'capital_flow_score': 50,
            'sector_score': 10,
            'news_sentiment': 0,
            'hot_rank': None,
            'day_position_pct': round(day_pos, 1),
            'price_to_day_high_pct': round(price_to_high, 2),
            'last_bar_vol_share_tail_pct': 15,
            'next_open': round(close * (1 + (day_ret * 0.3)), 3),
        }
        stocks.append(stock)
    
    # Market data
    market = {
        'state': 'range',
        'index_tail_return_pct': float(index_data.get('pct_chg', 0)) if index_data else 0,
        'breadth_up_ratio': 0.5,
        'limit_up': 50,
        'limit_down': 30,
    }
    
    fixture = {
        'asof_time': '14:40',
        'trade_dates': [trade_date],
        'days': [
            {
                'trade_date': trade_date,
                'next_trade_date': trade_date,
                'market': market,
                'stocks': stocks,
            }
        ]
    }
    
    fixture_path = DATA_DIR / f'fixture_{trade_date}_1440.json'
    with open(fixture_path, 'w', encoding='utf-8') as f:
        json.dump(fixture, f, ensure_ascii=False, indent=2)
    
    print(f'\nFixture saved to {fixture_path}')
    print(f'  Stocks: {len(stocks)}')
    
    return fixture_path

if __name__ == '__main__':
    path = main()
    print(path)
