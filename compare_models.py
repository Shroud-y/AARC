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
    brier_baseline, _ = compute_baseline_brier(train_grid, test_grid)

    # ── 2. Markov ─────────────────────────────────────────────────────────────
    markov = pd.read_parquet(MARKOV_PREDS)
    brier_markov = float(((markov["y_pred"] - markov["y_true"]) ** 2).mean())

    # ── 3. LightGBM ────────────────────────────────────────────────────────────
    lgbm = pd.read_parquet(LGBM_PREDS)
    brier_lgbm = float(((lgbm["y_pred"] - lgbm["y_true"]) ** 2).mean())

    # ── Alignment check ────────────────────────────────────────────────────────
    n_test_cells = len(test_grid) * len(test_grid.columns)
    assert len(markov) == n_test_cells, f"Markov row count mismatch: {len(markov)} vs {n_test_cells}"
    assert len(lgbm) == n_test_cells, f"LightGBM row count mismatch: {len(lgbm)} vs {n_test_cells}"

    # ── Results ────────────────────────────────────────────────────────────────
    print()
    print("=" * 55)
    print(f"  Brier Score Comparison  (test: {SPLIT_DATE} to end)")
    print(f"  Test set: {n_test_cells:,} (oblast × hour) pairs")
    print("=" * 55)
    print(f"  {'Model':<25}  {'Brier Score':>12}  {'vs Baseline':>12}")
    print("  " + "-" * 51)
    models = [
        ("Frequency baseline", brier_baseline),
        ("Markov chain",       brier_markov),
        ("LightGBM",           brier_lgbm),
    ]
    for name, score in models:
        delta = score - brier_baseline
        arrow = f"{'-' if delta < 0 else '+' if delta > 0 else ' '}{abs(delta):.5f}"
        print(f"  {name:<25}  {score:>12.5f}  {arrow:>12}")
    print("=" * 55)
    print()
    best = min(models, key=lambda x: x[1])
    print(f"  Best model: {best[0]}  (Brier = {best[1]:.5f})")
    print()
    print("  Note: Brier score range 0 (perfect) to 1 (worst).")
    print("  Random (p=0.5 always) ~= 0.25000")
    naive_rate = float(test_grid.values.mean())
    brier_always_zero = float((test_grid.values ** 2).mean())
    brier_base_rate = naive_rate ** 2 + (1 - naive_rate) ** 2 * 0  # easier:
    brier_base_rate = float(((naive_rate - test_grid.values) ** 2).mean())
    print(f"  Always-predict-0      ~= {brier_always_zero:.5f}  (base rate {naive_rate:.3f})")

    # Save scores for Streamlit app to display without recomputing
    scores_data = {
        "split_date": SPLIT_DATE,
        "test_cells": n_test_cells,
        "alert_base_rate": round(naive_rate, 5),
        "brier_always_zero": round(brier_always_zero, 5),
        "models": [
            {"name": name, "brier": round(score, 5)}
            for name, score in models
        ],
    }
    scores_path = Path("model/model_scores.json")
    with open(scores_path, "w") as f:
        json.dump(scores_data, f, indent=2)
    print(f"\nScores saved -> {scores_path}")


if __name__ == "__main__":
    main()
