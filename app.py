"""
Streamlit app: Ukraine air raid alert probability map.
Reads pre-built model/hourly_probabilities.parquet + data/ukraine_oblasts.geojson.
Never recomputes from raw CSV at runtime.
"""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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
    merged["hover_label"] = merged["alert_pct"].apply(
        lambda x: "No data" if pd.isna(x) else f"{x}%"
    )
    return merged


def main():
    st.set_page_config(page_title="Advanced Air Raid Calendar", layout="wide")

    prob_df = load_probabilities()
    geojson = load_geojson()
    scores = load_scores()

    ds_start = (scores or {}).get("dataset_start", "2022-03-15")
    ds_end = (scores or {}).get("dataset_end", "2026-06-23")

    st.markdown(
        f"""
        <style>
        /* Fit everything on one screen, no page scroll */
        .block-container {{
            padding-top: 1.2rem;
            padding-bottom: 0rem;
            max-width: 100%;
        }}
        header[data-testid="stHeader"] {{ height: 0; background: transparent; }}
        [data-testid="stToolbar"] {{ display: none; }}
        footer {{ display: none; }}
        #MainMenu {{ display: none; }}
        /* Tighten vertical gaps between blocks */
        [data-testid="stVerticalBlock"] {{ gap: 0.4rem; }}
        /* Shift map + legend up without moving anything else */
        [data-testid="stPlotlyChart"] {{ margin-top: -90px; }}
        .aarc-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            padding-bottom: 6px;
            border-bottom: 1px solid rgba(128,128,128,0.25);
            margin-bottom: 16px;
        }}
        .aarc-meta {{
            font-size: 0.76rem;
            line-height: 1.55;
            opacity: 0.6;
        }}
        .aarc-title {{ text-align: right; }}
        .aarc-title h1 {{
            margin: 0 0 2px 0;
            font-size: 1.7rem;
            line-height: 1.1;
        }}
        .aarc-title p {{
            margin: 0;
            font-size: 0.8rem;
            opacity: 0.65;
        }}
        </style>
        <div class="aarc-header">
            <div class="aarc-meta">
                Source: Vadimkin/ukrainian-air-raid-sirens-dataset<br>
                Period: {ds_start} &ndash; {ds_end}<br>
                Times: UTC+3 (Kyiv, no DST) &middot; occupancy probability &middot; not a forecast
            </div>
            <div class="aarc-title">
                <h1>Advanced Air Raid Calendar</h1>
                <p>2-day AI-assisted pet-project for the KSE AI Agentic Summer School</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Slider (full width above main columns) ────────────────────────────────
    sl_col, metric_col = st.columns([4, 1])
    with sl_col:
        hour_range = st.slider(
            "Time window — hour of day (Kyiv time)",
            min_value=0,
            max_value=23,
            value=(8, 20),
            step=1,
        )
    start_h, end_h = hour_range
    hours = selected_hours(start_h, end_h)
    with metric_col:
        if start_h <= end_h:
            st.metric("Hours selected", f"{end_h - start_h + 1}h  ({start_h:02d}–{end_h:02d})")
        else:
            st.metric("Hours selected", f"{len(hours)}h  overnight")

    # ── Main row: map left, comparison right ──────────────────────────────────
    map_col, info_col = st.columns([3, 2])

    window_probs = compute_window_probs(prob_df, hours)
    display_df = build_display_df(window_probs)

    with map_col:
        fig = px.choropleth(
            display_df,
            geojson=geojson,
            locations="geojson_name",
            featureidkey="properties.name:en",
            color="alert_prob",
            color_continuous_scale="Reds",
            range_color=(0, 1),
            labels={"alert_prob": "Alert probability"},
            custom_data=["geojson_name", "hover_label"],
        )
        fig.update_traces(
            hovertemplate="<b>%{customdata[0]}</b><br>Alert chance: %{customdata[1]}<extra></extra>"
        )

        # px.choropleth leaves NaN-valued regions unfilled (invisible).
        # Draw them as a separate gray "No data" layer so Crimea etc. still appear.
        no_data = display_df[display_df["alert_prob"].isna()]
        if not no_data.empty:
            fig.add_trace(
                go.Choropleth(
                    geojson=geojson,
                    locations=no_data["geojson_name"],
                    featureidkey="properties.name:en",
                    z=[0] * len(no_data),
                    colorscale=[[0, "rgba(120,120,120,0.45)"], [1, "rgba(120,120,120,0.45)"]],
                    showscale=False,
                    marker_line_width=0.4,
                    marker_line_color="rgba(150,150,150,0.6)",
                    hovertemplate="<b>%{location}</b><br>No data<extra></extra>",
                )
            )

        fig.update_geos(
            fitbounds="locations",
            visible=False,
            bgcolor="rgba(0,0,0,0)",
            projection_type="mercator",
        )
        fig.update_layout(
            # Small top/right margin so colorbar title + 100% tick aren't clipped.
            margin={"r": 10, "t": 24, "l": 0, "b": 0},
            coloraxis_colorbar=dict(
                title=dict(text="Alert probability", side="top"),
                tickformat=".0%",
                len=0.7,
                thickness=12,
                yanchor="middle",
                y=0.5,
            ),
            # Taller box -> larger map fill, colorbar spans full height.
            height=580,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            dragmode=False,
        )
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"scrollZoom": False, "displayModeBar": False},
        )

    with info_col:
        st.subheader("Model Comparison — Brier & PR-AUC")
        if scores is None:
            st.info("Run `python compare_models.py` to generate model_scores.json.")
        else:
            rows = []
            for m in scores["models"]:
                rows.append({
                    "Model": m["name"],
                    "Brier score": m["brier"],
                    "PR-AUC": m.get("pr_auc"),
                })
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Brier score": st.column_config.NumberColumn(format="%.5f"),
                    "PR-AUC": st.column_config.NumberColumn(format="%.5f"),
                },
            )
            st.caption(
                f"Test split: {scores['split_date']} to end of dataset "
                f"({scores['test_cells']:,} oblast-hour pairs).  \n"
                f"Brier: lower is better (0 = perfect, 0.25 = random).  \n"
                f"PR-AUC: higher is better. With a ~{scores['alert_base_rate']:.0%} "
                f"alert base rate, alerts are rare — PR-AUC focuses on the positive "
                f"(alert) class, so it separates models better than Brier, which is "
                f"dominated by the many easy no-alert hours. PR-AUC of a random "
                f"classifier ~= the base rate ({scores['alert_base_rate']:.0%})."
            )


if __name__ == "__main__":
    main()
