"""
Markov chain model: two states per oblast (alert=1 / no-alert=0).
Transition matrix estimated from TRAIN data only.
Prediction for each test hour t: P(state_t=1 | state_{t-1}).

LEAKAGE GUARD: the transition matrix is fit on rows where index < SPLIT_DATE.
The test predictions use state_{t-1}, which is known at prediction time.
No future data is accessed.
"""

import numpy as np
import pandas as pd
from pathlib import Path
from utils import load_alerts, build_hourly_grid, SPLIT_DATE, TZ_KYIV

OUT_PATH = Path("model/markov_test_preds.parquet")


def estimate_transition(train_series: np.ndarray) -> np.ndarray:
    """
    Return 2x2 transition matrix T where T[i,j] = P(next=j | current=i).
    Laplace smoothing (alpha=1) prevents zero-probability entries.
    """
    counts = np.ones((2, 2))  # Laplace smoothing
    for i in range(len(train_series) - 1):
        counts[train_series[i], train_series[i + 1]] += 1
    return counts / counts.sum(axis=1, keepdims=True)


def main():
    print("Loading alerts and building hourly grid...")
    df = load_alerts()
    grid = build_hourly_grid(df)
    oblasts = list(grid.columns)
    print(f"Grid shape: {grid.shape}  ({grid.shape[0]} hours × {grid.shape[1]} oblasts)")

    split_ts = pd.Timestamp(SPLIT_DATE, tz=TZ_KYIV)
    train_grid = grid[grid.index < split_ts]
    test_grid = grid[grid.index >= split_ts]
    print(f"Train hours: {len(train_grid)}   Test hours: {len(test_grid)}")

    # Estimate one transition matrix per oblast on train data.
    transition: dict[str, np.ndarray] = {}
    for oblast in oblasts:
        transition[oblast] = estimate_transition(train_grid[oblast].to_numpy())

    # Build predictions for each test hour.
    # For test hour t, the feature is the state at t-1 (which may be in train or test).
    # We concatenate the last train row with the test grid to always have t-1 available.
    full_context = pd.concat([train_grid.iloc[[-1]], test_grid])  # last train row + all test

    records = []
    for oblast in oblasts:
        T = transition[oblast]
        series = full_context[oblast].to_numpy()  # length = 1 + len(test_grid)
        # series[0] is the last train state; series[1:] are test states (y_true)
        prev_states = series[:-1]   # state at t-1, index aligns with test_grid
        y_true = series[1:]          # state at t

        y_pred = T[prev_states, 1]   # P(next=1 | current=prev_state)

        df_out = pd.DataFrame({
            "timestamp": test_grid.index,
            "oblast": oblast,
            "y_true": y_true.astype(np.float32),
            "y_pred": y_pred.astype(np.float32),
        })
        records.append(df_out)

    preds = pd.concat(records, ignore_index=True)

    brier = float(((preds["y_pred"] - preds["y_true"]) ** 2).mean())
    print(f"\nMarkov Brier score (test): {brier:.5f}")

    OUT_PATH.parent.mkdir(exist_ok=True)
    preds.to_parquet(OUT_PATH, index=False)
    print(f"Saved predictions -> {OUT_PATH}  ({len(preds)} rows)")

    # ── Report plot (not the app): pooled 2x2 transition matrix heatmap ─────────
    # Pool transition counts across all oblasts from TRAIN data, then row-normalize.
    pooled = np.ones((2, 2))  # Laplace smoothing, same as per-oblast estimate
    for oblast in oblasts:
        s = train_grid[oblast].to_numpy()
        for i in range(len(s) - 1):
            pooled[s[i], s[i + 1]] += 1
    pooled = pooled / pooled.sum(axis=1, keepdims=True)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    labels = {
        (0, 0): "no-alert -> no-alert",
        (0, 1): "no-alert -> alert",
        (1, 0): "alert -> no-alert",
        (1, 1): "alert -> alert",
    }
    fig, ax = plt.subplots(figsize=(5.5, 4.5))
    im = ax.imshow(pooled, cmap="Reds", vmin=0, vmax=1)
    ax.set_xticks([0, 1], labels=["no-alert", "alert"])
    ax.set_yticks([0, 1], labels=["no-alert", "alert"])
    ax.set_xlabel("Next hour state")
    ax.set_ylabel("Current state")
    ax.set_title("Markov transition matrix (pooled, train)")
    for (i, j), p in np.ndenumerate(pooled):
        ax.text(j, i, f"{labels[(i, j)]}\n{p:.3f}",
                ha="center", va="center",
                color="white" if p > 0.5 else "black", fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="P(next | current)")
    fig.tight_layout()
    out_png = reports_dir / "markov_transitions.png"
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    print(f"Saved transition heatmap -> {out_png}")


if __name__ == "__main__":
    main()
