from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

import data_sources

hide_menu_style = """
        <style>
        #MainMenu {visibility: hidden;}
        header[data-testid="stHeader"] {display: none;}
        [data-testid="stMainBlockContainer"] {padding-top: 1rem;}
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)

st.set_page_config(
    page_title="Health dashboard",
    page_icon=str(Path(__file__).parent / "icons8-dumbbell-100.png"),
    layout="wide",
)

NYC = ZoneInfo("America/New_York")
CALORIE_GOAL = 2000
PROTEIN_GOAL = 140
CALORIE_EXPENDITURE = 2400  # TDEE, for daily deficit = expenditure - intake
BLUE = "#2a78d6"
GOOD_GREEN = "#0ca30c"
WEIGHT_PURPLE = "#8b5cf6"
WEIGHT_EWMA_SPAN = 7
WEIGHT_JOURNEY_START = date(2026, 7, 14)

# Semi-ring gauge geometry (shared by both rings). st.html() strips <svg>
# tags, so the ring is drawn with a conic-gradient masked down to a stroke,
# clipped to its top half - no SVG involved.
_RING_SIZE = 140  # circle diameter, px
_RING_THICKNESS = 14  # stroke width, px


def _progress_ring(
    value: float,
    goal: float,
    unit: str,
    label: str,
    accent: str,
    track: str,
    ink: str,
    muted_ink: str,
) -> str:
    fraction = max(0.0, min(value / goal, 1.0)) if goal else 0.0
    progress_deg = fraction * 180
    display_value = f"{value:,.0f}"
    radius = _RING_SIZE // 2
    return f"""
    <div style="flex:0 0 auto; text-align:center; font-family:inherit;">
      <div style="position:relative; width:{_RING_SIZE}px; height:{radius + 8}px; margin:0 auto; overflow:hidden;">
        <div style="position:absolute; top:0; left:0; width:{_RING_SIZE}px; height:{_RING_SIZE}px; border-radius:50%;
                    background:conic-gradient(from -90deg, {accent} 0deg {progress_deg:.1f}deg, {track} {progress_deg:.1f}deg 180deg, transparent 180deg 360deg);
                    -webkit-mask:radial-gradient(farthest-side, transparent calc(50% - {_RING_THICKNESS}px), #000 calc(50% - {_RING_THICKNESS}px));
                    mask:radial-gradient(farthest-side, transparent calc(50% - {_RING_THICKNESS}px), #000 calc(50% - {_RING_THICKNESS}px));">
        </div>
        <div style="position:absolute; left:0; right:0; bottom:0; text-align:center;">
          <div style="font-size:1.5rem; font-weight:600; color:{ink}; line-height:1.1;">{display_value}{unit}</div>
          <div style="font-size:0.72rem; color:{muted_ink};">of {goal:,.0f}{unit} {label}</div>
        </div>
      </div>
    </div>
    """


# -----------------------------------------------------------------------------
# Data loading


@st.cache_data(ttl="2m", show_spinner="Loading today's meals...")
def load_meals_today() -> list[dict]:
    return data_sources.get_meals_today()


@st.cache_data(ttl="15m", show_spinner="Loading workouts...")
def load_workouts(max_pages: int = 6) -> list[dict]:
    workouts = []
    for page in range(1, max_pages + 1):
        data = data_sources.get_workouts(page, 10)
        workouts.extend(data["workouts"])
        if page >= data.get("page_count", page):
            break
    return workouts


@st.cache_data(ttl="15m", show_spinner="Loading nutrition history...")
def load_meals_range(start_date: str, end_date: str) -> list[dict]:
    return data_sources.get_meals_by_date_range(start_date, end_date)


@st.cache_data(ttl="5m", show_spinner="Loading meal templates...")
def load_meal_templates() -> list[dict]:
    return data_sources.get_meal_templates()


@st.cache_data(ttl="15m", show_spinner="Loading body measurements...")
def load_body_measurements(max_pages: int = 10) -> list[dict]:
    measurements = []
    for page in range(1, max_pages + 1):
        data = data_sources.get_body_measurements(page, 10)
        measurements.extend(data["body_measurements"])
        if page >= data.get("page_count", page):
            break
    return measurements


@st.cache_data(ttl="15m", show_spinner="Syncing and loading transactions...")
def load_transactions(start_date: str, end_date: str) -> list[dict]:
    data_sources.sync_transactions()
    return data_sources.get_transactions_by_date_range(start_date, end_date)


# -----------------------------------------------------------------------------
# Header

body = st.container(gap="small")

# -----------------------------------------------------------------------------
# Today's progress (main display)

try:
    meals_today = load_meals_today()
except Exception as e:
    with body:
        st.error(f"Couldn't load today's meals: {e}", icon=":material/error:")
    st.stop()

calories_today = sum(m.get("calories") or 0 for m in meals_today)
protein_today = sum(m.get("protein_g") or 0 for m in meals_today)

is_dark = st.context.theme.type == "dark"
ring_track = "#383835" if is_dark else "#e1e0d9"
ring_ink = "#ffffff" if is_dark else "#0b0b0b"
ring_muted_ink = "#c3c2b7" if is_dark else "#52514e"
calorie_accent = "#3987e5" if is_dark else "#2a78d6"
protein_accent = "#199e70" if is_dark else "#1baf7a"

with body:
    with st.container(border=True):
        st.html(f"""
            <div style="display:flex; flex-wrap:nowrap; justify-content:center; gap:32px;">
              {_progress_ring(calories_today, CALORIE_GOAL, "", "kcal", calorie_accent, ring_track, ring_ink, ring_muted_ink)}
              {_progress_ring(protein_today, PROTEIN_GOAL, "g", "protein", protein_accent, ring_track, ring_ink, ring_muted_ink)}
            </div>
            """)

    with st.container(border=True):
        if meals_today:
            df_meals = pd.DataFrame(meals_today).sort_values("logged_at")
            df_meals["time"] = df_meals["logged_at"].apply(
                lambda s: datetime.fromisoformat(s).strftime("%-I:%M %p")
            )
            df_meals = df_meals.rename(
                columns={
                    "meal_type": "meal",
                    "desc": "description",
                    "calories": "kcal",
                    "protein_g": "protein (g)",
                    "carbs_g": "carbs (g)",
                    "fat_g": "fat (g)",
                }
            )
            st.dataframe(
                df_meals[
                    [
                        "time",
                        "meal",
                        "description",
                        "kcal",
                        "protein (g)",
                        "carbs (g)",
                        "fat (g)",
                    ]
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("Nothing logged yet today.")

        try:
            meal_templates = load_meal_templates()
        except Exception as e:
            meal_templates = []
            st.caption(f"Couldn't load meal templates: {e}")

        if meal_templates:
            st.divider()
            template_options = {
                (
                    f"{t['name']} — {t['calories']:.0f} kcal"
                    if t.get("calories") is not None
                    else t["name"]
                ): t["id"]
                for t in meal_templates
            }
            with st.form("log_from_template", border=False):
                cols = st.columns([3, 2, 2, 1])
                template_label = cols[0].selectbox(
                    "Template", template_options.keys(), label_visibility="collapsed"
                )
                meal_type = cols[1].selectbox(
                    "Meal type",
                    ["breakfast", "lunch", "dinner", "snack"],
                    label_visibility="collapsed",
                )
                log_time = cols[2].time_input(
                    "Time",
                    value=datetime.now(NYC).time(),
                    label_visibility="collapsed",
                )
                logged = cols[3].form_submit_button("Log", width="stretch")
            if logged:
                logged_at = datetime.combine(
                    datetime.now(NYC).date(), log_time, tzinfo=NYC
                ).isoformat()
                try:
                    data_sources.log_meal_from_template(
                        template_options[template_label], meal_type, logged_at
                    )
                except Exception as e:
                    st.error(f"Couldn't log meal: {e}", icon=":material/error:")
                else:
                    st.cache_data.clear()
                    st.rerun()

# -----------------------------------------------------------------------------
# Exercises per day + calorie deficit (past 7 days, excluding today)

try:
    workouts = load_workouts()
except Exception as e:
    with body:
        st.error(f"Couldn't load workouts: {e}", icon=":material/error:")
    workouts = []

today = datetime.now(NYC).date()
week_days = [today - timedelta(days=i) for i in range(7, 0, -1)]  # today-7 .. today-1

exercise_counts = {d: 0 for d in week_days}
for w in workouts:
    d = datetime.fromisoformat(w["start_time"]).astimezone(NYC).date()
    if d in exercise_counts:
        exercise_counts[d] += len(w.get("exercises") or [])

df_exercises = pd.DataFrame(
    {"date": week_days, "exercises": [exercise_counts[d] for d in week_days]}
)
df_exercises["day_label"] = df_exercises["date"].apply(lambda d: d.strftime("%a %-d"))

try:
    meals_week = load_meals_range(str(week_days[0]), str(week_days[-1]))
except Exception as e:
    with body:
        st.error(f"Couldn't load nutrition history: {e}", icon=":material/error:")
    meals_week = []

daily_calories_week = {d: 0.0 for d in week_days}
for m in meals_week:
    d = datetime.fromisoformat(m["logged_at"]).date()
    if d in daily_calories_week:
        daily_calories_week[d] += m.get("calories") or 0

df_deficit = pd.DataFrame(
    {
        "date": week_days,
        "deficit": [
            CALORIE_EXPENDITURE - daily_calories_week[d] for d in week_days
        ],
    }
)
df_deficit["day_label"] = df_deficit["date"].apply(lambda d: d.strftime("%a %-d"))
rolling_avg_deficit = df_deficit["deficit"].mean()

# -----------------------------------------------------------------------------
# Weight - EWMA since the start of the weight-loss journey

try:
    body_measurements = load_body_measurements()
except Exception as e:
    with body:
        st.error(f"Couldn't load body measurements: {e}", icon=":material/error:")
    body_measurements = []

df_weight_raw = pd.DataFrame(body_measurements)
if not df_weight_raw.empty:
    df_weight_raw["date"] = pd.to_datetime(df_weight_raw["date"]).dt.date
    df_weight_raw = df_weight_raw.sort_values(["date", "created_at"])
    weight_by_date = df_weight_raw.groupby("date")["weight_kg"].last()
else:
    weight_by_date = pd.Series(dtype=float)

df_weight = pd.DataFrame(
    {"date": pd.date_range(WEIGHT_JOURNEY_START, today, freq="D").date}
)
df_weight["weight_kg"] = df_weight["date"].map(weight_by_date)
# Weigh-ins are sparse, especially early on; fill gaps with the next
# available reading so the EWMA isn't skewed by missing days.
df_weight["weight_kg"] = df_weight["weight_kg"].bfill()
df_weight["ewma"] = df_weight["weight_kg"].ewm(span=WEIGHT_EWMA_SPAN, adjust=False).mean()

latest_weight_ewma = df_weight["ewma"].dropna()
weight_header = (
    f"**Weight** &mdash; 7-day EWMA: {latest_weight_ewma.iloc[-1]:,.1f} kg"
    if not latest_weight_ewma.empty
    else "**Weight** &mdash; 7-day EWMA"
)

with body:
    with st.container(border=True):
        st.markdown(f"**Calorie deficit** &mdash; 7-day avg: {rolling_avg_deficit:,.0f} kcal")
        bars = (
            alt.Chart(df_deficit)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=24, color=calorie_accent, tooltip=False)
            .encode(
                x=alt.X(
                    "day_label:N",
                    sort=list(df_deficit["day_label"]),
                    axis=alt.Axis(title=None, labelAngle=0),
                ),
                y=alt.Y(
                    "deficit:Q",
                    axis=alt.Axis(title="Deficit (kcal)"),
                    scale=alt.Scale(zero=True),
                ),
            )
        )
        rule = (
            alt.Chart(pd.DataFrame({"y": [rolling_avg_deficit]}))
            .mark_rule(strokeDash=[4, 4], color=ring_muted_ink, size=1.5, tooltip=False)
            .encode(y=alt.Y("y:Q", scale=alt.Scale(zero=True)))
        )
        chart = (
            (bars + rule)
            .properties(height=220)
            .configure_view(strokeWidth=0)
            .configure_axis(gridColor=ring_track, domainColor=ring_muted_ink)
        )
        st.altair_chart(chart, width="stretch")
        st.caption(f"Expenditure assumed at {CALORIE_EXPENDITURE:,} kcal/day. Dashed line is the 7-day average.")

    with st.container(border=True):
        st.markdown(weight_header)
        chart = (
            alt.Chart(df_weight)
            .mark_line(point=True, color=WEIGHT_PURPLE, strokeWidth=2.5, tooltip=False)
            .encode(
                x=alt.X(
                    "date:T",
                    axis=alt.Axis(title=None, format="%b %-d", labelAngle=0),
                ),
                y=alt.Y(
                    "ewma:Q",
                    axis=alt.Axis(title="Weight (kg)"),
                    scale=alt.Scale(zero=False),
                ),
            )
            .properties(height=220)
            .configure_view(strokeWidth=0)
            .configure_axis(gridColor=ring_track, domainColor=ring_muted_ink)
        )
        st.altair_chart(chart, width="stretch")
        st.caption(
            f"Since {WEIGHT_JOURNEY_START.strftime('%b %-d')}. Gaps in logged weigh-ins are filled with the "
            "next available reading; line is a 7-day EWMA."
        )

    with st.container(border=True):
        st.markdown("**Exercises** &mdash; past 7 days")
        chart = (
            alt.Chart(df_exercises)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=24, color=GOOD_GREEN, tooltip=False)
            .encode(
                x=alt.X(
                    "day_label:N",
                    sort=list(df_exercises["day_label"]),
                    axis=alt.Axis(title=None, labelAngle=0),
                ),
                y=alt.Y(
                    "exercises:Q",
                    axis=alt.Axis(title="Exercises", tickMinStep=1),
                    scale=alt.Scale(zero=True),
                ),
            )
            .properties(height=220)
            .configure_view(strokeWidth=0)
            .configure_axis(gridColor=ring_track, domainColor=ring_muted_ink)
        )
        st.altair_chart(chart, width="stretch")

# -----------------------------------------------------------------------------
# Weekly spend

try:
    transactions = load_transactions(str(week_days[0]), str(week_days[-1]))
except Exception as e:
    with body:
        st.error(f"Couldn't load transactions: {e}", icon=":material/error:")
    transactions = []

NON_SPEND_CATEGORY_PREFIXES = (
    "Payment",
    "Transfer",
)  # card payoffs, payroll/ACH transfers

daily_totals = {d: 0.0 for d in week_days}
for t in transactions:
    if t["category"].startswith(NON_SPEND_CATEGORY_PREFIXES):
        continue
    d = date.fromisoformat(t["authorized_date"])
    if d in daily_totals:
        daily_totals[d] += t["amount"]

df_spend = pd.DataFrame(
    {"date": list(daily_totals.keys()), "spend": list(daily_totals.values())}
).sort_values("date")
df_spend["day_label"] = df_spend["date"].apply(lambda d: d.strftime("%a %-d"))
total_week_spend = df_spend["spend"].sum()

with body:
    with st.container(border=True):
        st.markdown(f"**Total: ${total_week_spend:,.2f}** over the last 7 days")
        chart = (
            alt.Chart(df_spend)
            .mark_bar(
                cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=24, color=BLUE, tooltip=False
            )
            .encode(
                x=alt.X(
                    "day_label:N",
                    sort=list(df_spend["day_label"]),
                    axis=alt.Axis(title=None, labelAngle=0),
                ),
                y=alt.Y("spend:Q", axis=alt.Axis(title="Spend ($)")),
            )
            .properties(height=280)
            .configure_view(strokeWidth=0)
            .configure_axis(gridColor="#e1e0d9", domainColor="#c3c2b7")
        )
        st.altair_chart(chart, width="stretch")
        st.caption("Excludes credit-card payments and payroll/ACH transfers.")

# -----------------------------------------------------------------------------
# Reload

with st.container(horizontal=True, horizontal_alignment="center"):
    if st.button(":material/refresh: Reload", type="tertiary"):
        st.cache_data.clear()
        st.rerun()
