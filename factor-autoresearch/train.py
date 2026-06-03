"""
Factor model — the file the agent modifies.

Baseline: IC-weighted rank-normalized scoring.
"""

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.feature_selection import VarianceThreshold
from prepare import DataCache, evaluate, compute_stats, TEST_START, ID_COLS

# ── Hyperparameters (tune these) ──────────────────────────────────────────

TOP_N = 75
FORWARD_DAYS = 10
REBALANCE_WEEKDAY = 2          # 2=Wednesday
TRAIN_START = "2018-04-01"
IC_THRESHOLD = 0.01
IC_HALFLIFE = 13               # weeks
VARIANCE_THRESHOLD = 0.01
CORRELATION_THRESHOLD = 0.9
MIN_DV = 1_000_000
MIN_PRICE = 1
MAX_PRICE = 10_000

# ── Factor selection ──────────────────────────────────────────────────────

def get_candidate_factors(metrics):
    numeric = [c for c in metrics.columns if c not in ID_COLS]
    X = metrics[numeric].fillna(0).values

    sel = VarianceThreshold(threshold=VARIANCE_THRESHOLD)
    sel.fit(X)
    retained = [c for c, k in zip(numeric, sel.get_support()) if k]

    corr = np.abs(np.corrcoef(metrics[retained].fillna(0).values, rowvar=False))
    drop = set()
    for i in range(len(retained)):
        for j in range(i + 1, len(retained)):
            if corr[i, j] > CORRELATION_THRESHOLD:
                drop.add(retained[j])
    return [c for c in retained if c not in drop]


def add_momentum_factors(data):
    """Add price momentum columns to metrics."""
    for period, name in [(63, "mom_3m"), (126, "mom_6m"), (252, "mom_12m")]:
        if name in data.metrics.columns:
            continue
        ret = data.price_pivot.pct_change(period)
        ret_stacked = ret.stack().rename(name)
        ret_stacked.index.names = ["date", "symbol"]
        data.metrics = data.metrics.merge(
            ret_stacked.reset_index(), on=["symbol", "date"], how="left"
        )
    return ["mom_3m", "mom_6m", "mom_12m"]


def compute_ic_weights(data, factors, train_dates):
    ic_history = {f: [] for f in factors}
    n_valid = 0

    for date in train_dates:
        idx = data.date_idx.get(date)
        if idx is None or idx + FORWARD_DAYS >= len(data.trading_dates):
            continue
        fwd_date = data.trading_dates[idx + FORWARD_DAYS]

        available = data.metrics[data.metrics["date"] <= date]
        latest = available.groupby("symbol").last()
        dv_today = data.dv_rolling.loc[date] if date in data.dv_rolling.index else pd.Series(dtype=float)

        valid_syms = [s for s in latest.index
                      if s in data.price_pivot.columns
                      and pd.notna(data.price_pivot.at[date, s])
                      and pd.notna(data.price_pivot.at[fwd_date, s])
                      and data.price_pivot.at[date, s] > MIN_PRICE
                      and dv_today.get(s, 0) >= MIN_DV]
        if len(valid_syms) < 30:
            continue

        fwd_ret = (data.price_pivot.loc[fwd_date, valid_syms] /
                   data.price_pivot.loc[date, valid_syms] - 1).dropna()
        valid_syms = list(fwd_ret.index)
        if len(valid_syms) < 30:
            continue

        n_valid += 1
        for f in factors:
            vals = latest.loc[valid_syms, f]
            mask = vals.notna() & fwd_ret.notna()
            if mask.sum() < 20:
                ic_history[f].append(np.nan)
                continue
            ic, _ = spearmanr(vals[mask], fwd_ret[mask])
            ic_history[f].append(ic)

    # Select by IC with exponential decay
    decay = np.array([2 ** (-(n_valid - 1 - i) / IC_HALFLIFE) for i in range(n_valid)])
    selected = []
    weights = []
    for f in factors:
        ics = np.array(ic_history[f])
        valid = ~np.isnan(ics)
        if valid.sum() < 10:
            continue
        w = decay[valid]
        w = w / w.sum()
        mean_ic = np.average(ics[valid], weights=w)
        if abs(mean_ic) >= IC_THRESHOLD:
            selected.append(f)
            weights.append(mean_ic)
            print(f"  {f:40s} IC={mean_ic:+.4f}")
        else:
            print(f"  {f:40s} IC={mean_ic:+.4f} (dropped)")

    weights = np.array(weights)
    if len(weights) > 0:
        weights = weights / np.abs(weights).sum()
    return selected, weights


