"""Health dashboard — today's nutrition/protein rings, meal log + quick-log
form, weekly calorie deficit, weight trend, exercise counts, and spend.

Page config, the header-hiding CSS, and this page's sidebar label/icon are
all set once in streamlit_app.py (the router) rather than here - see that
file's docstring for why.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

import data_sources
from ui_components import progress_ring, ring_theme

NYC = ZoneInfo("America/New_York")
CALORIE_GOAL = 2000
PROTEIN_GOAL = 140
CALORIE_EXPENDITURE = 2400  # TDEE, for daily deficit = expenditure - intake
BLUE = "#2a78d6"
GOOD_GREEN = "#0ca30c"
CREDIT_ORANGE = "#e0862b"
WEIGHT_PURPLE = "#8b5cf6"
WEIGHT_EWMA_SPAN = 7
WEIGHT_JOURNEY_START = date(2026, 7, 14)

# -----------------------------------------------------------------------------
# Data loading


@st.cache_data(ttl="2m", show_spinner="Loading today's meals...")
def load_meals_today() -> list[dict]:
    return data_sources.get_meals_today()


@st.cache_data(ttl="15m", show_spinner="Loading workouts...")
def load_workouts(start_date: str, end_date: str) -> list[dict]:
    """Pulls only workouts changed since `since` via Hevy's /workouts/events
    change-feed (data_sources.get_workout_events()), rather than always
    re-fetching the most recent N workouts regardless of date like
    data_sources.get_workouts() does - this page only ever needs the past
    7 days, which is far less data than the ~60 most recent workouts. An
    event with no "workout" key is a deletion event (Hevy's feed reports
    those too) and is skipped, not a workout to include."""
    data_sources.sync_workouts()
    return data_sources.get_workouts_by_date_range(start_date, end_date)


@st.cache_data(ttl="30m", show_spinner="Loading exercise catalog...")
def load_exercise_templates() -> list[dict]:
    return data_sources.get_exercise_templates()


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
ring_track, ring_ink, ring_muted_ink = ring_theme(is_dark)
calorie_accent = "#3987e5" if is_dark else "#2a78d6"
protein_accent = "#199e70" if is_dark else "#1baf7a"

with body:
    with st.container(border=True):
        st.html(f"""
            <div style="display:flex; flex-wrap:nowrap; justify-content:center; gap:32px;">
              {progress_ring(calories_today, CALORIE_GOAL, "", "kcal", calorie_accent, ring_track, ring_ink, ring_muted_ink)}
              {progress_ring(protein_today, PROTEIN_GOAL, "g", "protein", protein_accent, ring_track, ring_ink, ring_muted_ink)}
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
                    key="log_meal_time",
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
# Training volume (past 7 days, through today) + calorie deficit (past 7
# days, excluding today - see workout_days vs week_days below)

today = datetime.now(NYC).date()
week_days = [today - timedelta(days=i) for i in range(7, 0, -1)]  # today-7 .. today-1
# Workouts get their own window that runs through today (today-6 .. today)
# rather than stopping yesterday like week_days - a workout done earlier
# today should show up right away instead of waiting until tomorrow. The
# calorie deficit and spend cards below stay on week_days/yesterday on
# purpose: today's numbers there are still incomplete (dinner not logged
# yet, more spending still to come) and would read as misleadingly low.
workout_days = [today - timedelta(days=i) for i in range(6, -1, -1)]

# Midnight NYC the day before week_days[0], converted to UTC - one day of
# slack around the actual window so a workout right at the boundary can't
# be missed by an off-by-one in how Hevy's `since` filter treats the edge.
# The per-workout date check below still enforces the exact week_days
# window regardless, so over-asking here only costs a little extra data,
# never correctness.
workouts_since = (
    datetime.combine(week_days[0] - timedelta(days=1), datetime.min.time(), tzinfo=NYC)
    .astimezone(timezone.utc)
    .strftime("%Y-%m-%dT%H:%M:%SZ")
)

workouts_end = (
    datetime.combine(today, datetime.max.time(), tzinfo=NYC)
    .astimezone(timezone.utc)
    .strftime("%Y-%m-%dT%H:%M:%SZ")
)

try:
    workouts = load_workouts(workouts_since, workouts_end)
except Exception as e:
    with body:
        st.error(f"Couldn't load workouts: {e}", icon=":material/error:")
    workouts = []

try:
    exercise_templates = load_exercise_templates()
