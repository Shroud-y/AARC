"""
LightGBM binary classifier: predicts P(alert active at hour t) for each oblast.

Features computed at time t-1 (strictly before the target):
  - lag1          : alert active at t-1 (same oblast)
  - lag24         : alert active at t-24 (same hour yesterday)
  - hour_of_day   : hour-of-day of t  (0–23)
  - day_of_week   : day-of-week of t  (0=Mon … 6=Sun)
  - neighbor_count: active neighbor-oblast count at t-1
  - country_count : active oblast count across all 25 oblasts at t-1
  - oblast_id     : label-encoded oblast (categorical signal)

LEAKAGE GUARD:
  - All lag/aggregate features are shifted by ≥1 step relative to the target.
  - Model trained exclusively on rows where timestamp < SPLIT_DATE.
  - Test rows start at SPLIT_DATE; lag features reach back into the train period,
    which is allowed (past data is always observable at prediction time).
  - The train/test boundary is the same pd.Timestamp used in build_markov.py.
"""

import numpy as np
import pandas as pd
import lightgbm as lgb
from pathlib import Path
from utils import load_alerts, build_hourly_grid, SPLIT_DATE, TZ_KYIV, NEIGHBORS

OUT_PATH = Path("model/lgbm_test_preds.parquet")


def build_feature_matrix(grid: pd.DataFrame) -> pd.DataFrame:
    """
    Build long-format feature DataFrame.
    Each row = (timestamp t, oblast); target = state at t.
    All features use data at t-1 or earlier.
    """
    oblasts = list(grid.columns)
    oblast_id = {o: i for i, o in enumerate(oblasts)}

    # Country-wide active count at each t-1 (shifted 1 step relative to target t)
    country_lag1 = grid.shift(1).sum(axis=1)  # sum over all oblasts at t-1

    records = []
    for oblast in oblasts:
        s = grid[oblast]

        lag1 = s.shift(1)   # state at t-1
        lag24 = s.shift(24)  # state 24h before t

        neighbor_cols = NEIGHBORS.get(oblast, [])
        if neighbor_cols:
            neighbor_lag1 = grid[neighbor_cols].shift(1).sum(axis=1)
        else:
            neighbor_lag1 = pd.Series(0.0, index=grid.index)

        df_obl = pd.DataFrame({
            "timestamp": grid.index,
            "oblast": oblast,
            "oblast_id": oblast_id[oblast],
            "y": s.values,
            "lag1": lag1.values,
            "lag24": lag24.values,
            "hour_of_day": grid.index.hour,
            "day_of_week": grid.index.dayofweek,
            "neighbor_count": neighbor_lag1.values,
            "country_count": country_lag1.values,
        })
        records.append(df_obl)

    feat = pd.concat(records, ignore_index=True)
    # Drop rows where any lag is NaN (first 24 hours of the dataset)
    feat = feat.dropna(subset=["lag1", "lag24"])
    feat = feat.astype({
        "y": np.int8,
        "lag1": np.int8,
        "lag24": np.int8,
        "hour_of_day": np.int8,
        "day_of_week": np.int8,
        "neighbor_count": np.int16,
        "country_count": np.int16,
        "oblast_id": np.int8,
    })
    return feat


FEATURE_COLS = ["lag1", "lag24", "hour_of_day", "day_of_week",
                "neighbor_count", "country_count", "oblast_id"]


def main():
    print("Loading alerts and building hourly grid...")
    df_alerts = load_alerts()
    grid = build_hourly_grid(df_alerts)
    print(f"Grid shape: {grid.shape}")

    split_ts = pd.Timestamp(SPLIT_DATE, tz=TZ_KYIV)

    print("Building feature matrix...")
    feat = build_feature_matrix(grid)
    print(f"Feature matrix: {feat.shape}")

    # Temporal split — must match all other models exactly
    train_feat = feat[feat["timestamp"] < split_ts].copy()
    test_feat = feat[feat["timestamp"] >= split_ts].copy()
    print(f"Train rows: {len(train_feat)}   Test rows: {len(test_feat)}")

    X_train = train_feat[FEATURE_COLS].to_numpy(dtype=np.float32)
    y_train = train_feat["y"].to_numpy(dtype=np.float32)
    X_test = test_feat[FEATURE_COLS].to_numpy(dtype=np.float32)
    y_test = test_feat["y"].to_numpy(dtype=np.float32)

    print("Training LightGBM...")
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "n_estimators": 300,
        "min_child_samples": 50,
        "verbose": -1,
        "n_jobs": -1,
    }
    model = lgb.LGBMClassifier(**params)
    model.fit(X_train, y_train)

    y_pred = model.predict_proba(X_test)[:, 1].astype(np.float32)
    brier = float(((y_pred - y_test) ** 2).mean())
    print(f"\nLightGBM Brier score (test): {brier:.5f}")

    preds = pd.DataFrame({
        "timestamp": test_feat["timestamp"].values,
        "oblast": test_feat["oblast"].values,
        "y_true": y_test,
        "y_pred": y_pred,
    })
    OUT_PATH.parent.mkdir(exist_ok=True)
    preds.to_parquet(OUT_PATH, index=False)
    print(f"Saved predictions -> {OUT_PATH}  ({len(preds)} rows)")

    print("\nTop feature importances:")
    for feat_name, imp in sorted(
        zip(FEATURE_COLS, model.feature_importances_), key=lambda x: -x[1]
    ):
        print(f"  {feat_name:<20} {imp}")


if __name__ == "__main__":
    main()
