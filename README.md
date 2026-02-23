# Lodon - Factor-Based Automated Trading System

Quantitative trading system using fundamental factor scoring with walk-forward validation and IBKR execution.

## Overview

```
Data Pipeline (Daily)     →  Factor Model (Weekly)      →  Execution (On-demand)
FMP API → DuckDB             Walk-forward CV → Signals     SQS → IB Gateway
```

## Features

- **21 fundamental factors** selected via variance/correlation filtering
- **Walk-forward validation** to prevent look-ahead bias
- **Score-weighted position sizing** (higher conviction = larger position)
- **Automated signal generation** with BUY/SELL signals
- **IB Gateway integration** via IBC for headless execution

## Architecture

### Data Pipeline
- Daily price updates from Financial Modeling Prep (FMP) API
- Weekly fundamental metrics refresh
- DuckDB for efficient storage and querying

### Factor Model
- Purged K-fold cross-validation for feature selection
- StandardScaler normalization on training data only
- Equal-weighted composite score from 21 factors

### Execution
- Signals published to AWS SQS FIFO queue
- Execution engine polls SQS and executes via IB Gateway
- Account-relative position sizing using target weights

## Factors Used

| Category | Factors |
|----------|---------|
| Valuation | market_cap, ev_to_sales, ev_to_operating_cash_flow, net_debt_to_ebitda |
| Quality | income_quality, graham_number, graham_net_net |
| Liquidity | current_ratio, working_capital, invested_capital |
| Returns | return_on_tangible_assets |
| Capital Efficiency | capex_to_operating_cash_flow, capex_to_depreciation, capex_to_revenue |
| Cost Structure | sales_general_admin_to_revenue, stock_based_compensation_to_revenue |
| Working Capital | average_receivables, average_payables, average_inventory, tangible_asset_value, net_current_asset_value |

## Setup

### Prerequisites
- Python 3.12+
- IB Gateway with IBC
- AWS account with SQS
- FMP API key

### Installation
```bash
git clone https://github.com/yourusername/lodon.git
cd lodon
pip install -r requirements.txt
```

### Configuration
1. Copy `config.example.ini` to `config.ini` and fill in credentials
2. Set up AWS credentials in `~/.aws/credentials`
3. Configure IBC in `ibc/config.ini`

## Usage

### Run Backtest
```bash
cd zipline-factor-backtest
python normalization_weighting_Zipline.py
```

### Publish Signals
```bash
python signal_publisher.py --env live
```

### Start Execution Engine
```bash
ENV=live IB_PORT=4001 python execution_engine.py
```

## Project Structure

```
lodon/
├── zipline-factor-backtest/
│   ├── normalization_weighting_Zipline.py  # Main backtest
│   └── run_weekly_backtest.sh              # Cron runner
├── execution_engine.py                      # IB order execution
├── signal_publisher.py                      # SQS publisher
├── fetch_key_metrics_1yr.py                # Data fetcher
└── check_*.py                              # Account utilities
```

## Risk Controls

- Maximum 10 positions
- Score-weighted allocation (no equal weight)
- Price filter: $1-$500
- No OTC/penny stocks
- Weekly rebalancing

## License

MIT