except Exception as e:
    with body:
        st.error(f"Couldn't load exercise catalog: {e}", icon=":material/error:")
    exercise_templates = []
templates_by_id = {t["exercise_template_id"]: t for t in exercise_templates}


def _exercise_volume(sets: list[dict]) -> tuple[float, str] | None:
    """Total 'volume' for one exercise's sets, in whichever unit it
    actually logs - ported from test.ipynb's volume(): checks weight_kg,
    then distance_meters, then duration_seconds, then falls back to reps,
    since which of those fields Hevy populates depends on the exercise
    itself, not just its catalog `type`. Unlike the notebook, the unit
    returned here is tied to whichever field actually produced the number
    (kg / km / min / reps) instead of being looked up from `type`
    separately - the notebook's type-based label didn't match what its own
    formula computed for distance-based exercises (e.g. cycling came out
    in meters, not seconds). Distance and duration are converted to
    km/minutes rather than left as raw meters/seconds, just for
    readability."""
    if not sets:
        return None
    first = sets[0]
    if first.get("weight_kg") is not None:
        total = sum((s.get("weight_kg") or 0) * (s.get("reps") or 0) for s in sets)
        return round(total, 2), "kg"
    if first.get("distance_meters") is not None:
        total_m = sum(
            (s.get("distance_meters") or 0) * (s.get("reps") or 1) for s in sets
        )
        return round(total_m / 1000, 2), "km"
    if first.get("duration_seconds") is not None:
        total_s = sum(
            (s.get("duration_seconds") or 0) * (s.get("reps") or 1) for s in sets
        )
        return round(total_s / 60, 1), "min"
    return sum(s.get("reps") or 0 for s in sets), "reps"


# Primary-muscle-group volume only (no secondary-muscle credit) for
# weight_reps exercises; everything else (Table Tennis, Cycling, ...) is
# enumerated separately in whatever unit it actually logs, since there's no
# kg figure to fold it into.
muscle_volume_kg: dict[str, float] = {}
other_activity: dict[tuple[str, str], float] = {}

for w in workouts:
    d = datetime.fromisoformat(w["start_time"]).astimezone(NYC).date()
    if d not in workout_days:
        continue
    for ex in w.get("exercises") or []:
        result = _exercise_volume(ex.get("sets") or [])
        if result is None:
            continue
        value, unit = result
        template = templates_by_id.get(ex.get("exercise_template_id"))
        if template is None:
            continue  # not in the catalog (e.g. a custom exercise) - skip
        if unit == "kg":
            muscle = template["primary_muscle_group"]
            muscle_volume_kg[muscle] = muscle_volume_kg.get(muscle, 0) + value
        else:
            key = (template["title"], unit)
            other_activity[key] = other_activity.get(key, 0) + value

df_muscle_volume = pd.DataFrame(
    [{"muscle": m, "kg": round(v, 1)} for m, v in muscle_volume_kg.items()],
    columns=["muscle", "kg"],
).sort_values("kg", ascending=False)

df_other_activity = pd.DataFrame(
    [
        {"activity": title, "total": round(v, 1), "unit": unit}
        for (title, unit), v in other_activity.items()
    ],
    columns=["activity", "total", "unit"],
).sort_values("total", ascending=False)

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
        "deficit": [CALORIE_EXPENDITURE - daily_calories_week[d] for d in week_days],
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
df_weight["ewma"] = (
    df_weight["weight_kg"].ewm(span=WEIGHT_EWMA_SPAN, adjust=False).mean()
)

latest_weight_ewma = df_weight["ewma"].dropna()
weight_header = (
    f"**Weight** &mdash; 7-day EWMA: {latest_weight_ewma.iloc[-1]:,.1f} kg"
    if not latest_weight_ewma.empty
    else "**Weight** &mdash; 7-day EWMA"
)

