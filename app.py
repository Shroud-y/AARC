"""
Streamlit app: Ukraine air raid alert probability map.
Reads pre-built model/hourly_probabilities.parquet + data/ukraine_oblasts.geojson.
Never recomputes from raw CSV at runtime.
"""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

PARQUET_PATH = Path("model/hourly_probabilities.parquet")
GEOJSON_PATH = Path("data/ukraine_oblasts.geojson")
SCORES_PATH = Path("model/model_scores.json")

# Maps GeoJSON feature name:en -> CSV oblast name used in the model.
# Missing/None means no alert data for that region.
GEOJSON_TO_CSV: dict[str, str | None] = {
    "Cherkasy Oblast": "Cherkaska oblast",
    "Chernihiv Oblast": "Chernihivska oblast",
    "Chernivtsi Oblast": "Chernivetska oblast",
    "Autonomous Republic of Crimea": None,
    "Dnipropetrovsk Oblast": "Dnipropetrovska oblast",
    "Donetsk Oblast": "Donetska oblast",
    "Ivano-Frankivsk Oblast": "Ivano-Frankivska oblast",
    "Kharkiv Oblast": "Kharkivska oblast",
    "Kherson Oblast": "Khersonska oblast",
    "Khmelnytskyi Oblast": "Khmelnytska oblast",
    "Kiev Oblast": "Kyivska oblast",
    "Kirovohrad Oblast": "Kirovohradska oblast",
    "Luhansk Oblast": "Luhanska oblast",
    "Lviv Oblast": "Lvivska oblast",
    "Mykolaiv Oblast": "Mykolaivska oblast",
    "Odessa Oblast": "Odeska oblast",
    "Poltava Oblast": "Poltavska oblast",
    "Rivne Oblast": "Rivnenska oblast",
    "Sumy Oblast": "Sumska oblast",
    "Ternopil Oblast": "Ternopilska oblast",
    "Vinnytsia Oblast": "Vinnytska oblast",
    "Volyn Oblast": "Volynska oblast",
    "Zakarpattia Oblast": "Zakarpatska oblast",
    "Zaporizhia Oblast": "Zaporizka oblast",
    "Zhytomyr Oblast": "Zhytomyrska oblast",
}


@st.cache_data
def load_probabilities() -> pd.DataFrame:
    return pd.read_parquet(PARQUET_PATH)


@st.cache_data
def load_geojson() -> dict:
    with open(GEOJSON_PATH, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_scores() -> dict | None:
    if not SCORES_PATH.exists():
        return None
    with open(SCORES_PATH) as f:
        return json.load(f)


def selected_hours(start: int, end: int) -> list[int]:
    """Return list of hours for range [start, end] with overnight wrap-around."""
    if start <= end:
        return list(range(start, end + 1))
    # Wrap: e.g. 22 -> 4 means 22,23,0,1,2,3,4
    return list(range(start, 24)) + list(range(0, end + 1))


def compute_window_probs(prob_df: pd.DataFrame, hours: list[int]) -> pd.DataFrame:
    """Mean probability across selected hours, per oblast."""
    subset = prob_df[prob_df["hour"].isin(hours)]
    return subset.groupby("oblast", as_index=False)["prob"].mean()


def build_display_df(window_probs: pd.DataFrame) -> pd.DataFrame:
    """Join model probs onto GeoJSON name space for Plotly."""
    mapping_df = pd.DataFrame(
        [{"geojson_name": gn, "csv_name": cn} for gn, cn in GEOJSON_TO_CSV.items()]
    )
    merged = mapping_df.merge(
        window_probs.rename(columns={"prob": "alert_prob"}),
        left_on="csv_name",
        right_on="oblast",
        how="left",
    )
    merged["alert_pct"] = (merged["alert_prob"] * 100).round(1)
    return merged


def main():
    st.set_page_config(page_title="Ukraine Air Raid Alert Probability", layout="wide")
    st.title("Ukraine — Air Raid Alert Probability by Oblast")
    st.caption(
        "Data snapshot: Vadimkin/ukrainian-air-raid-sirens-dataset. "
        "Times are local (Kyiv, Europe/Kyiv). "
        "Probability = historical occupancy — not a forecast."
    )

    prob_df = load_probabilities()
    geojson = load_geojson()

    st.subheader("Select time window (hour of day, Kyiv time)")
    col1, col2 = st.columns([3, 1])
    with col1:
        hour_range = st.slider(
            "Hours (0 = midnight, 12 = noon)",
            min_value=0,
            max_value=23,
            value=(8, 20),
            step=1,
        )
    start_h, end_h = hour_range
    hours = selected_hours(start_h, end_h)

    with col2:
        if start_h <= end_h:
            st.metric("Hours selected", f"{end_h - start_h + 1}h ({start_h:02d}:00–{end_h:02d}:59)")
        else:
            n = len(hours)
            st.metric("Hours selected", f"{n}h (overnight: {start_h:02d}–{end_h:02d})")

    window_probs = compute_window_probs(prob_df, hours)
    display_df = build_display_df(window_probs)

    fig = px.choropleth(
        display_df,
        geojson=geojson,
        locations="geojson_name",
        featureidkey="properties.name:en",
        color="alert_prob",
        color_continuous_scale="Reds",
        range_color=(0, 1),
        labels={"alert_prob": "Alert probability"},
        custom_data=["geojson_name", "alert_pct"],
    )
    fig.update_traces(
        hovertemplate="<b>%{customdata[0]}</b><br>Alert chance: %{customdata[1]}%<extra></extra>"
    )
    fig.update_geos(
        fitbounds="locations",
        visible=False,
    )
    fig.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        coloraxis_colorbar=dict(title="Probability", tickformat=".0%"),
        height=600,
    )

    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Raw probability table"):
        st.dataframe(
            display_df[["geojson_name", "alert_pct"]]
            .rename(columns={"geojson_name": "Oblast", "alert_pct": "Alert chance (%)"})
            .sort_values("Alert chance (%)", ascending=False)
            .reset_index(drop=True),
            use_container_width=True,
        )

    # ── Model comparison ──────────────────────────────────────────────────────
    st.divider()
    st.subheader("Model Comparison — Brier Score")

    scores = load_scores()
    if scores is None:
        st.info("Run `python compare_models.py` to generate model_scores.json.")
    else:
        baseline_brier = scores["models"][0]["brier"]
        rows = []
        for m in scores["models"]:
            delta = m["brier"] - baseline_brier
            rows.append({
                "Model": m["name"],
                "Brier score": m["brier"],
                "vs Baseline": f"{'+' if delta > 0 else ''}{delta:.5f}",
            })
        scores_df = pd.DataFrame(rows)

        st.dataframe(
            scores_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Brier score": st.column_config.NumberColumn(format="%.5f"),
            },
        )
        st.caption(
            f"Test split: {scores['split_date']} to end of dataset "
            f"({scores['test_cells']:,} oblast-hour pairs). "
            f"Brier score: lower is better (0 = perfect, 0.25 = random). "
            f"Alert base rate: {scores['alert_base_rate']:.1%}. "
            f"Always-predict-0 baseline: {scores['brier_always_zero']:.5f}."
        )


if __name__ == "__main__":
    main()
