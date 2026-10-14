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


def _impact_line(impact: Impact) -> str:
    location = f"{impact.usage.file.as_posix()}:{impact.usage.line}"
    return escape(f"{location}  {impact.label}")
