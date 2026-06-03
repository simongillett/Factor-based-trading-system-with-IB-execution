"""Backfill new CIKs into existing metrics and price databases."""
import json
import requests
import duckdb
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import os

API_KEY = os.environ["FMP_API_KEY"]

def load_new_companies():
    with open('new_ciks_to_backfill.json', 'r') as f:
        return json.load(f)

def load_exchanges():
    resp = requests.get(f"https://financialmodelingprep.com/api/v3/stock/list?apikey={API_KEY}")
    return {s['symbol']: s.get('exchangeShortName', '') for s in resp.json()}

def get_key_metrics_1yr(symbol):
    url = f"https://financialmodelingprep.com/api/v3/key-metrics/{symbol}?period=quarter&limit=4&apikey={API_KEY}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        return symbol, data if isinstance(data, list) else []
    except:
        return symbol, []

def get_key_metrics_10yr(symbol):
    url = f"https://financialmodelingprep.com/api/v3/key-metrics/{symbol}?period=quarter&limit=40&apikey={API_KEY}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        return symbol, data if isinstance(data, list) else []
    except:
        return symbol, []

def get_prices_1yr(symbol):
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}?from={start}&to={end}&apikey={API_KEY}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        return symbol, data.get('historical', [])
    except:
        return symbol, []

def get_prices_10yr(symbol):
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=3650)).strftime('%Y-%m-%d')
    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}?from={start}&to={end}&apikey={API_KEY}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        return symbol, data.get('historical', [])
    except:
        return symbol, []

def insert_metrics(conn, symbol, cik, exchange, metrics):
    for m in metrics:
        if not isinstance(m, dict):
            continue
        conn.execute("""
            INSERT INTO key_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, cik, exchange, m.get('date'),
            m.get('fiscalYear'), m.get('period'), m.get('reportedCurrency'),
            m.get('marketCap'), m.get('enterpriseValue'),
            m.get('evToSales'), m.get('evToOperatingCashFlow'),
            m.get('evToFreeCashFlow'), m.get('evToEBITDA'),
            m.get('netDebtToEBITDA'), m.get('currentRatio'),
            m.get('incomeQuality'), m.get('grahamNumber'),
            m.get('grahamNetNet'), m.get('taxBurden'),
            m.get('interestBurden'), m.get('workingCapital'),
            m.get('investedCapital'), m.get('returnOnAssets'),
            m.get('operatingReturnOnAssets'), m.get('returnOnTangibleAssets'),
            m.get('returnOnEquity'), m.get('returnOnInvestedCapital'),
            m.get('returnOnCapitalEmployed'), m.get('earningsYield'),
            m.get('freeCashFlowYield'), m.get('capexToOperatingCashFlow'),
            m.get('capexToDepreciation'), m.get('capexToRevenue'),
            m.get('salesGeneralAndAdministrativeToRevenue'),
            m.get('researchAndDevelopementToRevenue'),
            m.get('stockBasedCompensationToRevenue'),
            m.get('intangiblesToTotalAssets'), m.get('averageReceivables'),
            m.get('averagePayables'), m.get('averageInventory'),
            m.get('daysOfSalesOutstanding'), m.get('daysOfPayablesOutstanding'),
            m.get('daysOfInventoryOutstanding'), m.get('operatingCycle'),
            m.get('cashConversionCycle'), m.get('freeCashFlowToEquity'),
            m.get('freeCashFlowToFirm'), m.get('tangibleAssetValue'),
            m.get('netCurrentAssetValue')
        ))

def insert_metrics_10yr(conn, symbol, cik, exchange, metrics):
    for m in metrics:
        if not isinstance(m, dict):
            continue
        conn.execute("""
            INSERT INTO key_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, cik, m.get('date'),
            m.get('fiscalYear'), m.get('period'), m.get('reportedCurrency'),
            m.get('marketCap'), m.get('enterpriseValue'),
            m.get('evToSales'), m.get('evToOperatingCashFlow'),
            m.get('evToFreeCashFlow'), m.get('evToEBITDA'),
            m.get('netDebtToEBITDA'), m.get('currentRatio'),
            m.get('incomeQuality'), m.get('grahamNumber'),
            m.get('grahamNetNet'), m.get('taxBurden'),
            m.get('interestBurden'), m.get('workingCapital'),
            m.get('investedCapital'), m.get('returnOnAssets'),
            m.get('operatingReturnOnAssets'), m.get('returnOnTangibleAssets'),
            m.get('returnOnEquity'), m.get('returnOnInvestedCapital'),
            m.get('returnOnCapitalEmployed'), m.get('earningsYield'),
            m.get('freeCashFlowYield'), m.get('capexToOperatingCashFlow'),
            m.get('capexToDepreciation'), m.get('capexToRevenue'),
            m.get('salesGeneralAndAdministrativeToRevenue'),
            m.get('researchAndDevelopementToRevenue'),
            m.get('stockBasedCompensationToRevenue'),
            m.get('intangiblesToTotalAssets'), m.get('averageReceivables'),
            m.get('averagePayables'), m.get('averageInventory'),
            m.get('daysOfSalesOutstanding'), m.get('daysOfPayablesOutstanding'),
            m.get('daysOfInventoryOutstanding'), m.get('operatingCycle'),
            m.get('cashConversionCycle'), m.get('freeCashFlowToEquity'),
            m.get('freeCashFlowToFirm'), m.get('tangibleAssetValue'),
            m.get('netCurrentAssetValue'), exchange
        ))

