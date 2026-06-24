"""
Shared utilities for the three-model comparison pipeline.
Builds the hourly binary state grid (1 = alert active, 0 = not) from the
alerts snapshot CSV, and exposes shared constants used across all model scripts.
"""

import numpy as np
import pandas as pd
from pathlib import Path
from datetime import timezone, timedelta

DATA_PATH = Path("data/alerts_snapshot.csv")
# MVP: fixed UTC+3 ("Kyiv" summer offset). We deliberately do NOT use the real
# Europe/Kyiv zone (which switches +2/+3 with DST) — no location/DST tracking.
TZ_KYIV = timezone(timedelta(hours=3))

# All three models are evaluated on the SAME temporal split.
# Train: [dataset start, SPLIT_DATE)   Test: [SPLIT_DATE, dataset end]
# Change here to re-split everywhere at once.
SPLIT_DATE = "2025-01-01"

# Geographic neighbors — CSV oblast names.
# Used as a LightGBM feature (number of active neighbors at time t).
NEIGHBORS: dict[str, list[str]] = {
    "Cherkaska oblast": ["Kyivska oblast", "Kyiv City", "Poltavska oblast", "Kirovohradska oblast", "Vinnytska oblast", "Chernihivska oblast"],
    "Chernihivska oblast": ["Kyivska oblast", "Kyiv City", "Sumska oblast", "Poltavska oblast", "Zhytomyrska oblast"],
    "Chernivetska oblast": ["Ivano-Frankivska oblast", "Ternopilska oblast", "Khmelnytska oblast", "Vinnytska oblast", "Odeska oblast", "Zakarpatska oblast"],
    "Dnipropetrovska oblast": ["Zaporizka oblast", "Khersonska oblast", "Mykolaivska oblast", "Kirovohradska oblast", "Poltavska oblast", "Kharkivska oblast", "Donetska oblast"],
    "Donetska oblast": ["Zaporizka oblast", "Dnipropetrovska oblast", "Kharkivska oblast", "Luhanska oblast"],
    "Ivano-Frankivska oblast": ["Lvivska oblast", "Ternopilska oblast", "Chernivetska oblast", "Zakarpatska oblast"],
    "Kharkivska oblast": ["Sumska oblast", "Poltavska oblast", "Dnipropetrovska oblast", "Luhanska oblast", "Donetska oblast"],
    "Khersonska oblast": ["Zaporizka oblast", "Dnipropetrovska oblast", "Mykolaivska oblast"],
    "Khmelnytska oblast": ["Rivnenska oblast", "Ternopilska oblast", "Vinnytska oblast", "Zhytomyrska oblast", "Chernivetska oblast"],
    "Kirovohradska oblast": ["Cherkaska oblast", "Poltavska oblast", "Dnipropetrovska oblast", "Mykolaivska oblast", "Odeska oblast", "Vinnytska oblast"],
    "Kyiv City": ["Kyivska oblast"],
    "Kyivska oblast": ["Chernihivska oblast", "Sumska oblast", "Poltavska oblast", "Cherkaska oblast", "Zhytomyrska oblast", "Kyiv City"],
    "Luhanska oblast": ["Kharkivska oblast", "Donetska oblast"],
    "Lvivska oblast": ["Volynska oblast", "Rivnenska oblast", "Ternopilska oblast", "Ivano-Frankivska oblast", "Zakarpatska oblast"],
    "Mykolaivska oblast": ["Odeska oblast", "Khersonska oblast", "Dnipropetrovska oblast", "Kirovohradska oblast", "Vinnytska oblast"],
    "Odeska oblast": ["Mykolaivska oblast", "Kirovohradska oblast", "Vinnytska oblast", "Chernivetska oblast"],
    "Poltavska oblast": ["Sumska oblast", "Kharkivska oblast", "Dnipropetrovska oblast", "Kirovohradska oblast", "Cherkaska oblast", "Kyivska oblast", "Chernihivska oblast"],
    "Rivnenska oblast": ["Volynska oblast", "Zhytomyrska oblast", "Khmelnytska oblast", "Lvivska oblast"],
    "Sumska oblast": ["Poltavska oblast", "Kharkivska oblast", "Chernihivska oblast", "Kyivska oblast"],
    "Ternopilska oblast": ["Khmelnytska oblast", "Lvivska oblast", "Ivano-Frankivska oblast", "Chernivetska oblast", "Vinnytska oblast", "Rivnenska oblast"],
    "Vinnytska oblast": ["Khmelnytska oblast", "Ternopilska oblast", "Chernivetska oblast", "Odeska oblast", "Mykolaivska oblast", "Kirovohradska oblast", "Cherkaska oblast", "Zhytomyrska oblast"],
    "Volynska oblast": ["Rivnenska oblast", "Lvivska oblast", "Zhytomyrska oblast"],
    "Zakarpatska oblast": ["Lvivska oblast", "Ivano-Frankivska oblast", "Chernivetska oblast"],
    "Zaporizka oblast": ["Donetska oblast", "Dnipropetrovska oblast", "Khersonska oblast", "Mykolaivska oblast"],
    "Zhytomyrska oblast": ["Kyivska oblast", "Vinnytska oblast", "Khmelnytska oblast", "Rivnenska oblast", "Volynska oblast", "Chernihivska oblast"],
}


