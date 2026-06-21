# -*- coding: utf-8 -*-
"""Fetch 601988 data via yfinance as fallback"""
import os
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'no_proxy']:
    os.environ.pop(key, None)

import yfinance as yf
import pandas as pd

# 601988.SS on Yahoo Finance
ticker = yf.Ticker("601988.SS")
df = ticker.history(start="2015-01-01", end="2026-06-08")
print(f"yfinance: {len(df)} rows")
print(df.head())
print(df.tail())

# Save to CSV
df.to_csv("D:\\mycode\\601988_yf.csv", encoding="utf-8")
print("Saved to 601988_yf.csv")
