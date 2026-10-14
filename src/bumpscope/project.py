import os
from pathlib import Path

# Directories that never hold the project's own code.
SKIPPED_DIRS = {"build", "dist", "__pycache__"}


class ProjectError(Exception):
    """The checked project cannot tell us what we need, e.g., it has no `.venv`."""


def python_files(project: Path) -> list[Path]:
    """Every `.py` file of the project itself, skipping environments and build output."""
    files = []
    for root, dirs, names in os.walk(project):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d not in SKIPPED_DIRS
            and not (Path(root) / d / "pyvenv.cfg").exists()
        )
        files.extend(Path(root) / name for name in sorted(names) if name.endswith(".py"))
    return files