def load_alerts() -> pd.DataFrame:
    """Load and clean oblast-level alerts; add start_kyiv / end_kyiv columns."""
    df = pd.read_csv(DATA_PATH, usecols=["oblast", "level", "started_at", "finished_at"])
    df = df[df["level"] == "oblast"].copy()
    df["started_at"] = pd.to_datetime(df["started_at"], utc=True)
    df["finished_at"] = pd.to_datetime(df["finished_at"], utc=True)
    df = df.dropna(subset=["started_at", "finished_at"])
    df = df[df["finished_at"] > df["started_at"]]
    df["start_kyiv"] = df["started_at"].dt.tz_convert(TZ_KYIV)
    df["end_kyiv"] = df["finished_at"].dt.tz_convert(TZ_KYIV)
    return df


def build_hourly_grid(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a (n_hours × n_oblasts) int8 DataFrame.
    Index: hourly tz-aware DatetimeIndex (Kyiv).
    Columns: sorted oblast names.
    Value 1 means at least one alert was active during that hour, 0 otherwise.

    Uses numpy index arithmetic for speed (~130K rows, each covering ~1-3 hours).
    """
    oblasts = sorted(df["oblast"].unique())
    oblast_to_col = {o: i for i, o in enumerate(oblasts)}

    hour_ns = 3_600_000_000_000  # 1 hour in nanoseconds

    min_hour: pd.Timestamp = df["start_kyiv"].min().floor("h")
    max_end_ns = df["end_kyiv"].max().value
    max_hour_ns = (max_end_ns - 1) // hour_ns * hour_ns
    max_hour = pd.Timestamp(max_hour_ns, tz="UTC").tz_convert(TZ_KYIV)

    min_ns = min_hour.value
    n_hours = int((max_hour.value - min_ns) // hour_ns) + 1

    grid_np = np.zeros((n_hours, len(oblasts)), dtype=np.int8)

    for row in df.itertuples(index=False):
        # Use integer UTC-nanosecond arithmetic to avoid tz-aware floor() raising
        # Fixed UTC+3 means local-hour boundaries are exactly UTC boundaries
        # shifted by 3h, so integer UTC-ns bucketing is exact (no DST edge cases).
        first_idx = (row.start_kyiv.value - min_ns) // hour_ns
        last_idx = (row.end_kyiv.value - 1 - min_ns) // hour_ns  # -1 ns: exclude exact-hour endpoint
        col = oblast_to_col[row.oblast]
        grid_np[int(first_idx) : int(last_idx) + 1, col] = 1

    idx = pd.date_range(min_hour, periods=n_hours, freq="h", tz=TZ_KYIV)
    return pd.DataFrame(grid_np, index=idx, columns=oblasts)
