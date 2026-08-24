import os
from pathlib import Path

import httpx
import libsql
import pandas as pd

headers = {"accept": "application/json", "api-key": os.getenv("HEVY", "")}
page_count = None
PAGE_SIZE = 100
page = 1
templates = []
while True:
    resp = httpx.get(
        f"https://api.hevy.com/v1/exercise_templates",
        headers=headers,
        params={"page": page, "pageSize": PAGE_SIZE},
        timeout=30,
    )
    resp.raise_for_status()
    res: dict = resp.json()

    templates.extend(res["exercise_templates"])

    page += 1
    if page_count is None:
        page_count = res["page_count"]
    if page > page_count:
        break

templates_df = pd.DataFrame.from_records(templates).drop(
    columns=["secondary_muscle_groups", "is_custom"]
)
templates_df.rename(columns={"id": "exercise_template_id"}, inplace=True)
templates_df.to_csv("exercise_templates.csv", index=False)
print(f"Pulled {len(templates_df)} exercise templates from Hevy.")

# --- Upsert into Turso -------------------------------------------------
# Same embedded-replica pattern as data_sources.py's _get_db() - points at
# the same local replica file, since this is the same Turso database the
# rest of the app talks to. Hevy's catalog is the source of truth for a
# template's own fields, so every column but the id gets refreshed on
# conflict (same reasoning as PROJECTS_UPSERT in data_sources.py).

REPO_ROOT = Path(__file__).parent.parent
LOCAL_DB_PATH = str(REPO_ROOT / ".streamlit" / "nutrition_replica.db")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS exercise_templates (
  exercise_template_id TEXT PRIMARY KEY,
  title                 TEXT NOT NULL,
  type                  TEXT NOT NULL,
  primary_muscle_group  TEXT NOT NULL,
  equipment             TEXT NOT NULL
);
"""

UPSERT = """
INSERT INTO exercise_templates (exercise_template_id, title, type, primary_muscle_group, equipment)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(exercise_template_id) DO UPDATE SET
  title                 = excluded.title,
  type                  = excluded.type,
  primary_muscle_group  = excluded.primary_muscle_group,
  equipment              = excluded.equipment;
"""

conn = libsql.connect(
    LOCAL_DB_PATH,
    sync_url=os.environ["TURSO_DATABASE_URL"],
    auth_token=os.environ["TURSO_AUTH_TOKEN"],
)
conn.sync()
try:
    conn.execute(CREATE_TABLE)
    rows = list(
        templates_df[
            [
                "exercise_template_id",
                "title",
                "type",
                "primary_muscle_group",
                "equipment",
            ]
        ].itertuples(index=False, name=None)
    )
    conn.executemany(UPSERT, rows)
    conn.commit()
finally:
    conn.close()

print(f"Upserted {len(rows)} exercise templates into Turso.")
