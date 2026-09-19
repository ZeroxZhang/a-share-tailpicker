#!/usr/bin/env python3
"""使用 iFinD (stock_finance_data) 数据执行今日尾盘选股。"""
import csv
import io
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path('/Users/zerox/.agents/skills/a-share-tailpicker/scripts')))
import agent_gw
from tailpicker import DEFAULT_UNIVERSE, STATIC_SECTOR_MAP, EXCLUDED_SECTORS

DATA_DIR = Path('/Users/zerox/.agents/skills/a-share-tailpicker/data_cache')
REPORTS_DIR = Path('/Users/zerox/.agents/skills/a-share-tailpicker/reports')

def call_api(data_source_name, api_name, params):
    client = agent_gw.AgentGwClient()
    resp = client.tools.call_data_source_tool({
        'data_source_name': data_source_name,
        'api_name': api_name,
        'params': params
    })
    return resp

def parse_csv_from_response(resp):
    """Parse CSV data from API response text."""
    if not resp or not resp.is_success:
        return None
    try:
        data = json.loads(resp.text)
        preview = data.get('data_preview', '')
        if not preview:
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

def get_close_summary_batch(codes, trade_date):
    tickers = ','.join([f'{c}.SH' for c in codes])
    resp = call_api('stock_finance_data', 'stock_finance_data_get_stock_realtime_price', {
        'ticker': tickers,
        'type': 'close_summary',
        'time': f'{trade_date} 15:00:00',
        'file_path': str(DATA_DIR / 'dummy_summary.csv'),
        'format': 'json'
    })
    return parse_csv_from_response(resp)

def get_realtime_price_batch(codes, trade_date, time_str):
    tickers = ','.join([f'{c}.SH' for c in codes])
    resp = call_api('stock_finance_data', 'stock_finance_data_get_stock_realtime_price', {
        'ticker': tickers,
        'type': 'realtime_price',
        'time': f'{trade_date} {time_str}',
        'file_path': str(DATA_DIR / 'dummy_rt.csv'),
        'format': 'json'
    })
    return parse_csv_from_response(resp)

def get_index_price(index_code, start_date, end_date):
    resp = call_api('stock_finance_data', 'stock_finance_data_get_price', {
        'ticker': f'{index_code}.SH',
        'start_date': start_date,
        'end_date': end_date,
        'interval': 'D',
        'file_path': str(DATA_DIR / 'dummy_index.csv'),
        'format': 'json'
    })
    return parse_csv_from_response(resp)