with body:
    with st.container(border=True):
        st.markdown(
            f"**Calorie deficit** &mdash; 7-day avg: {rolling_avg_deficit:,.0f} kcal"
        )
        bars = (
            alt.Chart(df_deficit)
            .mark_bar(
                cornerRadiusTopLeft=4,
                cornerRadiusTopRight=4,
                size=24,
                color=calorie_accent,
                tooltip=False,
            )
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
        st.caption(
            f"Expenditure assumed at {CALORIE_EXPENDITURE:,} kcal/day. Dashed line is the 7-day average."
        )

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
        st.markdown("**Volume by muscle group** &mdash; past 7 days")
        if not df_muscle_volume.empty:
            chart = (
                alt.Chart(df_muscle_volume)
                .mark_bar(
                    cornerRadiusTopRight=4,
                    cornerRadiusBottomRight=4,
                    size=16,
                    color=GOOD_GREEN,
                    tooltip=False,
                )
                .encode(
                    y=alt.Y(
                        "muscle:N",
                        sort=list(df_muscle_volume["muscle"]),
                        axis=alt.Axis(title=None),
                    ),
                    x=alt.X(
                        "kg:Q",
                        axis=alt.Axis(title="Volume (kg)"),
                        scale=alt.Scale(zero=True),
                    ),
                )
                # Below ~130px total, Streamlit's default chart theme
                # (theme="streamlit" in st.altair_chart) collides adjacent
                # y-axis category bands - bars/labels silently overlap and
                # a row disappears from view even though it's still in
                # df_muscle_volume (verified: reproduced at height=120,
                # which is what `40 * len(...)` gives for 2-3 rows; gone
                # at height>=130). 50px/row with a 160px floor stays clear
                # of that threshold regardless of how many muscle groups
                # show up.
                .properties(height=max(50 * len(df_muscle_volume), 160))
                .configure_view(strokeWidth=0)
                .configure_axis(gridColor=ring_track, domainColor=ring_muted_ink)
            )
            st.altair_chart(chart, width="stretch")
            st.caption(
                "Weight-based exercises only, credited to the primary muscle group - no secondary-muscle credit."
            )
        else:
            st.caption("No weight_reps exercises logged in the past 7 days.")

        if not df_other_activity.empty:
            st.divider()
            st.caption(
                "Everything else, in its own unit - no kg figure to fold these into."
            )
            st.dataframe(df_other_activity, hide_index=True, width="stretch")

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
)  # card payoffs, payroll/ACH transfers - excluded on both accounts, so a
# credit-card bill paid from checking doesn't get counted as spend twice

CARD_TYPES = ["Debit", "Credit"]
CARD_COLORS = {"Debit": BLUE, "Credit": CREDIT_ORANGE}


def _card_type(account_name: str | None) -> str:
    return "Credit" if (account_name or "").startswith("credit") else "Debit"


daily_totals = {(d, card): 0.0 for d in week_days for card in CARD_TYPES}
for t in transactions:
    if t["category"].startswith(NON_SPEND_CATEGORY_PREFIXES):
        continue
    d = date.fromisoformat(t["authorized_date"])
    if d in week_days:
        daily_totals[(d, _card_type(t["account_name"]))] += t["amount"]

df_spend = pd.DataFrame(
    [
        {"date": d, "card": card, "spend": amount}
        for (d, card), amount in daily_totals.items()
    ]
).sort_values("date")
df_spend["day_label"] = df_spend["date"].apply(lambda d: d.strftime("%a %-d"))
total_week_spend = df_spend["spend"].sum()
total_by_card = df_spend.groupby("card")["spend"].sum()

with body:
    with st.container(border=True):
        st.markdown(
            f"**Total: \\${total_week_spend:,.2f}** over the last 7 days "
            f"&mdash; Debit \\${total_by_card.get('Debit', 0):,.2f} / "
            f"Credit \\${total_by_card.get('Credit', 0):,.2f}"
        )
        chart = (
            alt.Chart(df_spend)
            .mark_bar(
                cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=24, tooltip=False
            )
            .encode(
                x=alt.X(
                    "day_label:N",
                    sort=[d.strftime("%a %-d") for d in week_days],
                    axis=alt.Axis(title=None, labelAngle=0),
                ),
                y=alt.Y("spend:Q", axis=alt.Axis(title="Spend ($)")),
                color=alt.Color(
                    "card:N",
                    scale=alt.Scale(
                        domain=CARD_TYPES, range=[CARD_COLORS[c] for c in CARD_TYPES]
                    ),
                    legend=alt.Legend(title=None, orient="top"),
                ),
                order=alt.Order("card:N"),
            )
            .properties(height=280)
            .configure_view(strokeWidth=0)
            .configure_axis(gridColor="#e1e0d9", domainColor="#c3c2b7")
        )
        st.altair_chart(chart, width="stretch")
        st.caption(
            "Excludes credit-card bill payments and payroll/ACH transfers. Stacked by debit vs. credit card."
        )

# -----------------------------------------------------------------------------
# Reload

with st.container(horizontal=True, horizontal_alignment="center"):
    if st.button(":material/refresh: Reload", type="tertiary"):
        st.cache_data.clear()
        st.rerun()
