# Factor Model Autoresearch

Autonomous research loop for finding the best factor model configuration.

## Setup

The data lives in `~/Lodon/FMP_Databases/`:
- `company_metrics_10yr.db` — fundamental metrics (key_metrics table)
- `company_prices_daily.db` — daily OHLCV prices

`prepare.py` is fixed and provides:
- `DataCache` — loads and caches all data once
- `evaluate()` — pure-math backtest: no execution engine, no leverage
- `compute_stats()` — Sharpe, CAGR, max drawdown, etc.

`train.py` is the file you modify. It defines the factor model: which factors to use, how to compute ICs, how to select and weight factors, how to score stocks, the forward horizon, rebalance frequency, top_n, etc.

## Experimentation

Each experiment runs `uv run train.py` (or `python3 train.py`). It should complete in under 5 minutes. The script prints a summary:

```
---
sharpe:           0.564
cagr_pct:         7.71
max_drawdown_pct: -21.91
total_return_pct: 14.52
volatility_pct:   15.8
n_factors:        11
n_weeks:          104
```

Extract the key metric: `grep "^sharpe:" run.log`

**The goal: get the highest Sharpe ratio** on the 2yr out-of-sample test period (Apr 2024 – Apr 2026).

**What you CAN do:**
- Modify `train.py` — everything is fair game: factor selection, IC computation, scoring method, forward horizon, rebalance frequency, top_n, weighting scheme, outlier handling, sector neutralization, momentum factors, etc.

**What you CANNOT do:**
- Modify `prepare.py`. It contains the fixed evaluation and data loading.
- Change the test period (2024-04-01 to 2026-04-17).
- Introduce leverage in the evaluation.
- Look ahead (use future data in scoring).

**Simplicity criterion**: All else being equal, simpler is better. A small Sharpe improvement that adds ugly complexity is not worth it.

## Output format

The script must print results in this exact format at the end:

```
---
sharpe:           <float>
cagr_pct:         <float>
max_drawdown_pct: <float>
total_return_pct: <float>
volatility_pct:   <float>
n_factors:        <int>
n_weeks:          <int>
```

## Logging results

Log to `results.tsv` (tab-separated):

```
commit	sharpe	cagr_pct	max_dd_pct	status	description
```

- commit: git short hash (7 chars)
- sharpe: Sharpe ratio achieved
- cagr_pct: annualized return
- max_dd_pct: max drawdown
- status: `keep`, `discard`, or `crash`
- description: what this experiment tried

## The experiment loop

LOOP FOREVER:

1. Look at current state and results so far
2. Modify `train.py` with an experimental idea
3. git commit
4. Run: `python3 train.py > run.log 2>&1`
5. Read results: `grep "^sharpe:\|^cagr_pct:\|^max_drawdown_pct:" run.log`
6. If empty, it crashed — `tail -n 50 run.log` to debug
7. Log to results.tsv
8. If Sharpe improved, keep the commit
9. If Sharpe is equal or worse, `git reset --hard HEAD~1`

**Ideas to explore:**
- Forward horizon: 5, 10, 20, 40, 60 trading days
- Rebalance frequency: weekly, biweekly, monthly
- Top N: 25, 50, 100, 200
- IC threshold: 0.005, 0.01, 0.015, 0.02
- IC halflife: 26, 52, 104 weeks
- Scoring: rank-normalized, z-score, percentile
- Add momentum factors (1m, 3m, 6m, 12m price momentum computed from prices)
- Add volatility factors (realized vol, vol-adjusted returns)
- Add reversal factors (short-term mean reversion)
- Sector-neutral scoring
- Different IC weighting: equal, IC-weighted, IR-weighted
- Winsorization levels
- Minimum stocks for IC computation
- Combining fundamental + price factors
- Non-linear scoring (e.g. top quintile vs bottom quintile spread)

**NEVER STOP**: Once the loop begins, do NOT pause to ask the human. Run indefinitely until manually stopped.
