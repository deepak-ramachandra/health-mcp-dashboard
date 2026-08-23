-- Generic "many projects" schema for the Research page: a `projects` table
-- (one row per tracked plan/roadmap/reading-list) and a `milestones` table
-- (one row per checkbox item), linked by project_id. Supersedes
-- create_milestones_table.sql, which had a hardcoded plan enum that would've
-- needed a migration every time a new project was added - that file was
-- never applied, so this replaces it rather than migrating it.
--
-- Both tables are populated by seeding from projects/*.toml (see
-- research_projects.py): those files are the source of truth for what a
-- project/milestone *is* (name, phases, labels, pacing); these tables track
-- that plus which milestones are checked off. Adding a new project is a
-- matter of dropping in a new .toml file - no schema change required.

CREATE TABLE IF NOT EXISTS projects (
  id            TEXT    PRIMARY KEY,
  name          TEXT    NOT NULL,
  subtitle      TEXT,
  kind          TEXT    NOT NULL CHECK (kind IN ('study_plan', 'roadmap', 'reading_list')),
  source        TEXT,
  start_date    TEXT    NOT NULL,
  horizon_weeks REAL,
  accent        TEXT,
  created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS milestones (
  id           TEXT    PRIMARY KEY,
  project_id   TEXT    NOT NULL REFERENCES projects(id),
  phase_id     TEXT    NOT NULL,
  phase_name   TEXT    NOT NULL,
  seq          INTEGER NOT NULL,
  label        TEXT    NOT NULL,
  weeks        REAL,             -- null when the source plan gives no pacing (e.g. a reading list)
  completed    INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
  completed_at TEXT,
  created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- seq is per-project ordering (1, 2, 3... within that project), not global -
-- so adding project #4 never means renumbering anything that came before it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_milestones_project_seq ON milestones(project_id, seq);
