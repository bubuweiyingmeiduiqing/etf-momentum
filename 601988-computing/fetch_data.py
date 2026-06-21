# -*- coding: utf-8 -*-
"""Fetch data directly bypassing proxy, save to CSV for reuse"""
import os
# Disable all proxies
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'no_proxy', 'NO_PROXY']:
    os.environ.pop(key, None)
os.environ['no_proxy'] = '*'

import requests
import json

session = requests.Session()
session.trust_env = False

url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
params = {
    'fields1': 'f1,f2,f3,f4,f5,f6',
    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116',
    'ut': '7eea3edcaed734bea9cbfc24409ed989',
    'klt': '101',
    'fqt': '1',
    'secid': '1.601988',
    'beg': '20150101',
    'end': '20260608'
}

r = session.get(url, params=params, timeout=30)
print(f'Status: {r.status_code}')
data = r.json()
if data.get('data') and data['data'].get('klines'):
    klines = data['data']['klines']
    print(f'Got {len(klines)} klines')
    print(f'First: {klines[0]}')
    # Save raw response for reuse
    with open('D:\\mycode\\601988_data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    print('Saved to 601988_data.json')
else:
    print(f'Response: {json.dumps(data, ensure_ascii=False)[:500]}')
