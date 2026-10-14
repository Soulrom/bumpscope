"""What the checked project has installed in its `.venv`, and which of it the code imports."""

import csv
import io
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from email.parser import HeaderParser
from enum import StrEnum
from pathlib import Path

from bumpscope.project import SKIPPED_DIRS, ProjectError
from bumpscope.usages import ImportedModule

STDLIB_MODULES = frozenset(sys.stdlib_module_names)
NOT_FROM_PYPI = "not installed from PyPI"
UNKNOWN_MODULES = "cannot tell which modules it provides"

# File suffixes that make an importable module. Compiled ones carry a platform tag:
# `_yaml.cp313-win_amd64.pyd`, `_speedups.cpython-313-x86_64-linux-gnu.so`.
MODULE_SUFFIXES = (".py", ".pyi", ".pyd", ".so")


class Origin(StrEnum):
    PYPI = "pypi"
    # `pip install -e .`: the project's own code.
    EDITABLE = "editable"
    # Installed from git, a URL or a local path. PyPI may host an unrelated package of that name.
    OTHER = "other"


@dataclass(frozen=True)
class InstalledPackage:
    name: str
    version: str
    # Dotted module paths the package provides, including parent packages:
    # {"yaml", "yaml.loader", ...}. None when the package records neither RECORD nor
    # top_level.txt, so nobody can tell.
    modules: frozenset[str] | None
    origin: Origin


@dataclass(frozen=True)
class ImportedPackages:
    to_check: tuple[InstalledPackage, ...]
    # Imported packages that must not or cannot be compared, with the reason.
    unchecked: tuple[tuple[InstalledPackage, str], ...]


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


def _module_of(path: str) -> str | None:
    """`yaml/loader.py` -> `yaml.loader`. None for files that are not modules."""
    if path.startswith("../") or "__pycache__" in path:
        return None
    parts = path.split("/")
    name = parts[-1]
    if not name.endswith(MODULE_SUFFIXES):
        return None
    # Drop the suffix and any platform tag: `_yaml.cp313-win_amd64.pyd` -> `_yaml`.
    parts[-1] = name.split(".", 1)[0]
    if parts[-1] == "__init__":
        parts.pop()
    # Only real import paths: this drops `.dist-info`, `.data` and vendored folders such as
    # `numpy.libs/`, which are not importable.
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def _with_parents(modules: Iterable[str]) -> frozenset[str]:
    """A module also makes its parent packages importable: `a.b.c` gives `a`, `a.b`, `a.b.c`."""
    result = set()
    for module in modules:
        parts = module.split(".")
        result.update(".".join(parts[: i + 1]) for i in range(len(parts)))
    return frozenset(result)


def _read_record(info: Path) -> frozenset[str] | None:
    try:
        text = (info / "RECORD").read_text(encoding="utf-8")
        rows = list(csv.reader(io.StringIO(text)))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    return _with_parents(module for row in rows if row and (module := _module_of(row[0])))


def _read_top_level(info: Path) -> frozenset[str] | None:
    try:
        text = (info / "top_level.txt").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return frozenset(line.strip() for line in text.splitlines() if line.strip())


def _modules(info: Path, name: str, origin: Origin) -> frozenset[str] | None:
    record = _read_record(info)
    top_level = _read_top_level(info)
    if origin is Origin.EDITABLE:
        # PEP 660 editable installs list only a .pth or finder file in RECORD, so also go by
        # top_level.txt and by the package name (`my-lib` provides `my_lib`).
        by_name = {name.replace("-", "_")}
        return (record or frozenset()) | (top_level or frozenset()) | by_name
    return record if record is not None else top_level


