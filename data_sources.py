"""Direct, static-credential access to this dashboard's data sources - no
MCP protocol, no OAuth. Mirrors the query/API logic of the underlying MCP
server's own source (Turso for meals/templates/transactions, Hevy's REST API
for workouts, Plaid for transaction sync) so the dashboard can read the same
data without going through that server's hosted OAuth gateway.

Required secrets (.streamlit/secrets.toml locally, or Secrets settings on
Streamlit Community Cloud): TURSO_DATABASE_URL, TURSO_AUTH_TOKEN,
HEVY_API_KEY, PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ACCESS_TOKEN.
"""

from datetime import datetime, timedelta
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
INSERT INTO transactions (transaction_id, authorized_date, amount, merchant_name, category)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(transaction_id) DO UPDATE SET
  authorized_date = excluded.authorized_date,
  amount          = excluded.amount,
  merchant_name   = excluded.merchant_name,
  category        = excluded.category;
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


def _nyc_day_to_utc_range(date_str: str) -> tuple[str, str]:
    day_start = NYC.localize(datetime.strptime(date_str, "%Y-%m-%d"))
    day_end = day_start + timedelta(days=1)
    return day_start.astimezone(pytz.utc).isoformat(), day_end.astimezone(pytz.utc).isoformat()


def _meal_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "meal_type": row[1],
        "calories": row[2],
        "protein_g": row[3],
        "carbs_g": row[4],
        "fat_g": row[5],
        "logged_at": datetime.fromisoformat(row[6]).astimezone(NYC).isoformat(),
        "desc": row[7],
    }


def get_meals_by_date(date_str: str) -> list[dict]:
    start_utc, end_utc = _nyc_day_to_utc_range(date_str)
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM meals WHERE logged_at >= ? AND logged_at < ? ORDER BY logged_at",
            (start_utc, end_utc),
        ).fetchall()
        return [_meal_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_meals_today() -> list[dict]:
    today = datetime.now(NYC).strftime("%Y-%m-%d")
    return get_meals_by_date(today)


def get_meals_by_date_range(start_date: str, end_date: str) -> list[dict]:
    start_utc, _ = _nyc_day_to_utc_range(start_date)
    _, end_utc = _nyc_day_to_utc_range(end_date)
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM meals WHERE logged_at >= ? AND logged_at < ? ORDER BY logged_at",
            (start_utc, end_utc),
        ).fetchall()
        return [_meal_row_to_dict(r) for r in rows]
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


def _plaid_creds() -> dict:
    return {
        "client_id": _secret("PLAID_CLIENT_ID"),
        "secret": _secret("PLAID_SECRET"),
        "access_token": _secret("PLAID_ACCESS_TOKEN"),
    }


def _txn_row(txn: dict[str, Any]) -> tuple:
    return (
        txn["transaction_id"],
        txn.get("authorized_date") or txn["date"],
        txn["amount"],
        txn.get("merchant_name") or txn.get("name"),
        " ".join(txn.get("category") or []),
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

                with conn:
                    conn.executemany(
                        TRANSACTIONS_UPSERT,
                        [_txn_row(t) for t in page["added"] + page["modified"]],
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
    }


def get_transactions_by_date_range(start_date: str, end_date: str) -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT transaction_id, authorized_date, amount, merchant_name, category "
            "FROM transactions WHERE authorized_date BETWEEN ? AND ?",
            (start_date, end_date),
        ).fetchall()
        return [_txn_row_to_dict(r) for r in rows]
    finally:
        conn.close()
