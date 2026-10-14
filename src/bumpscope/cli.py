from itertools import groupby
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.spinner import SPINNERS
from rich.status import Status

from bumpscope import apidiff, environment, impact, project, pypi, usages
from bumpscope.report import (
    EXIT_ERROR,
    EXIT_FOUND,
    PackageResult,
    Report,
    count,
    print_groups,
    render_text,
    same_version,
)

app = typer.Typer(help="See which dependency updates actually affect your Python code.")
# The report goes to stdout; progress and errors go to stderr, so the report can be redirected.
console = Console(highlight=False, soft_wrap=True)
err_console = Console(stderr=True, highlight=False, soft_wrap=True)


def _status(message: str) -> Status:
    """A spinner on stderr that the console can actually print.

    The default spinner draws braille characters. On Windows, output sent to `NUL` (and old
    cp1252 consoles) claims to be a terminal that cannot encode them, and the spinner crashed
    with UnicodeEncodeError. Fall back to an ASCII spinner there.
    """
    spinner = "dots"
    try:
        "".join(SPINNERS[spinner]["frames"]).encode(err_console.encoding)
    except UnicodeEncodeError:
        spinner = "line"
    return err_console.status(message, spinner=spinner)


def _change_lines(change: apidiff.Change) -> list[str]:
    """One line per public path, with old -> new details dimmed when there are any."""
    details = f"  [dim]{escape(change.details)}[/dim]" if change.details else ""
    return [escape(label) + details for label in change.labels()]


def _fail(error: Exception) -> NoReturn:
    err_console.print(f"[red]Error:[/red] {escape(str(error))}")
    raise typer.Exit(code=EXIT_ERROR) from error


@app.command()
def diff(package: str, old_version: str, new_version: str) -> None:
    """Show breaking API changes between two versions of a package."""
    try:
        with _status(f"Comparing {package} {old_version} and {new_version}..."):
            changes = apidiff.diff(package, old_version, new_version)
    except pypi.PackageNotFoundError as error:
        _fail(error)

    console.print(
        f"\n[bold]{escape(package)}[/bold] {escape(old_version)} -> {escape(new_version)}\n"
    )
    if not changes:
        console.print("[green]No breaking API changes found.[/green]")
        return

    print_groups(
        console,
        (
            (kind, [line for change in group for line in _change_lines(change)])
            for kind, group in groupby(changes, key=lambda c: c.kind)
        ),
    )
    console.print(count(len(changes), "breaking change"))
    raise typer.Exit(code=EXIT_FOUND)


def _check_package(
    package: str, project_dir: Path, from_version: str | None, to_version: str | None
) -> PackageResult:
    """Compare one package and match its breaking changes against the project's code.

    Any failure while fetching or comparing the package (missing on PyPI, network, broken
    wheel, griffe) makes it an Unchecked package with the reason. A project problem, such as no
    `.venv`, raises `ProjectError`.

    When the installed version is read from `.venv`, a package not installed from PyPI (git, a
    URL, a local path or an editable install) is not compared: PyPI may host an unrelated
    package of the same name. An explicit `--from` skips this rule.
    """
    if from_version is None:
        installed = environment.find_installed(project_dir, package)
        if installed.origin is not environment.Origin.PYPI:
            return PackageResult(
                package, installed.version, to_version, reason=environment.NOT_FROM_PYPI
            )
        from_version = installed.version
    old_version = from_version
    new_version = to_version
    try:
        new_version = new_version or pypi.latest_version(package)
        if same_version(old_version, new_version):
            return PackageResult(package, old_version, new_version)
        with _status(f"Comparing {package} {old_version} and {new_version}..."):
            changes = apidiff.diff(package, old_version, new_version)
    except pypi.PackageNotFoundError as error:
        return PackageResult(package, old_version, new_version, reason=str(error))
    except Exception as error:  # noqa: BLE001 - one package's failure must not stop the run
        reason = f"{type(error).__name__}: {error}"
        return PackageResult(package, old_version, new_version, reason=reason)

    if not changes:
        return PackageResult(package, old_version, new_version)
    roots = {target.split(".", 1)[0] for change in changes for target in change.targets()}
    with _status(f"Scanning {project_dir}..."):
        found = usages.find_usages(project.python_files(project_dir), roots, project_dir)
    impacts = impact.find_impacts(changes, found)
    return PackageResult(package, old_version, new_version, impacts=tuple(impacts))


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
    try:
        result = _check_package(package, project_dir.resolve(), from_version, to_version)
    except project.ProjectError as error:
        _fail(error)

    report = Report((result,))
    render_text(report, console)
    raise typer.Exit(code=report.exit_code())
