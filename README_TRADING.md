# Automated Trading System

## Overview

Factor-based automated trading system that runs entirely on a single machine. Generates signals from a weekly factor model with fresh daily prices, then executes directly via IB Gateway.

```
Daily Flow:
6:00 AM  → FMP Data Update (prices)
9:00 AM  → IB Gateway healthcheck
9:30 AM  → daily_trade.py (generate signals + execute via IB Gateway)

Weekly:
8:00 PM Sunday → Backtest + metrics update (produces factor model artifacts)
```

## daily_trade.py

Single script that handles the full trading pipeline:
1. Loads weekly factor model artifacts (factors + scaler)
2. Scores universe using fresh prices + metrics from DuckDB
3. Generates BUY signals for top 15 stocks (score-weighted, 1.5x leverage)
4. Connects to IB Gateway, detects held positions
5. Generates SELL signals for positions no longer in the BUY set
6. Executes SELLs first, then BUYs
7. Uses IB Adaptive algo orders (Normal priority) for price improvement
8. Waits for fills via event-driven tracking (5-minute timeout)

```bash
python daily_trade.py
```

## Account Management

### Check Trade Executions
```bash
python check_executions.py --start YYYYMMDD [--end YYYYMMDD] [--symbol TICKER]
```

### Check Account Status (P&L)
```bash
python check_pnl.py
```

### Check Open Orders
```bash
python check_orders.py
```

## Data Pipeline

### Data Source
- **Provider**: Financial Modeling Prep (FMP) API
- **Databases**:
  - `FMP_Databases/company_prices_daily.db` — 10-year historical prices
  - `FMP_Databases/company_prices_daily_1yr.db` — 1-year rolling prices
  - `FMP_Databases/company_metrics__1yr.db` — Fundamental metrics

### Ingestion
- **Daily** (6am): Price update via `FMP_Databases/daily_update.py`
- **Weekly** (Sunday 8pm): Metrics update + backtest via `run_weekly_backtest.sh`

## Cron Schedule

```bash
0 6 * * *   cd ~/Lodon/FMP_Databases && python3 daily_update.py
0 20 * * 0  ~/Lodon/zipline-factor-backtest/run_weekly_backtest.sh
0 9 * * 1-5 ~/Lodon/ibgateway_healthcheck.sh
30 9 * * 1-5 cd ~/Lodon && python daily_trade.py
```

## Configuration

### Trading Parameters
- **Max Positions**: 15
- **Max Leverage**: 1.5x
- **Order Type**: IB Adaptive (Normal priority)
- **Price Filter**: $1–$10,000
- **Min Dollar Volume**: $1M
- **No OTC/penny stocks**

### IB Gateway
- Port 4001 (live)
- Managed by IBC (handles login, 2FA, restarts)
- Healthcheck runs at 9:00am Mon-Fri

## Monitoring

- `logs/daily_trade_YYYYMMDD.log` — daily execution log
- `logs/trades.json` — trade history
- `daily_signals/signals_YYYYMMDD.json` — generated signals
