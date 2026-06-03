# Zipline Factor Backtest

Factor-based backtesting using Zipline Reloaded with DuckDB data.

## Methodology

### Walk-Forward Validation
- 9-month rolling training window
- 3-month out-of-sample test window
- Scaler fit only on historical data before each test period

### Purged K-Fold Cross-Validation
- 5 folds for feature selection
- 2-day purge buffer before test set (prevents leakage from sequential correlation)
- 5-day embargo after test set
- Features retained only if selected in majority of folds

### Feature Selection Pipeline
1. Variance threshold (>0.01) removes low-variance features
2. Correlation filter (>0.9) removes redundant features
3. Equal weighting of surviving features into composite score

### Current Features (21)
**Valuation:** market_cap, ev_to_sales, ev_to_operating_cash_flow, net_debt_to_ebitda

**Quality/Value:** income_quality, graham_number, graham_net_net

**Liquidity:** current_ratio, working_capital, invested_capital

**Returns:** return_on_tangible_assets

**Capital Efficiency:** capex_to_operating_cash_flow, capex_to_depreciation, capex_to_revenue

**Cost Structure:** sales_general_admin_to_revenue, stock_based_compensation_to_revenue

**Working Capital:** average_receivables, average_payables, average_inventory, tangible_asset_value, net_current_asset_value

### Strategy Parameters
```python
N_SPLITS = 5          # K-fold splits
EMBARGO_DAYS = 5      # Gap after test set
PURGE_DAYS = 2        # Gap before test set
TRAIN_MONTHS = 9      # Training window
TEST_MONTHS = 3       # Test window
```

## Data Pipeline

### Weekly Update (Sundays 8pm ET)
1. Fetch latest quarterly metrics → `FMP_Databases/company_metrics__1yr.db`
2. Run walk-forward backtest
3. Save results to `results/`

### Daily Update (6am ET)
- Price data → `FMP_Databases/company_prices_daily_1yr.db`

## Usage

### Manual Run
```bash
source venv/bin/activate
python normalization_weighting_Zipline.py
```

### Cron Setup
```bash
crontab -e
# Weekly backtest + metrics update
0 20 * * 0 /home/simon0099/Lodon/zipline-factor-backtest/run_weekly_backtest.sh >> /home/simon0099/Lodon/zipline-factor-backtest/logs/weekly_backtest.log 2>&1

# Daily price update
0 6 * * * cd /home/simon0099/Lodon/FMP_Databases && /home/simon0099/Lodon/.venv/bin/python3 daily_update.py >> daily_update.log 2>&1
```

## Output

Results saved to `results/backtest_results_YYYYMMDD_to_YYYYMMDD_Ndays.json`

## Files

- `normalization_weighting_Zipline.py` - Main backtest with walk-forward CV
- `run_weekly_backtest.sh` - Weekly runner (updates metrics + runs backtest)
- `.zipline/` - Zipline bundle configuration
