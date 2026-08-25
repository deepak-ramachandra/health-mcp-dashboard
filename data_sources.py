"""Direct, static-credential access to this dashboard's data sources - no
MCP protocol, no OAuth. Mirrors the query/API logic of the underlying MCP
server's own source (Turso for meals/templates/transactions, Hevy's REST API
for workouts, Plaid for transaction sync) so the dashboard can read the same
data without going through that server's hosted OAuth gateway.

Required secrets (.streamlit/secrets.toml locally, or Secrets settings on
Streamlit Community Cloud): TURSO_DATABASE_URL, TURSO_AUTH_TOKEN,
HEVY_API_KEY, PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ACCESS_TOKEN.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import libsql
import pytz
import streamlit as st

NYC = pytz.timezone("America/New_York")
LOCAL_DB_PATH = str(Path(__file__).parent / ".streamlit" / "nutrition_replica.db")
HEVY_URL = "https://api.hevyapp.com/v1"
PLAID_URL = "https://production.plaid.com"

TRANSACTIONS_UPSERT = """
INSERT INTO transactions (transaction_id, authorized_date, amount, merchant_name, category, account_name)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(transaction_id) DO UPDATE SET
  authorized_date = excluded.authorized_date,
  amount          = excluded.amount,
  merchant_name   = excluded.merchant_name,
  category        = excluded.category,
  account_name    = excluded.account_name;
"""

# `projects` and `milestones` are provisioned by schema migration, same as
# meals/transactions/sleep - see research_tracking_schema.sql.
# projects/*.toml (via research_projects.py) is the source of truth for what
# a project and its milestones *are* (structure, labels, pacing) - that's
# read straight off disk on every page load, no DB round trip needed. These
# tables only track completion state, and only ever get written to when a
# checkbox is actually toggled (see set_milestone_completed() below) - not on
# every page load. A missing milestone row just means "not completed yet."
#
# seed_projects()/seed_milestones() below are NOT called from the Research
# page's normal load path anymore (that was the slow part - every page load
# was paying for a handful of upsert round trips just to keep already-seeded
# rows in sync). They're kept as one-off tools: seed_projects() to register a
# brand-new project's metadata row, seed_milestones() to bulk-precheck a
# project's already-completed items (its TOML's seed_completed flags) the
# first time it's added - the same kind of one-off step as apply_schema.py,
# run once by hand, not on every visit.
PROJECTS_UPSERT = """
INSERT INTO projects (id, name, subtitle, kind, source, start_date, horizon_weeks, accent)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  name          = excluded.name,
  subtitle      = excluded.subtitle,
  kind          = excluded.kind,
  source        = excluded.source,
  horizon_weeks = excluded.horizon_weeks,
  accent        = excluded.accent;
"""

MILESTONES_SEED_UPSERT = """
INSERT INTO milestones (id, project_id, phase_id, phase_name, seq, label, weeks, completed)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO NOTHING;
"""

# The real-time write path: fires once per checkbox click, not per page
# load. Inserts the milestone's full row the first time it's checked (a row
# may not exist yet, since nothing pre-seeds them), or just flips
# completed/completed_at if it does.
MILESTONE_UPSERT = """
INSERT INTO milestones (id, project_id, phase_id, phase_name, seq, label, weeks, completed, completed_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  completed    = excluded.completed,
  completed_at = excluded.completed_at;
"""


def _secret(key: str) -> str:
    value = st.secrets.get(key)
    if not value:
        raise RuntimeError(
            f"Missing '{key}' secret. Add it to .streamlit/secrets.toml locally, "
            "or this app's Secrets settings on Streamlit Community Cloud."
        )
    return value


def _get_db():
    conn = libsql.connect(
        LOCAL_DB_PATH,
        sync_url=_secret("TURSO_DATABASE_URL"),
        auth_token=_secret("TURSO_AUTH_TOKEN"),
    )
    conn.sync()
    return conn


def _meal_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "meal_type": row[1],
        "calories": row[2],
        "protein_g": row[3],
        "carbs_g": row[4],
        "fat_g": row[5],
        "logged_at": row[6],
        "desc": row[7],
    }


def get_meals_by_date(date_str: str) -> list[dict]:
    conn = _get_db()
    try:
        # logged_at is stored NYC-local with its correct offset (e.g.
        # -04:00/-05:00 across DST), so the date is just its first 10 chars.
        rows = conn.execute(
            "SELECT * FROM meals WHERE substr(logged_at, 1, 10) = ? ORDER BY logged_at",
            (date_str,),
        ).fetchall()
        return [_meal_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_meals_today() -> list[dict]:
    today = datetime.now(NYC).strftime("%Y-%m-%d")
    return get_meals_by_date(today)


def get_meals_by_date_range(start_date: str, end_date: str) -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM meals WHERE substr(logged_at, 1, 10) >= ? "
            "AND substr(logged_at, 1, 10) <= ? ORDER BY logged_at",
            (start_date, end_date),
        ).fetchall()
        return [_meal_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _template_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "calories": row[2],
        "protein_g": row[3],
        "carbs_g": row[4],
        "fat_g": row[5],
        "notes": row[6],
    }


def get_meal_templates() -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, calories, protein_g, carbs_g, fat_g, notes "
            "FROM meal_templates ORDER BY name"
        ).fetchall()
        return [_template_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def log_meal_from_template(template_id: str, meal_type: str, logged_at: str) -> dict:
    """Log a meal using macros from a saved template. Mirrors the
    log_meal_from_template tool on the MCP server."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, name, calories, protein_g, carbs_g, fat_g, notes "
            "FROM meal_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No meal template found with id {template_id}")
        t = _template_row_to_dict(row)
        conn.execute(
            "INSERT INTO meals (meal_type, calories, protein_g, carbs_g, fat_g, logged_at, desc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                meal_type,
                t["calories"],
                t["protein_g"],
                t["carbs_g"],
                t["fat_g"],
                logged_at,
                t["name"],
            ),
        )
        conn.commit()
        meal_row = conn.execute(
            "SELECT * FROM meals WHERE logged_at = ? AND meal_type = ? ORDER BY rowid DESC LIMIT 1",
            (logged_at, meal_type),
        ).fetchone()
        return _meal_row_to_dict(meal_row)
    finally:
        conn.close()


