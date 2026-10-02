import os
import re
from email.parser import HeaderParser
from pathlib import Path

# Directories that never hold the project's own code.
SKIPPED_DIRS = {"build", "dist", "__pycache__"}


class ProjectError(Exception):
    """The checked project cannot tell us what we need, e.g., it has no `.venv`."""


def _normalize(name: str) -> str:
    # PEP 503: `PyYAML` and `pyyaml`, or `my_pkg` and `my-pkg`, name the same package.
    return re.sub(r"[-_.]+", "-", name).lower()


def _site_packages(venv: Path) -> list[Path]:
    # Windows: .venv/Lib/site-packages. Elsewhere: .venv/lib/python3.X/site-packages.
    return [
        p
        for p in [venv / "Lib" / "site-packages", *venv.glob("lib/python*/site-packages")]
        if p.is_dir()
    ]


def installed_version(project: Path, package: str) -> str:
    """Return the version of `package` installed in the project's `.venv`."""
    venv = project / ".venv"
    if not venv.is_dir():
        raise ProjectError(
            f"No .venv found in {project}. Pass --from to set the installed version."
        )

    wanted = _normalize(package)
    for site_packages in _site_packages(venv):
        for metadata in site_packages.glob("*.dist-info/METADATA"):
            headers = HeaderParser().parsestr(metadata.read_text(encoding="utf-8"))
            if _normalize(headers.get("Name", "")) == wanted:
                return headers["Version"]
    raise ProjectError(
        f"{package} is not installed in {venv}. Pass --from to set the installed version."
    )


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
