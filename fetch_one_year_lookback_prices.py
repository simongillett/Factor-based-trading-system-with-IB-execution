import json
import requests
import duckdb
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import os

API_KEY = os.environ["FMP_API_KEY"]

def load_companies():
    with open('all_companies_ciks.json', 'r') as f:
        return json.load(f)

def get_price_data(symbol):
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}?from={start_date}&to={end_date}&apikey={API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if isinstance(data, dict) and 'historical' in data:
            return symbol, data['historical']
        return symbol, []
    except:
        return symbol, []

def setup_database():
    conn = duckdb.connect('FMP_Databases/company_prices_daily.db')
    conn.execute("DROP TABLE IF EXISTS prices")
    conn.execute("""
        CREATE TABLE prices (
            symbol VARCHAR,
            cik VARCHAR,
            date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            adj_close DOUBLE,
            volume BIGINT,
            unadjusted_volume BIGINT,
            change DOUBLE,
            change_percent DOUBLE,
            vwap DOUBLE,
            label VARCHAR,
            change_over_time DOUBLE,
            PRIMARY KEY(symbol, date)
        )
    """)
    return conn

def insert_prices(conn, symbol, cik, prices):
    for price in prices:
        if isinstance(price, dict):
            conn.execute("""
                INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, cik, price.get('date'),
                price.get('open'), price.get('high'), price.get('low'),
                price.get('close'), price.get('adjClose'), price.get('volume'),
                price.get('unadjustedVolume'), price.get('change'),
                price.get('changePercent'), price.get('vwap'),
                price.get('label'), price.get('changeOverTime')
            ))

def main():
    companies = load_companies()
    conn = setup_database()
    
    batch_size = 750
    processed = 0
    
    for i in range(0, len(companies), batch_size):
        batch = companies[i:i+batch_size]
        start_time = time.time()
        
        print(f"Processing batch {i//batch_size + 1}: companies {i+1}-{min(i+batch_size, len(companies))}")
        
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(get_price_data, company['symbol']) for company in batch]
            
            for future in as_completed(futures):
                symbol, prices = future.result()
                company = next(c for c in batch if c['symbol'] == symbol)
                insert_prices(conn, symbol, company['cik'], prices)
                processed += 1
                
                if processed % 100 == 0:
                    print(f"Processed {processed}/{len(companies)} companies")
        
        elapsed = time.time() - start_time
        if elapsed < 60:
            time.sleep(60 - elapsed)
    
    result = conn.execute("SELECT COUNT(*) FROM prices").fetchone()
    print(f"Completed! Inserted {result[0]} price records")
    
    conn.close()

if __name__ == "__main__":
    main()
