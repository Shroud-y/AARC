"""
Three-model Brier score comparison on the same temporal test split.

Models:
  1. Frequency baseline  — per-(oblast, hour-of-day) occupancy from TRAIN data only
  2. Markov chain        — P(alert | prev_state), transition matrix from TRAIN data
  3. LightGBM            — binary classifier with lag/context features, trained on TRAIN data

All three evaluate on IDENTICAL test rows: timestamps >= SPLIT_DATE.

LEAKAGE CHECK:
  - Baseline probabilities computed from train_grid (index < SPLIT_DATE) only.
  - Markov / LightGBM predictions loaded from parquet files produced by their
    respective build_*.py scripts, which enforce the same temporal boundary.
  - If any model shows Brier ≈ 0, that is a data-leakage bug, not a success.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.metrics import average_precision_score
from utils import load_alerts, build_hourly_grid, SPLIT_DATE, TZ_KYIV

MARKOV_PREDS = Path("model/markov_test_preds.parquet")
LGBM_PREDS = Path("model/lgbm_test_preds.parquet")


def compute_baseline_brier(train_grid: pd.DataFrame, test_grid: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    """
    Frequency baseline: for each (oblast, hour-of-day h), prediction =
    mean(train_grid rows where index.hour == h) for that oblast.
    Returns (brier_score, long-format predictions DataFrame).
    """
    # Compute per-(oblast, hour-of-day) frequency from TRAIN only
    freq = (
        train_grid
        .groupby(train_grid.index.hour)
        .mean()
    )  # shape: (24, n_oblasts), index = hour-of-day 0..23

    # Apply to test: each test row gets its hour-of-day's frequency
    pred_wide = test_grid.copy().astype(np.float64)
    for h in range(24):
        mask = test_grid.index.hour == h
        if mask.any():
            pred_wide.loc[mask] = freq.loc[h].values

    # Brier score
    brier = float(((pred_wide.values - test_grid.values) ** 2).mean())

    # Long format for optional inspection
    pred_long = pred_wide.reset_index().melt(
        id_vars="index", var_name="oblast", value_name="y_pred"
    ).rename(columns={"index": "timestamp"})
    true_long = test_grid.reset_index().melt(
        id_vars="index", var_name="oblast", value_name="y_true"
    ).rename(columns={"index": "timestamp"})
    preds = pred_long.merge(true_long, on=["timestamp", "oblast"])

    return brier, preds


def main():
    print("Building hourly grid...")
    df_alerts = load_alerts()
    grid = build_hourly_grid(df_alerts)

    split_ts = pd.Timestamp(SPLIT_DATE, tz=TZ_KYIV)
    train_grid = grid[grid.index < split_ts]
    test_grid = grid[grid.index >= split_ts]
    print(f"Train: {len(train_grid)} hours  |  Test: {len(test_grid)} hours  |  Split: {SPLIT_DATE}")

    # ── 1. Frequency baseline ──────────────────────────────────────────────────
    brier_baseline, base_preds = compute_baseline_brier(train_grid, test_grid)
    ap_baseline = float(average_precision_score(base_preds["y_true"], base_preds["y_pred"]))

    # ── 2. Markov ─────────────────────────────────────────────────────────────
    markov = pd.read_parquet(MARKOV_PREDS)
    brier_markov = float(((markov["y_pred"] - markov["y_true"]) ** 2).mean())
    ap_markov = float(average_precision_score(markov["y_true"], markov["y_pred"]))

    # ── 3. LightGBM ────────────────────────────────────────────────────────────
    lgbm = pd.read_parquet(LGBM_PREDS)
    brier_lgbm = float(((lgbm["y_pred"] - lgbm["y_true"]) ** 2).mean())
    ap_lgbm = float(average_precision_score(lgbm["y_true"], lgbm["y_pred"]))

    # ── Alignment check ────────────────────────────────────────────────────────
    n_test_cells = len(test_grid) * len(test_grid.columns)
    assert len(markov) == n_test_cells, f"Markov row count mismatch: {len(markov)} vs {n_test_cells}"
    assert len(lgbm) == n_test_cells, f"LightGBM row count mismatch: {len(lgbm)} vs {n_test_cells}"

    naive_rate = float(test_grid.values.mean())
    brier_always_zero = float((test_grid.values ** 2).mean())

    # ── Results ────────────────────────────────────────────────────────────────
    print()
    print("=" * 55)
    print(f"  Brier Score Comparison  (test: {SPLIT_DATE} to end)")
    print(f"  Test set: {n_test_cells:,} (oblast × hour) pairs")
    print("=" * 55)
    print(f"  {'Model':<25}  {'Brier':>10}  {'PR-AUC':>10}  {'vs Base':>10}")
    print("  " + "-" * 61)
    # (name, brier [lower better], pr_auc [higher better])
    models = [
        ("Frequency baseline", brier_baseline, ap_baseline),
        ("Markov chain",       brier_markov,   ap_markov),
        ("LightGBM",           brier_lgbm,     ap_lgbm),
    ]
    for name, score, ap in models:
        delta = score - brier_baseline
        arrow = f"{'-' if delta < 0 else '+' if delta > 0 else ' '}{abs(delta):.5f}"
        print(f"  {name:<25}  {score:>10.5f}  {ap:>10.5f}  {arrow:>10}")
    print("=" * 55)
    print()
    best = min(models, key=lambda x: x[1])
    print(f"  Best model (Brier):  {best[0]}  (Brier = {best[1]:.5f})")
    best_ap = max(models, key=lambda x: x[2])
    print(f"  Best model (PR-AUC): {best_ap[0]}  (PR-AUC = {best_ap[2]:.5f})")
    print()
    print("  Note: Brier range 0 (perfect) to 1 (worst); PR-AUC higher is better.")
    print(f"  PR-AUC baseline (random) = positive rate ~= {naive_rate:.5f}")
    print("  Random (p=0.5 always) ~= 0.25000")
    print(f"  Always-predict-0      ~= {brier_always_zero:.5f}  (base rate {naive_rate:.3f})")

    # Save scores for Streamlit app to display without recomputing
    scores_data = {
        "split_date": SPLIT_DATE,
        "dataset_start": str(grid.index.min().date()),
        "dataset_end": str(grid.index.max().date()),
        "test_cells": n_test_cells,
        "alert_base_rate": round(naive_rate, 5),
        "brier_always_zero": round(brier_always_zero, 5),
        "models": [
            {"name": name, "brier": round(score, 5), "pr_auc": round(ap, 5)}
            for name, score, ap in models
        ],
    }
    scores_path = Path("model/model_scores.json")
    with open(scores_path, "w") as f:
        json.dump(scores_data, f, indent=2)
    print(f"\nScores saved -> {scores_path}")

    print("y_true sums:",
      int(test_grid.values.sum()),
      int(markov["y_true"].sum()),
      int(lgbm["y_true"].sum()))



if __name__ == "__main__":
    main()
