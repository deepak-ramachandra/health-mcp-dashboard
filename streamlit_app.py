from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

import data_sources

hide_menu_style = """
        <style>
        #MainMenu {visibility: hidden;}
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)

st.set_page_config(
    page_title="Health dashboard",
    page_icon=":material/monitor_heart:",
    layout="wide",
)

NYC = ZoneInfo("America/New_York")
CALORIE_GOAL = 2000
PROTEIN_GOAL = 140
BLUE = "#2a78d6"
GOOD_GREEN = "#0ca30c"
MUTED_CELL = "#e1e0d9"

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


@st.cache_data(ttl="15m", show_spinner="Syncing and loading transactions...")
def load_transactions(start_date: str, end_date: str) -> list[dict]:
    data_sources.sync_transactions()
    return data_sources.get_transactions_by_date_range(start_date, end_date)


# -----------------------------------------------------------------------------
# Header

with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    st.title("Health dashboard")
    if st.button(":material/refresh: Refresh", type="tertiary"):
        st.cache_data.clear()
        st.rerun()

# -----------------------------------------------------------------------------
# Today's progress (main display)

st.subheader("Today's progress")

try:
    meals_today = load_meals_today()
except Exception as e:
    st.error(f"Couldn't load today's meals: {e}", icon=":material/error:")
    st.stop()

calories_today = sum(m.get("calories") or 0 for m in meals_today)
protein_today = sum(m.get("protein_g") or 0 for m in meals_today)

col1, col2 = st.columns(2)
with col1:
    with st.container(border=True):
        st.metric(
            "Calories",
            f"{calories_today:,.0f} kcal",
            f"{calories_today - CALORIE_GOAL:+,.0f} vs {CALORIE_GOAL:,} goal",
            delta_color="inverse",
        )
        st.progress(min(calories_today / CALORIE_GOAL, 1.0))
with col2:
    with st.container(border=True):
        st.metric(
            "Protein",
            f"{protein_today:,.0f} g",
            f"{protein_today - PROTEIN_GOAL:+,.0f} vs {PROTEIN_GOAL:g}g goal",
        )
        st.progress(min(protein_today / PROTEIN_GOAL, 1.0))

with st.container(border=True):
    st.markdown("**Logged today**")
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
            df_meals[["time", "meal", "description", "kcal", "protein (g)", "carbs (g)", "fat (g)"]],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Nothing logged yet today.")

# -----------------------------------------------------------------------------
# Gym attendance calendar

st.subheader("Gym attendance")

try:
    workouts = load_workouts()
except Exception as e:
    st.error(f"Couldn't load workouts: {e}", icon=":material/error:")
    workouts = []

today = datetime.now(NYC).date()

NUM_WEEKS = 6
range_start = today - timedelta(days=NUM_WEEKS * 7 - 1)
range_start -= timedelta(days=range_start.weekday())  # align to Monday

attended_dates = set()
for w in workouts:
    d = datetime.fromisoformat(w["start_time"]).astimezone(NYC).date()
    if range_start <= d <= today:
        attended_dates.add(d)

calendar_rows = []
d = range_start
while d <= today:
    calendar_rows.append(
        {
            "date": d,
            "weekday": d.weekday(),  # 0=Mon .. 6=Sun
            "week": (d - range_start).days // 7,
            "attended": d in attended_dates,
        }
    )
    d += timedelta(days=1)
df_cal = pd.DataFrame(calendar_rows)
weekday_label_expr = (
    "datum.value == 0 ? 'Mon' : datum.value == 2 ? 'Wed' : "
    "datum.value == 4 ? 'Fri' : datum.value == 6 ? 'Sun' : ''"
)

with st.container(border=True):
    st.markdown(
        f"**{range_start.strftime('%b %-d')} – {today.strftime('%b %-d')}** "
        f"&mdash; {len(attended_dates)} sessions"
    )
    chart = (
        alt.Chart(df_cal)
        .mark_rect(cornerRadius=4)
        .encode(
            x=alt.X("week:O", axis=None),
            y=alt.Y(
                "weekday:O",
                sort=[0, 1, 2, 3, 4, 5, 6],
                axis=alt.Axis(title=None, labelExpr=weekday_label_expr, ticks=False, domain=False),
            ),
            color=alt.condition("datum.attended", alt.value(GOOD_GREEN), alt.value(MUTED_CELL)),
            tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("attended:N", title="Workout")],
        )
        .properties(width=alt.Step(26), height=alt.Step(26))
        .configure_view(strokeWidth=0)
        .configure_axis(grid=False)
    )
    st.altair_chart(chart, width="content")
    st.caption(":material/square: green = workout logged")

# -----------------------------------------------------------------------------
# Weekly spend

st.subheader("Spending this week")

week_start = today - timedelta(days=6)
try:
    transactions = load_transactions(str(week_start), str(today))
except Exception as e:
    st.error(f"Couldn't load transactions: {e}", icon=":material/error:")
    transactions = []

NON_SPEND_CATEGORY_PREFIXES = ("Payment", "Transfer")  # card payoffs, payroll/ACH transfers

daily_totals = {week_start + timedelta(days=i): 0.0 for i in range(7)}
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

with st.container(border=True):
    st.markdown(f"**Total: ${total_week_spend:,.2f}** over the last 7 days")
    chart = (
        alt.Chart(df_spend)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=24, color=BLUE)
        .encode(
            x=alt.X("day_label:N", sort=list(df_spend["day_label"]), axis=alt.Axis(title=None, labelAngle=0)),
            y=alt.Y("spend:Q", axis=alt.Axis(title="Spend ($)")),
            tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("spend:Q", title="Spend", format="$.2f")],
        )
        .properties(height=280)
        .configure_view(strokeWidth=0)
        .configure_axis(gridColor="#e1e0d9", domainColor="#c3c2b7")
    )
    st.altair_chart(chart, width="stretch")
    st.caption("Excludes credit-card payments and payroll/ACH transfers.")