# ── Scoring function ──────────────────────────────────────────────────────

def make_score_fn(sel_factors, ic_weights):
    """Return a scoring function for evaluate()."""
    def score_fn(candidates, data, date):
        score = np.zeros(len(candidates))
        for i, f in enumerate(sel_factors):
            vals = candidates[f].fillna(candidates[f].median())
            score += vals.rank(pct=True).values * ic_weights[i]
        return pd.Series(score, index=candidates.index)
    return score_fn


def evaluate_score_weighted(score_fn, top_n, rebal_dates, forward_days, data,
                            min_price, max_price, min_dv):
    """Score-weighted portfolio evaluation (no leverage)."""
    from prepare import get_tradeable_symbols, TEST_START
    weekly_rets = []
    for date in rebal_dates:
        if date < TEST_START or date > pd.Timestamp("2026-04-17"):
            continue
        idx = data.date_idx.get(date)
        if idx is None or idx + forward_days >= len(data.trading_dates):
            continue
        fwd_date = data.trading_dates[idx + forward_days]

        available = data.metrics[data.metrics["date"] <= date]
        latest = available.groupby("symbol").last()
        tradeable = get_tradeable_symbols(data, date, fwd_date, min_price, max_price, min_dv)
        candidates = latest.loc[latest.index.isin(tradeable)]
        if len(candidates) < top_n:
            continue

        scores = score_fn(candidates, data, date).dropna()
        if len(scores) < top_n:
            continue

        top = scores.nlargest(top_n)
        shifted = top - top.min() + 1e-6
        weights = shifted / shifted.sum()  # sums to 1.0

        fwd_ret = data.price_pivot.loc[fwd_date, top.index] / data.price_pivot.loc[date, top.index] - 1
        ret = (weights * fwd_ret).sum()
        weekly_rets.append(ret)

    return np.array(weekly_rets)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    data = DataCache.get()

    train_start = pd.Timestamp(TRAIN_START)
    rebal_dates = data.all_weekdays[REBALANCE_WEEKDAY]
    # Biweekly: take every other rebalance date
    rebal_dates = rebal_dates[::2]
    train_dates = [d for d in rebal_dates if train_start <= d < TEST_START]
    test_dates = [d for d in rebal_dates if TEST_START <= d <= pd.Timestamp("2026-04-17")]

    # Factor selection on training data
    train_metrics = data.metrics[data.metrics["date"] < TEST_START]
    candidates = get_candidate_factors(train_metrics)
    # Add momentum factors
    mom_factors = add_momentum_factors(data)
    candidates = candidates + mom_factors
    print(f"Candidate factors ({len(candidates)}): {candidates}")

    # IC computation
    sel_factors, ic_weights = compute_ic_weights(data, candidates, train_dates)
    print(f"\nSelected {len(sel_factors)} factors")

    if len(sel_factors) == 0:
        print("ERROR: no factors selected")
        return

    # Evaluate
    score_fn = make_score_fn(sel_factors, ic_weights)
    rets = evaluate(score_fn, TOP_N, test_dates, FORWARD_DAYS, data,
                    MIN_PRICE, MAX_PRICE, MIN_DV)
    stats = compute_stats(rets)
    stats["n_factors"] = len(sel_factors)

    # Print results
    print("\n---")
    for k in ["sharpe", "cagr_pct", "max_drawdown_pct", "total_return_pct",
              "volatility_pct", "n_factors", "n_weeks"]:
        v = stats[k]
        print(f"{k + ':':18s}{v}")


if __name__ == "__main__":
    main()
