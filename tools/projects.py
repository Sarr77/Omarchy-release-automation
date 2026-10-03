"""Explicit release destinations; adding a project needs a separate review."""
from dataclasses import dataclass

AUTOMATION_REPOSITORY = "Sarr77/Omarchy-release-automation"


@dataclass(frozen=True)
class Project:
    key: str
    repository: str
    plugin_id: str
    archive_prefix: str
    author_name: str
    author_id: int
    plan_variable: str
    token_secret: str


SCRATCHPEEK = Project(
    key="scratchpeek", repository="Sarr77/ScratchPeek", plugin_id="sarr.scratchpeek",
    archive_prefix="ScratchPeek", author_name="Sarr", author_id=19185477,
    plan_variable="SCRATCHPEEK_RELEASE_PLAN", token_secret="SCRATCHPEEK_RELEASE_TOKEN",
)
# WindowPeek is not enabled. Each future entry needs its own reviewed workflow
# job, repository-scoped release token and independently approved release plan.
PROJECTS = {SCRATCHPEEK.key: SCRATCHPEEK}