def get_workouts(page: int, page_size: int) -> dict:
    headers = {"accept": "application/json", "api-key": _secret("HEVY_API_KEY")}
    resp = httpx.get(
        f"{HEVY_URL}/workouts",
        headers=headers,
        params={"page": page, "pageSize": page_size},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_workout_events(page: int, page_size: int, since: str) -> dict:
    """Hevy's change-feed endpoint (see test.ipynb) - unlike get_workouts()
    above, which always returns the N most recent workouts regardless of
    date, this only returns workouts created/updated since `since` (a UTC
    ISO datetime, e.g. "2026-08-01T00:00:00Z"). A caller that only needs a
    rolling window - the dashboard only ever looks at the past 7 days -
    can page through just that window instead of re-fetching everything on
    every load."""
    headers = {"accept": "application/json", "api-key": _secret("HEVY_API_KEY")}
    resp = httpx.get(
        f"{HEVY_URL}/workouts/events",
        headers=headers,
        params={"page": page, "pageSize": page_size, "since": since},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_body_measurements(page: int, page_size: int) -> dict:
    headers = {"accept": "application/json", "api-key": _secret("HEVY_API_KEY")}
    resp = httpx.get(
        f"{HEVY_URL}/body_measurements",
        headers=headers,
        params={"page": page, "pageSize": page_size},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _plaid_creds() -> dict:
    return {
        "client_id": _secret("PLAID_CLIENT_ID"),
        "secret": _secret("PLAID_SECRET"),
        "access_token": _secret("PLAID_ACCESS_TOKEN"),
    }


def _txn_row(txn: dict[str, Any], account_name: str | None) -> tuple:
    return (
        txn["transaction_id"],
        txn.get("authorized_date") or txn["date"],
        txn["amount"],
        txn.get("merchant_name") or txn.get("name"),
        " ".join(txn.get("category") or []),
        account_name,
    )


def sync_transactions() -> dict[str, int]:
    """Pull all pages from Plaid's /transactions/sync since the last saved
    cursor (shared with the MCP server's own syncs via the same DB row)."""
    conn = _get_db()
    try:
        cur = conn.execute("SELECT cursor FROM sync_state WHERE id = 1").fetchone()
        cursor = cur[0] if cur else None
        result = dict(added=0, modified=0, removed=0)
        creds = _plaid_creds()
        with httpx.Client(timeout=30) as client:
            while True:
                body = {**creds, "count": 500}
                if cursor:
                    body["cursor"] = cursor
                r = client.post(f"{PLAID_URL}/transactions/sync", json=body)
                r.raise_for_status()
                page = r.json()

                account_map = {
                    a["account_id"]: f"{a['subtype']}-{a['mask']}"
                    for a in page["accounts"]
                }
                with conn:
                    conn.executemany(
                        TRANSACTIONS_UPSERT,
                        [
                            _txn_row(t, account_map.get(t["account_id"]))
                            for t in page["added"] + page["modified"]
                        ],
                    )
                    conn.executemany(
                        "DELETE FROM transactions WHERE transaction_id = ?",
                        [(t["transaction_id"],) for t in page["removed"]],
                    )
                    conn.execute(
                        "INSERT INTO sync_state (id, cursor) VALUES (1, ?) "
                        "ON CONFLICT(id) DO UPDATE SET cursor = excluded.cursor",
                        (page["next_cursor"],),
                    )

                cursor = page["next_cursor"]
                result["added"] += len(page["added"])
                result["modified"] += len(page["modified"])
                result["removed"] += len(page["removed"])
                if not page["has_more"]:
                    break
        return result
    finally:
        conn.close()


def _txn_row_to_dict(row) -> dict:
    return {
        "transaction_id": row[0],
        "authorized_date": row[1],
        "amount": row[2],
        "merchant_name": row[3],
        "category": row[4],
        "account_name": row[5],
    }


def get_transactions_by_date_range(start_date: str, end_date: str) -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT transaction_id, authorized_date, amount, merchant_name, category, account_name FROM transactions WHERE authorized_date BETWEEN ? AND ?",
            (start_date, end_date),
        ).fetchall()
        return [_txn_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _project_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "subtitle": row[2],
        "kind": row[3],
        "source": row[4],
        "start_date": row[5],
        "horizon_weeks": row[6],
        "accent": row[7],
    }


def seed_projects(rows: list[tuple]) -> None:
    """Upsert project metadata - every column except id refreshes from
    projects/*.toml on every call, since that file is the source of truth
    for what a project *is*. `rows` are (id, name, subtitle, kind, source,
    start_date, horizon_weeks, accent)."""
    conn = _get_db()
    try:
        conn.executemany(PROJECTS_UPSERT, rows)
        conn.commit()
    finally:
        conn.close()


def get_projects() -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, subtitle, kind, source, start_date, horizon_weeks, accent FROM projects"
        ).fetchall()
        return [_project_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _milestone_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "project_id": row[1],
        "phase_id": row[2],
        "phase_name": row[3],
        "seq": row[4],
        "label": row[5],
        "weeks": row[6],
        "completed": bool(row[7]),
        "completed_at": row[8],
        "created_at": row[9],
    }


def seed_milestones(rows: list[tuple]) -> None:
    """Idempotently seed the milestones table - safe to call on every page
    load. `rows` are (id, project_id, phase_id, phase_name, seq, label,
    weeks, completed); existing rows (matched by id) are left untouched, so
    this never clobbers progress already checked off."""
    conn = _get_db()
    try:
        conn.executemany(MILESTONES_SEED_UPSERT, rows)
        conn.commit()
    finally:
        conn.close()


def get_milestones(project_id: str | None = None) -> list[dict]:
    conn = _get_db()
    try:
        if project_id is not None:
            rows = conn.execute(
                "SELECT id, project_id, phase_id, phase_name, seq, label, weeks, completed, completed_at, created_at "
                "FROM milestones WHERE project_id = ? ORDER BY seq",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, project_id, phase_id, phase_name, seq, label, weeks, completed, completed_at, created_at "
                "FROM milestones ORDER BY project_id, seq"
            ).fetchall()
        return [_milestone_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def set_milestone_completed(static_row: tuple, completed: bool) -> None:
    """Upsert one milestone's completion state - called from the checkbox's
    on_change, so this is the only time the milestones table gets a write in
    normal use. `static_row` is (id, project_id, phase_id, phase_name, seq,
    label, weeks) - the item's fixed fields, sourced from projects/*.toml
    (research_projects.milestone_seed_rows()) rather than a DB read, since
    the row itself may not exist yet."""
    conn = _get_db()
    try:
        conn.execute(
            MILESTONE_UPSERT,
            (
                *static_row,
                1 if completed else 0,
                datetime.now(timezone.utc).isoformat() if completed else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _exercise_template_row_to_dict(row) -> dict:
    return {
        "exercise_template_id": row[0],
        "title": row[1],
        "type": row[2],
        "primary_muscle_group": row[3],
        "equipment": row[4],
    }


def get_exercise_templates() -> list[dict]:
    """Hevy's exercise catalog - read-only here. Populated/refreshed by
    scripts/get_exercise_templates.py, run by hand whenever Hevy's catalog
    changes, not on every page load (it's static reference data, not
    something this app writes to)."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT exercise_template_id, title, type, primary_muscle_group, equipment FROM exercise_templates"
        ).fetchall()
        return [_exercise_template_row_to_dict(r) for r in rows]
    finally:
        conn.close()
