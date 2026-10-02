# Design: `bumpscope check` v0.1

This is the source of truth for building `check` v0.1. Read it before starting any step.
Steps are done one at a time, each separately. Terms in **bold** are defined in
[`CONTEXT.md`](../../CONTEXT.md).

## Context

`bumpscope diff` lists breaking API changes between two versions of a package. The v0.1
milestone is `bumpscope check`: report only the breaking changes that touch the user's project.
Tests and CI land before `check`.

Existing code: `src/bumpscope/apidiff.py` (`diff`, `Change`, `_explain`, `_load`),
`src/bumpscope/pypi.py` (`download`, `top_level_modules`, `_wheel_url`, `PackageNotFoundError`),
`src/bumpscope/cli.py` (typer app, rich output grouped by kind).

## Settled decisions

1. **Scope**: `check` for one package, terminal output. Tests and CI come first.
2. **Terms**: **Package**, **Module**, **Dependency** (see glossary).
3. **Change naming**: a change is named by **all its public paths**; the definition path is
   secondary. `Cls.__init__` changes render as the class call: `httpx.Client(proxies)`, not
   `httpx._client.Client.__init__(proxies)`.
4. **Breaking changes only**, no deprecations.
5. **Versions**: the **From version** is the one installed in the *checked project's* `.venv`, not
   in the interpreter running bumpscope (it may run via uvx or pipx); `--from` overrides. The
   **To version** is the latest PyPI release, skipping pre-releases and yanked releases; `--to`
   overrides.
