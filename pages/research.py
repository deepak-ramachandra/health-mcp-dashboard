"""Research progress — tracks every project you're following (study plans,
roadmaps, reading lists) against whatever pace each one's own source doc
gives, so progress is visible next to the health dashboard.

Projects are defined as data files under projects/*.toml (see
research_projects.py) - adding a new one is a matter of dropping in a new
.toml file, no code change needed here. Structure (phases, items, pacing)
is read straight from those files on every load - it's static, so there's
nothing to cache or write to a database about it. Completion state lives in
the `milestones` table in the same Turso database as the rest of the app
(see data_sources.py / research_tracking_schema.sql), but a row only gets
written once, the first time its checkbox is checked - a missing row just
means "not completed yet." Nothing here upserts on page load anymore.

Page config, the header-hiding CSS, and this page's sidebar label/icon are
all set once in streamlit_app.py (the router) rather than here - see that
file's docstring for why.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

import data_sources
import research_projects as rp
from ui_components import progress_ring, ring_theme

NYC = ZoneInfo("America/New_York")
today = datetime.now(NYC).date()


# -----------------------------------------------------------------------------
# Data loading — projects (structure, phases, pacing) come straight from
# projects/*.toml, read fresh off disk on every load; that's free, so there's
# nothing to cache or seed about it. Milestones (completion state) live in
# Turso, but this is a pure read now - a missing row just means "not
# completed yet" (see _completed() below), so no upserts happen here. A row
# only gets written once, the first time its checkbox is toggled (_toggle()).
# This used to also seed both tables on every load, which meant every visit
# paid for several upsert round trips just to keep already-seeded rows in
# sync - that's what made this page noticeably slower to load than the main
# dashboard. See data_sources.py for the one-off tools (seed_projects(),
# seed_milestones()) that replace what that seeding used to do, for the rare
# case of registering a brand-new project.


@st.cache_data(ttl="1m", show_spinner="Loading projects...")
def load_state() -> tuple[list[rp.Project], dict[str, dict]]:
    projects = rp.load_projects()
    milestones_by_id = {m["id"]: m for m in data_sources.get_milestones()}
    return projects, milestones_by_id


try:
    projects, milestones_by_id = load_state()
except Exception as e:
    st.error(f"Couldn't load projects: {e}", icon=":material/error:")
    st.stop()

# Item id -> (id, project_id, phase_id, phase_name, seq, label, weeks), i.e.
# a milestone row's static fields minus its completion state - sourced from
# the TOML data already loaded above, not a DB read. Used to write the full
# row the first time an item's checkbox is checked, since no row may exist
# for it yet.
_milestone_static = {
    row[0]: row[:-1]
    for project in projects
    for row in rp.milestone_seed_rows(project)
}


def _key(item_id: str) -> str:
    return f"chk_{item_id}"


def _completed(item_id: str) -> bool:
    return milestones_by_id.get(item_id, {}).get("completed", False)


def _toggle(item_id: str) -> None:
    try:
        data_sources.set_milestone_completed(
            _milestone_static[item_id], st.session_state[_key(item_id)]
        )
    except Exception as e:
        st.error(f"Couldn't save that checkbox: {e}", icon=":material/error:")
    else:
        load_state.clear()


is_dark = st.context.theme.type == "dark"
ring_track, ring_ink, ring_muted_ink = ring_theme(is_dark)

# -----------------------------------------------------------------------------
# Pace helpers


def _item_counts(items: list[rp.Item]) -> tuple[int, int]:
    done = sum(1 for i in items if _completed(i.id))
    return done, len(items)


def _banked_weeks(items: list[rp.Item]) -> float:
    return sum(i.weeks or 0 for i in items if _completed(i.id))


def _pace_stats(project: rp.Project, start: date) -> dict:
    items = project.items
    total_weeks = project.total_weeks
    banked = _banked_weeks(items)
    elapsed_weeks = max((today - start).days / 7, 0.0)
    delta = banked - elapsed_weeks
    projected = None
    if banked > 0 and elapsed_weeks > 0:
        rate = banked / elapsed_weeks  # weeks of material per calendar week
        remaining = max(total_weeks - banked, 0.0)
        if rate > 0:
            projected = today + timedelta(weeks=remaining / rate)
    return dict(total_weeks=total_weeks, banked=banked, elapsed_weeks=elapsed_weeks, delta=delta, projected=projected)


# -----------------------------------------------------------------------------
# Rendering — one generic function for every project, regardless of kind.


def _render_project(project: rp.Project) -> None:
    items = project.items
    done, total = _item_counts(items)

    date_bits = f"Started {project.start_date.strftime('%b %-d, %Y')}"
    if project.doc_date and project.doc_date != project.start_date:
        date_bits = (
            f"Doc dated {project.doc_date.strftime('%b %Y')}, "
            f"tracking started {project.start_date.strftime('%b %-d, %Y')}"
        )

    with st.container(border=True):
        header = f"**{project.name}**"
        if project.subtitle:
            header += f" — {project.subtitle}"
        st.markdown(header)

        left, right = st.columns([1, 2], gap="large")
        with left:
            st.html(
                f"""<div style="display:flex; justify-content:center;">
                {progress_ring(done, total, "", project.item_label, project.accent, ring_track, ring_ink, ring_muted_ink)}
                </div>"""
            )
        with right:
            if project.has_pace:
                pace = _pace_stats(project, project.start_date)
                m1, m2, m3 = st.columns(3)
                m1.metric("Elapsed", f"{pace['elapsed_weeks']:.1f} wk")
                m2.metric("Banked", f"{pace['banked']:.1f} wk", delta=f"{pace['delta']:+.1f} wk vs. pace")
                m3.metric("Projected finish", pace["projected"].strftime("%b %Y") if pace["projected"] else "—")
                extra = f" · ~{project.horizon_weeks:.0f} week horizon" if project.horizon_weeks else ""
                n_phases = len(project.phases)
                phase_word = "phase" if n_phases == 1 else "phases"
                st.caption(f"{date_bits}{extra} · {total} {project.item_label} across {n_phases} {phase_word}.")
            else:
                st.metric("Progress", f"{done} of {total} {project.item_label}")
                st.caption(f"{date_bits} · no stated pace in the source doc, so no ETA — just sequence.")

        st.divider()

        first_incomplete = next(
            (phase.id for phase in project.phases if _item_counts(phase.items)[0] < _item_counts(phase.items)[1]),
            None,
        )
        for phase in project.phases:
            p_done, p_total = _item_counts(phase.items)
            mark = "✅ " if p_total and p_done == p_total else ""
            with st.expander(f"{mark}{phase.name} — {p_done}/{p_total}", expanded=(phase.id == first_incomplete)):
                if phase.detail:
                    st.caption(phase.detail)
                for item in phase.items:
                    st.checkbox(
                        item.label,
                        value=_completed(item.id),
                        key=_key(item.id),
                        on_change=_toggle,
                        args=(item.id,),
                    )


# -----------------------------------------------------------------------------
# Header

st.markdown("### Research progress")
st.caption(
    "Tracking every study plan, roadmap, and reading list you're following — "
    "add a new one by dropping a .toml file into projects/, no code change needed."
)

for project in projects:
    _render_project(project)

# -----------------------------------------------------------------------------
# Reload

with st.container(horizontal=True, horizontal_alignment="center"):
    if st.button(":material/refresh: Reload", type="tertiary"):
        load_state.clear()
        st.rerun()
