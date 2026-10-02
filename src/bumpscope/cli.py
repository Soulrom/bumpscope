from itertools import groupby

import typer
from rich.console import Console
from rich.markup import escape

from bumpscope import apidiff, pypi

app = typer.Typer(help="See which dependency updates actually affect your Python code.")
console = Console(highlight=False, soft_wrap=True)


@app.callback()
def main() -> None:
    """Keep `bumpscope <command>` form even while there is a single command."""


@app.command()
def diff(package: str, old_version: str, new_version: str) -> None:
    """Show breaking API changes between two versions of a package."""
    try:
        with console.status(f"Comparing {package} {old_version} and {new_version}..."):
            changes = apidiff.diff(package, old_version, new_version)
    except pypi.PackageNotFoundError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    console.print(f"\n[bold]{package}[/bold] {old_version} -> {new_version}\n")
    if not changes:
        console.print("[green]No breaking API changes found.[/green]")
        return

    for kind, group in groupby(changes, key=lambda change: change.kind):
        console.print(f"[bold yellow]{kind.upper()}[/bold yellow]")
        for change in group:
            suffix = f"  [dim]{escape(change.details)}[/dim]" if change.details else ""
            console.print(f"  {escape(change.path)}{suffix}")
        console.print()

    console.print(f"{len(changes)} breaking changes")