6. **Match rules** per griffe `BreakageKind`:

   | Kind | Impact when |
   |---|---|
   | `OBJECT_REMOVED`, `OBJECT_CHANGED_KIND`, `CLASS_REMOVED_BASE` | any reference (import or attribute access) |
   | `PARAMETER_REMOVED` | the call passes it (by keyword, or positionally at its old index) |
   | `PARAMETER_MOVED` | the call passes it positionally |
   | `PARAMETER_CHANGED_KIND` | the call passes it in a way the new kind forbids |
   | `PARAMETER_ADDED_REQUIRED`, `PARAMETER_CHANGED_REQUIRED` | the call does **not** pass it |
   | `PARAMETER_CHANGED_DEFAULT`, `RETURN_CHANGED_TYPE`, `ATTRIBUTE_CHANGED_TYPE`, `ATTRIBUTE_CHANGED_VALUE` | skipped in `check` (still shown by `diff`) |
   | Module was removed (bumpscope's own kind) | any import of the module |

7. **Impact** = a **Breaking change** matched to a **Usage** at file:line.
8. **Tests**: offline fixtures (tiny two-version packages in `tmp_path`, `pypi.download` faked);
   one live-PyPI httpx test behind a marker, skipped by default.
9. **CI**: GitHub Actions, ubuntu + windows × Python 3.12 / 3.13: `uv sync`, `ruff check`,
   `ruff format --check`, `pytest`.
10. **No `.venv`**: if `<project>/.venv` is missing, or the package is not installed there, exit
    with an error suggesting `--from`.
11. **Scan scope**: every `.py` under the project root, tests included. Skip `.git`, hidden dirs,
    any dir containing `pyvenv.cfg`, `build`, `dist`, `__pycache__`.
12. **Exit codes** for both `diff` and `check`: 0 nothing found, 1 found, 2 error.
13. **Usage resolution**: import bindings only (aliases, from-imports, attribute chains on them).
    Method calls on instances (`c = httpx.Client(); c.get(...)`) are a documented v0.1 gap.
14. **Private imports** (`from httpx._api import get`) match via the definition path and are shown
    as the user wrote them.
15. **CLI**: `bumpscope check PACKAGE [--project PATH] [--from V] [--to V]`. `PACKAGE` is the PyPI
    name; `--project` defaults to the current directory.
16. **Output**: grouped by kind, like `diff`, one line per impact:
    `src/app/client.py:31  httpx.Client(proxies)`.
17. **Never "safe"**: `check` can have false negatives (decision 13), so output never calls an
    update safe. With no impacts:
    `httpx 0.24.1 -> 0.28.0: 29 breaking changes, none matched in your code (method calls on instances are not analyzed)`.
    Exit code 0 means "no impact found", not "safe".

No ADRs: every decision above is cheap to reverse.

## Step 1: domain docs, README, design doc

- This file.
- `CONTEXT.md` glossary: Package, Module, Dependency, Breaking change, Public path, Definition
  path, From version / To version, Usage, Impact.
- README "Where this is going": replace the pydantic deprecation example with a real breaking
  change; rename `SAFE TO UPGRADE` to `NO IMPACT FOUND`.
- README: add a "Limitations" section (instance method calls not analyzed; only breaking API
  changes detected).

## Step 2: tests and CI (before `check`)

- `pyproject.toml`: `[dependency-groups] dev = ["pytest", "ruff"]`; `[tool.pytest.ini_options]`
  with `markers = ["network: hits real PyPI"]` and `addopts = "-m 'not network'"`; `[tool.ruff]`.
- `tests/conftest.py`: fixture that writes a fake unpacked wheel (package dir + `__init__.py` +
  modules) for "old" and "new" source strings, and monkeypatches `pypi.download` to return them.
- `tests/test_apidiff.py`: removed parameter, removed module, `__version__` ignored, dedupe/sort.
- `tests/test_pypi.py`: `top_level_modules` (skips `.dist-info` / `.data`, single-file modules);
  `_wheel_url` with a monkeypatched `httpx.get` (404 → `PackageNotFoundError`, no wheel, prefers
  `-none-any`).
- `tests/test_cli.py`: typer `CliRunner` on `diff` (output and exit codes).
- `tests/test_live.py`: `@pytest.mark.network` httpx 0.24.1 → 0.28.0 sanity check.
- `.github/workflows/ci.yml`: matrix as in decision 9, `astral-sh/setup-uv`.
- Run `ruff format` once so CI starts green.

## Step 3: `diff` changes

- `apidiff._load`: add `resolve_aliases=True` (and `resolve_implicit=True`) so each griffe
  object's `.aliases` holds every re-export path.
- `Change`: replace `path: str` with `public_paths: tuple[str, ...]` and `definition_path: str`,
  plus `kind` (griffe `BreakageKind` or bumpscope's "Module was removed"), `parameter: str | None`,
  and the old parameter's index and kind (needed by the match rules).
  Public paths = the object's `.aliases` keys plus its own path, filtered to those with no
  `_`-prefixed segment; fall back to the definition path when none are public.
- Display: `Cls.__init__` → `Cls`; parameter appended as `(name)`; one line per public path.
- CLI `diff`: exit codes per decision 12. Update tests first.

## Step 4: `check`

New modules, each small, mirroring the existing style:

- `src/bumpscope/project.py`: `installed_version(project, package)` reads
  `<project>/.venv/{Lib,lib/python*}/site-packages/*.dist-info/METADATA`, normalising names
  (PEP 503); raises an error → exit 2 with "pass --from". `python_files(project)` walks per
  decision 11.
- `pypi.latest_version(package)`: `https://pypi.org/pypi/{package}/json` `releases`; drop
  pre-releases (`packaging.version.Version.is_prerelease`, add `packaging` as a dependency) and
  releases whose files are all yanked; take the max.
- `src/bumpscope/usages.py`: `ast` visitor per file. Tracks import bindings (`import a.b as c`,
  `from a import b as c`), resolves `Name` / `Attribute` chains to dotted paths, and emits
  `Usage(path, file, line, kind="reference"|"call", positional_count, keywords)` for paths under
  the package's top-level modules.
- `src/bumpscope/impact.py`: `find_impacts(changes, usages) -> list[Impact]` implementing the
  match table (decision 6). A usage matches a change when its path equals any public path or the
  definition path (for `__init__` changes, the class path).
- `cli.check`: resolve From/To versions, run `apidiff.diff`, scan, match, print grouped by kind
  (`file:line  public.path(param)`), summary line, exit codes.
- Tests (fixture packages + `tmp_path` projects with a fake `.venv` dist-info): each match-table
  row, alias imports, private import, skipped dirs, no `.venv` error, no-impact summary, exit codes.
  The no-impact output contains "none matched" and the limitation note, and never the word "safe".
- README: "What works today" gains `check`, linking to the Limitations section.

## Verification

- `uv run ruff check . && uv run ruff format --check . && uv run pytest` green locally (Windows).
- `uv run pytest -m network` passes against real PyPI.
- Manual: `uv run bumpscope diff httpx 0.24.1 0.28.0` shows `httpx.get(proxies)` and
  `httpx.Client(proxies)`, exit code 1.
- Manual: a tmp project with a `.venv` holding httpx 0.24.1 and a file calling
  `httpx.Client(proxies=...)`: `uv run bumpscope check httpx --project <tmp> --to 0.28.0` reports
  that line, exit 1. Remove the call: the "none matched … not analyzed" summary, exit 0.
- CI green on the PR (ubuntu + windows).
