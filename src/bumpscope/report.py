import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from itertools import groupby

from packaging.version import InvalidVersion, Version
from rich.console import Console
from rich.markup import escape

from bumpscope.impact import Impact

# Shared by all commands: 0 nothing found, 1 something found, 2 error.
EXIT_OK = 0
EXIT_FOUND = 1
EXIT_ERROR = 2

# `check` follows imported names only, so it can miss impacts. Every report says so.
LIMITATION = "method calls on instances are not analyzed"


def same_version(a: str | None, b: str | None) -> bool:
    """`2.0` and `2.0.0` are the same version. Unparsable versions compare as text."""
    if a is None or b is None:
        return a == b
    try:
        return Version(a) == Version(b)
    except InvalidVersion:
        return a == b


@dataclass(frozen=True)
class PackageResult:
    """The outcome of checking one package: Impacts (maybe none), unchecked, or up to date."""

    package: str
    from_version: str | None
    to_version: str | None = None
    impacts: tuple[Impact, ...] = ()
    # Set for an Unchecked package: why it could not be compared.
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.is_unchecked:
            if self.impacts:
                raise ValueError(f"{self.package}: an Unchecked package cannot have Impacts")
            return
        if self.from_version is None or self.to_version is None:
            raise ValueError(f"{self.package}: a checked package needs both versions")
        if self.impacts and self.is_up_to_date:
            raise ValueError(f"{self.package}: an up-to-date package cannot have Impacts")

    @property
    def is_unchecked(self) -> bool:
        return self.reason is not None

    @property
    def is_up_to_date(self) -> bool:
        return not self.is_unchecked and same_version(self.from_version, self.to_version)

    @property
    def is_affected(self) -> bool:
        return bool(self.impacts)


@dataclass(frozen=True)
class Report:
    results: tuple[PackageResult, ...]

    def _sorted(self, keep) -> list[PackageResult]:
        return sorted((r for r in self.results if keep(r)), key=lambda r: r.package.lower())

    @cached_property
    def affected(self) -> list[PackageResult]:
        return self._sorted(lambda r: r.is_affected)

    @cached_property
    def unchecked(self) -> list[PackageResult]:
        return self._sorted(lambda r: r.is_unchecked)

    @cached_property
    def no_impact(self) -> list[PackageResult]:
        return self._sorted(lambda r: not (r.is_affected or r.is_unchecked or r.is_up_to_date))

    @cached_property
    def up_to_date(self) -> list[PackageResult]:
        return self._sorted(lambda r: r.is_up_to_date)

    def exit_code(self) -> int:
        """1 if anything is affected; otherwise 2 if anything is unchecked; otherwise 0."""
        if self.affected:
            return EXIT_FOUND
        if self.unchecked:
            return EXIT_ERROR
        return EXIT_OK


def count(n: int, noun: str) -> str:
    """`1 impact`, `2 impacts`."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def print_groups(
    console: Console,
    groups: Iterable[tuple[str, list[str]]],
    indent: int = 0,
    spaced: bool = True,
) -> None:
    """Print each group as a yellow title, then its lines indented. Lines are rich markup.

    `spaced` adds a blank line after each group.
    """
    pad = " " * indent
    for title, lines in groups:
        console.print(f"{pad}[bold yellow]{title.upper()}[/bold yellow]")
        for line in lines:
            console.print(f"{pad}  {line}")
        if spaced:
            console.print()


def _versions(result: PackageResult) -> str:
    return escape(f"{result.package} {result.from_version} -> {result.to_version}")


def render_text(report: Report, console: Console) -> None:
    """Print the report: affected, unchecked, no impact, up-to-date count, limitation note."""
    updates = len(report.results) - len(report.up_to_date)
    console.print(f"{count(updates, 'update')} available\n")

    if report.affected:
        console.print(f"[bold]AFFECTS YOUR CODE ({len(report.affected)})[/bold]")
        for result in report.affected:
            console.print(f"  [bold]{_versions(result)}[/bold]")
            print_groups(
                console,
                (
                    (kind, [_impact_line(i) for i in group])
                    for kind, group in groupby(result.impacts, key=lambda i: i.change.kind)
                ),
                indent=4,
                spaced=False,
            )
            console.print()

    if report.unchecked:
        console.print(f"[bold]COULD NOT CHECK ({len(report.unchecked)})[/bold]")
        for result in report.unchecked:
            name = " ".join(filter(None, [result.package, result.from_version]))
            if result.from_version and result.to_version:
                name += f" -> {result.to_version}"
            console.print(f"  {escape(name)}: {escape(str(result.reason))}")
        console.print()

    if report.no_impact:
        console.print(f"[bold]NO IMPACT FOUND ({len(report.no_impact)})[/bold]")
        console.print("  " + ", ".join(_versions(r) for r in report.no_impact))
        console.print()

    if report.up_to_date:
        console.print(f"{count(len(report.up_to_date), 'package')} up to date")
    console.print(f"({LIMITATION})")


def _location(impact: Impact) -> str:
    return f"{impact.usage.file.as_posix()}:{impact.usage.line}"


def _impact_line(impact: Impact) -> str:
    return escape(f"{_location(impact)}  {impact.label}")


# GitHub PR comments allow about 65,000 characters; leave room for whatever wraps the report.
MARKDOWN_LIMIT = 60_000

# Characters that mean something in GitHub Markdown, escaped in free text such as a reason.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+!|~])")


def _cell(text: str) -> str:
    """Escape `|` so it does not end a table cell, even inside a code span."""
    return text.replace("|", r"\|")


def _md_text(text: str) -> str:
    """Free text, such as an exception message, as one line of literal Markdown."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def _md_range(result: PackageResult) -> str:
    """`1.0 → 2.0`, `1.0` when the target is unknown, `→ 2.0` when the installed one is."""
    if result.from_version and result.to_version:
        return f"{result.from_version} → {result.to_version}"
    if result.to_version:
        return f"→ {result.to_version}"
    return result.from_version or ""


