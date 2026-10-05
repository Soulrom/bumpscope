import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import griffe
from griffe import Alias, Object

from bumpscope import pypi

# griffe warns about every annotation it cannot resolve. That is noise for our users.
logging.getLogger("griffe").setLevel(logging.CRITICAL)

# Changes to these names are noise: they differ in every release by design.
IGNORED_NAMES = {"__version__"}
MAX_DETAILS_LENGTH = 70
MODULE_REMOVED = "Module was removed"


@dataclass(frozen=True)
class Parameter:
    name: str
    # Position and kind in the old signature, or in the new one for a parameter that is new.
    index: int
    # griffe allows a parameter without a kind. Matching then treats it as neither positional
    # nor keyword, so it never counts as passed.
    kind: griffe.ParameterKind | None
    # Set only when the parameter kind was changed.
    new_kind: griffe.ParameterKind | None = None


@dataclass(frozen=True)
class Change:
    kind: str
    public_paths: tuple[str, ...]
    definition_path: str
    parameter: Parameter | None = None
    details: str = ""

    def labels(self) -> list[str]:
        """How the change is shown: one label per public path, `__init__` as the class call."""
        suffix = f"({self.parameter.name})" if self.parameter else ""
        return [path.removesuffix(".__init__") + suffix for path in self.public_paths]

    def targets(self) -> list[str]:
        """Every path code can name the changed object by: public paths, then the definition path.

        A change to `__init__` is used through the class call, so `X.__init__` becomes `X`.
        """
        paths = dict.fromkeys([*self.public_paths, self.definition_path])
        return [path.removesuffix(".__init__") for path in paths]


def _shorten(value: Any) -> str:
    # `value` is a griffe breakage value (an expression, string, list of bases, or kind),
    # typed `Any` in griffe. All of them have a readable `str`.
    if isinstance(value, list):
        text = "[" + ", ".join(str(item) for item in value) + "]"
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= MAX_DETAILS_LENGTH else text[: MAX_DETAILS_LENGTH - 3] + "..."


def _is_public(path: str) -> bool:
    # griffe also records wildcard imports as aliases, e.g. `httpx.httpx/_models/*`. Skip those.
    return all(
        part.isidentifier()
        and (not part.startswith("_") or (part.startswith("__") and part.endswith("__")))
        for part in path.split(".")
    )


def _paths(obj: Object | Alias) -> set[str]:
    """Every dotted path the object can be reached by: its own, re-exports, and via its parents."""
    if obj.is_alias:
        # A removed re-export: only its own path is gone, its target may still exist.
        return {obj.path}
    paths = {obj.path, *obj.aliases}
    parent = obj.parent
    if parent is not None:
        paths |= {f"{path}.{obj.name}" for path in _paths(parent)}
    return paths


def _public_paths(obj: Object | Alias) -> tuple[str, ...]:
    public = sorted(path for path in _paths(obj) if _is_public(path))
    return tuple(public) or (obj.path,)


def _parameter(old: object, new: object) -> Parameter | None:
    param = next((v for v in (old, new) if isinstance(v, griffe.Parameter)), None)
    if param is None:
        return None
    siblings = list(param.function.parameters) if param.function else [param]
    index = next(i for i, sibling in enumerate(siblings) if sibling is param)
    new_kind = (
        new.kind
        if isinstance(old, griffe.Parameter) and isinstance(new, griffe.Parameter)
        else None
    )
    return Parameter(
        name=param.name,
        index=index,
        kind=param.kind,
        new_kind=new_kind if new_kind is not param.kind else None,
    )


def _explain(breakage: griffe.Breakage) -> Change:
    old, new = breakage.old_value, breakage.new_value
    obj = breakage.obj
    parameter = _parameter(old, new)

    details = ""
    if parameter is None and old is not None and new is not None:
        details = f"{_shorten(old)} -> {_shorten(new)}"
    return Change(
        kind=breakage.kind.value,
        public_paths=_public_paths(obj),
        definition_path=obj.path,
        parameter=parameter,
        details=details,
    )


def _load(module: str, directory: Path) -> Object | Alias:
    # allow_inspection=False: read the source only, never import or run package code.
    # resolve_aliases: record every re-export on the object, so changes get public paths.
    return griffe.load(
        module,
        search_paths=[directory],
        allow_inspection=False,
        resolve_aliases=True,
        resolve_implicit=True,
    )


def diff(package: str, old_version: str, new_version: str) -> list[Change]:
    """Return the breaking API changes between two versions of a package."""
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = pypi.download(package, old_version, Path(tmp) / "old")
        new_dir = pypi.download(package, new_version, Path(tmp) / "new")
        new_modules = pypi.top_level_modules(new_dir)

        changes = []
        for module in pypi.top_level_modules(old_dir):
            if module not in new_modules:
                changes.append(
                    Change(kind=MODULE_REMOVED, public_paths=(module,), definition_path=module)
                )
                continue
            breakages = griffe.find_breaking_changes(_load(module, old_dir), _load(module, new_dir))
            changes.extend(_explain(breakage) for breakage in breakages)

    changes = [c for c in changes if c.definition_path.rsplit(".", 1)[-1] not in IGNORED_NAMES]
    return sorted(set(changes), key=lambda change: (change.kind, change.labels()))
