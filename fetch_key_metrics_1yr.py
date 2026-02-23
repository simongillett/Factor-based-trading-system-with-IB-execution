import json
import requests
import duckdb
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

API_KEY = "fgfEv44qtqQrb6pudFfWS2UsSCTlQbpJ"

def load_companies():
    with open('all_companies_ciks.json', 'r') as f:
        return json.load(f)

def get_key_metrics(symbol):
    url = f"https://financialmodelingprep.com/api/v3/key-metrics/{symbol}?period=quarter&limit=4&apikey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if isinstance(data, list):
            return symbol, data
        else:
            return symbol, []
    except:
        return symbol, []

def setup_database():
    conn = duckdb.connect('FMP_Databases/company_metrics__1yr.db')
    conn.execute("DROP TABLE IF EXISTS key_metrics")
    conn.execute("""
        CREATE TABLE key_metrics (
            symbol VARCHAR,
            cik VARCHAR,
            date DATE,
            fiscal_year VARCHAR,
            period VARCHAR,
            reported_currency VARCHAR,
            market_cap DOUBLE,
            enterprise_value DOUBLE,
            ev_to_sales DOUBLE,
            ev_to_operating_cash_flow DOUBLE,
            ev_to_free_cash_flow DOUBLE,
            ev_to_ebitda DOUBLE,
            net_debt_to_ebitda DOUBLE,
            current_ratio DOUBLE,
            income_quality DOUBLE,
            graham_number DOUBLE,
            graham_net_net DOUBLE,
            tax_burden DOUBLE,
            interest_burden DOUBLE,
            working_capital DOUBLE,
            invested_capital DOUBLE,
            return_on_assets DOUBLE,
            operating_return_on_assets DOUBLE,
            return_on_tangible_assets DOUBLE,
            return_on_equity DOUBLE,
            return_on_invested_capital DOUBLE,
            return_on_capital_employed DOUBLE,
            earnings_yield DOUBLE,
            free_cash_flow_yield DOUBLE,
            capex_to_operating_cash_flow DOUBLE,
            capex_to_depreciation DOUBLE,
            capex_to_revenue DOUBLE,
            sales_general_admin_to_revenue DOUBLE,
            research_development_to_revenue DOUBLE,
            stock_based_compensation_to_revenue DOUBLE,
            intangibles_to_total_assets DOUBLE,
            average_receivables DOUBLE,
            average_payables DOUBLE,
            average_inventory DOUBLE,
            days_of_sales_outstanding DOUBLE,
            days_of_payables_outstanding DOUBLE,
            days_of_inventory_outstanding DOUBLE,
            operating_cycle DOUBLE,
            cash_conversion_cycle DOUBLE,
            free_cash_flow_to_equity DOUBLE,
            free_cash_flow_to_firm DOUBLE,
            tangible_asset_value DOUBLE,
            net_current_asset_value DOUBLE
        )
    """)
    return conn

def insert_metrics(conn, symbol, cik, metrics):
    for metric in metrics:
        if isinstance(metric, dict):
            conn.execute("""
                INSERT INTO key_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, cik, metric.get('date'),
                metric.get('fiscalYear'), metric.get('period'), metric.get('reportedCurrency'),
                metric.get('marketCap'), metric.get('enterpriseValue'),
                metric.get('evToSales'), metric.get('evToOperatingCashFlow'),
                metric.get('evToFreeCashFlow'), metric.get('evToEBITDA'),
                metric.get('netDebtToEBITDA'), metric.get('currentRatio'),
                metric.get('incomeQuality'), metric.get('grahamNumber'),
                metric.get('grahamNetNet'), metric.get('taxBurden'),
                metric.get('interestBurden'), metric.get('workingCapital'),
                metric.get('investedCapital'), metric.get('returnOnAssets'),
                metric.get('operatingReturnOnAssets'), metric.get('returnOnTangibleAssets'),
                metric.get('returnOnEquity'), metric.get('returnOnInvestedCapital'),
                metric.get('returnOnCapitalEmployed'), metric.get('earningsYield'),
                metric.get('freeCashFlowYield'), metric.get('capexToOperatingCashFlow'),
                metric.get('capexToDepreciation'), metric.get('capexToRevenue'),
                metric.get('salesGeneralAndAdministrativeToRevenue'),
                metric.get('researchAndDevelopementToRevenue'),
                metric.get('stockBasedCompensationToRevenue'),
                metric.get('intangiblesToTotalAssets'), metric.get('averageReceivables'),
                metric.get('averagePayables'), metric.get('averageInventory'),
                metric.get('daysOfSalesOutstanding'), metric.get('daysOfPayablesOutstanding'),
                metric.get('daysOfInventoryOutstanding'), metric.get('operatingCycle'),
                metric.get('cashConversionCycle'), metric.get('freeCashFlowToEquity'),
                metric.get('freeCashFlowToFirm'), metric.get('tangibleAssetValue'),
                metric.get('netCurrentAssetValue')
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
            futures = [executor.submit(get_key_metrics, company['symbol']) for company in batch]
            
            for future in as_completed(futures):
                symbol, metrics = future.result()
                company = next(c for c in batch if c['symbol'] == symbol)
                insert_metrics(conn, symbol, company['cik'], metrics)
                processed += 1
                
                if processed % 100 == 0:
                    print(f"Processed {processed}/{len(companies)} companies")
        
        elapsed = time.time() - start_time
        if elapsed < 60:
            time.sleep(60 - elapsed)
    
    result = conn.execute("SELECT COUNT(*) FROM key_metrics").fetchone()
    print(f"Completed! Inserted {result[0]} total records")
    
    conn.close()

if __name__ == "__main__":
    main()