def _md_versions(result: PackageResult) -> str:
    return f"{result.package} {_md_range(result)}".rstrip()


def _md_title(report: Report) -> str:
    updates = len(report.results) - len(report.up_to_date)
    if report.affected:
        verb = "affects" if len(report.affected) == 1 else "affect"
        return f"{len(report.affected)} of {count(updates, 'update')} {verb} your code"
    if not updates:
        return "no updates available"
    # Only packages that were compared count as "no impact found".
    if not report.no_impact:
        return f"{count(len(report.unchecked), 'package')} could not be checked"
    title = f"no impact found in {count(len(report.no_impact), 'update')}"
    if report.unchecked:
        title += f", {len(report.unchecked)} could not be checked"
    return title


def _assemble(report: Report, impacts_shown: int, no_impact_shown: int) -> str:
    """The Markdown report with only the first `impacts_shown` Impact rows and the first
    `no_impact_shown` NO IMPACT FOUND packages. Everything else is always complete."""
    lines = [f"## bumpscope: {_md_title(report)}", ""]

    if report.affected:
        lines += ["### Affects your code", ""]
        left = impacts_shown
        for result in report.affected:
            rows = result.impacts[: max(left, 0)]
            left -= len(result.impacts)
            if not rows:
                continue
            lines += [
                f"#### {_md_versions(result)}",
                "",
                "| Location | Change | Kind |",
                "|---|---|---|",
            ]
            for i in rows:
                lines.append(
                    f"| `{_cell(_location(i))}` | `{_cell(i.label)}` | {_cell(i.change.kind)} |"
                )
            lines.append("")
        hidden = sum(len(r.impacts) for r in report.affected) - impacts_shown
        if hidden > 0:
            lines += [f"...and {count(hidden, 'more impact')}.", ""]

    if report.unchecked:
        lines += ["### Could not check", ""]
        for result in report.unchecked:
            versions = _md_range(result)
            reason = _md_text(str(result.reason))
            lines.append(f"- `{result.package}`{' ' + versions if versions else ''}: {reason}")
        lines.append("")

    if report.no_impact:
        shown = [_md_versions(r) for r in report.no_impact[:no_impact_shown]]
        hidden = len(report.no_impact) - len(shown)
        if hidden:
            shown.append(f"and {hidden} more")
        summary = f"No impact found ({len(report.no_impact)})"
        lines += [
            f"<details><summary>{summary}</summary>",
            "",
            ", ".join(shown),
            "",
            "</details>",
            "",
        ]

    footer = f"{LIMITATION[0].upper()}{LIMITATION[1:]}."
    if report.up_to_date:
        footer = f"{count(len(report.up_to_date), 'package')} up to date. {footer}"
    lines.append(footer)
    return "\n".join(lines) + "\n"


def _most_that_fit(upper: int, fits) -> int:
    """The largest n in [0, upper] with fits(n), for a `fits` that only gets harder as n grows."""
    low, high = 0, upper
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low


def render_markdown(report: Report, limit: int = MARKDOWN_LIMIT) -> str:
    """The report as Markdown, for CI summaries and PR comments, at most about `limit` long.

    When it is too long, NO IMPACT FOUND is trimmed first, then Impact rows from the end, each
    ending with "and N more". The title and COULD NOT CHECK are never cut, so the report can
    still exceed `limit` when they alone do. Only whole rows and list items are dropped.
    """
    impacts = sum(len(r.impacts) for r in report.affected)
    no_impact = len(report.no_impact)
    full = _assemble(report, impacts, no_impact)
    if len(full) <= limit:
        return full

    if len(_assemble(report, impacts, 0)) <= limit:
        kept = _most_that_fit(no_impact, lambda n: len(_assemble(report, impacts, n)) <= limit)
        return _assemble(report, impacts, kept)
    kept = _most_that_fit(impacts, lambda n: len(_assemble(report, n, 0)) <= limit)
    return _assemble(report, kept, 0)
