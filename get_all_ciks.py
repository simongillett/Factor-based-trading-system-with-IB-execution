import requests
import json

def get_all_company_ciks():
    """Get all active companies with CIKs from SEC"""
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {'User-Agent': 'Lodon Trading admin@lodon.dev'}
    response = requests.get(url, headers=headers)
    data = response.json()
    
    companies = []
    for key, company in data.items():
        companies.append({
            'symbol': company['ticker'],
            'name': company['title'],
            'cik': str(company['cik_str']).zfill(10)
        })
    
    return companies

def main():
    print("Fetching all active companies with CIKs...")
    companies = get_all_company_ciks()
    
    with open('all_companies_ciks.json', 'w') as f:
        json.dump(companies, f, indent=2)
    
    print(f"Found {len(companies)} active companies with CIKs")
    print("Results saved to all_companies_ciks.json")
    
    print("\nFirst 10 companies:")
    for company in companies[:10]:
        print(f"{company['symbol']}: {company['cik']} - {company['name']}")

if __name__ == "__main__":
    main()