def insert_prices(conn, symbol, cik, prices):
    for p in prices:
        if not isinstance(p, dict):
            continue
        conn.execute("""
            INSERT OR IGNORE INTO prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, cik, p.get('date'), p.get('open'), p.get('high'),
            p.get('low'), p.get('close'), p.get('adjClose', p.get('close')),
            p.get('volume'), p.get('unadjustedVolume'),
            p.get('change'), p.get('changePercent'),
            p.get('vwap'), p.get('label'), p.get('changeOverTime')
        ))

def run_batches(companies, fetch_fn, insert_fn, conn):
    # Skip symbols already in this DB
    try:
        existing = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM key_metrics").fetchall()}
    except:
        existing = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM prices").fetchall()}
    to_fetch = [c for c in companies if c['symbol'] not in existing]
    print(f"  {len(to_fetch)} new (skipping {len(companies) - len(to_fetch)} already present)")

    batch_size = 750
    processed = 0
    for i in range(0, len(to_fetch), batch_size):
        batch = to_fetch[i:i+batch_size]
        start_time = time.time()
        print(f"  Batch {i//batch_size + 1}: {i+1}-{min(i+batch_size, len(to_fetch))}")

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(fetch_fn, c['symbol']) for c in batch]
            for future in as_completed(futures):
                symbol, data = future.result()
                if data:
                    company = next(c for c in batch if c['symbol'] == symbol)
                    insert_fn(conn, symbol, company['cik'], data)
                processed += 1
                if processed % 100 == 0:
                    print(f"    {processed}/{len(to_fetch)}")

        elapsed = time.time() - start_time
        if elapsed < 60:
            time.sleep(60 - elapsed)

def main():
    companies = load_new_companies()
    print(f"Backfilling {len(companies)} new symbols...")

    print("\nLoading exchange mappings...")
    exchanges = load_exchanges()

    def insert_metrics_1yr_with_exchange(conn, symbol, cik, metrics):
        insert_metrics(conn, symbol, cik, exchanges.get(symbol, ''), metrics)

    def insert_metrics_10yr_with_exchange(conn, symbol, cik, metrics):
        insert_metrics_10yr(conn, symbol, cik, exchanges.get(symbol, ''), metrics)

    # 1yr metrics
    print(f"\n1yr metrics ({len(companies)} symbols)...")
    con = duckdb.connect('FMP_Databases/company_metrics__1yr.db')
    run_batches(companies, get_key_metrics_1yr, insert_metrics_1yr_with_exchange, con)
    con.close()

    # 10yr metrics
    print(f"\n10yr metrics ({len(companies)} symbols)...")
    con = duckdb.connect('FMP_Databases/company_metrics_10yr.db')
    run_batches(companies, get_key_metrics_10yr, insert_metrics_10yr_with_exchange, con)
    con.close()

    # 1yr prices
    print(f"\n1yr prices ({len(companies)} symbols)...")
    con = duckdb.connect('FMP_Databases/company_prices_daily_1yr.db')
    run_batches(companies, get_prices_1yr, insert_prices, con)
    con.close()

    # 10yr prices
    print(f"\n10yr prices ({len(companies)} symbols)...")
    con = duckdb.connect('FMP_Databases/company_prices_daily.db')
    run_batches(companies, get_prices_10yr, insert_prices, con)
    con.close()

    print("\nBackfill complete!")

if __name__ == "__main__":
    main()
