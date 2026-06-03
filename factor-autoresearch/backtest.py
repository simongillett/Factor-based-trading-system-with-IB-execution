"""
Walk-forward factor backtest using 10yr data.
No zipline — pure vectorized backtest for speed.

Training uses Information Coefficient (IC):
- For each rebalance in the training window, compute Spearman rank correlation
  between each factor and forward 1-week returns
- Keep factors with mean |IC| > threshold and consistent sign (IR > 0.5)
- Weight factors by their Information Ratio (mean IC / std IC)
- Use rank-normalization instead of z-scores for robustness to outliers

Walk-forward test logic:
- Rebalance weekly
- At each rebalance, only use metrics with date <= rebalance_date
- Rank-normalize factors cross-sectionally, score with IC weights
- Pick top N, allocate by score-weighted sizing
"""

import json
import sys
import time
import duckdb
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import spearmanr

# ── Config ────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "top_n": 15,
    "leverage": 1.0,
    "min_dollar_volume": 1_000_000,
    "min_price": 1,
    "max_price": 10_000,
    "variance_threshold": 0.01,
    "correlation_threshold": 0.9,
    "rebalance_day": "Monday",
    "train_start": "2016-04-01",
    "test_start": "2021-04-01",
    "test_end": "2026-04-17",
    "capital": 1_000_000,
    "ic_threshold": 0.02,       # min |mean IC| to keep a factor
    "ir_threshold": 0.5,        # min |mean IC / std IC| to keep a factor
    "ic_halflife": 52,          # exponential decay halflife in rebalance periods
    "forward_days": 5,          # forward return horizon (trading days)
    "factors": None,
    "factor_directions": None,
}

ID_COLS = {"symbol", "cik", "date", "fiscal_year", "period", "reported_currency", "exchange"}


def load_config(path="config.json"):
    cfg = DEFAULT_CONFIG.copy()
    p = Path(__file__).parent / path
    if p.exists():
        with open(p) as f:
            cfg.update(json.load(f))
    return cfg


def load_data(cfg):
    db_dir = Path(__file__).parent.parent / "FMP_Databases"

    con = duckdb.connect(str(db_dir / "company_metrics_10yr.db"), read_only=True)
    metrics = con.execute(f"""
        SELECT * FROM key_metrics
        WHERE date >= '{cfg['train_start']}'
          AND exchange NOT IN ('OTC', 'PNK', '')
          AND reported_currency = 'USD'
    """).fetchdf()
    con.close()

    con = duckdb.connect(str(db_dir / "company_prices_daily.db"), read_only=True)
    prices = con.execute(f"""
        SELECT symbol, date, close, volume
        FROM prices
        WHERE date >= '{cfg['train_start']}' AND date <= '{cfg['test_end']}'
    """).fetchdf()
    con.close()

    metrics["date"] = pd.to_datetime(metrics["date"])
    prices["date"] = pd.to_datetime(prices["date"])
    prices["dollar_volume"] = prices["close"] * prices["volume"]

    return metrics, prices


def prefilter_factors(metrics, cfg):
    """Variance + correlation filter to get candidate factors."""
    from sklearn.feature_selection import VarianceThreshold

    numeric_cols = [c for c in metrics.columns if c not in ID_COLS]
    X = metrics[numeric_cols].fillna(0).values

    sel = VarianceThreshold(threshold=cfg["variance_threshold"])
    sel.fit(X)
    retained = [c for c, k in zip(numeric_cols, sel.get_support()) if k]

    corr = np.abs(np.corrcoef(metrics[retained].fillna(0).values, rowvar=False))
    drop = set()
    for i in range(len(retained)):
        for j in range(i + 1, len(retained)):
            if corr[i, j] > cfg["correlation_threshold"]:
                drop.add(retained[j])
    return [c for c in retained if c not in drop]


def rank_normalize(series):
    """Cross-sectional rank normalization to [0, 1]."""
    ranked = series.rank(pct=True)
    return ranked


