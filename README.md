# Lodon - Factor-Based Automated Trading System

Quantitative trading system using fundamental factor scoring with walk-forward validation and IBKR execution.

## Overview

```
Data Pipeline (Daily)     →  Factor Model (Weekly)      →  Execution (Daily)
FMP API → DuckDB             Walk-forward CV → Signals     daily_trade.py → IB Gateway
```

## Features

- **Dynamically selected factors** via IC-based selection from variance/correlation-filtered candidates
- **Walk-forward validation** to prevent look-ahead bias
- **USD-only filtering** to ensure consistent metric units
- **Score-weighted position sizing** (higher conviction = larger position)
- **1.5x max leverage** with score-weighted allocation
- **IB Adaptive algo orders** for price improvement with fill tracking
- **IB Gateway integration** via IBC for headless execution
- **Automated TOTP login** via `gateway_login.sh` with xdotool-based 2FA entry
- **Log rotation** with 30-day retention and monthly gzip archival

## Architecture

### Data Pipeline
- Daily price updates (6am) from FMP API via cron
- Weekly corporate metrics database update + backtest (Sunday 8pm) via cron
- DuckDB for efficient storage and querying

### Factor Model
- IC-based factor selection via Spearman rank correlation with forward returns
- Variance threshold + correlation filter for candidate screening
- StandardScaler normalization (fit on training data only)
- Equal-weighted composite score with IC-derived directions

### Position Sizing
- Score determines target weight: `weight = leverage × score / sum(scores)`
- Dollar allocation: `dollars = account_value × target_weight`
- Shares: `shares = dollars / stock_price`

### Execution
- `daily_trade.py` generates signals and executes directly via IB Gateway
- USD-only filtering to match backtest universe
- IB contract resolution (conId) with automatic exclusion of unresolvable symbols
- IB Adaptive algo orders (Normal priority) for price improvement
- Event-driven fill tracking with 5-minute timeout
- SELLs execute before BUYs

### IB Gateway Stack
```
daily_trade.py
    ↓ TWS API (ibapi)
IB Gateway (localhost:4001)
    ↓ managed by
IBC (handles login/2FA/restarts)
    ↓
IBKR Servers
```

- **IB Gateway**: IBKR's headless trading application (like TWS without GUI)
- **IBC**: Third-party controller that automates login, handles 2FA, and manages restarts
- **gateway_login.sh**: Wrapper that starts IBC and auto-fills TOTP via xdotool when IBC's 2FA dialog appears
- **TWS API**: IBKR's Python library (`ibapi`) for sending orders and receiving data

## Factors Used

Factors are dynamically selected each week via IC-based filtering. The candidate pool (after variance/correlation screening) is scored by Spearman IC against forward returns; factors with |IC| ≥ 0.01 are selected. The IC sign determines direction (+1 or -1).

| Category | Candidate Factors |
|----------|---------|
| Valuation | market_cap, ev_to_sales, net_debt_to_ebitda |
| Quality | income_quality, graham_number, graham_net_net |
| Liquidity | current_ratio, working_capital |
| Returns | return_on_tangible_assets |
| Yield | earnings_yield, free_cash_flow_yield |
| Capital Efficiency | capex_to_operating_cash_flow |
| Cost Structure | sales_general_admin_to_revenue, stock_based_compensation_to_revenue |
| Asset Composition | intangibles_to_total_assets |
| Working Capital | average_payables |

## Setup

### Prerequisites
- Python 3.12+
- IB Gateway with IBC
- FMP API key

### Installation
```bash
git clone https://github.com/yourusername/lodon.git
cd lodon
pip install -r requirements.txt
```

### Configuration
1. Copy `config.example.ini` to `config.ini` and fill in credentials
2. Configure IBC in `~/ibc/config.ini`:
   - Set `AutoRestart=yes` to keep Gateway running up to 1 week without re-authentication
   - Set `IbAutoClosedown=no` to prevent automatic shutdown
   - Set `ReloginAfterSecondFactorAuthenticationTimeout=yes` for 2FA handling

## Usage

### Run Backtest
```bash
cd zipline-factor-backtest
python normalization_weighting_Zipline.py
```

### Run Daily Trade (signal generation + execution)
```bash
python daily_trade.py
```

## Cron Schedule

| Time | Job |
|------|-----|
| 6:00am daily | FMP price data update |
| 8:00pm Sunday | Weekly backtest + metrics update + currency backfill |
| 9:00am Mon-Fri | IB Gateway healthcheck |
| 9:30am Mon-Fri | `daily_trade.py` — generate signals + execute |
| 2:00am 1st of month | Log rotation — archive logs older than 30 days |

## Project Structure

```
lodon/
├── zipline-factor-backtest/
│   ├── normalization_weighting_Zipline.py  # Main backtest
│   └── run_weekly_backtest.sh              # Cron runner
├── daily_trade.py                           # Signal generation + IB execution
├── gateway_login.sh                         # IBC + TOTP auto-login wrapper
├── log_rotate.sh                            # Monthly log archival
├── fetch_key_metrics_1yr.py                 # Data fetcher
├── add_reported_currency.py                 # Currency backfill for metrics DBs
├── ibgateway_healthcheck.sh                 # IB Gateway health check
└── check_*.py                               # Account utilities
```

## Risk Controls

- Maximum 15 positions
- 1.5x max leverage
- Score-weighted allocation (no equal weight)
- Price filter: $1-$10,000
- No OTC/penny stocks
- USD-only reporters (non-USD metrics excluded)
- Daily rebalancing

## License

MIT
