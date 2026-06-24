# Air Raid Alert Probability — Build Instructions

Instructions for an LLM to implement an educational time-series project that
estimates the probability of an air raid alert per Ukrainian oblast, based on a
**frequency baseline** over historical data, and exposes it through a **Streamlit**
app with a time-range selector and an interactive oblast map.

This is an educational MVP. Absolute predictive accuracy is **not** a goal.
Favor simplicity and reproducibility over sophistication.

---

## 1. Goal

Given a historical dataset of air raid alerts (start/end timestamps per oblast),
compute, for every oblast and every hour of the day, the probability that an
alert is active. Then let the user pick a range of hours and see, on a map of
Ukraine, the chance of an alert in each oblast for that range (shown on hover).

There is **no machine learning model and no neural network**. The "model" is a
frequency table computed directly from the data. This is intentional.

---

## 2. Tech Stack

- **Python 3.11+**
- **pandas** + **pyarrow** — data processing, Parquet I/O
- **streamlit** — GUI (priority: speed of development)
- **plotly** — interactive choropleth map with native hover tooltips

No Django/Flask, no database, no auth, no JS/HTML. Everything is Python.

`requirements.txt`:
```
streamlit
pandas
pyarrow
plotly
```

---

## 3. Architecture

Two clearly separated stages: an **offline build step** that produces a
probability table, and the **Streamlit app** that only reads and displays it.
The app must never recompute the baseline from the raw CSV at request time.

```
air-raid-baseline/
├── data/
│   ├── alerts_snapshot.csv          # static snapshot from Vadimkin dataset
│   └── ukraine_oblasts.geojson      # static admin-1 (oblast) boundaries
├── build_model.py                   # CSV -> probability table -> Parquet
├── model/
│   └── hourly_probabilities.parquet # output of build_model.py
├── app.py                           # Streamlit app (reads Parquet + GeoJSON)
├── requirements.txt
└── README.md
```

Data flow:

```
alerts_snapshot.csv ──build_model.py──> hourly_probabilities.parquet ──app.py──> Streamlit UI
                                                                          │
ukraine_oblasts.geojson ──────────────────────────────────────────────────┘
```

---

## 4. Data Source (snapshot, not live)

Primary source: **Vadimkin/ukrainian-air-raid-sirens-dataset** on GitHub
(`datasets/official_data_en.csv`). All timestamps are in **UTC**. Rows flagged
`naive=True` have `finished_at = started_at + 30 minutes` — trust the value as-is.

This project uses a **static snapshot**: download the CSV once into
`data/alerts_snapshot.csv` and commit it. Do **not** implement live fetching,
Telegram scraping, or daily updates — that is explicitly out of scope. The
snapshot guarantees reproducible results.

> **Do not assume the exact column names.** Before writing any processing code,
> read and print the CSV header and the set of distinct oblast names. Adapt to
> the real schema. Expected fields are an oblast/region name, a start timestamp,
> and an end timestamp.

---

## 5. Probability Definition (the baseline)

For each oblast and each hour-of-day `h` (0–23), define:

> **p(oblast, h) = expected fraction of time during hour `h` that an alert is
> active in that oblast, averaged over all days in the dataset.**

This is *occupancy*: it answers "at a random moment during hour `h`, how likely
is it that this oblast is under an alert?" It is well-defined, lies in `[0, 1]`,
and discriminates between oblasts better than "at least one alert occurred."

Computation:
1. Parse each alert as an interval `[started_at, finished_at)`.
2. **Convert timestamps from UTC to `Europe/Kyiv`** so that "20:00" means local
   evening to the user. Use `zoneinfo` so DST is handled. (Document this choice;
   keeping UTC is acceptable but less intuitive.)
3. For each oblast, split alert intervals across hour-of-day buckets and sum the
   covered minutes per `(oblast, hour-of-day)` over the whole dataset.
4. Divide by the total observed minutes for that hour-of-day (number of days in
   range × 60) to get `p(oblast, h)`.

Output `model/hourly_probabilities.parquet` in **long format**:

| oblast | hour | prob |
|--------|------|------|
| ...    | 0–23 | 0.0–1.0 |

24 rows per oblast.

---

## 6. Features (required)

### 6.1 Time-range selector
- A range control over hours of the day (0–23), e.g. `st.slider` returning a
  `(start, end)` tuple.
- **Support overnight wrap-around**: if `start > end`, the window wraps past
  midnight (e.g. `22 → 4` selects 22,23,0,1,2,3). Night raids are the most
  interesting case, so this matters. Build the set of selected hours `H`
  accordingly.

### 6.2 Interactive oblast map
- Plotly choropleth of Ukraine using `data/ukraine_oblasts.geojson`.
- For each oblast, the displayed probability for the selected window is
  `mean( prob for hour in H )` from the table.
- Color each oblast by that probability (continuous scale, fixed range `0–1`).
- **On hover**, show the oblast name and the alert chance as a percentage
  (e.g. "Lviv oblast — Alert chance: 37.4%") via a `hovertemplate`.
- Render with `st.plotly_chart(fig, use_container_width=True)`. Hover works
  natively in Streamlit; no extra wiring needed.

---

## 7. Build Sequence

Not formal phases — just an ordered checklist so each piece is verified before
the next is built.

1. **Setup.** Create venv, install `requirements.txt`. Download the snapshot CSV
   and an admin-1 (oblast-level) Ukraine GeoJSON into `data/`.
2. **Inspect & reconcile names.** Print the CSV columns and distinct oblast
   names. Print the oblast names from the GeoJSON `properties`. Build an explicit
   normalization map so the two name sets match exactly. **This is the most
   error-prone step — do it before anything else.** Mismatched names = blank map.
3. **build_model.py.** Parse intervals → convert to `Europe/Kyiv` → compute
   `p(oblast, h)` occupancy → write `model/hourly_probabilities.parquet`.
4. **(Optional) Validation.** Split the data by time (compute probabilities on
   the earlier portion, check them against observed frequencies in the later
   portion). Print one reliability number, e.g. a **Brier score** of predicted
   vs. actual. Useful for the project defense; not required for the app to run.
5. **app.py.** Load Parquet + GeoJSON behind `@st.cache_data`. Add the hour-range
   slider, compute per-oblast probability for the selected window, render the
   choropleth with hover. Add a short title and a one-line caption stating the
   data snapshot date and that times are local (Kyiv).

---

## 8. Implementation Notes / Gotchas

- **Caching is mandatory, not optional.** Streamlit re-runs the whole script on
  every interaction. Wrap data and GeoJSON loading in `@st.cache_data`, or the UI
  will re-read files on every slider move and feel sluggish.
- **Oblast name matching** between the dataset and the GeoJSON is the #1 failure
  mode (see step 2). Keep the mapping explicit and in one place.
- **Crimea / occupied territories** may be missing or have no data — handle
  absent oblasts gracefully (render them with a neutral "no data" color, not an
  error).
- **Fixed color scale `0–1`** so colors are comparable across different selected
  windows. Do not auto-rescale per selection.
- **Timezone:** convert once, in `build_model.py`. The app should not touch
  timezones.

---

## 9. Out of Scope (do not build)

- Live / daily data updates, Telegram or API fetching.
- Any ML model, gradient boosting, Markov chains, or neural networks.
- Day-of-week or date-range conditioning (hour-of-day only for this MVP).
- Authentication, multi-page apps, deployment configuration, databases.
- Forecasting future-dated alerts. The output is a historical-frequency
  probability, presented as a chance — not a calendar forecast.
