import sys
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import StrEnum
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
    render_markdown,
    render_text,
    same_version,
)

app = typer.Typer(help="See which dependency updates actually affect your Python code.")
# The report goes to stdout; progress and errors go to stderr, so the report can be redirected.
console = Console(highlight=False, soft_wrap=True)
err_console = Console(stderr=True, highlight=False, soft_wrap=True)

# Packages checked at the same time in a full check. Most of the time goes to downloads.
WORKERS = 8

# Finds the Usages of the given top-level modules in the project.
Scan = Callable[[set[str]], list[usages.Usage]]


class Format(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"


class _NoStatus:
    """Stands in for a spinner when there must be none, e.g. for a Markdown report."""

    def __enter__(self) -> "_NoStatus":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def update(self, message: str) -> None:
        return None


def _status(message: str, show: bool = True) -> Status | _NoStatus:
    """A spinner on stderr that the console can actually print, or none when `show` is False.

    The default spinner draws braille characters. On Windows, output sent to `NUL` (and old
    cp1252 consoles) claims to be a terminal that cannot encode them, and the spinner crashed
    with UnicodeEncodeError. Fall back to an ASCII spinner there.
    """
    if not show:
        return _NoStatus()
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


def _write_utf8(text: str) -> None:
    """Write to stdout as UTF-8 with "\\n" line endings, as is: no colors, no wrapping.

    On Windows a redirected stdout uses the locale encoding (e.g. cp1251), which cannot encode
    the "→" in a Markdown report, and turns "\\n" into "\\r\\n". This runs last, so changing the
    stream's settings affects nothing else.
    """
    stream = sys.stdout
    if stream is None:  # pythonw and some embeddings have no stdout at all
        return
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", newline="\n")
    stream.write(text)
    stream.flush()


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


def _compare(package: str, old_version: str, to_version: str | None, scan: Scan) -> PackageResult:
    """Fetch, compare and match one package against the project's code.

    A failure of the package itself (missing on PyPI, network, broken wheel, griffe) makes it
    an Unchecked package with the reason. Matching against the project runs outside that net:
    a problem there is not the package's fault. Safe to run in a thread: `apidiff.diff`
    downloads into its own temp directories and loads with its own griffe calls, and `scan`
    only reads.
    """
    new_version = to_version
    try:
        new_version = new_version or pypi.latest_version(package)
        if same_version(old_version, new_version):
            return PackageResult(package, old_version, new_version)
        changes = apidiff.diff(package, old_version, new_version)
    except pypi.PackageNotFoundError as error:
        return PackageResult(package, old_version, new_version, reason=str(error))
    except Exception as error:  # noqa: BLE001 - one package's failure must not stop the run
        reason = f"{type(error).__name__}: {error}"
        return PackageResult(package, old_version, new_version, reason=reason)

    if not changes:
        return PackageResult(package, old_version, new_version)
    roots = {target.split(".", 1)[0] for change in changes for target in change.targets()}
    impacts = impact.find_impacts(changes, scan(roots))
    return PackageResult(package, old_version, new_version, impacts=tuple(impacts))


def _check_one(
    package: str,
    project_dir: Path,
    from_version: str | None,
    to_version: str | None,
    progress: bool = True,
) -> PackageResult:
    """`check PACKAGE`. A project problem, such as no `.venv`, raises `ProjectError`.

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

    def scan(roots: set[str]) -> list[usages.Usage]:
        # Only reached when there are changes to match, so an up-to-date package skips the walk.
        return usages.find_usages(project.python_files(project_dir), roots, project_dir)

    with _status(f"Checking {package}...", show=progress):
        return _compare(package, from_version, to_version, scan)


def _check_all(project_dir: Path, progress: bool = True) -> list[PackageResult]:
    """`check` with no package: every Imported package, in a thread pool.

    The project is read once, before the pool starts: its imports, the packages they come
    from, and the Usages of all of them. The workers only read that scan.
    """
    with _status("Finding imported packages...", show=progress) as status:
        installed = environment.installed_packages(project_dir)
        files = project.python_files(project_dir)
        imports = usages.imported_modules(files, project_dir)
        found = environment.imported_packages(project_dir, installed, imports)
        roots = {
            module.split(".", 1)[0] for package in found.to_check for module in package.modules
        }
        # Grouped by top-level module, so each package is matched only against its own Usages.
        by_root: dict[str, list[usages.Usage]] = defaultdict(list)
        for usage in usages.find_usages(files, roots, project_dir):
            by_root[usage.path.split(".", 1)[0]].append(usage)

        def scan(package_roots: set[str]) -> list[usages.Usage]:
            return [usage for root in package_roots for usage in by_root.get(root, [])]

        results = [PackageResult(p.name, p.version, reason=r) for p, r in found.unchecked]
        total = len(found.to_check)
        status.update(f"Checking {count(total, 'package')}...")
        pool = ThreadPoolExecutor(max_workers=WORKERS)
        try:
            futures = {
                pool.submit(_compare, p.name, p.version, None, scan): p for p in found.to_check
            }
            for done, future in enumerate(as_completed(futures), start=1):
                results.append(future.result())
                status.update(f"Checking {done}/{total}: {futures[future].name}")
        finally:
            # On Ctrl+C or an error, drop the queued packages instead of waiting for them all.
            pool.shutdown(wait=False, cancel_futures=True)
    return results


@app.command()
def check(
    package: Annotated[
        str | None,
        typer.Argument(help="One package to check. Default: every package your code imports."),
    ] = None,
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
    output_format: Annotated[
        Format,
        typer.Option("--format", help="text for the terminal, markdown for CI and PR comments."),
    ] = Format.TEXT,
) -> None:
    """Show which breaking changes in your package updates affect your code."""
    if package is None and (from_version or to_version):
        _fail(ValueError("--from and --to need a PACKAGE: they set the versions of one package."))

    project_dir = project_dir.resolve()
    markdown = output_format is Format.MARKDOWN
    try:
        if package is None:
            results = _check_all(project_dir, progress=not markdown)
        else:
            results = [_check_one(package, project_dir, from_version, to_version, not markdown)]
    except project.ProjectError as error:
        if markdown:
            # CI posts stdout as is: say what went wrong rather than post an empty comment.
            _write_utf8(f"## bumpscope: could not run\n\n{error}\n")
        _fail(error)

    report = Report(tuple(results))
    if markdown:
        _write_utf8(render_markdown(report))
    else:
        render_text(report, console)
    raise typer.Exit(code=report.exit_code())