def _origin(info: Path) -> Origin:
    direct_url = info / "direct_url.json"
    if not direct_url.is_file():
        return Origin.PYPI
    try:
        data = json.loads(direct_url.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # The file exists, so the package came from a direct URL, even if it is unreadable.
        return Origin.OTHER
    if isinstance(data, dict) and data.get("dir_info", {}).get("editable"):
        return Origin.EDITABLE
    return Origin.OTHER


def _name_and_version(info: Path) -> tuple[str, str] | None:
    """From METADATA, or None when it is missing, unreadable or incomplete."""
    try:
        text = (info / "METADATA").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    headers = HeaderParser().parsestr(text)
    name, version = headers.get("Name"), headers.get("Version")
    return (name, version) if name and version else None


def _dist_infos(project: Path) -> list[Path]:
    venv = project / ".venv"
    if not venv.is_dir():
        raise ProjectError(
            f"No .venv found in {project}. Pass --from to set the installed version."
        )
    return [info for sp in _site_packages(venv) for info in sorted(sp.glob("*.dist-info"))]


def _package(info: Path, name: str, version: str) -> InstalledPackage:
    origin = _origin(info)
    return InstalledPackage(name, version, _modules(info, name, origin), origin)


def installed_packages(project: Path) -> list[InstalledPackage]:
    """Every package installed in the project's `.venv`. Broken `.dist-info` are skipped."""
    packages = []
    for info in _dist_infos(project):
        found = _name_and_version(info)
        if found:
            packages.append(_package(info, *found))
    return packages


def find_installed(project: Path, package: str) -> InstalledPackage:
    """The installed `package`, matching names the way PyPI does.

    Only METADATA is read until the package is found, so files of unrelated packages cannot
    get in the way.
    """
    wanted = _normalize(package)
    for info in _dist_infos(project):
        found = _name_and_version(info)
        if found and _normalize(found[0]) == wanted:
            return _package(info, *found)
    raise ProjectError(
        f"{package} is not installed in {project / '.venv'}. "
        "Pass --from to set the installed version."
    )


def own_modules(project: Path) -> set[str]:
    """Top-level names the project itself provides, from the project root or `src/`.

    Skips the same folders as `project.python_files`: hidden ones, build output and
    environments.
    """
    names = set()
    for directory in (project, project / "src"):
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            if entry.is_dir():
                if (
                    not entry.name.startswith(".")
                    and entry.name not in SKIPPED_DIRS
                    and not (entry / "pyvenv.cfg").exists()
                ):
                    names.add(entry.name)
            elif entry.suffix == ".py":
                names.add(entry.stem)
    return names


def _providers(path: str, installed: Iterable[InstalledPackage]) -> list[InstalledPackage]:
    """The packages that provide the longest prefix of `path`. Several when they tie."""
    parts = path.split(".")
    best, providers = 0, []
    for package in installed:
        if not package.modules:
            continue
        length = next(
            (n for n in range(len(parts), 0, -1) if ".".join(parts[:n]) in package.modules), 0
        )
        if length > best:
            best, providers = length, [package]
        elif length and length == best:
            providers.append(package)
    return providers


def imported_packages(
    project: Path, installed: list[InstalledPackage], imports: list[ImportedModule]
) -> ImportedPackages:
    """Which installed packages the imports come from, and which of them cannot be checked."""
    own = own_modules(project)
    to_check: dict[str, InstalledPackage] = {}
    unchecked: dict[str, tuple[InstalledPackage, str]] = {}
    has_unmatched = False

    for module in imports:
        top = module.path.split(".", 1)[0]
        if top in STDLIB_MODULES:
            continue
        # Installed packages come first: a project folder named `alembic/` must not hide the
        # alembic package. The project's own modules only keep an import from being unmatched.
        providers = _providers(module.path, installed)
        if not providers and not module.optional and top not in own:
            has_unmatched = True
        for package in providers:
            if package.origin is Origin.PYPI:
                to_check[package.name] = package
            elif package.origin is Origin.OTHER:
                unchecked[package.name] = (package, NOT_FROM_PYPI)

    # A package that does not say what it provides might be behind an import nobody matched.
    if has_unmatched:
        for package in installed:
            if package.modules is None and package.origin is not Origin.EDITABLE:
                reason = NOT_FROM_PYPI if package.origin is Origin.OTHER else UNKNOWN_MODULES
                unchecked.setdefault(package.name, (package, reason))

    return ImportedPackages(tuple(to_check.values()), tuple(unchecked.values()))
