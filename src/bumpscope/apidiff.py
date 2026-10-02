import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import griffe

from bumpscope import pypi

# griffe warns about every annotation it cannot resolve. That is noise for our users.
logging.getLogger("griffe").setLevel(logging.CRITICAL)

# Changes to these names are noise: they differ in every release by design.
IGNORED_NAMES = {"__version__"}
MAX_DETAILS_LENGTH = 70


@dataclass(frozen=True)
class Change:
    kind: str
    path: str
    details: str = ""


def _shorten(value: object) -> str:
    if isinstance(value, list):
        text = "[" + ", ".join(str(item) for item in value) + "]"
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= MAX_DETAILS_LENGTH else text[: MAX_DETAILS_LENGTH - 3] + "..."


def _explain(breakage: griffe.Breakage) -> Change:
    data = breakage.as_dict()
    path = data["object_path"]
    old, new = data.get("old_value"), data.get("new_value")

    # For parameter changes griffe returns the parameter itself. Show its name.
    parameter = next((v for v in (old, new) if isinstance(v, griffe.Parameter)), None)
    if parameter is not None:
        return Change(kind=breakage.kind.value, path=f"{path}({parameter.name})")

    details = f"{_shorten(old)} -> {_shorten(new)}" if old is not None and new is not None else ""
    return Change(kind=breakage.kind.value, path=path, details=details)


def _load(module: str, directory: Path) -> griffe.Object:
    # allow_inspection=False: read the source only, never import or run package code.
    return griffe.load(module, search_paths=[directory], allow_inspection=False)


def diff(package: str, old_version: str, new_version: str) -> list[Change]:
    """Return the breaking API changes between two versions of a package."""
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = pypi.download(package, old_version, Path(tmp) / "old")
        new_dir = pypi.download(package, new_version, Path(tmp) / "new")
        new_modules = pypi.top_level_modules(new_dir)

        changes = []
        for module in pypi.top_level_modules(old_dir):
            if module not in new_modules:
                changes.append(Change(kind="Module was removed", path=module))
                continue
            breakages = griffe.find_breaking_changes(_load(module, old_dir), _load(module, new_dir))
            changes.extend(_explain(breakage) for breakage in breakages)

    changes = [c for c in changes if c.path.rsplit(".", 1)[-1] not in IGNORED_NAMES]
    return sorted(set(changes), key=lambda change: (change.kind, change.path))