def main():
    import sys
    trade_date = sys.argv[1] if len(sys.argv) > 1 else '2026-06-17'
    
    from datetime import datetime, timedelta
    dt = datetime.strptime(trade_date, '%Y-%m-%d')
    prev_dt = dt - timedelta(days=1)
    while prev_dt.weekday() >= 5:
        prev_dt -= timedelta(days=1)
    prev_date = prev_dt.strftime('%Y-%m-%d')
    
    prev_prev_dt = prev_dt - timedelta(days=1)
    while prev_prev_dt.weekday() >= 5:
        prev_prev_dt -= timedelta(days=1)
    prev_prev_date = prev_prev_dt.strftime('%Y-%m-%d')
    
    start_dt = prev_prev_dt - timedelta(days=7)
    while start_dt.weekday() >= 5:
        start_dt -= timedelta(days=1)
    start_date = start_dt.strftime('%Y-%m-%d')
    
    print(f'=== 使用 iFinD 数据执行 {trade_date} 14:40 尾盘选股 ===')
    
    # 1. 获取所有 100 只股票的 10 天日线数据
    print(f'\n[1/5] 获取 {len(DEFAULT_UNIVERSE)} 只股票的 10 天日线数据...')
    all_price_data = {}
    batch_size = 3
    for i in range(0, len(DEFAULT_UNIVERSE), batch_size):
        batch = DEFAULT_UNIVERSE[i:i+batch_size]
        rows = get_price_batch(batch, start_date, trade_date)
        if rows:
            for row in rows:
                code = row.get('thscode', '').replace('.SH', '')
                if code not in all_price_data:
                    all_price_data[code] = []
                all_price_data[code].append(row)
        print(f'  批次 {i//batch_size + 1}/{(len(DEFAULT_UNIVERSE)-1)//batch_size + 1} 完成')
    
    print(f'  获取了 {len(all_price_data)} 只股票的数据')
    
    # 2. 计算各只股票的当日涨幅和量比，筛选出硬过滤候选
    print(f'\n[2/5] 计算涨幅和量比...')
    candidates = []
    for code in DEFAULT_UNIVERSE:
        rows = all_price_data.get(code, [])
        if len(rows) < 2:
            continue
        
        rows_sorted = sorted(rows, key=lambda r: r.get('time', ''))
        
        # 获取最近 3 天
        if len(rows_sorted) < 3:
            continue
        
        curr_row = rows_sorted[-1]
        prev_row = rows_sorted[-2]
        prev_prev_row = rows_sorted[-3]
        
        pre_close = float(prev_row.get('close', 0))
        close = float(curr_row.get('close', 0))
        prev_close = float(prev_prev_row.get('close', 0))
        
        if pre_close <= 0 or close <= 0:
            continue
        
        day_ret = (close / pre_close - 1) * 100
        prev_ret = (pre_close / prev_close - 1) * 100 if prev_close > 0 else 0
        
        # 量比计算：当日 volume / 前 5 日平均 volume
        volumes = [float(r.get('volume', 0)) for r in rows_sorted[-6:-1] if r.get('volume')]
        curr_volume = float(curr_row.get('volume', 0))
        avg_volume = sum(volumes) / len(volumes) if volumes else 1
        volume_ratio = curr_volume / avg_volume if avg_volume > 0 else 1.0
        
        # 硬过滤条件检查
        if not (1 <= day_ret < 4):
            continue
        if not (-2 <= prev_ret <= 2):
            continue
        if not (0.8 <= volume_ratio <= 3.0):
            continue
        
        candidates.append({
            'code': code,
            'day_ret': day_ret,
            'prev_ret': prev_ret,
            'volume_ratio': volume_ratio,
            'curr_row': curr_row,
            'prev_row': prev_row,
        })
    
    print(f'  通过涨幅/量比硬过滤: {len(candidates)} 只')
    for c in candidates:
        print(f'    {c["code"]}: 涨幅={c["day_ret"]:.2f}%, 前日={c["prev_ret"]:.2f}%, 量比={c["volume_ratio"]:.2f}')
    
    # 3. 获取候选股票的 close_summary（获取 amt, turn, pre_close 等）
    print(f'\n[3/5] 获取 {len(candidates)} 只候选的 close_summary...')
    candidate_codes = [c['code'] for c in candidates]
    summary_data = {}
    for i in range(0, len(candidate_codes), 3):
        batch = candidate_codes[i:i+3]
        rows = get_close_summary_batch(batch, trade_date)
        if rows:
            for row in rows:
                code = row.get('thscode', '').replace('.SH', '')
                summary_data[code] = row
        print(f'  批次 {i//3 + 1}/{(len(candidate_codes)-1)//3 + 1} 完成')
    
    # 4. 获取 14:40 实时数据（仅候选）
    print(f'\n[4/5] 获取 {len(candidates)} 只候选的 14:40 分钟数据...')
    rt_data = {}
    for i in range(0, len(candidate_codes), 3):
        batch = candidate_codes[i:i+3]
        rows = get_realtime_price_batch(batch, trade_date, '14:40:00')
        if rows:
            for row in rows:
                code = row.get('thscode', '').replace('.SH', '')
                rt_data[code] = row
        print(f'  批次 {i//3 + 1}/{(len(candidate_codes)-1)//3 + 1} 完成')
    
    # 5. 获取上证指数数据
    print(f'\n[5/5] 获取上证指数数据...')
    index_rows = get_index_price('000001', trade_date, trade_date)
    index_data = index_rows[0] if index_rows else None
    
    # 构建 fixture
    print(f'\n构建 fixture...')
    stocks = []
    for c in candidates:
        code = c['code']
        summary = summary_data.get(code, {})
        rt = rt_data.get(code, {})
        
        # 使用 close_summary 的 pre_close 和 amt, turn
        pre_close = float(summary.get('pre_close', c['prev_row'].get('close', 0)))
        close = float(summary.get('close', c['curr_row'].get('close', 0)))
        high = float(summary.get('high', c['curr_row'].get('high', 0)))
        low = float(summary.get('low', c['curr_row'].get('low', 0)))
        open_price = float(summary.get('open', c['curr_row'].get('open', 0)))
        turn = float(summary.get('turn', 0))
        amt = float(summary.get('amt', 0))
        volume = float(summary.get('volume', c['curr_row'].get('volume', 0)))
        
        # 14:40 实时价格（如果可用，否则用 close）
        rt_close = float(rt.get('close', 0)) if rt else 0
        rt_high = float(rt.get('high', 0)) if rt else 0
        rt_low = float(rt.get('low', 0)) if rt else 0
        rt_volume = float(rt.get('volume', 0)) if rt else 0
        
        # 使用 14:40 数据（如果可用）
        if rt_close > 0:
            price = rt_close
            high = rt_high if rt_high > 0 else high
            low = rt_low if rt_low > 0 else low
        else:
            price = close
        
        if pre_close <= 0 or price <= 0:
            continue
        
        day_ret = (price / pre_close - 1) * 100
        
        # 日内位置
        if high > low:
            day_pos = (price - low) / (high - low) * 100
        else:
            day_pos = 50
        
        # 价格距高点
        price_to_high = (price / high - 1) * 100 if high > 0 else 0
        
        # 成交额（百万）
        amount_mn = amt / 1e6 if amt > 0 else 500
        
        # 流通市值估算
        market_cap = amt / (turn / 100) / 1e8 if turn > 0 else 500
        
        # 量比（使用 close_summary 的 volume）
        volumes = [float(r.get('volume', 0)) for r in all_price_data.get(code, [])[-6:-1] if r.get('volume')]
        avg_volume = sum(volumes) / len(volumes) if volumes else 1
        volume_ratio = volume / avg_volume if avg_volume > 0 else 1.0
        
        # 尾盘涨幅（使用 14:40 价格 vs pre_close）
        tail_gain = day_ret
        
        # 资金流代理分（基于 buyVolume/sellVolume）
        buy_vol = float(rt.get('buyVolume', 0)) if rt else 0
        sell_vol = float(rt.get('sellVolume', 0)) if rt else 0
        if buy_vol > 0 and sell_vol > 0:
            capital_flow = 50 + (buy_vol - sell_vol) / (buy_vol + sell_vol) * 50
        else:
            capital_flow = 50 + day_ret * 5  # 简单代理
        
        sector = STATIC_SECTOR_MAP.get(code, '未知')
        
        stock = {
            'code': code,
            'name': code,
            'sector': sector,
            'price': round(price, 3),
            'pre_close': round(pre_close, 3),
            'tail_gain_pct': round(tail_gain, 2),
            'volume_ratio': round(volume_ratio, 2),
            'tail_vol_ratio': 0.25,
            'turnover_rate': round(turn, 2) if turn > 0 else 0,
            'amount_mn': round(amount_mn, 1),
            'market_cap_bn': round(market_cap, 1),
            'pe': None,
            'ma_state': 'bull' if day_ret > 0 else 'range',
            'pattern': 'breakout' if day_ret > 1 else 'pullback' if day_ret > 0 else 'none',
            'capital_flow_score': round(capital_flow, 1),
            'sector_score': 10,
            'news_sentiment': 0,
            'hot_rank': None,
            'day_position_pct': round(day_pos, 1),
            'price_to_day_high_pct': round(price_to_high, 2),
            'last_bar_vol_share_tail_pct': 15,
            'next_open': round(price * (1 + (day_ret * 0.3 / 100)), 3),
        }
        stocks.append(stock)
    
    # 市场数据
    index_pct_chg = float(index_data.get('pct_chg', 0)) if index_data else 0
    market = {
        'state': 'range',
        'index_tail_return_pct': round(index_pct_chg, 2),
        'breadth_up_ratio': 0.5,
        'limit_up': 50,
        'limit_down': 30,
    }
    
    # 构建 fixture
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
    
    fixture_path = DATA_DIR / f'fixture_{trade_date}_1440_ifind.json'
    with open(fixture_path, 'w', encoding='utf-8') as f:
        json.dump(fixture, f, ensure_ascii=False, indent=2)
    
    print(f'\nFixture 保存到: {fixture_path}')
    print(f'  包含 {len(stocks)} 只股票')
    
    return fixture_path

if __name__ == '__main__':
    path = main()
    print(path)
