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
    package_layout: str
    package_script_sha256: str = ""


SCRATCHPEEK = Project(
    key="scratchpeek", repository="Sarr77/ScratchPeek", plugin_id="sarr.scratchpeek",
    archive_prefix="ScratchPeek", author_name="Sarr", author_id=19185477,
    plan_variable="SCRATCHPEEK_RELEASE_PLAN", token_secret="SCRATCHPEEK_RELEASE_TOKEN",
    package_layout="file-list",
)
WINDOWPEEK = Project(
    key="windowpeek", repository="Sarr77/WindowPeek", plugin_id="sarr.windowpeek",
    archive_prefix="WindowPeek", author_name="Sarr", author_id=19185477,
    plan_variable="WINDOWPEEK_RELEASE_PLAN", token_secret="WINDOWPEEK_RELEASE_TOKEN",
    package_layout="windowpeek-v1",
    package_script_sha256="61f8560a47e395c2c5a748db7d83fc9e140c2dac9ffa03b320de1f1497fecdc7",
)
PROJECTS = {project.key: project for project in (SCRATCHPEEK, WINDOWPEEK)}
