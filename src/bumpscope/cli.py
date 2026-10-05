from collections.abc import Iterable
from itertools import groupby
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.spinner import SPINNERS
from rich.status import Status

from bumpscope import apidiff, impact, project, pypi, usages

app = typer.Typer(help="See which dependency updates actually affect your Python code.")
console = Console(highlight=False, soft_wrap=True)

# Shared by all commands: 0 nothing found, 1 something found, 2 error.
EXIT_FOUND = 1
EXIT_ERROR = 2

# `check` follows imported names only, so it can miss impacts. Say so whenever nothing matched.
LIMITATION = "method calls on instances are not analyzed"


def _count(items: list, noun: str) -> str:
    return f"{len(items)} {noun}{'' if len(items) == 1 else 's'}"


def _status(message: str) -> Status:
    """A spinner that the console can actually print.

    The default spinner draws braille characters. On Windows, output sent to `NUL` (and old
    cp1252 consoles) claims to be a terminal that cannot encode them, and the spinner crashed
    with UnicodeEncodeError. Fall back to an ASCII spinner there.
    """
    spinner = "dots"
    try:
        "".join(SPINNERS[spinner]["frames"]).encode(console.encoding)
    except UnicodeEncodeError:
        spinner = "line"
    return console.status(message, spinner=spinner)


def _print_title(package: str, old_version: str, new_version: str) -> None:
    console.print(f"\n[bold]{package}[/bold] {old_version} -> {new_version}\n")


def _print_groups(groups: Iterable[tuple[str, list[str]]]) -> None:
    """Print each group as a yellow title, then its lines indented. Lines are rich markup."""
    for title, lines in groups:
        console.print(f"[bold yellow]{title.upper()}[/bold yellow]")
        for line in lines:
            console.print(f"  {line}")
        console.print()


def _change_lines(change: apidiff.Change) -> list[str]:
    """One line per public path, with old -> new details dimmed when there are any."""
    details = f"  [dim]{escape(change.details)}[/dim]" if change.details else ""
    return [escape(label) + details for label in change.labels()]


def _fail(error: Exception) -> NoReturn:
    console.print(f"[red]Error:[/red] {escape(str(error))}")
    raise typer.Exit(code=EXIT_ERROR) from error


@app.command()
def diff(package: str, old_version: str, new_version: str) -> None:
    """Show breaking API changes between two versions of a package."""
    try:
        with _status(f"Comparing {package} {old_version} and {new_version}..."):
            changes = apidiff.diff(package, old_version, new_version)
    except pypi.PackageNotFoundError as error:
        _fail(error)

    _print_title(package, old_version, new_version)
    if not changes:
        console.print("[green]No breaking API changes found.[/green]")
        return

    _print_groups(
        (kind, [line for change in group for line in _change_lines(change)])
        for kind, group in groupby(changes, key=lambda c: c.kind)
    )
    console.print(_count(changes, "breaking change"))
    raise typer.Exit(code=EXIT_FOUND)


@app.command()
def check(
    package: str,
    project_dir: Annotated[
        Path,
        typer.Option("--project", help="The project to check.", exists=True, file_okay=False),
    ] = Path("."),
    from_version: Annotated[
        str | None,
        typer.Option("--from", help="Installed version. Default: read from the project's .venv."),
    ] = None,
    to_version: Annotated[
        str | None,
        typer.Option("--to", help="Target version. Default: the latest stable release."),
    ] = None,
) -> None:
    """Show which breaking changes in a package update affect your code."""
    project_dir = project_dir.resolve()
    try:
        old_version = from_version or project.installed_version(project_dir, package)
        new_version = to_version or pypi.latest_version(package)
        if old_version == new_version:
            console.print(f"{package} {old_version} is already the target version.")
            return
        with _status(f"Comparing {package} {old_version} and {new_version}..."):
            changes = apidiff.diff(package, old_version, new_version)
    except (pypi.PackageNotFoundError, project.ProjectError) as error:
        _fail(error)

    header = f"{package} {old_version} -> {new_version}"
    if not changes:
        console.print(f"{header}: no breaking API changes found.")
        return

    roots = {target.split(".", 1)[0] for change in changes for target in change.targets()}
    with _status(f"Scanning {project_dir}..."):
        found = usages.find_usages(project.python_files(project_dir), roots, project_dir)
    impacts = impact.find_impacts(changes, found)

    if not impacts:
        changed = _count(changes, "breaking change")
        console.print(f"{header}: {changed}, none matched in your code ({LIMITATION})")
        return

    _print_title(package, old_version, new_version)
    _print_groups(
        (kind, [escape(f"{i.usage.file.as_posix()}:{i.usage.line}  {i.label}") for i in group])
        for kind, group in groupby(impacts, key=lambda i: i.change.kind)
    )
    console.print(
        f"{_count(impacts, 'impact')} from {_count(changes, 'breaking change')} ({LIMITATION})"
    )
    raise typer.Exit(code=EXIT_FOUND)
