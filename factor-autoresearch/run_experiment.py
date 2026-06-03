"""
Autoresearch experiment runner for factor model tuning.

Usage:
    python run_experiment.py                    # run with config.json
    python run_experiment.py --baseline         # run baseline and log it
    python run_experiment.py --desc "try X"     # run and log with description

Reads config.json, runs backtest, logs to results.tsv.
The metric to optimize is Sharpe ratio on the out-of-sample test period.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import load_config, run_backtest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--desc", default="", help="experiment description")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    desc = "baseline" if args.baseline else args.desc

    print(f"=== Experiment: {desc or 'unnamed'} ===")
    t0 = time.time()
    try:
        result, stats = run_backtest(cfg)
        elapsed = time.time() - t0
        if result is None:
            raise RuntimeError("No data in test period")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"CRASH: {e}")
        log_result("crash", 0, 0, 0, 0, desc or "unnamed", elapsed)
        sys.exit(1)

    print(f"\nCompleted in {elapsed:.1f}s")
    for k, v in stats.items():
        if k != "factors":
            print(f"  {k}: {v}")

    status = "keep"  # caller decides keep/discard
    log_result(
        status, stats["sharpe"], stats["cagr_pct"],
        stats["max_drawdown_pct"], stats["total_return_pct"],
        desc or "unnamed", elapsed
    )


def log_result(status, sharpe, cagr, max_dd, total_ret, desc, elapsed):
    tsv = Path(__file__).parent / "results.tsv"
    if not tsv.exists():
        tsv.write_text("sharpe\tcagr_pct\tmax_dd_pct\ttotal_return_pct\tstatus\ttime_s\tdescription\n")
    with open(tsv, "a") as f:
        f.write(f"{sharpe}\t{cagr}\t{max_dd}\t{total_ret}\t{status}\t{elapsed:.0f}\t{desc}\n")
    print(f"Logged to {tsv}")


if __name__ == "__main__":
    main()