def compute_ic_weights(metrics, prices, candidate_factors, price_pivot, trading_dates, cfg):
    """
    Compute IC for each factor over the training period.
    Returns: (selected_factors, ic_weights) where ic_weights are signed.
    """
    day_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4}
    rebal_day = day_map[cfg["rebalance_day"]]
    train_start = pd.Timestamp(cfg["train_start"])
    test_start = pd.Timestamp(cfg["test_start"])
    fwd_days = cfg["forward_days"]

    train_rebal_dates = [d for d in trading_dates
                         if d.weekday() == rebal_day and train_start <= d < test_start]

    # Need forward return dates
    date_list = list(trading_dates)
    date_idx = {d: i for i, d in enumerate(date_list)}

    # Dollar volume rolling for liquidity filter
    dv_pivot = prices.pivot_table(index="date", columns="symbol", values="dollar_volume")
    dv_rolling = dv_pivot.rolling(20, min_periods=5).mean()

    # Collect ICs per factor per rebalance date
    ic_history = {f: [] for f in candidate_factors}
    n_valid = 0

    for date in train_rebal_dates:
        idx = date_idx.get(date)
        if idx is None or idx + fwd_days >= len(date_list):
            continue
        fwd_date = date_list[idx + fwd_days]

        # Latest metrics per symbol as of this date
        available = metrics[metrics["date"] <= date]
        latest = available.groupby("symbol").last().reset_index()

        # Liquidity filter
        dv_today = dv_rolling.loc[date] if date in dv_rolling.index else pd.Series(dtype=float)
        tradeable = []
        for sym in latest["symbol"]:
            if sym not in price_pivot.columns:
                continue
            p = price_pivot.at[date, sym]
            pf = price_pivot.at[fwd_date, sym] if fwd_date in price_pivot.index else np.nan
            dv = dv_today.get(sym, 0)
            if (pd.notna(p) and pd.notna(pf) and p > 0
                    and cfg["min_price"] <= p <= cfg["max_price"]
                    and dv >= cfg["min_dollar_volume"]):
                tradeable.append(sym)

        candidates = latest[latest["symbol"].isin(tradeable)].set_index("symbol")
        if len(candidates) < 30:
            continue

        # Forward returns
        p_now = price_pivot.loc[date, candidates.index]
        p_fwd = price_pivot.loc[fwd_date, candidates.index]
        fwd_ret = (p_fwd / p_now - 1).dropna()
        valid_syms = fwd_ret.index

        if len(valid_syms) < 30:
            continue

        n_valid += 1
        for f in candidate_factors:
            vals = candidates.loc[valid_syms, f]
            # Need enough non-null
            mask = vals.notna() & fwd_ret.loc[valid_syms].notna()
            if mask.sum() < 20:
                ic_history[f].append(np.nan)
                continue
            ic, _ = spearmanr(vals[mask], fwd_ret[valid_syms][mask])
            ic_history[f].append(ic)

    print(f"IC computed over {n_valid} training rebalances")

    # Exponential decay weights
    halflife = cfg["ic_halflife"]
    decay = np.array([2 ** (-(n_valid - 1 - i) / halflife) for i in range(n_valid)])

    # Select factors by IC threshold and IR threshold
    selected = []
    weights = []
    for f in candidate_factors:
        ics = np.array(ic_history[f])
        valid = ~np.isnan(ics)
        if valid.sum() < 10:
            continue
        w = decay[valid]
        w = w / w.sum()
        ic_vals = ics[valid]
        mean_ic = np.average(ic_vals, weights=w)
        std_ic = np.sqrt(np.average((ic_vals - mean_ic) ** 2, weights=w))
        ir = mean_ic / std_ic if std_ic > 0 else 0

        if abs(mean_ic) >= cfg["ic_threshold"] and abs(ir) >= cfg["ir_threshold"]:
            selected.append(f)
            weights.append(mean_ic)  # signed weight — direction is embedded
            print(f"  {f:40s}  IC={mean_ic:+.4f}  IR={ir:+.3f}")

    if not selected:
        print("WARNING: no factors passed IC/IR filter, falling back to top 5 by |IC|")
        all_ics = []
        for f in candidate_factors:
            ics = np.array(ic_history[f])
            valid = ~np.isnan(ics)
            if valid.sum() < 10:
                continue
            mean_ic = np.nanmean(ics[valid])
            all_ics.append((f, mean_ic))
        all_ics.sort(key=lambda x: abs(x[1]), reverse=True)
        for f, ic in all_ics[:5]:
            selected.append(f)
            weights.append(ic)
            print(f"  (fallback) {f:40s}  IC={ic:+.4f}")

    weights = np.array(weights)
    # Normalize weights to sum of abs = 1
    weights = weights / np.abs(weights).sum()

    return selected, weights


