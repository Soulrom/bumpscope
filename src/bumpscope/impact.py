from dataclasses import dataclass

from griffe import BreakageKind, ParameterKind

from bumpscope.apidiff import MODULE_REMOVED, Change, Parameter
from bumpscope.usages import Usage

# Matched by any reference: an import, an attribute access or a call.
ANY_REFERENCE = {
    BreakageKind.OBJECT_REMOVED,
    BreakageKind.OBJECT_CHANGED_KIND,
    BreakageKind.CLASS_REMOVED_BASE,
    MODULE_REMOVED,
}
POSITIONAL = {ParameterKind.positional_only, ParameterKind.positional_or_keyword}
KEYWORD = {ParameterKind.positional_or_keyword, ParameterKind.keyword_only}


@dataclass(frozen=True)
class Impact:
    change: Change
    usage: Usage
    # The changed path as it appears in the user's code, e.g. `httpx.Client(proxies)`.
    label: str


def _targets(change: Change) -> list[str]:
    # A change to `__init__` is used through the class call.
    paths = dict.fromkeys([*change.public_paths, change.definition_path])
    return [path.removesuffix(".__init__") for path in paths]


def _position(change: Change, parameter: Parameter) -> int:
    # Calling the class passes `self` implicitly, so `__init__(self, app)` takes `app` first.
    implicit_self = change.definition_path.endswith(".__init__")
    return parameter.index - implicit_self


def _affects(change: Change, usage: Usage) -> bool:
    if change.kind in ANY_REFERENCE:
        return True

    parameter = change.parameter
    if parameter is None or usage.kind != "call":
        return False

    positional = parameter.kind in POSITIONAL
    keyword = parameter.kind in KEYWORD
    positionally = positional and usage.positional_count > _position(change, parameter)
    by_keyword = keyword and parameter.name in usage.keywords
    # `f(*args)` or `f(**kwargs)` may or may not pass it. We cannot tell, so we lean towards an
    # impact: a false alarm is better than a missed break.
    maybe_positionally = positionally or (positional and usage.unpacks_args)
    maybe_by_keyword = by_keyword or (keyword and usage.unpacks_kwargs)

    match change.kind:
        case BreakageKind.PARAMETER_REMOVED:
            return maybe_positionally or maybe_by_keyword
        case BreakageKind.PARAMETER_MOVED:
            return maybe_positionally
        case BreakageKind.PARAMETER_CHANGED_KIND:
            new_kind = parameter.new_kind
            return (maybe_positionally and new_kind not in POSITIONAL) or (
                maybe_by_keyword and new_kind not in KEYWORD
            )
        case BreakageKind.PARAMETER_ADDED_REQUIRED | BreakageKind.PARAMETER_CHANGED_REQUIRED:
            # Only a definite pass clears it, so unpacking alone still counts as an impact.
            return not (positionally or by_keyword)
    # Default, return type and attribute changes are not checked: see the design doc.
    return False


def _match(change: Change, usage: Usage) -> str | None:
    """Return the changed path the usage refers to, or None."""
    for target in _targets(change):
        if usage.path == target:
            return target
        # Using `demo.Client.send` also uses `demo.Client`, so it counts if `Client` was removed.
        if change.kind in ANY_REFERENCE and usage.path.startswith(target + "."):
            return target
    return None


def find_impacts(changes: list[Change], usages: list[Usage]) -> list[Impact]:
    """Pair each breaking change with the places in the project it affects."""
    impacts = []
    for change in changes:
        suffix = f"({change.parameter.name})" if change.parameter else ""
        for usage in usages:
            target = _match(change, usage)
            if target is not None and _affects(change, usage):
                impacts.append(Impact(change, usage, target + suffix))
    return sorted(
        impacts, key=lambda i: (i.change.kind, i.usage.file.as_posix(), i.usage.line, i.label)
    )
