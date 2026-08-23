"""Generic loader for the Research page's tracked projects.

Each project (a study plan, a roadmap, a reading list, ...) is a .toml file
under projects/. This module parses those files into plain dataclasses and
builds the rows needed to seed the `projects` and `milestones` tables (see
data_sources.py / research_tracking_schema.sql).

Adding a new project should never require touching this file, data_sources.py,
or pages/1_Research.py - it's a matter of dropping in a new projects/*.toml.
See any existing file in that directory for the shape.
"""

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECTS_DIR = Path(__file__).parent / "projects"

VALID_KINDS = {"study_plan", "roadmap", "reading_list"}


@dataclass(frozen=True)
class Item:
    id: str
    label: str
    weeks: float | None = None  # None = the source plan gives no pacing for this item
    seed_completed: bool = False


@dataclass(frozen=True)
class Phase:
    id: str
    name: str
    detail: str = ""
    items: list[Item] = field(default_factory=list)

    @property
    def weeks(self) -> float:
        return sum(i.weeks or 0 for i in self.items)


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    subtitle: str = ""
    kind: str = "study_plan"  # study_plan | roadmap | reading_list
    source: str = ""
    start_date: date = None
    doc_date: date | None = None  # when the source doc itself is dated, if that differs from start_date
    horizon_weeks: float | None = None
    accent: str = "#2a78d6"
    item_label: str = "items"  # e.g. "topics", "steps", "papers" - used in the progress ring
    order: int = 0
    phases: list[Phase] = field(default_factory=list)

    @property
    def items(self) -> list[Item]:
        return [i for p in self.phases for i in p.items]

    @property
    def total_weeks(self) -> float:
        return sum(i.weeks or 0 for i in self.items)

    @property
    def has_pace(self) -> bool:
        """Whether this project's plan gives enough pacing info to compare
        actual progress against calendar time. False for a plain reading
        list where no per-item time budget was ever specified."""
        return self.total_weeks > 0


def load_projects() -> list[Project]:
    projects = []
    for path in sorted(PROJECTS_DIR.glob("*.toml")):
        data = tomllib.loads(path.read_text())
        if data.get("kind", "study_plan") not in VALID_KINDS:
            raise ValueError(f"{path.name}: kind must be one of {VALID_KINDS}, got {data.get('kind')!r}")
        phases = [
            Phase(
                id=ph["id"],
                name=ph["name"],
                detail=ph.get("detail", ""),
                items=[
                    Item(
                        id=it["id"],
                        label=it["label"],
                        weeks=it.get("weeks"),
                        seed_completed=it.get("seed_completed", False),
                    )
                    for it in ph.get("items", [])
                ],
            )
            for ph in data.get("phases", [])
        ]
        projects.append(
            Project(
                id=data["id"],
                name=data["name"],
                subtitle=data.get("subtitle", ""),
                kind=data.get("kind", "study_plan"),
                source=data.get("source", ""),
                start_date=data["start_date"],
                doc_date=data.get("doc_date"),
                horizon_weeks=data.get("horizon_weeks"),
                accent=data.get("accent", "#2a78d6"),
                item_label=data.get("item_label", "items"),
                order=data.get("order", 0),
                phases=phases,
            )
        )
    return sorted(projects, key=lambda p: p.order)


def project_seed_row(project: Project) -> tuple:
    """(id, name, subtitle, kind, source, start_date, horizon_weeks, accent)"""
    return (
        project.id,
        project.name,
        project.subtitle,
        project.kind,
        project.source,
        project.start_date.isoformat(),
        project.horizon_weeks,
        project.accent,
    )


def milestone_seed_rows(project: Project) -> list[tuple]:
    """(id, project_id, phase_id, phase_name, seq, label, weeks, completed) -
    seq is 1-indexed within this project, in file order."""
    rows: list[tuple] = []
    seq = 1
    for phase in project.phases:
        for item in phase.items:
            rows.append(
                (item.id, project.id, phase.id, phase.name, seq, item.label, item.weeks, int(item.seed_completed))
            )
            seq += 1
    return rows