def run_backtest(cfg):
    t0 = time.time()
    metrics, prices = load_data(cfg)
    print(f"Loaded {len(metrics)} metrics, {len(prices)} prices in {time.time()-t0:.1f}s")

    # Prefilter candidates
    train_metrics = metrics[metrics["date"] < cfg["test_start"]]
    candidate_factors = prefilter_factors(train_metrics, cfg)
    print(f"Candidate factors ({len(candidate_factors)}): {candidate_factors}")

    # Build price panel
    metrics = metrics.sort_values(["symbol", "date"])
    prices = prices.sort_values(["symbol", "date"])
    price_pivot = prices.pivot_table(index="date", columns="symbol", values="close")
    trading_dates = price_pivot.index.sort_values()

    # IC-based factor selection and weighting
    factors, ic_weights = compute_ic_weights(
        metrics, prices, candidate_factors, price_pivot, trading_dates, cfg
    )
    print(f"\nSelected factors ({len(factors)}): {factors}")
    print(f"IC weights: {dict(zip(factors, ic_weights.round(4)))}")

    # Dollar volume rolling
    dv_pivot = prices.pivot_table(index="date", columns="symbol", values="dollar_volume")
    dv_rolling = dv_pivot.rolling(20, min_periods=5).mean()

    # Rebalance dates
    day_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4}
    rebal_day = day_map[cfg["rebalance_day"]]
    test_start = pd.Timestamp(cfg["test_start"])
    test_end = pd.Timestamp(cfg["test_end"])
    rebal_dates = [d for d in trading_dates if d.weekday() == rebal_day and test_start <= d <= test_end]
    print(f"Test period: {test_start.date()} to {test_end.date()}, {len(rebal_dates)} rebalances")

    # Walk-forward loop
    holdings = {}
    cash = float(cfg["capital"])
    portfolio_values = []
    all_dates = [d for d in trading_dates if test_start <= d <= test_end]
    rebal_set = set(rebal_dates)
    top_n = cfg["top_n"]
    leverage = cfg["leverage"]

    for date in all_dates:
        port_value = cash
        for sym, shares in holdings.items():
            if sym in price_pivot.columns:
                p = price_pivot.at[date, sym]
                if pd.notna(p):
                    port_value += shares * p

        portfolio_values.append({"date": date, "portfolio_value": port_value})

        if date not in rebal_set:
            continue

        # Latest metrics per symbol
        available = metrics[metrics["date"] <= date]
        latest = available.groupby("symbol").last().reset_index()

        # Liquidity + price filter
        dv_today = dv_rolling.loc[date] if date in dv_rolling.index else pd.Series(dtype=float)
        tradeable = set()
        for sym in latest["symbol"]:
            if sym not in price_pivot.columns:
                continue
            p = price_pivot.at[date, sym]
            dv = dv_today.get(sym, 0)
            if pd.notna(p) and cfg["min_price"] <= p <= cfg["max_price"] and dv >= cfg["min_dollar_volume"]:
                tradeable.add(sym)
        candidates = latest[latest["symbol"].isin(tradeable)].copy()
        if len(candidates) < 5:
            continue

        # Drop rows missing >50% of factors
        candidates = candidates[candidates[factors].notna().sum(axis=1) >= len(factors) * 0.5]
        if len(candidates) < 5:
            continue

        # Rank-normalize each factor cross-sectionally, apply IC weights
        score = np.zeros(len(candidates))
        for i, f in enumerate(factors):
            vals = candidates[f].copy()
            vals = vals.fillna(vals.median())
            ranked = vals.rank(pct=True).values
            score += ranked * ic_weights[i]
        candidates["score"] = score

        # Top N
        top = candidates.nlargest(top_n, "score")
        if len(top) == 0:
            continue

        # Equal weight, no leverage in backtest evaluation
        w = 1.0 / len(top)

        # Liquidate
        for sym, shares in holdings.items():
            if sym in price_pivot.columns:
                p = price_pivot.at[date, sym]
                if pd.notna(p):
                    cash += shares * p
        holdings = {}

        # Buy
        for _, row in top.iterrows():
            sym = row["symbol"]
            p = price_pivot.at[date, sym]
            if pd.notna(p) and p > 0:
                dollar_alloc = port_value * w
                shares = int(dollar_alloc / p)
                if shares > 0:
                    holdings[sym] = shares
                    cash -= shares * p

    result = pd.DataFrame(portfolio_values)
    if len(result) == 0:
        return None, {}

    # Stats
    pv = result["portfolio_value"].values
    total_return = (pv[-1] / pv[0] - 1) * 100
    years = (result["date"].iloc[-1] - result["date"].iloc[0]).days / 365.25
    cagr = ((pv[-1] / pv[0]) ** (1 / years) - 1) * 100 if years > 0 and pv[-1] > 0 else 0

    peak = np.maximum.accumulate(pv)
    dd = (pv - peak) / peak
    max_dd = dd.min() * 100

    daily_ret = np.diff(pv) / pv[:-1]
    sharpe = np.sqrt(252) * daily_ret.mean() / daily_ret.std() if daily_ret.std() > 0 else 0

    stats = {
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 3),
        "final_value": round(pv[-1], 2),
        "n_factors": len(factors),
        "factors": factors,
        "ic_weights": dict(zip(factors, ic_weights.round(4).tolist())),
        "years": round(years, 1),
        "n_rebalances": len(rebal_dates),
    }
    return result, stats


def main():
    cfg = load_config()
    print(f"Config: top_n={cfg['top_n']}, leverage={cfg['leverage']}, "
          f"rebalance={cfg['rebalance_day']}")
    result, stats = run_backtest(cfg)
    if result is None:
        print("FAIL: no data in test period")
        sys.exit(1)

    print("\n---")
    for k, v in stats.items():
        if k in ("factors", "ic_weights"):
            continue
        print(f"{k}: {v}")
    print(f"factors: {stats['factors']}")
    print(f"ic_weights: {stats['ic_weights']}")


if __name__ == "__main__":
    main()
