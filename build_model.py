"""
Build hourly alert probability table from alerts_snapshot.csv.
Output: model/hourly_probabilities.parquet (long format: oblast, hour, prob)

Timestamps converted UTC -> Europe/Kyiv (handles DST via zoneinfo).
Probability = fraction of minutes in hour h that oblast was under alert,
              averaged across all days in the dataset (occupancy metric).
"""

import pandas as pd
from zoneinfo import ZoneInfo
from collections import defaultdict
from pathlib import Path
import datetime

DATA_PATH = Path("data/alerts_snapshot.csv")
OUT_PATH = Path("model/hourly_probabilities.parquet")
TZ_KYIV = ZoneInfo("Europe/Kyiv")


def compute_hour_coverage(start_local: datetime.datetime, end_local: datetime.datetime) -> dict:
    """Return {hour_of_day: minutes_covered} for interval [start, end)."""
    coverage = defaultdict(float)
    # Snap to start of the hour containing start_local
    bucket = start_local.replace(minute=0, second=0, microsecond=0)
    while bucket < end_local:
        next_bucket = bucket + datetime.timedelta(hours=1)
        seg_start = max(bucket, start_local)
        seg_end = min(next_bucket, end_local)
        if seg_end > seg_start:
            coverage[bucket.hour] += (seg_end - seg_start).total_seconds() / 60.0
        bucket = next_bucket
    return coverage


def main():
    print("Loading CSV...")
    df = pd.read_csv(DATA_PATH, usecols=["oblast", "level", "started_at", "finished_at"])

    # Keep only oblast-level alerts
    df = df[df["level"] == "oblast"].copy()
    print(f"Oblast-level rows: {len(df)}")

    # Parse timestamps (they come as UTC offset-aware strings)
    df["started_at"] = pd.to_datetime(df["started_at"], utc=True)
    df["finished_at"] = pd.to_datetime(df["finished_at"], utc=True)

    # Drop any rows where timestamps are bad
    df = df.dropna(subset=["started_at", "finished_at"])
    df = df[df["finished_at"] > df["started_at"]]
    print(f"Rows after cleanup: {len(df)}")

    # Convert to Kyiv local time for hour-of-day bucketing
    df["start_kyiv"] = df["started_at"].dt.tz_convert(TZ_KYIV)
    df["end_kyiv"] = df["finished_at"].dt.tz_convert(TZ_KYIV)

    # Determine total dataset range in days (Kyiv calendar days)
    first_day = df["start_kyiv"].dt.date.min()
    last_day = df["end_kyiv"].dt.date.max()
    total_days = (last_day - first_day).days + 1
    print(f"Dataset range: {first_day} to {last_day} ({total_days} days)")

    # Accumulate covered minutes per (oblast, hour)
    print("Computing hour coverage (this may take ~30s)...")
    covered: dict[tuple, float] = defaultdict(float)  # (oblast, hour) -> minutes

    for _, row in df.iterrows():
        hour_cov = compute_hour_coverage(row["start_kyiv"], row["end_kyiv"])
        for h, mins in hour_cov.items():
            covered[(row["oblast"], h)] += mins

    # Build probability table
    # denominator: total_days * 60 minutes per hour bucket
    denom = total_days * 60.0

    oblasts = df["oblast"].unique()
    records = []
    for oblast in sorted(oblasts):
        for hour in range(24):
            prob = covered.get((oblast, hour), 0.0) / denom
            records.append({"oblast": oblast, "hour": hour, "prob": prob})

    result = pd.DataFrame(records)
    print(f"Table shape: {result.shape}")
    print(result.groupby("oblast")["prob"].mean().sort_values(ascending=False).head(5))

    OUT_PATH.parent.mkdir(exist_ok=True)
    result.to_parquet(OUT_PATH, index=False)
    print(f"Saved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
